import { ref } from 'vue'
import { defineStore } from 'pinia'

import * as api from '@/services/api'

// Owns all dashboard data + polling that previously lived inline in
// ModelList.vue. Components read these refs and call the actions; no component
// talks to the network directly anymore.
export const useModelsStore = defineStore('models', () => {
  const server = ref({})
  const llmModels = ref([])
  const embeddingServer = ref({})
  const resources = ref(null)
  const gpuProcesses = ref([])

  let timers = []

  async function loadConfig() {
    const config = await api.getConfig()
    server.value = config.server

    llmModels.value = Object.entries(config.LLM_engines).map(([name, cfg]) => ({
      name,
      port: cfg.port,
      cuda_device: cfg.cuda_device,
      max_model_len: cfg.max_model_len,
      gpu_memory_utilization: cfg.gpu_memory_utilization,
      tool_parser: cfg.tool_parser || cfg.reasoning_parser || 'unknown',
      status: '未啟動',
    }))

    embeddingServer.value = {
      name: 'Embedding & reranking Server',
      port: config.embedding_server.port,
      cuda_device: config.embedding_server.cuda_device,
      embedding_models: config.embedding_server.embedding_models,
      reranking_models: config.embedding_server.reranking_models,
      status: '未啟動',
    }
  }

  async function refreshStatus() {
    try {
      const data = await api.getStatusAll()
      const statusMap = Object.fromEntries(data.models.map((m) => [m.name, m.status]))
      llmModels.value.forEach((model) => {
        if (statusMap[model.name]) model.status = statusMap[model.name]
      })
      if (statusMap['Embedding & reranking Server']) {
        embeddingServer.value.status = statusMap['Embedding & reranking Server']
      }
    } catch (err) {
      console.error('Error fetching model status:', err)
    }
  }

  async function refreshResources() {
    try {
      resources.value = await api.getResources()
    } catch (err) {
      console.warn('無法取得系統資源資訊', err)
    }
  }

  async function refreshGpuProcesses() {
    try {
      const data = await api.getGpuProcesses()
      gpuProcesses.value = Array.isArray(data) ? data : []
    } catch (err) {
      console.error('無法取得 GPU 占用程序資訊', err)
    }
  }

  async function startPolling() {
    await loadConfig()
    await Promise.all([refreshStatus(), refreshResources(), refreshGpuProcesses()])
    timers.push(setInterval(refreshStatus, 5000))
    timers.push(setInterval(refreshResources, 1000))
    timers.push(setInterval(refreshGpuProcesses, 5000))
  }

  function stopPolling() {
    timers.forEach(clearInterval)
    timers = []
  }

  async function startLLM(name) {
    const model = llmModels.value.find((m) => m.name === name)
    if (!model) return
    model.status = '啟動中'
    try {
      const data = await api.startLlm(name)
      model.status = data.status || '啟動中'
    } catch (err) {
      model.status = '啟動失敗'
      console.error('啟動失敗', err)
    }
  }

  async function stopLLM(name) {
    const model = llmModels.value.find((m) => m.name === name)
    if (!model) return
    model.status = '關閉中'
    try {
      const data = await api.stopLlm(name)
      model.status = data.status || '未啟動'
    } catch (err) {
      model.status = '未知錯誤'
      console.error('關閉失敗', err)
    }
  }

  async function startEmbedding() {
    embeddingServer.value.status = '啟動中'
    try {
      const data = await api.startEmbedding()
      embeddingServer.value.status = data.status || '啟動中'
    } catch (err) {
      embeddingServer.value.status = '啟動失敗'
      console.error('啟動失敗', err)
    }
  }

  async function stopEmbedding() {
    embeddingServer.value.status = '關閉中'
    try {
      const data = await api.stopEmbedding()
      embeddingServer.value.status = data.status || '未啟動'
    } catch (err) {
      embeddingServer.value.status = '未知錯誤'
      console.error('關閉失敗', err)
    }
  }

  return {
    server,
    llmModels,
    embeddingServer,
    resources,
    gpuProcesses,
    startPolling,
    stopPolling,
    startLLM,
    stopLLM,
    startEmbedding,
    stopEmbedding,
  }
})
