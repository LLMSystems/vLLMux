import type {
  ApiKey,
  CacheInfo,
  ConfigSummary,
  CreatedKey,
  CreateModelPayload,
  DownloadJob,
  GpuProcess,
  HealthZ,
  LogResponse,
  ModelView,
  OpenAIModelList,
  ParsedModel,
  RequestRow,
  ResourcesView,
  RouterMetrics,
  StateEvent,
  TimeseriesPoint,
  UsageRow,
} from '@/types/api'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:5000'
const ROUTER_BASE = import.meta.env.VITE_ROUTER_BASE_URL ?? 'http://localhost:8887'

// ---- Admin token --------------------------------------------------------
// Gates backend writes (sent as X-Admin-Token) and authenticates the dashboard
// against the router for inference (sent as Authorization: Bearer). Persisted so
// it survives reloads; empty when auth is disabled or the operator hasn't unlocked.
const ADMIN_TOKEN_KEY = 'llmops_admin_token'
let adminToken = (typeof localStorage !== 'undefined' && localStorage.getItem(ADMIN_TOKEN_KEY)) || ''

export function setAdminToken(token: string) {
  adminToken = token
  try {
    localStorage.setItem(ADMIN_TOKEN_KEY, token)
  } catch {
    /* private mode / no storage — keep the in-memory value */
  }
}
export function clearAdminToken() {
  adminToken = ''
  try {
    localStorage.removeItem(ADMIN_TOKEN_KEY)
  } catch {
    /* ignore */
  }
}
export function getAdminToken() {
  return adminToken
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${base}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(adminToken ? { 'X-Admin-Token': adminToken } : {}),
        ...init?.headers,
      },
    })
  } catch (e) {
    throw new ApiError(0, `Network error reaching ${base}${path}`, e)
  }
  const text = await res.text()
  const data = text ? safeJson(text) : null
  if (!res.ok) {
    let detail = res.statusText
    if (data && typeof data === 'object' && 'detail' in data) {
      detail = String((data as Record<string, unknown>).detail)
    }
    throw new ApiError(res.status, detail, data)
  }
  return data as T
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

/** `key` may contain `::`; encodeURIComponent makes it URL-safe (%3A%3A). */
const enc = (key: string) => encodeURIComponent(key)

