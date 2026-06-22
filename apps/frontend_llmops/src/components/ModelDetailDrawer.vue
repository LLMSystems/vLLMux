<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Download, Loader2, Pencil, Play, RefreshCw, Search, Square, Trash2 } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import Sheet from '@/components/ui/Sheet.vue'
import Tabs from '@/components/ui/Tabs.vue'
import TabsList from '@/components/ui/TabsList.vue'
import TabsTrigger from '@/components/ui/TabsTrigger.vue'
import TabsContent from '@/components/ui/TabsContent.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import StatusDot from '@/components/StatusDot.vue'
import EmbeddingModelDialog from '@/components/EmbeddingModelDialog.vue'
import { useModelsStore } from '@/stores/models'
import { useTrafficStore } from '@/stores/traffic'
import { useModelControl } from '@/composables/useModelControl'
import { useAuth } from '@/composables/useAuth'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/lib/toast'
import { formatDuration, formatLatency, formatNumber, formatPercent, formatTime } from '@/lib/utils'
import { routingStrategyLabel } from '@/lib/routingStrategies'
import type { EmbeddingModelParams, LoraAdapter, ModelStartupMetrics, StateEvent } from '@/types/api'

const open = defineModel<boolean>('open', { default: false })
const props = defineProps<{ modelKey: string | null }>()
const emit = defineEmits<{ deleted: [key: string]; edit: [key: string] }>()

const models = useModelsStore()
const traffic = useTrafficStore()
const control = useModelControl()
const { ensureUnlocked } = useAuth()
const { t } = useI18n()

const tab = ref('overview')
const events = ref<StateEvent[]>([])
const logs = ref('')
const logsError = ref<string | null>(null)
const loadingLogs = ref(false)
const logFilter = ref('')

const filteredLogs = computed(() => {
  if (!logFilter.value.trim()) return logs.value
  const q = logFilter.value.toLowerCase()
  return logs.value
    .split('\n')
    .filter((line) => line.toLowerCase().includes(q))
    .join('\n')
})

