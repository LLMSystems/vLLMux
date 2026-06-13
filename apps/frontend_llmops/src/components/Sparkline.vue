<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    data: number[]
    width?: number
    height?: number
    /** CSS color (e.g. var(--chart-1) or currentColor). */
    color?: string
    fill?: boolean
    /** Fixed max for normalization; defaults to data max. */
    max?: number
  }>(),
  { width: 120, height: 36, color: 'currentColor', fill: true },
)

const gid = `spark-${Math.random().toString(36).slice(2, 9)}`

const points = computed(() => {
  const d = props.data
  if (d.length < 2) return ''
  const max = props.max ?? Math.max(...d, 1)
  const min = Math.min(...d, 0)
  const range = max - min || 1
  const stepX = props.width / (d.length - 1)
  return d
    .map((v, i) => {
      const x = i * stepX
      const y = props.height - ((v - min) / range) * (props.height - 2) - 1
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})

const areaPath = computed(() => {
  if (!points.value) return ''
  return `M0,${props.height} L${points.value.replaceAll(' ', ' L')} L${props.width},${props.height} Z`
})
</script>

<template>
  <svg
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    preserveAspectRatio="none"
    class="overflow-visible"
  >
    <defs>
      <linearGradient :id="gid" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" :stop-color="color" stop-opacity="0.28" />
        <stop offset="100%" :stop-color="color" stop-opacity="0" />
      </linearGradient>
    </defs>
    <path v-if="fill && areaPath" :d="areaPath" :fill="`url(#${gid})`" />
    <polyline
      v-if="points"
      :points="points"
      fill="none"
      :stroke="color"
      stroke-width="1.75"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
</template>
