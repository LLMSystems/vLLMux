<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Loader2, Pencil, Play, RefreshCw, Square, Trash2 } from '@lucide/vue'
import Sheet from '@/components/ui/Sheet.vue'
import Tabs from '@/components/ui/Tabs.vue'
import TabsList from '@/components/ui/TabsList.vue'
import TabsTrigger from '@/components/ui/TabsTrigger.vue'
import TabsContent from '@/components/ui/TabsContent.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import StatusDot from '@/components/StatusDot.vue'
import { useModelsStore } from '@/stores/models'
import { useTrafficStore } from '@/stores/traffic'
import { useModelControl } from '@/composables/useModelControl'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/lib/toast'
import { formatDuration, formatLatency, formatNumber, formatPercent, formatTime } from '@/lib/utils'
import type { StateEvent } from '@/types/api'

const open = defineModel<boolean>('open', { default: false })
const props = defineProps<{ modelKey: string | null }>()
const emit = defineEmits<{ deleted: [key: string]; edit: [key: string] }>()

const models = useModelsStore()
const traffic = useTrafficStore()
const control = useModelControl()

const tab = ref('overview')
const events = ref<StateEvent[]>([])
const logs = ref('')
const logsError = ref<string | null>(null)
const loadingLogs = ref(false)

const model = computed(() => (props.modelKey ? models.byKey.get(props.modelKey) : undefined))
const engine = computed(() => (props.modelKey ? models.engineConfig(props.modelKey) : null))
const gpu = computed(() =>
  model.value ? models.gpuForKey(model.value.key, model.value.kind) : null,
)
const busy = computed(() => (props.modelKey ? models.pending.has(props.modelKey) : false))

// Every vLLM parameter from model_config, shown generically (model_tag is
// already surfaced in the header, so it's filtered out here).
const vllmParams = computed(() =>
  Object.entries(engine.value?.settings ?? {}).filter(([k]) => k !== 'model_tag'),
)
function fmtParam(v: string | number | boolean | null): string {
  if (v === null) return '—'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return String(v)
}
const isRunning = computed(() => !!model.value && ['ready', 'starting'].includes(model.value.state))
const removable = computed(() => !!model.value && ['stopped', 'failed'].includes(model.value.state))
// Params only apply on the next launch, so editing is allowed only while stopped.
const editable = computed(() => removable.value && model.value?.kind === 'llm')

function edit() {
  if (model.value) emit('edit', model.value.key)
}
// One LLM may start at a time: block this model's start while another is mid-start.
const startLocked = computed(() => model.value?.kind === 'llm' && control.isLlmStarting.value)
const startLockTitle = computed(() =>
  startLocked.value ? `已有模型啟動中（${control.startingLlmName()}），請待其完成` : '啟動',
)

