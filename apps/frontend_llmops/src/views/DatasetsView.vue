<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { Database, Download, HardDrive, Loader2, Trash2 } from '@lucide/vue'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatBytes } from '@/lib/utils'
import type { DatasetCacheInfo, DatasetDownloadJob, DatasetEntry } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'

const { ensureUnlocked } = useAuth()

const cache = ref<DatasetCacheInfo | null>(null)
const downloads = ref<DatasetDownloadJob[]>([])
let poll: ReturnType<typeof setInterval> | null = null

const diskPct = computed(() => {
  const d = cache.value?.disk
  return d && d.total ? (d.used / d.total) * 100 : 0
})
const jobsByKey = computed(() => Object.fromEntries(downloads.value.map((j) => [j.key, j])))
const activeDownloads = computed(() =>
  downloads.value.filter((d) => d.state === 'pending' || d.state === 'downloading'),
)

const perfDatasets = computed(() => (cache.value?.datasets ?? []).filter((d) => d.category === 'perf'))
// Eval datasets grouped by capability tier, preserving catalog order.
const evalTiers = computed(() => {
  const groups: { tier: string; items: DatasetEntry[] }[] = []
  for (const d of cache.value?.datasets ?? []) {
    if (d.category !== 'eval') continue
    const tier = d.tier ?? '其他'
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
    toast.error('無法讀取資料集', { description: String(e) })
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
    toast.success('開始下載', { description: '可離開此頁，下載會在背景繼續。' })
    await loadDownloads()
  } catch (e) {
    toast.error('無法開始下載', { description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e) })
  }
}

async function remove(key: string, label: string) {
  if (!(await ensureUnlocked())) return
  if (!confirm(`確定刪除已快取的「${label}」？此操作會釋放磁碟空間。`)) return
  try {
    await api.deleteDataset(key)
    toast.success(`已刪除 ${label}`)
    await loadCache()
  } catch (e) {
    toast.error('刪除失敗', { description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e) })
  }
}

function isDownloading(key: string): boolean {
  const j = jobsByKey.value[key]
  return !!j && (j.state === 'pending' || j.state === 'downloading')
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
      <h1 class="flex items-center gap-2 text-lg font-semibold"><Database class="size-5" />資料集庫</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        預先下載資料集到共用 ModelScope 快取，跑壓測 / 評測時就不必等首次下載。
        <span class="font-mono">random</span> / 速度基準為即時生成，無需下載。
      </p>
    </div>

    <!-- Disk -->
    <Card v-if="cache" class="p-5">
      <div class="mb-2 flex items-center justify-between text-sm">
        <span class="flex items-center gap-2 font-medium"><HardDrive class="size-4" />快取磁碟</span>
        <span class="tabular text-muted-foreground">
          已用 {{ formatBytes(cache.disk.used) }} / {{ formatBytes(cache.disk.total) }}
          · 剩餘 <span class="font-medium text-foreground">{{ formatBytes(cache.disk.free) }}</span>
        </span>
      </div>
      <div class="h-2 overflow-hidden rounded-full bg-muted">
        <div
          class="h-full rounded-full"
          :class="diskPct > 90 ? 'bg-status-failed' : 'bg-[var(--chart-1)]'"
          :style="{ width: `${diskPct}%` }"
        />
      </div>
    </Card>

    <!-- Perf (load-test) datasets -->
    <Card class="overflow-hidden">
      <div class="flex items-center justify-between border-b border-border/60 px-5 py-3">
        <span class="text-sm font-semibold">壓測資料集</span>
        <span class="text-xs text-muted-foreground">下載後可在「壓測」頁選用</span>
      </div>
      <div v-if="perfDatasets.length" class="divide-y divide-border/60">
        <div v-for="d in perfDatasets" :key="d.key" class="flex items-center gap-4 px-5 py-3">
          <div class="min-w-0 flex-1">
            <p class="flex items-center gap-2 text-sm font-medium">
              {{ d.label }}
              <Badge v-if="d.cached" variant="default">已快取</Badge>
            </p>
            <p class="truncate text-xs text-muted-foreground">
              <span class="font-mono">{{ d.dataset_id }}/{{ d.file }}</span> · {{ d.note }}
            </p>
          </div>
          <span class="shrink-0 tabular text-sm text-muted-foreground">
            {{ d.cached ? formatBytes(d.size_on_disk) : d.approx }}
          </span>
          <div class="flex w-24 shrink-0 justify-end">
            <Button v-if="isDownloading(d.key)" size="sm" variant="ghost" disabled>
              <Loader2 class="size-3.5 animate-spin" />
              <span class="tabular">{{ formatBytes(jobsByKey[d.key]!.downloaded_bytes) }}</span>
            </Button>
            <Button v-else-if="d.cached" size="icon-sm" variant="ghost" title="刪除快取" @click="remove(d.key, d.label)">
              <Trash2 class="size-4" />
            </Button>
            <Button v-else size="sm" variant="outline" @click="download(d.key)">
              <Download class="size-3.5" />下載
            </Button>
          </div>
        </div>
      </div>
    </Card>

    <!-- Eval (accuracy) datasets, grouped by capability tier -->
    <Card class="overflow-hidden">
      <div class="flex items-center justify-between border-b border-border/60 px-5 py-3">
        <span class="text-sm font-semibold">評測資料集</span>
        <span class="text-xs text-muted-foreground">下載後可在「評測」頁選用</span>
      </div>
      <div v-for="g in evalTiers" :key="g.tier">
        <div class="bg-muted/40 px-5 py-1.5 text-xs font-medium text-muted-foreground">{{ g.tier }}</div>
        <div class="divide-y divide-border/60">
          <div v-for="d in g.items" :key="d.key" class="flex items-center gap-4 px-5 py-3">
            <div class="min-w-0 flex-1">
              <p class="flex items-center gap-2 text-sm font-medium">
                {{ d.label }}
                <Badge v-if="d.cached" variant="default">已快取</Badge>
                <Badge v-if="d.note" variant="secondary">{{ d.note }}</Badge>
              </p>
              <p class="truncate text-xs text-muted-foreground">
                <span class="font-mono">{{ d.dataset_id }}</span>
                <span v-if="d.metric"> · {{ d.metric }}</span>
              </p>
            </div>
            <span class="shrink-0 tabular text-sm text-muted-foreground">
              {{ d.cached ? formatBytes(d.size_on_disk) : '—' }}
            </span>
            <div class="flex w-24 shrink-0 justify-end">
              <Button v-if="isDownloading(d.key)" size="sm" variant="ghost" disabled>
                <Loader2 class="size-3.5 animate-spin" />
                <span class="tabular">{{ formatBytes(jobsByKey[d.key]!.downloaded_bytes) }}</span>
              </Button>
              <Button v-else-if="d.cached" size="icon-sm" variant="ghost" title="刪除快取" @click="remove(d.key, d.label)">
                <Trash2 class="size-4" />
              </Button>
              <Button v-else size="sm" variant="outline" @click="download(d.key)">
                <Download class="size-3.5" />下載
              </Button>
            </div>
          </div>
        </div>
      </div>
    </Card>

    <!-- Failed downloads -->
    <Card v-for="job in downloads.filter((j) => j.state === 'failed')" :key="job.key" class="p-3">
      <p class="text-xs text-status-failed">下載「{{ job.label }}」失敗：{{ job.error }}</p>
    </Card>
  </div>
</template>
