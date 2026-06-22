<script setup lang="ts">
import { computed } from 'vue'
import Card from '@/components/ui/Card.vue'
import { mibToGb, formatPercent } from '@/lib/utils'
import type { GpuInfo } from '@/types/api'

const props = defineProps<{ gpu: GpuInfo }>()

const memPct = computed(() =>
  props.gpu.memory_total ? (props.gpu.memory_used / props.gpu.memory_total) * 100 : 0,
)

// Color shifts green → amber → red as memory pressure rises.
const ringColor = computed(() => {
  const p = memPct.value
  if (p >= 90) return 'var(--status-failed)'
  if (p >= 70) return 'var(--status-starting)'
  return 'var(--chart-2)'
})

const R = 52
const C = 2 * Math.PI * R
const dash = computed(() => `${(memPct.value / 100) * C} ${C}`)
</script>

<template>
  <Card glass class="p-5">
    <div class="flex items-center gap-4">
      <div class="relative size-[128px] shrink-0">
        <svg viewBox="0 0 128 128" class="size-full -rotate-90">
          <circle cx="64" cy="64" :r="R" fill="none" stroke="var(--muted)" stroke-width="9" />
          <circle
            cx="64"
            cy="64"
            :r="R"
            fill="none"
            :stroke="ringColor"
            stroke-width="9"
            stroke-linecap="round"
            :stroke-dasharray="dash"
            class="transition-all duration-500"
          />
        </svg>
        <div class="absolute inset-0 flex flex-col items-center justify-center">
          <span class="text-2xl font-semibold tabular leading-none">{{ formatPercent(memPct) }}</span>
          <span class="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">{{ $t('gpuGauge.vram') }}</span>
        </div>
      </div>
      <div class="min-w-0 flex-1 space-y-2">
        <div>
          <p class="text-xs text-muted-foreground">{{ $t('statusBar.gpu') }} {{ gpu.index }}</p>
          <p class="truncate text-sm font-medium" :title="gpu.name">{{ gpu.name }}</p>
        </div>
        <dl class="space-y-1 text-xs">
          <div class="flex justify-between">
            <dt class="text-muted-foreground">{{ $t('gpuGauge.memory') }}</dt>
            <dd class="tabular">{{ mibToGb(gpu.memory_used) }} / {{ mibToGb(gpu.memory_total) }}</dd>
          </div>
          <div class="flex justify-between">
            <dt class="text-muted-foreground">{{ $t('gpuGauge.util') }}</dt>
            <dd class="tabular font-medium">{{ formatPercent(gpu.gpu_util) }}</dd>
          </div>
        </dl>
        <div class="h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            class="h-full rounded-full bg-[var(--chart-1)] transition-all duration-500"
            :style="{ width: `${Math.min(100, gpu.gpu_util)}%` }"
          />
        </div>
      </div>
    </div>
  </Card>
</template>
