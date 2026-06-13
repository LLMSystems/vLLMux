import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api } from '@/lib/api'
import type { GpuProcess, ResourcesView } from '@/types/api'

const HISTORY = 40 // points kept for sparklines

/** System resources (CPU/mem/GPU) + GPU process table, polled on an interval. */
export const useResourcesStore = defineStore('resources', () => {
  const resources = ref<ResourcesView | null>(null)
  const processes = ref<GpuProcess[]>([])
  const error = ref<string | null>(null)

  const cpuHistory = ref<number[]>([])
  const memHistory = ref<number[]>([])
  const gpuHistory = ref<number[]>([]) // averaged GPU util across cards

  let timer: ReturnType<typeof setInterval> | null = null

  const avgGpuUtil = computed(() => {
    const g = resources.value?.gpus ?? []
    if (!g.length) return 0
    return g.reduce((s, x) => s + x.gpu_util, 0) / g.length
  })

  function push(arr: number[], v: number) {
    arr.push(v)
    if (arr.length > HISTORY) arr.shift()
  }

  async function refresh() {
    try {
      const [res, procs] = await Promise.all([api.getResources(), api.getGpuProcesses()])
      resources.value = res
      processes.value = procs
      push(cpuHistory.value, res.cpu)
      push(memHistory.value, res.memory.percent)
      push(gpuHistory.value, avgGpuUtil.value)
      error.value = null
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load resources'
    }
  }

  function start(intervalMs = 3000) {
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
    resources,
    processes,
    error,
    cpuHistory,
    memHistory,
    gpuHistory,
    avgGpuUtil,
    refresh,
    start,
    stop,
  }
})
