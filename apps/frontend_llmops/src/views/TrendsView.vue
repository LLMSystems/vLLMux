<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RefreshCw } from '@lucide/vue'
import { api } from '@/lib/api'
import { useModelsStore } from '@/stores/models'
import Card from '@/components/ui/Card.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import CardTitle from '@/components/ui/CardTitle.vue'
import CardContent from '@/components/ui/CardContent.vue'
import Button from '@/components/ui/Button.vue'
import TimeChart from '@/components/TimeChart.vue'
import { formatLatency, formatNumber, formatPercent } from '@/lib/utils'
import type { TimeseriesPoint } from '@/types/api'

const models = useModelsStore()

const ranges = [
  { label: '15m', window: 900, bucket: 30 },
  { label: '1h', window: 3600, bucket: 60 },
  { label: '6h', window: 21600, bucket: 300 },
  { label: '24h', window: 86400, bucket: 900 },
]
const range = ref(ranges[1]!)
const modelFilter = ref<string>('')
const points = ref<TimeseriesPoint[]>([])
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const groups = computed(() => [...new Set(models.llms.map((m) => m.key.split('::')[0] ?? m.key))])

async function load() {
  loading.value = true
  try {
    points.value = await api.getTimeseries({
      window: range.value.window,
      bucket: range.value.bucket,
      modelKey: modelFilter.value || undefined,
    })
  } catch {
    points.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
  timer = setInterval(load, 15000)
})
onUnmounted(() => timer && clearInterval(timer))
watch([range, modelFilter], load)

// Derived series + headline totals.
const reqSeries = computed(() => points.value.map((p) => ({ ts: p.ts, value: p.count })))
const errSeries = computed(() =>
  points.value.map((p) => ({ ts: p.ts, value: p.count ? (p.error_count / p.count) * 100 : 0 })),
)
const p95Series = computed(() => points.value.map((p) => ({ ts: p.ts, value: p.p95_latency_ms ?? 0 })))
const tokSeries = computed(() => points.value.map((p) => ({ ts: p.ts, value: p.total_tokens })))

const totalReq = computed(() => points.value.reduce((s, p) => s + p.count, 0))
const totalErr = computed(() => points.value.reduce((s, p) => s + p.error_count, 0))
const errRate = computed(() => (totalReq.value ? (totalErr.value / totalReq.value) * 100 : 0))
const peakP95 = computed(() => Math.max(0, ...points.value.map((p) => p.p95_latency_ms ?? 0)))
const totalTokens = computed(() => points.value.reduce((s, p) => s + p.total_tokens, 0))
</script>

<template>
  <div class="space-y-6 p-6">
    <!-- Controls -->
    <div class="flex flex-wrap items-center gap-3">
      <div class="inline-flex rounded-lg border border-border/60 bg-muted/40 p-0.5">
        <button
          v-for="r in ranges"
          :key="r.label"
          class="rounded-md px-3 py-1 text-sm font-medium transition-colors"
          :class="
            range.label === r.label
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          "
          @click="range = r"
        >
          {{ r.label }}
        </button>
      </div>
      <select
        v-model="modelFilter"
        class="h-8 rounded-md border border-input bg-background/40 px-2 text-sm"
      >
        <option value="">全部模型</option>
        <option v-for="g in groups" :key="g" :value="g">{{ g }}</option>
      </select>
      <Button variant="outline" size="sm" class="ml-auto" :disabled="loading" @click="load">
        <RefreshCw class="size-3.5" :class="loading && 'animate-spin'" />重新整理
      </Button>
    </div>

    <div class="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader class="flex-row items-baseline justify-between">
          <CardTitle>請求次數</CardTitle>
          <span class="text-sm font-semibold tabular">共 {{ formatNumber(totalReq) }}</span>
        </CardHeader>
        <CardContent>
          <div class="text-[var(--chart-1)]">
            <TimeChart :points="reqSeries" color="var(--chart-1)" :format="(v) => formatNumber(v)" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader class="flex-row items-baseline justify-between">
          <CardTitle>錯誤率</CardTitle>
          <span class="text-sm font-semibold tabular" :class="errRate > 0 ? 'text-status-failed' : ''">
            {{ formatPercent(errRate) }}
          </span>
        </CardHeader>
        <CardContent>
          <div class="text-status-failed">
            <TimeChart :points="errSeries" color="var(--status-failed)" :format="(v) => `${v.toFixed(0)}%`" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader class="flex-row items-baseline justify-between">
          <CardTitle>p95 延遲</CardTitle>
          <span class="text-sm font-semibold tabular">峰值 {{ formatLatency(peakP95) }}</span>
        </CardHeader>
        <CardContent>
          <div class="text-[var(--chart-4)]">
            <TimeChart :points="p95Series" color="var(--chart-4)" :format="(v) => formatLatency(v)" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader class="flex-row items-baseline justify-between">
          <CardTitle>Tokens</CardTitle>
          <span class="text-sm font-semibold tabular">共 {{ formatNumber(totalTokens) }}</span>
        </CardHeader>
        <CardContent>
          <div class="text-[var(--chart-2)]">
            <TimeChart :points="tokSeries" color="var(--chart-2)" :format="(v) => formatNumber(v, true)" />
          </div>
        </CardContent>
      </Card>
    </div>
  </div>
</template>
