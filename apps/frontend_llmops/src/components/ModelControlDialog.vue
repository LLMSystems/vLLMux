<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Lock } from '@lucide/vue'
import Dialog from '@/components/ui/Dialog.vue'
import Input from '@/components/ui/Input.vue'
import Button from '@/components/ui/Button.vue'
import { useModelControl } from '@/composables/useModelControl'

const { dialogOpen, pending, submitPassword } = useModelControl()
const password = ref('')
const error = ref(false)

// Human-readable target: a single model name, or "N instances" for bulk.
const target = computed(() => {
  const keys = pending.value?.keys ?? []
  if (keys.length === 1) return keys[0]!.split('::')[0]
  return `${keys.length} instances`
})

watch(dialogOpen, (open) => {
  if (open) {
    password.value = ''
    error.value = false
  }
})

function confirm() {
  if (!submitPassword(password.value)) error.value = true
}
</script>

<template>
  <Dialog v-model:open="dialogOpen" title="確認模型控制">
    <div class="space-y-4">
      <p class="flex items-center gap-2 text-sm text-muted-foreground">
        <Lock class="size-4" />
        <span>
          請輸入控制密碼以
          <span class="font-medium text-foreground">{{ pending?.action }}</span>
          <span class="font-mono text-foreground"> {{ target }}</span>。
        </span>
      </p>
      <div>
        <Input
          v-model="password"
          type="password"
          placeholder="控制密碼"
          :class="error ? 'border-status-failed focus-visible:ring-status-failed' : ''"
          @keydown.enter="confirm"
        />
        <p v-if="error" class="mt-1.5 text-xs text-status-failed">密碼錯誤。</p>
      </div>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="dialogOpen = false">取消</Button>
        <Button @click="confirm">確認</Button>
      </div>
    </div>
  </Dialog>
</template>
