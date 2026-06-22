<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, ChevronDown, ExternalLink, Gauge, Loader2, Play, Plus, Square, Trash2, X } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import { api, ApiError } from '@/lib/api'
import { useModelsStore } from '@/stores/models'
import { lorasOfGroup } from '@/composables/useModelOptions'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatLatency, formatNumber, formatTime } from '@/lib/utils'
import type { PerfPoint, PerfRequest, PerfRun, SlaGroup } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import PerfSweepChart from '@/components/PerfSweepChart.vue'
import PerfCompareChart from '@/components/PerfCompareChart.vue'
import type { CompareSeries } from '@/components/PerfCompareChart.vue'

const models = useModelsStore()
const { ensureUnlocked } = useAuth()
const { t } = useI18n()

// ---- config form ----
const groups = computed(() => [...new Set(models.llms.map((m) => m.key.split('::')[0] ?? m.key))])
function groupReady(g: string) {
  return models.llms.some((m) => m.key.split('::')[0] === g && m.state === 'ready')
}
const model = ref('')
const name = ref('')
const target = ref<'router' | 'instance'>('router')
const instanceKey = ref('')
const dataset = ref<'random' | 'openqa'>('random')
const endpoint = ref<'chat' | 'completions'>('chat')
const mode = ref<'sweep' | 'openloop' | 'multiturn' | 'sla' | 'embedding' | 'rerank' | 'speed'>('sweep')
const MODES = [
  { v: 'sweep', label: 'benchmark.modeSweep' },
  { v: 'openloop', label: 'benchmark.modeOpenloop' },
  { v: 'multiturn', label: 'benchmark.modeMultiturn' },
  { v: 'sla', label: 'benchmark.modeSla' },
  { v: 'speed', label: 'benchmark.modeSpeed' },
  { v: 'embedding', label: 'benchmark.modeEmbedding' },
  { v: 'rerank', label: 'benchmark.modeRerank' },
] as const
const isEmbedMode = computed(() => mode.value === 'embedding' || mode.value === 'rerank')
// Embedding/rerank server + served-model picker (mirrors the Playground logic).
const embeddingServerReady = computed(() => models.byKey.get('embedding::default')?.state === 'ready')
const embModels = computed(() => {
  const e = models.config?.embedding_server
  if (!e) return [] as string[]
  return Object.keys(mode.value === 'rerank' ? (e.reranking_models ?? {}) : (e.embedding_models ?? {}))
})
const embModel = ref('')
const rerankDocs = ref(10)
const embPromptLen = ref(256)
watch([embModels, isEmbedMode], () => {
  if (isEmbedMode.value && (!embModel.value || !embModels.value.includes(embModel.value)))
    embModel.value = embModels.value[0] ?? ''
}, { immediate: true })
const parallelInput = ref('1,4,8,16')
const rateInput = ref('5,10,20')
const reqPerPoint = ref(50)
// multi-turn
const mtDataset = ref<'share_gpt_zh_multi_turn' | 'random_multi_turn' | 'custom_multi_turn'>('share_gpt_zh_multi_turn')
const mtDatasetPath = ref('')
const mtConcurrentInput = ref('4')
const mtTotal = ref(20)
const minTurns = ref(2)
const maxTurns = ref(4)
const mtConcurrent = computed(() =>
  mtConcurrentInput.value.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => n > 0),
)
const maxTokens = ref(256)
const promptLen = ref(512)
const prefixLen = ref(0)
const durationSec = ref(0)
const speedLong = ref(false)
const warmup = ref(0.1)
const stream = ref(true)
const launching = ref(false)

// SLA auto-tune form (conditions are OR-ed: each becomes its own search).
const slaVariable = ref<'parallel' | 'rate'>('parallel')
const slaConditions = ref<{ metric: string; op: string; value: number }[]>([
  { metric: 'p99_latency', op: '<=', value: 2 },
])
const slaLower = ref(1)
const slaUpper = ref(32)
const slaNumRuns = ref(1)
const slaFixedParallel = ref(10)
const SLA_METRICS = [
  { v: 'p99_latency', label: 'benchmark.slaMetrics.p99_latency' },
  { v: 'avg_latency', label: 'benchmark.slaMetrics.avg_latency' },
  { v: 'p99_ttft', label: 'benchmark.slaMetrics.p99_ttft' },
  { v: 'avg_ttft', label: 'benchmark.slaMetrics.avg_ttft' },
  { v: 'p99_tpot', label: 'benchmark.slaMetrics.p99_tpot' },
  { v: 'avg_tpot', label: 'benchmark.slaMetrics.avg_tpot' },
  { v: 'rps', label: 'benchmark.slaMetrics.rps' },
  { v: 'tps', label: 'benchmark.slaMetrics.tps' },
]
const SLA_OPS = ['<=', '<', '>=', '>', 'max', 'min']
function addCondition() {
  slaConditions.value.push({ metric: 'avg_ttft', op: '<=', value: 200 })
}
function removeCondition(i: number) {
  slaConditions.value.splice(i, 1)
}

// LoRA adapters mounted on ready base groups — selectable as a benchmark target.
const loraOptions = computed(() => {
  const out: { value: string; group: string }[] = []
  for (const g of groups.value) {
    if (!groupReady(g)) continue
    for (const l of lorasOfGroup(models, g)) out.push({ value: l.name, group: g })
  }
  return out
})
function baseGroupOf(v: string): string {
  return loraOptions.value.find((l) => l.value === v)?.group ?? v
}
const instanceOptions = computed(() =>
  models.llms
    .filter((m) => m.key.split('::')[0] === baseGroupOf(model.value))
    .map((m) => ({ key: m.key, ready: m.state === 'ready', state: m.state })),
)
const parallel = computed(() =>
  parallelInput.value.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => n > 0),
)
const rates = computed(() =>
  rateInput.value.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => n > 0),
)

