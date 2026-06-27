<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { AlertTriangle, Check, Download, Loader2, Moon, Plus, Share2, Trash2, Wand2 } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import Dialog from '@/components/ui/Dialog.vue'
import Input from '@/components/ui/Input.vue'
import Textarea from '@/components/ui/Textarea.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import { api } from '@/lib/api'
import { toast } from '@/lib/toast'
import { ApiError } from '@/lib/api'
import { useModelsStore } from '@/stores/models'
import { useResourcesStore } from '@/stores/resources'
import { formatBytes } from '@/lib/utils'
import { ROUTING_STRATEGIES, routingStrategyLabel } from '@/lib/routingStrategies'
import { KV_SHARE_PRESET, isKvShared } from '@/lib/kvSharing'
import type { CachedModel, DownloadJob, KvTransferConfig, LoraAdapter, LoraModule, SettingValue } from '@/types/api'

const open = defineModel<boolean>('open', { default: false })
const props = defineProps<{ mode?: 'create' | 'edit'; editKey?: string | null }>()
const emit = defineEmits<{ created: [key: string]; updated: [key: string] }>()
const { t } = useI18n()

const models = useModelsStore()
const resources = useResourcesStore()

const isEdit = computed(() => props.mode === 'edit')

const command = ref('')
const parsing = ref(false)
const parsed = ref(false)
const warnings = ref<string[]>([])
const creating = ref(false)

// Editable form (populated from the parsed command).
const group = ref('')
const instanceId = ref('')
const host = ref('localhost')
const port = ref<number>(8000)
const cudaDevice = ref<number | null>(null)
const modelTag = ref('')
const params = ref<{ key: string; value: string }[]>([])
// Router-only load-balancing policy for the group. Lives in model_config but is
// NOT a vLLM flag, so it's edited as its own field and kept out of the raw param
// list (the launcher would otherwise reject it as an unknown `vllm serve` arg).
// '' = inherit the global default.
const routingStrategy = ref('')
// Cross-instance KV-cache sharing toggle. On = write the OffloadingConnector
// preset into model_config; off = each instance keeps its own KV. Edited via a
// switch, never the raw param list (kv_transfer_config is a nested object).
const kvShared = ref(false)
// Sleep-mode (warm-standby) toggle. On = launch with --enable-sleep-mode +
// VLLM_SERVER_DEV_MODE=1 so the instance can be slept (VRAM freed, seconds-fast
// wake). Edited via a switch, kept out of the raw param list.
const sleepMode = ref(false)
// LoRA adapters mounted at serve time; edited apart from the flat param list
// because each is a {name, path, base_model_name} object, not a scalar flag.
const loras = ref<LoraModule[]>([])
// Adapters available in the local LoRA library (for the path picker).
const loraLibrary = ref<LoraAdapter[]>([])
async function loadLoraLibrary() {
  try {
    loraLibrary.value = (await api.listLora()).adapters
  } catch {
    loraLibrary.value = [] // best-effort; the path field still accepts free text
  }
}
function adapterByPath(path: string): LoraAdapter | undefined {
  return loraLibrary.value.find((a) => a.path === path)
}
/** Base-model mismatch warning for a mounted adapter, or '' if fine/unknown. */
function loraBaseWarning(l: LoraModule): string {
  const a = adapterByPath(l.path)
  if (!a || !a.base_model) return ''
  return a.base_model === modelTag.value
    ? ''
    : t('addModel.loraBaseMismatch', {
      adapterBase: a.base_model,
      modelTag: modelTag.value || `(${t('addModel.notSet')})`,
    })
}

const gpuOptions = computed(() => resources.resources?.gpus.map((g) => g.index) ?? [])

// ---- Weight cache status for the entered model_tag ----
const cacheModels = ref<CachedModel[]>([])
const downloadJobs = ref<DownloadJob[]>([])
let downloadPoll: ReturnType<typeof setInterval> | null = null

const cachedEntry = computed(() =>
  modelTag.value ? cacheModels.value.find((m) => m.repo_id === modelTag.value) ?? null : null,
)
const downloadJob = computed(() =>
  modelTag.value ? downloadJobs.value.find((d) => d.repo_id === modelTag.value) ?? null : null,
)
const downloadPct = computed(() => {
  const j = downloadJob.value
  if (!j || !j.total_bytes) return null
  return Math.min(100, (j.downloaded_bytes / j.total_bytes) * 100)
})

