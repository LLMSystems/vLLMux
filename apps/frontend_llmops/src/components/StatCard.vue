<script setup lang="ts">
import { type Component } from 'vue'
import Card from '@/components/ui/Card.vue'
import Sparkline from '@/components/Sparkline.vue'

defineProps<{
  label: string
  value: string
  hint?: string
  icon?: Component
  spark?: number[]
  /** Accent color for icon + sparkline. */
  color?: string
}>()
</script>

<template>
  <Card glass class="relative overflow-hidden p-5">
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <p class="text-xs font-medium uppercase tracking-wide text-muted-foreground">{{ label }}</p>
        <p class="mt-1.5 text-2xl font-semibold tabular tracking-tight">{{ value }}</p>
        <p v-if="hint" class="mt-1 text-xs text-muted-foreground tabular">{{ hint }}</p>
      </div>
      <div
        v-if="icon"
        class="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-background/40"
        :style="color ? { color } : undefined"
      >
        <component :is="icon" class="size-4.5" />
      </div>
    </div>
    <div v-if="spark && spark.length > 1" class="mt-3 -mx-1" :style="color ? { color } : undefined">
      <Sparkline :data="spark" :width="260" :height="34" />
    </div>
  </Card>
</template>
