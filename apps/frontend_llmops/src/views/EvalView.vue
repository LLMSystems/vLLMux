<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ClipboardCheck, ExternalLink, Loader2, Play, Square, Trash2 } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import { api, ApiError } from '@/lib/api'
import { useModelsStore } from '@/stores/models'
import { lorasOfGroup } from '@/composables/useModelOptions'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatTime } from '@/lib/utils'
import type {
  EvalDataset,
  EvalReportDataset,
  EvalRequest,
  EvalResult,
  EvalRun,
} from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import EvalSampleBrowser from '@/components/EvalSampleBrowser.vue'

const models = useModelsStore()
const { ensureUnlocked } = useAuth()
const { t } = useI18n()

// ---- model picker (shared with the benchmark page) ----
const groups = computed(() => [...new Set(models.llms.map((m) => m.key.split('::')[0] ?? m.key))])
function groupReady(g: string) {
  return models.llms.some((m) => m.key.split('::')[0] === g && m.state === 'ready')
}
// Multiple models can be queued in one go (each becomes its own run); they run
// in parallel within the shared batch_size budget. A LoRA served name counts as
// a model here too.
const selectedModels = ref<Set<string>>(new Set())
const name = ref('')
const target = ref<'router' | 'instance'>('router')
const instanceKey = ref('')
function toggleModel(v: string) {
  const s = new Set(selectedModels.value)
  if (s.has(v)) s.delete(v)
  else s.add(v)
  selectedModels.value = s
}
// LoRA adapters mounted on ready base groups — selectable as an eval target
// (router routes them over the base group's instances).
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
// Direct-to-instance only makes sense for a single model; its instances.
const singleModel = computed(() => (selectedModels.value.size === 1 ? [...selectedModels.value][0]! : ''))
const instanceOptions = computed(() =>
  models.llms
    .filter((m) => m.key.split('::')[0] === baseGroupOf(singleModel.value))
    .map((m) => ({ key: m.key, ready: m.state === 'ready', state: m.state })),
)
watch(groups, (g) => {
  if (!selectedModels.value.size && g.length) {
    const seed = g.find(groupReady) ?? g[0]!
    selectedModels.value = new Set([seed])
  }
}, { immediate: true })
// Instance target needs exactly one model; fall back to router otherwise.
watch(selectedModels, () => {
  if (selectedModels.value.size !== 1 && target.value === 'instance') target.value = 'router'
  instanceKey.value = instanceOptions.value.find((o) => o.ready)?.key ?? ''
})

