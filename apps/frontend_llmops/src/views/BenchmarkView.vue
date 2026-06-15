<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Check, ChevronDown, ExternalLink, Gauge, Loader2, Play, Plus, Square, Trash2, X } from '@lucide/vue'
import { api, ApiError } from '@/lib/api'
import { useModelsStore } from '@/stores/models'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatLatency, formatNumber, formatTime } from '@/lib/utils'
import type { PerfPoint, PerfRequest, PerfRun, SlaGroup } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import PerfSweepChart from '@/components/PerfSweepChart.vue'

const models = useModelsStore()
const { ensureUnlocked } = useAuth()

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
const mode = ref<'sweep' | 'sla'>('sweep')
const parallelInput = ref('1,4,8,16')
const reqPerPoint = ref(50)
const maxTokens = ref(256)
const promptLen = ref(512)
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
  { v: 'p99_latency', label: 'p99 延遲 (s)' },
  { v: 'avg_latency', label: '平均延遲 (s)' },
  { v: 'p99_ttft', label: 'p99 TTFT (ms)' },
  { v: 'avg_ttft', label: '平均 TTFT (ms)' },
  { v: 'p99_tpot', label: 'p99 TPOT (ms)' },
  { v: 'avg_tpot', label: '平均 TPOT (ms)' },
  { v: 'rps', label: 'RPS (req/s)' },
  { v: 'tps', label: '輸出 tok/s' },
]
const SLA_OPS = ['<=', '<', '>=', '>', 'max', 'min']
function addCondition() {
  slaConditions.value.push({ metric: 'avg_ttft', op: '<=', value: 200 })
}
function removeCondition(i: number) {
  slaConditions.value.splice(i, 1)
}

const instanceOptions = computed(() =>
  models.llms.filter((m) => m.key.split('::')[0] === model.value).map((m) => m.key),
)
const parallel = computed(() =>
  parallelInput.value.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => n > 0),
)

watch(groups, (g) => {
  if (!model.value && g.length) model.value = g.find(groupReady) ?? g[0]!
}, { immediate: true })
watch(model, () => { instanceKey.value = instanceOptions.value[0] ?? '' })

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
    toast.error('無法載入結果', { description: String(e) })
  }
  await loadLog()
}

async function loadLog() {
  if (selectedId.value == null) return
  try {
    log.value = (await api.getPerfLog(selectedId.value)).content
  } catch {
    log.value = ''
  }
}

async function launch() {
  if (!model.value || launching.value) return
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
    stream: stream.value,
  }
  let req: PerfRequest
  if (mode.value === 'sla') {
    if (!slaConditions.value.length) {
      toast.error('請至少加入一個 SLA 條件')
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
  } else {
    if (!parallel.value.length) {
      toast.error('請輸入至少一個並發數')
      return
    }
    req = {
      ...common,
      parallel: parallel.value,
      number: parallel.value.map(() => reqPerPoint.value),
      warmup_num: warmup.value || undefined,
    }
  }
  if (!(await ensureUnlocked())) return
  launching.value = true
  try {
    const run = await api.startPerf(req)
    toast.success(`已開始壓測 #${run.id}`, { description: '可離開此頁，背景持續執行。' })
    await loadRuns()
    await select(run.id)
  } catch (e) {
    toast.error('無法開始壓測', { description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e) })
  } finally {
    launching.value = false
  }
}

async function cancel(id: number) {
  try {
    await api.cancelPerf(id)
    toast.info('已要求取消')
    await loadRuns()
  } catch (e) {
    toast.error('取消失敗', { description: String(e) })
  }
}

async function remove(id: number) {
  if (!confirm(`刪除壓測 #${id}？`)) return
  try {
    await api.deletePerf(id)
    if (selectedId.value === id) { selectedId.value = null; selected.value = null; points.value = [] }
    await loadRuns()
  } catch (e) {
    toast.error('刪除失敗', { description: String(e) })
  }
}

