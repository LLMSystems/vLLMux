<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Activity, Cpu, Gauge, Server, Timer, Zap } from '@lucide/vue'
import { useModelsStore } from '@/stores/models'
import { useResourcesStore } from '@/stores/resources'
import { useTrafficStore } from '@/stores/traffic'
import { api } from '@/lib/api'
import StatCard from '@/components/StatCard.vue'
import SystemTopology from '@/components/SystemTopology.vue'
import Card from '@/components/ui/Card.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import CardTitle from '@/components/ui/CardTitle.vue'
import CardContent from '@/components/ui/CardContent.vue'
import GpuGauge from '@/components/GpuGauge.vue'
import StatusDot from '@/components/StatusDot.vue'
import Badge from '@/components/ui/Badge.vue'
import { formatLatency, formatNumber, formatPercent, formatTime } from '@/lib/utils'
import type { StateEvent } from '@/types/api'

const { t } = useI18n()
const models = useModelsStore()
const resources = useResourcesStore()
const traffic = useTrafficStore()

const events = ref<StateEvent[]>([])
let timer: ReturnType<typeof setInterval> | null = null

async function loadEvents() {
  try {
    events.value = (await api.getEvents(8)).reverse()
  } catch {
    /* ignore */
  }
}
onMounted(() => {
  void loadEvents()
  timer = setInterval(loadEvents, 5000)
})
onUnmounted(() => timer && clearInterval(timer))

function codeVariant(code: number) {
  if (code >= 500) return 'failed'
  if (code >= 400) return 'starting'
  return 'ready'
}
</script>

