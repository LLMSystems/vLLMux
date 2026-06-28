<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bell, Loader2, Lock, Plus, Send, ShieldAlert, Trash2 } from '@lucide/vue'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import type { AlertSink, Severity, SinkType } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'

const { t } = useI18n()
const { authEnabled, isAdmin, ensureUnlocked, refreshStatus } = useAuth()

const sinks = ref<AlertSink[]>([])
const loading = ref(false)
const locked = ref(false)
const denied = ref(false)
const newType = ref<SinkType>('slack')
const newUrl = ref('')
const newSeverity = ref<Severity>('error')
const creating = ref(false)
const testing = ref(false)

const sevVariant: Record<Severity, 'muted' | 'starting' | 'failed'> = {
  info: 'muted',
  warning: 'starting',
  error: 'failed',
  critical: 'failed',
}

async function load() {
  if (!(await ensureUnlocked())) {
    locked.value = true
    return
  }
  locked.value = false
  loading.value = true
  try {
    sinks.value = await api.listAlertSinks()
    denied.value = false
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) locked.value = true
    else if (e instanceof ApiError && e.status === 403) denied.value = true
    else toast.error(t('notifications.loadFailed'), { description: String(e) })
  } finally {
    loading.value = false
  }
}

async function create() {
  const url = newUrl.value.trim()
  if (!url || creating.value) return
  if (!(await ensureUnlocked())) return
  creating.value = true
  try {
    await api.createAlertSink(newType.value, url, newSeverity.value)
    newUrl.value = ''
    toast.success(t('notifications.added'))
    await load()
  } catch (e) {
    toast.error(t('notifications.addFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    creating.value = false
  }
}

async function remove(s: AlertSink) {
  if (s.id == null || !(await ensureUnlocked())) return
  try {
    await api.deleteAlertSink(s.id)
    toast.success(t('notifications.removed'))
    await load()
  } catch (e) {
    toast.error(t('notifications.removeFailed'), { description: String(e) })
  }
}

async function test(id?: number) {
  if (testing.value || !(await ensureUnlocked())) return
  testing.value = true
  try {
    const { results } = await api.testAlertSinks(id)
    const ok = results.filter((r) => r.ok).length
    const bad = results.length - ok
    if (bad === 0) toast.success(t('notifications.testOk', { n: ok }))
    else toast.error(t('notifications.testPartial', { ok, bad }))
  } catch (e) {
    toast.error(t('notifications.testFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    testing.value = false
  }
}

onMounted(async () => {
  await refreshStatus()
  await load()
})
</script>

<template>
  <div class="space-y-6 p-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="flex items-center gap-2 text-lg font-semibold"><Bell class="size-5" />{{ $t('notifications.title') }}</h1>
        <p class="mt-0.5 text-sm text-muted-foreground">{{ $t('notifications.description') }}</p>
      </div>
      <Button v-if="sinks.length" size="sm" variant="outline" :disabled="testing" @click="test()">
        <Loader2 v-if="testing" class="size-4 animate-spin" /><Send v-else class="size-4" />{{ $t('notifications.testAll') }}
      </Button>
    </div>

    <Card v-if="denied" class="flex flex-col items-center gap-3 p-10 text-center">
      <ShieldAlert class="size-8 text-status-failed" />
      <p class="text-sm text-muted-foreground">{{ $t('notifications.adminOnly') }}</p>
    </Card>

    <Card v-else-if="locked" class="flex flex-col items-center gap-3 p-10 text-center">
      <Lock class="size-8 text-muted-foreground" />
      <p class="text-sm text-muted-foreground">{{ $t('notifications.locked') }}</p>
      <Button size="sm" @click="load">{{ $t('notifications.unlock') }}</Button>
    </Card>

    <template v-else-if="isAdmin || authEnabled === false">
      <!-- Add -->
      <Card class="p-5">
        <p class="mb-3 text-sm font-semibold">{{ $t('notifications.addNew') }}</p>
        <div class="flex flex-wrap items-end gap-2">
          <label class="w-32">
            <span class="text-xs text-muted-foreground">{{ $t('notifications.type') }}</span>
            <select v-model="newType" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm text-foreground">
              <option value="slack">Slack</option>
              <option value="discord">Discord</option>
              <option value="webhook">Webhook</option>
            </select>
          </label>
          <label class="min-w-64 flex-1">
            <span class="text-xs text-muted-foreground">{{ $t('notifications.url') }}</span>
            <Input v-model="newUrl" :placeholder="$t('notifications.urlPlaceholder')" class="mt-1" @keydown.enter="create" />
          </label>
          <label class="w-36">
            <span class="text-xs text-muted-foreground">{{ $t('notifications.minSeverity') }}</span>
            <select v-model="newSeverity" class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm text-foreground">
              <option value="info">info</option>
              <option value="warning">warning</option>
              <option value="error">error</option>
              <option value="critical">critical</option>
            </select>
          </label>
          <Button :disabled="!newUrl.trim() || creating" @click="create">
            <Loader2 v-if="creating" class="size-4 animate-spin" /><Plus v-else class="size-4" />{{ $t('common.add') }}
          </Button>
        </div>
        <p class="mt-2 text-[11px] text-muted-foreground">{{ $t('notifications.hint') }}</p>
      </Card>

      <!-- List -->
      <Card class="overflow-hidden">
        <div class="flex items-center justify-between border-b border-border/60 px-5 py-3">
          <p class="text-sm font-semibold">{{ $t('notifications.sinks') }}</p>
          <Loader2 v-if="loading" class="size-4 animate-spin text-muted-foreground" />
        </div>
        <div v-if="sinks.length" class="divide-y divide-border/60">
          <div v-for="(s, i) in sinks" :key="s.id ?? `env-${i}`" class="flex items-center gap-4 px-5 py-3">
            <Badge variant="muted" class="w-16 justify-center uppercase">{{ s.type }}</Badge>
            <div class="min-w-0 flex-1">
              <span class="block truncate font-mono text-xs text-muted-foreground">{{ s.url_preview }}</span>
            </div>
            <Badge :variant="sevVariant[s.min_severity]">≥ {{ s.min_severity }}</Badge>
            <Badge v-if="s.source === 'env'" variant="outline" :title="$t('notifications.envTitle')">env</Badge>
            <Button
              v-if="s.id != null"
              size="icon-sm"
              variant="ghost"
              :title="$t('notifications.testOne')"
              :disabled="testing"
              @click="test(s.id)"
            >
              <Send class="size-4" />
            </Button>
            <Button
              v-if="s.id != null"
              size="icon-sm"
              variant="ghost"
              :title="$t('notifications.removeTitle')"
              @click="remove(s)"
            >
              <Trash2 class="size-4" />
            </Button>
          </div>
        </div>
        <p v-else-if="!loading" class="px-5 py-10 text-center text-sm text-muted-foreground">{{ $t('notifications.none') }}</p>
      </Card>
    </template>
  </div>
</template>
