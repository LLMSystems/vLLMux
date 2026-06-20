/** Shared types mirroring the Dashboard Backend / Router API (see docs/API.md). */

export type ModelKind = 'llm' | 'embedding'
export type ModelState = 'stopped' | 'starting' | 'ready' | 'failed' | 'stopping'
export type DesiredState = 'running' | 'stopped'

// vLLM startup capacity/memory/compile metrics parsed from the engine log.
// Fields are individually nullable (a missing log line yields null).
export interface ModelStartupMetrics {
  ready: boolean
  has_any: boolean
  capacity?: {
    kv_cache_tokens: number | null
    max_concurrency: number | null
    concurrency_req_tokens: number | null
    kv_cache_gib: number | null
  }
  memory?: { model_gib: number | null; cudagraph_gib: number | null; kv_cache_gib: number | null }
  startup?: {
    weights_load_s: number | null
    model_load_s: number | null
    compile_s: number | null
    warmup_s: number | null
  }
  gpu_mem_util?: { current: number | null; effective: number | null; suggested: number | null }
}

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
  api_key_name: string | null
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
      settings: Record<string, string | number | boolean | null> & {
        lora_modules?: LoraModule[]
      }
    }
  >
  embedding_server: {
    host: string
    port: number
    cuda_device: number
    embedding_models: Record<string, EmbeddingModelParams>
    reranking_models: Record<string, EmbeddingModelParams>
  }
}

