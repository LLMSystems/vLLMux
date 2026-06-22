<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { PerfPoint } from '@/types/api'

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    points: PerfPoint[]
    metric: (p: PerfPoint) => number | null
    label: string
    color?: string
    format?: (v: number) => string
    xLabel?: string
  }>(),
  { color: 'var(--chart-1)', format: (v: number) => `${Math.round(v)}` },
)
const displayXLabel = computed(() => props.xLabel ?? t('benchmark.tableParallel'))

// x = concurrency for closed-loop; for open-loop (concurrency = -1) fall back to rate.
function xVal(p: PerfPoint): number | null {
  return p.concurrency != null && p.concurrency > 0 ? p.concurrency : p.rate
}

const W = 320
const H = 170
const PAD_L = 40
const PAD_R = 10
const PAD_T = 10
const PAD_B = 26

const pts = computed(() =>
  props.points
    .filter((p) => xVal(p) != null && props.metric(p) != null)
    .map((p) => ({ x: xVal(p) as number, y: props.metric(p) as number }))
    .sort((a, b) => a.x - b.x),
)
const xMin = computed(() => (pts.value.length ? pts.value[0]!.x : 0))
const xMax = computed(() => (pts.value.length ? pts.value[pts.value.length - 1]!.x : 1))
const yMax = computed(() => Math.max(1, ...pts.value.map((p) => p.y)))

function px(x: number): number {
  const span = xMax.value - xMin.value || 1
  return PAD_L + ((x - xMin.value) / span) * (W - PAD_L - PAD_R)
}
function py(y: number): number {
  return PAD_T + (1 - y / yMax.value) * (H - PAD_T - PAD_B)
}
const line = computed(() => pts.value.map((p) => `${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`).join(' '))
const yTicks = computed(() => [0, 0.5, 1].map((f) => ({ v: yMax.value * f, y: py(yMax.value * f) })))
</script>

<template>
  <div>
    <p class="mb-1 text-xs font-medium text-muted-foreground">{{ label }}</p>
    <svg :viewBox="`0 0 ${W} ${H}`" class="w-full" preserveAspectRatio="none">
      <!-- y gridlines + labels -->
      <g v-for="t in yTicks" :key="t.v">
        <line :x1="PAD_L" :x2="W - PAD_R" :y1="t.y" :y2="t.y" stroke="var(--border)" stroke-opacity="0.4"
          stroke-width="1" vector-effect="non-scaling-stroke" />
        <text :x="PAD_L - 4" :y="t.y + 3" text-anchor="end" class="fill-muted-foreground" style="font-size: 9px">
          {{ format(t.v) }}
        </text>
      </g>
      <!-- axes -->
      <line :x1="PAD_L" :x2="PAD_L" :y1="PAD_T" :y2="H - PAD_B" stroke="var(--border)" stroke-width="1" vector-effect="non-scaling-stroke" />
      <line :x1="PAD_L" :x2="W - PAD_R" :y1="H - PAD_B" :y2="H - PAD_B" stroke="var(--border)" stroke-width="1" vector-effect="non-scaling-stroke" />
      <!-- series -->
      <polyline v-if="pts.length > 1" :points="line" fill="none" :stroke="color" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
      <g v-for="(p, i) in pts" :key="i">
        <circle :cx="px(p.x)" :cy="py(p.y)" r="3" :fill="color" />
        <!-- x tick labels (concurrency) -->
        <text :x="px(p.x)" :y="H - PAD_B + 12" text-anchor="middle" class="fill-muted-foreground" style="font-size: 9px">
          {{ p.x }}
        </text>
      </g>
      <text :x="(PAD_L + W - PAD_R) / 2" :y="H - 2" text-anchor="middle" class="fill-muted-foreground" style="font-size: 9px">
        {{ displayXLabel }}
      </text>
    </svg>
  </div>
</template>
