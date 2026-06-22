<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Database, Download, HardDrive, Layers } from '@lucide/vue'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatBytes } from '@/lib/utils'
import type { DatasetCacheInfo, DatasetDownloadJob, DatasetEntry } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import StatCard from '@/components/StatCard.vue'
import DatasetCard from '@/components/DatasetCard.vue'
import DatasetPreviewDialog from '@/components/DatasetPreviewDialog.vue'

const { t } = useI18n()
const { ensureUnlocked } = useAuth()

// Dataset preview (eval datasets only) — reuses the Eval page's inspector dialog.
const previewOpen = ref(false)
const previewKey = ref('')
function openPreview(key: string) {
  previewKey.value = key
  previewOpen.value = true
}

const cache = ref<DatasetCacheInfo | null>(null)
const downloads = ref<DatasetDownloadJob[]>([])
let poll: ReturnType<typeof setInterval> | null = null

const jobsByKey = computed(() => Object.fromEntries(downloads.value.map((j) => [j.key, j])))
const activeDownloads = computed(() =>
  downloads.value.filter((d) => d.state === 'pending' || d.state === 'downloading'),
)

const allDatasets = computed(() => cache.value?.datasets ?? [])
const cachedCount = computed(() => allDatasets.value.filter((d) => d.cached).length)
const cachedSize = computed(() =>
  allDatasets.value.filter((d) => d.cached).reduce((s, d) => s + d.size_on_disk, 0),
)
const perfDatasets = computed(() => allDatasets.value.filter((d) => d.category === 'perf'))
// Eval datasets grouped by capability tier, preserving catalog order.
const evalTiers = computed(() => {
  const groups: { tier: string; items: DatasetEntry[] }[] = []
  for (const d of allDatasets.value) {
    if (d.category !== 'eval') continue
    const tier = d.tier ?? t('common.more')
    let g = groups.find((x) => x.tier === tier)
    if (!g) groups.push((g = { tier, items: [] }))
    g.items.push(d)
  }
  return groups
})

async function loadCache() {
  try {
    cache.value = await api.getDatasets()
  } catch (e) {
    toast.error(t('datasets.loadFailed'), { description: String(e) })
  }
}

async function loadDownloads() {
  try {
    const prevActive = activeDownloads.value.length
    downloads.value = await api.listDatasetDownloads()
    if (prevActive > 0 && activeDownloads.value.length < prevActive) await loadCache()
  } catch {
    /* transient */
  }
}

async function download(key: string) {
  if (!(await ensureUnlocked())) return
  try {
    await api.startDatasetDownload(key)
    toast.success(t('datasets.downloadStarted'), {
      description: t('datasets.downloadStartedDesc'),
    })
    await loadDownloads()
  } catch (e) {
    toast.error(t('datasets.downloadStartFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  }
}

async function remove(key: string, label: string) {
  if (!(await ensureUnlocked())) return
  if (!confirm(t('datasets.deleteConfirm', { label }))) return
  try {
    await api.deleteDataset(key)
    toast.success(t('datasets.deleted', { label }))
    await loadCache()
  } catch (e) {
    toast.error(t('datasets.deleteFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  }
}

onMounted(() => {
  void loadCache()
  void loadDownloads()
  poll = setInterval(loadDownloads, 1500)
})
onBeforeUnmount(() => {
  if (poll) clearInterval(poll)
})
</script>

<template>
  <div class="space-y-6 p-6">
    <div>
      <h1 class="flex items-center gap-2 text-lg font-semibold"><Database class="size-5" />{{ $t('datasets.title') }}</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        {{ $t('datasets.description') }}
        <span class="font-mono">random</span> {{ $t('datasets.descriptionRandom') }}
      </p>
    </div>

    <!-- Stats -->
    <div v-if="cache" class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCard :icon="Database" :label="$t('datasets.cachedTotal')" :value="`${cachedCount} / ${allDatasets.length}`" />
      <StatCard :icon="HardDrive" :label="$t('datasets.diskUsed')" :value="formatBytes(cachedSize)" />
      <StatCard :icon="Download" :label="$t('library.downloading')" :value="String(activeDownloads.length)" />
      <StatCard
        :icon="HardDrive"
        :label="$t('datasets.diskRemaining')"
        :value="formatBytes(cache.disk.free)"
        :hint="$t('datasets.diskTotal', { size: formatBytes(cache.disk.total) })"
        color="var(--chart-1)"
      />
    </div>

    <!-- Perf (load-test) datasets -->
    <div>
      <div class="mb-2 flex items-center justify-between">
        <p class="flex items-center gap-2 text-sm font-semibold"><Layers class="size-4" />{{ $t('datasets.perfDatasets') }}</p>
        <span class="text-xs text-muted-foreground">{{ $t('datasets.perfHint') }}</span>
      </div>
      <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <DatasetCard
          v-for="d in perfDatasets"
          :key="d.key"
          :entry="d"
          :job="jobsByKey[d.key]"
          @download="download(d.key)"
          @remove="remove(d.key, d.label)"
        />
      </div>
    </div>

    <!-- Eval (accuracy) datasets, grouped by capability tier -->
    <div>
      <div class="mb-2 flex items-center justify-between">
        <p class="flex items-center gap-2 text-sm font-semibold"><Layers class="size-4" />{{ $t('datasets.evalDatasets') }}</p>
        <span class="text-xs text-muted-foreground">{{ $t('datasets.evalHint') }}</span>
      </div>
      <div class="space-y-4">
        <div v-for="g in evalTiers" :key="g.tier">
          <p class="mb-1.5 text-xs font-medium text-muted-foreground">{{ g.tier }}</p>
          <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <DatasetCard
              v-for="d in g.items"
              :key="d.key"
              :entry="d"
              :job="jobsByKey[d.key]"
              @download="download(d.key)"
              @remove="remove(d.key, d.label)"
              @preview="openPreview(d.key)"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- Failed downloads -->
    <Card v-for="job in downloads.filter((j) => j.state === 'failed')" :key="job.key" class="p-3">
      <p class="text-xs text-status-failed">{{ $t('datasets.downloadFailed', { label: job.label }) }}{{ job.error }}</p>
    </Card>

    <DatasetPreviewDialog v-model:open="previewOpen" :dataset-key="previewKey" />
  </div>
</template>
