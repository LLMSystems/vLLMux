<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ClipboardCheck, ExternalLink, Loader2, Play, Square, Trash2 } from '@lucide/vue'
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
  models.llms.filter((m) => m.key.split('::')[0] === baseGroupOf(singleModel.value)).map((m) => m.key),
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
  instanceKey.value = instanceOptions.value[0] ?? ''
})

// ---- dataset catalog (grouped by tier) + cached state ----
const catalog = ref<EvalDataset[]>([])
const cachedKeys = ref<Set<string>>(new Set())
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
  const s = new Set(selected.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  selected.value = s
}
function toggleTier(items: EvalDataset[]) {
  const s = new Set(selected.value)
  const allOn = items.every((d) => s.has(d.key))
  for (const d of items) {
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

// ---- advanced (dataset_args) ----
const fewShot = ref(0) // 0 = dataset default
const datasetArgsJson = ref('') // raw per-dataset overrides (advanced)
const showAdvanced = ref(false)
// Build TaskConfig.dataset_args from the few-shot convenience + raw JSON override.
function buildDatasetArgs(): Record<string, Record<string, unknown>> | undefined {
  const da: Record<string, Record<string, unknown>> = {}
  if (fewShot.value > 0) for (const k of selected.value) da[k] = { few_shot_num: fewShot.value }
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
    toast.error('無法讀取評測資料集', { description: String(e) })
  }
  try {
    const cache = await api.getDatasets()
    cachedKeys.value = new Set(cache.datasets.filter((d) => d.cached).map((d) => d.key))
  } catch {
    /* non-fatal: cached badges just won't show */
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
    toast.success(`並發預算已設為 ${c.concurrency_budget}`)
  } catch (e) {
    toast.error('無法調整預算', { description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e) })
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
    toast.error('無法載入結果', { description: String(e) })
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
    toast.error('請至少選擇一個模型')
    return
  }
  if (!selected.value.size) {
    toast.error('請至少選擇一個資料集')
    return
  }
  if (target.value === 'instance' && targetModels.length !== 1) {
    toast.error('直連實例僅支援單一模型')
    return
  }
  if (judgeEnabled.value && !judgeModel.value) {
    toast.error('請設定裁判模型')
    return
  }
  if (judgeEnabled.value && judgeTarget.value === 'external' && !judgeApiUrl.value) {
    toast.error('外部裁判需要 API URL')
    return
  }
  let datasetArgs: EvalRequest['dataset_args']
  try {
    datasetArgs = buildDatasetArgs()
  } catch {
    toast.error('進階 dataset_args 不是合法 JSON')
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
        toast.error(`無法評測 ${mdl}`, { description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e) })
      }
    }
    if (ok > 0) {
      toast.success(`已排入 ${ok} 個評測`, { description: '可離開此頁，背景持續執行。' })
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
    toast.info('已要求取消')
    await loadRuns()
  } catch (e) {
    toast.error('取消失敗', { description: String(e) })
  }
}

async function remove(id: number) {
  if (!confirm(`刪除評測 #${id}？`)) return
  try {
    await api.deleteEval(id)
    if (selectedId.value === id) { selectedId.value = null; detail.value = null; result.value = null }
    await loadRuns()
  } catch (e) {
    toast.error('刪除失敗', { description: String(e) })
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
    if (selectedRunning.value) void loadLog()
  }, 2500)
})
onBeforeUnmount(() => { if (poll) clearInterval(poll) })

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'outline'> = {
  completed: 'default', running: 'secondary', queued: 'outline', failed: 'outline', cancelled: 'outline',
}
</script>

