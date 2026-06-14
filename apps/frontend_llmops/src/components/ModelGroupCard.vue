<script setup lang="ts">
import { computed, ref } from 'vue'
import { Box, ChevronDown, Loader2, Play, RotateCw, Sparkles, Square } from '@lucide/vue'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Tooltip from '@/components/ui/Tooltip.vue'
import StatusDot from '@/components/StatusDot.vue'
import { useModelsStore } from '@/stores/models'
import { useTrafficStore } from '@/stores/traffic'
import { useModelControl } from '@/composables/useModelControl'
import type { ModelState, ModelView } from '@/types/api'

const props = defineProps<{ group: string; instances: ModelView[] }>()
const emit = defineEmits<{ open: [key: string] }>()

const models = useModelsStore()
const traffic = useTrafficStore()
const control = useModelControl()

const COLLAPSE_AT = 4
const expanded = ref(false)
const collapsible = computed(() => props.instances.length > COLLAPSE_AT + 1)
const visibleInstances = computed(() =>
  collapsible.value && !expanded.value ? props.instances.slice(0, COLLAPSE_AT) : props.instances,
)
const hiddenCount = computed(() => props.instances.length - visibleInstances.value.length)

const kind = computed(() => props.instances[0]?.kind ?? 'llm')
const modelTag = computed<string | null>(() => {
  const tag =
    props.instances[0]?.model_tag ??
    models.engineConfig(props.instances[0]?.key ?? '')?.settings?.model_tag
  return tag != null ? String(tag) : null
})

const readyCount = computed(() => props.instances.filter((m) => m.state === 'ready').length)
const total = computed(() => props.instances.length)

// Worst-wins rollup so the header dot reflects the group's least-healthy state.
const headerState = computed<ModelState>(() => {
  const states = props.instances.map((m) => m.state)
  if (states.includes('failed')) return 'failed'
  if (states.some((s) => s === 'starting' || s === 'stopping')) return 'starting'
  if (states.every((s) => s === 'ready')) return 'ready'
  return 'stopped'
})

const startableKeys = computed(() =>
  props.instances.filter((m) => !['ready', 'starting'].includes(m.state)).map((m) => m.key),
)
const stoppableKeys = computed(() =>
  props.instances.filter((m) => m.managed && m.state !== 'stopped').map((m) => m.key),
)

// Live router metrics — only meaningful while an instance is actually serving.
function metricsFor(m: ModelView) {
  if (m.state !== 'ready' && m.state !== 'starting') return null
  const inst = m.key.split('::')[1] ?? ''
  return traffic.metrics[props.group]?.[inst] ?? null
}
function loadPct(m: ModelView): number {
  const im = metricsFor(m)
  if (!im) return 0
  // Soft saturation: a handful of running/waiting reqs fills the meter.
  // Fields are null when the scrape failed — treat as 0 for the meter.
  const score = (im.waiting ?? 0) * 10 + (im.running ?? 0) * 3 + (im.kv_cache_usage_perc ?? 0) * 100
  return Math.min(100, score)
}
/** KV cache as a percent string, or "—" when the metric is unavailable. */
function kvText(m: ModelView): string {
  const kv = metricsFor(m)?.kv_cache_usage_perc
  return kv == null ? '—' : `${(kv * 100).toFixed(0)}%`
}

const Icon = computed(() => (kind.value === 'llm' ? Sparkles : Box))

function isBusy(key: string) {
  return models.pending.has(key)
}
function isRunning(state: ModelState) {
  return state === 'ready' || state === 'starting'
}

// One LLM may start at a time: block this group's start controls while another
// LLM is mid-start (embeddings are unrestricted).
const startLocked = computed(() => kind.value === 'llm' && control.isLlmStarting.value)
const startLockTitle = computed(() =>
  startLocked.value ? `已有模型啟動中（${control.startingLlmName()}），請待其完成` : '啟動',
)
</script>

