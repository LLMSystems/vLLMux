<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Info, RefreshCw } from '@lucide/vue'
import { useTrafficStore } from '@/stores/traffic'
import { useModelsStore } from '@/stores/models'
import Card from '@/components/ui/Card.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import CardTitle from '@/components/ui/CardTitle.vue'
import CardContent from '@/components/ui/CardContent.vue'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import RouterFanDiagram from '@/components/RouterFanDiagram.vue'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/lib/toast'
import { routingStrategyLabel } from '@/lib/routingStrategies'
import { formatLatency, formatNumber } from '@/lib/utils'

const { t } = useI18n()
const traffic = useTrafficStore()
const models = useModelsStore()

// ---- Router load-balancing strategy (global, hot-swappable) ----
// One-line "best for" per strategy, shown in the help card.
const strategy = ref<string>('')
const strategies = ref<string[]>([])
const savingStrategy = ref(false)
const showStrategyHelp = ref(false)
const strategyLabel = routingStrategyLabel
const strategyInfo = (s: string) => {
  const info = t('traffic.strategyInfo.' + s)
  return info === 'traffic.strategyInfo.' + s ? '' : info
}

onMounted(async () => {
  try {
    const r = await api.getRouting()
    strategy.value = r.strategy
    strategies.value = r.available
  } catch {
    /* router may be unreachable; the selector just stays empty */
  }
})

async function onStrategyChange(e: Event) {
  const next = (e.target as HTMLSelectElement).value
  const prev = strategy.value
  savingStrategy.value = true
  try {
    const r = await api.setRouting(next)
    strategy.value = r.strategy
    toast.success(t('traffic.strategyChanged', { name: strategyLabel(r.strategy) }), {
      description: t('traffic.strategyChangedDesc'),
    })
  } catch (e) {
    strategy.value = prev // revert the <select> to the real value
    toast.error(t('traffic.strategyChangeFailed'), {
      description: e instanceof ApiError ? e.message : String(e),
    })
  } finally {
    savingStrategy.value = false
  }
}

// Max p95 across rows → scale the latency bars.
const maxP95 = computed(() => Math.max(1, ...traffic.usage.map((u) => u.p95_latency_ms)))

function codeVariant(code: number) {
  if (code >= 500) return 'failed'
  if (code >= 400) return 'starting'
  return 'ready'
}

// LLM groups from the model registry (sorted live-first) so the fans reflect
// real topology even when the router reports no metrics yet.
const llmGroups = computed(() => {
  const hasLive = new Map<string, boolean>()
  for (const m of models.llms) {
    const g = m.key.split('::')[0] ?? m.key
    const live = m.state === 'ready' || m.state === 'starting'
    hasLive.set(g, (hasLive.get(g) ?? false) || live)
  }
  return [...hasLive.entries()].sort((a, b) => Number(b[1]) - Number(a[1])).map(([g]) => g)
})

function onFilter(e: Event) {
  const v = (e.target as HTMLSelectElement).value
  void traffic.setFilter(v || null)
}
</script>