function downloadLogs() {
  if (!logs.value || !props.modelKey) return
  const blob = new Blob([logs.value], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${props.modelKey.replace('::', '__')}.log`
  a.click()
  URL.revokeObjectURL(url)
}

const model = computed(() => (props.modelKey ? models.byKey.get(props.modelKey) : undefined))
const engine = computed(() => (props.modelKey ? models.engineConfig(props.modelKey) : null))
const gpu = computed(() =>
  model.value ? models.gpuForKey(model.value.key, model.value.kind) : null,
)
const busy = computed(() => (props.modelKey ? models.pending.has(props.modelKey) : false))

// Every vLLM parameter from model_config, shown generically. model_tag is in the
// header; lora_modules has its own section; routing_strategy is a router-only knob
// (not a vLLM flag), surfaced separately below.
const vllmParams = computed(
  () =>
    Object.entries(engine.value?.settings ?? {}).filter(
      ([k]) => k !== 'model_tag' && k !== 'lora_modules' && k !== 'routing_strategy',
    ) as [string, string | number | boolean | null][],
)
// Router load-balancing policy for the group (shown apart from vLLM flags).
const routingStrategy = computed(() => {
  const s = engine.value?.settings?.routing_strategy
  return typeof s === 'string' && s ? s : null
})
// LoRA adapters mounted on this group (rendered apart from the scalar params).
const loras = computed(() => engine.value?.settings?.lora_modules ?? [])
// Runtime (hot) LoRA load/unload is only possible when the model is running and
// was launched with allow_runtime_lora (the VLLM_ALLOW_RUNTIME_LORA env toggle).
const canHotLora = computed(
  () => model.value?.state === 'ready' && !!engine.value?.settings?.allow_runtime_lora,
)
const loraLibrary = ref<LoraAdapter[]>([])
const pickLoraPath = ref('')
const hotLoraBusy = ref(false)
async function loadLoraLibrary() {
  try {
    loraLibrary.value = (await api.listLora()).adapters
  } catch {
    loraLibrary.value = []
  }
}
async function hotLoadLora() {
  const key = props.modelKey
  const a = loraLibrary.value.find((x) => x.path === pickLoraPath.value)
  if (!key || !a || hotLoraBusy.value) return
  if (!(await ensureUnlocked())) return
  hotLoraBusy.value = true
  try {
    await api.loadLora(key, { name: a.name, path: a.path, base_model_name: a.base_model ?? undefined })
    await api.routerReload() // make the new adapter routable now
    await models.loadConfig() // refresh the mounted list
    pickLoraPath.value = ''
    toast.success(t('modelDetail.hotLoadSuccess', { name: a.name }), {
      description: t('modelDetail.hotLoadSuccessDesc'),
    })
  } catch (e) {
    toast.error(t('modelDetail.hotLoadFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    hotLoraBusy.value = false
  }
}
async function hotUnloadLora(name: string) {
  const key = props.modelKey
  if (!key || hotLoraBusy.value) return
  if (!(await ensureUnlocked())) return
  if (!confirm(t('modelDetail.hotUnloadConfirm', { name }))) return
  hotLoraBusy.value = true
  try {
    await api.unloadLora(key, name)
    await api.routerReload()
    await models.loadConfig()
    toast.success(t('modelDetail.hotUnloadSuccess', { name }))
  } catch (e) {
    toast.error(t('modelDetail.hotUnloadFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    hotLoraBusy.value = false
  }
}
// Embedding server hosts several models; list them since it has no model_config.
const servedModels = computed(() => {
  const e = models.config?.embedding_server
  if (model.value?.kind !== 'embedding' || !e) return []
  return [
    ...Object.entries(e.embedding_models).map(([name, params]) => ({ name, type: 'embedding' as const, params })),
    ...Object.entries(e.reranking_models).map(([name, params]) => ({ name, type: 'reranking' as const, params })),
  ]
})
// Embedding params are editable only while the server is stopped.
const editEmbeddingOpen = ref(false)
type ServedModel = { type: 'embedding' | 'reranking'; name: string; params: EmbeddingModelParams }
const editTarget = ref<ServedModel | null>(null)
function editEmbeddingModel(sm: ServedModel) {
  editTarget.value = sm
  editEmbeddingOpen.value = true
}
function paramSummary(params: EmbeddingModelParams): string {
  const bits: string[] = []
  if (params.max_length != null) bits.push(`len ${params.max_length}`)
  if (params.use_gpu != null) bits.push(params.use_gpu ? 'GPU' : 'CPU')
  if (params.use_float16) bits.push('fp16')
  return bits.join(' · ')
}
function fmtParam(v: string | number | boolean | null): string {
  if (v === null) return '—'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return String(v)
}
// Offer Stop whenever there's a live process to reap: running/starting, or a
// FAILED instance that still has a process (e.g. a hung start before cleanup).
const canStop = computed(
  () =>
    !!model.value &&
    (['ready', 'starting'].includes(model.value.state) ||
      (model.value.state === 'failed' && model.value.pid != null)),
)
const removable = computed(() => !!model.value && ['stopped', 'failed'].includes(model.value.state))
// Params only apply on the next launch, so editing is allowed only while stopped.
const editable = computed(() => removable.value && model.value?.kind === 'llm')

function edit() {
  if (model.value) emit('edit', model.value.key)
}
// One LLM may start at a time: block this model's start while another is mid-start.
const startLocked = computed(() => model.value?.kind === 'llm' && control.isLlmStarting.value)
const startLockTitle = computed(() =>
  startLocked.value
    ? t('modelDetail.startLocked', { name: control.startingLlmName() })
    : t('modelDetail.startLabel'),
)

async function remove() {
  if (!model.value) return
  if (!(await ensureUnlocked())) return
  const k = model.value.key
  try {
    await api.deleteModel(k)
    void api.routerReload() // keep the router's routing table in sync
    toast.success(t('modelDetail.removeSuccess', { key: k }))
    open.value = false
    emit('deleted', k)
  } catch (e) {
    toast.error(t('modelDetail.removeFailed'), {
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

// vLLM startup capacity/memory metrics — only fetched for a READY instance.
const startupMetrics = ref<ModelStartupMetrics | null>(null)
async function loadStartupMetrics() {
  startupMetrics.value = null
  if (!props.modelKey || model.value?.state !== 'ready') return
  try {
    const m = await api.getModelMetrics(props.modelKey)
    startupMetrics.value = m.has_any ? m : null
  } catch {
    startupMetrics.value = null
  }
}
function fmt(v: number | null | undefined, digits = 1, suffix = '') {
  return v == null ? '—' : v.toFixed(digits) + suffix
}

watch(
  () => [open.value, props.modelKey] as const,
  ([isOpen]) => {
    if (isOpen && props.modelKey) {
      tab.value = 'overview'
      void loadEvents()
      void loadLogs()
      void loadLoraLibrary()
      void loadStartupMetrics()
    }
  },
  { immediate: true },
)
// Re-fetch capacity once the model actually reaches READY (its log only has the
// metrics after loading finishes).
watch(
  () => model.value?.state,
  (s) => {
    if (open.value && s === 'ready') void loadStartupMetrics()
    else if (s !== 'ready') startupMetrics.value = null
  },
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
  <Sheet v-model:open="open" :title="model ? model.key : t('common.model')">
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
            :title="$t('modelDetail.editParams')"
            @click="edit"
          >
            <Pencil class="size-4" />
          </Button>
          <Button
            v-if="removable"
            size="icon-sm"
            variant="ghost"
            :title="$t('modelDetail.removeModel')"
            @click="remove"
          >
            <Trash2 class="size-4" />
          </Button>
          <Button
            v-if="!canStop"
            size="sm"
            variant="success"
            :disabled="busy || startLocked"
            :title="startLockTitle"
            @click="control.request(model.key, 'start')"
          >
            <Loader2 v-if="busy" class="size-4 animate-spin" /><Play v-else class="size-4" />{{ $t('modelDetail.startLabel') }}
          </Button>
          <Button
            v-else
            size="sm"
            variant="outline"
            :disabled="!model.managed || model.state === 'stopping'"
            :title="model.state === 'failed'
              ? $t('modelDetail.terminateLabel')
              : model.state === 'starting'
                ? $t('modelDetail.abortLabel')
                : $t('modelDetail.stopLabel')"
            @click="control.request(model.key, 'stop')"
          >
            <Loader2 v-if="busy" class="size-4 animate-spin" /><Square v-else class="size-4" />{{ model.state === 'failed' ? $t('modelDetail.terminateLabel') : $t('modelDetail.stopLabel') }}
          </Button>
        </div>
      </div>

      <Tabs v-model="tab">
        <TabsList class="w-full">
          <TabsTrigger value="overview" class="flex-1">{{ $t('modelDetail.overview') }}</TabsTrigger>
          <TabsTrigger value="events" class="flex-1">{{ $t('modelDetail.events') }}</TabsTrigger>
          <TabsTrigger value="logs" class="flex-1">{{ $t('modelDetail.logs') }}</TabsTrigger>
        </TabsList>

        <!-- Overview -->
        <TabsContent value="overview" class="mt-4 space-y-4">
          <div class="grid grid-cols-2 gap-3">
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">{{ $t('modelDetail.endpoint') }}</p>
              <p class="mt-0.5 font-mono text-sm">{{ model.host }}:{{ model.port }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">{{ $t('modelDetail.gpu') }}</p>
              <p class="mt-0.5 font-mono text-sm tabular">{{ gpu !== null ? `cuda:${gpu}` : '—' }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">{{ $t('modelDetail.pid') }}</p>
              <p class="mt-0.5 font-mono text-sm tabular">{{ model.pid ?? '—' }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">{{ $t('modelDetail.managedLabel') }}</p>
              <p class="mt-0.5 text-sm">{{ model.managed ? $t('modelDetail.managedYes') : $t('modelDetail.managedExternal') }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">{{ $t('modelDetail.uptime') }}</p>
              <p class="mt-0.5 text-sm tabular">
                {{ model.ready_at ? formatDuration(model.ready_at) : '—' }}
              </p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3">
              <p class="text-xs text-muted-foreground">{{ $t('modelDetail.autoRestarts') }}</p>
              <p class="mt-0.5 text-sm tabular" :class="model.restart_count ? 'text-status-starting' : ''">
                {{ model.restart_count ?? 0 }}
              </p>
            </div>
          </div>

          <!-- vLLM startup capacity snapshot (READY instances only) -->
          <div v-if="startupMetrics" class="space-y-2.5">
            <p class="text-xs font-medium uppercase tracking-wide text-muted-foreground">{{ $t('modelDetail.startupSnapshot') }}</p>

            <!-- Capacity headline -->
            <div
              v-if="startupMetrics.capacity?.kv_cache_tokens != null || startupMetrics.capacity?.max_concurrency != null"
              class="rounded-lg border border-[var(--chart-1)]/30 bg-[var(--chart-1)]/5 p-3"
            >
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <p class="text-xs text-muted-foreground">{{ $t('modelDetail.kvCacheCapacity') }}</p>
                  <p class="text-lg font-semibold tabular">
                    {{ startupMetrics.capacity?.kv_cache_tokens != null ? formatNumber(startupMetrics.capacity.kv_cache_tokens) : '—' }}
                    <span class="text-xs font-normal text-muted-foreground">tokens</span>
                  </p>
                </div>
                <div>
                  <p class="text-xs text-muted-foreground">
                    {{ $t('modelDetail.maxConcurrency') }}（{{ startupMetrics.capacity?.concurrency_req_tokens != null ? $t('modelDetail.concurrencyReqTok', { n: formatNumber(startupMetrics.capacity.concurrency_req_tokens) }) : '?' }}）
                  </p>
                  <p class="text-lg font-semibold tabular">{{ fmt(startupMetrics.capacity?.max_concurrency, 1, '×') }}</p>
                </div>
              </div>
              <p v-if="startupMetrics.capacity?.max_concurrency != null" class="mt-1 text-[11px] text-muted-foreground">
                {{ $t('modelDetail.concurrencyHint', { n: Math.floor(startupMetrics.capacity.max_concurrency) }) }}
              </p>
            </div>

            <!-- Memory breakdown -->
            <div v-if="startupMetrics.memory" class="grid grid-cols-3 gap-2 text-xs">
              <div class="rounded-md border border-border/60 p-2">
                <p class="text-muted-foreground">{{ $t('modelDetail.memWeights') }}</p>
                <p class="tabular">{{ fmt(startupMetrics.memory.model_gib, 2, ' GiB') }}</p>
              </div>
              <div class="rounded-md border border-border/60 p-2">
                <p class="text-muted-foreground">CUDA graph</p>
                <p class="tabular">{{ fmt(startupMetrics.memory.cudagraph_gib, 2, ' GiB') }}</p>
              </div>
              <div class="rounded-md border border-border/60 p-2">
                <p class="text-muted-foreground">KV cache</p>
                <p class="tabular">{{ fmt(startupMetrics.memory.kv_cache_gib, 2, ' GiB') }}</p>
              </div>
            </div>

            <!-- Startup timing -->
            <div v-if="startupMetrics.startup" class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
              <span>{{ $t('modelDetail.startupWeightsLoad') }} {{ fmt(startupMetrics.startup.weights_load_s, 2, 's') }}</span>
              <span>{{ $t('modelDetail.startupModelLoad') }} {{ fmt(startupMetrics.startup.model_load_s, 1, 's') }}</span>
              <span>{{ $t('modelDetail.startupCompile') }} {{ fmt(startupMetrics.startup.compile_s, 1, 's') }}</span>
              <span>{{ $t('modelDetail.startupWarmup') }} {{ fmt(startupMetrics.startup.warmup_s, 2, 's') }}</span>
            </div>

            <!-- gpu_memory_utilization advisory -->
            <div
              v-if="startupMetrics.gpu_mem_util?.suggested != null && startupMetrics.gpu_mem_util.suggested !== startupMetrics.gpu_mem_util.current"
              class="rounded-md border border-amber-500/40 bg-amber-500/5 p-2.5 text-[11px]"
            >
              <p class="text-amber-600">{{ $t('modelDetail.gpuMemUtilTitle') }}</p>
              <p class="mt-0.5 leading-relaxed text-muted-foreground">
                {{ $t('modelDetail.gpuMemUtilDesc', {
                  current: fmt(startupMetrics.gpu_mem_util.current, 2),
                  effective: fmt(startupMetrics.gpu_mem_util.effective, 2),
                  suggested: fmt(startupMetrics.gpu_mem_util.suggested, 2),
                }) }}
              </p>
            </div>
          </div>

          <!-- Embedding server: the models it serves (editable while stopped) -->
          <div v-if="servedModels.length">
            <p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {{ $t('modelDetail.servedModels') }}
            </p>
            <div class="overflow-hidden rounded-lg border border-border/60">
              <div
                v-for="(sm, i) in servedModels"
                :key="sm.name"
                class="flex items-center gap-3 px-3 py-2 text-sm"
                :class="i % 2 ? 'bg-background/20' : 'bg-background/40'"
              >
                <Badge variant="muted" :class="sm.type === 'embedding' ? 'text-[var(--chart-4)]' : 'text-[var(--chart-2)]'">
                  {{ sm.type === 'embedding' ? $t('common.embedding') : $t('common.reranking') }}
                </Badge>
                <div class="min-w-0 flex-1">
                  <p class="truncate font-mono text-sm">{{ sm.name }}</p>
                  <p v-if="paramSummary(sm.params)" class="truncate text-xs text-muted-foreground tabular">{{ paramSummary(sm.params) }}</p>
                </div>
                <Button
                  v-if="removable"
                  size="icon-sm"
                  variant="ghost"
                  :title="$t('modelDetail.editEmbedding')"
                  @click="editEmbeddingModel(sm)"
                >
                  <Pencil class="size-3.5" />
                </Button>
              </div>
            </div>
          </div>

          <!-- Routing policy (router-only, not a vLLM flag) -->
          <div v-if="routingStrategy">
            <p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {{ $t('modelDetail.routingPolicy') }}
            </p>
            <div class="rounded-lg border border-border/60 bg-background/40 px-3 py-2 text-sm">
              {{ routingStrategyLabel(routingStrategy) }}
              <span class="font-mono text-xs text-muted-foreground">（{{ routingStrategy }}）</span>
            </div>
          </div>

          <!-- Full vLLM model_config -->
          <div v-if="vllmParams.length">
            <p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {{ $t('modelDetail.vllmParams') }}
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

          <!-- LoRA adapters -->
          <div v-if="loras.length || canHotLora">
            <p class="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {{ $t('modelDetail.loraAdapters') }}
              <Badge v-if="canHotLora" variant="outline" class="text-[10px] normal-case">{{ $t('modelDetail.hotLoadEnabled') }}</Badge>
            </p>
            <div v-if="loras.length" class="overflow-hidden rounded-lg border border-border/60">
              <div
                v-for="(l, i) in loras"
                :key="l.name"
                class="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                :class="i % 2 ? 'bg-background/20' : 'bg-background/40'"
              >
                <span class="shrink-0 font-mono text-xs">{{ l.name }}</span>
                <span class="min-w-0 flex-1 truncate text-right font-mono text-xs text-muted-foreground" :title="l.path">{{ l.path }}</span>
                <Button
                  v-if="canHotLora"
                  size="icon-sm"
                  variant="ghost"
                  :disabled="hotLoraBusy"
                  :title="$t('modelDetail.hotUnload')"
                  @click="hotUnloadLora(l.name)"
                >
                  <Trash2 class="size-3.5" />
                </Button>
              </div>
            </div>

            <!-- Hot-load a new adapter from the library -->
            <div v-if="canHotLora" class="mt-2 flex items-center gap-2">
              <select
                v-model="pickLoraPath"
                class="h-9 min-w-0 flex-1 rounded-md border border-input bg-background/40 px-2 font-mono text-xs"
              >
                <option value="">{{ $t('modelDetail.hotLoadPick') }}</option>
                <option v-for="a in loraLibrary" :key="a.path" :value="a.path">
                  {{ a.name }}{{ a.rank != null ? ` (r${a.rank})` : '' }}{{ a.base_model ? ` · ${a.base_model}` : '' }}
                </option>
              </select>
              <Button size="sm" :disabled="!pickLoraPath || hotLoraBusy" @click="hotLoadLora">
                <Loader2 v-if="hotLoraBusy" class="size-3.5 animate-spin" /><Download v-else class="size-3.5" />{{ $t('modelDetail.hotLoad') }}
              </Button>
            </div>

            <p class="mt-1 text-[11px] text-muted-foreground/80">
              <template v-if="canHotLora">{{ $t('modelDetail.hotLoadHint') }}</template>
              <template v-else>{{ $t('modelDetail.coldHint') }}</template>
            </p>
          </div>
          <!-- Running but hot-load not enabled: tell the user how to turn it on -->
          <div
            v-else-if="model?.state === 'ready' && !engine?.settings?.allow_runtime_lora"
            class="rounded-lg border border-border/60 bg-muted/20 p-3 text-[11px] text-muted-foreground"
          >
            {{ $t('modelDetail.hotLoadEnableHint') }}
          </div>

          <!-- Live router metrics -->
          <div>
            <p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {{ $t('modelDetail.liveMetrics') }}
            </p>
            <div v-if="metrics" class="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">{{ metrics.running }}</p>
                <p class="text-xs text-muted-foreground">{{ $t('modelDetail.metricsRunning') }}</p>
              </div>
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">{{ metrics.waiting }}</p>
                <p class="text-xs text-muted-foreground">{{ $t('modelDetail.metricsWaiting') }}</p>
              </div>
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">
                  {{ metrics.kv_cache_usage_perc == null ? '—' : formatPercent(metrics.kv_cache_usage_perc * 100) }}
                </p>
                <p class="text-xs text-muted-foreground">{{ $t('modelDetail.metricsKvCache') }}</p>
              </div>
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">
                  {{ formatNumber(metrics.generation_tokens, true) }}
                </p>
                <p class="text-xs text-muted-foreground">{{ $t('modelDetail.metricsGenTokens') }}</p>
              </div>
            </div>
            <p v-else class="text-sm text-muted-foreground">{{ $t('modelDetail.noMetrics') }}</p>
          </div>

          <!-- Usage rollup -->
          <div v-if="usageRow">
            <p class="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{{ $t('modelDetail.usageSection') }}</p>
            <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
                <p class="text-lg font-semibold tabular">{{ formatNumber(usageRow.count) }}</p>
                <p class="text-xs text-muted-foreground">{{ $t('modelDetail.requestCount') }}</p>
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
            <p class="text-xs font-medium text-status-failed">{{ $t('modelDetail.lastError') }}</p>
            <pre class="mt-1 whitespace-pre-wrap break-words font-mono text-xs text-status-failed/90">{{ model.last_error }}</pre>
          </div>
        </TabsContent>

        <!-- Events timeline -->
        <TabsContent value="events" class="mt-4">
          <div class="flex justify-end">
            <Button variant="ghost" size="sm" @click="loadEvents"><RefreshCw class="size-3.5" />{{ $t('common.refresh') }}</Button>
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
            <li v-if="!events.length" class="text-sm text-muted-foreground">{{ $t('modelDetail.noEventRecords') }}</li>
          </ol>
        </TabsContent>

        <!-- Logs -->
        <TabsContent value="logs" class="mt-4 space-y-2">
          <div class="flex items-center gap-2">
            <div class="relative flex-1">
              <Search class="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                v-model="logFilter"
                :placeholder="$t('modelDetail.filterLogs')"
                class="h-8 w-full rounded-md border border-input bg-background/40 pl-8 pr-2 text-xs"
              />
            </div>
            <Button variant="ghost" size="sm" :disabled="!logs" :title="$t('modelDetail.downloadLogs')" @click="downloadLogs">
              <Download class="size-3.5" />{{ $t('common.download') }}
            </Button>
            <Button variant="ghost" size="sm" :disabled="loadingLogs" @click="loadLogs">
              <RefreshCw class="size-3.5" :class="loadingLogs && 'animate-spin'" />{{ $t('common.refresh') }}
            </Button>
          </div>
          <pre
            v-if="filteredLogs"
            class="max-h-[60vh] overflow-auto rounded-lg border border-border/60 bg-black/40 p-3 font-mono text-xs leading-relaxed text-foreground/90"
          >{{ filteredLogs }}</pre>
          <p v-else class="rounded-lg border border-border/60 bg-background/40 p-4 text-sm text-muted-foreground">
            {{ logs ? $t('modelDetail.noFilterMatch') : (logsError ?? $t('modelDetail.noLogContent')) }}
          </p>
        </TabsContent>
      </Tabs>
    </div>
  </Sheet>

  <EmbeddingModelDialog
    v-if="editTarget"
    v-model:open="editEmbeddingOpen"
    :model-type="editTarget.type"
    :name="editTarget.name"
    :params="editTarget.params"
  />
</template>
