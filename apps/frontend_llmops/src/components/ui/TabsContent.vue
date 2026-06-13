<script setup lang="ts">
import { TabsContent } from 'reka-ui'
import { cn } from '@/lib/utils'
const props = defineProps<{ value: string; class?: string }>()
</script>

<template>
  <TabsContent
    :value="value"
    :class="cn('focus-visible:outline-none', props.class)"
  >
    <slot />
  </TabsContent>
</template>