async function loadCache() {
  try {
    cacheModels.value = (await api.getCache()).models
  } catch {
    /* best-effort: weights status just won't show */
  }
}
async function loadDownloads() {
  try {
    downloadJobs.value = await api.listDownloads()
    if (downloadJob.value?.state === 'completed') await loadCache()
  } catch {
    /* transient */
  }
}
async function downloadWeights() {
  const repo = modelTag.value.trim()
  if (!repo) return
  try {
    await api.startDownload(repo)
    toast.success(t('addModel.downloadStarted', { repo }), {
      description: t('addModel.downloadStartedDesc'),
    })
    await loadDownloads()
  } catch (e) {
    toast.error(t('addModel.downloadFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  }
}

onBeforeUnmount(() => {
  if (downloadPoll) clearInterval(downloadPoll)
})

const key = computed(() => `${group.value}::${instanceId.value}`)
const keyExists = computed(() => !!group.value && !!instanceId.value && models.byKey.has(key.value))
const portInUse = computed(() => models.models.some((m) => m.port === port.value && m.key !== key.value))
const groupExists = computed(() =>
  models.models.some((m) => m.key.split('::')[0] === group.value),
)
// model_config edits apply to the whole group; warn when other instances share it.
const groupSiblings = computed(() =>
  models.models.filter((m) => m.kind === 'llm' && m.key.split('::')[0] === group.value && m.key !== key.value).length,
)
const canSubmit = computed(
  () =>
    !!group.value &&
    !!instanceId.value &&
    !!modelTag.value &&
    !portInUse.value &&
    (isEdit.value || !keyExists.value),
)

function reset() {
  command.value = ''
  parsed.value = false
  warnings.value = []
  group.value = ''
  instanceId.value = ''
  host.value = 'localhost'
  port.value = 8000
  cudaDevice.value = null
  modelTag.value = ''
  params.value = []
  routingStrategy.value = ''
  kvShared.value = false
  sleepMode.value = false
  loras.value = []
}

/** Pull `lora_modules` out of a settings/model_config object into the LoRA
 *  editor, returning the remaining flat [key, value] entries for the param list. */
function extractLoras(entries: [string, unknown][]): [string, unknown][] {
  loras.value = []
  const rest: [string, unknown][] = []
  for (const [k, v] of entries) {
    if (k === 'lora_modules') {
      // `lora_modules` is always owned by the LoRA editor — never let it leak
      // into the raw param list. The config endpoint reports `lora_modules: null`
      // for models without adapters; if that fell through to `rest` it would be
      // rendered as a param and re-submitted as the string "", which fails the
      // backend's `Optional[list[LoraModule]]` validation.
      loras.value = Array.isArray(v)
        ? (v as LoraModule[]).map((m) => ({
            name: m.name ?? '',
            path: m.path ?? '',
            base_model_name: m.base_model_name ?? '',
          }))
        : []
    } else {
      rest.push([k, v])
    }
  }
  return rest
}

/** Prefill the form from the live config for the model being edited. */
function prefillForEdit() {
  const k = props.editKey
  const cfg = k ? models.engineConfig(k) : null
  if (!k || !cfg) return
  const [g, id] = k.split('::')
  group.value = g ?? ''
  instanceId.value = id ?? ''
  host.value = cfg.host ?? 'localhost'
  port.value = cfg.port
  cudaDevice.value = cfg.cuda_device ?? null
  modelTag.value = String(cfg.settings.model_tag ?? '')
  routingStrategy.value = String(cfg.settings.routing_strategy ?? '')
  kvShared.value = isKvShared(cfg.settings)
  sleepMode.value = !!cfg.settings.enable_sleep_mode
  params.value = extractLoras(
    Object.entries(cfg.settings).filter(
      ([k2]) =>
        k2 !== 'model_tag' &&
        k2 !== 'routing_strategy' &&
        k2 !== 'kv_transfer_config' &&
        k2 !== 'enable_sleep_mode',
    ),
  ).map(([k2, v]) => ({ key: k2, value: v === null ? '' : String(v) }))
  warnings.value = []
  parsed.value = true // skip the paste/parse step
}

watch(open, (v) => {
  if (!v) {
    reset()
    if (downloadPoll) {
      clearInterval(downloadPoll)
      downloadPoll = null
    }
    return
  }
  if (isEdit.value) prefillForEdit()
  void loadCache()
  void loadDownloads()
  void loadLoraLibrary()
  downloadPoll = setInterval(loadDownloads, 1500) // reflect background progress
})

async function parse() {
  if (!command.value.trim() || parsing.value) return
  parsing.value = true
  try {
    const p = await api.parseCommand(command.value)
    group.value = p.group
    instanceId.value = p.instance.id
    host.value = p.instance.host
    port.value = p.instance.port
    cudaDevice.value = p.instance.cuda_device
    modelTag.value = String(p.model_config.model_tag ?? '')
    routingStrategy.value = String(
      (p.model_config as Record<string, unknown>).routing_strategy ?? '',
    )
    kvShared.value = isKvShared(p.model_config as Record<string, unknown>)
    sleepMode.value = !!(p.model_config as Record<string, unknown>).enable_sleep_mode
    params.value = extractLoras(
      Object.entries(p.model_config).filter(
        ([k]) =>
          k !== 'model_tag' &&
          k !== 'routing_strategy' &&
          k !== 'kv_transfer_config' &&
          k !== 'enable_sleep_mode',
      ),
    ).map(([k, v]) => ({ key: k, value: String(v) }))
    warnings.value = p.warnings
    parsed.value = true
  } catch (e) {
    toast.error(t('addModel.parseFailed'), {
      description: e instanceof ApiError ? e.message : String(e),
    })
  } finally {
    parsing.value = false
  }
}

function coerce(v: string): SettingValue {
  const t = v.trim()
  if (t === '') return ''
  if (t === 'true' || t === 'false') return t === 'true'
  if (!Number.isNaN(Number(t))) return Number(t)
  return v
}

function addParam() {
  params.value.push({ key: '', value: '' })
}
function removeParam(i: number) {
  params.value.splice(i, 1)
}

// ---- LoRA adapters ----
function addLora() {
  loras.value.push({ name: '', path: '', base_model_name: '' })
}
function removeLora(i: number) {
  loras.value.splice(i, 1)
}
/** Add --enable-lora as a flag when the first adapter is mounted (vLLM needs it). */
function ensureEnableLora() {
  if (!params.value.some((p) => p.key === 'enable_lora')) setParam('enable_lora', 'true')
}
/** Picked an adapter from the library: default served name, base, and bump
 *  max_lora_rank to cover every mounted adapter's rank. */
function onPickLora(l: LoraModule) {
  ensureEnableLora()
  const a = adapterByPath(l.path)
  if (!a) return
  if (!l.name.trim()) l.name = a.name
  if (a.base_model && !l.base_model_name) l.base_model_name = a.base_model
  const ranks = loras.value.map((m) => adapterByPath(m.path)?.rank ?? 0)
  const maxRank = Math.max(0, ...ranks)
  if (maxRank > 0) setParam('max_lora_rank', String(maxRank))
}

// ---- Tool-calling presets (see docs/vllm_auto_tool_整理.md) ----
const showToolHint = ref(false)
// Recommended (tool_call_parser, reasoning_parser) by model family.
const TOOL_PRESETS = [
  { label: 'Qwen2.5 / QwQ', parser: 'hermes', reasoning: '' },
  { label: 'addModel.qwen3Thinking', parser: 'hermes', reasoning: 'qwen3' },
  { label: 'Qwen3-Coder', parser: 'qwen3_xml', reasoning: '' },
  { label: 'Llama 3.1/3.2', parser: 'llama3_json', reasoning: '' },
  { label: 'Llama 4', parser: 'llama4_pythonic', reasoning: '' },
  { label: 'Mistral', parser: 'mistral', reasoning: '' },
  { label: 'DeepSeek-V3/R1', parser: 'deepseek_v3', reasoning: '' },
  { label: 'GLM-4.5/4.6', parser: 'glm45', reasoning: '' },
]
function setParam(key: string, value: string) {
  const existing = params.value.find((p) => p.key === key)
  if (existing) existing.value = value
  else params.value.push({ key, value })
}
function applyToolPreset(parser: string, reasoning: string) {
  setParam('enable_auto_tool_choice', 'true')
  setParam('tool_call_parser', parser)
  if (reasoning) setParam('reasoning_parser', reasoning)
}

// ---- vLLM acceleration presets (see docs/vllm_推理加速參數整理.md) ----
// All ten are plain model_config keys, edited through the same `params` list so
// they coexist with the raw editor. An empty value clears the key -> vLLM default.
const showAccel = ref(false)
function getParam(key: string): string {
  return params.value.find((p) => p.key === key)?.value ?? ''
}
function clearParam(key: string) {
  const i = params.value.findIndex((p) => p.key === key)
  if (i >= 0) params.value.splice(i, 1)
}
function setParamOrClear(key: string, value: string) {
  if (value === '') clearParam(key)
  else setParam(key, value)
}
// gpu_memory_utilization is intentionally NOT here: it's the model's deliberate
// memory setting, so "清除 / 套模板" must preserve the existing value, not wipe it.
const ACCEL_KEYS = [
  'performance_mode', 'optimization_level', 'max_num_batched_tokens', 'max_num_seqs',
  'enable_prefix_caching', 'enable_chunked_prefill',
  'kv_cache_dtype', 'async_scheduling', 'stream_interval', 'quantization',
  // advanced (tier 2)
  'speculative_config', 'prefix_caching_hash_algo', 'max_num_partial_prefills',
  'max_long_partial_prefills', 'long_prefill_token_threshold', 'cpu_offload_gb',
]
function clearAccel() {
  for (const k of ACCEL_KEYS) clearParam(k)
}

// ---- Advanced (tier 2) ----
const showAdvanced = ref(false)
// N-gram speculative decoding is configured as a JSON object; we store it as a
// JSON *string* in params (coerce keeps strings as-is, and the launcher passes it
// straight through to --speculative-config). params stays the single source.
function specCfg(): Record<string, unknown> {
  try {
    return JSON.parse(getParam('speculative_config') || '{}')
  } catch {
    return {}
  }
}
function writeSpec(tokens: number) {
  setParam(
    'speculative_config',
    JSON.stringify({ method: 'ngram', num_speculative_tokens: tokens, prompt_lookup_min: 2, prompt_lookup_max: 5 }),
  )
}
const specEnabled = computed({
  get: () => specCfg().method === 'ngram',
  set: (on: boolean) => (on ? writeSpec(Number(specTokens.value) || 4) : clearParam('speculative_config')),
})
const specTokens = computed({
  get: () => Number(specCfg().num_speculative_tokens ?? 4),
  set: (n: number) => {
    if (specEnabled.value) writeSpec(Number(n) || 4)
  },
})
// Starting-point combos; values tuned conservatively for a single small GPU.
const ACCEL_PRESETS: Record<string, Record<string, string>> = {
  latency: {
    performance_mode: 'interactivity', max_num_batched_tokens: '2048',
    max_num_seqs: '64', enable_prefix_caching: 'true', stream_interval: '1',
  },
  throughput: {
    performance_mode: 'throughput', max_num_batched_tokens: '8192',
    max_num_seqs: '128', enable_prefix_caching: 'true', async_scheduling: 'true',
  },
}
function applyAccelPreset(name: 'latency' | 'throughput') {
  clearAccel()
  for (const [k, v] of Object.entries(ACCEL_PRESETS[name]!)) setParam(k, v)
  showAccel.value = true
}

async function submit() {
  if (!canSubmit.value || creating.value) return
  creating.value = true
  const settings: Record<string, SettingValue> & {
    lora_modules?: LoraModule[]
    kv_transfer_config?: KvTransferConfig
    enable_sleep_mode?: boolean
  } = {
    model_tag: modelTag.value,
  }
  for (const { key: k, value } of params.value) {
    // `lora_modules`, `routing_strategy` and `kv_transfer_config` have dedicated
    // editors below — never let a raw param (e.g. a stray "" from a null, or a
    // leaked key) stomp them via the generic param list.
    const kk = k.trim()
    if (
      kk &&
      kk !== 'lora_modules' &&
      kk !== 'routing_strategy' &&
      kk !== 'kv_transfer_config' &&
      kk !== 'enable_sleep_mode'
    )
      settings[kk] = coerce(value)
  }
  // Router-only load-balancing policy; '' inherits the global default, so only
  // send it when explicitly chosen.
  if (routingStrategy.value) settings.routing_strategy = routingStrategy.value
  // Cross-instance KV-cache sharing: write the OffloadingConnector preset when
  // the toggle is on; otherwise leave it unset so each instance keeps its own KV.
  if (kvShared.value) settings.kv_transfer_config = KV_SHARE_PRESET
  // Sleep-mode warm-standby tier: launch with --enable-sleep-mode + dev mode so
  // the instance can be slept/woken (see docs/autoscaling-design_zh-CN.md).
  if (sleepMode.value) settings.enable_sleep_mode = true
  // Mounted adapters: keep only filled rows; drop the empty base_model_name field.
  const cleanLoras = loras.value
    .filter((l) => l.name.trim() && l.path.trim())
    .map((l) => ({
      name: l.name.trim(),
      path: l.path.trim(),
      ...(l.base_model_name?.trim() ? { base_model_name: l.base_model_name.trim() } : {}),
    }))
  if (cleanLoras.length) settings.lora_modules = cleanLoras
  const payload = {
    group: group.value,
    instance: { id: instanceId.value, host: host.value, port: port.value, cuda_device: cudaDevice.value },
    settings,
  }
  try {
    if (isEdit.value && props.editKey) {
      const view = await api.updateModel(props.editKey, payload)
      toast.success(t('addModel.editSuccess', { key: view.key }), {
        description: t('addModel.editSuccessDesc'),
      })
      // Keep the store's cached config in sync so the next edit prefills fresh.
      void models.loadConfig()
      if (!(await api.routerReload())) {
        toast.warning(t('addModel.routerReloadFailed'), {
          description: t('addModel.routerReloadEditDesc'),
        })
      }
      emit('updated', view.key)
    } else {
      const view = await api.createModel(payload)
      toast.success(t('addModel.createSuccess', { key: view.key }), {
        description: t('addModel.createSuccessDesc'),
      })
      void models.loadConfig()
      // Make it routable end-to-end by refreshing the router's view of the config.
      if (!(await api.routerReload())) {
        toast.warning(t('addModel.routerReloadFailed'), {
          description: t('addModel.routerReloadCreateDesc'),
        })
      }
      emit('created', view.key)
    }
    open.value = false
  } catch (e) {
    toast.error(isEdit.value ? t('addModel.editFailed') : t('addModel.createFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <Dialog v-model:open="open" :title="isEdit ? $t('addModel.editTitle') : $t('addModel.createTitle')" width-class="max-w-2xl">
    <div class="space-y-4">
      <!-- Paste + parse (create only) -->
      <div v-if="!isEdit">
        <label class="text-xs font-medium text-muted-foreground">{{ $t('addModel.pasteCommand') }}</label>
        <Textarea
          v-model="command"
          placeholder="CUDA_VISIBLE_DEVICES=0 vllm serve Qwen/Qwen2.5-3B-Instruct --port 8020 --dtype float16 --max-model-len 4096 --gpu-memory-utilization 0.85"
          class="mt-1 min-h-[80px] font-mono text-xs"
        />
        <Button class="mt-2" size="sm" :disabled="!command.trim() || parsing" @click="parse">
          <Loader2 v-if="parsing" class="size-4 animate-spin" /><Wand2 v-else class="size-4" />
          {{ $t('addModel.parseCommand') }}
        </Button>
      </div>

      <!-- Editable preview -->
      <template v-if="parsed">
        <!-- Warnings -->
        <div
          v-if="warnings.length"
          class="rounded-lg border border-status-starting/30 bg-status-starting/10 p-3 text-xs text-status-starting"
        >
          <p v-for="w in warnings" :key="w" class="flex items-start gap-1.5">
            <AlertTriangle class="mt-px size-3.5 shrink-0" />{{ w }}
          </p>
        </div>

        <!-- model_config is shared across the group -->
        <div
          v-if="isEdit && groupSiblings > 0"
          class="rounded-lg border border-status-starting/30 bg-status-starting/10 p-3 text-xs text-status-starting"
        >
          <p class="flex items-start gap-1.5">
            <AlertTriangle class="mt-px size-3.5 shrink-0" />
            {{ $t('addModel.groupSharedWarn', { group, n: groupSiblings + 1 }) }}
          </p>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('addModel.groupLabel') }}</span>
            <Input v-model="group" class="mt-1" :disabled="isEdit" />
            <span v-if="!isEdit && groupExists && !keyExists" class="mt-1 block text-[11px] text-muted-foreground">
              {{ $t('addModel.groupExists') }}
            </span>
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('addModel.instanceLabel') }}</span>
            <Input v-model="instanceId" class="mt-1" :disabled="isEdit" :class="!isEdit && keyExists ? 'border-status-failed' : ''" />
            <span v-if="!isEdit && keyExists" class="mt-1 block text-[11px] text-status-failed">
              {{ $t('addModel.keyExists', { key }) }}
            </span>
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('addModel.hostLabel') }}</span>
            <Input v-model="host" class="mt-1" />
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('addModel.portLabel') }}</span>
            <Input v-model.number="port" type="number" class="mt-1" :class="portInUse ? 'border-status-failed' : ''" />
            <span v-if="portInUse" class="mt-1 block text-[11px] text-status-failed">
              {{ $t('addModel.portInUse', { port }) }}
            </span>
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('addModel.gpuLabel') }}</span>
            <select
              v-model="cudaDevice"
              class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm"
            >
              <option :value="null">{{ $t('addModel.gpuAuto') }}</option>
              <option v-for="i in gpuOptions" :key="i" :value="i">cuda:{{ i }}</option>
            </select>
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">{{ $t('addModel.modelTagLabel') }} <span class="text-status-failed">*</span></span>
            <Input v-model="modelTag" class="mt-1 font-mono" placeholder="org/model" />
          </label>
          <label class="col-span-2 block">
            <span class="text-xs text-muted-foreground">{{ $t('addModel.routingLabel') }}</span>
            <select
              v-model="routingStrategy"
              class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm"
            >
              <option value="">{{ $t('addModel.routingDefault') }}</option>
              <option v-for="s in ROUTING_STRATEGIES" :key="s" :value="s">{{ routingStrategyLabel(s) }}</option>
            </select>
            <span class="mt-1 block text-[11px] text-muted-foreground">
              {{ $t('addModel.routingHint') }}
            </span>
          </label>
          <label
            class="col-span-2 flex cursor-pointer items-start gap-3 rounded-lg border border-input bg-background/40 px-3 py-2.5"
            :class="kvShared && 'border-[var(--chart-1)]/50 bg-[var(--chart-1)]/5'"
          >
            <input v-model="kvShared" type="checkbox" class="mt-0.5 size-4 accent-[var(--chart-1)]" />
            <span class="min-w-0">
              <span class="flex items-center gap-1.5 text-sm font-medium">
                <Share2 class="size-3.5 text-[var(--chart-1)]" />{{ $t('addModel.kvShareLabel') }}
              </span>
              <span class="mt-0.5 block text-[11px] text-muted-foreground">
                {{ $t('addModel.kvShareDesc') }}
              </span>
            </span>
          </label>
          <label
            class="col-span-2 flex cursor-pointer items-start gap-3 rounded-lg border border-input bg-background/40 px-3 py-2.5"
            :class="sleepMode && 'border-[var(--chart-4)]/50 bg-[var(--chart-4)]/5'"
          >
            <input v-model="sleepMode" type="checkbox" class="mt-0.5 size-4 accent-[var(--chart-4)]" />
            <span class="min-w-0">
              <span class="flex items-center gap-1.5 text-sm font-medium">
                <Moon class="size-3.5 text-[var(--chart-4)]" />{{ $t('addModel.sleepModeLabel') }}
              </span>
              <span class="mt-0.5 block text-[11px] text-muted-foreground">
                {{ $t('addModel.sleepModeDesc') }}
              </span>
            </span>
          </label>
        </div>

        <!-- Weight cache status for the entered model_tag -->
        <div
          v-if="modelTag.trim()"
          class="flex items-center gap-2 rounded-lg border border-border/60 bg-background/40 px-3 py-2 text-xs"
        >
          <!-- Downloading -->
          <template v-if="downloadJob && (downloadJob.state === 'downloading' || downloadJob.state === 'pending')">
            <Loader2 class="size-3.5 shrink-0 animate-spin text-muted-foreground" />
            <span class="text-muted-foreground">{{ $t('addModel.weightsDownloading') }}</span>
            <div class="mx-1 h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                class="h-full rounded-full bg-[var(--chart-2)] transition-[width] duration-700"
                :class="downloadPct == null ? 'w-1/3 animate-pulse' : ''"
                :style="downloadPct != null ? { width: `${downloadPct}%` } : {}"
              />
            </div>
            <span class="shrink-0 tabular text-muted-foreground">
              {{ downloadPct != null ? `${downloadPct.toFixed(0)}%` : formatBytes(downloadJob.downloaded_bytes) }}
            </span>
          </template>
          <!-- Cached -->
          <template v-else-if="cachedEntry">
            <Check class="size-3.5 shrink-0 text-status-ready" />
            <span class="text-status-ready">{{ $t('addModel.weightsCached') }}</span>
            <span class="ml-auto tabular text-muted-foreground">{{ formatBytes(cachedEntry.size_on_disk) }}</span>
          </template>
          <!-- Failed -->
          <template v-else-if="downloadJob && downloadJob.state === 'failed'">
            <AlertTriangle class="size-3.5 shrink-0 text-status-failed" />
            <span class="truncate text-status-failed">{{ $t('addModel.weightsDownloadFailed') }}{{ downloadJob.error }}</span>
            <Button size="sm" variant="ghost" class="ml-auto shrink-0" @click="downloadWeights">
              <Download class="size-3.5" />{{ $t('common.retry') }}
            </Button>
          </template>
          <!-- Not cached -->
          <template v-else>
            <span class="text-muted-foreground">{{ $t('addModel.weightsNotCached') }}</span>
            <Button size="sm" variant="ghost" class="ml-auto shrink-0" @click="downloadWeights">
              <Download class="size-3.5" />{{ $t('common.preDownload') }}
            </Button>
          </template>
        </div>

        <!-- Acceleration presets (curated subset of model_config) -->
        <div class="rounded-md border border-border/60 bg-muted/20">
          <button
            type="button"
            class="flex w-full items-center justify-between px-3 py-2 text-left"
            @click="showAccel = !showAccel"
          >
            <span class="text-xs font-medium">{{ $t('addModel.accelTitle') }}</span>
            <span class="text-xs text-muted-foreground">{{ showAccel ? '▾' : '▸' }}</span>
          </button>
          <div v-if="showAccel" class="space-y-3 border-t border-border/60 p-3">
            <!-- Scenario templates -->
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-[11px] text-muted-foreground">{{ $t('addModel.accelTemplates') }}</span>
              <Button size="sm" variant="outline" @click="applyAccelPreset('latency')">{{ $t('addModel.accelLatency') }}</Button>
              <Button size="sm" variant="outline" @click="applyAccelPreset('throughput')">{{ $t('addModel.accelThroughput') }}</Button>
              <Button size="sm" variant="ghost" @click="clearAccel">{{ $t('addModel.accelClear') }}</Button>
            </div>

            <div class="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
              <!-- performance_mode -->
              <label class="space-y-1">
                <span class="text-[11px] font-medium text-muted-foreground">performance_mode <span class="font-normal">({{ $t('addModel.defaultLabel') }} balanced)</span></span>
                <select :value="getParam('performance_mode')" class="h-8 w-full rounded-md border border-input bg-background px-2 text-xs" @change="setParamOrClear('performance_mode', ($event.target as HTMLSelectElement).value)">
                  <option value="">{{ $t('addModel.defaultLabel') }} (balanced)</option>
                  <option value="interactivity">interactivity ({{ $t('addModel.accelLatency') }})</option>
                  <option value="throughput">throughput ({{ $t('addModel.accelThroughput') }})</option>
                </select>
              </label>
              <!-- optimization_level -->
              <label class="space-y-1">
                <span class="text-[11px] font-medium text-muted-foreground">optimization_level <span class="font-normal">({{ $t('addModel.defaultLabel') }} 2)</span></span>
                <select :value="getParam('optimization_level')" class="h-8 w-full rounded-md border border-input bg-background px-2 text-xs" @change="setParamOrClear('optimization_level', ($event.target as HTMLSelectElement).value)">
                  <option value="">{{ $t('addModel.defaultLabel') }} (2)</option>
                  <option value="0">0 ({{ $t('addModel.fastestStartup') }})</option>
                  <option value="1">1</option>
                  <option value="2">2</option>
                  <option value="3">3 ({{ $t('addModel.mostAggressive') }})</option>
                </select>
              </label>
              <!-- max_num_batched_tokens -->
              <label class="space-y-1">
                <span class="text-[11px] font-medium text-muted-foreground">max_num_batched_tokens <span class="font-normal">({{ $t('addModel.defaultLabel') }} {{ $t('addModel.autoLabel') }})</span></span>
                <Input :model-value="getParam('max_num_batched_tokens')" type="number" min="1" :placeholder="$t('addModel.autoLabel')" class="h-8 text-xs" @update:model-value="setParamOrClear('max_num_batched_tokens', String($event))" />
              </label>
              <!-- max_num_seqs -->
              <label class="space-y-1">
                <span class="text-[11px] font-medium text-muted-foreground">max_num_seqs <span class="font-normal">({{ $t('addModel.defaultLabel') }} {{ $t('addModel.autoLabel') }})</span></span>
                <Input :model-value="getParam('max_num_seqs')" type="number" min="1" :placeholder="$t('addModel.autoLabel')" class="h-8 text-xs" @update:model-value="setParamOrClear('max_num_seqs', String($event))" />
              </label>
              <!-- gpu_memory_utilization (keep the model's existing value; don't impose a default) -->
              <label class="space-y-1">
                <span class="text-[11px] font-medium text-muted-foreground">gpu_memory_utilization <span class="font-normal">({{ $t('addModel.gpuMemHint') }})</span></span>
                <Input :model-value="getParam('gpu_memory_utilization')" type="number" min="0.1" max="0.98" step="0.01" :placeholder="$t('addModel.notSet')" class="h-8 text-xs" @update:model-value="setParamOrClear('gpu_memory_utilization', String($event))" />
              </label>
              <!-- kv_cache_dtype -->
              <label class="space-y-1">
                <span class="text-[11px] font-medium text-muted-foreground">kv_cache_dtype <span class="font-normal">({{ $t('addModel.defaultLabel') }} auto)</span></span>
                <select :value="getParam('kv_cache_dtype')" class="h-8 w-full rounded-md border border-input bg-background px-2 text-xs" @change="setParamOrClear('kv_cache_dtype', ($event.target as HTMLSelectElement).value)">
                  <option value="">{{ $t('addModel.kvCacheDefault') }}</option>
                  <option value="fp8">fp8</option>
                  <option value="fp8_e4m3">fp8_e4m3</option>
                  <option value="fp8_e5m2">fp8_e5m2</option>
                </select>
              </label>
              <!-- enable_prefix_caching -->
              <label class="space-y-1">
                <span class="text-[11px] font-medium text-muted-foreground">enable_prefix_caching <span class="font-normal">({{ $t('addModel.prefixCacheDefault') }})</span></span>
                <select :value="getParam('enable_prefix_caching')" class="h-8 w-full rounded-md border border-input bg-background px-2 text-xs" @change="setParamOrClear('enable_prefix_caching', ($event.target as HTMLSelectElement).value)">
                  <option value="">{{ $t('addModel.prefixCacheDefault') }}</option>
                  <option value="true">{{ $t('addModel.forceOn') }}</option>
                  <option value="false">{{ $t('addModel.off') }}</option>
                </select>
              </label>
              <!-- enable_chunked_prefill -->
              <label class="space-y-1">
                <span class="text-[11px] font-medium text-muted-foreground">enable_chunked_prefill <span class="font-normal">({{ $t('addModel.chunkedPrefillDefault') }})</span></span>
                <select :value="getParam('enable_chunked_prefill')" class="h-8 w-full rounded-md border border-input bg-background px-2 text-xs" @change="setParamOrClear('enable_chunked_prefill', ($event.target as HTMLSelectElement).value)">
                  <option value="">{{ $t('addModel.chunkedPrefillDefault') }}</option>
                  <option value="true">{{ $t('addModel.forceOn') }}</option>
                  <option value="false">{{ $t('addModel.off') }}</option>
                </select>
              </label>
              <!-- async_scheduling -->
              <label class="space-y-1">
                <span class="text-[11px] font-medium text-muted-foreground">async_scheduling <span class="font-normal">({{ $t('addModel.asyncSchedDefault') }})</span></span>
                <select :value="getParam('async_scheduling')" class="h-8 w-full rounded-md border border-input bg-background px-2 text-xs" @change="setParamOrClear('async_scheduling', ($event.target as HTMLSelectElement).value)">
                  <option value="">{{ $t('addModel.asyncSchedDefault') }}</option>
                  <option value="true">{{ $t('addModel.onExperimental') }}</option>
                  <option value="false">{{ $t('addModel.off') }}</option>
                </select>
              </label>
              <!-- stream_interval -->
              <label class="space-y-1">
                <span class="text-[11px] font-medium text-muted-foreground">stream_interval <span class="font-normal">({{ $t('addModel.defaultLabel') }} 1)</span></span>
                <Input :model-value="getParam('stream_interval')" type="number" min="1" placeholder="1" class="h-8 text-xs" @update:model-value="setParamOrClear('stream_interval', String($event))" />
              </label>
              <!-- quantization (online) -->
              <label class="space-y-1 sm:col-span-2">
                <span class="text-[11px] font-medium text-muted-foreground">quantization <span class="font-normal">({{ $t('addModel.quantDefault') }})</span></span>
                <select :value="getParam('quantization')" class="h-8 w-full rounded-md border border-input bg-background px-2 text-xs" @change="setParamOrClear('quantization', ($event.target as HTMLSelectElement).value)">
                  <option value="">{{ $t('addModel.quantDefault') }}</option>
                  <option value="bitsandbytes">{{ $t('addModel.quantBnb') }}</option>
                  <option value="fp8_per_tensor">{{ $t('addModel.quantFp8Tensor') }}</option>
                  <option value="fp8_per_block">{{ $t('addModel.quantFp8Block') }}</option>
                  <option value="int8_per_channel_weight_only">{{ $t('addModel.quantInt8') }}</option>
                </select>
                <span class="text-[10px] text-muted-foreground">{{ $t('addModel.quantHint') }}</span>
              </label>
            </div>

            <!-- Advanced (tier 2) -->
            <div class="rounded-md border border-border/60">
              <button type="button" class="flex w-full items-center justify-between px-2.5 py-1.5 text-left" @click="showAdvanced = !showAdvanced">
                <span class="text-[11px] font-medium text-muted-foreground">{{ $t('addModel.advancedTitle') }}</span>
                <span class="text-[11px] text-muted-foreground">{{ showAdvanced ? '▾' : '▸' }}</span>
              </button>
              <div v-if="showAdvanced" class="space-y-3 border-t border-border/60 p-3">
                <!-- speculative decoding (ngram) -->
                <div class="space-y-1.5">
                  <label class="flex items-center gap-2 text-[11px] font-medium text-muted-foreground">
                    <input v-model="specEnabled" type="checkbox" class="size-3.5 accent-[var(--chart-1)]" />
                    {{ $t('addModel.ngramSpec') }}
                  </label>
                  <label v-if="specEnabled" class="flex items-center gap-2 pl-5 text-[11px] text-muted-foreground">
                    num_speculative_tokens
                    <Input v-model.number="specTokens" type="number" min="1" max="10" class="h-7 w-20 text-xs" />
                    <span class="text-[10px]">({{ $t('addModel.defaultLabel') }} 4)</span>
                  </label>
                </div>

                <div class="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
                  <!-- prefix_caching_hash_algo -->
                  <label class="space-y-1">
                    <span class="text-[11px] font-medium text-muted-foreground">prefix_caching_hash_algo <span class="font-normal">({{ $t('addModel.hashDefault') }})</span></span>
                    <select :value="getParam('prefix_caching_hash_algo')" class="h-8 w-full rounded-md border border-input bg-background px-2 text-xs" @change="setParamOrClear('prefix_caching_hash_algo', ($event.target as HTMLSelectElement).value)">
                      <option value="">{{ $t('addModel.hashDefault') }}</option>
                      <option value="sha256">{{ $t('addModel.hashSha256') }}</option>
                      <option value="xxhash">{{ $t('addModel.hashXxhash') }}</option>
                      <option value="sha256_cbor">sha256_cbor</option>
                      <option value="xxhash_cbor">xxhash_cbor</option>
                    </select>
                  </label>
                  <!-- cpu_offload_gb -->
                  <label class="space-y-1">
                    <span class="text-[11px] font-medium text-muted-foreground">cpu_offload_gb <span class="font-normal">({{ $t('addModel.defaultLabel') }} 0)</span></span>
                    <Input :model-value="getParam('cpu_offload_gb')" type="number" min="0" placeholder="0" class="h-8 text-xs" @update:model-value="setParamOrClear('cpu_offload_gb', String($event))" />
                    <span class="text-[10px] text-muted-foreground">{{ $t('addModel.offloadHint') }}</span>
                  </label>
                  <!-- max_num_partial_prefills -->
                  <label class="space-y-1">
                    <span class="text-[11px] font-medium text-muted-foreground">max_num_partial_prefills <span class="font-normal">({{ $t('addModel.defaultLabel') }} 1)</span></span>
                    <Input :model-value="getParam('max_num_partial_prefills')" type="number" min="1" placeholder="1" class="h-8 text-xs" @update:model-value="setParamOrClear('max_num_partial_prefills', String($event))" />
                  </label>
                  <!-- max_long_partial_prefills -->
                  <label class="space-y-1">
                    <span class="text-[11px] font-medium text-muted-foreground">max_long_partial_prefills <span class="font-normal">({{ $t('addModel.defaultLabel') }} 1)</span></span>
                    <Input :model-value="getParam('max_long_partial_prefills')" type="number" min="1" placeholder="1" class="h-8 text-xs" @update:model-value="setParamOrClear('max_long_partial_prefills', String($event))" />
                  </label>
                  <!-- long_prefill_token_threshold -->
                  <label class="space-y-1 sm:col-span-2">
                    <span class="text-[11px] font-medium text-muted-foreground">long_prefill_token_threshold <span class="font-normal">({{ $t('addModel.defaultLabel') }} 0={{ $t('addModel.autoLabel') }})</span></span>
                    <Input :model-value="getParam('long_prefill_token_threshold')" type="number" min="0" placeholder="0" class="h-8 text-xs" @update:model-value="setParamOrClear('long_prefill_token_threshold', String($event))" />
                    <span class="text-[10px] text-muted-foreground">{{ $t('addModel.partialPrefillHint') }}</span>
                  </label>
                </div>
              </div>
            </div>

            <p class="text-[10px] text-muted-foreground">
              {{ $t('addModel.accelHint') }}
            </p>
          </div>
        </div>

        <!-- vLLM params -->
        <div>
          <div class="mb-1.5 flex items-center justify-between">
            <span class="text-xs font-medium text-muted-foreground">{{ $t('addModel.vllmParams') }}</span>
            <Button size="sm" variant="ghost" @click="addParam"><Plus class="size-3.5" />{{ $t('addModel.addParam') }}</Button>
          </div>
          <div class="space-y-1.5">
            <div v-for="(p, i) in params" :key="i" class="flex items-center gap-2">
              <Input v-model="p.key" :placeholder="$t('addModel.flagPlaceholder')" class="flex-1 font-mono text-xs" />
              <Input v-model="p.value" :placeholder="$t('addModel.valuePlaceholder')" class="flex-1 font-mono text-xs" />
              <Button size="icon-sm" variant="ghost" @click="removeParam(i)"><Trash2 class="size-3.5" /></Button>
            </div>
            <p v-if="!params.length" class="text-xs text-muted-foreground">{{ $t('addModel.noExtraParams') }}</p>
          </div>

          <!-- Tool-calling hint + quick presets -->
          <div class="mt-2 rounded-md border border-border/60 bg-muted/30 p-2.5">
            <button
              type="button"
              class="flex w-full items-center justify-between text-xs font-medium text-muted-foreground hover:text-foreground"
              @click="showToolHint = !showToolHint"
            >
              <span>{{ $t('addModel.toolCallingTitle') }}</span>
              <span>{{ showToolHint ? '▾' : '▸' }}</span>
            </button>
            <div v-if="showToolHint" class="mt-2 space-y-2 text-[11px] text-muted-foreground">
              <p>{{ $t('addModel.toolCallingDesc') }}</p>
              <p class="font-medium text-foreground">{{ $t('addModel.toolCallingPresetHint') }}</p>
              <div class="flex flex-wrap gap-1.5">
                <Button
                  v-for="t in TOOL_PRESETS"
                  :key="t.label"
                  size="sm"
                  variant="outline"
                  class="h-7 text-[11px]"
                  :title="`tool_call_parser=${t.parser}${t.reasoning ? ` · reasoning_parser=${t.reasoning}` : ''}`"
                  @click="applyToolPreset(t.parser, t.reasoning)"
                >
                  {{ t.label.startsWith('addModel.') ? $t(t.label) : t.label }}
                </Button>
              </div>
              <p class="text-muted-foreground/80">
                {{ $t('addModel.toolCallingDocRef') }}
              </p>
            </div>
          </div>
        </div>

        <!-- LoRA adapters (static mount at serve time) -->
        <div>
          <div class="mb-1.5 flex items-center justify-between">
            <span class="text-xs font-medium text-muted-foreground">{{ $t('addModel.loraTitle') }}</span>
            <Button size="sm" variant="ghost" @click="addLora"><Plus class="size-3.5" />{{ $t('addModel.addLora') }}</Button>
          </div>
          <div class="space-y-1.5">
            <div v-for="(l, i) in loras" :key="i" class="space-y-1">
              <div class="flex items-center gap-2">
                <Input
                  v-model="l.name"
                  :placeholder="$t('addModel.loraServedName')"
                  class="flex-1 font-mono text-xs"
                  @update:model-value="ensureEnableLora"
                />
                <!-- Pick from the LoRA library, or keep a free-typed path. -->
                <select
                  v-model="l.path"
                  class="h-9 flex-[1.4] rounded-md border border-input bg-background/40 px-2 font-mono text-xs"
                  @change="onPickLora(l)"
                >
                  <option value="">{{ $t('addModel.loraPickAdapter') }}</option>
                  <option v-for="a in loraLibrary" :key="a.path" :value="a.path">
                    {{ a.name }}{{ a.rank != null ? ` (r${a.rank})` : '' }}
                  </option>
                  <option v-if="l.path && !loraLibrary.some((a) => a.path === l.path)" :value="l.path">
                    {{ l.path }} {{ $t('addModel.loraTyped') }}
                  </option>
                </select>
                <Button size="icon-sm" variant="ghost" @click="removeLora(i)"><Trash2 class="size-3.5" /></Button>
              </div>
              <p v-if="loraBaseWarning(l)" class="flex items-center gap-1 text-[11px] text-status-failed">
                <AlertTriangle class="size-3" />{{ loraBaseWarning(l) }}
              </p>
            </div>
            <p v-if="!loras.length" class="text-xs text-muted-foreground">{{ $t('addModel.noLora') }}</p>
            <p v-else class="text-[11px] text-muted-foreground/80">{{ $t('addModel.loraAutoHint') }}</p>
          </div>
        </div>

        <div class="flex items-center justify-end gap-2 pt-2">
          <Badge v-if="!isEdit && !groupExists" variant="muted">{{ $t('addModel.newGroup') }}</Badge>
          <Button variant="ghost" @click="open = false">{{ $t('common.cancel') }}</Button>
          <Button :disabled="!canSubmit || creating" @click="submit">
            <Loader2 v-if="creating" class="size-4 animate-spin" /><Plus v-else class="size-4" />
            {{ isEdit ? $t('addModel.saveChanges') : $t('addModel.addModelBtn') }}
          </Button>
        </div>
      </template>
    </div>
  </Dialog>
</template>
