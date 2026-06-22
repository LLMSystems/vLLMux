<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Boxes, Cpu, Download, HardDrive, Layers, Loader2, Trash2 } from '@lucide/vue'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatBytes } from '@/lib/utils'
import type { LoraDownloadJob, LoraLibraryInfo } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import StatCard from '@/components/StatCard.vue'

const { t } = useI18n()
const { ensureUnlocked } = useAuth()

const lib = ref<LoraLibraryInfo | null>(null)
const downloads = ref<LoraDownloadJob[]>([])
const repoInput = ref('')
const nameInput = ref('')
const starting = ref(false)
let poll: ReturnType<typeof setInterval> | null = null

const diskPct = computed(() => {
  const d = lib.value?.disk
  return d && d.total ? (d.used / d.total) * 100 : 0
})
const activeDownloads = computed(() =>
  downloads.value.filter((d) => d.state === 'pending' || d.state === 'downloading'),
)
const totalSize = computed(() => (lib.value?.adapters ?? []).reduce((s, a) => s + a.size_on_disk, 0))

function pct(job: LoraDownloadJob): number | null {
  if (!job.total_bytes) return null
  return Math.min(100, (job.downloaded_bytes / job.total_bytes) * 100)
}

async function loadLib() {
  try {
    lib.value = await api.listLora()
  } catch (e) {
    toast.error(t('loraLibrary.loadFailed'), { description: String(e) })
  }
}

async function loadDownloads() {
  try {
    const prevActive = activeDownloads.value.length
    downloads.value = await api.listLoraDownloads()
    if (prevActive > 0 && activeDownloads.value.length < prevActive) await loadLib()
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
    await api.startLoraDownload(repo, nameInput.value.trim() || undefined)
    repoInput.value = ''
    nameInput.value = ''
    toast.success(t('loraLibrary.downloadStarted', { repo }), {
      description: t('loraLibrary.downloadStartedDesc'),
    })
    await loadDownloads()
  } catch (e) {
    toast.error(t('loraLibrary.downloadFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    starting.value = false
  }
}

async function remove(name: string) {
  if (!(await ensureUnlocked())) return
  if (!confirm(t('loraLibrary.deleteConfirm', { name }))) return
  try {
    await api.deleteLora(name)
    toast.success(t('loraLibrary.deleted', { name }))
    await loadLib()
  } catch (e) {
    toast.error(t('loraLibrary.deleteFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  }
}

onMounted(() => {
  void loadLib()
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
      <h1 class="flex items-center gap-2 text-lg font-semibold"><Layers class="size-5" />{{ $t('loraLibrary.title') }}</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        {{ $t('loraLibrary.description') }}
        <span class="font-mono">{{ lib?.root ?? $t('sidebar.loraLibrary') }}</span>
        {{ $t('loraLibrary.descriptionEnd') }}
      </p>
    </div>

    <!-- Stats -->
    <div v-if="lib" class="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCard :icon="Layers" :label="$t('loraLibrary.adapters')" :value="String(lib.adapters.length)" />
      <StatCard :icon="HardDrive" :label="$t('loraLibrary.diskUsed')" :value="formatBytes(totalSize)" />
      <StatCard :icon="Download" :label="$t('library.downloading')" :value="String(activeDownloads.length)" />
      <StatCard
        :icon="HardDrive"
        :label="$t('loraLibrary.diskRemaining')"
        :value="formatBytes(lib.disk.free)"
        :hint="$t('library.diskUsed') + ' ' + Math.round(diskPct) + '% / ' + formatBytes(lib.disk.total)"
        color="var(--chart-1)"
      />
    </div>

    <!-- Download -->
    <Card class="p-5">
      <p class="mb-3 text-sm font-semibold">{{ $t('loraLibrary.downloadAdapter') }}</p>
      <div class="flex items-end gap-2">
        <label class="flex-1">
          <span class="text-xs text-muted-foreground">{{ $t('loraLibrary.repoId') }}</span>
          <Input
            v-model="repoInput"
            :placeholder="$t('loraLibrary.repoPlaceholder')"
            class="mt-1 font-mono"
            @keydown.enter="startDownload"
          />
        </label>
        <label class="w-44">
          <span class="text-xs text-muted-foreground">{{ $t('loraLibrary.localName') }}</span>
          <Input
            v-model="nameInput"
            :placeholder="$t('loraLibrary.localNamePlaceholder')"
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
        <div v-for="job in downloads" :key="job.name" class="rounded-lg border border-border/60 bg-background/40 p-3">
          <div class="flex items-center justify-between gap-3 text-sm">
            <span class="truncate font-mono text-xs">{{ job.name }} <span class="text-muted-foreground">← {{ job.repo_id }}</span></span>
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

    <!-- Adapters — card grid -->
    <div>
      <p class="mb-2 text-sm font-semibold">{{ $t('loraLibrary.localAdapters') }}</p>
      <div v-if="lib?.adapters.length" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <div
          v-for="a in lib.adapters"
          :key="a.name"
          class="group relative rounded-xl border border-border/60 bg-card p-4 transition hover:border-border hover:shadow-sm"
        >
          <div class="flex items-start gap-3">
            <div class="grid size-10 shrink-0 place-items-center rounded-lg bg-muted text-[var(--chart-3)]">
              <Layers class="size-5" />
            </div>
            <div class="min-w-0 flex-1">
              <p class="truncate font-medium" :title="a.name">{{ a.name }}</p>
              <p class="flex items-center gap-1 truncate font-mono text-xs text-muted-foreground" :title="a.base_model ?? ''">
                <Cpu class="size-3 shrink-0" />{{ a.base_model ?? $t('loraLibrary.unknownBase') }}
              </p>
            </div>
            <Button
              size="icon-sm"
              variant="ghost"
              class="opacity-0 transition group-hover:opacity-100"
              :title="$t('common.delete')"
              @click="remove(a.name)"
            >
              <Trash2 class="size-4" />
            </Button>
          </div>

          <div class="mt-3 flex flex-wrap gap-1.5">
            <Badge v-if="a.rank != null" variant="outline" class="text-[10px]">rank {{ a.rank }}</Badge>
            <Badge v-if="a.alpha != null" variant="muted" class="text-[10px]">α {{ a.alpha }}</Badge>
            <Badge v-for="t in a.target_modules.slice(0, 3)" :key="t" variant="muted" class="font-mono text-[10px]">
              <Boxes class="size-3" />{{ t }}
            </Badge>
            <Badge v-if="a.target_modules.length > 3" variant="muted" class="text-[10px]">+{{ a.target_modules.length - 3 }}</Badge>
          </div>

          <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
            <div>
              <dt class="text-muted-foreground">{{ $t('library.size') }}</dt>
              <dd class="tabular font-medium">{{ formatBytes(a.size_on_disk) }}</dd>
            </div>
            <div class="min-w-0">
              <dt class="text-muted-foreground">{{ $t('common.path') }}</dt>
              <dd class="truncate font-mono" :title="a.path">{{ a.path }}</dd>
            </div>
          </dl>
        </div>
      </div>
      <Card v-else class="p-10 text-center text-sm text-muted-foreground">
        {{ $t('loraLibrary.noAdapters') }} <span class="font-mono">{{ lib?.root }}</span>。
      </Card>
    </div>
  </div>
</template>
