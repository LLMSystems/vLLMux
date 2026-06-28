<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Coins, Loader2, Lock, Save, TriangleAlert } from '@lucide/vue'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'
import { toast } from '@/lib/toast'
import type { CostSummary } from '@/types/api'
import Card from '@/components/ui/Card.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Badge from '@/components/ui/Badge.vue'

const { t } = useI18n()
const { isAdmin, authEnabled, ensureUnlocked, refreshStatus } = useAuth()

const summary = ref<CostSummary | null>(null)
const loading = ref(false)
const locked = ref(false)
const since = ref('')
const until = ref('')
// Per-model price edits (admin), keyed by model -> {in, out}.
const edits = reactive<Record<string, { in: string; out: string }>>({})
const savingModel = ref<string | null>(null)

const toEpoch = (v: string): number | undefined =>
  v ? Math.floor(new Date(v).getTime() / 1000) : undefined

const currency = computed(() => summary.value?.currency ?? 'USD')

function fmtCost(n: number): string {
  return `${n.toFixed(n !== 0 && Math.abs(n) < 0.01 ? 6 : 2)}`
}
function fmtTokens(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return String(n)
}

async function load() {
  if (!(await ensureUnlocked())) {
    locked.value = true
    return
  }
  locked.value = false
  loading.value = true
  try {
    summary.value = await api.costSummary(toEpoch(since.value), toEpoch(until.value))
    for (const m of summary.value.by_model) {
      edits[m.model] = { in: String(m.input_price), out: String(m.output_price) }
    }
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) locked.value = true
    else toast.error(t('cost.loadFailed'), { description: String(e) })
  } finally {
    loading.value = false
  }
}

async function savePrice(model: string) {
  const e = edits[model]
  if (!e) return
  const input = Number(e.in)
  const output = Number(e.out)
  if (!(input >= 0) || !(output >= 0)) {
    toast.error(t('cost.badPrice'))
    return
  }
  savingModel.value = model
  try {
    await api.setPrice(model, input, output)
    toast.success(t('cost.priceSaved', { model }))
    await load()
  } catch (err) {
    toast.error(t('cost.saveFailed'), {
      description: err instanceof ApiError ? `${err.status}: ${err.message}` : String(err),
    })
  } finally {
    savingModel.value = null
  }
}

const canEdit = computed(() => isAdmin.value || authEnabled.value === false)

onMounted(async () => {
  await refreshStatus()
  await load()
})
</script>