// ---- dataset catalog (grouped by tier) + cached state ----
const catalog = ref<EvalDataset[]>([])
const cachedKeys = ref<Set<string>>(new Set())
// Datasets with an in-flight download — not selectable until they finish.
const downloadingKeys = ref<Set<string>>(new Set())
const selected = ref<Set<string>>(new Set())
const tiers = computed(() => {
  const out: { tier: string; items: EvalDataset[] }[] = []
  for (const d of catalog.value) {
    let g = out.find((x) => x.tier === d.tier)
    if (!g) out.push((g = { tier: d.tier, items: [] }))
    g.items.push(d)
  }
  return out
})
function toggleDataset(key: string) {
  if (downloadingKeys.value.has(key)) return // still downloading — not ready
  const s = new Set(selected.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  selected.value = s
}
function toggleTier(items: EvalDataset[]) {
  const pickable = items.filter((d) => !downloadingKeys.value.has(d.key))
  const s = new Set(selected.value)
  const allOn = pickable.every((d) => s.has(d.key))
  for (const d of pickable) {
    if (allOn) s.delete(d.key)
    else s.add(d.key)
  }
  selected.value = s
}

// ---- run config ----
const limit = ref(10) // 0 = full dataset
const repeats = ref(1)
const temperature = ref(0)
const maxTokens = ref(2048)
// evalscope's internal concurrency — how many requests it fires at the running
// vLLM instance at once. Higher fills vLLM's batch better (faster), but too high
// can queue requests past their timeout. Doesn't affect scores.
const batchSize = ref(8)
const launching = ref(false)

// Long-context datasets need a big context window — warn if any are selected.
const longContextSelected = computed(() =>
  [...selected.value].some((k) => catalog.value.find((d) => d.key === k)?.long_context),
)
// general_fc et al. need the served model to have a vLLM tool parser enabled.
const toolParserSelected = computed(() =>
  [...selected.value].some((k) => catalog.value.find((d) => d.key === k)?.needs_tool_parser),
)

// ---- per-dataset subset (subject) selection ----
// key -> chosen subset names. Empty = evalscope's default (all subsets).
const subsetSel = ref<Record<string, string[]>>({})
const selectedDatasets = computed(() => catalog.value.filter((d) => selected.value.has(d.key)))
function toggleSubset(key: string, sub: string) {
  const cur = new Set(subsetSel.value[key] ?? [])
  if (cur.has(sub)) cur.delete(sub)
  else cur.add(sub)
  subsetSel.value = { ...subsetSel.value, [key]: [...cur] }
}
function clearSubsets(key: string) {
  const next = { ...subsetSel.value }
  delete next[key]
  subsetSel.value = next
}

// ---- advanced (dataset_args) ----
const fewShot = ref(0) // 0 = dataset default
const datasetArgsJson = ref('') // raw per-dataset overrides (advanced)
const showAdvanced = ref(false)
// Build TaskConfig.dataset_args from few-shot + subset picks + raw JSON override.
function buildDatasetArgs(): Record<string, Record<string, unknown>> | undefined {
  const da: Record<string, Record<string, unknown>> = {}
  if (fewShot.value > 0) for (const k of selected.value) da[k] = { few_shot_num: fewShot.value }
  for (const [k, subs] of Object.entries(subsetSel.value)) {
    if (selected.value.has(k) && subs.length) da[k] = { ...da[k], subset_list: subs }
  }
  if (datasetArgsJson.value.trim()) {
    const custom = JSON.parse(datasetArgsJson.value) as Record<string, Record<string, unknown>>
    for (const [k, v] of Object.entries(custom)) da[k] = { ...da[k], ...v }
  }
  return Object.keys(da).length ? da : undefined
}

// ---- LLM judge (only for free-form QA datasets that can't be rule-scored) ----
const judgeTarget = ref<'internal' | 'external'>('internal')
const judgeModel = ref('') // internal group key OR external model id
const judgeApiUrl = ref('')
const judgeApiKey = ref('')
// The judge is needed exactly when a selected dataset is judge-scored; it's
// pointless for rule-scored datasets, so we only show/send it then.
const judgeEnabled = computed(() =>
  [...selected.value].some((k) => catalog.value.find((d) => d.key === k)?.needs_judge),
)
watch([judgeTarget, groups], () => {
  if (judgeTarget.value === 'internal' && !judgeModel.value) judgeModel.value = groups.value[0] ?? ''
}, { immediate: true })

async function loadCatalog() {
  try {
    catalog.value = (await api.listEvalDatasets()).datasets
  } catch (e) {
    toast.error(t('eval.loadCatalogFailed'), { description: String(e) })
  }
  await refreshDatasetState()
}

// Refresh cached + in-flight-download state (polled, so a chip unlocks the moment
// its download finishes). A dataset mid-download is auto-deselected — it's not
// ready to evaluate.
async function refreshDatasetState() {
  try {
    const cache = await api.getDatasets()
    cachedKeys.value = new Set(cache.datasets.filter((d) => d.cached).map((d) => d.key))
  } catch {
    /* non-fatal: cached badges just won't show */
  }
  try {
    const jobs = await api.listDatasetDownloads()
    const dl = new Set(
      jobs.filter((j) => j.state === 'pending' || j.state === 'downloading').map((j) => j.key),
    )
    downloadingKeys.value = dl
    if ([...selected.value].some((k) => dl.has(k))) {
      selected.value = new Set([...selected.value].filter((k) => !dl.has(k)))
    }
  } catch {
    /* non-fatal */
  }
}

// ---- runs ----
const runs = ref<EvalRun[]>([])
const busy = ref(false)
const runningCount = ref(0)
const queuedCount = ref(0)
const budget = ref(32)
const usedBudget = ref(0)
const budgetDraft = ref(32) // editable; applied on blur/Enter
const selectedId = ref<number | null>(null)
const detail = ref<EvalRun | null>(null)
const result = ref<EvalResult | null>(null)
const log = ref('')
let poll: ReturnType<typeof setInterval> | null = null

const selectedRunning = computed(() => detail.value?.status === 'running')

function parseResult(raw: string | null | undefined): EvalResult | null {
  if (!raw) return null
  try {
    return JSON.parse(raw) as EvalResult
  } catch {
    return null
  }
}
function runDatasets(r: EvalRun): string[] {
  try {
    return JSON.parse(r.datasets) as string[]
  } catch {
    return []
  }
}
function pct(score: number): string {
  return `${(score * 100).toFixed(1)}%`
}

async function loadRuns() {
  try {
    const r = await api.listEval()
    runs.value = r.runs
    busy.value = r.busy
    runningCount.value = r.running
    queuedCount.value = r.queued
    budget.value = r.budget
    usedBudget.value = r.used_budget
    if (!budgetEditing.value) budgetDraft.value = r.budget
  } catch {
    /* transient */
  }
}

const budgetEditing = ref(false)
async function applyBudget() {
  budgetEditing.value = false
  if (budgetDraft.value === budget.value || budgetDraft.value < 1) {
    budgetDraft.value = budget.value
    return
  }
  try {
    const c = await api.setEvalConfig(budgetDraft.value)
    budget.value = c.concurrency_budget
    usedBudget.value = c.used_budget
    toast.success(t('eval.budgetSet', { n: c.concurrency_budget }))
  } catch (e) {
    toast.error(t('eval.budgetSetFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
    budgetDraft.value = budget.value
  }
}

// Rich per-dataset detail (speed / subsets / description) — lazily fetched only
// when a completed run is opened, so the run list stays light.
const reportDetail = ref<EvalReportDataset[] | null>(null)
const loadingDetail = ref(false)
const showSamples = ref(false)
const reportDatasets = computed(() => reportDetail.value?.map((d) => d.dataset) ?? [])

async function loadDetail(id: number, status: string) {
  reportDetail.value = null
  showSamples.value = false
  if (status !== 'completed') return
  loadingDetail.value = true
  try {
    reportDetail.value = (await api.getEvalDetail(id)).datasets
  } catch {
    /* non-fatal: the basic score table still shows */
  } finally {
    loadingDetail.value = false
  }
}

async function select(id: number) {
  selectedId.value = id
  try {
    detail.value = await api.getEval(id)
    result.value = parseResult(detail.value.result)
    await loadDetail(id, detail.value.status)
  } catch (e) {
    toast.error(t('eval.loadResultFailed'), { description: String(e) })
  }
  await loadLog()
}

function fmtNum(v: number | null | undefined, digits = 1) {
  return v == null ? '—' : v.toFixed(digits)
}

// Bring the result block into view on an explicit pick (not poll re-selects), so
// a chosen result is never buried below a long history.
const resultArea = ref<HTMLElement | null>(null)
async function onSelectRun(id: number) {
  await select(id)
  await nextTick()
  resultArea.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

async function loadLog() {
  if (selectedId.value == null) return
  try {
    log.value = (await api.getEvalLog(selectedId.value)).content
  } catch {
    log.value = ''
  }
}

async function launch() {
  if (launching.value) return
  const targetModels = [...selectedModels.value]
  if (!targetModels.length) {
    toast.error(t('eval.selectModelRequired'))
    return
  }
  if (!selected.value.size) {
    toast.error(t('eval.selectDatasetRequired'))
    return
  }
  if (target.value === 'instance' && targetModels.length !== 1) {
    toast.error(t('eval.instanceSingleOnly'))
    return
  }
  if (target.value === 'instance' && !instanceOptions.value.find((o) => o.key === instanceKey.value)?.ready) {
    toast.error(t('eval.instanceNotReady'))
    return
  }
  if (judgeEnabled.value && !judgeModel.value) {
    toast.error(t('eval.judgeModelRequired'))
    return
  }
  if (judgeEnabled.value && judgeTarget.value === 'external' && !judgeApiUrl.value) {
    toast.error(t('eval.judgeApiRequired'))
    return
  }
  let datasetArgs: EvalRequest['dataset_args']
  try {
    datasetArgs = buildDatasetArgs()
  } catch {
    toast.error(t('eval.invalidDatasetArgs'))
    return
  }
  if (!(await ensureUnlocked())) return
  launching.value = true
  // One run per selected model; they share the datasets/params and queue against
  // the concurrency budget on the backend.
  let firstId: number | null = null
  let ok = 0
  try {
    for (const mdl of targetModels) {
      const req: EvalRequest = {
        model: mdl,
        name: name.value || undefined,
        target: target.value,
        instance_key: target.value === 'instance' ? instanceKey.value : undefined,
        datasets: [...selected.value],
        limit: limit.value > 0 ? limit.value : null,
        repeats: repeats.value,
        temperature: temperature.value,
        max_tokens: maxTokens.value,
        eval_batch_size: batchSize.value,
        judge_enabled: judgeEnabled.value,
        judge_target: judgeEnabled.value ? judgeTarget.value : undefined,
        judge_model: judgeEnabled.value ? judgeModel.value : undefined,
        judge_api_url: judgeEnabled.value && judgeTarget.value === 'external' ? judgeApiUrl.value : undefined,
        judge_api_key: judgeEnabled.value && judgeTarget.value === 'external' ? judgeApiKey.value || undefined : undefined,
        dataset_args: datasetArgs,
      }
      try {
        const run = await api.startEval(req)
        if (firstId === null) firstId = run.id
        ok++
      } catch (e) {
        toast.error(t('eval.evalFailed', { model: mdl }), {
          description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
        })
      }
    }
    if (ok > 0) {
      toast.success(t('eval.startedToast', { n: ok }), {
        description: t('eval.startedDesc'),
      })
      await loadRuns()
      if (firstId !== null) await select(firstId)
    }
  } finally {
    launching.value = false
  }
}

async function cancel(id: number) {
  try {
    await api.cancelEval(id)
    toast.info(t('eval.cancelRequested'))
    await loadRuns()
  } catch (e) {
    toast.error(t('eval.cancelFailed'), { description: String(e) })
  }
}

async function remove(id: number) {
  if (!confirm(t('eval.deleteConfirm', { id }))) return
  try {
    await api.deleteEval(id)
    if (selectedId.value === id) { selectedId.value = null; detail.value = null; result.value = null }
    await loadRuns()
  } catch (e) {
    toast.error(t('eval.deleteFailed'), { description: String(e) })
  }
}

// ---- run comparison (dataset × run score matrix) ----
const compareIds = ref<number[]>([])
function toggleCompare(id: number) {
  const i = compareIds.value.indexOf(id)
  if (i >= 0) compareIds.value.splice(i, 1)
  else compareIds.value.push(id)
}
const compareRuns = computed(() =>
  compareIds.value
    .map((id) => runs.value.find((r) => r.id === id))
    .filter((r): r is EvalRun => r != null && r.status === 'completed'),
)
// Union of datasets across the compared runs (row order = first appearance).
const compareDatasets = computed(() => {
  const seen = new Map<string, string>() // dataset -> pretty
  for (const r of compareRuns.value)
    for (const d of parseResult(r.result)?.datasets ?? [])
      if (!seen.has(d.dataset)) seen.set(d.dataset, d.pretty)
  return [...seen.entries()].map(([dataset, pretty]) => ({ dataset, pretty }))
})
// scoreFor[runId][dataset] = score (0..1) or undefined
const compareScores = computed(() => {
  const m: Record<number, Record<string, number>> = {}
  for (const r of compareRuns.value) {
    m[r.id] = {}
    for (const d of parseResult(r.result)?.datasets ?? []) m[r.id]![d.dataset] = d.score
  }
  return m
})
function bestForDataset(dataset: string): number | null {
  let best: number | null = null
  for (const r of compareRuns.value) {
    const s = compareScores.value[r.id]?.[dataset]
    if (s != null && (best == null || s > best)) best = s
  }
  return best
}

// Re-fetch the selected run's detail once it leaves the running state.
watch(runs, (list) => {
  if (selectedId.value == null) return
  const r = list.find((x) => x.id === selectedId.value)
  if (r && detail.value && r.status !== detail.value.status) void select(r.id)
})

onMounted(() => {
  void loadCatalog()
  void loadRuns()
  poll = setInterval(() => {
    void loadRuns()
    if (downloadingKeys.value.size) void refreshDatasetState()
    if (selectedRunning.value) void loadLog()
  }, 2500)
})
onBeforeUnmount(() => { if (poll) clearInterval(poll) })

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'outline'> = {
  completed: 'default', running: 'secondary', queued: 'outline', failed: 'outline', cancelled: 'outline',
}
const statusLabel = (status: string) => {
  const map: Record<string, string> = {
    completed: t('common.completed'),
    running: t('common.running'),
    queued: t('common.queued'),
    failed: t('common.failed'),
    cancelled: t('common.cancelled'),
  }
  return map[status] ?? status
}
</script>

<template>
  <div class="space-y-6 p-6">
    <div>
      <h1 class="flex items-center gap-2 text-lg font-semibold"><ClipboardCheck class="size-5" />{{ $t('eval.title') }}</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        {{ $t('eval.description') }}
        <RouterLink to="/datasets" class="text-[var(--chart-1)] underline">{{ $t('sidebar.datasets') }}</RouterLink>
        {{ $t('eval.descriptionEnd') }}
      </p>
    </div>

    <div class="grid gap-6 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
      <!-- Config -->
      <Card class="h-fit space-y-4 p-5 text-sm">
        <p class="flex items-center gap-2 text-sm font-semibold"><ClipboardCheck class="size-4" />{{ $t('eval.configTitle') }}</p>
        <div class="space-y-1.5">
          <div class="flex items-center justify-between">
            <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.modelLabel') }}</label>
            <span class="text-[10px] text-muted-foreground">{{ $t('eval.selected') }} {{ selectedModels.size }}</span>
          </div>
          <p v-if="!groups.length" class="text-xs text-muted-foreground">{{ $t('eval.noModels') }}</p>
          <div class="flex flex-wrap gap-1.5">
            <button
              v-for="g in groups"
              :key="g"
              class="rounded-md border px-2 py-1 text-xs transition-colors"
              :class="selectedModels.has(g)
                ? 'border-[var(--chart-1)] bg-[var(--chart-1)]/10 text-foreground'
                : 'border-border text-muted-foreground hover:bg-muted'"
              @click="toggleModel(g)"
            >
              {{ g }}<span v-if="!groupReady(g)" class="ml-1 text-muted-foreground/70">{{ $t('eval.offline') }}</span>
            </button>
          </div>
          <div v-if="loraOptions.length" class="flex flex-wrap gap-1.5">
            <button
              v-for="l in loraOptions"
              :key="l.value"
              class="rounded-md border px-2 py-1 text-xs transition-colors"
              :class="selectedModels.has(l.value)
                ? 'border-[var(--chart-3)] bg-[var(--chart-3)]/10 text-foreground'
                : 'border-border text-muted-foreground hover:bg-muted'"
              :title="`LoRA · ${l.group}`"
              @click="toggleModel(l.value)"
            >
              <span class="text-[var(--chart-3)]">◆</span> {{ l.value }}
            </button>
          </div>
          <p v-if="selectedModels.size > 1" class="text-[10px] text-muted-foreground">
            {{ $t('eval.multiModelHint') }}
          </p>
        </div>

        <div class="space-y-1.5">
          <label class="text-xs font-medium text-muted-foreground">{{ $t('common.target') }}</label>
          <div class="flex gap-1.5">
            <Button size="sm" :variant="target === 'router' ? 'default' : 'outline'" @click="target = 'router'">{{ $t('eval.targetRouter') }}</Button>
            <Button
              size="sm"
              :variant="target === 'instance' ? 'default' : 'outline'"
              :disabled="selectedModels.size !== 1"
              :title="selectedModels.size !== 1 ? $t('eval.instanceOnly') : ''"
              @click="target = 'instance'"
            >{{ $t('eval.targetInstance') }}</Button>
          </div>
          <select
            v-if="target === 'instance'"
            v-model="instanceKey"
            class="mt-1.5 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm"
          >
            <option
              v-for="o in instanceOptions"
              :key="o.key"
              :value="o.key"
              :disabled="!o.ready"
            >{{ o.key }}{{ o.ready ? '' : ` (${o.state} - ${$t('eval.instanceNotReady')})` }}</option>
          </select>
        </div>

        <!-- Dataset multi-select -->
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.datasetLabel') }}</label>
            <span class="text-[10px] text-muted-foreground">{{ $t('eval.selected') }} {{ selected.size }}</span>
          </div>
          <div v-for="t in tiers" :key="t.tier" class="space-y-1">
            <button
              class="text-[11px] font-medium text-muted-foreground hover:text-foreground"
              @click="toggleTier(t.items)"
            >
              {{ t.tier }} ＋
            </button>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="d in t.items"
                :key="d.key"
                class="rounded-md border px-2 py-1 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                :class="selected.has(d.key)
                  ? 'border-[var(--chart-1)] bg-[var(--chart-1)]/10 text-foreground'
                  : 'border-border text-muted-foreground hover:bg-muted'"
                :disabled="downloadingKeys.has(d.key)"
                :title="downloadingKeys.has(d.key)
                  ? `${d.dataset_id} (${$t('eval.downloading')})`
                  : (d.note ? `${d.dataset_id} · ${d.note}` : d.dataset_id)"
                @click="toggleDataset(d.key)"
              >
                {{ d.label }}
                <span v-if="d.needs_judge" class="ml-1 text-amber-500" :title="$t('eval.judgeTitle')">⚖</span>
                <span v-if="downloadingKeys.has(d.key)" class="ml-1 text-muted-foreground">{{ $t('eval.downloading') }}</span>
                <span v-else-if="cachedKeys.has(d.key)" class="ml-1 text-[var(--chart-1)]">●</span>
              </button>
            </div>
          </div>
          <p class="text-[10px] text-muted-foreground">{{ $t('eval.cachedHint') }}</p>
          <p
            v-if="longContextSelected"
            class="rounded-md bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-600"
          >
            {{ $t('eval.longContextWarn') }}
          </p>
          <p
            v-if="toolParserSelected"
            class="rounded-md bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-600"
          >
            {{ $t('eval.toolParserWarn') }}
          </p>
        </div>

        <!-- Selected dataset details: subjects (subset) picker -->
        <div v-if="selectedDatasets.length" class="space-y-2">
          <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.subsetLabel') }}</label>
          <div
            v-for="d in selectedDatasets"
            :key="d.key"
            class="space-y-2 rounded-md border border-border/60 p-2.5"
          >
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-xs font-medium">{{ d.label }}</span>
              <Badge v-for="t in d.meta?.tags ?? []" :key="t" variant="muted" class="text-[10px]">{{ t }}</Badge>
              <span v-if="d.meta?.metric?.length" class="text-[10px] text-muted-foreground">· {{ d.meta.metric.join(', ') }}</span>
              <span v-if="d.meta && d.meta.few_shot_num > 0" class="text-[10px] text-muted-foreground">· {{ $t('eval.defaultFewShot', { n: d.meta.few_shot_num }) }}</span>
            </div>
            <p v-if="d.meta?.description" class="line-clamp-2 text-[10px] text-muted-foreground/80">
              {{ d.meta.description }}
            </p>
            <!-- subjects / subsets (only when there's more than one to choose) -->
            <div v-if="(d.meta?.subsets?.length ?? 0) > 1" class="space-y-1">
              <div class="flex items-center gap-2">
                <span class="text-[10px] text-muted-foreground">{{ $t('eval.subsetHint', { n: d.meta!.subsets.length }) }}</span>
                <button
                  v-if="(subsetSel[d.key]?.length ?? 0) > 0"
                  class="text-[10px] text-[var(--chart-1)] hover:underline"
                  @click="clearSubsets(d.key)"
                >
                  {{ $t('eval.clearSubsets', { n: subsetSel[d.key]!.length }) }}
                </button>
              </div>
              <div class="flex max-h-24 flex-wrap gap-1 overflow-y-auto">
                <button
                  v-for="sub in d.meta!.subsets"
                  :key="sub"
                  class="rounded border px-1.5 py-0.5 font-mono text-[10px] transition-colors"
                  :class="(subsetSel[d.key] ?? []).includes(sub)
                    ? 'border-[var(--chart-1)] bg-[var(--chart-1)]/10 text-foreground'
                    : 'border-border text-muted-foreground hover:bg-muted'"
                  @click="toggleSubset(d.key, sub)"
                >
                  {{ sub }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- Params -->
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1">
            <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.samplesPerDataset') }}</label>
            <Input v-model.number="limit" type="number" min="0" />
          </div>
          <div class="space-y-1">
            <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.repeats') }}</label>
            <Input v-model.number="repeats" type="number" min="1" />
          </div>
          <div class="space-y-1">
            <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.temperature') }}</label>
            <Input v-model.number="temperature" type="number" min="0" max="2" step="0.1" />
          </div>
          <div class="space-y-1">
            <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.maxOutputTokens') }}</label>
            <Input v-model.number="maxTokens" type="number" min="1" />
          </div>
          <div class="space-y-1">
            <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.batchSize') }}</label>
            <Input v-model.number="batchSize" type="number" min="1" />
            <p class="text-[10px] text-muted-foreground">{{ $t('eval.batchSizeHint') }}</p>
          </div>
        </div>

        <!-- Advanced: dataset_args -->
        <div class="space-y-2 rounded-md border border-border/60 p-3">
          <button class="text-xs font-medium text-muted-foreground hover:text-foreground" @click="showAdvanced = !showAdvanced">
            {{ $t('eval.advanced') }} {{ showAdvanced ? '▾' : '▸' }}
          </button>
          <template v-if="showAdvanced">
            <div class="space-y-1">
              <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.fewShot') }}</label>
              <Input v-model.number="fewShot" type="number" min="0" />
            </div>
            <div class="space-y-1">
              <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.datasetArgsJson') }}</label>
              <textarea
                v-model="datasetArgsJson"
                rows="3"
                spellcheck="false"
                :placeholder="$t('eval.datasetArgsPlaceholder')"
                class="w-full rounded-md border border-border bg-background px-2 py-1.5 font-mono text-[11px]"
              />
              <p class="text-[10px] text-muted-foreground">{{ $t('eval.datasetArgsHint') }}</p>
            </div>
          </template>
        </div>

        <!-- LLM judge — only shown when a judge-scored (⚖) dataset is selected -->
        <div v-if="judgeEnabled" class="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
          <p class="flex items-center gap-2 text-xs font-medium">
            <span class="text-amber-500">{{ $t('eval.judgeTitle') }}</span>
            <span class="text-[10px] font-normal text-muted-foreground">{{ $t('eval.judgeHint') }}</span>
          </p>
          <div class="flex gap-1.5">
            <Button size="sm" :variant="judgeTarget === 'internal' ? 'default' : 'outline'" @click="judgeTarget = 'internal'">{{ $t('eval.judgeInternal') }}</Button>
            <Button size="sm" :variant="judgeTarget === 'external' ? 'default' : 'outline'" @click="judgeTarget = 'external'">{{ $t('eval.judgeExternal') }}</Button>
          </div>
          <select
            v-if="judgeTarget === 'internal'"
            v-model="judgeModel"
            class="h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm"
          >
            <option v-for="g in groups" :key="g" :value="g">{{ g }}{{ groupReady(g) ? '' : ` ${$t('eval.offline')}` }}</option>
          </select>
          <template v-else>
            <Input v-model="judgeApiUrl" :placeholder="$t('eval.judgeApiUrl')" />
            <Input v-model="judgeModel" :placeholder="$t('eval.judgeModelId')" />
            <Input v-model="judgeApiKey" type="password" :placeholder="$t('eval.judgeApiKey')" />
          </template>
          <p class="text-[10px] text-muted-foreground">{{ $t('eval.judgeQualityHint') }}</p>
        </div>

        <div class="space-y-1">
          <label class="text-xs font-medium text-muted-foreground">{{ $t('eval.nameLabel') }}</label>
          <Input v-model="name" :placeholder="$t('eval.namePlaceholder')" />
        </div>

        <Button class="w-full" :disabled="launching" @click="launch">
          <Loader2 v-if="launching" class="size-4 animate-spin" />
          <Play v-else class="size-4" />{{ selectedModels.size > 1 ? $t('eval.startEvalMulti', { n: selectedModels.size }) : $t('eval.startEval') }}
        </Button>
      </Card>

      <!-- Runs + detail -->
      <div class="min-w-0 space-y-4">
        <!-- Run list -->
        <Card class="overflow-hidden">
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 px-5 py-3">
            <div class="flex items-center gap-2">
              <span class="text-sm font-semibold">{{ $t('eval.runHistory') }}</span>
              <span v-if="runningCount" class="rounded bg-[var(--chart-1)]/10 px-1.5 py-0.5 text-[10px] text-[var(--chart-1)]">{{ $t('eval.runningCount', { n: runningCount }) }}</span>
              <span v-if="queuedCount" class="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{{ $t('eval.queuedCount', { n: queuedCount }) }}</span>
            </div>
            <div class="flex items-center gap-2">
              <label class="flex items-center gap-1 text-[11px] text-muted-foreground" :title="$t('eval.concurrencyBudgetHint')">
                {{ $t('eval.concurrencyBudget') }}
                <Input
                  v-model.number="budgetDraft"
                  type="number"
                  min="1"
                  class="h-7 w-16 text-xs"
                  @focus="budgetEditing = true"
                  @blur="applyBudget"
                  @keyup.enter="applyBudget"
                />
              </label>
              <span class="text-[10px] text-muted-foreground">{{ $t('eval.budgetUsage', { used: usedBudget, total: budget }) }}</span>
            </div>
          </div>
          <!-- Bounded + scrollable so a long history never pushes the result far down. -->
          <div v-if="runs.length" class="max-h-72 divide-y divide-border/60 overflow-y-auto">
            <button
              v-for="r in runs"
              :key="r.id"
              class="flex w-full items-center gap-3 px-5 py-2.5 text-left text-sm hover:bg-muted/50"
              :class="selectedId === r.id && 'bg-muted/60'"
              @click="onSelectRun(r.id)"
            >
              <input
                type="checkbox"
                class="accent-[var(--chart-1)]"
                :checked="compareIds.includes(r.id)"
                :disabled="r.status !== 'completed'"
                :title="$t('eval.addCompare')"
                @click.stop="toggleCompare(r.id)"
              />
              <span class="tabular text-xs text-muted-foreground">#{{ r.id }}</span>
              <Badge :variant="STATUS_VARIANT[r.status] ?? 'outline'">{{ statusLabel(r.status) }}</Badge>
              <span class="min-w-0 flex-1 truncate">
                <span class="font-medium">{{ r.name || r.model }}</span>
                <span class="ml-2 text-xs text-muted-foreground">{{ runDatasets(r).join(', ') }}</span>
              </span>
              <span class="shrink-0 text-xs text-muted-foreground">{{ formatTime(r.created_at) }}</span>
              <Square
                v-if="r.status === 'running' || r.status === 'queued'"
                class="size-3.5 text-muted-foreground hover:text-status-failed"
                :title="r.status === 'queued' ? $t('eval.cancelQueue') : $t('common.cancel')"
                @click.stop="cancel(r.id)"
              />
              <Trash2
                v-else
                class="size-3.5 text-muted-foreground hover:text-status-failed"
                @click.stop="remove(r.id)"
              />
            </button>
          </div>
          <p v-else class="px-5 py-8 text-center text-sm text-muted-foreground">{{ $t('eval.noHistory') }}</p>
        </Card>

        <!-- Comparison + selected result; scrolled into view on an explicit pick. -->
        <div ref="resultArea" class="scroll-mt-4 space-y-4">
        <!-- Comparison matrix (datasets × runs) -->
        <Card v-if="compareRuns.length >= 2" class="overflow-hidden">
          <div class="border-b border-border/60 px-5 py-3 text-sm font-semibold">{{ $t('eval.scoreComparison') }}</div>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border/60 text-left text-xs text-muted-foreground">
                  <th class="px-5 py-2">{{ $t('eval.compareDataset') }}</th>
                  <th v-for="r in compareRuns" :key="r.id" class="px-3 py-2 text-right">
                    {{ r.name || r.model }}<span class="text-muted-foreground/60"> #{{ r.id }}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="d in compareDatasets" :key="d.dataset" class="border-b border-border/40">
                  <td class="px-5 py-1.5 font-medium">{{ d.pretty }}</td>
                  <td
                    v-for="r in compareRuns"
                    :key="r.id"
                    class="px-3 py-1.5 text-right tabular"
                    :class="compareScores[r.id]?.[d.dataset] != null
                      && compareScores[r.id]?.[d.dataset] === bestForDataset(d.dataset)
                      ? 'font-semibold text-[var(--chart-1)]'
                      : ''"
                  >
                    {{ compareScores[r.id]?.[d.dataset] != null ? pct(compareScores[r.id]![d.dataset]!) : '—' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="px-5 py-2 text-[10px] text-muted-foreground">{{ $t('eval.compareBestHint') }}</p>
        </Card>

        <!-- Selected detail -->
        <Card v-if="detail" class="p-5">
          <div class="mb-3 flex items-center justify-between">
            <div>
              <p class="text-sm font-semibold">{{ detail.name || detail.model }} · #{{ detail.id }}</p>
              <p class="text-xs text-muted-foreground">{{ detail.model }} · {{ runDatasets(detail).join(', ') }}</p>
            </div>
            <a
              v-if="detail.status === 'completed'"
              :href="api.evalReportUrl(detail.id)"
              target="_blank"
              class="flex items-center gap-1 text-xs text-[var(--chart-1)] hover:underline"
            >
              {{ $t('eval.detailReport') }} <ExternalLink class="size-3" />
            </a>
          </div>

          <div v-if="detail.status === 'queued'" class="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 class="size-4 animate-spin" />{{ $t('eval.queuedHint') }}
          </div>
          <div v-else-if="detail.status === 'running'" class="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 class="size-4 animate-spin" />{{ $t('eval.runningHint') }}
          </div>
          <div
            v-else-if="detail.status === 'failed'"
            class="break-words rounded-md bg-status-failed/10 p-3 text-sm text-status-failed"
          >
            {{ $t('eval.failedPrefix') }}{{ detail.error }}
          </div>

          <!-- Scores -->
          <table v-if="result?.datasets.length" class="w-full text-sm">
            <thead>
              <tr class="border-b border-border/60 text-left text-xs text-muted-foreground">
                <th class="py-1.5">{{ $t('common.dataset') }}</th>
                <th class="py-1.5">{{ $t('eval.metric') }}</th>
                <th class="py-1.5 text-right">{{ $t('eval.samples') }}</th>
                <th class="py-1.5 text-right">{{ $t('eval.score') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in result.datasets" :key="d.dataset" class="border-b border-border/40">
                <td class="py-1.5 font-medium">{{ d.pretty }}</td>
                <td class="py-1.5 text-xs text-muted-foreground">
                  {{ d.metrics.map((m) => m.name).join(', ') }}
                </td>
                <td class="py-1.5 text-right tabular text-muted-foreground">{{ d.num }}</td>
                <td class="py-1.5 text-right tabular font-semibold">{{ pct(d.score) }}</td>
              </tr>
            </tbody>
          </table>

          <!-- Rich detail (lazy): speed / per-subset / description, per dataset -->
          <div v-if="loadingDetail" class="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 class="size-3.5 animate-spin" />{{ $t('eval.loadingDetail') }}
          </div>
          <div v-else-if="reportDetail?.length" class="mt-4 space-y-4">
            <div v-for="d in reportDetail" :key="d.dataset" class="rounded-lg border border-border/60 p-3">
              <p class="mb-2 text-sm font-semibold">{{ d.pretty }}</p>

              <!-- Speed / throughput (eval also measures this) -->
              <div v-if="d.perf" class="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <div class="rounded-md bg-muted/40 p-2">
                  <p class="text-[10px] text-muted-foreground">{{ $t('eval.latencyP50P99') }}</p>
                  <p class="tabular text-sm">{{ fmtNum(d.perf.latency.p50) }}s <span class="text-muted-foreground">/ {{ fmtNum(d.perf.latency.p99) }}s</span></p>
                </div>
                <div class="rounded-md bg-muted/40 p-2">
                  <p class="text-[10px] text-muted-foreground">{{ $t('eval.outputThroughput') }}</p>
                  <p class="tabular text-sm">{{ fmtNum(d.perf.output_tps) }} <span class="text-muted-foreground">tok/s</span></p>
                </div>
                <div class="rounded-md bg-muted/40 p-2">
                  <p class="text-[10px] text-muted-foreground">{{ $t('eval.avgInputTok') }}</p>
                  <p class="tabular text-sm">{{ fmtNum(d.perf.input_tokens_mean, 0) }}</p>
                </div>
                <div class="rounded-md bg-muted/40 p-2">
                  <p class="text-[10px] text-muted-foreground">{{ $t('eval.avgOutputTok') }}</p>
                  <p class="tabular text-sm">{{ fmtNum(d.perf.output_tokens_mean, 0) }}</p>
                </div>
              </div>

              <!-- Per-subset breakdown (only when a metric spans >1 subject) -->
              <template v-for="m in d.metrics" :key="m.name">
                <div v-if="m.subsets.length > 1" class="mt-3">
                  <p class="mb-1 text-[11px] font-medium text-muted-foreground">{{ m.name }} {{ $t('eval.subsetBreakdown') }}</p>
                  <div class="flex flex-wrap gap-1.5">
                    <span
                      v-for="s in m.subsets"
                      :key="s.name"
                      class="rounded-md border border-border/60 px-1.5 py-0.5 text-[11px] tabular"
                    >{{ s.name }} <span class="font-semibold">{{ pct(s.score) }}</span> <span class="text-muted-foreground">({{ s.num }})</span></span>
                  </div>
                </div>
              </template>

              <!-- Benchmark description -->
              <details v-if="d.description" class="mt-3">
                <summary class="cursor-pointer text-[11px] text-muted-foreground">{{ $t('eval.whatDoesItTest') }}</summary>
                <pre class="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted/40 p-2 text-[11px] leading-relaxed text-muted-foreground">{{ d.description }}</pre>
              </details>
            </div>

            <!-- Per-sample browser (lazy: nothing fetched until opened) -->
            <div>
              <Button variant="outline" size="sm" @click="showSamples = !showSamples">
                {{ showSamples ? $t('eval.collapseSamples') : $t('eval.expandSamples') }}
              </Button>
              <div v-if="showSamples && reportDatasets.length" class="mt-3">
                <EvalSampleBrowser :run-id="detail.id" :datasets="reportDatasets" />
              </div>
            </div>
          </div>

          <!-- Log -->
          <details class="mt-4" :open="detail.status !== 'completed'">
            <summary class="cursor-pointer text-xs text-muted-foreground">{{ $t('eval.executionLog') }}</summary>
            <pre class="mt-2 max-h-64 overflow-auto rounded-md bg-muted/60 p-3 text-[11px] leading-relaxed">{{ log || $t('eval.noLog') }}</pre>
          </details>
        </Card>
        </div>
      </div>
    </div>

  </div>
</template>