watch(groups, (g) => {
  if (!model.value && g.length) model.value = g.find(groupReady) ?? g[0]!
}, { immediate: true })
watch(model, () => { instanceKey.value = instanceOptions.value.find((o) => o.ready)?.key ?? '' })

// ---- runs ----
const runs = ref<PerfRun[]>([])
const busy = ref(false)
const selectedId = ref<number | null>(null)
const selected = ref<PerfRun | null>(null)
const points = ref<PerfPoint[]>([])
const sla = ref<SlaGroup[] | null>(null)
const log = ref('')

function parseResult(raw: string | null | undefined): { points: PerfPoint[]; sla: SlaGroup[] | null } {
  if (!raw) return { points: [], sla: null }
  try {
    const p = JSON.parse(raw)
    if (Array.isArray(p)) return { points: p, sla: null } // legacy sweep shape
    return { points: p.points ?? [], sla: p.sla ?? null }
  } catch {
    return { points: [], sla: null }
  }
}
let poll: ReturnType<typeof setInterval> | null = null

const selectedRunning = computed(() => selected.value?.status === 'running')

async function loadRuns() {
  try {
    const r = await api.listPerf()
    runs.value = r.runs
    busy.value = r.busy
  } catch {
    /* transient */
  }
}

async function select(id: number) {
  selectedId.value = id
  try {
    selected.value = await api.getPerf(id)
    const r = parseResult(selected.value.result)
    points.value = r.points
    sla.value = r.sla
  } catch (e) {
    toast.error(t('benchmark.loadResultFailed'), { description: String(e) })
  }
  await loadLog()
}