<template>
  <div class="space-y-6 p-6">
    <div>
      <h1 class="flex items-center gap-2 text-lg font-semibold"><ClipboardCheck class="size-5" />模型評測</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        評測模型的「答對率 / 品質」（與壓測的速度不同）。先在
        <RouterLink to="/datasets" class="text-[var(--chart-1)] underline">資料集庫</RouterLink>
        下載資料集，跑評測就不必等。小模型分數偏低屬正常，重點是換模型 / 調參時的比較。
      </p>
    </div>

    <div class="grid gap-6 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
      <!-- Config -->
      <Card class="space-y-4 p-5">
        <div class="space-y-1.5">
          <div class="flex items-center justify-between">
            <label class="text-xs font-medium text-muted-foreground">模型</label>
            <span class="text-[10px] text-muted-foreground">已選 {{ selectedModels.size }}</span>
          </div>
          <p v-if="!groups.length" class="text-xs text-muted-foreground">尚未設定模型</p>
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
              {{ g }}<span v-if="!groupReady(g)" class="ml-1 text-muted-foreground/70">· 離線</span>
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
            多選 = 每個模型各排一個評測，依共用並發預算並行 / 排隊。
          </p>
        </div>

        <div class="space-y-1.5">
          <label class="text-xs font-medium text-muted-foreground">目標</label>
          <div class="flex gap-1.5">
            <Button size="sm" :variant="target === 'router' ? 'default' : 'outline'" @click="target = 'router'">路由器</Button>
            <Button
              size="sm"
              :variant="target === 'instance' ? 'default' : 'outline'"
              :disabled="selectedModels.size !== 1"
              :title="selectedModels.size !== 1 ? '直連實例僅支援單一模型' : ''"
              @click="target = 'instance'"
            >直連實例</Button>
          </div>
          <select
            v-if="target === 'instance'"
            v-model="instanceKey"
            class="mt-1.5 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          >
            <option v-for="k in instanceOptions" :key="k" :value="k">{{ k }}</option>
          </select>
        </div>

        <!-- Dataset multi-select -->
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <label class="text-xs font-medium text-muted-foreground">資料集</label>
            <span class="text-[10px] text-muted-foreground">已選 {{ selected.size }}</span>
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
                class="rounded-md border px-2 py-1 text-xs transition-colors"
                :class="selected.has(d.key)
                  ? 'border-[var(--chart-1)] bg-[var(--chart-1)]/10 text-foreground'
                  : 'border-border text-muted-foreground hover:bg-muted'"
                :title="d.note ? `${d.dataset_id} · ${d.note}` : d.dataset_id"
                @click="toggleDataset(d.key)"
              >
                {{ d.label }}
                <span v-if="d.needs_judge" class="ml-1 text-amber-500" title="需裁判模型">⚖</span>
                <span v-if="cachedKeys.has(d.key)" class="ml-1 text-[var(--chart-1)]">●</span>
              </button>
            </div>
          </div>
          <p class="text-[10px] text-muted-foreground">● = 已快取，未快取的會在執行時下載。⚖ = 需裁判模型評分。</p>
          <p
            v-if="longContextSelected"
            class="rounded-md bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-600"
          >
            ⚠ 已選長上下文資料集，需模型有夠大的 <span class="font-mono">max_model_len</span>（數萬 token），否則會被截斷或回 400。
          </p>
          <p
            v-if="toolParserSelected"
            class="rounded-md bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-600"
          >
            ⚠ 已選真實函數調用資料集，需模型啟用 vLLM tool parser（<span class="font-mono">enable_auto_tool_choice</span> + <span class="font-mono">tool_call_parser</span>），否則分數恆為 0。
          </p>
        </div>

        <!-- Params -->
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1">
            <label class="text-xs font-medium text-muted-foreground">每集樣本數（0=全部）</label>
            <Input v-model.number="limit" type="number" min="0" />
          </div>
          <div class="space-y-1">
            <label class="text-xs font-medium text-muted-foreground">重複次數</label>
            <Input v-model.number="repeats" type="number" min="1" />
          </div>
          <div class="space-y-1">
            <label class="text-xs font-medium text-muted-foreground">溫度</label>
            <Input v-model.number="temperature" type="number" min="0" max="2" step="0.1" />
          </div>
          <div class="space-y-1">
            <label class="text-xs font-medium text-muted-foreground">最大輸出 tokens</label>
            <Input v-model.number="maxTokens" type="number" min="1" />
          </div>
          <div class="space-y-1">
            <label class="text-xs font-medium text-muted-foreground">並發數（batch size）</label>
            <Input v-model.number="batchSize" type="number" min="1" />
            <p class="text-[10px] text-muted-foreground">一次對模型發幾個並發請求；調高可跑更快，太高會排隊逾時。不影響分數。</p>
          </div>
        </div>

        <!-- Advanced: dataset_args -->
        <div class="space-y-2 rounded-md border border-border/60 p-3">
          <button class="text-xs font-medium text-muted-foreground hover:text-foreground" @click="showAdvanced = !showAdvanced">
            進階設定 {{ showAdvanced ? '▾' : '▸' }}
          </button>
          <template v-if="showAdvanced">
            <div class="space-y-1">
              <label class="text-xs font-medium text-muted-foreground">few-shot 範例數（0=各資料集預設）</label>
              <Input v-model.number="fewShot" type="number" min="0" />
            </div>
            <div class="space-y-1">
              <label class="text-xs font-medium text-muted-foreground">dataset_args JSON（依資料集名覆寫，選填）</label>
              <textarea
                v-model="datasetArgsJson"
                rows="3"
                spellcheck="false"
                placeholder='例：{"arc": {"subset_list": ["ARC-Challenge"]}}'
                class="w-full rounded-md border border-border bg-background px-2 py-1.5 font-mono text-[11px]"
              />
              <p class="text-[10px] text-muted-foreground">如 few_shot_num、subset_list、shuffle。math 多採樣可配「重複次數」+ <span class="font-mono">aggregation</span>。</p>
            </div>
          </template>
        </div>

        <!-- LLM judge — only shown when a judge-scored (⚖) dataset is selected -->
        <div v-if="judgeEnabled" class="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
          <p class="flex items-center gap-2 text-xs font-medium">
            <span class="text-amber-500">⚖</span>裁判模型
            <span class="text-[10px] font-normal text-muted-foreground">所選問答資料集需要 LLM 評分</span>
          </p>
          <div class="flex gap-1.5">
            <Button size="sm" :variant="judgeTarget === 'internal' ? 'default' : 'outline'" @click="judgeTarget = 'internal'">內部模型</Button>
            <Button size="sm" :variant="judgeTarget === 'external' ? 'default' : 'outline'" @click="judgeTarget = 'external'">外部 API</Button>
          </div>
          <select
            v-if="judgeTarget === 'internal'"
            v-model="judgeModel"
            class="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          >
            <option v-for="g in groups" :key="g" :value="g">{{ g }}{{ groupReady(g) ? '' : ' · 離線' }}</option>
          </select>
          <template v-else>
            <Input v-model="judgeApiUrl" placeholder="API URL，例：https://api.openai.com/v1" />
            <Input v-model="judgeModel" placeholder="裁判模型 ID，例：gpt-4o-mini" />
            <Input v-model="judgeApiKey" type="password" placeholder="API Key（選填）" />
          </template>
          <p class="text-[10px] text-muted-foreground">裁判越強分數越可靠；小模型當裁判僅供參考。</p>
        </div>

        <div class="space-y-1">
          <label class="text-xs font-medium text-muted-foreground">名稱（選填）</label>
          <Input v-model="name" placeholder="例如：Qwen3-0.6B 基線" />
        </div>

        <Button class="w-full" :disabled="launching" @click="launch">
          <Loader2 v-if="launching" class="size-4 animate-spin" />
          <Play v-else class="size-4" />{{ selectedModels.size > 1 ? `排入 ${selectedModels.size} 個評測` : '開始評測' }}
        </Button>
      </Card>

      <!-- Runs + detail -->
      <div class="min-w-0 space-y-4">
        <!-- Run list -->
        <Card class="overflow-hidden">
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 px-5 py-3">
            <div class="flex items-center gap-2">
              <span class="text-sm font-semibold">評測紀錄</span>
              <span v-if="runningCount" class="rounded bg-[var(--chart-1)]/10 px-1.5 py-0.5 text-[10px] text-[var(--chart-1)]">執行中 {{ runningCount }}</span>
              <span v-if="queuedCount" class="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">排隊 {{ queuedCount }}</span>
            </div>
            <div class="flex items-center gap-2">
              <label class="flex items-center gap-1 text-[11px] text-muted-foreground" title="所有並行評測的 batch_size 加總上限；填滿就排隊。即時生效，重啟回預設。">
                並發預算
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
              <span class="text-[10px] text-muted-foreground">用量 {{ usedBudget }}/{{ budget }}</span>
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
                title="加入比較"
                @click.stop="toggleCompare(r.id)"
              />
              <span class="tabular text-xs text-muted-foreground">#{{ r.id }}</span>
              <Badge :variant="STATUS_VARIANT[r.status] ?? 'outline'">{{ r.status }}</Badge>
              <span class="min-w-0 flex-1 truncate">
                <span class="font-medium">{{ r.name || r.model }}</span>
                <span class="ml-2 text-xs text-muted-foreground">{{ runDatasets(r).join(', ') }}</span>
              </span>
              <span class="shrink-0 text-xs text-muted-foreground">{{ formatTime(r.created_at) }}</span>
              <Square
                v-if="r.status === 'running' || r.status === 'queued'"
                class="size-3.5 text-muted-foreground hover:text-status-failed"
                :title="r.status === 'queued' ? '取消排隊' : '取消'"
                @click.stop="cancel(r.id)"
              />
              <Trash2
                v-else
                class="size-3.5 text-muted-foreground hover:text-status-failed"
                @click.stop="remove(r.id)"
              />
            </button>
          </div>
          <p v-else class="px-5 py-8 text-center text-sm text-muted-foreground">尚無評測紀錄。</p>
        </Card>

        <!-- Comparison + selected result; scrolled into view on an explicit pick. -->
        <div ref="resultArea" class="scroll-mt-4 space-y-4">
        <!-- Comparison matrix (datasets × runs) -->
        <Card v-if="compareRuns.length >= 2" class="overflow-hidden">
          <div class="border-b border-border/60 px-5 py-3 text-sm font-semibold">分數比較</div>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border/60 text-left text-xs text-muted-foreground">
                  <th class="px-5 py-2">資料集</th>
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
          <p class="px-5 py-2 text-[10px] text-muted-foreground">每列最高分以主色標示。</p>
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
              完整報告 <ExternalLink class="size-3" />
            </a>
          </div>

          <div v-if="detail.status === 'queued'" class="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 class="size-4 animate-spin" />排隊中…等待並發預算或壓測結束。
          </div>
          <div v-else-if="detail.status === 'running'" class="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 class="size-4 animate-spin" />評測執行中…（可離開此頁）
          </div>
          <div
            v-else-if="detail.status === 'failed'"
            class="break-words rounded-md bg-status-failed/10 p-3 text-sm text-status-failed"
          >
            失敗：{{ detail.error }}
          </div>

          <!-- Scores -->
          <table v-if="result?.datasets.length" class="w-full text-sm">
            <thead>
              <tr class="border-b border-border/60 text-left text-xs text-muted-foreground">
                <th class="py-1.5">資料集</th>
                <th class="py-1.5">指標</th>
                <th class="py-1.5 text-right">樣本</th>
                <th class="py-1.5 text-right">分數</th>
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
            <Loader2 class="size-3.5 animate-spin" />載入詳細數據…
          </div>
          <div v-else-if="reportDetail?.length" class="mt-4 space-y-4">
            <div v-for="d in reportDetail" :key="d.dataset" class="rounded-lg border border-border/60 p-3">
              <p class="mb-2 text-sm font-semibold">{{ d.pretty }}</p>

              <!-- Speed / throughput (eval also measures this) -->
              <div v-if="d.perf" class="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <div class="rounded-md bg-muted/40 p-2">
                  <p class="text-[10px] text-muted-foreground">延遲 p50 / p99</p>
                  <p class="tabular text-sm">{{ fmtNum(d.perf.latency.p50) }}s <span class="text-muted-foreground">/ {{ fmtNum(d.perf.latency.p99) }}s</span></p>
                </div>
                <div class="rounded-md bg-muted/40 p-2">
                  <p class="text-[10px] text-muted-foreground">輸出吞吐</p>
                  <p class="tabular text-sm">{{ fmtNum(d.perf.output_tps) }} <span class="text-muted-foreground">tok/s</span></p>
                </div>
                <div class="rounded-md bg-muted/40 p-2">
                  <p class="text-[10px] text-muted-foreground">平均輸入 tok</p>
                  <p class="tabular text-sm">{{ fmtNum(d.perf.input_tokens_mean, 0) }}</p>
                </div>
                <div class="rounded-md bg-muted/40 p-2">
                  <p class="text-[10px] text-muted-foreground">平均輸出 tok</p>
                  <p class="tabular text-sm">{{ fmtNum(d.perf.output_tokens_mean, 0) }}</p>
                </div>
              </div>

              <!-- Per-subset breakdown (only when a metric spans >1 subject) -->
              <template v-for="m in d.metrics" :key="m.name">
                <div v-if="m.subsets.length > 1" class="mt-3">
                  <p class="mb-1 text-[11px] font-medium text-muted-foreground">{{ m.name }} · 分科目</p>
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
                <summary class="cursor-pointer text-[11px] text-muted-foreground">這個評測在測什麼？</summary>
                <pre class="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted/40 p-2 text-[11px] leading-relaxed text-muted-foreground">{{ d.description }}</pre>
              </details>
            </div>

            <!-- Per-sample browser (lazy: nothing fetched until opened) -->
            <div>
              <Button variant="outline" size="sm" @click="showSamples = !showSamples">
                {{ showSamples ? '收起逐題瀏覽' : '逐題瀏覽（看每題對錯 / 答案）' }}
              </Button>
              <div v-if="showSamples && reportDatasets.length" class="mt-3">
                <EvalSampleBrowser :run-id="detail.id" :datasets="reportDatasets" />
              </div>
            </div>
          </div>

          <!-- Log -->
          <details class="mt-4" :open="detail.status !== 'completed'">
            <summary class="cursor-pointer text-xs text-muted-foreground">執行日誌</summary>
            <pre class="mt-2 max-h-64 overflow-auto rounded-md bg-muted/60 p-3 text-[11px] leading-relaxed">{{ log || '（無日誌）' }}</pre>
          </details>
        </Card>
        </div>
      </div>
    </div>
  </div>
</template>
