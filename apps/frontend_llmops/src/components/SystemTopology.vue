<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { VueFlow, useVueFlow, Handle, Position, type Edge, type Node, type NodeMouseEvent } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import { Box, Cpu, Layers, Server, Settings2, Sparkles, Users } from '@lucide/vue'
import { useModelsStore } from '@/stores/models'
import { useResourcesStore } from '@/stores/resources'
import { useTrafficStore } from '@/stores/traffic'
import { lorasOfGroup } from '@/composables/useModelOptions'
import StatusDot from '@/components/StatusDot.vue'
import { formatNumber, formatPercent } from '@/lib/utils'
import type { ModelState } from '@/types/api'

const models = useModelsStore()
const resources = useResourcesStore()
const traffic = useTrafficStore()
const router = useRouter()

// Nodes act as drill-in entry points to the relevant page.
function onNodeClick({ node }: NodeMouseEvent) {
  switch (node.type) {
    case 'group':
      router.push({ path: '/models', query: { q: (node.data as GroupAgg).group } })
      break
    case 'lora':
      router.push({ path: '/models', query: { q: (node.data as { group: string }).group } })
      break
    case 'embedding':
      router.push({ path: '/models', query: { q: 'embedding' } })
      break
    case 'gpu':
      router.push('/resources')
      break
    case 'backend':
      router.push('/models')
      break
    case 'router':
    case 'client':
      router.push('/traffic')
      break
  }
}

// Edge-plane visibility toggles.
const showData = ref(true)
const showPlacement = ref(true)
const showControl = ref(false)
const showLora = ref(true)

function togglePlane(k: string) {
  if (k === 'data') showData.value = !showData.value
  else if (k === 'placement') showPlacement.value = !showPlacement.value
  else if (k === 'control') showControl.value = !showControl.value
  else if (k === 'lora') showLora.value = !showLora.value
}

const flowId = 'system-topology'
const { fitView, onNodesInitialized } = useVueFlow(flowId)
onNodesInitialized(() => fitView({ padding: 0.15 }))

const ROW = 92
const COL = { client: 20, ctrl: 300, mid: 620, lora: 880, gpu: 1140 }
const LORA_CAP = 4 // max satellite nodes drawn per group; the badge shows the true total

const stateBorder: Record<ModelState, string> = {
  ready: 'var(--status-ready)',
  starting: 'var(--status-starting)',
  stopping: 'var(--status-starting)',
  failed: 'var(--status-failed)',
  stopped: 'var(--border)',
}

interface GroupAgg {
  group: string
  ready: number
  total: number
  worst: ModelState
  running: number
  waiting: number
  share: number
  gpus: number[]
  loras: string[] // served names of adapters mounted on this group
}

const groups = computed<GroupAgg[]>(() => {
  const byGroup = new Map<string, GroupAgg>()
  const reqTotal = traffic.requests.length
  const reqByGroup: Record<string, number> = {}
  for (const r of traffic.requests) reqByGroup[r.model_key] = (reqByGroup[r.model_key] ?? 0) + 1

  for (const m of models.llms) {
    const g = m.key.split('::')[0] ?? m.key
    const inst = m.key.split('::')[1] ?? ''
    const agg =
      byGroup.get(g) ??
      ({ group: g, ready: 0, total: 0, worst: 'stopped', running: 0, waiting: 0, share: 0, gpus: [], loras: [] } as GroupAgg)
    agg.total++
    if (m.state === 'ready') agg.ready++
    const gpu = models.gpuForKey(m.key, m.kind)
    if (gpu !== null && !agg.gpus.includes(gpu)) agg.gpus.push(gpu)
    const im = (m.state === 'ready' || m.state === 'starting') ? traffic.metrics[g]?.[inst] : null
    if (im) {
      agg.running += im.running ?? 0
      agg.waiting += im.waiting ?? 0
    }
    byGroup.set(g, agg)
  }
  // worst-state rollup + traffic share
  for (const [g, agg] of byGroup) {
    const states = models.llms.filter((m) => m.key.split('::')[0] === g).map((m) => m.state)
    agg.worst = states.includes('failed')
      ? 'failed'
      : states.some((s) => s === 'starting' || s === 'stopping')
        ? 'starting'
        : states.every((s) => s === 'ready')
          ? 'ready'
          : 'stopped'
    agg.share = reqTotal ? (reqByGroup[g] ?? 0) / reqTotal : 0
    agg.loras = lorasOfGroup(models, g).map((l) => l.name)
  }
  return [...byGroup.values()]
})

