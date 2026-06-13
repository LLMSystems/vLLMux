import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, ApiError } from '@/lib/api'
import type { ConfigSummary, ModelKind, ModelState, ModelView } from '@/types/api'

type ConnState = 'connecting' | 'live' | 'polling' | 'error'

/**
 * Single source of truth for model lifecycle state. Subscribes to the backend
 * SSE stream (`/api/stream/models`) for push updates and falls back to polling
 * if the stream drops. Start/stop apply optimistic transitions that the stream
 * then confirms or corrects.
 */
export const useModelsStore = defineStore('models', () => {
  const models = ref<ModelView[]>([])
  const conn = ref<ConnState>('connecting')
  const error = ref<string | null>(null)
  const lastUpdated = ref<number | null>(null)
  const pending = ref<Set<string>>(new Set()) // keys with an in-flight start/stop
  const config = ref<ConfigSummary | null>(null) // static deploy config (GPU, ctx len…)

  let source: EventSource | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null

  const byKey = computed(() => {
    const m = new Map<string, ModelView>()
    for (const model of models.value) m.set(model.key, model)
    return m
  })
  const llms = computed(() => models.value.filter((m) => m.kind === 'llm'))
  const embeddings = computed(() => models.value.filter((m) => m.kind === 'embedding'))
  const counts = computed(() => {
    const c: Record<ModelState, number> = {
      ready: 0,
      starting: 0,
      stopping: 0,
      failed: 0,
      stopped: 0,
    }
    for (const m of models.value) c[m.state]++
    return c
  })
  const readyCount = computed(() => counts.value.ready)
  const total = computed(() => models.value.length)
  const hasFailures = computed(() => counts.value.failed > 0)

  function applySnapshot(next: ModelView[]) {
    models.value = next
    lastUpdated.value = Date.now()
    // Clear pending markers once the stream reflects a settled state.
    for (const m of next) {
      if (pending.value.has(m.key) && m.state !== 'starting' && m.state !== 'stopping') {
        pending.value.delete(m.key)
      }
    }
  }

  /** Look up the static engine config (cuda_device, max_model_len…) for a key. */
  function engineConfig(key: string) {
    return config.value?.LLM_engines?.[key] ?? null
  }
  /** Configured GPU index for a model (LLM from its engine, embedding from the server). */
  function gpuForKey(key: string, kind: ModelKind): number | null {
    if (kind === 'embedding') return config.value?.embedding_server?.cuda_device ?? null
    return config.value?.LLM_engines?.[key]?.cuda_device ?? null
  }

  async function loadConfig() {
    try {
      config.value = await api.getConfig()
    } catch {
      /* config is best-effort; cards just omit GPU info if unavailable */
    }
  }

  function connect() {
    if (source) return
    void loadConfig()
    conn.value = 'connecting'
    try {
      source = new EventSource(api.modelStreamUrl())
    } catch {
      startPolling()
      return
    }
    source.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as ModelView[]
        if (Array.isArray(data)) {
          applySnapshot(data)
          conn.value = 'live'
          error.value = null
          stopPolling()
        }
      } catch {
        /* heartbeat or malformed frame — ignore */
      }
    }
    source.onerror = () => {
      conn.value = 'error'
      // EventSource auto-reconnects; meanwhile poll so the UI stays fresh.
      startPolling()
    }
  }

  function startPolling() {
    if (pollTimer) return
    conn.value = conn.value === 'live' ? 'live' : 'polling'
    void refresh()
    pollTimer = setInterval(() => void refresh(), 3000)
  }
  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function refresh() {
    try {
      applySnapshot(await api.listModels())
      if (conn.value !== 'live') conn.value = 'polling'
      error.value = null
    } catch (e) {
      conn.value = 'error'
      error.value = e instanceof ApiError ? e.message : 'Failed to load models'
    }
  }

  function disconnect() {
    source?.close()
    source = null
    stopPolling()
  }

  async function start(key: string) {
    pending.value.add(key)
    optimistic(key, 'starting')
    try {
      const view = await api.startModel(key)
      patch(view)
    } catch (e) {
      pending.value.delete(key)
      void refresh()
      throw e
    }
  }

  async function stop(key: string) {
    pending.value.add(key)
    optimistic(key, 'stopping')
    try {
      const view = await api.stopModel(key)
      patch(view)
    } catch (e) {
      pending.value.delete(key)
      void refresh()
      throw e
    }
  }

  function optimistic(key: string, state: ModelState) {
    const m = byKey.value.get(key)
    if (m) patch({ ...m, state })
  }
  function patch(view: ModelView) {
    const idx = models.value.findIndex((m) => m.key === view.key)
    if (idx >= 0) models.value.splice(idx, 1, view)
    else models.value.push(view)
  }

  return {
    models,
    conn,
    error,
    lastUpdated,
    pending,
    config,
    engineConfig,
    gpuForKey,
    loadConfig,
    byKey,
    llms,
    embeddings,
    counts,
    readyCount,
    total,
    hasFailures,
    connect,
    disconnect,
    refresh,
    start,
    stop,
  }
})
