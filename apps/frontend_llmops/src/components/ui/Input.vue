<script setup lang="ts">
import { cn } from '@/lib/utils'
const props = defineProps<{ class?: string }>()
const model = defineModel<string | number>()
</script>

<template>
  <input
    v-model="model"
    :class="
      cn(
        'flex h-9 w-full rounded-md border border-input bg-background/40 px-3 py-1 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50',
        props.class,
      )
    "
  />
</template>
