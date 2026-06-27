<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
import type { ModelState } from '@/types/api'

const props = withDefaults(defineProps<{ state: ModelState; size?: 'sm' | 'md' | 'lg' }>(), {
  size: 'md',
})

const colorClass: Record<ModelState, string> = {
  ready: 'text-status-ready',
  starting: 'text-status-starting',
  stopping: 'text-status-stopping',
  failed: 'text-status-failed',
  stopped: 'text-status-stopped',
  sleeping: 'text-status-sleeping',
}
const transient = computed(() => props.state === 'starting' || props.state === 'stopping')
const sizeClass = computed(
  () => ({ sm: 'size-1.5', md: 'size-2', lg: 'size-2.5' })[props.size],
)
</script>

<template>
  <span
    :class="
      cn(
        'inline-block shrink-0 rounded-full bg-current',
        sizeClass,
        colorClass[state],
        transient && 'animate-status-pulse',
      )
    "
  />
</template>
