<script setup lang="ts">
import { computed } from 'vue'
import { Cpu, MemoryStick } from '@lucide/vue'
import { useResourcesStore } from '@/stores/resources'
import Card from '@/components/ui/Card.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import CardTitle from '@/components/ui/CardTitle.vue'
import CardContent from '@/components/ui/CardContent.vue'
import Sparkline from '@/components/Sparkline.vue'
import GpuGauge from '@/components/GpuGauge.vue'
import { formatBytes, formatPercent } from '@/lib/utils'

const resources = useResourcesStore()
const mem = computed(() => resources.resources?.memory)

function shortCmd(cmdline?: string[]) {
  if (!cmdline?.length) return '—'
  return cmdline.join(' ').replace(/^.*?(python|vllm)/, '$1').slice(0, 80)
}
</script>

<template>
  <div class="space-y-6 p-6">
    <!-- CPU + Memory -->
    <div class="grid gap-4 md:grid-cols-2">
      <Card glass class="p-5">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <Cpu class="size-4 text-[var(--chart-1)]" />
            <span class="text-sm font-medium">{{ $t('resources.cpu') }}</span>
          </div>
          <span class="text-2xl font-semibold tabular">{{ formatPercent(resources.resources?.cpu) }}</span>
        </div>
        <div class="mt-2 text-[var(--chart-1)]">
          <Sparkline :data="resources.cpuHistory" :width="600" :height="48" :max="100" />
        </div>
      </Card>

      <Card glass class="p-5">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <MemoryStick class="size-4 text-[var(--chart-2)]" />
            <span class="text-sm font-medium">{{ $t('resources.memory') }}</span>
          </div>
          <span class="text-2xl font-semibold tabular">{{ formatPercent(mem?.percent) }}</span>
        </div>
        <div class="mt-2 text-[var(--chart-2)]">
          <Sparkline :data="resources.memHistory" :width="600" :height="48" :max="100" />
        </div>
        <p v-if="mem" class="mt-2 text-xs text-muted-foreground tabular">
          {{ $t('resources.memorySummary', {
            used: formatBytes(mem.used),
            total: formatBytes(mem.total),
            available: formatBytes(mem.available),
          }) }}
        </p>
      </Card>
    </div>

    <!-- GPUs -->
    <div v-if="resources.resources?.gpus.length" class="grid gap-4 lg:grid-cols-2">
      <GpuGauge v-for="g in resources.resources.gpus" :key="g.index" :gpu="g" />
    </div>

    <!-- GPU processes -->
    <Card>
      <CardHeader>
        <CardTitle>{{ $t('resources.gpuProcesses') }}</CardTitle>
        <p class="text-xs text-muted-foreground">{{ $t('resources.gpuProcessesDesc') }}</p>
      </CardHeader>
      <CardContent>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr class="border-b border-border/60">
                <th class="pb-2 pr-4 font-medium">{{ $t('resources.tablePid') }}</th>
                <th class="pb-2 pr-4 font-medium">{{ $t('resources.tableUser') }}</th>
                <th class="pb-2 pr-4 font-medium">{{ $t('resources.tableName') }}</th>
                <th class="pb-2 pr-4 font-medium">{{ $t('resources.tableCommand') }}</th>
                <th class="pb-2 text-right font-medium">{{ $t('resources.tableGpuMem') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="p in resources.processes"
                :key="p.pid"
                class="border-b border-border/40 last:border-0"
              >
                <td class="py-2 pr-4 font-mono tabular">{{ p.pid }}</td>
                <td class="py-2 pr-4 text-muted-foreground">{{ p.username ?? '—' }}</td>
                <td class="py-2 pr-4">{{ p.name ?? p.nvidia_smi_name }}</td>
                <td class="py-2 pr-4 font-mono text-xs text-muted-foreground" :title="p.cmdline?.join(' ')">
                  <span v-if="p.error" class="text-status-failed">{{ p.error }}</span>
                  <span v-else>{{ shortCmd(p.cmdline) }}</span>
                </td>
                <td class="py-2 text-right font-medium tabular">{{ formatBytes(p.used_memory_mib, true) }}</td>
              </tr>
              <tr v-if="!resources.processes.length">
                <td colspan="5" class="py-6 text-center text-muted-foreground">{{ $t('resources.noGpuProcesses') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  </div>
</template>
