<script setup lang="ts">
import { computed } from 'vue'
import { RefreshCw } from '@lucide/vue'
import { useTrafficStore } from '@/stores/traffic'
import { useModelsStore } from '@/stores/models'
import Card from '@/components/ui/Card.vue'
import CardHeader from '@/components/ui/CardHeader.vue'
import CardTitle from '@/components/ui/CardTitle.vue'
import CardContent from '@/components/ui/CardContent.vue'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import RouterFanDiagram from '@/components/RouterFanDiagram.vue'
import { formatLatency, formatNumber } from '@/lib/utils'

const traffic = useTrafficStore()
const models = useModelsStore()

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
        <CardTitle>各模型用量</CardTitle>
        <Button variant="outline" size="sm" @click="traffic.refresh()"><RefreshCw class="size-3.5" />重新整理</Button>
      </CardHeader>
      <CardContent>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr class="border-b border-border/60">
                <th class="pb-2 pr-4 font-medium">模型</th>
                <th class="pb-2 pr-4 text-right font-medium">請求次數</th>
                <th class="pb-2 pr-4 text-right font-medium">錯誤數</th>
                <th class="pb-2 pr-4 text-right font-medium">平均</th>
                <th class="pb-2 pr-4 text-right font-medium">p50</th>
                <th class="pb-2 pr-4 font-medium">p95</th>
                <th class="pb-2 text-right font-medium">Tokens</th>
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
                <td colspan="7" class="py-6 text-center text-muted-foreground">尚無用量記錄。</td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>

    <!-- Router load: load-balancing fan per group -->
    <Card>
      <CardHeader>
        <CardTitle>路由器負載均衡</CardTitle>
        <p class="text-xs text-muted-foreground">
          線條粗細 = 實際流量佔比（來自請求記錄）· ★ = 路由器下次選擇的最低分實例
        </p>
      </CardHeader>
      <CardContent class="space-y-3">
        <RouterFanDiagram v-for="g in llmGroups" :key="g" :group="g" />
        <p v-if="!llmGroups.length" class="py-6 text-center text-sm text-muted-foreground">
          尚未設定 LLM 群組。
        </p>
      </CardContent>
    </Card>

    <!-- Request log -->
    <Card>
      <CardHeader class="flex-row items-center justify-between">
        <CardTitle>請求記錄</CardTitle>
        <select
          class="h-8 rounded-md border border-input bg-background/40 px-2 text-xs"
          :value="traffic.filterModel ?? ''"
          @change="onFilter"
        >
          <option value="">全部模型</option>
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
                <th class="pb-2 pr-3 font-medium">模型</th>
                <th class="pb-2 pr-3 font-medium">路徑</th>
                <th class="pb-2 pr-3 font-medium">狀態</th>
                <th class="pb-2 pr-3 text-right font-medium">延遲</th>
                <th class="pb-2 text-right font-medium">Tok</th>
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
                <td colspan="5" class="py-6 text-center text-muted-foreground">尚無請求。</td>
              </tr>
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  </div>
</template>
