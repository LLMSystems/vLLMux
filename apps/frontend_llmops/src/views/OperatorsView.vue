<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Copy, Loader2, Lock, Plus, ShieldAlert, Trash2, Users } from '@lucide/vue'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import { formatTime } from '@/lib/utils'
import type { CreatedOperator, Operator, Role } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'
import Dialog from '@/components/ui/Dialog.vue'
import UserAvatar from '@/components/UserAvatar.vue'

const { t } = useI18n()
const { authEnabled, isAdmin, ensureUnlocked, refreshStatus } = useAuth()

const operators = ref<Operator[]>([])
const loading = ref(false)
const locked = ref(false)
const denied = ref(false)
const newLabel = ref('')
const newRole = ref<Role>('operator')
const creating = ref(false)

// One-time plaintext reveal after creation.
const reveal = ref<CreatedOperator | null>(null)
const revealOpen = ref(false)

const roleVariant: Record<Role, 'default' | 'ready' | 'muted'> = {
  admin: 'default',
  operator: 'ready',
  viewer: 'muted',
}

async function load() {
  if (!(await ensureUnlocked())) {
    locked.value = true
    return
  }
  locked.value = false
  loading.value = true
  try {
    operators.value = await api.listOperators()
    denied.value = false
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) locked.value = true
    else if (e instanceof ApiError && e.status === 403) denied.value = true
    else toast.error(t('operators.loadFailed'), { description: String(e) })
  } finally {
    loading.value = false
  }
}

