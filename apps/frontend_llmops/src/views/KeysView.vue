<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Copy, KeyRound, Loader2, Lock, Plus, Trash2 } from '@lucide/vue'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatNumber, formatTime } from '@/lib/utils'
import type { ApiKey, CreatedKey, QuotaPeriod } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import Dialog from '@/components/ui/Dialog.vue'

const { t } = useI18n()
const { authEnabled, ensureUnlocked, refreshStatus } = useAuth()

const keys = ref<ApiKey[]>([])
const loading = ref(false)
const locked = ref(false)
const newName = ref('')
const newRpm = ref<number | undefined>(undefined)
const newQuota = ref<number | undefined>(undefined)
const newPeriod = ref<QuotaPeriod>('total')
const creating = ref(false)

// One-time plaintext reveal after creation.
const reveal = ref<CreatedKey | null>(null)
const revealOpen = ref(false)

async function load() {
  if (!(await ensureUnlocked())) {
    locked.value = true
    return
  }
  locked.value = false
  loading.value = true
  try {
    keys.value = await api.listKeys()
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) locked.value = true
    else toast.error(t('keys.loadFailed'), { description: String(e) })
  } finally {
    loading.value = false
  }
}

async function create() {
  const name = newName.value.trim()
  if (!name || creating.value) return
  if (!(await ensureUnlocked())) return
  creating.value = true
  try {
    reveal.value = await api.createKey(
      name,
      newRpm.value && newRpm.value > 0 ? newRpm.value : null,
      newQuota.value && newQuota.value > 0 ? newQuota.value : null,
      newPeriod.value,
    )
    revealOpen.value = true
    newName.value = ''
    newRpm.value = undefined
    newQuota.value = undefined
    newPeriod.value = 'total'
    await load()
  } catch (e) {
    toast.error(t('keys.createFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    creating.value = false
  }
}

async function revoke(k: ApiKey) {
  if (!(await ensureUnlocked())) return
  try {
    await api.revokeKey(k.id)
    toast.success(t('keys.revokeSuccess', { name: k.name }))
    await load()
  } catch (e) {
    toast.error(t('keys.revokeFailed'), { description: String(e) })
  }
}

async function copyKey() {
  if (!reveal.value) return
  try {
    await navigator.clipboard.writeText(reveal.value.key)
    toast.success(t('keys.copySuccess'))
  } catch {
    toast.error(t('keys.copyFailed'))
  }
}

onMounted(async () => {
  await refreshStatus()
  await load()
})
</script>

<template>
  <div class="space-y-6 p-6">
    <div class="flex flex-wrap items-center gap-3">
      <div>
        <h1 class="flex items-center gap-2 text-lg font-semibold"><KeyRound class="size-5" />{{ $t('keys.title') }}</h1>
        <p class="mt-0.5 text-sm text-muted-foreground">
          {{ $t('keys.description') }}
        </p>
      </div>
    </div>

    <!-- Auth-disabled notice -->
    <Card
      v-if="authEnabled === false"
      class="border-status-starting/30 bg-status-starting/10 p-4 text-sm text-status-starting"
    >
      {{ $t('keys.authDisabled') }}
    </Card>

    <!-- Locked -->
    <Card v-if="locked" class="flex flex-col items-center gap-3 p-10 text-center">
      <Lock class="size-8 text-muted-foreground" />
      <p class="text-sm text-muted-foreground">{{ $t('keys.locked') }}</p>
      <Button size="sm" @click="load">{{ $t('keys.unlock') }}</Button>
    </Card>

    <template v-else>
      <!-- Create -->
      <Card class="p-5">
        <p class="mb-3 text-sm font-semibold">{{ $t('keys.createNew') }}</p>
        <div class="flex flex-wrap items-end gap-2">
          <label class="min-w-48 flex-1">
            <span class="text-xs text-muted-foreground">{{ $t('keys.nameLabel') }}</span>
            <Input v-model="newName" :placeholder="$t('keys.namePlaceholder')" class="mt-1" @keydown.enter="create" />
          </label>
          <label class="w-28">
            <span class="text-xs text-muted-foreground">{{ $t('keys.rateLimit') }}</span>
            <Input v-model.number="newRpm" type="number" min="1" :placeholder="$t('keys.ratePlaceholder')" class="mt-1" @keydown.enter="create" />
          </label>
          <label class="w-32">
            <span class="text-xs text-muted-foreground">{{ $t('keys.tokenQuota') }}</span>
            <Input v-model.number="newQuota" type="number" min="1" :placeholder="$t('keys.ratePlaceholder')" class="mt-1" @keydown.enter="create" />
          </label>
          <label class="w-28">
            <span class="text-xs text-muted-foreground">{{ $t('keys.quotaPeriod') }}</span>
            <select
              v-model="newPeriod"
              :disabled="!newQuota"
              class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm text-foreground disabled:opacity-50"
            >
              <option value="total">{{ $t('keys.periodTotal') }}</option>
              <option value="daily">{{ $t('keys.periodDaily') }}</option>
              <option value="monthly">{{ $t('keys.periodMonthly') }}</option>
            </select>
          </label>
          <Button :disabled="!newName.trim() || creating" @click="create">
            <Loader2 v-if="creating" class="size-4 animate-spin" /><Plus v-else class="size-4" />{{ $t('common.create') }}
          </Button>
        </div>
      </Card>

      <!-- List -->
      <Card class="overflow-hidden">
        <div class="flex items-center justify-between border-b border-border/60 px-5 py-3">
          <p class="text-sm font-semibold">{{ $t('keys.issuedKeys') }}</p>
          <Loader2 v-if="loading" class="size-4 animate-spin text-muted-foreground" />
        </div>
        <div v-if="keys.length" class="divide-y divide-border/60">
          <div v-for="k in keys" :key="k.id" class="flex items-center gap-4 px-5 py-3">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <span class="truncate text-sm font-medium">{{ k.name }}</span>
                <Badge v-if="k.revoked" variant="muted">{{ $t('keys.revoked') }}</Badge>
                <Badge v-if="k.rpm_limit" variant="muted" class="tabular">{{ k.rpm_limit }}{{ $t('keys.perMin') }}</Badge>
                <Badge
                  v-if="k.token_quota"
                  :variant="(k.quota_used ?? 0) >= k.token_quota ? 'failed' : 'muted'"
                  class="tabular"
                >
                  {{ formatNumber(k.quota_used ?? 0, true) }}/{{ formatNumber(k.token_quota, true) }}
                  {{ $t('keys.quotaPeriodShort.' + (k.quota_period || 'total')) }}
                </Badge>
              </div>
              <span class="font-mono text-xs text-muted-foreground">{{ k.prefix }}</span>
            </div>
            <div class="hidden text-right text-xs sm:block">
              <p class="tabular">
                <span class="font-medium text-foreground">{{ formatNumber(k.request_count) }}</span>
                <span class="text-muted-foreground"> {{ $t('keys.requestCount') }} · {{ formatNumber(k.total_tokens, true) }} {{ $t('common.tokens') }}</span>
              </p>
              <p class="text-muted-foreground">
                {{ $t('keys.lastUsed') }}{{ k.usage_last_ts ? formatTime(k.usage_last_ts) : '—' }}
              </p>
            </div>
            <Button
              v-if="!k.revoked"
              size="icon-sm"
              variant="ghost"
              :title="$t('keys.revokeTitle')"
              @click="revoke(k)"
            >
              <Trash2 class="size-4" />
            </Button>
          </div>
        </div>
        <p v-else-if="!loading" class="px-5 py-10 text-center text-sm text-muted-foreground">{{ $t('keys.noKeys') }}</p>
      </Card>
    </template>

    <!-- One-time reveal -->
    <Dialog v-model:open="revealOpen" :title="$t('keys.keyCreated')">
      <div class="space-y-4">
        <p class="text-sm text-status-failed">
          {{ $t('keys.copyImmediate') }}
        </p>
        <div class="flex items-center gap-2 rounded-lg border border-border/60 bg-background/40 p-3">
          <code class="flex-1 break-all font-mono text-xs">{{ reveal?.key }}</code>
          <Button size="icon-sm" variant="ghost" :title="$t('codeBlock.copy')" @click="copyKey"><Copy class="size-4" /></Button>
        </div>
        <div class="flex justify-end">
          <Button @click="revealOpen = false">{{ $t('common.done') }}</Button>
        </div>
      </div>
    </Dialog>
  </div>
</template>
