<script setup lang="ts">
import { computed } from 'vue'
import { Download, Eye, Gauge, GraduationCap, Loader2, Trash2 } from '@lucide/vue'
import { formatBytes } from '@/lib/utils'
import type { DatasetDownloadJob, DatasetEntry } from '@/types/api'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'

const props = defineProps<{ entry: DatasetEntry; job?: DatasetDownloadJob }>()
defineEmits<{ download: []; remove: []; preview: [] }>()

const downloading = computed(
  () => !!props.job && (props.job.state === 'pending' || props.job.state === 'downloading'),
)
// Post-download evalscope cache warm still running — preview isn't fast yet.
const warming = computed(() => !!props.job && props.job.state === 'completed' && !!props.job.warming)
const subtitle = computed(() =>
  props.entry.file ? `${props.entry.dataset_id}/${props.entry.file}` : props.entry.dataset_id,
)
const size = computed(() =>
  props.entry.cached ? formatBytes(props.entry.size_on_disk) : (props.entry.approx ?? '—'),
)
const accent = computed(() => (props.entry.category === 'perf' ? 'var(--chart-2)' : 'var(--chart-1)'))
</script>

<template>
  <div
    class="group relative flex flex-col rounded-xl border border-border/60 bg-card p-4 transition hover:border-border hover:shadow-sm"
    :class="entry.cached ? '' : 'opacity-95'"
  >
    <div class="flex items-start gap-3">
      <div class="grid size-9 shrink-0 place-items-center rounded-lg bg-muted" :style="{ color: accent }">
        <component :is="entry.category === 'perf' ? Gauge : GraduationCap" class="size-4.5" />
      </div>
      <div class="min-w-0 flex-1">
        <p class="truncate text-sm font-medium" :title="entry.label">{{ entry.label }}</p>
        <p class="truncate font-mono text-xs text-muted-foreground" :title="subtitle">{{ subtitle }}</p>
      </div>
    </div>

    <div class="mt-2.5 flex flex-wrap gap-1.5">
      <Badge v-if="entry.cached" variant="ready" class="text-[10px]">{{ $t('datasetCard.cached') }}</Badge>
      <Badge v-if="entry.tier" variant="outline" class="text-[10px]">{{ entry.tier }}</Badge>
      <Badge v-if="entry.metric" variant="muted" class="font-mono text-[10px]">{{ entry.metric }}</Badge>
      <Badge v-if="entry.note" variant="secondary" class="text-[10px]">{{ entry.note }}</Badge>
    </div>

    <div class="mt-3 flex items-center justify-between gap-2 border-t border-border/40 pt-2.5">
      <span class="tabular text-xs text-muted-foreground">{{ size }}</span>
      <div class="flex justify-end">
        <span v-if="downloading" class="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Loader2 class="size-3.5 animate-spin" />
          <span class="tabular">{{ formatBytes(job!.downloaded_bytes) }}</span>
        </span>
        <span v-else-if="warming" class="flex items-center gap-1.5 text-xs text-muted-foreground" :title="$t('datasetCard.warmingHint')">
          <Loader2 class="size-3.5 animate-spin" />{{ $t('datasetCard.warming') }}
        </span>
        <template v-else-if="entry.cached">
          <Button
            v-if="entry.category === 'eval'"
            size="icon-sm"
            variant="ghost"
            :title="$t('datasetCard.preview')"
            @click="$emit('preview')"
          >
            <Eye class="size-4" />
          </Button>
          <Button size="icon-sm" variant="ghost" :title="$t('datasetCard.deleteCache')" @click="$emit('remove')">
            <Trash2 class="size-4" />
          </Button>
        </template>
        <Button v-else size="sm" variant="outline" @click="$emit('download')">
          <Download class="size-3.5" />{{ $t('common.download') }}
        </Button>
      </div>
    </div>
  </div>
</template>