<template>
  <div class="space-y-6 p-6">
    <!-- KPI row -->
    <div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <StatCard
        :label="$t('overview.readyModels')"
        :value="`${models.readyCount} / ${models.total}`"
        :hint="models.counts.failed ? t('overview.failedCount', { n: models.counts.failed }) : $t('overview.allNormal')"
        :icon="Server"
        color="var(--chart-1)"
      />
      <StatCard
        :label="$t('overview.requestCount')"
        :value="formatNumber(traffic.totalRequests)"
        :hint="t('overview.errorRate', { rate: formatPercent(traffic.errorRate) })"
        :icon="Zap"
        color="var(--chart-2)"
      />
      <StatCard
        :label="$t('overview.latencyP95')"
        :value="formatLatency(traffic.weightedP95)"
        :hint="t('overview.processedTokens', { n: formatNumber(traffic.totalTokens) })"
        :icon="Timer"
        color="var(--chart-4)"
      />
      <StatCard
        :label="$t('overview.gpuUtil')"
        :value="formatPercent(resources.avgGpuUtil)"
        :hint="t('overview.deviceCount', { n: resources.resources?.gpus.length ?? 0 })"
        :icon="Gauge"
        :spark="resources.gpuHistory"
        color="var(--chart-1)"
      />
    </div>

    <!-- System topology — the live mission-control view of the whole stack -->
    <SystemTopology />

    <!-- GPUs -->
    <section v-if="resources.resources?.gpus.length">
      <div class="grid gap-4 lg:grid-cols-2">
        <GpuGauge v-for="g in resources.resources.gpus" :key="g.index" :gpu="g" />
      </div>
    </section>

    <div class="grid gap-6 lg:grid-cols-3">
      <!-- Model roster -->
      <Card class="lg:col-span-2">
        <CardHeader class="flex-row items-center justify-between">
          <CardTitle>{{ $t('overview.models') }}</CardTitle>
          <RouterLink to="/models" class="text-xs text-muted-foreground hover:text-foreground">
            {{ $t('common.manage') }}
          </RouterLink>
        </CardHeader>
        <CardContent class="space-y-1">
          <RouterLink
            v-for="m in models.models"
            :key="m.key"
            to="/models"
            class="flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-accent"
          >
            <StatusDot :state="m.state" />
            <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ m.key.split('::')[0] }}</span>
            <Badge variant="muted" class="shrink-0">{{ m.kind.toUpperCase() }}</Badge>
            <span class="hidden shrink-0 font-mono text-xs text-muted-foreground sm:inline"
              >{{ m.host }}:{{ m.port }}</span
            >
            <Badge :variant="m.state" class="shrink-0 capitalize">{{ m.state }}</Badge>
          </RouterLink>
          <p v-if="!models.total" class="py-6 text-center text-sm text-muted-foreground">
            {{ $t('overview.noModelsYet') }}
          </p>
        </CardContent>
      </Card>

      <!-- Recent activity -->
      <Card>
        <CardHeader class="flex-row items-center justify-between">
          <CardTitle class="flex items-center gap-1.5"><Activity class="size-4" />{{ $t('overview.activity') }}</CardTitle>
          <RouterLink to="/activity" class="text-xs text-muted-foreground hover:text-foreground">
            {{ $t('common.viewAll') }}
          </RouterLink>
        </CardHeader>
        <CardContent class="space-y-3">
          <div v-for="ev in events" :key="ev.id" class="flex items-start gap-2.5 text-sm">
            <StatusDot :state="ev.to_state" class="mt-1.5" />
            <div class="min-w-0">
              <p class="truncate">
                <span class="font-medium">{{ ev.key.split('::')[0] }}</span>
                <span class="text-muted-foreground"> {{ ev.from_state }} → {{ ev.to_state }}</span>
              </p>
              <p class="text-xs text-muted-foreground tabular">{{ formatTime(ev.ts) }}</p>
            </div>
          </div>
          <p v-if="!events.length" class="py-6 text-center text-sm text-muted-foreground">
            {{ $t('overview.noRecentEvents') }}
          </p>
        </CardContent>
      </Card>
    </div>

    <!-- Recent requests -->
    <Card>
      <CardHeader class="flex-row items-center justify-between">
        <CardTitle class="flex items-center gap-1.5"><Cpu class="size-4" />{{ $t('overview.recentRequests') }}</CardTitle>
        <RouterLink to="/traffic" class="text-xs text-muted-foreground hover:text-foreground">
          {{ $t('overview.traffic') }}
        </RouterLink>
      </CardHeader>
      <CardContent>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr class="border-b border-border/60">
                <th class="pb-2 pr-4 font-medium">{{ $t('overview.tableTime') }}</th>
                <th class="pb-2 pr-4 font-medium">{{ $t('overview.tableModel') }}</th>
                <th class="pb-2 pr-4 font-medium">{{ $t('overview.tablePath') }}</th>
                <th class="pb-2 pr-4 font-medium">{{ $t('overview.tableStatus') }}</th>
                <th class="pb-2 pr-4 text-right font-medium">{{ $t('overview.tableLatency') }}</th>
                <th class="pb-2 text-right font-medium">{{ $t('common.tokens') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="r in traffic.requests.slice(0, 8)"
                :key="r.id"
                class="border-b border-border/40 last:border-0"
              >
                <td class="py-2 pr-4 text-muted-foreground tabular">{{ formatTime(r.ts) }}</td>
                <td class="py-2 pr-4 font-medium">{{ r.model_key }}</td>
                <td class="py-2 pr-4 font-mono text-xs text-muted-foreground">{{ r.path }}</td>
                <td class="py-2 pr-4">
                  <Badge :variant="codeVariant(r.status_code) as any" class="tabular">{{ r.status_code }}</Badge>
                </td>
                <td class="py-2 pr-4 text-right tabular">{{ formatLatency(r.latency_ms) }}</td>
                <td class="py-2 text-right tabular text-muted-foreground">{{ r.total_tokens ?? '—' }}</td>
              </tr>
              <tr v-if="!traffic.requests.length">
                <td colspan="6" class="py-6 text-center text-muted-foreground">{{ $t('overview.noRequestRecords') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  </div>
</template>