/** Per embedding/reranking model params (declared + any extra keys). */
export interface EmbeddingModelParams {
  model_name?: string
  model_path?: string
  tokenizer_path?: string
  max_length?: number
  use_gpu?: boolean
  use_float16?: boolean
  [key: string]: string | number | boolean | null | undefined
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

/** Global load-balancing strategy state from the router's GET /routing. */
export interface RoutingInfo {
  strategy: string
  available: string[]
  default: string
}

export interface OpenAIModelList {
  object: string
  // `parent` is present only for LoRA adapters (points at the base group).
  data: { id: string; object: string; parent?: string }[]
}

/** A LoRA adapter statically mounted on a base model group. */
export interface LoraModule {
  name: string
  path: string
  base_model_name?: string
}

/** An adapter folder in the local LoRA library (scanned from LLMOPS_LORA_DIR). */
export interface LoraAdapter {
  name: string
  path: string
  base_model: string | null
  rank: number | null
  alpha: number | null
  target_modules: string[]
  size_on_disk: number
}

export interface LoraLibraryInfo {
  disk: DiskUsage
  root: string
  adapters: LoraAdapter[]
}

export interface LoraDownloadJob {
  name: string
  repo_id: string
  state: DownloadState
  total_bytes: number | null
  downloaded_bytes: number
  error: string | null
  started_at: number
  updated_at: number
}

export interface ApiKey {
  id: number
  name: string
  prefix: string
  created_at: number
  last_used_at: number | null
  revoked: number
  rpm_limit: number | null
  request_count: number
  total_tokens: number
  usage_last_ts: number | null
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

// ---- Benchmark datasets (ModelScope cache) ----
export interface DatasetEntry {
  key: string
  label: string
  dataset_id: string
  category: 'perf' | 'eval'
  file?: string // perf datasets only (single file); eval datasets are whole-repo
  note?: string
  approx?: string
  tier?: string // eval datasets: capability group (基線/中文/推理/數學/程式碼)
  metric?: string // eval datasets: scoring metric
  cached: boolean
  size_on_disk: number
}

export interface DatasetCacheInfo {
  disk: DiskUsage
  datasets: DatasetEntry[]
}

export interface DatasetDownloadJob {
  key: string
  label: string
  state: DownloadState
  total_bytes: number | null
  downloaded_bytes: number
  error: string | null
  started_at: number
  updated_at: number
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

// ---- Load testing (evalscope perf) ----
export interface PerfPoint {
  label: string
  concurrency: number | null
  rate: number | null
  total: number | null
  success: number | null
  failed: number | null
  duration: number | null
  rps: number | null
  avg_latency: number | null
  avg_ttft: number | null
  avg_tpot: number | null
  avg_itl: number | null
  output_tps: number | null
  total_tps: number | null
  avg_in: number | null
  avg_out: number | null
  latency_p50: number | null
  latency_p99: number | null
  latency_max: number | null
  ttft_p50: number | null
  ttft_p99: number | null
  ttft_max: number | null
  tpot_p50: number | null
  tpot_p99: number | null
  tpot_max: number | null
  // multi-turn only
  turns: number | null
  cache_hit: number | null
  first_ttft: number | null
  subsequent_ttft: number | null
}

export interface SlaTracePoint {
  val: number | null
  passed: boolean
  rps: number | null
  tps: number | null
  metrics: Record<string, number | null>
}
export interface SlaGroup {
  criteria: string
  variable: string
  max_satisfied: number | null
  note: string
  points: SlaTracePoint[]
}
/** Parsed shape of PerfRun.result (string-encoded). */
export interface PerfResult {
  points: PerfPoint[]
  sla: SlaGroup[] | null
}

export type PerfStatus = 'running' | 'completed' | 'failed' | 'cancelled'

export interface PerfRun {
  id: number
  created_at: number
  name: string | null
  model: string
  target_url: string
  status: PerfStatus
  params: string | null // JSON string of the launch config
  result?: string | null // JSON string of PerfPoint[] (only from get)
  output_dir: string | null
  error: string | null
  started_at: number | null
  finished_at: number | null
}

export interface PerfRequest {
  model: string
  name?: string
  mode: 'sweep' | 'openloop' | 'multiturn' | 'sla' | 'embedding' | 'rerank' | 'speed'
  target: 'router' | 'instance'
  instance_key?: string
  dataset: 'random' | 'openqa'
  endpoint: 'chat' | 'completions'
  max_tokens: number
  min_prompt_length: number
  max_prompt_length: number
  prefix_length?: number
  duration?: number
  speed_long?: boolean
  stream: boolean
  // sweep / multiturn
  parallel?: number[]
  number?: number[]
  warmup_num?: number
  // open-loop
  rate?: number[]
  // multi-turn
  mt_dataset?: 'share_gpt_zh_multi_turn' | 'random_multi_turn' | 'custom_multi_turn'
  mt_dataset_path?: string
  min_turns?: number
  max_turns?: number
  // sla auto-tune
  sla_variable?: 'parallel' | 'rate'
  sla_params?: Record<string, string>[]
  sla_lower_bound?: number
  sla_upper_bound?: number
  sla_num_runs?: number
  sla_fixed_parallel?: number
  // embedding / rerank
  rerank_documents?: number
}

// ---- Accuracy / quality evaluation (evalscope run_task) ----
export interface EvalDataset {
  key: string
  label: string
  dataset_id: string
  category: 'eval'
  tier: string
  metric?: string
  note?: string
  needs_judge?: boolean
  long_context?: boolean
  needs_tool_parser?: boolean
}

export interface EvalMetricScore {
  name: string
  score: number
  macro_score: number
  num: number
}
export interface EvalDatasetScore {
  dataset: string
  pretty: string
  score: number
  num: number
  metrics: EvalMetricScore[]
}
/** Parsed shape of EvalRun.result (string-encoded). */
export interface EvalResult {
  datasets: EvalDatasetScore[]
}

export type EvalStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface EvalListResponse {
  busy: boolean
  running: number
  queued: number
  budget: number
  used_budget: number
  runs: EvalRun[]
}

export interface EvalConfig {
  concurrency_budget: number
  used_budget: number
}

export interface EvalRun {
  id: number
  created_at: number
  name: string | null
  model: string
  target_url: string
  datasets: string // JSON string of dataset ids
  status: EvalStatus
  params: string | null
  result?: string | null // JSON string of EvalResult
  output_dir: string | null
  error: string | null
  started_at: number | null
  finished_at: number | null
}

// ---- Rich eval detail (lazy: fetched when a run is opened) ----
export interface EvalSubsetScore {
  name: string
  score: number
  num: number
}
export interface EvalReportMetric {
  name: string
  score: number | null
  num: number | null
  subsets: EvalSubsetScore[]
}
export interface EvalReportPerf {
  n_samples: number | null
  latency: { mean: number | null; p50: number | null; p99: number | null; max: number | null }
  output_tps: number | null
  req_ps: number | null
  input_tokens_mean: number | null
  output_tokens_mean: number | null
}
export interface EvalReportDataset {
  dataset: string
  pretty: string
  description: string | null
  score: number | null
  num: number | null
  metrics: EvalReportMetric[]
  perf: EvalReportPerf | null
}

// ---- Per-sample browser (lazy + paginated) ----
export interface EvalSampleRow {
  index: number
  subset: string
  scores: Record<string, number>
  score: number | null
  correct: boolean
  extracted: string | null
  target: string | null
  preview: string | null
}
export interface EvalSamplesPage {
  total: number
  total_all: number
  total_correct: number
  page: number
  page_size: number
  samples: EvalSampleRow[]
}
export interface EvalSampleDetail {
  index: number
  prompt: { role: string; content: string }[]
  answer: string | null
  target: string | null
  scores: Record<string, number>
  correct: boolean
  perf: { latency: number | null; ttft: number | null; input_tokens: number | null; output_tokens: number | null } | null
}

export interface EvalRequest {
  model: string
  name?: string
  target: 'router' | 'instance'
  instance_key?: string
  datasets: string[]
  limit?: number | null
  repeats?: number
  temperature?: number
  top_p?: number
  max_tokens?: number
  eval_batch_size?: number
  timeout?: number
  stream?: boolean
  // LLM judge (for free-form QA datasets)
  judge_enabled?: boolean
  judge_strategy?: 'auto' | 'llm'
  judge_target?: 'internal' | 'external'
  judge_model?: string
  judge_api_url?: string
  judge_api_key?: string
  // Per-dataset advanced settings (few_shot_num, subset_list, …)
  dataset_args?: Record<string, Record<string, unknown>>
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
  settings: Record<string, SettingValue> & { lora_modules?: LoraModule[] }
}