const embedding = computed(() => models.byKey.get('embedding::default') ?? null)
const gpus = computed(() => resources.resources?.gpus ?? [])

// GPU nodes = physically-present GPUs ∪ GPU indices referenced by any model's
// config. A referenced-but-undetected index (e.g. embedding on cuda:1 on a
// single-GPU box) still gets a node so its placement edge has a target.
const gpuList = computed(() => {
  const present = new Map(gpus.value.map((g) => [g.index, g]))
  const referenced = new Set<number>()
  for (const g of groups.value) g.gpus.forEach((i) => referenced.add(i))
  const embGpu = models.gpuForKey('embedding::default', 'embedding')
  if (embGpu !== null) referenced.add(embGpu)
  const all = new Set<number>([...present.keys(), ...referenced])
  return [...all]
    .sort((a, b) => a - b)
    .map((index) => ({ index, info: present.get(index) ?? null }))
})
const routerUp = computed(() => Object.keys(traffic.metrics).length > 0 || models.readyCount > 0)

const nodes = ref<Node[]>([])
const edges = ref<Edge[]>([])

function buildGraph() {
  const gs = groups.value
  const hasEmb = !!embedding.value
  const midCount = gs.length + (hasEmb ? 1 : 0)
  const midH = Math.max(midCount, 1) * ROW
  const centerY = midH / 2

  const nextNodes: Node[] = []
  const nextEdges: Edge[] = []
  const base = { draggable: false, selectable: false }

  // --- Control-plane / entry nodes ---
  nextNodes.push({
    id: 'client',
    type: 'client',
    position: { x: COL.client, y: centerY - 26 },
    data: { total: traffic.totalRequests },
    ...base,
  })
  nextNodes.push({
    id: 'router',
    type: 'router',
    position: { x: COL.ctrl, y: centerY - 84 },
    data: { up: routerUp.value, total: traffic.totalRequests, errorRate: traffic.errorRate },
    ...base,
  })
  nextNodes.push({
    id: 'backend',
    type: 'backend',
    position: { x: COL.ctrl, y: centerY + 16 },
    data: { ready: models.readyCount, total: models.total },
    ...base,
  })

  // client -> router (data)
  if (showData.value) {
    nextEdges.push({
      id: 'e-client-router',
      source: 'client',
      target: 'router',
      animated: traffic.totalRequests > 0,
      style: { stroke: 'var(--chart-1)', strokeWidth: 2, opacity: 0.9 },
    })
  }

  // --- Model groups ---
  gs.forEach((g, i) => {
    const id = `group:${g.group}`
    const active = g.running + g.waiting > 0 || g.share > 0
    nextNodes.push({
      id,
      type: 'group',
      position: { x: COL.mid, y: i * ROW },
      data: g,
      ...base,
    })
    if (showData.value) {
      nextEdges.push({
        id: `e-router-${id}`,
        source: 'router',
        target: id,
        animated: active && g.worst === 'ready',
        label: g.share > 0 ? formatPercent(g.share * 100) : undefined,
        style: {
          stroke: 'var(--chart-1)',
          strokeWidth: 1.2 + g.share * 8,
          opacity: g.worst === 'ready' ? 0.9 : 0.3,
        },
        labelStyle: { fill: 'var(--foreground)', fontSize: '10px', fontWeight: 600 },
        labelBgStyle: { fill: 'var(--card)', fillOpacity: 0.85 },
      })
    }
    if (showControl.value) {
      nextEdges.push({
        id: `c-backend-${id}`,
        source: 'backend',
        target: id,
        style: { stroke: 'var(--chart-4)', strokeWidth: 1, strokeDasharray: '4 4', opacity: 0.4 },
      })
    }
    if (showPlacement.value) {
      for (const gpu of g.gpus) {
        nextEdges.push({
          id: `p-${id}-gpu${gpu}`,
          source: id,
          target: `gpu:${gpu}`,
          style: { stroke: 'var(--chart-2)', strokeWidth: 1, opacity: 0.45 },
        })
      }
    }
    // LoRA satellites: structural (config) lineage, hung off the base group.
    // Dashed (not traffic) — per-adapter request flow isn't tracked yet.
    if (showLora.value && g.loras.length) {
      const shown = g.loras.slice(0, LORA_CAP)
      shown.forEach((name, k) => {
        const lid = `lora:${g.group}:${name}`
        nextNodes.push({
          id: lid,
          type: 'lora',
          position: { x: COL.lora, y: i * ROW + 14 + (k - (shown.length - 1) / 2) * 26 },
          data: { name, group: g.group, state: g.worst, extra: k === LORA_CAP - 1 ? g.loras.length - LORA_CAP : 0 },
          ...base,
        })
        nextEdges.push({
          id: `e-${id}-${lid}`,
          source: id,
          target: lid,
          style: { stroke: 'var(--chart-3)', strokeWidth: 1, strokeDasharray: '3 3', opacity: 0.55 },
        })
      })
    }
  })

  // --- Embedding server ---
  if (hasEmb) {
    const id = 'embedding'
    nextNodes.push({
      id,
      type: 'embedding',
      position: { x: COL.mid, y: gs.length * ROW },
      data: { state: embedding.value!.state, port: embedding.value!.port },
      ...base,
    })
    if (showData.value) {
      nextEdges.push({
        id: 'e-router-embedding',
        source: 'router',
        target: id,
        animated: embedding.value!.state === 'ready',
        style: {
          stroke: 'var(--chart-1)',
          strokeWidth: 1.2,
          opacity: embedding.value!.state === 'ready' ? 0.8 : 0.3,
        },
      })
    }
    if (showControl.value) {
      nextEdges.push({
        id: 'c-backend-embedding',
        source: 'backend',
        target: id,
        style: { stroke: 'var(--chart-4)', strokeWidth: 1, strokeDasharray: '4 4', opacity: 0.4 },
      })
    }
    const embGpu = models.gpuForKey('embedding::default', 'embedding')
    if (showPlacement.value && embGpu !== null) {
      nextEdges.push({
        id: `p-embedding-gpu${embGpu}`,
        source: id,
        target: `gpu:${embGpu}`,
        style: { stroke: 'var(--chart-2)', strokeWidth: 1, opacity: 0.45 },
      })
    }
  }

  // --- GPUs (present ∪ referenced) ---
  const G = gpuList.value.length
  const gpuStart = centerY - (G * 96) / 2
  gpuList.value.forEach((gpu, i) => {
    nextNodes.push({
      id: `gpu:${gpu.index}`,
      type: 'gpu',
      position: { x: COL.gpu, y: gpuStart + i * 96 },
      data: gpu,
      ...base,
    })
  })

  nodes.value = nextNodes
  edges.value = nextEdges

  // Only refit when the layout (which nodes exist / where) actually changes —
  // not on every metric poll, otherwise a manual zoom/pan snaps back each tick.
  const layoutKey = nextNodes.map((n) => `${n.id}@${n.position.x},${n.position.y}`).join('|')
  if (layoutKey !== lastLayoutKey) {
    lastLayoutKey = layoutKey
    void nextTick(() => fitView({ padding: 0.15 }))
  }
}
let lastLayoutKey = ''

