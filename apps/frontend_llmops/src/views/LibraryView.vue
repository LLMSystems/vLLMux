<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { Download, HardDrive, Loader2, Package, Trash2 } from '@lucide/vue'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatBytes, timeAgo } from '@/lib/utils'
import type { CacheInfo, DownloadJob } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'

const { ensureUnlocked } = useAuth()

const cache = ref<CacheInfo | null>(null)
const downloads = ref<DownloadJob[]>([])
const repoInput = ref('')
const starting = ref(false)
let poll: ReturnType<typeof setInterval> | null = null

const diskPct = computed(() => {
  const d = cache.value?.disk
  return d && d.total ? (d.used / d.total) * 100 : 0
})
const activeDownloads = computed(() =>
  downloads.value.filter((d) => d.state === 'pending' || d.state === 'downloading'),
)

function pct(job: DownloadJob): number | null {
  if (!job.total_bytes) return null
  return Math.min(100, (job.downloaded_bytes / job.total_bytes) * 100)
}

async function loadCache() {
  try {
    cache.value = await api.getCache()
  } catch (e) {
    toast.error('無法讀取快取', { description: String(e) })
  }
}

async function loadDownloads() {
  try {
    const prevActive = activeDownloads.value.length
    downloads.value = await api.listDownloads()
    // A download just finished -> refresh the cache list + free space.
    if (prevActive > 0 && activeDownloads.value.length < prevActive) await loadCache()
  } catch {
    /* transient — keep last good state */
  }
}

async function startDownload() {
  const repo = repoInput.value.trim()
  if (!repo || starting.value) return
  if (!(await ensureUnlocked())) return
  starting.value = true
  try {
    await api.startDownload(repo)
    repoInput.value = ''
    toast.success(`開始下載 ${repo}`, { description: '可離開此頁，下載會在背景繼續。' })
    await loadDownloads()
  } catch (e) {
    toast.error('無法開始下載', { description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e) })
  } finally {
    starting.value = false
  }
}

async function remove(repoId: string) {
  if (!(await ensureUnlocked())) return
  if (!confirm(`確定刪除已快取的 ${repoId}？此操作會釋放磁碟空間。`)) return
  try {
    await api.deleteCache(repoId)
    toast.success(`已刪除 ${repoId}`)
    await loadCache()
  } catch (e) {
    toast.error('刪除失敗', { description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e) })
  }
}

onMounted(() => {
  void loadCache()
  void loadDownloads()
  poll = setInterval(loadDownloads, 1500) // cheap; only meaningful while a job runs
})
onBeforeUnmount(() => {
  if (poll) clearInterval(poll)
})
</script>

<template>
  <div class="space-y-6 p-6">
    <div>
      <h1 class="flex items-center gap-2 text-lg font-semibold"><Package class="size-5" />模型庫</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        預先下載 Hugging Face 權重到共用快取，啟動模型時就不必等待。Gated 模型需在後端設定
        <span class="font-mono">HF_TOKEN</span>。
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

    <!-- Download -->
    <Card class="p-5">
      <p class="mb-3 text-sm font-semibold">下載新模型</p>
      <div class="flex items-end gap-2">
        <label class="flex-1">
          <span class="text-xs text-muted-foreground">Hugging Face repo id</span>
          <Input
            v-model="repoInput"
            placeholder="例如：Qwen/Qwen3-0.6B"
            class="mt-1 font-mono"
            @keydown.enter="startDownload"
          />
        </label>
        <Button :disabled="!repoInput.trim() || starting" @click="startDownload">
          <Loader2 v-if="starting" class="size-4 animate-spin" /><Download v-else class="size-4" />下載
        </Button>
      </div>

      <!-- Active progress -->
      <div v-if="downloads.length" class="mt-4 space-y-3">
        <div v-for="job in downloads" :key="job.repo_id" class="rounded-lg border border-border/60 bg-background/40 p-3">
          <div class="flex items-center justify-between gap-3 text-sm">
            <span class="truncate font-mono text-xs">{{ job.repo_id }}</span>
            <span class="flex shrink-0 items-center gap-2">
              <Badge v-if="job.state === 'completed'" variant="default">完成</Badge>
              <Badge v-else-if="job.state === 'failed'" variant="muted" class="text-status-failed">失敗</Badge>
              <span v-else class="flex items-center gap-1.5 text-muted-foreground">
                <Loader2 class="size-3.5 animate-spin" />
                {{ pct(job) != null ? `${pct(job)!.toFixed(0)}%` : '下載中…' }}
              </span>
            </span>
          </div>
          <div v-if="job.state === 'downloading' || job.state === 'pending'" class="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              class="h-full rounded-full bg-[var(--chart-2)] transition-[width] duration-700"
              :class="pct(job) == null ? 'animate-pulse w-1/3' : ''"
              :style="pct(job) != null ? { width: `${pct(job)}%` } : {}"
            />
          </div>
          <p class="mt-1.5 text-xs text-muted-foreground">
            <template v-if="job.state === 'failed'" >{{ job.error }}</template>
            <template v-else>
              {{ formatBytes(job.downloaded_bytes) }}<template v-if="job.total_bytes"> / {{ formatBytes(job.total_bytes) }}</template>
            </template>
          </p>
        </div>
      </div>
    </Card>

    <!-- Cached models -->
    <Card class="overflow-hidden">
      <div class="border-b border-border/60 px-5 py-3 text-sm font-semibold">已快取模型</div>
      <div v-if="cache?.models.length" class="divide-y divide-border/60">
        <div v-for="m in cache.models" :key="m.repo_id" class="flex items-center gap-4 px-5 py-3">
          <div class="min-w-0 flex-1">
            <p class="truncate font-mono text-sm">{{ m.repo_id }}</p>
            <p class="text-xs text-muted-foreground">
              {{ m.nb_files }} 個檔案 · 更新於 {{ timeAgo(m.last_modified) }}
            </p>
          </div>
          <span class="shrink-0 tabular text-sm font-medium">{{ formatBytes(m.size_on_disk) }}</span>
          <Button size="icon-sm" variant="ghost" title="刪除快取" @click="remove(m.repo_id)">
            <Trash2 class="size-4" />
          </Button>
        </div>
      </div>
      <p v-else class="px-5 py-10 text-center text-sm text-muted-foreground">快取中尚無模型。</p>
    </Card>
  </div>
</template>