<template>
  <div class="space-y-6 p-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="flex items-center gap-2 text-lg font-semibold"><Coins class="size-5" />{{ $t('cost.title') }}</h1>
        <p class="mt-0.5 text-sm text-muted-foreground">{{ $t('cost.description') }}</p>
      </div>
      <div class="flex flex-wrap items-end gap-2">
        <label>
          <span class="text-xs text-muted-foreground">{{ $t('cost.since') }}</span>
          <Input v-model="since" type="datetime-local" class="mt-1 w-44" />
        </label>
        <label>
          <span class="text-xs text-muted-foreground">{{ $t('cost.until') }}</span>
          <Input v-model="until" type="datetime-local" class="mt-1 w-44" />
        </label>
        <Button size="sm" :disabled="loading" @click="load">
          <Loader2 v-if="loading" class="size-4 animate-spin" />{{ $t('cost.apply') }}
        </Button>
      </div>
    </div>

    <Card v-if="locked" class="flex flex-col items-center gap-3 p-10 text-center">
      <Lock class="size-8 text-muted-foreground" />
      <p class="text-sm text-muted-foreground">{{ $t('cost.locked') }}</p>
      <Button size="sm" @click="load">{{ $t('cost.unlock') }}</Button>
    </Card>

    <template v-else-if="summary">
      <!-- Total -->
      <div class="grid gap-4 sm:grid-cols-3">
        <Card class="p-5">
          <p class="text-xs uppercase tracking-wide text-muted-foreground">{{ $t('cost.totalSpend') }}</p>
          <p class="mt-1 text-2xl font-semibold tabular">
            {{ currency }} {{ fmtCost(summary.total_cost) }}
          </p>
        </Card>
        <Card class="flex items-center gap-3 p-5 sm:col-span-2">
          <TriangleAlert v-if="summary.any_unpriced" class="size-5 shrink-0 text-status-starting" />
          <p class="text-sm text-muted-foreground">
            <template v-if="summary.any_unpriced">{{ $t('cost.unpricedHint') }}</template>
            <template v-else>{{ $t('cost.pricedHint') }}</template>
            <span v-if="summary.default_input_price || summary.default_output_price" class="block text-xs">
              {{ $t('cost.defaultPrice', {
                cur: currency,
                in: summary.default_input_price,
                out: summary.default_output_price,
              }) }}
            </span>
          </p>
        </Card>
      </div>

      <!-- Per model -->
      <Card class="overflow-hidden">
        <div class="border-b border-border/60 px-5 py-3">
          <p class="text-sm font-semibold">{{ $t('cost.byModel') }}</p>
          <p class="text-xs text-muted-foreground">{{ $t('cost.priceUnit') }}</p>
        </div>
        <div class="overflow-x-auto">
          <table v-if="summary.by_model.length" class="w-full text-sm">
            <thead class="border-b border-border/60 text-left text-xs text-muted-foreground">
              <tr>
                <th class="px-4 py-2.5 font-medium">{{ $t('cost.colModel') }}</th>
                <th class="px-4 py-2.5 text-right font-medium">{{ $t('cost.colReq') }}</th>
                <th class="px-4 py-2.5 text-right font-medium">{{ $t('cost.colIn') }}</th>
                <th class="px-4 py-2.5 text-right font-medium">{{ $t('cost.colOut') }}</th>
                <th class="px-4 py-2.5 text-right font-medium">{{ $t('cost.colInPrice') }}</th>
                <th class="px-4 py-2.5 text-right font-medium">{{ $t('cost.colOutPrice') }}</th>
                <th class="px-4 py-2.5 text-right font-medium">{{ $t('cost.colCost') }}</th>
                <th v-if="canEdit" class="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody class="divide-y divide-border/50">
              <tr v-for="m in summary.by_model" :key="m.model" class="hover:bg-muted/30">
                <td class="px-4 py-2 font-mono text-xs">
                  {{ m.model }}
                  <Badge v-if="!m.priced" variant="muted" class="ml-1">{{ $t('cost.default') }}</Badge>
                </td>
                <td class="px-4 py-2 text-right tabular text-muted-foreground">{{ m.requests }}</td>
                <td class="px-4 py-2 text-right tabular text-muted-foreground">{{ fmtTokens(m.prompt_tokens) }}</td>
                <td class="px-4 py-2 text-right tabular text-muted-foreground">{{ fmtTokens(m.completion_tokens) }}</td>
                <template v-if="canEdit && edits[m.model]">
                  <td class="px-4 py-2 text-right">
                    <Input v-model="edits[m.model]!.in" type="number" min="0" step="0.01" class="h-8 w-24 text-right" />
                  </td>
                  <td class="px-4 py-2 text-right">
                    <Input v-model="edits[m.model]!.out" type="number" min="0" step="0.01" class="h-8 w-24 text-right" />
                  </td>
                </template>
                <template v-else>
                  <td class="px-4 py-2 text-right tabular text-muted-foreground">{{ m.input_price }}</td>
                  <td class="px-4 py-2 text-right tabular text-muted-foreground">{{ m.output_price }}</td>
                </template>
                <td class="px-4 py-2 text-right font-medium tabular">{{ currency }} {{ fmtCost(m.cost) }}</td>
                <td v-if="canEdit" class="px-4 py-2 text-right">
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    :title="$t('cost.savePrice')"
                    :disabled="savingModel === m.model"
                    @click="savePrice(m.model)"
                  >
                    <Loader2 v-if="savingModel === m.model" class="size-4 animate-spin" /><Save v-else class="size-4" />
                  </Button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="px-5 py-10 text-center text-sm text-muted-foreground">{{ $t('cost.noData') }}</p>
        </div>
      </Card>

      <!-- Per key -->
      <Card v-if="summary.by_key.length" class="overflow-hidden">
        <div class="border-b border-border/60 px-5 py-3">
          <p class="text-sm font-semibold">{{ $t('cost.byKey') }}</p>
        </div>
        <table class="w-full text-sm">
          <thead class="border-b border-border/60 text-left text-xs text-muted-foreground">
            <tr>
              <th class="px-4 py-2.5 font-medium">{{ $t('cost.colKey') }}</th>
              <th class="px-4 py-2.5 text-right font-medium">{{ $t('cost.colReq') }}</th>
              <th class="px-4 py-2.5 text-right font-medium">{{ $t('cost.colTokens') }}</th>
              <th class="px-4 py-2.5 text-right font-medium">{{ $t('cost.colCost') }}</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-border/50">
            <tr v-for="k in summary.by_key" :key="k.name" class="hover:bg-muted/30">
              <td class="px-4 py-2 font-mono text-xs">{{ k.name }}</td>
              <td class="px-4 py-2 text-right tabular text-muted-foreground">{{ k.requests }}</td>
              <td class="px-4 py-2 text-right tabular text-muted-foreground">{{ fmtTokens(k.total_tokens) }}</td>
              <td class="px-4 py-2 text-right font-medium tabular">{{ currency }} {{ fmtCost(k.cost) }}</td>
            </tr>
          </tbody>
        </table>
      </Card>
    </template>
  </div>
</template>
