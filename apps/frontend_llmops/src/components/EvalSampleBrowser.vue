<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CheckCircle2, ChevronLeft, ChevronRight, Loader2, XCircle } from '@lucide/vue'
import { api } from '@/lib/api'
import { toast } from '@/lib/toast'
import type { EvalSampleDetail, EvalSamplesPage } from '@/types/api'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'

// The browser only ever holds one page of compact rows + one expanded sample,
// so the DOM and payload stay bounded no matter how many samples a run has.
const props = defineProps<{ runId: number; datasets: string[] }>()

const dataset = ref(props.datasets[0] ?? '')
const filter = ref<'all' | 'correct' | 'wrong'>('all')
const page = ref(1)
const pageSize = 25
const data = ref<EvalSamplesPage | null>(null)
const loading = ref(false)

const expanded = ref<number | null>(null)
const detail = ref<EvalSampleDetail | null>(null)
const loadingDetail = ref(false)

const pageCount = computed(() =>
  data.value ? Math.max(1, Math.ceil(data.value.total / data.value.page_size)) : 1,
)

async function load() {
  if (!dataset.value) return
  loading.value = true
  try {
    data.value = await api.getEvalSamples(props.runId, dataset.value, {
      filter: filter.value,
      page: page.value,
      pageSize,
    })
  } catch (e) {
    toast.error('無法載入逐題資料', { description: String(e) })
  } finally {
    loading.value = false
  }
}

async function toggleRow(index: number) {
  if (expanded.value === index) {
    expanded.value = null
    detail.value = null
    return
  }
  expanded.value = index
  detail.value = null
  loadingDetail.value = true
  try {
    detail.value = await api.getEvalSample(props.runId, dataset.value, index)
  } catch (e) {
    toast.error('無法載入此題詳情', { description: String(e) })
  } finally {
    loadingDetail.value = false
  }
}

function setFilter(f: 'all' | 'correct' | 'wrong') {
  filter.value = f
  page.value = 1
}
function setDataset(d: string) {
  dataset.value = d
  filter.value = 'all'
  page.value = 1
}
function go(delta: number) {
  page.value = Math.min(pageCount.value, Math.max(1, page.value + delta))
}

// Any change to the query re-fetches; switching rows away collapses the detail.
watch([dataset, filter, page], () => {
  expanded.value = null
  detail.value = null
  void load()
}, { immediate: true })

function pct(v: number | null) {
  return v == null ? '—' : `${(v * 100).toFixed(0)}%`
}

// Rule-scored datasets (e.g. IFEval) have no gold answer — `target` is empty.
// Drop the "standard answer" column/box for them so it doesn't look broken.
function hasText(t: string | null | undefined) {
  return !!t && t.trim().length > 0
}
const showTargetCol = computed(() => (data.value?.samples ?? []).some((s) => hasText(s.target)))
const colCount = computed(() => (showTargetCol.value ? 4 : 3))
</script>

