<script setup lang="ts">
import { cn } from '@/lib/utils'
const props = defineProps<{ class?: string }>()
</script>

<template>
  <div :class="cn('flex flex-col gap-1 px-5 pt-5 pb-3', props.class)"><slot /></div>
</template>
