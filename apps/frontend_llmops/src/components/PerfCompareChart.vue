<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { PerfPoint } from '@/types/api'

export interface CompareSeries {
  label: string
  color: string
  points: PerfPoint[]
}

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    series: CompareSeries[]
    metric: (p: PerfPoint) => number | null
    label: string
    format?: (v: number) => string
    xLabel?: string
  }>(),
  { format: (v: number) => `${Math.round(v)}` },
)
const displayXLabel = computed(() => props.xLabel ?? t('benchmark.tableParallel'))

function xVal(p: PerfPoint): number | null {
  return p.concurrency != null && p.concurrency > 0 ? p.concurrency : p.rate
}

const W = 320
const H = 180
const PAD_L = 40
const PAD_R = 10
const PAD_T = 10
const PAD_B = 26

const lines = computed(() =>
  props.series.map((s) => ({
    color: s.color,
    pts: s.points
      .filter((p) => xVal(p) != null && props.metric(p) != null)
      .map((p) => ({ x: xVal(p) as number, y: props.metric(p) as number }))
      .sort((a, b) => a.x - b.x),
  })),
)
const allPts = computed(() => lines.value.flatMap((l) => l.pts))
const xMin = computed(() => (allPts.value.length ? Math.min(...allPts.value.map((p) => p.x)) : 0))
const xMax = computed(() => (allPts.value.length ? Math.max(...allPts.value.map((p) => p.x)) : 1))
const yMax = computed(() => Math.max(1, ...allPts.value.map((p) => p.y)))

function px(x: number): number {
  const span = xMax.value - xMin.value || 1
  return PAD_L + ((x - xMin.value) / span) * (W - PAD_L - PAD_R)
}
function py(y: number): number {
  return PAD_T + (1 - y / yMax.value) * (H - PAD_T - PAD_B)
}
function poly(pts: { x: number; y: number }[]): string {
  return pts.map((p) => `${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`).join(' ')
}
const yTicks = computed(() => [0, 0.5, 1].map((f) => ({ v: yMax.value * f, y: py(yMax.value * f) })))
const xTicks = computed(() => [...new Set(allPts.value.map((p) => p.x))].sort((a, b) => a - b))
</script>

<template>
  <div>
    <p class="mb-1 text-xs font-medium text-muted-foreground">{{ label }}</p>
    <svg :viewBox="`0 0 ${W} ${H}`" class="w-full" preserveAspectRatio="none">
      <g v-for="t in yTicks" :key="t.v">
        <line :x1="PAD_L" :x2="W - PAD_R" :y1="t.y" :y2="t.y" stroke="var(--border)" stroke-opacity="0.4"
          stroke-width="1" vector-effect="non-scaling-stroke" />
        <text :x="PAD_L - 4" :y="t.y + 3" text-anchor="end" class="fill-muted-foreground" style="font-size: 9px">
          {{ format(t.v) }}
        </text>
      </g>
      <line :x1="PAD_L" :x2="PAD_L" :y1="PAD_T" :y2="H - PAD_B" stroke="var(--border)" stroke-width="1" vector-effect="non-scaling-stroke" />
      <line :x1="PAD_L" :x2="W - PAD_R" :y1="H - PAD_B" :y2="H - PAD_B" stroke="var(--border)" stroke-width="1" vector-effect="non-scaling-stroke" />
      <g v-for="(l, li) in lines" :key="li">
        <polyline v-if="l.pts.length > 1" :points="poly(l.pts)" fill="none" :stroke="l.color" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
        <circle v-for="(p, pi) in l.pts" :key="pi" :cx="px(p.x)" :cy="py(p.y)" r="3" :fill="l.color" />
      </g>
      <text v-for="x in xTicks" :key="x" :x="px(x)" :y="H - PAD_B + 12" text-anchor="middle" class="fill-muted-foreground" style="font-size: 9px">{{ x }}</text>
      <text :x="(PAD_L + W - PAD_R) / 2" :y="H - 2" text-anchor="middle" class="fill-muted-foreground" style="font-size: 9px">{{ displayXLabel }}</text>
    </svg>
  </div>
</template>
