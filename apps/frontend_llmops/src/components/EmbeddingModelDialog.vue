<script setup lang="ts">
import { ref, watch } from 'vue'
import { Loader2, Plus, Save, Trash2 } from '@lucide/vue'
import Dialog from '@/components/ui/Dialog.vue'
import Input from '@/components/ui/Input.vue'
import Button from '@/components/ui/Button.vue'
import Badge from '@/components/ui/Badge.vue'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/lib/toast'
import { useAuth } from '@/composables/useAuth'
import { useModelsStore } from '@/stores/models'
import type { EmbeddingModelParams, SettingValue } from '@/types/api'

const open = defineModel<boolean>('open', { default: false })
const props = defineProps<{
  modelType: 'embedding' | 'reranking'
  name: string
  params: EmbeddingModelParams | null
}>()
const emit = defineEmits<{ updated: [] }>()

const models = useModelsStore()
const { ensureUnlocked } = useAuth()
const rows = ref<{ key: string; value: string }[]>([])
const saving = ref(false)

function coerce(v: string): SettingValue {
  const t = v.trim()
  if (t === '') return ''
  if (t === 'true' || t === 'false') return t === 'true'
  if (!Number.isNaN(Number(t))) return Number(t)
  return v
}

watch(open, (v) => {
  if (!v) return
  rows.value = Object.entries(props.params ?? {}).map(([key, val]) => ({
    key,
    value: val == null ? '' : String(val),
  }))
})

function addRow() {
  rows.value.push({ key: '', value: '' })
}
function removeRow(i: number) {
  rows.value.splice(i, 1)
}

async function submit() {
  if (saving.value) return
  if (!(await ensureUnlocked())) return
  const settings: Record<string, SettingValue> = {}
  for (const { key, value } of rows.value) {
    if (key.trim()) settings[key.trim()] = coerce(value)
  }
  saving.value = true
  try {
    await api.updateEmbeddingModel(props.modelType, props.name, settings)
    toast.success(`已更新 ${props.name}`, { description: '變更將於 embedding server 下次啟動時生效。' })
    void models.loadConfig()
    emit('updated')
    open.value = false
  } catch (e) {
    toast.error('更新失敗', { description: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e) })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog v-model:open="open" :title="`編輯 ${name}`" width-class="max-w-xl">
    <div class="space-y-4">
      <p class="flex items-center gap-2 text-sm text-muted-foreground">
        <Badge variant="muted">{{ modelType === 'embedding' ? '嵌入' : '重排序' }}</Badge>
        <span>參數於 embedding server <span class="font-medium text-foreground">停止後</span>編輯，下次啟動生效。</span>
      </p>

      <div>
        <div class="mb-1.5 flex items-center justify-between">
          <span class="text-xs font-medium text-muted-foreground">參數</span>
          <Button size="sm" variant="ghost" @click="addRow"><Plus class="size-3.5" />新增</Button>
        </div>
        <div class="space-y-1.5">
          <div v-for="(r, i) in rows" :key="i" class="flex items-center gap-2">
            <Input v-model="r.key" placeholder="參數名（snake_case）" class="flex-1 font-mono text-xs" />
            <Input v-model="r.value" placeholder="值（true/false/數字/字串）" class="flex-1 font-mono text-xs" />
            <Button size="icon-sm" variant="ghost" @click="removeRow(i)"><Trash2 class="size-3.5" /></Button>
          </div>
          <p v-if="!rows.length" class="text-xs text-muted-foreground">無參數。</p>
        </div>
      </div>

      <div class="flex justify-end gap-2 pt-2">
        <Button variant="ghost" @click="open = false">取消</Button>
        <Button :disabled="saving" @click="submit">
          <Loader2 v-if="saving" class="size-4 animate-spin" /><Save v-else class="size-4" />儲存
        </Button>
      </div>
    </div>
  </Dialog>
</template>
