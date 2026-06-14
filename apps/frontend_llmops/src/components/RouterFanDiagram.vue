<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { VueFlow, useVueFlow, Handle, Position, type Edge, type Node } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import { useTrafficStore } from '@/stores/traffic'
import { useModelsStore } from '@/stores/models'
import StatusDot from '@/components/StatusDot.vue'
import Badge from '@/components/ui/Badge.vue'
import { formatPercent } from '@/lib/utils'
import type { InstanceMetrics, ModelState, ModelView } from '@/types/api'

const props = defineProps<{ group: string }>()
const traffic = useTrafficStore()
const models = useModelsStore()

const STEP = 196 // horizontal spacing between instance nodes
const NODE_W = 150
const ROUTER_W = 112

interface NodeData {
  id: string
  state: ModelState
  im: InstanceMetrics | null
  share: number
  serving: boolean
  score: number | null
  preferred: boolean
}

const instances = computed(() => models.models.filter((m) => m.key.split('::')[0] === props.group))
const readyCount = computed(() => instances.value.filter((m) => m.state === 'ready').length)
const live = computed(() =>
  instances.value.some((m) => m.state === 'ready' || m.state === 'starting'),
)

function instId(m: ModelView) {
  return m.key.split('::')[1] ?? ''
}
function metricsFor(m: ModelView): InstanceMetrics | null {
  if (m.state !== 'ready' && m.state !== 'starting') return null
  return traffic.metrics[props.group]?.[instId(m)] ?? null
}

const shareByInst = computed(() => {
  const reqs = traffic.requests.filter((r) => r.model_key === props.group)
  const total = reqs.length
  const counts: Record<string, number> = {}
  for (const r of reqs) counts[r.instance_id] = (counts[r.instance_id] ?? 0) + 1
  const out: Record<string, number> = {}
  for (const m of instances.value) out[instId(m)] = total ? (counts[instId(m)] ?? 0) / total : 0
  return out
})

// Unique scope per group so multiple canvases on the page don't share a store.
const flowId = `rf-${props.group}-${Math.random().toString(36).slice(2, 7)}`
const { fitView, onNodesInitialized } = useVueFlow(flowId)

// Fit only once node dimensions are measured (serving nodes are taller than
// "Stopped" ones, so an early fit would clip them).
onNodesInitialized(() => fitView({ padding: 0.25 }))

const nodes = ref<Node[]>([])
const edges = ref<Edge[]>([])

watch(
  [instances, () => traffic.metrics, shareByInst],
  () => {
    const insts = instances.value
    const n = insts.length
    // Scores for ready instances (lower = router-preferred).
    const scores = new Map<string, number>()
    for (const m of insts) {
      const im = metricsFor(m)
      if (m.state === 'ready' && im) {
        scores.set(
          m.key,
          (im.waiting ?? 0) * 10 + (im.running ?? 0) * 3 + (im.kv_cache_usage_perc ?? 0) * 100,
        )
      }
    }
    const minScore = scores.size ? Math.min(...scores.values()) : Infinity
    const rowWidth = (n - 1) * STEP + NODE_W
    const routerX = rowWidth / 2 - ROUTER_W / 2

    const nextNodes: Node[] = [
      {
        id: '__router__',
        type: 'router',
        position: { x: routerX, y: 0 },
        data: {},
        draggable: false,
        selectable: false,
      },
    ]
    const nextEdges: Edge[] = []

    insts.forEach((m, i) => {
      const id = instId(m)
      const im = metricsFor(m)
      const share = shareByInst.value[id] ?? 0
      const serving = m.state === 'ready' || m.state === 'starting'
      const active = serving && (share > 0 || (im ? (im.running ?? 0) + (im.waiting ?? 0) > 0 : false))
      const data: NodeData = {
        id,
        state: m.state,
        im,
        share,
        serving,
        score: scores.get(m.key) ?? null,
        preferred: m.state === 'ready' && scores.get(m.key) === minScore && Number.isFinite(minScore),
      }
      nextNodes.push({
        id,
        type: 'instance',
        position: { x: i * STEP, y: 168 },
        data,
        draggable: false,
        selectable: false,
      })
      nextEdges.push({
        id: `e-${id}`,
        source: '__router__',
        target: id,
        animated: active,
        label: share > 0 ? formatPercent(share * 100) : undefined,
        style: {
          stroke: serving ? 'var(--chart-1)' : 'var(--muted-foreground)',
          strokeWidth: serving ? 1.5 + share * 9 : 1,
          strokeDasharray: serving ? undefined : '4 4',
          opacity: serving ? (active ? 0.95 : 0.4) : 0.25,
        },
        labelStyle: { fill: 'var(--foreground)', fontSize: '10px', fontWeight: 600 },
        labelBgStyle: { fill: 'var(--card)', fillOpacity: 0.85 },
      })
    })

    nodes.value = nextNodes
    edges.value = nextEdges
    // Re-fit after the DOM updates so state changes (which alter node heights)
    // never leave a node clipped.
    void nextTick(() => fitView({ padding: 0.25 }))
  },
  { immediate: true, deep: true },
)
</script>

