/** Shared types mirroring the Dashboard Backend / Router API (see docs/API.md). */

export type ModelKind = 'llm' | 'embedding'
export type ModelState = 'stopped' | 'starting' | 'ready' | 'failed' | 'stopping'
export type DesiredState = 'running' | 'stopped'

export interface ModelView {
  key: string
  kind: ModelKind
  model_tag: string | null
  host: string
  port: number
  state: ModelState
  desired: DesiredState
  managed: boolean
  pid: number | null
  last_error: string | null
  started_at: number | null
  ready_at: number | null
  updated_at: number | null
  restart_count?: number
}

export interface TimeseriesPoint {
  ts: number
  count: number
  error_count: number
  avg_latency_ms: number | null
  p95_latency_ms: number | null
  total_tokens: number
}

export interface MemoryInfo {
  total: number
  available: number
  percent: number
  used: number
  free: number
}

export interface GpuInfo {
  index: number
  name: string
  memory_used: number // MiB
  memory_total: number // MiB
  gpu_util: number // percent
}

export interface ResourcesView {
  cpu: number
  memory: MemoryInfo
  gpus: GpuInfo[]
}

export interface GpuProcess {
  pid: number
  nvidia_smi_name: string
  used_memory_mib: number
  exe?: string
  name?: string
  cmdline?: string[]
  username?: string
  error?: string
}

export interface StateEvent {
  id: number
  ts: number
  key: string
  kind: ModelKind
  from_state: ModelState
  to_state: ModelState
  detail: string | null
}

export interface UsageRow {
  model_key: string
  count: number
  error_count: number
  avg_latency_ms: number
  max_latency_ms: number
  total_tokens: number
  p50_latency_ms: number
  p95_latency_ms: number
}

export interface RequestRow {
  id: number
  ts: number
  model_key: string
  instance_id: string
  path: string
  status_code: number
  latency_ms: number
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  error: string | null
}

export interface LogResponse {
  key: string
  log_path: string
  content: string
}

export interface HealthZ {
  status: string
  models: Record<string, number>
}

export interface ConfigSummary {
  server: { host: string; port: number; uvicorn_log_level: string }
  LLM_engines: Record<
    string,
    {
      host: string
      port: number
      cuda_device: number
      max_model_len: number
      gpu_memory_utilization: number
      tool_parser: string
      /** Full vLLM model_config: declared fields + any extra flags. */
      settings: Record<string, string | number | boolean | null>
    }
  >
  embedding_server: {
    port: number
    cuda_device: number
    embedding_models: string[]
    reranking_models: string[]
  }
}

/** Per-instance vLLM metrics from Router `/metrics`, grouped by model group. */
export interface InstanceMetrics {
  base_url: string
  // null when the router's scrape of this instance failed (unreachable); the
  // router nulls these rather than reporting a misleading sentinel value.
  running: number | null
  waiting: number | null
  kv_cache_usage_perc: number | null
  prompt_tokens: number | null
  generation_tokens: number | null
}
export type RouterMetrics = Record<string, Record<string, InstanceMetrics>>

export interface OpenAIModelList {
  object: string
  data: { id: string; object: string }[]
}

export interface ApiKey {
  id: number
  name: string
  prefix: string
  created_at: number
  last_used_at: number | null
  revoked: number
}

/** Returned once on creation — `key` is the plaintext, shown only here. */
export interface CreatedKey {
  id: number
  name: string
  prefix: string
  key: string
}

export interface DiskUsage {
  total: number
  used: number
  free: number
}

export interface CachedModel {
  repo_id: string
  size_on_disk: number
  nb_files: number
  last_modified: number | null
  revisions: string[]
}

export interface CacheInfo {
  disk: DiskUsage
  models: CachedModel[]
}

export type DownloadState = 'pending' | 'downloading' | 'completed' | 'failed'

export interface DownloadJob {
  repo_id: string
  state: DownloadState
  total_bytes: number | null
  downloaded_bytes: number
  error: string | null
  started_at: number
  updated_at: number
}

export type SettingValue = string | number | boolean | null

export interface ParsedModel {
  group: string
  instance: { id: string; host: string; port: number; cuda_device: number | null }
  model_config: Record<string, SettingValue>
  warnings: string[]
  conflicts: { key_exists: boolean; port_in_use: boolean }
}

export interface CreateModelPayload {
  group: string
  instance: { id: string; host: string; port: number; cuda_device: number | null }
  settings: Record<string, SettingValue>
}