// The result block; bringing it into view on an explicit pick (not on poll
// re-selects) means a chosen result is never buried below a long history.
const resultArea = ref<HTMLElement | null>(null)
async function onSelectRun(id: number) {
  await select(id)
  await nextTick()
  resultArea.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

async function loadLog() {
  if (selectedId.value == null) return
  try {
    log.value = (await api.getPerfLog(selectedId.value)).content
  } catch {
    log.value = ''
  }
}

// Direct-to-instance only makes sense against a ready instance.
function instanceTargetInvalid(): boolean {
  if (target.value !== 'instance') return false
  if (instanceOptions.value.find((o) => o.key === instanceKey.value)?.ready) return false
  toast.error(t('benchmark.instanceNotReady'))
  return true
}

async function launch() {
  if (launching.value) return
  if (isEmbedMode.value) return launchEmbedding()
  if (mode.value === 'speed') return launchSpeed()
  if (!model.value) return
  if (instanceTargetInvalid()) return
  const common = {
    model: model.value,
    name: name.value || undefined,
    mode: mode.value,
    target: target.value,
    instance_key: target.value === 'instance' ? instanceKey.value : undefined,
    dataset: dataset.value,
    endpoint: endpoint.value,
    max_tokens: maxTokens.value,
    min_prompt_length: promptLen.value,
    max_prompt_length: promptLen.value,
    prefix_length: prefixLen.value || undefined,
    stream: stream.value,
  }
  let req: PerfRequest
  if (mode.value === 'sla') {
    if (!slaConditions.value.length) {
      toast.error(t('benchmark.addSlaCondition'))
      return
    }
    req = {
      ...common,
      sla_variable: slaVariable.value,
      // each condition is its own OR-group; max/min ignore the value
      sla_params: slaConditions.value.map((c) => ({
        [c.metric]: c.op === 'max' || c.op === 'min' ? c.op : `${c.op}${c.value}`,
      })),
      sla_lower_bound: slaLower.value,
      sla_upper_bound: slaUpper.value,
      sla_num_runs: slaNumRuns.value,
      sla_fixed_parallel: slaVariable.value === 'rate' ? slaFixedParallel.value : undefined,
    }
  } else if (mode.value === 'openloop') {
    if (!rates.value.length) {
      toast.error(t('benchmark.enterRate'))
      return
    }
    req = { ...common, rate: rates.value, number: rates.value.map(() => reqPerPoint.value), duration: durationSec.value || undefined }
  } else if (mode.value === 'multiturn') {
    if (!mtConcurrent.value.length) {
      toast.error(t('benchmark.enterMtConcurrent'))
      return
    }
    req = {
      ...common,
      endpoint: 'chat', // multi-turn replays a conversation — chat only
      parallel: mtConcurrent.value,
      number: mtConcurrent.value.map(() => mtTotal.value),
      mt_dataset: mtDataset.value,
      mt_dataset_path: mtDataset.value === 'custom_multi_turn' ? mtDatasetPath.value : undefined,
      min_turns: minTurns.value,
      max_turns: maxTurns.value,
      duration: durationSec.value || undefined,
    }
  } else {
    if (!parallel.value.length) {
      toast.error(t('benchmark.enterParallel'))
      return
    }
    req = {
      ...common,
      parallel: parallel.value,
      number: parallel.value.map(() => reqPerPoint.value),
      warmup_num: warmup.value || undefined,
    }
  }
  await submit(req)
}

async function launchEmbedding() {
  if (!embModel.value) {
    toast.error(t('benchmark.selectModel'))
    return
  }
  if (!parallel.value.length) {
    toast.error(t('benchmark.enterParallel'))
    return
  }
  const req: PerfRequest = {
    model: embModel.value,
    name: name.value || undefined,
    mode: mode.value as 'embedding' | 'rerank',
    target: target.value,
    dataset: 'random',
    endpoint: 'chat',
    max_tokens: 16, // ignored for embedding/rerank; kept valid for the schema
    min_prompt_length: embPromptLen.value,
    max_prompt_length: embPromptLen.value,
    stream: false,
    parallel: parallel.value,
    number: parallel.value.map(() => reqPerPoint.value),
    rerank_documents: mode.value === 'rerank' ? rerankDocs.value : undefined,
  }
  await submit(req)
}

async function launchSpeed() {
  if (!model.value) {
    toast.error(t('benchmark.selectModel'))
    return
  }
  if (instanceTargetInvalid()) return
  const req: PerfRequest = {
    model: model.value,
    name: name.value || undefined,
    mode: 'speed',
    target: target.value,
    instance_key: target.value === 'instance' ? instanceKey.value : undefined,
    dataset: 'random',
    endpoint: 'completions', // speed benchmark must hit /v1/completions
    max_tokens: maxTokens.value,
    min_prompt_length: promptLen.value,
    max_prompt_length: promptLen.value,
    stream: true,
    speed_long: speedLong.value,
  }
  await submit(req)
}

async function submit(req: PerfRequest) {
  if (!(await ensureUnlocked())) return
  launching.value = true
  try {
    const run = await api.startPerf(req)
    toast.success(t('benchmark.startedToast', { id: run.id }), {
      description: t('benchmark.startedDesc'),
    })
    await loadRuns()
    await select(run.id)
  } catch (e) {
    toast.error(t('benchmark.startFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    launching.value = false
  }
}

// ---- run comparison ----
const COMPARE_COLORS = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-3)']
const compareIds = ref<number[]>([])
const compareData = ref<Record<number, { run: PerfRun; points: PerfPoint[] }>>({})

async function toggleCompare(id: number) {
  const i = compareIds.value.indexOf(id)
  if (i >= 0) {
    compareIds.value.splice(i, 1)
    return
  }
  if (compareIds.value.length >= COMPARE_COLORS.length) {
    toast.error(t('benchmark.compareMax', { n: COMPARE_COLORS.length }))
    return
  }
  compareIds.value.push(id)
  if (!compareData.value[id]) {
    try {
      const run = await api.getPerf(id)
      compareData.value[id] = { run, points: parseResult(run.result).points }
    } catch {
      compareIds.value.splice(compareIds.value.indexOf(id), 1)
      toast.error(t('benchmark.compareLoadFailed'))
    }
  }
}
const compareSeries = computed<CompareSeries[]>(() =>
  compareIds.value
    .map((id, i) => {
      const d = compareData.value[id]
      if (!d) return null
      return { label: d.run.name || `#${d.run.id}`, color: COMPARE_COLORS[i % COMPARE_COLORS.length]!, points: d.points }
    })
    .filter((s): s is CompareSeries => s != null),
)
const compareXLabel = computed(() =>
  compareIds.value.some((id) => compareData.value[id]?.run.params?.includes('"openloop"'))
    ? t('benchmark.tableRate')
    : t('benchmark.tableParallel'),
)

// Runs we've asked to stop — shows a 'stopping…' state and reveals force-kill.
const cancelling = ref<Set<number>>(new Set())
async function cancel(id: number, force = false) {
  if (force && !confirm(t('benchmark.forceConfirm', { id }))) return
  try {
    await api.cancelPerf(id, force)
    cancelling.value = new Set(cancelling.value).add(id)
    toast.info(force ? t('benchmark.forceKill') : t('benchmark.stoppingBadge'), {
      description: force ? undefined : t('benchmark.stoppingHint'),
    })
    await loadRuns()
  } catch (e) {
    toast.error(t('benchmark.cancelFailed'), { description: String(e) })
  }
}

async function remove(id: number) {
  if (!confirm(t('benchmark.deleteConfirm', { id }))) return
  try {
    await api.deletePerf(id)
    if (selectedId.value === id) { selectedId.value = null; selected.value = null; points.value = [] }
    const ci = compareIds.value.indexOf(id)
    if (ci >= 0) compareIds.value.splice(ci, 1)
    delete compareData.value[id]
    await loadRuns()
  } catch (e) {
    toast.error(t('benchmark.deleteFailed'), { description: String(e) })
  }
}

// Re-fetch the selected run's detail once it leaves the running state.
watch(runs, (list) => {
  // Drop 'stopping…' state for runs that have actually stopped.
  if (cancelling.value.size) {
    const stillRunning = new Set(
      list.filter((r) => r.status === 'running').map((r) => r.id),
    )
    const next = new Set([...cancelling.value].filter((id) => stillRunning.has(id)))
    if (next.size !== cancelling.value.size) cancelling.value = next
  }
  if (selectedId.value == null) return
  const r = list.find((x) => x.id === selectedId.value)
  if (r && selected.value && r.status !== selected.value.status) void select(r.id)
})

onMounted(() => {
  void loadRuns()
  poll = setInterval(() => {
    void loadRuns()
    if (selectedRunning.value) void loadLog()
  }, 2000)
})
onBeforeUnmount(() => { if (poll) clearInterval(poll) })

// Run summary aggregates (mirrors evalscope's Basic Information block).
const parsedParams = computed<Record<string, unknown>>(() => {
  try {
    return selected.value?.params ? JSON.parse(selected.value.params) : {}
  } catch {
    return {}
  }
})
const aggregates = computed(() => {
  let dur = 0
  let gen = 0
  for (const p of points.value) {
    dur += p.duration ?? 0
    gen += (p.success ?? 0) * (p.avg_out ?? 0)
  }
  return { duration: dur, generated: gen, rate: dur ? gen / dur : 0 }
})
const isOpenLoop = computed(() => parsedParams.value.mode === 'openloop')
const isEmbedResult = computed(() => parsedParams.value.mode === 'embedding' || parsedParams.value.mode === 'rerank')
const speedLengths = computed(() =>
  speedLong.value ? t('benchmark.speedLengthsLong') : t('benchmark.speedLengthsShort'),
)
const statusLabel = (status: string) => {
  const map: Record<string, string> = {
    running: t('common.running'),
    completed: t('common.completed'),
    failed: t('common.failed'),
    cancelled: t('common.cancelled'),
  }
  return map[status] ?? status
}
const runModeLabel = computed(() => {
  if (sla.value) {
    return `SLA (${parsedParams.value.sla_variable === 'rate' ? t('benchmark.slaVarRate') : t('benchmark.slaVarParallel')})`
  }
  return ({
    embedding: t('benchmark.modeEmbedding'),
    rerank: t('benchmark.modeRerank'),
    openloop: t('benchmark.modeOpenloop'),
    multiturn: t('benchmark.modeMultiturn'),
    speed: t('benchmark.modeSpeed'),
  } as Record<string, string>)[parsedParams.value.mode as string] ?? t('benchmark.modeSweep')
})
const mtPoint = computed(() => points.value.find((p) => p.turns != null) ?? null)
const expanded = ref<string | null>(null)
function toggle(label: string) {
  expanded.value = expanded.value === label ? null : label
}
function openReport() {
  if (selectedId.value != null) window.open(api.perfReportUrl(selectedId.value), '_blank')
}

const statusColor: Record<string, string> = {
  running: 'text-status-starting',
  completed: 'text-status-ready',
  failed: 'text-status-failed',
  cancelled: 'text-muted-foreground',
}
</script>

<template>
  <div class="space-y-4 p-6">
    <div>
      <h1 class="flex items-center gap-2 text-lg font-semibold"><Gauge class="size-5" />{{ $t('benchmark.title') }}</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        {{ $t('benchmark.description') }}
        <RouterLink to="/datasets" class="text-[var(--chart-1)] underline">{{ $t('benchmark.datasetsLink') }}</RouterLink>
        {{ $t('benchmark.descriptionEnd') }}
      </p>
    </div>
    <div class="grid gap-4 lg:grid-cols-[20rem_1fr]">
      <!-- Config -->
    <Card class="h-fit p-5">
      <p class="mb-4 flex items-center gap-2 text-sm font-semibold"><Gauge class="size-4" />{{ $t('benchmark.configTitle') }}</p>
      <div class="space-y-3 text-sm">
        <label v-if="!isEmbedMode" class="block">
          <span class="text-xs text-muted-foreground">{{ $t('benchmark.modelLabel') }}</span>
          <select v-model="model" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
            <option v-for="g in groups" :key="g" :value="g">{{ g }}{{ groupReady(g) ? '' : ` ${$t('benchmark.notStarted')}` }}</option>
            <optgroup v-if="loraOptions.length" label="LoRA">
              <option v-for="l in loraOptions" :key="l.value" :value="l.value">{{ l.group }} / {{ l.value }}</option>
            </optgroup>
          </select>
        </label>
        <label v-else class="block">
          <span class="text-xs text-muted-foreground">{{ mode === 'rerank' ? $t('benchmark.rerankModelLabel') : $t('benchmark.embModelLabel') }}</span>
          <select v-model="embModel" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
            <option v-for="m in embModels" :key="m" :value="m">{{ m }}</option>
          </select>
          <p v-if="!embeddingServerReady" class="mt-1 text-[11px] text-status-failed">{{ $t('benchmark.embNotStarted') }}</p>
          <p v-else-if="!embModels.length" class="mt-1 text-[11px] text-muted-foreground">{{ $t('benchmark.noModelsForMode') }}</p>
        </label>
        <div class="grid grid-cols-2 gap-1 rounded-lg border border-border/60 bg-muted/40 p-0.5">
          <button
            v-for="m in MODES"
            :key="m.v"
            class="rounded-md px-2 py-1 text-xs font-medium transition-colors"
            :class="mode === m.v ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'"
            @click="mode = m.v"
          >
            {{ $t(m.label) }}
          </button>
        </div>
        <label class="block">
          <span class="text-xs text-muted-foreground">{{ $t('benchmark.targetLabel') }}</span>
          <select v-model="target" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
            <option value="router">{{ $t('benchmark.targetRouter') }}</option>
            <option value="instance">{{ $t('benchmark.targetInstance') }}</option>
          </select>
        </label>
        <label v-if="target === 'instance' && !isEmbedMode" class="block">
          <span class="text-xs text-muted-foreground">{{ $t('benchmark.instanceLabel') }}</span>
          <select v-model="instanceKey" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
            <option
              v-for="o in instanceOptions"
              :key="o.key"
              :value="o.key"
              :disabled="!o.ready"
            >{{ o.key.split('::')[1] }}{{ o.ready ? '' : ` (${o.state} - ${$t('benchmark.instanceNotReady')})` }}</option>
          </select>
        </label>
        <!-- Multi-turn / embedding / speed use their own dataset + endpoint. -->
        <div v-if="mode !== 'multiturn' && mode !== 'speed' && !isEmbedMode" class="grid grid-cols-2 gap-2">
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.datasetLabel') }}</span>
            <select v-model="dataset" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
              <option value="random">random</option>
              <option value="openqa">openqa</option>
            </select>
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.endpointLabel') }}</span>
            <select v-model="endpoint" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
              <option value="chat">chat</option>
              <option value="completions">completions</option>
            </select>
          </label>
        </div>
        <!-- Sweep-specific -->
        <template v-if="mode === 'sweep'">
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.parallelLabel') }}</span>
            <Input v-model="parallelInput" placeholder="1,4,8,16" class="mt-1 font-mono" />
          </label>
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.reqPerPoint') }}</span>
              <Input v-model.number="reqPerPoint" type="number" min="1" class="mt-1" />
            </label>
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.warmupRatio') }}</span>
              <Input v-model.number="warmup" type="number" min="0" step="0.1" class="mt-1" />
            </label>
          </div>
        </template>

        <!-- Open-loop: arrival-rate sweep (Poisson; parallel ignored) -->
        <template v-else-if="mode === 'openloop'">
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.rateLabel') }}</span>
            <Input v-model="rateInput" placeholder="5,10,20" class="mt-1 font-mono" />
          </label>
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.reqPerRate') }}</span>
              <Input v-model.number="reqPerPoint" type="number" min="1" class="mt-1" />
            </label>
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.maxDuration') }}</span>
              <Input v-model.number="durationSec" type="number" min="0" class="mt-1" />
            </label>
          </div>
        </template>

        <!-- Multi-turn conversations -->
        <template v-else-if="mode === 'multiturn'">
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.mtDatasetLabel') }}</span>
            <select v-model="mtDataset" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
              <option value="share_gpt_zh_multi_turn">{{ $t('benchmark.mtShareGpt') }}</option>
              <option value="random_multi_turn">{{ $t('benchmark.mtRandom') }}</option>
              <option value="custom_multi_turn">{{ $t('benchmark.mtCustom') }}</option>
            </select>
          </label>
          <label v-if="mtDataset === 'custom_multi_turn'" class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.mtDatasetPath') }}</span>
            <Input v-model="mtDatasetPath" placeholder="/app/data/convos.jsonl" class="mt-1 font-mono text-xs" />
          </label>
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.mtConcurrent') }}</span>
              <Input v-model="mtConcurrentInput" placeholder="4 or 2,4,8" class="mt-1 font-mono" />
            </label>
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.mtConvPerPoint') }}</span>
              <Input v-model.number="mtTotal" type="number" min="1" class="mt-1" />
            </label>
          </div>
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.mtMinTurns') }}</span>
              <Input v-model.number="minTurns" type="number" min="1" class="mt-1" />
            </label>
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.mtMaxTurns') }}</span>
              <Input v-model.number="maxTurns" type="number" min="1" class="mt-1" />
            </label>
          </div>
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.mtMaxDuration') }}</span>
            <Input v-model.number="durationSec" type="number" min="0" class="mt-1" />
          </label>
        </template>

        <!-- SLA-specific (conditions OR-ed; each runs its own binary search) -->
        <template v-else-if="mode === 'sla'">
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.slaVariable') }}</span>
            <select v-model="slaVariable" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
              <option value="parallel">{{ $t('benchmark.slaParallel') }}</option>
              <option value="rate">{{ $t('benchmark.slaRate') }}</option>
            </select>
          </label>
          <div>
            <div class="mb-1 flex items-center justify-between">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.slaConditions') }}</span>
              <Button size="sm" variant="ghost" @click="addCondition"><Plus class="size-3.5" />{{ $t('common.add') }}</Button>
            </div>
            <div class="space-y-1.5">
              <div v-for="(c, i) in slaConditions" :key="i" class="flex items-center gap-1.5">
                <select v-model="c.metric" class="h-8 flex-1 rounded-md border border-input bg-background/40 px-1 text-xs">
                  <option v-for="m in SLA_METRICS" :key="m.v" :value="m.v">{{ $t(m.label) }}</option>
                </select>
                <select v-model="c.op" class="h-8 w-14 rounded-md border border-input bg-background/40 px-1 text-xs">
                  <option v-for="o in SLA_OPS" :key="o" :value="o">{{ o }}</option>
                </select>
                <Input v-if="c.op !== 'max' && c.op !== 'min'" v-model.number="c.value" type="number" step="0.01" class="h-8 w-16 text-xs" />
                <button v-if="slaConditions.length > 1" class="text-muted-foreground hover:text-foreground" @click="removeCondition(i)"><Trash2 class="size-3.5" /></button>
              </div>
            </div>
          </div>
          <label v-if="slaVariable === 'rate'" class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.slaFixedParallel') }}</span>
            <Input v-model.number="slaFixedParallel" type="number" min="1" class="mt-1" />
          </label>
          <div class="grid grid-cols-3 gap-2">
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.slaLower') }}</span>
              <Input v-model.number="slaLower" type="number" min="1" class="mt-1" />
            </label>
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.slaUpper') }}</span>
              <Input v-model.number="slaUpper" type="number" min="1" class="mt-1" />
            </label>
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.slaRunsPerPoint') }}</span>
              <Input v-model.number="slaNumRuns" type="number" min="1" class="mt-1" />
            </label>
          </div>
        </template>

        <!-- Speed baseline: single request, fixed prompt lengths, /v1/completions -->
        <template v-else-if="mode === 'speed'">
          <div class="rounded-lg border border-border/60 bg-muted/30 p-2.5 text-[11px] leading-relaxed text-muted-foreground">
            {{ $t('benchmark.speedDesc', { lengths: speedLengths }) }}
          </div>
          <label class="flex items-center justify-between">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.speedLongCtx') }}</span>
            <input v-model="speedLong" type="checkbox" class="size-4 accent-[var(--chart-1)]" />
          </label>
        </template>

        <!-- Embedding / rerank: closed-loop concurrency sweep, no output tokens -->
        <template v-else-if="isEmbedMode">
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.embParallel') }}</span>
            <Input v-model="parallelInput" placeholder="1,4,8,16" class="mt-1 font-mono" />
          </label>
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.embReqPerPoint') }}</span>
              <Input v-model.number="reqPerPoint" type="number" min="1" class="mt-1" />
            </label>
            <label class="block">
              <span class="text-xs text-muted-foreground">{{ $t('benchmark.embInputLen') }}</span>
              <Input v-model.number="embPromptLen" type="number" min="1" class="mt-1" />
            </label>
          </div>
          <label v-if="mode === 'rerank'" class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.embDocsPerReq') }}</span>
            <Input v-model.number="rerankDocs" type="number" min="1" class="mt-1" />
          </label>
        </template>

        <!-- Common knobs (LLM modes only — embedding has no output tokens/stream) -->
        <div v-if="!isEmbedMode" class="grid grid-cols-2 gap-2">
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ mode === 'speed' ? $t('benchmark.outputTokensFixed') : $t('benchmark.outputTokens') }}</span>
            <Input v-model.number="maxTokens" type="number" min="1" class="mt-1" />
          </label>
          <label v-if="dataset === 'random' && ['sweep', 'openloop', 'sla'].includes(mode)" class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.inputLength') }}</span>
            <Input v-model.number="promptLen" type="number" min="1" class="mt-1" />
          </label>
          <label v-if="dataset === 'random' && ['sweep', 'openloop', 'sla'].includes(mode)" class="block">
            <span class="text-xs text-muted-foreground">{{ $t('benchmark.prefixLength') }}</span>
            <Input v-model.number="prefixLen" type="number" min="0" class="mt-1" />
          </label>
        </div>
        <label class="block">
          <span class="text-xs text-muted-foreground">{{ $t('benchmark.nameLabel') }}</span>
          <Input v-model="name" :placeholder="$t('benchmark.namePlaceholder')" class="mt-1" />
        </label>
        <label v-if="!isEmbedMode" class="flex items-center justify-between">
          <span class="text-xs text-muted-foreground">{{ $t('benchmark.streamLabel') }}</span>
          <input v-model="stream" type="checkbox" class="size-4 accent-[var(--chart-1)]" />
        </label>

        <Button class="w-full" :disabled="launching || busy || (isEmbedMode ? !embModel : !model)" @click="launch">
          <Loader2 v-if="launching" class="size-4 animate-spin" /><Play v-else class="size-4" />
          {{ busy ? $t('benchmark.runningBusy') : $t('benchmark.startBenchmark') }}
        </Button>
      </div>
    </Card>

    <!-- Runs + results -->
    <div class="space-y-4">
      <!-- History -->
      <Card class="overflow-hidden">
        <div class="flex items-center justify-between border-b border-border/60 px-4 py-2.5 text-sm font-semibold">
          <span>{{ $t('benchmark.history') }} <span class="text-xs font-normal text-muted-foreground">{{ $t('benchmark.historyCount', { n: runs.length }) }}</span></span>
          <span class="text-xs font-normal text-muted-foreground">{{ $t('benchmark.compareHint') }}</span>
        </div>
        <!-- Bounded + scrollable so a long history never pushes the result far down. -->
        <div v-if="runs.length" class="max-h-72 divide-y divide-border/60 overflow-y-auto">
          <div
            v-for="r in runs"
            :key="r.id"
            class="flex cursor-pointer items-center gap-3 px-4 py-2.5 hover:bg-accent/30"
            :class="selectedId === r.id ? 'bg-accent/40' : ''"
            @click="onSelectRun(r.id)"
          >
            <input
              v-if="r.status === 'completed'"
              type="checkbox"
              :checked="compareIds.includes(r.id)"
              class="size-4 shrink-0 accent-[var(--chart-1)]"
              :title="$t('benchmark.addToCompare')"
              @click.stop="toggleCompare(r.id)"
            />
            <span v-else class="size-4 shrink-0" />
            <Loader2 v-if="r.status === 'running'" class="size-3.5 shrink-0 animate-spin text-status-starting" />
            <span v-else class="size-2 shrink-0 rounded-full" :class="r.status === 'completed' ? 'bg-status-ready' : r.status === 'failed' ? 'bg-status-failed' : 'bg-muted'" />
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-medium">{{ r.name || r.model }} <span class="text-xs text-muted-foreground">#{{ r.id }}</span></p>
              <p class="truncate font-mono text-[11px] text-muted-foreground">{{ r.model }} · {{ formatTime(r.created_at) }}</p>
            </div>
            <Badge variant="muted" :class="statusColor[r.status]">{{ cancelling.has(r.id) ? $t('benchmark.stoppingBadge') : statusLabel(r.status) }}</Badge>
            <template v-if="r.status === 'running'">
              <Button
                v-if="!cancelling.has(r.id)"
                size="icon-sm"
                variant="ghost"
                :title="$t('benchmark.cancelStop')"
                @click.stop="cancel(r.id)"
              ><Square class="size-3.5" /></Button>
              <Button
                v-else
                size="icon-sm"
                variant="ghost"
                :title="$t('benchmark.forceKillHint')"
                class="text-status-failed"
                @click.stop="cancel(r.id, true)"
              ><X class="size-3.5" /></Button>
            </template>
            <Button v-else size="icon-sm" variant="ghost" :title="$t('common.delete')" @click.stop="remove(r.id)"><Trash2 class="size-3.5" /></Button>
          </div>
        </div>
        <p v-else class="px-4 py-8 text-center text-sm text-muted-foreground">{{ $t('benchmark.noHistory') }}</p>
      </Card>

      <!-- Comparison + selected result; scrolled into view on an explicit pick. -->
      <div ref="resultArea" class="scroll-mt-4 space-y-4">
      <!-- Comparison (≥2 completed runs selected) -->
      <Card v-if="compareSeries.length >= 2" class="p-4">
        <div class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
          <p class="text-sm font-semibold">{{ $t('benchmark.comparison') }}</p>
          <span v-for="(s, i) in compareSeries" :key="i" class="flex items-center gap-1.5 text-xs">
            <span class="size-2.5 rounded-full" :style="{ background: s.color }" />{{ s.label }}
          </span>
        </div>
        <div class="grid gap-4 lg:grid-cols-3">
          <PerfCompareChart :series="compareSeries" :metric="(p: PerfPoint) => p.rps" :label="$t('benchmark.chartRps')" :format="(v) => v.toFixed(1)" :x-label="compareXLabel" />
          <PerfCompareChart :series="compareSeries" :metric="(p: PerfPoint) => p.avg_latency != null ? p.avg_latency * 1000 : null" :label="$t('benchmark.chartAvgLatency')" :format="(v) => `${Math.round(v)}`" :x-label="compareXLabel" />
          <PerfCompareChart :series="compareSeries" :metric="(p: PerfPoint) => p.output_tps" :label="$t('benchmark.chartGenTps')" :format="(v) => formatNumber(Math.round(v))" :x-label="compareXLabel" />
        </div>
      </Card>

      <!-- Selected detail -->
      <template v-if="selected">
        <div v-if="selected.status === 'failed'" class="rounded-lg border border-status-failed/30 bg-status-failed/10 p-3 text-xs text-status-failed">
          {{ $t('benchmark.failed') }}: {{ selected.error }}
        </div>

        <!-- Run summary (Basic Information) -->
        <Card v-if="points.length" class="p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-3">
              <div><span class="text-muted-foreground">{{ $t('benchmark.modelLabel') }}</span> <span class="font-mono">{{ selected.model }}</span></div>
              <div><span class="text-muted-foreground">{{ $t('benchmark.datasetLabel') }}</span> {{ parsedParams.dataset ?? '—' }}</div>
              <div><span class="text-muted-foreground">{{ $t('benchmark.targetLabel') }}</span> <span class="font-mono">{{ selected.target_url.replace(/^https?:\/\//, '') }}</span></div>
              <div>
                <span class="text-muted-foreground">{{ $t('benchmark.modeLabel') }}</span>
                {{ runModeLabel }}
              </div>
              <div><span class="text-muted-foreground">{{ $t('benchmark.outputTokens') }}</span> {{ parsedParams.max_tokens ?? '—' }}</div>
              <div><span class="text-muted-foreground">{{ $t('benchmark.totalTestDuration') }}</span> <span class="tabular">{{ aggregates.duration.toFixed(1) }}s</span></div>
              <div><span class="text-muted-foreground">{{ $t('benchmark.totalGenerated') }}</span> <span class="tabular">{{ formatNumber(Math.round(aggregates.generated)) }} tok</span></div>
              <div><span class="text-muted-foreground">{{ $t('benchmark.avgOutputRate') }}</span> <span class="tabular">{{ aggregates.rate.toFixed(1) }} tok/s</span></div>
            </div>
            <!-- evalscope only writes an HTML report for sweep runs. -->
            <Button v-if="!sla" variant="outline" size="sm" class="shrink-0" @click="openReport">
              <ExternalLink class="size-3.5" />{{ $t('benchmark.fullReport') }}
            </Button>
          </div>
        </Card>

        <!-- SLA auto-tune answer cards (one per OR condition) + search trace -->
        <div v-if="sla && sla.length" class="grid gap-4 md:grid-cols-2">
          <Card v-for="(g, gi) in sla" :key="gi" class="p-4">
            <div class="flex items-start justify-between gap-2">
              <div>
                <p class="font-mono text-xs text-muted-foreground">{{ g.criteria }}</p>
                <p class="mt-1 text-2xl font-semibold tabular">
                  {{ g.max_satisfied ?? '—' }}
                  <span class="text-sm font-normal text-muted-foreground">{{ $t('benchmark.slaMaxVar', { var: g.variable === 'rate' ? $t('benchmark.slaVarRate') : $t('benchmark.slaVarParallel') }) }}</span>
                </p>
              </div>
              <Badge :class="g.max_satisfied != null ? 'text-status-ready' : 'text-status-failed'" variant="muted">{{ g.note }}</Badge>
            </div>
            <!-- search trace -->
            <div class="mt-3 flex flex-wrap gap-1.5">
              <span
                v-for="(p, pi) in g.points"
                :key="pi"
                class="flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] tabular"
                :class="p.passed ? 'border-status-ready/40 text-status-ready' : 'border-status-failed/40 text-status-failed'"
              >
                <Check v-if="p.passed" class="size-3" /><X v-else class="size-3" />
                {{ g.variable === 'rate' ? 'r' : 'p' }}{{ p.val }}
                <span class="text-muted-foreground">· {{ Object.values(p.metrics).map((v) => (v == null ? '—' : v.toFixed(2))).join('/') }}</span>
              </span>
            </div>
          </Card>
        </div>

        <!-- Multi-turn aggregate metrics (only present for multi-turn runs) -->
        <Card v-if="mtPoint" class="p-4">
          <p class="mb-2 text-xs font-medium text-muted-foreground">{{ $t('benchmark.multiTurnMetrics') }}</p>
          <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
              <p class="text-lg font-semibold tabular">{{ mtPoint.turns?.toFixed(1) ?? '—' }}</p>
              <p class="text-xs text-muted-foreground">{{ $t('benchmark.turnsPerConv') }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
              <p class="text-lg font-semibold tabular">{{ mtPoint.cache_hit != null ? `${mtPoint.cache_hit.toFixed(1)}%` : '—' }}</p>
              <p class="text-xs text-muted-foreground">{{ $t('benchmark.kvCacheHit') }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
              <p class="text-lg font-semibold tabular">{{ mtPoint.first_ttft != null ? `${mtPoint.first_ttft.toFixed(0)}ms` : '—' }}</p>
              <p class="text-xs text-muted-foreground">{{ $t('benchmark.firstTtft') }}</p>
            </div>
            <div class="rounded-lg border border-border/60 bg-background/40 p-3 text-center">
              <p class="text-lg font-semibold tabular">{{ mtPoint.subsequent_ttft != null ? `${mtPoint.subsequent_ttft.toFixed(0)}ms` : '—' }}</p>
              <p class="text-xs text-muted-foreground">{{ $t('benchmark.subsequentTtft') }}</p>
            </div>
          </div>
        </Card>

        <!-- Charts: throughput + decode + tail latency (need ≥2 points for a curve) -->
        <div v-if="points.length > 1" class="grid gap-4 lg:grid-cols-3">
          <Card class="p-4"><PerfSweepChart :points="points" :metric="(p: PerfPoint) => p.rps" :label="$t('benchmark.chartRps')" color="var(--chart-1)" :format="(v) => v.toFixed(1)" :x-label="isOpenLoop ? $t('benchmark.tableRate') : $t('benchmark.tableParallel')" /></Card>
          <template v-if="isEmbedResult">
            <Card class="p-4"><PerfSweepChart :points="points" :metric="(p: PerfPoint) => p.avg_latency != null ? p.avg_latency * 1000 : null" :label="$t('benchmark.chartAvgLatency')" color="var(--chart-2)" :format="(v) => `${Math.round(v)}`" :x-label="$t('benchmark.tableParallel')" /></Card>
            <Card class="p-4"><PerfSweepChart :points="points" :metric="(p: PerfPoint) => p.latency_p99 != null ? p.latency_p99 * 1000 : null" :label="$t('benchmark.chartP99Latency')" color="var(--chart-4)" :format="(v) => `${Math.round(v)}`" :x-label="$t('benchmark.tableParallel')" /></Card>
          </template>
          <template v-else>
            <Card class="p-4"><PerfSweepChart :points="points" :metric="(p: PerfPoint) => p.output_tps" :label="$t('benchmark.chartGenTps')" color="var(--chart-2)" :format="(v) => formatNumber(Math.round(v))" :x-label="isOpenLoop ? $t('benchmark.tableRate') : $t('benchmark.tableParallel')" /></Card>
            <Card class="p-4"><PerfSweepChart :points="points" :metric="(p: PerfPoint) => p.ttft_p99 ?? p.avg_ttft" :label="$t('benchmark.chartTtft')" color="var(--chart-4)" :format="(v) => `${Math.round(v)}`" :x-label="isOpenLoop ? $t('benchmark.tableRate') : $t('benchmark.tableParallel')" /></Card>
          </template>
        </div>

        <!-- Table (click a row for avg/p50/p99/max detail) -->
        <Card v-if="points.length" class="overflow-hidden">
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead class="border-b border-border/60 text-xs text-muted-foreground">
                <tr class="[&>th]:px-3 [&>th]:py-2 [&>th]:text-right [&>th]:font-medium [&>th:first-child]:text-left">
                  <th>{{ isOpenLoop ? $t('benchmark.tableRate') : $t('benchmark.tableParallel') }}</th><th>RPS</th><th>Gen/s</th><th>Total tok/s</th><th>{{ $t('benchmark.tableAvgLatency') }}</th><th>TTFT</th><th>TTFT p99</th><th>TPOT</th><th>ITL</th><th>{{ $t('benchmark.chartP99Latency') }}</th><th>{{ $t('benchmark.tableInputOutput') }}</th><th>{{ $t('benchmark.tableSuccessTotal') }}</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="p in points" :key="p.label">
                  <tr
                    class="cursor-pointer border-b border-border/40 [&>td]:px-3 [&>td]:py-2 [&>td]:text-right [&>td:first-child]:text-left hover:bg-accent/30"
                    @click="toggle(p.label)"
                  >
                    <td class="font-mono">
                      <ChevronDown class="mr-1 inline size-3 transition-transform" :class="expanded === p.label && 'rotate-180'" />{{ p.concurrency != null && p.concurrency > 0 ? p.concurrency : p.rate }}
                    </td>
                    <td class="tabular">{{ p.rps?.toFixed(2) ?? '—' }}</td>
                    <td class="tabular">{{ formatNumber(Math.round(p.output_tps ?? 0)) }}</td>
                    <td class="tabular">{{ formatNumber(Math.round(p.total_tps ?? 0)) }}</td>
                    <td class="tabular">{{ p.avg_latency != null ? formatLatency(p.avg_latency * 1000) : '—' }}</td>
                    <td class="tabular">{{ p.avg_ttft != null ? `${p.avg_ttft.toFixed(0)}ms` : '—' }}</td>
                    <td class="tabular">{{ p.ttft_p99 != null ? `${p.ttft_p99.toFixed(0)}ms` : '—' }}</td>
                    <td class="tabular">{{ p.avg_tpot != null ? `${p.avg_tpot.toFixed(1)}ms` : '—' }}</td>
                    <td class="tabular">{{ p.avg_itl != null ? `${p.avg_itl.toFixed(1)}ms` : '—' }}</td>
                    <td class="tabular">{{ p.latency_p99 != null ? formatLatency(p.latency_p99 * 1000) : '—' }}</td>
                    <td class="tabular text-muted-foreground">{{ Math.round(p.avg_in ?? 0) }}/{{ Math.round(p.avg_out ?? 0) }}</td>
                    <td class="tabular" :class="(p.failed ?? 0) > 0 ? 'text-status-failed' : ''">{{ p.success ?? '—' }}/{{ p.total ?? '—' }}</td>
                  </tr>
                  <tr v-if="expanded === p.label" :key="p.label + '-d'" class="border-b border-border/40 bg-background/30">
                    <td colspan="12" class="px-4 py-3">
                      <div class="grid gap-3 sm:grid-cols-3">
                        <div v-for="row in [
                          { name: $t('benchmark.latencyDetail'), avg: p.avg_latency, p50: p.latency_p50, p99: p.latency_p99, max: p.latency_max, ms: false },
                          { name: 'TTFT (ms)', avg: p.avg_ttft, p50: p.ttft_p50, p99: p.ttft_p99, max: p.ttft_max, ms: true },
                          { name: 'TPOT (ms)', avg: p.avg_tpot, p50: p.tpot_p50, p99: p.tpot_p99, max: p.tpot_max, ms: true },
                        ]" :key="row.name" class="rounded-lg border border-border/60 bg-background/40 p-2.5">
                          <p class="mb-1 text-xs font-medium">{{ row.name }}</p>
                          <div class="grid grid-cols-4 gap-1 text-[11px] tabular">
                            <span class="text-muted-foreground">avg</span><span class="text-muted-foreground">p50</span><span class="text-muted-foreground">p99</span><span class="text-muted-foreground">max</span>
                            <span>{{ row.avg != null ? row.avg.toFixed(row.ms ? 0 : 3) : '—' }}</span>
                            <span>{{ row.p50 != null ? row.p50.toFixed(row.ms ? 0 : 3) : '—' }}</span>
                            <span>{{ row.p99 != null ? row.p99.toFixed(row.ms ? 0 : 3) : '—' }}</span>
                            <span>{{ row.max != null ? row.max.toFixed(row.ms ? 0 : 3) : '—' }}</span>
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </Card>

        <!-- Live log -->
        <Card v-if="selectedRunning || log" class="p-4">
          <p class="mb-2 text-xs font-medium text-muted-foreground">{{ $t('benchmark.executionLog') }}</p>
          <pre class="max-h-64 overflow-auto rounded-lg border border-border/60 bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-foreground/90">{{ log || $t('benchmark.waitingOutput') }}</pre>
        </Card>
      </template>
      </div>
    </div>
    </div>
  </div>
</template>
