<script setup lang="ts">
import Badge from '@/components/ui/Badge.vue'
import StatusDot from '@/components/StatusDot.vue'
import type { ModelState } from '@/types/api'

defineProps<{ state: ModelState }>()

const label: Record<ModelState, string> = {
  ready: 'Ready',
  starting: 'Starting',
  stopping: 'Stopping',
  failed: 'Failed',
  stopped: 'Stopped',
}
</script>

<template>
  <Badge :variant="state" class="tabular">
    <StatusDot :state="state" size="sm" />
    {{ label[state] }}
  </Badge>
</template>