async function create() {
  const label = newLabel.value.trim()
  if (!label || creating.value) return
  if (!(await ensureUnlocked())) return
  creating.value = true
  try {
    reveal.value = await api.createOperator(label, newRole.value)
    revealOpen.value = true
    newLabel.value = ''
    newRole.value = 'operator'
    await load()
  } catch (e) {
    toast.error(t('operators.createFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    creating.value = false
  }
}

async function revoke(o: Operator) {
  if (!(await ensureUnlocked())) return
  try {
    await api.revokeOperator(o.id)
    toast.success(t('operators.revokeSuccess', { label: o.label }))
    await load()
  } catch (e) {
    toast.error(t('operators.revokeFailed'), { description: String(e) })
  }
}

async function copyToken() {
  if (!reveal.value) return
  try {
    await navigator.clipboard.writeText(reveal.value.token)
    toast.success(t('operators.copySuccess'))
  } catch {
    toast.error(t('operators.copyFailed'))
  }
}

onMounted(async () => {
  await refreshStatus()
  await load()
})
</script>

<template>
  <div class="space-y-6 p-6">
    <div>
      <h1 class="flex items-center gap-2 text-lg font-semibold"><Users class="size-5" />{{ $t('operators.title') }}</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">{{ $t('operators.description') }}</p>
    </div>

    <!-- Auth-disabled notice -->
    <Card
      v-if="authEnabled === false"
      class="border-status-starting/30 bg-status-starting/10 p-4 text-sm text-status-starting"
    >
      {{ $t('operators.authDisabled') }}
    </Card>

    <!-- Not an admin -->
    <Card v-if="denied" class="flex flex-col items-center gap-3 p-10 text-center">
      <ShieldAlert class="size-8 text-status-failed" />
      <p class="text-sm text-muted-foreground">{{ $t('operators.adminOnly') }}</p>
    </Card>

    <!-- Locked -->
    <Card v-else-if="locked" class="flex flex-col items-center gap-3 p-10 text-center">
      <Lock class="size-8 text-muted-foreground" />
      <p class="text-sm text-muted-foreground">{{ $t('operators.locked') }}</p>
      <Button size="sm" @click="load">{{ $t('operators.unlock') }}</Button>
    </Card>

    <template v-else-if="isAdmin || authEnabled === false">
      <!-- Create -->
      <Card class="p-5">
        <p class="mb-3 text-sm font-semibold">{{ $t('operators.createNew') }}</p>
        <div class="flex flex-wrap items-end gap-2">
          <label class="min-w-48 flex-1">
            <span class="text-xs text-muted-foreground">{{ $t('operators.labelLabel') }}</span>
            <Input v-model="newLabel" :placeholder="$t('operators.labelPlaceholder')" class="mt-1" @keydown.enter="create" />
          </label>
          <label class="w-36">
            <span class="text-xs text-muted-foreground">{{ $t('operators.role') }}</span>
            <select
              v-model="newRole"
              class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm text-foreground"
            >
              <option value="viewer">{{ $t('operators.roleViewer') }}</option>
              <option value="operator">{{ $t('operators.roleOperator') }}</option>
              <option value="admin">{{ $t('operators.roleAdmin') }}</option>
            </select>
          </label>
          <Button :disabled="!newLabel.trim() || creating" @click="create">
            <Loader2 v-if="creating" class="size-4 animate-spin" /><Plus v-else class="size-4" />{{ $t('common.create') }}
          </Button>
        </div>
        <p class="mt-2 text-[11px] text-muted-foreground">{{ $t('operators.roleHint') }}</p>
      </Card>

      <!-- List -->
      <Card class="overflow-hidden">
        <div class="flex items-center justify-between border-b border-border/60 px-5 py-3">
          <p class="text-sm font-semibold">{{ $t('operators.issued') }}</p>
          <Loader2 v-if="loading" class="size-4 animate-spin text-muted-foreground" />
        </div>
        <div v-if="operators.length" class="divide-y divide-border/60">
          <div v-for="o in operators" :key="o.id" class="flex items-center gap-4 px-5 py-3">
            <UserAvatar :seed="o.label" :size="36" />
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <span class="truncate text-sm font-medium">{{ o.label }}</span>
                <Badge :variant="roleVariant[o.role]">{{ $t('operators.role' + o.role.charAt(0).toUpperCase() + o.role.slice(1)) }}</Badge>
                <Badge v-if="o.revoked" variant="muted">{{ $t('operators.revoked') }}</Badge>
              </div>
              <span class="font-mono text-xs text-muted-foreground">{{ o.prefix }}</span>
            </div>
            <div class="hidden text-right text-xs text-muted-foreground sm:block">
              <p>{{ $t('operators.created') }}{{ formatTime(o.created_at) }}</p>
              <p>{{ $t('operators.lastUsed') }}{{ o.last_used_at ? formatTime(o.last_used_at) : '—' }}</p>
            </div>
            <Button
              v-if="!o.revoked"
              size="icon-sm"
              variant="ghost"
              :title="$t('operators.revokeTitle')"
              @click="revoke(o)"
            >
              <Trash2 class="size-4" />
            </Button>
          </div>
        </div>
        <p v-else-if="!loading" class="px-5 py-10 text-center text-sm text-muted-foreground">{{ $t('operators.none') }}</p>
      </Card>
    </template>

    <!-- One-time reveal -->
    <Dialog v-model:open="revealOpen" :title="$t('operators.created2')">
      <div class="space-y-4">
        <div class="flex items-center gap-3">
          <UserAvatar v-if="reveal" :seed="reveal.label" :size="40" />
          <div class="text-sm">
            <p class="font-medium">{{ reveal?.label }}</p>
            <Badge v-if="reveal" :variant="roleVariant[reveal.role]">{{ reveal.role }}</Badge>
          </div>
        </div>
        <p class="text-sm text-status-failed">{{ $t('operators.copyImmediate') }}</p>
        <div class="flex items-center gap-2 rounded-lg border border-border/60 bg-background/40 p-3">
          <code class="flex-1 break-all font-mono text-xs">{{ reveal?.token }}</code>
          <Button size="icon-sm" variant="ghost" :title="$t('codeBlock.copy')" @click="copyToken"><Copy class="size-4" /></Button>
        </div>
        <div class="flex justify-end">
          <Button @click="revealOpen = false">{{ $t('common.done') }}</Button>
        </div>
      </div>
    </Dialog>
  </div>
</template>