<template>
  <!-- Idle group: tidy one-liner instead of an empty canvas. -->
  <div
    v-if="!live"
    class="flex items-center gap-2.5 rounded-lg border border-border/50 bg-background/30 px-3 py-2"
  >
    <StatusDot state="stopped" />
    <span class="text-sm font-medium">{{ group }}</span>
    <Badge variant="muted">{{ instances.length }} instances</Badge>
    <span class="ml-auto text-xs text-muted-foreground">idle — no instance running</span>
  </div>

  <!-- Live group: Vue Flow routing canvas. -->
  <div v-else class="rounded-lg border border-border/50 bg-background/20">
    <div class="flex items-center gap-2 px-3 pt-3">
      <span class="text-sm font-medium">{{ group }}</span>
      <Badge variant="ready" class="tabular">{{ readyCount }}/{{ instances.length }} ready</Badge>
    </div>
    <div class="h-[260px] w-full">
      <VueFlow
        :id="flowId"
        :nodes="nodes"
        :edges="edges"
        :min-zoom="0.2"
        :max-zoom="1.2"
        :nodes-draggable="false"
        :nodes-connectable="false"
        :elements-selectable="false"
        :zoom-on-scroll="false"
        :pan-on-scroll="false"
        :pan-on-drag="false"
        :zoom-on-double-click="false"
        :prevent-scrolling="false"
        class="router-flow"
      >
        <Background :gap="18" :size="1" pattern-color="var(--border)" />

        <!-- Router node -->
        <template #node-router>
          <div
            class="glow-ring flex h-8 items-center justify-center rounded-lg border border-[var(--chart-1)]/50 bg-card px-4 text-xs font-semibold tracking-wide"
            :style="{ width: `${ROUTER_W}px` }"
          >
            ROUTER
            <Handle type="source" :position="Position.Bottom" />
          </div>
        </template>

        <!-- Instance node -->
        <template #node-instance="{ data }">
          <div
            class="rounded-lg border bg-card px-3 py-2 text-center shadow-sm"
            :class="[
              data.preferred
                ? 'border-[var(--chart-1)] animate-node-glow'
                : data.serving
                  ? 'border-[var(--chart-1)]/50'
                  : 'border-border',
            ]"
            :style="{ width: `${NODE_W}px` }"
          >
            <Handle type="target" :position="Position.Top" />
            <div class="flex items-center justify-center gap-1.5">
              <StatusDot :state="data.state" size="sm" />
              <span class="font-mono text-[13px] font-semibold">{{ data.id }}</span>
            </div>
            <template v-if="data.serving && data.im">
              <p class="mt-1 text-[11px] text-muted-foreground tabular">
                run {{ data.im.running }} · wait {{ data.im.waiting }}
              </p>
              <p class="text-[11px] text-muted-foreground tabular">
                kv {{ formatPercent(data.im.kv_cache_usage_perc * 100) }} · score
                {{ data.score?.toFixed(0) }}
              </p>
              <p v-if="data.preferred" class="mt-0.5 text-[10px] font-medium text-[var(--chart-1)]">
                ★ next pick
              </p>
            </template>
            <p v-else class="mt-1 text-[11px] capitalize text-muted-foreground/70">{{ data.state }}</p>
          </div>
        </template>
      </VueFlow>
    </div>
  </div>
</template>

<style scoped>
/* Hide Vue Flow connection handles (we only use them for edge anchoring). */
.router-flow :deep(.vue-flow__handle) {
  opacity: 0;
  pointer-events: none;
}
/* Let the canvas blend into the card (no default white background). */
.router-flow :deep(.vue-flow__pane),
.router-flow {
  background: transparent;
}
</style>
