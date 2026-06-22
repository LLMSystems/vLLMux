<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CheckCircle2, ChevronLeft, ChevronRight, Loader2, XCircle } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import { api } from '@/lib/api'
import { toast } from '@/lib/toast'
import type { EvalSampleDetail, EvalSamplesPage } from '@/types/api'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'

const props = defineProps<{ runId: number; datasets: string[] }>()
const { t } = useI18n()

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
    toast.error(t('evalSamples.loadFailed'), { description: String(e) })
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
    toast.error(t('evalSamples.loadDetailFailed'), { description: String(e) })
  } finally {
    loadingDetail.value = false
  }
}

function setFilter(next: 'all' | 'correct' | 'wrong') {
  filter.value = next
  page.value = 1
}

function setDataset(next: string) {
  dataset.value = next
  filter.value = 'all'
  page.value = 1
}

function go(delta: number) {
  page.value = Math.min(pageCount.value, Math.max(1, page.value + delta))
}

watch(
  [dataset, filter, page],
  () => {
    expanded.value = null
    detail.value = null
    void load()
  },
  { immediate: true },
)

function pct(v: number | null) {
  return v == null ? '--' : `${(v * 100).toFixed(0)}%`
}

function hasText(text: string | null | undefined) {
  return !!text && text.trim().length > 0
}

const showTargetCol = computed(() => (data.value?.samples ?? []).some((sample) => hasText(sample.target)))
const colCount = computed(() => (showTargetCol.value ? 4 : 3))
</script>

<template>
  <div class="space-y-3">
    <div v-if="datasets.length > 1" class="flex flex-wrap gap-1.5">
      <button
        v-for="name in datasets"
        :key="name"
        class="rounded-md border px-2 py-1 text-xs transition-colors"
        :class="
          dataset === name
            ? 'border-[var(--chart-1)] bg-[var(--chart-1)]/10 text-foreground'
            : 'border-border text-muted-foreground hover:bg-muted'
        "
        @click="setDataset(name)"
      >
        {{ name }}
      </button>
    </div>

    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-1.5">
        <Button :variant="filter === 'all' ? 'default' : 'outline'" size="sm" @click="setFilter('all')">
          {{ t('evalSamples.filterAll') }}
          <span class="ml-1 text-[10px] opacity-70">{{ data?.total_all ?? '--' }}</span>
        </Button>
        <Button :variant="filter === 'correct' ? 'default' : 'outline'" size="sm" @click="setFilter('correct')">
          {{ t('evalSamples.filterCorrect') }}
          <span class="ml-1 text-[10px] opacity-70">{{ data?.total_correct ?? '--' }}</span>
        </Button>
        <Button :variant="filter === 'wrong' ? 'default' : 'outline'" size="sm" @click="setFilter('wrong')">
          {{ t('evalSamples.filterWrong') }}
          <span class="ml-1 text-[10px] opacity-70">{{ data ? data.total_all - data.total_correct : '--' }}</span>
        </Button>
      </div>
      <div class="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 v-if="loading" class="size-3.5 animate-spin" />
        <span>
          {{ t('evalSamples.pageInfo', { page: data?.page ?? 1, total: pageCount, count: data?.total ?? 0 }) }}
        </span>
        <Button size="icon-sm" variant="ghost" :disabled="page <= 1" @click="go(-1)">
          <ChevronLeft class="size-4" />
        </Button>
        <Button size="icon-sm" variant="ghost" :disabled="page >= pageCount" @click="go(1)">
          <ChevronRight class="size-4" />
        </Button>
      </div>
    </div>

    <div class="overflow-hidden rounded-lg border border-border/60">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border/60 bg-muted/30 text-left text-xs text-muted-foreground">
            <th class="w-10 px-3 py-2">#</th>
            <th class="w-14 px-3 py-2">{{ t('evalSamples.colResult') }}</th>
            <th class="px-3 py-2">
              {{ showTargetCol ? t('evalSamples.colModelAnswer') : t('evalSamples.colModelOutput') }}
            </th>
            <th v-if="showTargetCol" class="w-28 px-3 py-2">{{ t('evalSamples.colStandardAnswer') }}</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="sample in data?.samples ?? []" :key="sample.index">
            <tr
              class="cursor-pointer border-b border-border/40 hover:bg-accent/30"
              :class="expanded === sample.index ? 'bg-accent/40' : ''"
              @click="toggleRow(sample.index)"
            >
              <td class="px-3 py-2 tabular text-muted-foreground">{{ sample.index }}</td>
              <td class="px-3 py-2">
                <CheckCircle2 v-if="sample.correct" class="size-4 text-status-ready" />
                <XCircle v-else class="size-4 text-status-failed" />
              </td>
              <td class="px-3 py-2">
                <span class="line-clamp-1 font-mono text-xs">{{ sample.extracted || sample.preview || '--' }}</span>
              </td>
              <td v-if="showTargetCol" class="px-3 py-2">
                <span class="line-clamp-1 font-mono text-xs text-muted-foreground">
                  {{ hasText(sample.target) ? sample.target : '--' }}
                </span>
              </td>
            </tr>
            <tr v-if="expanded === sample.index" :key="`${sample.index}-detail`" class="border-b border-border/40 bg-background/40">
              <td :colspan="colCount" class="px-4 py-3">
                <div v-if="loadingDetail" class="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 class="size-3.5 animate-spin" />{{ t('common.loading') }}
                </div>
                <div v-else-if="detail" class="space-y-3 text-xs">
                  <div class="flex flex-wrap items-center gap-2">
                    <Badge :class="detail.correct ? 'text-status-ready' : 'text-status-failed'" variant="muted">
                      {{ detail.correct ? t('evalSamples.correct') : t('evalSamples.wrong') }}
                    </Badge>
                    <span
                      v-for="(value, key) in detail.scores"
                      :key="key"
                      class="font-mono text-[11px] text-muted-foreground"
                    >
                      {{ key }}={{ pct(value) }}
                    </span>
                    <span v-if="detail.perf" class="ml-auto font-mono text-[11px] text-muted-foreground">
                      {{ detail.perf.latency != null ? detail.perf.latency.toFixed(1) + 's' : '' }}
                      · {{ detail.perf.output_tokens ?? '?' }} tok
                    </span>
                  </div>
                  <div v-if="hasText(detail.target)">
                    <p class="mb-1 font-medium text-muted-foreground">{{ t('evalSamples.standardAnswer') }}</p>
                    <pre class="max-h-24 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 font-mono">{{ detail.target }}</pre>
                  </div>
                  <p v-else class="text-[11px] text-muted-foreground">{{ t('evalSamples.ruleScoreHint') }}</p>
                  <div>
                    <p class="mb-1 font-medium text-muted-foreground">{{ t('evalSamples.modelAnswer') }}</p>
                    <pre class="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 font-mono">{{ detail.answer ?? '--' }}</pre>
                  </div>
                  <details>
                    <summary class="cursor-pointer text-muted-foreground">{{ t('evalSamples.promptLabel') }}</summary>
                    <pre class="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 font-mono">{{ detail.prompt.map((msg) => `[${msg.role}] ${msg.content}`).join('\n\n') }}</pre>
                  </details>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="data && !data.samples.length">
            <td :colspan="colCount" class="px-4 py-6 text-center text-sm text-muted-foreground">
              {{ t('evalSamples.noMatchingSamples') }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