watch(
  [groups, embedding, gpus, showData, showPlacement, showControl, showLora, routerUp],
  buildGraph,
  { immediate: true, deep: true },
)

function vramPct(used: number, total: number) {
  return total ? (used / total) * 100 : 0
}
function miniColor(node: Node) {
  if (node.type === 'gpu') return 'var(--chart-2)'
  if (node.type === 'group') return stateBorder[(node.data as GroupAgg).worst]
  return 'var(--chart-1)'
}
</script>

<template>
  <div class="rounded-xl border border-border/70 bg-card">
    <!-- Header + legend + plane toggles -->
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border/60 px-5 py-3">
      <h3 class="text-sm font-semibold">系統拓撲</h3>
      <span class="hidden text-[11px] text-muted-foreground sm:inline">· 點擊節點可深入查看</span>
      <div class="flex items-center gap-3 text-[11px] text-muted-foreground">
        <span class="flex items-center gap-1"><span class="h-0.5 w-4 rounded bg-[var(--chart-1)]" />資料</span>
        <span class="flex items-center gap-1"><span class="h-0.5 w-4 rounded bg-[var(--chart-2)]" />部署</span>
        <span class="flex items-center gap-1"><span class="h-0.5 w-4 rounded bg-[var(--chart-4)]" />控制</span>
        <span class="flex items-center gap-1"><span class="w-4 border-t border-dashed border-[var(--chart-3)]" />LoRA</span>
      </div>
      <div class="ml-auto flex items-center gap-1.5">
        <button
          v-for="t in [
            { k: 'data', label: '資料', model: showData },
            { k: 'placement', label: '部署', model: showPlacement },
            { k: 'control', label: '控制', model: showControl },
            { k: 'lora', label: 'LoRA', model: showLora },
          ]"
          :key="t.k"
          class="rounded-md border px-2 py-0.5 text-xs transition-colors"
          :class="
            t.model
              ? 'border-[var(--chart-1)]/40 bg-[var(--chart-1)]/10 text-foreground'
              : 'border-border bg-background/40 text-muted-foreground hover:text-foreground'
          "
          @click="togglePlane(t.k)"
        >
          {{ t.label }}
        </button>
      </div>
    </div>

    <div class="h-[460px] w-full">
      <VueFlow
        :id="flowId"
        :nodes="nodes"
        :edges="edges"
        :min-zoom="0.2"
        :max-zoom="1.5"
        :nodes-draggable="false"
        :nodes-connectable="false"
        :elements-selectable="false"
        :zoom-on-scroll="false"
        :pan-on-scroll="false"
        :zoom-on-double-click="false"
        :prevent-scrolling="false"
        class="system-flow"
        @node-click="onNodeClick"
      >
        <Background :gap="20" :size="1" pattern-color="var(--border)" />
        <Controls :show-interactive="false" position="bottom-left" />
        <MiniMap pannable :node-color="miniColor" class="!bg-card/80" />

        <!-- Client -->
        <template #node-client="{ data }">
          <div class="topo-node items-center gap-2 px-3 py-2">
            <Handle type="source" :position="Position.Right" />
            <Users class="size-4 text-[var(--chart-1)]" />
            <div class="text-left">
              <p class="text-[12px] font-semibold leading-tight">用戶端</p>
              <p class="text-[10px] text-muted-foreground tabular">{{ formatNumber(data.total) }} 請求</p>
            </div>
          </div>
        </template>

        <!-- Router -->
        <template #node-router="{ data }">
          <div class="topo-node flex-col gap-0.5 px-4 py-2" :style="{ borderColor: data.up ? 'var(--status-ready)' : 'var(--status-failed)' }">
            <Handle type="target" :position="Position.Left" />
            <Handle type="source" :position="Position.Right" />
            <div class="flex items-center gap-1.5">
              <Server class="size-4 text-[var(--chart-1)]" />
              <span class="text-[12px] font-semibold">路由器</span>
              <span class="text-[10px] text-muted-foreground">:8887</span>
            </div>
            <p class="text-[10px] text-muted-foreground tabular">
              {{ formatPercent(data.errorRate) }} 錯誤
            </p>
          </div>
        </template>

        <!-- Backend -->
        <template #node-backend="{ data }">
          <div class="topo-node flex-col gap-0.5 px-4 py-2 border-[var(--chart-4)]/50">
            <Handle type="target" :position="Position.Left" />
            <Handle type="source" :position="Position.Right" />
            <div class="flex items-center gap-1.5">
              <Settings2 class="size-4 text-[var(--chart-4)]" />
              <span class="text-[12px] font-semibold">後端</span>
              <span class="text-[10px] text-muted-foreground">:5000</span>
            </div>
            <p class="text-[10px] text-muted-foreground tabular">{{ data.ready }}/{{ data.total }} 就緒</p>
          </div>
        </template>

        <!-- Model group -->
        <template #node-group="{ data }">
          <div class="topo-node w-[200px] flex-col gap-1 px-3 py-2" :style="{ borderColor: stateBorder[data.worst as ModelState] }">
            <Handle type="target" :position="Position.Left" />
            <Handle type="source" :position="Position.Right" />
            <div class="flex items-center gap-1.5">
              <StatusDot :state="data.worst" size="sm" />
              <Sparkles class="size-3.5 text-[var(--chart-1)]" />
              <span class="truncate text-[12px] font-semibold" :title="data.group">{{ data.group }}</span>
              <span class="ml-auto text-[10px] text-muted-foreground tabular">{{ data.ready }}/{{ data.total }}</span>
            </div>
            <p class="text-[10px] text-muted-foreground tabular">
              執行 {{ data.running }} · 等待 {{ data.waiting }}
              <span v-if="data.gpus.length"> · GPU {{ data.gpus.join(',') }}</span>
            </p>
            <p v-if="data.loras?.length" class="flex items-center gap-1 text-[10px] text-[var(--chart-3)]">
              <Layers class="size-3" />{{ data.loras.length }} LoRA
            </p>
          </div>
        </template>

        <!-- LoRA adapter (structural satellite of a base group) -->
        <template #node-lora="{ data }">
          <div
            class="topo-node items-center gap-1 px-2 py-1"
            :style="{ borderColor: stateBorder[data.state as ModelState] }"
          >
            <Handle type="target" :position="Position.Left" />
            <Layers class="size-3 shrink-0 text-[var(--chart-3)]" />
            <span class="max-w-[120px] truncate text-[10px] font-medium" :title="data.name">{{ data.name }}</span>
            <span v-if="data.extra > 0" class="text-[10px] text-muted-foreground">+{{ data.extra }}</span>
          </div>
        </template>

        <!-- Embedding -->
        <template #node-embedding="{ data }">
          <div class="topo-node w-[200px] flex-col gap-1 px-3 py-2" :style="{ borderColor: stateBorder[data.state as ModelState] }">
            <Handle type="target" :position="Position.Left" />
            <Handle type="source" :position="Position.Right" />
            <div class="flex items-center gap-1.5">
              <StatusDot :state="data.state" size="sm" />
              <Box class="size-3.5 text-[var(--chart-4)]" />
              <span class="text-[12px] font-semibold">嵌入</span>
              <span class="ml-auto text-[10px] text-muted-foreground">:{{ data.port }}</span>
            </div>
            <p class="text-[10px] capitalize text-muted-foreground">{{ data.state }}</p>
          </div>
        </template>

        <!-- GPU (present, or referenced-but-undetected) -->
        <template #node-gpu="{ data }">
          <div
            class="topo-node w-[170px] flex-col gap-1 px-3 py-2"
            :class="data.info ? 'border-[var(--chart-2)]/50' : 'border-dashed border-border'"
          >
            <Handle type="target" :position="Position.Left" />
            <div class="flex items-center gap-1.5">
              <Cpu class="size-3.5" :class="data.info ? 'text-[var(--chart-2)]' : 'text-muted-foreground'" />
              <span class="text-[12px] font-semibold">GPU {{ data.index }}</span>
              <span v-if="data.info" class="ml-auto text-[10px] text-muted-foreground tabular">
                {{ formatPercent(data.info.gpu_util) }}
              </span>
            </div>
            <template v-if="data.info">
              <div class="h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  class="h-full rounded-full bg-[var(--chart-2)]"
                  :style="{ width: `${vramPct(data.info.memory_used, data.info.memory_total)}%` }"
                />
              </div>
              <p class="text-[10px] text-muted-foreground tabular">
                {{ formatPercent(vramPct(data.info.memory_used, data.info.memory_total)) }} VRAM
              </p>
            </template>
            <p v-else class="text-[10px] text-muted-foreground/70">已設定 · 未偵測到</p>
          </div>
        </template>
      </VueFlow>
    </div>
  </div>
</template>

<style scoped>
.system-flow :deep(.vue-flow__handle) {
  opacity: 0;
  pointer-events: none;
}
.system-flow :deep(.vue-flow__pane),
.system-flow {
  background: transparent;
}
.topo-node {
  display: flex;
  cursor: pointer;
  border-radius: 0.6rem;
  border-width: 1px;
  background-color: var(--card);
  box-shadow: 0 1px 2px rgb(0 0 0 / 0.08);
  transition:
    box-shadow 0.15s,
    transform 0.15s;
}
.topo-node:hover {
  box-shadow: 0 4px 14px -4px color-mix(in oklch, var(--primary) 30%, transparent);
  transform: translateY(-1px);
}
</style>
