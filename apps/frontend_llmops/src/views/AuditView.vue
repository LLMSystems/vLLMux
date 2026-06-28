<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Loader2, Lock, ScrollText, ShieldAlert } from '@lucide/vue'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatTime } from '@/lib/utils'
import type { AuditEntry } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import UserAvatar from '@/components/UserAvatar.vue'

const { t } = useI18n()
const { authEnabled, ensureUnlocked, refreshStatus } = useAuth()

const rows = ref<AuditEntry[]>([])
const loading = ref(false)
const locked = ref(false)
const denied = ref(false)
const filterActor = ref('')
const filterAction = ref('')

const methodVariant = (m: string) =>
  m === 'DELETE' ? 'failed' : m === 'POST' ? 'ready' : 'starting'
const statusVariant = (s: number) =>
  s >= 500 ? 'failed' : s >= 400 ? 'starting' : 'ready'

async function load() {
  if (!(await ensureUnlocked())) {
    locked.value = true
    return
  }
  locked.value = false
  loading.value = true
  try {
    rows.value = await api.listAudit({
      actor: filterActor.value.trim() || undefined,
      action: filterAction.value.trim() || undefined,
      limit: 300,
    })
    denied.value = false
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) locked.value = true
    else if (e instanceof ApiError && e.status === 403) denied.value = true
    else toast.error(t('audit.loadFailed'), { description: String(e) })
  } finally {
    loading.value = false
  }
}

const hasRows = computed(() => rows.value.length > 0)

onMounted(async () => {
  await refreshStatus()
  await load()
})
</script>

<template>
  <div class="space-y-6 p-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="flex items-center gap-2 text-lg font-semibold"><ScrollText class="size-5" />{{ $t('audit.title') }}</h1>
        <p class="mt-0.5 text-sm text-muted-foreground">{{ $t('audit.description') }}</p>
      </div>
      <div class="flex items-end gap-2">
        <label>
          <span class="text-xs text-muted-foreground">{{ $t('audit.filterActor') }}</span>
          <Input v-model="filterActor" :placeholder="$t('audit.actorPlaceholder')" class="mt-1 w-36" @keydown.enter="load" />
        </label>
        <label>
          <span class="text-xs text-muted-foreground">{{ $t('audit.filterAction') }}</span>
          <Input v-model="filterAction" :placeholder="$t('audit.actionPlaceholder')" class="mt-1 w-40" @keydown.enter="load" />
        </label>
        <Button size="sm" :disabled="loading" @click="load">
          <Loader2 v-if="loading" class="size-4 animate-spin" />{{ $t('audit.apply') }}
        </Button>
      </div>
    </div>

    <Card
      v-if="authEnabled === false"
      class="border-status-starting/30 bg-status-starting/10 p-4 text-sm text-status-starting"
    >
      {{ $t('audit.authDisabled') }}
    </Card>

    <Card v-if="denied" class="flex flex-col items-center gap-3 p-10 text-center">
      <ShieldAlert class="size-8 text-status-failed" />
      <p class="text-sm text-muted-foreground">{{ $t('audit.adminOnly') }}</p>
    </Card>

    <Card v-else-if="locked" class="flex flex-col items-center gap-3 p-10 text-center">
      <Lock class="size-8 text-muted-foreground" />
      <p class="text-sm text-muted-foreground">{{ $t('audit.locked') }}</p>
      <Button size="sm" @click="load">{{ $t('audit.unlock') }}</Button>
    </Card>

    <Card v-else class="overflow-hidden">
      <div class="overflow-x-auto">
        <table v-if="hasRows" class="w-full text-sm">
          <thead class="border-b border-border/60 text-left text-xs text-muted-foreground">
            <tr>
              <th class="px-4 py-2.5 font-medium">{{ $t('audit.colTime') }}</th>
              <th class="px-4 py-2.5 font-medium">{{ $t('audit.colActor') }}</th>
              <th class="px-4 py-2.5 font-medium">{{ $t('audit.colAction') }}</th>
              <th class="px-4 py-2.5 font-medium">{{ $t('audit.colTarget') }}</th>
              <th class="px-4 py-2.5 font-medium">{{ $t('audit.colStatus') }}</th>
              <th class="px-4 py-2.5 font-medium">{{ $t('audit.colDetail') }}</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-border/50">
            <tr v-for="r in rows" :key="r.id" class="hover:bg-muted/30">
              <td class="whitespace-nowrap px-4 py-2 text-xs text-muted-foreground">{{ formatTime(r.ts) }}</td>
              <td class="px-4 py-2">
                <div class="flex items-center gap-2">
                  <UserAvatar :seed="r.actor" :size="24" />
                  <div class="leading-tight">
                    <span class="text-xs font-medium">{{ r.actor }}</span>
                    <span v-if="r.role" class="ml-1 text-[10px] text-muted-foreground">{{ r.role }}</span>
                  </div>
                </div>
              </td>
              <td class="px-4 py-2">
                <div class="flex items-center gap-2">
                  <Badge :variant="methodVariant(r.method)" class="tabular">{{ r.method }}</Badge>
                  <code class="font-mono text-xs text-muted-foreground">{{ r.path }}</code>
                </div>
              </td>
              <td class="px-4 py-2 font-mono text-xs">{{ r.target || '—' }}</td>
              <td class="px-4 py-2"><Badge :variant="statusVariant(r.status)" class="tabular">{{ r.status }}</Badge></td>
              <td class="max-w-xs truncate px-4 py-2 font-mono text-[11px] text-muted-foreground" :title="r.detail || ''">
                {{ r.detail || '—' }}
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else-if="!loading" class="px-5 py-10 text-center text-sm text-muted-foreground">{{ $t('audit.none') }}</p>
      </div>
    </Card>
  </div>
</template>
