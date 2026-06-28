<script setup lang="ts">
/**
 * Deterministic user avatar — DiceBear `notionists` (black-line illustration that
 * suits the control-room UI). The seed is the operator label, so the same person
 * always gets the same face; generated offline, no upload, no network.
 */
import { computed } from 'vue'
import { createAvatar } from '@dicebear/core'
import { notionists } from '@dicebear/collection'

const props = withDefaults(
  defineProps<{ seed: string; size?: number }>(),
  { size: 32 },
)

// Soft backgrounds that read on both themes; chosen deterministically by seed.
const svg = computed(() =>
  createAvatar(notionists, {
    seed: props.seed || 'anonymous',
    radius: 50,
    backgroundColor: ['e6edf3', 'c0aede', 'b6e3f4', 'ffd5dc', 'ffdfbf'],
  }).toString(),
)
</script>

<template>
  <span
    class="inline-block shrink-0 overflow-hidden rounded-full ring-1 ring-border/60"
    :style="{ width: `${size}px`, height: `${size}px` }"
    :title="seed"
    v-html="svg"
  />
</template>
