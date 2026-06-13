<script setup lang="ts">
import { cn } from '@/lib/utils'
const props = defineProps<{ class?: string }>()
</script>

<template>
  <div :class="cn('px-5 pb-5', props.class)"><slot /></div>
</template>
