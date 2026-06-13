import axios from 'axios'

// Single axios client for the dashboard backend. Replaces the 8 raw
// `fetch(${VITE_API_BASE_URL}/api/...)` calls that were scattered across the
// components.
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
})

export const getConfig = () => api.get('/api/config').then((r) => r.data)
export const getResources = () => api.get('/api/resources').then((r) => r.data)
export const getStatusAll = () => api.get('/api/status/all').then((r) => r.data)
export const getGpuProcesses = () => api.get('/api/gpu/processes').then((r) => r.data)

export const startLlm = (name) => api.post(`/api/llm/start/${name}`).then((r) => r.data)
export const stopLlm = (name) => api.post(`/api/llm/stop/${name}`).then((r) => r.data)
export const startEmbedding = () => api.post('/api/embedding/start').then((r) => r.data)
export const stopEmbedding = () => api.post('/api/embedding/stop').then((r) => r.data)

export default api