export const api = {
  base: API_BASE,
  routerBase: ROUTER_BASE,

  // ---- Dashboard Backend ----------------------------------------------------
  listModels: () => request<ModelView[]>(API_BASE, '/api/models'),
  getModel: (key: string) => request<ModelView>(API_BASE, `/api/models/${enc(key)}`),
  startModel: (key: string, force = false) =>
    request<ModelView>(API_BASE, `/api/models/${enc(key)}/start${force ? '?force=true' : ''}`, {
      method: 'POST',
    }),
  stopModel: (key: string) =>
    request<ModelView>(API_BASE, `/api/models/${enc(key)}/stop`, { method: 'POST' }),
  parseCommand: (command: string) =>
    request<ParsedModel>(API_BASE, '/api/models/parse', {
      method: 'POST',
      body: JSON.stringify({ command }),
    }),
  createModel: (payload: CreateModelPayload) =>
    request<ModelView>(API_BASE, '/api/models', { method: 'POST', body: JSON.stringify(payload) }),
  updateModel: (key: string, payload: CreateModelPayload) =>
    request<ModelView>(API_BASE, `/api/models/${enc(key)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deleteModel: (key: string) =>
    request<null>(API_BASE, `/api/models/${enc(key)}`, { method: 'DELETE' }),
  getConfig: () => request<ConfigSummary>(API_BASE, '/api/config'),
  getResources: () => request<ResourcesView>(API_BASE, '/api/resources'),
  getGpuProcesses: () => request<GpuProcess[]>(API_BASE, '/api/gpu/processes'),
  getEvents: (limit = 100) => request<StateEvent[]>(API_BASE, `/api/events?limit=${limit}`),
  getModelEvents: (key: string, limit = 50) =>
    request<StateEvent[]>(API_BASE, `/api/models/${enc(key)}/events?limit=${limit}`),
  getUsage: (since?: number) =>
    request<UsageRow[]>(API_BASE, `/api/usage${since ? `?since=${since}` : ''}`),
  getRequests: (opts: { modelKey?: string; limit?: number } = {}) => {
    const params = new URLSearchParams()
    if (opts.modelKey) params.set('model_key', opts.modelKey)
    params.set('limit', String(opts.limit ?? 100))
    return request<RequestRow[]>(API_BASE, `/api/requests?${params.toString()}`)
  },
  getLogs: (key: string, tail = 200) =>
    request<LogResponse>(API_BASE, `/api/models/${enc(key)}/logs?tail=${tail}`),
  getTimeseries: (opts: { window?: number; bucket?: number; modelKey?: string } = {}) => {
    const params = new URLSearchParams()
    params.set('window', String(opts.window ?? 3600))
    params.set('bucket', String(opts.bucket ?? 60))
    if (opts.modelKey) params.set('model_key', opts.modelKey)
    return request<TimeseriesPoint[]>(API_BASE, `/api/metrics/timeseries?${params.toString()}`)
  },
  healthz: () => request<HealthZ>(API_BASE, '/healthz'),

  // ---- LLM Router -----------------------------------------------------------
  routerModels: () => request<OpenAIModelList>(ROUTER_BASE, '/v1/models'),
  routerMetrics: () => request<RouterMetrics>(ROUTER_BASE, '/metrics'),

  /** SSE endpoint URL for the live model snapshot stream. */
  modelStreamUrl: () => `${API_BASE}/api/stream/models`,

  /** Raw fetch against the Router for inference. Attaches the admin token as a
   *  bearer so the dashboard keeps working when the router enforces API keys. */
  routerFetch: (path: string, init?: RequestInit) =>
    fetch(`${ROUTER_BASE}${path}`, {
      ...init,
      headers: {
        ...(adminToken ? { Authorization: `Bearer ${adminToken}` } : {}),
        ...init?.headers,
      },
    }),

  // ---- Auth + API keys ------------------------------------------------------
  authStatus: () => request<{ auth_enabled: boolean }>(API_BASE, '/api/auth/status'),
  /** Validate a candidate admin token (not the stored one). */
  authVerify: async (token: string): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/verify`, {
        method: 'POST',
        headers: { 'X-Admin-Token': token },
      })
      return res.ok
    } catch {
      return false
    }
  },
  // ---- Model weights --------------------------------------------------------
  getCache: () => request<CacheInfo>(API_BASE, '/api/cache'),
  deleteCache: (repoId: string) =>
    request<null>(API_BASE, `/api/cache/${repoId}`, { method: 'DELETE' }),
  listDownloads: () => request<DownloadJob[]>(API_BASE, '/api/downloads'),
  startDownload: (repoId: string) =>
    request<DownloadJob>(API_BASE, '/api/downloads', {
      method: 'POST',
      body: JSON.stringify({ repo_id: repoId }),
    }),

  listKeys: () => request<ApiKey[]>(API_BASE, '/api/keys'),
  createKey: (name: string) =>
    request<CreatedKey>(API_BASE, '/api/keys', { method: 'POST', body: JSON.stringify({ name }) }),
  revokeKey: (id: number) => request<null>(API_BASE, `/api/keys/${id}`, { method: 'DELETE' }),

  /** Best-effort: ask the Router to re-read config + overlay so newly-added
   *  models become routable. Returns false if the router is unreachable. */
  routerReload: async (): Promise<boolean> => {
    try {
      const res = await fetch(`${ROUTER_BASE}/reload`, { method: 'POST' })
      return res.ok
    } catch {
      return false
    }
  },
}
