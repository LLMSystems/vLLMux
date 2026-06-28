import type {
  ApiKey,
  AuditEntry,
  CacheInfo,
  ConfigSummary,
  CreatedKey,
  CreatedOperator,
  CreateModelPayload,
  Me,
  Operator,
  Role,
  DatasetCacheInfo,
  DatasetDownloadJob,
  DownloadJob,
  EvalConfig,
  AutoscaleConfig,
  EvalDataset,
  EvalDatasetPreview,
  GroupLoad,
  QuotaPeriod,
  EvalListResponse,
  EvalReportDataset,
  EvalRequest,
  EvalRun,
  EvalSampleDetail,
  EvalSamplesPage,
  GpuProcess,
  HealthZ,
  LogResponse,
  LoraDownloadJob,
  LoraLibraryInfo,
  ModelStartupMetrics,
  ModelView,
  OpenAIModelList,
  ParsedModel,
  PerfRequest,
  PerfRun,
  RequestRow,
  ResourcesView,
  RouterMetrics,
  RoutingInfo,
  SettingValue,
  StateEvent,
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
  /** Set/clear a group's cross-model fallback chain (no stop required). */
  setFallback: (group: string, fallback: string[]) =>
    request<{ group: string; fallback: string[] | null }>(
      API_BASE,
      `/api/models/${enc(group)}/fallback`,
      { method: 'PUT', body: JSON.stringify({ fallback }) },
    ),
  /** Enable/disable + tune a group's autoscaling (no stop required). */
  setAutoscale: (
    group: string,
    body: {
      enabled: boolean
      min_ready?: number
      max_ready?: number | null
      min_warm?: number | null
      scale_up_waiting?: number | null
      scale_up_window_s?: number | null
      sleep_after_s?: number | null
      stop_after_s?: number | null
      cooldown_s?: number | null
    },
  ) =>
    request<{ group: string; autoscale: AutoscaleConfig | null }>(
      API_BASE,
      `/api/models/${enc(group)}/autoscale`,
      { method: 'PUT', body: JSON.stringify(body) },
    ),
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
  updateEmbeddingModel: (
    modelType: 'embedding' | 'reranking',
    name: string,
    settings: Record<string, SettingValue>,
  ) =>
    request<{ ok: boolean }>(API_BASE, '/api/embedding/models', {
      method: 'PUT',
      body: JSON.stringify({ model_type: modelType, name, settings }),
    }),
  getResources: () => request<ResourcesView>(API_BASE, '/api/resources'),
  getGpuProcesses: () => request<GpuProcess[]>(API_BASE, '/api/gpu/processes'),
  getEvents: (limit = 100) => request<StateEvent[]>(API_BASE, `/api/events?limit=${limit}`),
  getModelEvents: (key: string, limit = 50) =>
    request<StateEvent[]>(API_BASE, `/api/models/${enc(key)}/events?limit=${limit}`),
  getUsage: (since?: number) =>
    request<UsageRow[]>(API_BASE, `/api/usage${since ? `?since=${since}` : ''}`),
  /** Per-group live load (queue depth, ready/asleep counts) for the autoscaling signal. */
  groupLoad: () => request<Record<string, GroupLoad>>(API_BASE, '/api/load'),
  getRequests: (opts: { modelKey?: string; limit?: number } = {}) => {
    const params = new URLSearchParams()
    if (opts.modelKey) params.set('model_key', opts.modelKey)
    params.set('limit', String(opts.limit ?? 100))
    return request<RequestRow[]>(API_BASE, `/api/requests?${params.toString()}`)
  },
  getLogs: (key: string, tail = 200) =>
    request<LogResponse>(API_BASE, `/api/models/${enc(key)}/logs?tail=${tail}`),
  getModelMetrics: (key: string) =>
    request<ModelStartupMetrics>(API_BASE, `/api/models/${enc(key)}/metrics`),
  healthz: () => request<HealthZ>(API_BASE, '/healthz'),

  // ---- LLM Router -----------------------------------------------------------
  routerModels: () => request<OpenAIModelList>(ROUTER_BASE, '/v1/models'),
  routerMetrics: () => request<RouterMetrics>(ROUTER_BASE, '/metrics'),

  /** Current global load-balancing strategy + the selectable catalogue. */
  getRouting: () => request<RoutingInfo>(ROUTER_BASE, '/routing'),
  /** Hot-swap the global strategy (effective next request; not persisted). */
  setRouting: (strategy: string) =>
    request<{ strategy: string }>(ROUTER_BASE, '/routing', {
      method: 'POST',
      body: JSON.stringify({ strategy }),
    }),

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
  /** The caller's resolved identity + role (drives chrome + permission gating). */
  whoami: () => request<Me>(API_BASE, '/api/me'),
  /** Resolve a candidate token's identity without storing it (for login). */
  whoamiWith: async (token: string): Promise<Me | null> => {
    try {
      const res = await fetch(`${API_BASE}/api/me`, {
        headers: { 'X-Admin-Token': token },
      })
      return res.ok ? ((await res.json()) as Me) : null
    } catch {
      return null
    }
  },

  // ---- Operators (control-plane users) -------------------------------------
  listOperators: () => request<Operator[]>(API_BASE, '/api/operators'),
  createOperator: (label: string, role: Role) =>
    request<CreatedOperator>(API_BASE, '/api/operators', {
      method: 'POST',
      body: JSON.stringify({ label, role }),
    }),
  revokeOperator: (id: number) =>
    request<null>(API_BASE, `/api/operators/${id}`, { method: 'DELETE' }),

  // ---- Audit log -----------------------------------------------------------
  listAudit: (params?: { actor?: string; action?: string; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.actor) q.set('actor', params.actor)
    if (params?.action) q.set('action', params.action)
    if (params?.limit) q.set('limit', String(params.limit))
    const qs = q.toString()
    return request<AuditEntry[]>(API_BASE, `/api/audit${qs ? `?${qs}` : ''}`)
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

  // ---- Runtime (hot) LoRA load / unload on a running model ------------------
  loadLora: (key: string, body: { name: string; path: string; base_model_name?: string }) =>
    request<{ group: string; name: string; instances: string[] }>(
      API_BASE,
      `/api/models/${key}/lora`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  unloadLora: (key: string, name: string) =>
    request<{ group: string; name: string; instances: string[]; errors: string[] }>(
      API_BASE,
      `/api/models/${key}/lora/${name}`,
      { method: 'DELETE' },
    ),

  // ---- LoRA adapter library -------------------------------------------------
  listLora: () => request<LoraLibraryInfo>(API_BASE, '/api/lora'),
  deleteLora: (name: string) =>
    request<null>(API_BASE, `/api/lora/${name}`, { method: 'DELETE' }),
  listLoraDownloads: () => request<LoraDownloadJob[]>(API_BASE, '/api/lora/downloads'),
  startLoraDownload: (repoId: string, name?: string) =>
    request<LoraDownloadJob>(API_BASE, '/api/lora/downloads', {
      method: 'POST',
      body: JSON.stringify({ repo_id: repoId, name }),
    }),

  // ---- Benchmark datasets (ModelScope cache) --------------------------------
  getDatasets: () => request<DatasetCacheInfo>(API_BASE, '/api/datasets'),
  listDatasetDownloads: () => request<DatasetDownloadJob[]>(API_BASE, '/api/datasets/downloads'),
  startDatasetDownload: (key: string) =>
    request<DatasetDownloadJob>(API_BASE, '/api/datasets/download', {
      method: 'POST',
      body: JSON.stringify({ key }),
    }),
  deleteDataset: (key: string) =>
    request<null>(API_BASE, `/api/datasets/${key}`, { method: 'DELETE' }),

  // ---- Load testing (perf) --------------------------------------------------
  listPerf: () => request<{ busy: boolean; runs: PerfRun[] }>(API_BASE, '/api/perf'),
  startPerf: (body: PerfRequest) =>
    request<PerfRun>(API_BASE, '/api/perf', { method: 'POST', body: JSON.stringify(body) }),
  getPerf: (id: number) => request<PerfRun>(API_BASE, `/api/perf/${id}`),
  getPerfLog: (id: number, tail = 200) =>
    request<{ content: string }>(API_BASE, `/api/perf/${id}/log?tail=${tail}`),
  cancelPerf: (id: number, force = false) =>
    request<{ ok: boolean }>(API_BASE, `/api/perf/${id}/cancel${force ? '?force=true' : ''}`, {
      method: 'POST',
    }),
  deletePerf: (id: number) => request<null>(API_BASE, `/api/perf/${id}`, { method: 'DELETE' }),
  /** URL of the full evalscope HTML report (open in a new tab). */
  perfReportUrl: (id: number) => `${API_BASE}/api/perf/${id}/report`,

  // ---- Accuracy / quality evaluation (eval) ---------------------------------
  listEvalDatasets: () => request<{ datasets: EvalDataset[] }>(API_BASE, '/api/eval/datasets'),
  getEvalDatasetPreview: (key: string, subset?: string, n = 20) => {
    const p = new URLSearchParams({ n: String(n) })
    if (subset) p.set('subset', subset)
    return request<EvalDatasetPreview>(API_BASE, `/api/eval/datasets/${key}/preview?${p.toString()}`)
  },
  listEval: () => request<EvalListResponse>(API_BASE, '/api/eval'),
  getEvalConfig: () => request<EvalConfig>(API_BASE, '/api/eval/config'),
  setEvalConfig: (concurrency_budget: number) =>
    request<EvalConfig>(API_BASE, '/api/eval/config', {
      method: 'PATCH',
      body: JSON.stringify({ concurrency_budget }),
    }),
  startEval: (body: EvalRequest) =>
    request<EvalRun>(API_BASE, '/api/eval', { method: 'POST', body: JSON.stringify(body) }),
  getEval: (id: number) => request<EvalRun>(API_BASE, `/api/eval/${id}`),
  getEvalLog: (id: number, tail = 200) =>
    request<{ content: string }>(API_BASE, `/api/eval/${id}/log?tail=${tail}`),
  // Rich detail (per-dataset speed/subsets/description), loaded lazily on open.
  getEvalDetail: (id: number) =>
    request<{ datasets: EvalReportDataset[] }>(API_BASE, `/api/eval/${id}/detail`),
  // Paginated, server-filtered per-sample rows (compact — no full text).
  getEvalSamples: (
    id: number,
    dataset: string,
    opts: { filter?: 'all' | 'correct' | 'wrong'; page?: number; pageSize?: number } = {},
  ) => {
    const q = new URLSearchParams({
      dataset,
      filter: opts.filter ?? 'all',
      page: String(opts.page ?? 1),
      page_size: String(opts.pageSize ?? 50),
    })
    return request<EvalSamplesPage>(API_BASE, `/api/eval/${id}/samples?${q}`)
  },
  // Full detail for one sample (prompt + answer + target + scores + perf).
  getEvalSample: (id: number, dataset: string, index: number) =>
    request<EvalSampleDetail>(
      API_BASE,
      `/api/eval/${id}/samples/${index}?dataset=${encodeURIComponent(dataset)}`,
    ),
  cancelEval: (id: number) =>
    request<{ ok: boolean }>(API_BASE, `/api/eval/${id}/cancel`, { method: 'POST' }),
  deleteEval: (id: number) => request<null>(API_BASE, `/api/eval/${id}`, { method: 'DELETE' }),
  /** URL of the full evalscope HTML report (open in a new tab). */
  evalReportUrl: (id: number) => `${API_BASE}/api/eval/${id}/report`,

  listKeys: () => request<ApiKey[]>(API_BASE, '/api/keys'),
  createKey: (
    name: string,
    rpmLimit?: number | null,
    tokenQuota?: number | null,
    quotaPeriod: QuotaPeriod = 'total',
  ) =>
    request<CreatedKey>(API_BASE, '/api/keys', {
      method: 'POST',
      body: JSON.stringify({
        name,
        rpm_limit: rpmLimit ?? null,
        token_quota: tokenQuota ?? null,
        quota_period: quotaPeriod,
      }),
    }),
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
