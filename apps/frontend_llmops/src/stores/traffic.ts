import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/lib/api'
import type { RequestRow, RouterMetrics, UsageRow } from '@/types/api'

/** Router traffic: per-model usage rollups, recent request log, live metrics. */
export const useTrafficStore = defineStore('traffic', () => {
  const usage = ref<UsageRow[]>([])
  const requests = ref<RequestRow[]>([])
  const metrics = ref<RouterMetrics>({})
  const error = ref<string | null>(null)
  const filterModel = ref<string | null>(null)

  let timer: ReturnType<typeof setInterval> | null = null

  const totalRequests = computed(() => usage.value.reduce((s, u) => s + u.count, 0))
  const totalErrors = computed(() => usage.value.reduce((s, u) => s + u.error_count, 0))
  const errorRate = computed(() =>
    totalRequests.value ? (totalErrors.value / totalRequests.value) * 100 : 0,
  )
  const weightedP95 = computed(() => {
    if (!usage.value.length) return 0
    const num = usage.value.reduce((s, u) => s + u.p95_latency_ms * u.count, 0)
    const den = usage.value.reduce((s, u) => s + u.count, 0)
    return den ? num / den : 0
  })
  const totalTokens = computed(() => usage.value.reduce((s, u) => s + u.total_tokens, 0))

  async function refresh() {
    try {
      const [u, r, m] = await Promise.all([
        api.getUsage(),
        api.getRequests({ modelKey: filterModel.value ?? undefined, limit: 100 }),
        api.routerMetrics().catch(() => ({}) as RouterMetrics), // router may be down
      ])
      usage.value = u
      requests.value = r
      metrics.value = m
      error.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load traffic'
    }
  }

  async function setFilter(modelKey: string | null) {
    filterModel.value = modelKey
    requests.value = await api.getRequests({ modelKey: modelKey ?? undefined, limit: 100 })
  }

  function start(intervalMs = 5000) {
    if (timer) return
    void refresh()
    timer = setInterval(() => void refresh(), intervalMs)
  }
  function stop() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  return {
    usage,
    requests,
    metrics,
    error,
    filterModel,
    totalRequests,
    totalErrors,
    errorRate,
    weightedP95,
    totalTokens,
    refresh,
    setFilter,
    start,
    stop,
  }
})