<template>
  <div class="space-y-3">
    <!-- Dataset tabs (only when a run covers more than one) -->
    <div v-if="datasets.length > 1" class="flex flex-wrap gap-1.5">
      <button
        v-for="d in datasets"
        :key="d"
        class="rounded-md border px-2 py-1 text-xs transition-colors"
        :class="dataset === d ? 'border-[var(--chart-1)] bg-[var(--chart-1)]/10 text-foreground' : 'border-border text-muted-foreground hover:bg-muted'"
        @click="setDataset(d)"
      >{{ d }}</button>
    </div>

    <!-- Filter + paging header -->
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-1.5">
        <Button :variant="filter === 'all' ? 'default' : 'outline'" size="sm" @click="setFilter('all')">
          全部 <span class="ml-1 text-[10px] opacity-70">{{ data?.total_all ?? '—' }}</span>
        </Button>
        <Button :variant="filter === 'correct' ? 'default' : 'outline'" size="sm" @click="setFilter('correct')">
          答對 <span class="ml-1 text-[10px] opacity-70">{{ data?.total_correct ?? '—' }}</span>
        </Button>
        <Button :variant="filter === 'wrong' ? 'default' : 'outline'" size="sm" @click="setFilter('wrong')">
          答錯 <span class="ml-1 text-[10px] opacity-70">{{ data ? data.total_all - data.total_correct : '—' }}</span>
        </Button>
      </div>
      <div class="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 v-if="loading" class="size-3.5 animate-spin" />
        <span>第 {{ data?.page ?? 1 }} / {{ pageCount }} 頁 · 共 {{ data?.total ?? 0 }} 題</span>
        <Button size="icon-sm" variant="ghost" :disabled="page <= 1" @click="go(-1)"><ChevronLeft class="size-4" /></Button>
        <Button size="icon-sm" variant="ghost" :disabled="page >= pageCount" @click="go(1)"><ChevronRight class="size-4" /></Button>
      </div>
    </div>

    <!-- Rows -->
    <div class="overflow-hidden rounded-lg border border-border/60">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border/60 bg-muted/30 text-left text-xs text-muted-foreground">
            <th class="px-3 py-2 w-10">#</th>
            <th class="px-3 py-2 w-14">結果</th>
            <th class="px-3 py-2">{{ showTargetCol ? '模型答案（節錄）' : '模型輸出（節錄）' }}</th>
            <th v-if="showTargetCol" class="px-3 py-2 w-28">標準答案</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="r in data?.samples ?? []" :key="r.index">
            <tr
              class="cursor-pointer border-b border-border/40 hover:bg-accent/30"
              :class="expanded === r.index ? 'bg-accent/40' : ''"
              @click="toggleRow(r.index)"
            >
              <td class="px-3 py-2 tabular text-muted-foreground">{{ r.index }}</td>
              <td class="px-3 py-2">
                <CheckCircle2 v-if="r.correct" class="size-4 text-status-ready" />
                <XCircle v-else class="size-4 text-status-failed" />
              </td>
              <td class="px-3 py-2"><span class="line-clamp-1 font-mono text-xs">{{ r.extracted || r.preview || '—' }}</span></td>
              <td v-if="showTargetCol" class="px-3 py-2"><span class="line-clamp-1 font-mono text-xs text-muted-foreground">{{ hasText(r.target) ? r.target : '—' }}</span></td>
            </tr>
            <!-- Expanded: full prompt + answer + target + scores (fetched lazily) -->
            <tr v-if="expanded === r.index" :key="r.index + '-d'" class="border-b border-border/40 bg-background/40">
              <td :colspan="colCount" class="px-4 py-3">
                <div v-if="loadingDetail" class="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 class="size-3.5 animate-spin" />載入中…
                </div>
                <div v-else-if="detail" class="space-y-3 text-xs">
                  <div class="flex flex-wrap items-center gap-2">
                    <Badge :class="detail.correct ? 'text-status-ready' : 'text-status-failed'" variant="muted">
                      {{ detail.correct ? '答對' : '答錯' }}
                    </Badge>
                    <span v-for="(v, k) in detail.scores" :key="k" class="font-mono text-[11px] text-muted-foreground">{{ k }}={{ pct(v) }}</span>
                    <span v-if="detail.perf" class="ml-auto font-mono text-[11px] text-muted-foreground">
                      {{ detail.perf.latency != null ? detail.perf.latency.toFixed(1) + 's' : '' }}
                      · {{ detail.perf.output_tokens ?? '?' }} tok
                    </span>
                  </div>
                  <div v-if="hasText(detail.target)">
                    <p class="mb-1 font-medium text-muted-foreground">標準答案</p>
                    <pre class="max-h-24 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 font-mono">{{ detail.target }}</pre>
                  </div>
                  <p v-else class="text-[11px] text-muted-foreground">此為規則評分（檢查是否遵守指令），無單一標準答案；對錯看上方各項指標。</p>
                  <div>
                    <p class="mb-1 font-medium text-muted-foreground">模型回答</p>
                    <pre class="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 font-mono">{{ detail.answer ?? '—' }}</pre>
                  </div>
                  <details>
                    <summary class="cursor-pointer text-muted-foreground">題目 / Prompt</summary>
                    <pre class="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 font-mono">{{ detail.prompt.map((m) => `[${m.role}] ${m.content}`).join('\n\n') }}</pre>
                  </details>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="data && !data.samples.length">
            <td :colspan="colCount" class="px-4 py-6 text-center text-sm text-muted-foreground">沒有符合的題目。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
