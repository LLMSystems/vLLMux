<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { AlertTriangle, Check, Download, Loader2, Plus, Trash2, Wand2 } from '@lucide/vue'
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
import type { CachedModel, DownloadJob, LoraAdapter, LoraModule, SettingValue } from '@/types/api'

const open = defineModel<boolean>('open', { default: false })
const props = defineProps<{ mode?: 'create' | 'edit'; editKey?: string | null }>()
const emit = defineEmits<{ created: [key: string]; updated: [key: string] }>()

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
    : `此 adapter 的 base 是 ${a.base_model}，與本模型 ${modelTag.value || '(未填)'} 不符`
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
    toast.success(`開始下載 ${repo}`, { description: '可關閉此視窗，下載會在背景繼續。' })
    await loadDownloads()
  } catch (e) {
    toast.error('無法開始下載', { description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e) })
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
  loras.value = []
}

/** Pull `lora_modules` out of a settings/model_config object into the LoRA
 *  editor, returning the remaining flat [key, value] entries for the param list. */
function extractLoras(entries: [string, unknown][]): [string, unknown][] {
  loras.value = []
  const rest: [string, unknown][] = []
  for (const [k, v] of entries) {
    if (k === 'lora_modules' && Array.isArray(v)) {
      loras.value = (v as LoraModule[]).map((m) => ({
        name: m.name ?? '',
        path: m.path ?? '',
        base_model_name: m.base_model_name ?? '',
      }))
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
  params.value = extractLoras(
    Object.entries(cfg.settings).filter(([k2]) => k2 !== 'model_tag'),
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
    params.value = extractLoras(
      Object.entries(p.model_config).filter(([k]) => k !== 'model_tag'),
    ).map(([k, v]) => ({ key: k, value: String(v) }))
    warnings.value = p.warnings
    parsed.value = true
  } catch (e) {
    toast.error('無法解析指令', { description: e instanceof ApiError ? e.message : String(e) })
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
  { label: 'Qwen3（含 thinking）', parser: 'hermes', reasoning: 'qwen3' },
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

async function submit() {
  if (!canSubmit.value || creating.value) return
  creating.value = true
  const settings: Record<string, SettingValue> & { lora_modules?: LoraModule[] } = {
    model_tag: modelTag.value,
  }
  for (const { key: k, value } of params.value) {
    if (k.trim()) settings[k.trim()] = coerce(value)
  }
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
      toast.success(`已更新 ${view.key}`, { description: '變更將於下次啟動時生效。' })
      // Keep the store's cached config in sync so the next edit prefills fresh.
      void models.loadConfig()
      if (!(await api.routerReload())) {
        toast.warning('路由器未重新整理', { description: '變更已儲存，但路由器無法連線。' })
      }
      emit('updated', view.key)
    } else {
      const view = await api.createModel(payload)
      toast.success(`已新增 ${view.key}`, { description: '目前已停止 — 請按「啟動」以啟用。' })
      void models.loadConfig()
      // Make it routable end-to-end by refreshing the router's view of the config.
      if (!(await api.routerReload())) {
        toast.warning('路由器未重新整理', {
          description: '模型已新增，但路由器無法連線 — 重新載入前將無法路由至此模型。',
        })
      }
      emit('created', view.key)
    }
    open.value = false
  } catch (e) {
    toast.error(isEdit.value ? '更新模型失敗' : '新增模型失敗', {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <Dialog v-model:open="open" :title="isEdit ? '編輯模型' : '新增模型'" width-class="max-w-2xl">
    <div class="space-y-4">
      <!-- Paste + parse (create only) -->
      <div v-if="!isEdit">
        <label class="text-xs font-medium text-muted-foreground">貼上 vLLM 啟動指令</label>
        <Textarea
          v-model="command"
          placeholder="CUDA_VISIBLE_DEVICES=0 vllm serve Qwen/Qwen2.5-3B-Instruct --port 8020 --dtype float16 --max-model-len 4096 --gpu-memory-utilization 0.85"
          class="mt-1 min-h-[80px] font-mono text-xs"
        />
        <Button class="mt-2" size="sm" :disabled="!command.trim() || parsing" @click="parse">
          <Loader2 v-if="parsing" class="size-4 animate-spin" /><Wand2 v-else class="size-4" />
          解析指令
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
            vLLM 參數為群組共用 — 此變更將套用至群組 <span class="font-mono">{{ group }}</span> 的全部 {{ groupSiblings + 1 }} 個副本。
          </p>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <label class="block">
            <span class="text-xs text-muted-foreground">群組</span>
            <Input v-model="group" class="mt-1" :disabled="isEdit" />
            <span v-if="!isEdit && groupExists && !keyExists" class="mt-1 block text-[11px] text-muted-foreground">
              已存在群組 — 將新增為新副本。
            </span>
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">實例 ID</span>
            <Input v-model="instanceId" class="mt-1" :disabled="isEdit" :class="!isEdit && keyExists ? 'border-status-failed' : ''" />
            <span v-if="!isEdit && keyExists" class="mt-1 block text-[11px] text-status-failed">
              {{ key }} 已存在。
            </span>
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">主機</span>
            <Input v-model="host" class="mt-1" />
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">連接埠</span>
            <Input v-model.number="port" type="number" class="mt-1" :class="portInUse ? 'border-status-failed' : ''" />
            <span v-if="portInUse" class="mt-1 block text-[11px] text-status-failed">
              連接埠 {{ port }} 已被其他實例使用。
            </span>
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">GPU（cuda_device）</span>
            <select
              v-model="cudaDevice"
              class="mt-1 h-9 w-full rounded-md border border-input bg-background/40 px-2 text-sm"
            >
              <option :value="null">無 / 自動</option>
              <option v-for="i in gpuOptions" :key="i" :value="i">cuda:{{ i }}</option>
            </select>
          </label>
          <label class="block">
            <span class="text-xs text-muted-foreground">模型標籤 <span class="text-status-failed">*</span></span>
            <Input v-model="modelTag" class="mt-1 font-mono" placeholder="org/model" />
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
            <span class="text-muted-foreground">下載權重中…</span>
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
            <span class="text-status-ready">權重已快取</span>
            <span class="ml-auto tabular text-muted-foreground">{{ formatBytes(cachedEntry.size_on_disk) }}</span>
          </template>
          <!-- Failed -->
          <template v-else-if="downloadJob && downloadJob.state === 'failed'">
            <AlertTriangle class="size-3.5 shrink-0 text-status-failed" />
            <span class="truncate text-status-failed">下載失敗：{{ downloadJob.error }}</span>
            <Button size="sm" variant="ghost" class="ml-auto shrink-0" @click="downloadWeights">
              <Download class="size-3.5" />重試
            </Button>
          </template>
          <!-- Not cached -->
          <template v-else>
            <span class="text-muted-foreground">權重尚未快取 — 首次啟動會即時下載（較慢）。</span>
            <Button size="sm" variant="ghost" class="ml-auto shrink-0" @click="downloadWeights">
              <Download class="size-3.5" />先下載
            </Button>
          </template>
        </div>

        <!-- vLLM params -->
        <div>
          <div class="mb-1.5 flex items-center justify-between">
            <span class="text-xs font-medium text-muted-foreground">vLLM 參數（model_config）</span>
            <Button size="sm" variant="ghost" @click="addParam"><Plus class="size-3.5" />新增</Button>
          </div>
          <div class="space-y-1.5">
            <div v-for="(p, i) in params" :key="i" class="flex items-center gap-2">
              <Input v-model="p.key" placeholder="旗標（snake_case）" class="flex-1 font-mono text-xs" />
              <Input v-model="p.value" placeholder="值" class="flex-1 font-mono text-xs" />
              <Button size="icon-sm" variant="ghost" @click="removeParam(i)"><Trash2 class="size-3.5" /></Button>
            </div>
            <p v-if="!params.length" class="text-xs text-muted-foreground">無額外參數。</p>
          </div>

          <!-- Tool-calling hint + quick presets -->
          <div class="mt-2 rounded-md border border-border/60 bg-muted/30 p-2.5">
            <button
              type="button"
              class="flex w-full items-center justify-between text-xs font-medium text-muted-foreground hover:text-foreground"
              @click="showToolHint = !showToolHint"
            >
              <span>🛠 工具調用（tool calling）參數參考</span>
              <span>{{ showToolHint ? '▾' : '▸' }}</span>
            </button>
            <div v-if="showToolHint" class="mt-2 space-y-2 text-[11px] text-muted-foreground">
              <p>
                要讓模型支援 <span class="font-mono">tool_choice="auto"</span>，需加
                <span class="font-mono">enable_auto_tool_choice=true</span> +
                <span class="font-mono">tool_call_parser=&lt;parser&gt;</span>，reasoning 模型再加
                <span class="font-mono">reasoning_parser</span>。parser 要對得上模型輸出格式，別看品牌猜。
              </p>
              <p class="font-medium text-foreground">點一下帶入推薦參數：</p>
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
                  {{ t.label }}
                </Button>
              </div>
              <p class="text-muted-foreground/80">
                完整對照見 <span class="font-mono">docs/vllm_auto_tool_整理.md</span>。
                沒有對應 parser 的模型（如 SmolLM2 / TinyLlama / Phi-3.5）請勿亂加。
              </p>
            </div>
          </div>
        </div>

        <!-- LoRA adapters (static mount at serve time) -->
        <div>
          <div class="mb-1.5 flex items-center justify-between">
            <span class="text-xs font-medium text-muted-foreground">LoRA Adapters</span>
            <Button size="sm" variant="ghost" @click="addLora"><Plus class="size-3.5" />新增</Button>
          </div>
          <div class="space-y-1.5">
            <div v-for="(l, i) in loras" :key="i" class="space-y-1">
              <div class="flex items-center gap-2">
                <Input
                  v-model="l.name"
                  placeholder="served name（如 sql-lora）"
                  class="flex-1 font-mono text-xs"
                  @update:model-value="ensureEnableLora"
                />
                <!-- Pick from the LoRA library, or keep a free-typed path. -->
                <select
                  v-model="l.path"
                  class="h-9 flex-[1.4] rounded-md border border-input bg-background/40 px-2 font-mono text-xs"
                  @change="onPickLora(l)"
                >
                  <option value="">— 選 adapter / 自填 path —</option>
                  <option v-for="a in loraLibrary" :key="a.path" :value="a.path">
                    {{ a.name }}{{ a.rank != null ? ` (r${a.rank})` : '' }}
                  </option>
                  <option v-if="l.path && !loraLibrary.some((a) => a.path === l.path)" :value="l.path">
                    {{ l.path }}（自填）
                  </option>
                </select>
                <Button size="icon-sm" variant="ghost" @click="removeLora(i)"><Trash2 class="size-3.5" /></Button>
              </div>
              <p v-if="loraBaseWarning(l)" class="flex items-center gap-1 text-[11px] text-status-failed">
                <AlertTriangle class="size-3" />{{ loraBaseWarning(l) }}
              </p>
            </div>
            <p v-if="!loras.length" class="text-xs text-muted-foreground">
              無 LoRA。新增一列會自動補上 <span class="font-mono">enable_lora=true</span>；
              served name 即推論時 <span class="font-mono">model</span> 欄位要填的名稱。adapter 從
              <span class="font-mono">LoRA 庫</span>挑選，或自填 path。
            </p>
            <p v-else class="text-[11px] text-muted-foreground/80">
              從庫選 adapter 會自動帶入 base 並把 <span class="font-mono">max_lora_rank</span> 設到對齊的 rank。
              Base model 須支援 LoRA（vLLM <span class="font-mono">SupportsLoRA</span>）。
            </p>
          </div>
        </div>

        <div class="flex items-center justify-end gap-2 pt-2">
          <Badge v-if="!isEdit && !groupExists" variant="muted">新群組</Badge>
          <Button variant="ghost" @click="open = false">取消</Button>
          <Button :disabled="!canSubmit || creating" @click="submit">
            <Loader2 v-if="creating" class="size-4 animate-spin" /><Plus v-else class="size-4" />
            {{ isEdit ? '儲存變更' : '新增模型' }}
          </Button>
        </div>
      </template>
    </div>
  </Dialog>
</template>
