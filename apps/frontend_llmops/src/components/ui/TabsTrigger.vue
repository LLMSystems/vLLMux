<script setup lang="ts">
import { TabsTrigger } from 'reka-ui'
import { cn } from '@/lib/utils'
const props = defineProps<{ value: string; class?: string }>()
</script>

<template>
  <TabsTrigger
    :value="value"
    :class="
      cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm',
        props.class,
      )
    "
  >
    <slot />
  </TabsTrigger>
</template>