// Re-fetch the selected run's detail once it leaves the running state.
watch(runs, (list) => {
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
  <div class="grid gap-4 p-6 lg:grid-cols-[20rem_1fr]">
    <!-- Config -->
    <Card class="h-fit p-5">
      <p class="mb-4 flex items-center gap-2 text-sm font-semibold"><Gauge class="size-4" />壓測設定</p>
      <div class="space-y-3 text-sm">
        <label class="block">
          <span class="text-xs text-muted-foreground">模型</span>
          <select v-model="model" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
            <option v-for="g in groups" :key="g" :value="g">{{ g }}{{ groupReady(g) ? '' : '（未啟動）' }}</option>
          </select>
        </label>
        <div class="inline-flex w-full rounded-lg border border-border/60 bg-muted/40 p-0.5">
          <button
            v-for="m in (['sweep', 'sla'] as const)"
            :key="m"
            class="flex-1 rounded-md px-3 py-1 text-xs font-medium transition-colors"
            :class="mode === m ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'"
            @click="mode = m"
          >
            {{ m === 'sweep' ? '並發 Sweep' : 'SLA 自動調優' }}
          </button>
        </div>
        <label class="block">
          <span class="text-xs text-muted-foreground">目標</span>
          <select v-model="target" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
            <option value="router">路由器（整體 + 負載平衡）</option>
            <option value="instance">單一實例</option>
          </select>
        </label>
        <label v-if="target === 'instance'" class="block">
          <span class="text-xs text-muted-foreground">實例</span>
          <select v-model="instanceKey" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
            <option v-for="k in instanceOptions" :key="k" :value="k">{{ k.split('::')[1] }}</option>
          </select>
        </label>
        <div class="grid grid-cols-2 gap-2">
          <label class="block">
            <span class="text-xs text-muted-foreground">資料集</span>
            <select v-model="dataset" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
              <option value="random">random</option>
              <option value="openqa">openqa</option>
            </select>
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">端點</span>
            <select v-model="endpoint" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
              <option value="chat">chat</option>
              <option value="completions">completions</option>
            </select>
          </label>
        </div>
        <!-- Sweep-specific -->
        <template v-if="mode === 'sweep'">
          <label class="block">
            <span class="text-xs text-muted-foreground">並發點（逗號分隔，掃描）</span>
            <Input v-model="parallelInput" placeholder="1,4,8,16" class="mt-1 font-mono" />
          </label>
          <div class="grid grid-cols-2 gap-2">
            <label class="block">
              <span class="text-xs text-muted-foreground">每點請求數</span>
              <Input v-model.number="reqPerPoint" type="number" min="1" class="mt-1" />
            </label>
            <label class="block">
              <span class="text-xs text-muted-foreground">預熱比例</span>
              <Input v-model.number="warmup" type="number" min="0" step="0.1" class="mt-1" />
            </label>
          </div>
        </template>

        <!-- SLA-specific (conditions OR-ed; each runs its own binary search) -->
        <template v-else>
          <label class="block">
            <span class="text-xs text-muted-foreground">搜尋變數</span>
            <select v-model="slaVariable" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm">
              <option value="parallel">並發 parallel</option>
              <option value="rate">速率 rate（open-loop）</option>
            </select>
          </label>
          <div>
            <div class="mb-1 flex items-center justify-between">
              <span class="text-xs text-muted-foreground">SLA 條件（多條 = OR）</span>
              <Button size="sm" variant="ghost" @click="addCondition"><Plus class="size-3.5" />新增</Button>
            </div>
            <div class="space-y-1.5">
              <div v-for="(c, i) in slaConditions" :key="i" class="flex items-center gap-1.5">
                <select v-model="c.metric" class="h-8 flex-1 rounded-md border border-input bg-background/40 px-1 text-xs">
                  <option v-for="m in SLA_METRICS" :key="m.v" :value="m.v">{{ m.label }}</option>
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
            <span class="text-xs text-muted-foreground">固定並發（rate 模式）</span>
            <Input v-model.number="slaFixedParallel" type="number" min="1" class="mt-1" />
          </label>
          <div class="grid grid-cols-3 gap-2">
            <label class="block">
              <span class="text-xs text-muted-foreground">下界</span>
              <Input v-model.number="slaLower" type="number" min="1" class="mt-1" />
            </label>
            <label class="block">
              <span class="text-xs text-muted-foreground">上界</span>
              <Input v-model.number="slaUpper" type="number" min="1" class="mt-1" />
            </label>
            <label class="block">
              <span class="text-xs text-muted-foreground">每點 runs</span>
              <Input v-model.number="slaNumRuns" type="number" min="1" class="mt-1" />
            </label>
          </div>
        </template>

        <!-- Common knobs -->
        <div class="grid grid-cols-2 gap-2">
          <label class="block">
            <span class="text-xs text-muted-foreground">輸出 tokens</span>
            <Input v-model.number="maxTokens" type="number" min="1" class="mt-1" />
          </label>
          <label v-if="dataset === 'random'" class="block">
            <span class="text-xs text-muted-foreground">輸入長度</span>
            <Input v-model.number="promptLen" type="number" min="1" class="mt-1" />
          </label>
        </div>
        <label class="block">
          <span class="text-xs text-muted-foreground">名稱（選填）</span>
          <Input v-model="name" placeholder="例如：q05b-baseline" class="mt-1" />
        </label>
        <label class="flex items-center justify-between">
          <span class="text-xs text-muted-foreground">串流（量 TTFT 必須）</span>
          <input v-model="stream" type="checkbox" class="size-4 accent-[var(--chart-1)]" />
        </label>

        <Button class="w-full" :disabled="launching || busy || !model" @click="launch">
          <Loader2 v-if="launching" class="size-4 animate-spin" /><Play v-else class="size-4" />
          {{ busy ? '有壓測進行中…' : '開始壓測' }}
        </Button>
      </div>
    </Card>

    <!-- Runs + results -->
    <div class="space-y-4">
      <!-- History -->
      <Card class="overflow-hidden">
        <div class="border-b border-border/60 px-4 py-2.5 text-sm font-semibold">壓測歷史</div>
        <div v-if="runs.length" class="divide-y divide-border/60">
          <div
            v-for="r in runs"
            :key="r.id"
            class="flex cursor-pointer items-center gap-3 px-4 py-2.5 hover:bg-accent/30"
            :class="selectedId === r.id ? 'bg-accent/40' : ''"
            @click="select(r.id)"
          >
            <Loader2 v-if="r.status === 'running'" class="size-3.5 shrink-0 animate-spin text-status-starting" />
            <span v-else class="size-2 shrink-0 rounded-full" :class="r.status === 'completed' ? 'bg-status-ready' : r.status === 'failed' ? 'bg-status-failed' : 'bg-muted'" />
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm font-medium">{{ r.name || r.model }} <span class="text-xs text-muted-foreground">#{{ r.id }}</span></p>
              <p class="truncate font-mono text-[11px] text-muted-foreground">{{ r.model }} · {{ formatTime(r.created_at) }}</p>
            </div>
            <Badge variant="muted" :class="statusColor[r.status]">{{ r.status }}</Badge>
            <Button v-if="r.status === 'running'" size="icon-sm" variant="ghost" title="取消" @click.stop="cancel(r.id)"><Square class="size-3.5" /></Button>
            <Button v-else size="icon-sm" variant="ghost" title="刪除" @click.stop="remove(r.id)"><Trash2 class="size-3.5" /></Button>
          </div>
        </div>
        <p v-else class="px-4 py-8 text-center text-sm text-muted-foreground">尚無壓測紀錄。</p>
      </Card>

      <!-- Selected detail -->
      <template v-if="selected">
        <div v-if="selected.status === 'failed'" class="rounded-lg border border-status-failed/30 bg-status-failed/10 p-3 text-xs text-status-failed">
          失敗：{{ selected.error }}
        </div>

        <!-- Run summary (Basic Information) -->
        <Card v-if="points.length" class="p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-3">
              <div><span class="text-muted-foreground">模型</span> <span class="font-mono">{{ selected.model }}</span></div>
              <div><span class="text-muted-foreground">資料集</span> {{ parsedParams.dataset ?? '—' }}</div>
              <div><span class="text-muted-foreground">目標</span> <span class="font-mono">{{ selected.target_url.replace(/^https?:\/\//, '') }}</span></div>
              <div>
                <span class="text-muted-foreground">模式</span>
                {{ sla ? `SLA（${parsedParams.sla_variable ?? 'parallel'}）` : '並發 Sweep' }}
              </div>
              <div><span class="text-muted-foreground">輸出 tokens</span> {{ parsedParams.max_tokens ?? '—' }}</div>
              <div><span class="text-muted-foreground">總測試時間</span> <span class="tabular">{{ aggregates.duration.toFixed(1) }}s</span></div>
              <div><span class="text-muted-foreground">總生成</span> <span class="tabular">{{ formatNumber(Math.round(aggregates.generated)) }} tok</span></div>
              <div><span class="text-muted-foreground">平均輸出速率</span> <span class="tabular">{{ aggregates.rate.toFixed(1) }} tok/s</span></div>
            </div>
            <!-- evalscope only writes an HTML report for sweep runs. -->
            <Button v-if="!sla" variant="outline" size="sm" class="shrink-0" @click="openReport">
              <ExternalLink class="size-3.5" />完整報告
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
                  <span class="text-sm font-normal text-muted-foreground">最大 {{ g.variable === 'rate' ? '速率' : '並發' }}</span>
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

        <!-- Charts: throughput + decode + tail latency vs concurrency -->
        <div v-if="points.length" class="grid gap-4 lg:grid-cols-3">
          <Card class="p-4"><PerfSweepChart :points="points" :metric="(p: PerfPoint) => p.rps" label="RPS（req/s）" color="var(--chart-1)" :format="(v) => v.toFixed(1)" /></Card>
          <Card class="p-4"><PerfSweepChart :points="points" :metric="(p: PerfPoint) => p.output_tps" label="輸出吞吐 Gen/s（tok/s）" color="var(--chart-2)" :format="(v) => formatNumber(Math.round(v))" /></Card>
          <Card class="p-4"><PerfSweepChart :points="points" :metric="(p: PerfPoint) => p.ttft_p99 ?? p.avg_ttft" label="TTFT p99（ms）" color="var(--chart-4)" :format="(v) => `${Math.round(v)}`" /></Card>
        </div>

        <!-- Table (click a row for avg/p50/p99/max detail) -->
        <Card v-if="points.length" class="overflow-hidden">
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead class="border-b border-border/60 text-xs text-muted-foreground">
                <tr class="[&>th]:px-3 [&>th]:py-2 [&>th]:text-right [&>th]:font-medium [&>th:first-child]:text-left">
                  <th>並發</th><th>RPS</th><th>Gen/s</th><th>Total tok/s</th><th>平均延遲</th><th>TTFT</th><th>TTFT p99</th><th>TPOT</th><th>ITL</th><th>延遲 p99</th><th>輸入/輸出</th><th>成功/總數</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="p in points" :key="p.label">
                  <tr
                    class="cursor-pointer border-b border-border/40 [&>td]:px-3 [&>td]:py-2 [&>td]:text-right [&>td:first-child]:text-left hover:bg-accent/30"
                    @click="toggle(p.label)"
                  >
                    <td class="font-mono">
                      <ChevronDown class="mr-1 inline size-3 transition-transform" :class="expanded === p.label && 'rotate-180'" />{{ p.concurrency ?? p.rate }}
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
                          { name: '延遲 Latency (s)', avg: p.avg_latency, p50: p.latency_p50, p99: p.latency_p99, max: p.latency_max, ms: false },
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
          <p class="mb-2 text-xs font-medium text-muted-foreground">執行日誌</p>
          <pre class="max-h-64 overflow-auto rounded-lg border border-border/60 bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-foreground/90">{{ log || '（等待輸出…）' }}</pre>
        </Card>
      </template>
    </div>
  </div>
</template>