async function remove() {
  if (!model.value) return
  const k = model.value.key
  try {
    await api.deleteModel(k)
    void api.routerReload() // keep the router's routing table in sync
    toast.success(`已移除 ${k}`)
    open.value = false
    emit('deleted', k)
  } catch (e) {
    toast.error('無法移除模型', {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  }
}

const metrics = computed(() => {
  if (!props.modelKey) return null
  const [group, instance] = props.modelKey.split('::')
  return traffic.metrics[group ?? '']?.[instance ?? ''] ?? null
})
const usageRow = computed(() => {
  const group = props.modelKey?.split('::')[0]
  return traffic.usage.find((u) => u.model_key === group) ?? null
})

async function loadEvents() {
  if (!props.modelKey) return
  try {
    events.value = await api.getModelEvents(props.modelKey, 50)
  } catch {
    events.value = []
  }
}
async function loadLogs() {
  if (!props.modelKey) return
  loadingLogs.value = true
  logsError.value = null
  try {
    const res = await api.getLogs(props.modelKey, 400)
    logs.value = res.content
  } catch (e) {
    logsError.value = e instanceof Error ? e.message : 'No log available'
    logs.value = ''
  } finally {
    loadingLogs.value = false
  }
}

watch(
  () => [open.value, props.modelKey] as const,
  ([isOpen]) => {
    if (isOpen && props.modelKey) {
      tab.value = 'overview'
      void loadEvents()
      void loadLogs()
    }
  },
  { immediate: true },
)

const eventColor: Record<string, string> = {
  ready: 'text-status-ready',
  starting: 'text-status-starting',
  stopping: 'text-status-stopping',
  failed: 'text-status-failed',
  stopped: 'text-status-stopped',
}
</script>

<template>
  <Sheet v-model:open="open" :title="model ? model.key : 'Model'">
    <div v-if="model" class="space-y-6 p-6">
      <!-- Header summary -->
      <div class="flex items-start justify-between gap-4">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <h2 class="truncate text-lg font-semibold">{{ model.key.split('::')[0] }}</h2>
            <StatusBadge :state="model.state" />
          </div>
          <p class="mt-0.5 truncate font-mono text-xs text-muted-foreground">
            {{ model.model_tag ?? 'embedding / reranker' }}
          </p>
        </div>
        <div class="flex items-center gap-2">
          <Button
            v-if="editable"
            size="icon-sm"
            variant="ghost"
            title="編輯參數（需先停止；vLLM 參數為群組共用）"
            @click="edit"
          >
            <Pencil class="size-4" />
          </Button>
          <Button
            v-if="removable"
            size="icon-sm"
            variant="ghost"
            title="移除模型（僅限動態新增的模型）"
            @click="remove"
          >
            <Trash2 class="size-4" />
          </Button>
          <Button
            v-if="!isRunning"
            size="sm"
            variant="success"
            :disabled="busy || startLocked"
            :title="startLockTitle"
            @click="control.request(model.key, 'start')"
          >
            <Loader2 v-if="busy" class="size-4 animate-spin" /><Play v-else class="size-4" />啟動
          </Button>
          <Button
            v-else
            size="sm"
            variant="outline"
            :disabled="busy || !model.managed"
            @click="control.request(model.key, 'stop')"
          >
            <Loader2 v-if="busy" class="size-4 animate-spin" /><Square v-else class="size-4" />停止
          </Button>
        </div>
      </div>

      <Tabs v-model="tab">
        <TabsList class="w-full">
          <TabsTrigger value="overview" class="flex-1">概覽</TabsTrigger>
          <TabsTrigger value="events" class="flex-1">事件</TabsTrigger>
          <TabsTrigger value="logs" class="flex-1">日誌</TabsTrigger>
        </TabsList>

        <!-- Overview -->
        <TabsContent value="overview" class="mt-4 space-y-4">
          <div class="grid grid-cols-2 gap-3">
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">端點</p>
              <p class="mt-0.5 font-mono text-sm">{{ model.host }}:{{ model.port }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">顯示卡</p>
              <p class="mt-0.5 font-mono text-sm tabular">{{ gpu !== null ? `cuda:${gpu}` : '—' }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">程序 ID</p>
              <p class="mt-0.5 font-mono text-sm tabular">{{ model.pid ?? '—' }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">管理方式</p>
              <p class="mt-0.5 text-sm">{{ model.managed ? '是（可控制）' : '外部' }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">運行時間</p>
              <p class="mt-0.5 text-sm tabular">
                {{ model.ready_at ? formatDuration(model.ready_at) : '—' }}
              </p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">自動重啟次數</p>
              <p class="mt-0.5 text-sm tabular" :class="model.restart_count ? 'text-status-starting' : ''">
                {{ model.restart_count ?? 0 }}
              </p>
            </div>
          </div>

          <!-- Full vLLM model_config -->
          <div v-if="vllmParams.length">
            <p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              vLLM 參數（model_config）
            </p>
            <div class="overflow-hidden rounded-lg border border-border/60">
              <div
                v-for="([k, v], i) in vllmParams"
                :key="k"
                class="flex items-center justify-between gap-4 px-3 py-2 text-sm"
                :class="i % 2 ? 'bg-background/20' : 'bg-background/40'"
              >
                <span class="font-mono text-xs text-muted-foreground">{{ k }}</span>
                <span class="truncate font-mono text-sm tabular" :title="fmtParam(v)">{{ fmtParam(v) }}</span>
              </div>
            </div>
          </div>

          <!-- Live router metrics -->
          <div>
            <p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              即時負載（路由器 /metrics）
            </p>
            <div v-if="metrics" class="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">{{ metrics.running }}</p>
                <p class="text-xs text-muted-foreground">執行中</p>
              </div>
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">{{ metrics.waiting }}</p>
                <p class="text-xs text-muted-foreground">等待中</p>
              </div>
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">
                  {{ metrics.kv_cache_usage_perc == null ? '—' : formatPercent(metrics.kv_cache_usage_perc * 100) }}
                </p>
                <p class="text-xs text-muted-foreground">KV 快取</p>
              </div>
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">
                  {{ formatNumber(metrics.generation_tokens, true) }}
                </p>
                <p class="text-xs text-muted-foreground">生成 tokens</p>
              </div>
            </div>
            <p v-else class="text-sm text-muted-foreground">無即時指標（路由器無法連線或模型閒置）。</p>
          </div>

          <!-- Usage rollup -->
          <div v-if="usageRow">
            <p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">用量</p>
            <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">{{ formatNumber(usageRow.count) }}</p>
                <p class="text-xs text-muted-foreground">請求次數</p>
              </div>
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">{{ formatLatency(usageRow.p50_latency_ms) }}</p>
                <p class="text-xs text-muted-foreground">p50</p>
              </div>
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">{{ formatLatency(usageRow.p95_latency_ms) }}</p>
                <p class="text-xs text-muted-foreground">p95</p>
              </div>
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">{{ formatNumber(usageRow.total_tokens) }}</p>
                <p class="text-xs text-muted-foreground">tokens</p>
              </div>
            </div>
          </div>

          <div
            v-if="model.last_error"
            class="rounded-lg border border-status-failed/30 bg-status-failed/10 p-3"
          >
            <p class="text-xs font-medium text-status-failed">最後錯誤</p>
            <pre class="mt-1 whitespace-pre-wrap break-words font-mono text-xs text-status-failed/90">{{ model.last_error }}</pre>
          </div>
        </TabsContent>

        <!-- Events timeline -->
        <TabsContent value="events" class="mt-4">
          <div class="flex justify-end">
            <Button variant="ghost" size="sm" @click="loadEvents"><RefreshCw class="size-3.5" />重新整理</Button>
          </div>
          <ol class="relative mt-2 space-y-4 border-l border-border/70 pl-5">
            <li v-for="ev in events" :key="ev.id" class="relative">
              <StatusDot :state="ev.to_state" class="absolute -left-[1.45rem] top-1" />
              <div class="flex items-center gap-2 text-sm">
                <span class="text-muted-foreground tabular">{{ formatTime(ev.ts) }}</span>
                <span :class="eventColor[ev.from_state]">{{ ev.from_state }}</span>
                <span class="text-muted-foreground">→</span>
                <span class="font-medium" :class="eventColor[ev.to_state]">{{ ev.to_state }}</span>
              </div>
              <p v-if="ev.detail" class="mt-1 break-words font-mono text-xs text-muted-foreground">
                {{ ev.detail }}
              </p>
            </li>
            <li v-if="!events.length" class="text-sm text-muted-foreground">尚無事件記錄。</li>
          </ol>
        </TabsContent>

        <!-- Logs -->
        <TabsContent value="logs" class="mt-4 space-y-2">
          <div class="flex items-center justify-between">
            <Badge variant="outline" class="font-mono text-[11px]">tail 400</Badge>
            <Button variant="ghost" size="sm" :disabled="loadingLogs" @click="loadLogs">
              <RefreshCw class="size-3.5" :class="loadingLogs && 'animate-spin'" />重新整理
            </Button>
          </div>
          <pre
            v-if="logs"
            class="max-h-[60vh] overflow-auto rounded-lg border border-border/60 bg-black/40 p-3 font-mono text-xs leading-relaxed text-foreground/90"
          >{{ logs }}</pre>
          <p v-else class="rounded-lg border border-border/60 bg-background/40 p-4 text-sm text-muted-foreground">
            {{ logsError ?? '無日誌內容。' }}
          </p>
        </TabsContent>
      </Tabs>
    </div>
  </Sheet>
</template>