<template>
  <div class="space-y-6 p-6">
    <!-- Usage rollup -->
    <Card>
      <CardHeader class="flex-row items-center justify-between">
        <CardTitle>{{ $t('traffic.modelUsage') }}</CardTitle>
        <Button variant="outline" size="sm" @click="traffic.refresh()"><RefreshCw class="size-3.5" />{{ $t('common.refresh') }}</Button>
      </CardHeader>
      <CardContent>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr class="border-b border-border/60">
                <th class="pb-2 pr-4 font-medium">{{ $t('traffic.tableModel') }}</th>
                <th class="pb-2 pr-4 text-right font-medium">{{ $t('traffic.tableRequests') }}</th>
                <th class="pb-2 pr-4 text-right font-medium">{{ $t('traffic.tableErrors') }}</th>
                <th class="pb-2 pr-4 text-right font-medium">{{ $t('traffic.tableAvg') }}</th>
                <th class="pb-2 pr-4 text-right font-medium">p50</th>
                <th class="pb-2 pr-4 font-medium">p95</th>
                <th class="pb-2 text-right font-medium">{{ $t('common.tokens') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="u in traffic.usage"
                :key="u.model_key"
                class="border-b border-border/40 last:border-0"
              >
                <td class="py-2.5 pr-4 font-medium">{{ u.model_key }}</td>
                <td class="py-2.5 pr-4 text-right tabular">{{ formatNumber(u.count) }}</td>
                <td class="py-2.5 pr-4 text-right tabular">
                  <span :class="u.error_count ? 'text-status-failed' : 'text-muted-foreground'">{{
                    u.error_count
                  }}</span>
                </td>
                <td class="py-2.5 pr-4 text-right tabular text-muted-foreground">
                  {{ formatLatency(u.avg_latency_ms) }}
                </td>
                <td class="py-2.5 pr-4 text-right tabular">{{ formatLatency(u.p50_latency_ms) }}</td>
                <td class="py-2.5 pr-4">
                  <div class="flex items-center gap-2">
                    <div class="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
                      <div
                        class="h-full rounded-full bg-[var(--chart-4)]"
                        :style="{ width: `${(u.p95_latency_ms / maxP95) * 100}%` }"
                      />
                    </div>
                    <span class="tabular text-xs">{{ formatLatency(u.p95_latency_ms) }}</span>
                  </div>
                </td>
                <td class="py-2.5 text-right tabular text-muted-foreground">
                  {{ formatNumber(u.total_tokens) }}
                </td>
              </tr>
              <tr v-if="!traffic.usage.length">
                <td colspan="7" class="py-6 text-center text-muted-foreground">{{ $t('traffic.noUsage') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>

    <!-- Router load: load-balancing fan per group -->
    <Card>
      <CardHeader class="flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>{{ $t('traffic.routerLoadBalancing') }}</CardTitle>
          <p class="mt-1 text-xs text-muted-foreground">
            {{ $t('traffic.routerLoadDesc') }}
          </p>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          <label class="flex items-center gap-2 text-xs text-muted-foreground">
            {{ $t('traffic.strategy') }}
            <select
              class="h-8 rounded-md border border-input bg-background/40 px-2 text-xs text-foreground disabled:opacity-50"
              :value="strategy"
              :disabled="savingStrategy || !strategies.length"
              @change="onStrategyChange"
            >
              <option v-if="!strategy" value="">—</option>
              <option v-for="s in strategies" :key="s" :value="s">{{ strategyLabel(s) }}</option>
            </select>
          </label>
          <button
            type="button"
            class="flex size-7 items-center justify-center rounded-md border border-input text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            :class="showStrategyHelp && 'bg-accent text-foreground'"
            :aria-pressed="showStrategyHelp"
            :title="$t('traffic.scenarioTitle')"
            @click="showStrategyHelp = !showStrategyHelp"
          >
            <Info class="size-4" />
          </button>
        </div>
      </CardHeader>
      <CardContent class="space-y-3">
        <!-- Strategy reference: which one suits which scenario -->
        <div
          v-if="showStrategyHelp"
          class="rounded-lg border border-border/60 bg-muted/30 p-3 text-xs"
        >
          <p class="mb-2 font-medium text-foreground">{{ $t('traffic.scenarioTitle') }}</p>
          <ul class="space-y-1.5">
            <li
              v-for="s in strategies"
              :key="s"
              class="flex gap-2"
              :class="s === strategy ? 'text-foreground' : 'text-muted-foreground'"
            >
              <span
                class="mt-0.5 shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px]"
                :class="s === strategy ? 'bg-[var(--chart-1)]/20 text-[var(--chart-1)]' : 'bg-muted text-muted-foreground'"
              >{{ strategyLabel(s) }}</span>
              <span>{{ strategyInfo(s) }}</span>
            </li>
          </ul>
          <p class="mt-2 text-[11px] text-muted-foreground/80">
            {{ $t('traffic.scenarioFooter') }}
          </p>
        </div>
        <RouterFanDiagram v-for="g in llmGroups" :key="g" :group="g" />
        <p v-if="!llmGroups.length" class="py-6 text-center text-sm text-muted-foreground">
          {{ $t('traffic.noLlmGroups') }}
        </p>
      </CardContent>
    </Card>

    <!-- Request log -->
    <Card>
      <CardHeader class="flex-row items-center justify-between">
        <CardTitle>{{ $t('traffic.requestLog') }}</CardTitle>
        <select
          class="h-8 rounded-md border border-input bg-background/40 px-2 text-xs"
          :value="traffic.filterModel ?? ''"
          @change="onFilter"
        >
          <option value="">{{ $t('common.allModels') }}</option>
          <option v-for="m in models.llms" :key="m.key" :value="m.key.split('::')[0]">
            {{ m.key.split('::')[0] }}
          </option>
        </select>
      </CardHeader>
      <CardContent>
        <div class="max-h-[28rem] overflow-auto">
          <table class="w-full text-sm">
            <thead class="sticky top-0 bg-card text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr class="border-b border-border/60">
                <th class="pb-2 pr-3 font-medium">{{ $t('traffic.tableModel') }}</th>
                <th class="pb-2 pr-3 font-medium">{{ $t('common.path') }}</th>
                <th class="pb-2 pr-3 font-medium">{{ $t('common.status') }}</th>
                <th class="pb-2 pr-3 text-right font-medium">{{ $t('common.latency') }}</th>
                <th class="pb-2 text-right font-medium">{{ $t('common.tokens') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in traffic.requests" :key="r.id" class="border-b border-border/40 last:border-0">
                <td class="py-2 pr-3 font-medium">
                  {{ r.model_key }}<span class="text-muted-foreground">::{{ r.instance_id }}</span>
                </td>
                <td class="py-2 pr-3 font-mono text-xs text-muted-foreground">{{ r.path }}</td>
                <td class="py-2 pr-3">
                  <Badge :variant="codeVariant(r.status_code) as any" class="tabular">{{ r.status_code }}</Badge>
                </td>
                <td class="py-2 pr-3 text-right tabular">{{ formatLatency(r.latency_ms) }}</td>
                <td class="py-2 text-right tabular text-muted-foreground">{{ r.total_tokens ?? '—' }}</td>
              </tr>
              <tr v-if="!traffic.requests.length">
                <td colspan="5" class="py-6 text-center text-muted-foreground">{{ $t('traffic.noRequests') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  </div>
</template>
