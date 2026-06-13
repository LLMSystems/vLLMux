<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
import { badgeVariants, type BadgeVariants } from './variants'

const props = defineProps<{ variant?: BadgeVariants['variant']; class?: string }>()
const classes = computed(() => cn(badgeVariants({ variant: props.variant }), props.class))
</script>

<template>
  <span :class="classes"><slot /></span>
</template>
