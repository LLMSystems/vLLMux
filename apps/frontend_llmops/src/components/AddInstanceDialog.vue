<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Loader2, Plus } from '@lucide/vue'
import Dialog from '@/components/ui/Dialog.vue'
import Input from '@/components/ui/Input.vue'
import Button from '@/components/ui/Button.vue'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/lib/toast'
import { useModelsStore } from '@/stores/models'
import type { ModelView, SettingValue } from '@/types/api'

// Adds another instance to an *existing* LLM group. Instances of a group share
// one model_config, so this only asks for the per-instance fields (id / port /
// cuda_device) — the vLLM params are inherited, shown read-only. Created STOPPED;
// the user starts it manually, and the backend nudges the router to pick it up
// once it turns READY (so the router never load-balances onto a dead backend).
const open = defineModel<boolean>('open', { default: false })
const props = defineProps<{ group: string; instances: ModelView[] }>()
const emit = defineEmits<{ created: [key: string] }>()

const { t } = useI18n()
const models = useModelsStore()
const creating = ref(false)

const instanceId = ref('')
const host = ref('localhost')
const port = ref<number>(8000)
const cudaDevice = ref<number>(0)

// Shared model_config of the group (read from any existing instance's summary).
const sharedSettings = computed<Record<string, SettingValue>>(() => {
  const key = props.instances[0]?.key
  return (key && models.engineConfig(key)?.settings) || {}
})
const modelTag = computed(() => String(sharedSettings.value.model_tag ?? '—'))

const existingIds = computed(() => props.instances.map((m) => m.key.split('::')[1] ?? ''))
// Every port in use across *all* groups (LLM + embedding) — a new instance must
// not collide with any of them.
const usedPorts = computed(() => new Set(models.models.map((m) => m.port)))

function suggestId(): string {
  const base = props.group.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'inst'
  for (let n = props.instances.length + 1; ; n++) {
    const cand = `${base}-${n}`
    if (!existingIds.value.includes(cand)) return cand
  }
}
function suggestPort(): number {
  const max = Math.max(8000, ...models.models.map((m) => m.port))
  let p = max + 1
  while (usedPorts.value.has(p)) p++
  return p
}

// Seed sensible suggestions each time the dialog opens.
watch(open, (isOpen) => {
  if (!isOpen) return
  instanceId.value = suggestId()
  host.value = 'localhost'
  port.value = suggestPort()
  // Inherit the GPU the group's first instance uses; fall back to 0.
  const firstKey = props.instances[0]?.key ?? ''
  cudaDevice.value = models.engineConfig(firstKey)?.cuda_device ?? 0
})

const idConflict = computed(() => existingIds.value.includes(instanceId.value.trim()))
const portConflict = computed(() => usedPorts.value.has(port.value))
const valid = computed(
  () => !!instanceId.value.trim() && !idConflict.value && port.value > 0 && !portConflict.value,
)

async function submit() {
  if (!valid.value || creating.value) return
  creating.value = true
  try {
    // Pass the group's existing settings: the API requires model_tag, but for an
    // existing group the backend ignores model_config and just appends the
    // instance — so the shared config never diverges.
    const view = await api.createModel({
      group: props.group,
      instance: {
        id: instanceId.value.trim(),
        host: host.value.trim() || 'localhost',
        port: port.value,
        cuda_device: cudaDevice.value,
      },
      settings: sharedSettings.value,
    })
    // Deliberately NOT reloading the router here — it would route to a still-
    // stopped backend. The backend reloads it for us once this instance is READY.
    toast.success(t('addInstance.createSuccess', { key: view.key }), {
      description: t('addInstance.createSuccessDesc'),
    })
    emit('created', view.key)
    open.value = false
  } catch (e) {
    toast.error(t('addInstance.createFailed'), {
      description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
    })
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <Dialog v-model:open="open" :title="`${$t('addInstance.title')} · ${group}`" width-class="max-w-md">
    <div class="space-y-4 text-sm">
      <!-- Inherited shared config (read-only) -->
      <div class="rounded-md border border-border/60 bg-muted/30 p-3">
        <p class="text-xs font-medium text-muted-foreground">{{ $t('addInstance.sharedSettingsTitle') }}</p>
        <p class="mt-1 font-mono text-[12px]">{{ modelTag }}</p>
        <p class="mt-1 text-[11px] text-muted-foreground">
          max_model_len {{ sharedSettings.max_model_len ?? '—' }} ·
          gpu_memory_utilization {{ sharedSettings.gpu_memory_utilization ?? '—' }}
        </p>
        <p class="mt-1.5 text-[11px] text-muted-foreground">
          {{ $t('addInstance.sharedSettingsHint') }}
        </p>
      </div>

      <div class="space-y-1.5">
        <label class="text-xs font-medium text-muted-foreground">{{ $t('addInstance.instanceIdLabel') }}</label>
        <Input v-model="instanceId" :placeholder="$t('addInstance.instanceIdPlaceholder')" class="font-mono" />
        <p v-if="idConflict" class="text-[11px] text-status-failed">{{ $t('addInstance.idConflict') }}</p>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div class="space-y-1.5">
          <label class="text-xs font-medium text-muted-foreground">{{ $t('addModel.portLabel') }}</label>
          <Input v-model.number="port" type="number" min="1" class="font-mono" />
          <p v-if="portConflict" class="text-[11px] text-status-failed">{{ $t('addInstance.portConflict') }}</p>
        </div>
        <div class="space-y-1.5">
          <label class="text-xs font-medium text-muted-foreground">{{ $t('addInstance.cudaDeviceLabel') }}</label>
          <Input v-model.number="cudaDevice" type="number" min="0" class="font-mono" />
        </div>
      </div>

      <div class="space-y-1.5">
        <label class="text-xs font-medium text-muted-foreground">{{ $t('addModel.hostLabel') }}</label>
        <Input v-model="host" placeholder="localhost" class="font-mono" />
      </div>

      <div class="flex justify-end gap-2 pt-1">
        <Button variant="outline" @click="open = false">{{ $t('common.cancel') }}</Button>
        <Button :disabled="!valid || creating" @click="submit">
          <Loader2 v-if="creating" class="size-4 animate-spin" /><Plus v-else class="size-4" />{{ $t('addInstance.title') }}
        </Button>
      </div>
    </div>
  </Dialog>
</template>