<template>
  <Card glass class="overflow-hidden">
    <!-- Group header -->
    <div class="flex items-center justify-between gap-3 px-4 py-3">
      <div class="flex min-w-0 items-center gap-3">
        <div
          class="flex size-8 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-background/50"
          :class="kind === 'llm' ? 'text-[var(--chart-1)]' : 'text-[var(--chart-4)]'"
        >
          <component :is="Icon" class="size-4" />
        </div>
        <div class="min-w-0">
          <p class="truncate text-sm font-semibold leading-tight" :title="group">{{ group }}</p>
          <p class="truncate font-mono text-[11px] text-muted-foreground" :title="modelTag ?? ''">
            {{ modelTag ?? 'embedding / reranker' }}
          </p>
        </div>
      </div>
      <div class="flex shrink-0 items-center gap-1.5">
        <StatusDot :state="headerState" />
        <span class="text-xs text-muted-foreground tabular">{{ readyCount }}/{{ total }} 就緒</span>
      </div>
    </div>

    <!-- Instance rows (compact, single line) -->
    <div class="divide-y divide-border/40 border-t border-border/40">
      <div
        v-for="m in visibleInstances"
        :key="m.key"
        class="group/row flex cursor-pointer items-center gap-2.5 px-4 py-2 transition-colors hover:bg-accent/40"
        @click="emit('open', m.key)"
      >
        <StatusDot :state="m.state" size="sm" />
        <span class="font-mono text-[13px] font-medium">{{ m.key.split('::')[1] }}</span>
        <Badge
          v-if="models.gpuForKey(m.key, m.kind) !== null"
          variant="muted"
          class="px-1.5 py-0 text-[10px]"
        >
          GPU {{ models.gpuForKey(m.key, m.kind) }}
        </Badge>
        <span class="font-mono text-[11px] text-muted-foreground">:{{ m.port }}</span>
        <Tooltip v-if="m.restart_count" :text="`崩潰後自動重啟 ${m.restart_count} 次`">
          <span class="flex items-center gap-0.5 text-[10px] text-status-starting">
            <RotateCw class="size-3" />{{ m.restart_count }}
          </span>
        </Tooltip>

        <div class="ml-auto flex items-center gap-2.5">
          <!-- Live load: shown only while serving -->
          <Tooltip v-if="metricsFor(m)">
            <div class="hidden cursor-help items-center gap-1.5 sm:flex" @click.stop>
              <div class="h-1 w-10 overflow-hidden rounded-full bg-muted">
                <div
                  class="h-full rounded-full bg-[var(--chart-1)] transition-all"
                  :style="{ width: `${Math.max(4, loadPct(m))}%` }"
                />
              </div>
              <span class="w-16 text-[11px] text-muted-foreground tabular">
                {{ metricsFor(m)!.running ?? 0 }}r · {{ metricsFor(m)!.waiting ?? 0 }}w
              </span>
            </div>
            <template #content>
              <p class="font-medium">即時負載（路由器 /metrics）</p>
              <ul class="mt-1 space-y-0.5 text-muted-foreground">
                <li><span class="tabular text-foreground">{{ metricsFor(m)!.running ?? '—' }}</span> 執行中 — 目前正在生成的請求</li>
                <li><span class="tabular text-foreground">{{ metricsFor(m)!.waiting ?? '—' }}</span> 等待中 — 此實例的排隊請求</li>
                <li>
                  KV 快取 <span class="tabular text-foreground">{{ kvText(m) }}</span> 已使用
                </li>
              </ul>
            </template>
          </Tooltip>

          <Button
            v-if="!isRunning(m.state)"
            size="icon-sm"
            variant="success"
            :disabled="isBusy(m.key) || startLocked"
            :title="startLockTitle"
            @click.stop="control.request(m.key, 'start')"
          >
            <Loader2 v-if="isBusy(m.key)" class="size-3.5 animate-spin" /><Play v-else class="size-3.5" />
          </Button>
          <Button
            v-else
            size="icon-sm"
            variant="outline"
            :disabled="isBusy(m.key) || !m.managed"
            :title="!m.managed ? '外部模型 — 非本後端管理' : '停止'"
            @click.stop="control.request(m.key, 'stop')"
          >
            <Loader2 v-if="isBusy(m.key)" class="size-3.5 animate-spin" /><Square v-else class="size-3.5" />
          </Button>
        </div>
      </div>

      <!-- Collapse toggle for large groups -->
      <button
        v-if="collapsible"
        class="flex w-full items-center justify-center gap-1 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
        @click="expanded = !expanded"
      >
        <ChevronDown class="size-3.5 transition-transform" :class="expanded && 'rotate-180'" />
        {{ expanded ? '收起' : `顯示更多 ${hiddenCount} 個` }}
      </button>
    </div>

    <!-- Group actions: only meaningful for multi-instance groups (a single
         instance already has its own start/stop button on the row). -->
    <div v-if="instances.length > 1" class="flex gap-2 border-t border-border/40 p-3">
      <Button
        size="sm"
        variant="success"
        class="flex-1"
        :disabled="!startableKeys.length || startLocked"
        :title="startLocked ? startLockTitle : '依序啟動所有實例'"
        @click="control.requestMany(startableKeys, 'start')"
      >
        <Play class="size-3.5" />全部啟動
      </Button>
      <Button
        size="sm"
        variant="outline"
        class="flex-1"
        :disabled="!stoppableKeys.length"
        @click="control.requestMany(stoppableKeys, 'stop')"
      >
        <Square class="size-3.5" />全部停止
      </Button>
    </div>
  </Card>
</template>
