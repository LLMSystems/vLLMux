<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowDownUp, Boxes, Brain, Clock, Download, Files, HardDrive, Loader2, Package, Trash2 } from '@lucide/vue'
import type { Component } from 'vue'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatBytes, timeAgo } from '@/lib/utils'
import type { CacheInfo, CachedModel, DownloadJob } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import StatCard from '@/components/StatCard.vue'

const { t } = useI18n()
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
const totalSize = computed(() => (cache.value?.models ?? []).reduce((s, m) => s + m.size_on_disk, 0))

/** Best-effort model family from the repo id, for the card icon + tag. */
function kindMeta(repoId: string): { label: string; icon: Component; color: string } {
  const r = repoId.toLowerCase()
  if (/rerank/.test(r)) return { label: t('common.reranking'), icon: ArrowDownUp, color: 'var(--chart-4)' }
  if (/embed|bge|m3e|gte|e5|sentence/.test(r)) return { label: t('common.embedding'), icon: Boxes, color: 'var(--chart-2)' }
  return { label: t('models.llm'), icon: Brain, color: 'var(--chart-1)' }
}
function repoLeaf(repoId: string) {
  return repoId.split('/').pop() ?? repoId
}
function repoOrg(repoId: string) {
  return repoId.includes('/') ? repoId.split('/')[0] : ''
}

function pct(job: DownloadJob): number | null {
  if (!job.total_bytes) return null
  return Math.min(100, (job.downloaded_bytes / job.total_bytes) * 100)
}

async function loadCache() {
  try {
    cache.value = await api.getCache()
  } catch (e) {
    toast.error(t('library.loadCacheFailed'), { description: String(e) })
  }
}

async function loadDownloads() {
  try {
    const prevActive = activeDownloads.value.length
    downloads.value = await api.listDownloads()
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
    toast.success(t('library.downloadStarted', { repo }), {
      description: t('library.downloadStartedDesc'),
    })
    await loadDownloads()
  } catch (e) {
    toast.error(t('library.downloadFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    starting.value = false
  }
}

async function remove(model: CachedModel) {
  if (!(await ensureUnlocked())) return
  if (!confirm(t('library.deleteConfirm', { repo: model.repo_id }))) return
  try {
    await api.deleteCache(model.repo_id)
    toast.success(t('library.deleted', { repo: model.repo_id }))
    await loadCache()
  } catch (e) {
    toast.error(t('library.deleteFailed'), {
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
      <h1 class="flex items-center gap-2 text-lg font-semibold"><Package class="size-5" />{{ $t('library.title') }}</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        {{ $t('library.description') }} <span class="font-mono">HF_TOKEN</span> {{ $t('library.descriptionEnd') }}
      </p>
    </div>

    <!-- Stats -->
    <div v-if="cache" class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCard :icon="Package" :label="$t('library.cachedModels')" :value="String(cache.models.length)" />
      <StatCard :icon="HardDrive" :label="$t('library.occupiedSpace')" :value="formatBytes(totalSize)" />
      <StatCard :icon="Download" :label="$t('library.downloading')" :value="String(activeDownloads.length)" />
      <StatCard
        :icon="HardDrive"
        :label="$t('library.diskRemaining')"
        :value="formatBytes(cache.disk.free)"
        :hint="$t('library.diskUsed') + ' ' + Math.round(diskPct) + '% / ' + formatBytes(cache.disk.total)"
        color="var(--chart-1)"
      />
    </div>

    <!-- Download -->
    <Card class="p-5">
      <p class="mb-3 text-sm font-semibold">{{ $t('library.downloadNew') }}</p>
      <div class="flex items-end gap-2">
        <label class="flex-1">
          <span class="text-xs text-muted-foreground">{{ $t('library.repoId') }}</span>
          <Input
            v-model="repoInput"
            :placeholder="$t('library.repoPlaceholder')"
            class="mt-1 font-mono"
            @keydown.enter="startDownload"
          />
        </label>
        <Button :disabled="!repoInput.trim() || starting" @click="startDownload">
          <Loader2 v-if="starting" class="size-4 animate-spin" /><Download v-else class="size-4" />{{ $t('common.download') }}
        </Button>
      </div>

      <!-- Active progress -->
      <div v-if="downloads.length" class="mt-4 grid gap-3 sm:grid-cols-2">
        <div v-for="job in downloads" :key="job.repo_id" class="rounded-lg border border-border/60 bg-background/40 p-3">
          <div class="flex items-center justify-between gap-3 text-sm">
            <span class="truncate font-mono text-xs">{{ job.repo_id }}</span>
            <span class="flex shrink-0 items-center gap-2">
              <Badge v-if="job.state === 'completed'" variant="ready">{{ $t('library.downloadComplete') }}</Badge>
              <Badge v-else-if="job.state === 'failed'" variant="failed">{{ $t('library.downloadFailedBadge') }}</Badge>
              <span v-else class="flex items-center gap-1.5 text-muted-foreground">
                <Loader2 class="size-3.5 animate-spin" />
                {{ pct(job) != null ? `${pct(job)!.toFixed(0)}%` : $t('library.downloading') + '…' }}
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
            <template v-if="job.state === 'failed'">{{ job.error }}</template>
            <template v-else>
              {{ formatBytes(job.downloaded_bytes) }}<template v-if="job.total_bytes"> / {{ formatBytes(job.total_bytes) }}</template>
            </template>
          </p>
        </div>
      </div>
    </Card>

    <!-- Cached models — card grid -->
    <div>
      <p class="mb-2 text-sm font-semibold">{{ $t('library.cachedModels') }}</p>
      <div v-if="cache?.models.length" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <div
          v-for="m in cache.models"
          :key="m.repo_id"
          class="group relative rounded-xl border border-border/60 bg-card p-4 transition hover:border-border hover:shadow-sm"
        >
          <div class="flex items-start gap-3">
            <div class="grid size-10 shrink-0 place-items-center rounded-lg bg-muted" :style="{ color: kindMeta(m.repo_id).color }">
              <component :is="kindMeta(m.repo_id).icon" class="size-5" />
            </div>
            <div class="min-w-0 flex-1">
              <p class="truncate font-medium" :title="m.repo_id">{{ repoLeaf(m.repo_id) }}</p>
              <p v-if="repoOrg(m.repo_id)" class="truncate font-mono text-xs text-muted-foreground">{{ repoOrg(m.repo_id) }}</p>
            </div>
            <Button
              size="icon-sm"
              variant="ghost"
              class="opacity-0 transition group-hover:opacity-100"
              :title="$t('library.deleteCache')"
              @click="remove(m)"
            >
              <Trash2 class="size-4" />
            </Button>
          </div>

          <div class="mt-3 flex flex-wrap gap-1.5">
            <Badge variant="outline" class="text-[10px]">{{ kindMeta(m.repo_id).label }}</Badge>
            <Badge variant="muted" class="text-[10px]"><Files class="size-3" />{{ m.nb_files }} {{ $t('library.files') }}</Badge>
          </div>

          <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
            <div>
              <dt class="text-muted-foreground">{{ $t('library.size') }}</dt>
              <dd class="tabular font-medium">{{ formatBytes(m.size_on_disk) }}</dd>
            </div>
            <div>
              <dt class="flex items-center gap-1 text-muted-foreground"><Clock class="size-3" />{{ $t('library.updated') }}</dt>
              <dd class="font-medium">{{ timeAgo(m.last_modified) }}</dd>
            </div>
          </dl>
        </div>
      </div>
      <Card v-else class="p-10 text-center text-sm text-muted-foreground">{{ $t('library.noCachedModels') }}</Card>
    </div>
  </div>
</template>
