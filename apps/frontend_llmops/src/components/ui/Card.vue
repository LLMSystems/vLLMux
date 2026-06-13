<script setup lang="ts">
import { cn } from '@/lib/utils'
const props = defineProps<{ class?: string; glass?: boolean }>()
</script>

<template>
  <div
    :class="
      cn(
        'rounded-xl border border-border/70 text-card-foreground shadow-sm',
        glass ? 'glass' : 'bg-card',
        props.class,
      )
    "
  >
    <slot />
  </div>
</template>
