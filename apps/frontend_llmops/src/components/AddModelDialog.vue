<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { AlertTriangle, Loader2, Plus, Trash2, Wand2 } from '@lucide/vue'
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
import type { SettingValue } from '@/types/api'

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

const gpuOptions = computed(() => resources.resources?.gpus.map((g) => g.index) ?? [])

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
  params.value = Object.entries(cfg.settings)
    .filter(([k2]) => k2 !== 'model_tag')
    .map(([k2, v]) => ({ key: k2, value: v === null ? '' : String(v) }))
  warnings.value = []
  parsed.value = true // skip the paste/parse step
}

watch(open, (v) => {
  if (!v) reset()
  else if (isEdit.value) prefillForEdit()
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
    params.value = Object.entries(p.model_config)
      .filter(([k]) => k !== 'model_tag')
      .map(([k, v]) => ({ key: k, value: String(v) }))
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

async function submit() {
  if (!canSubmit.value || creating.value) return
  creating.value = true
  const settings: Record<string, SettingValue> = { model_tag: modelTag.value }
  for (const { key: k, value } of params.value) {
    if (k.trim()) settings[k.trim()] = coerce(value)
  }
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
