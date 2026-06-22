<script setup lang="ts">
import { ref, watch } from 'vue'
import { Lock } from '@lucide/vue'
import Dialog from '@/components/ui/Dialog.vue'
import Input from '@/components/ui/Input.vue'
import Button from '@/components/ui/Button.vue'
import { useAuth } from '@/composables/useAuth'

const { dialogOpen, submitToken, cancel } = useAuth()
const token = ref('')
const error = ref(false)
const busy = ref(false)

watch(dialogOpen, (open) => {
  if (open) {
    token.value = ''
    error.value = false
  }
})

async function confirm() {
  if (!token.value || busy.value) return
  busy.value = true
  error.value = false
  try {
    if (!(await submitToken(token.value))) error.value = true
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog v-model:open="dialogOpen" :title="$t('modelControl.title')" @update:open="(v) => !v && cancel()">
    <div class="space-y-4">
      <p class="flex items-center gap-2 text-sm text-muted-foreground">
        <Lock class="size-4" />
        <span>{{ $t('modelControl.description') }}</span>
      </p>
      <div>
        <Input
          v-model="token"
          type="password"
          :placeholder="$t('modelControl.tokenPlaceholder')"
          :class="error ? 'border-status-failed focus-visible:ring-status-failed' : ''"
          @keydown.enter="confirm"
        />
        <p v-if="error" class="mt-1.5 text-xs text-status-failed">{{ $t('modelControl.tokenInvalid') }}</p>
      </div>
      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="cancel">{{ $t('common.cancel') }}</Button>
        <Button :disabled="busy || !token" @click="confirm">{{ $t('modelControl.confirm') }}</Button>
      </div>
    </div>
  </Dialog>
</template>
