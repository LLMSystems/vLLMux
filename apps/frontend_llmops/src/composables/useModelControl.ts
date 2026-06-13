import { ref } from 'vue'
import { useModelsStore } from '@/stores/models'
import { toast } from '@/lib/toast'
import { ApiError } from '@/lib/api'

type Action = 'start' | 'stop'

// Module-level so the gate is shared app-wide and the password is only typed once.
const unlocked = ref(false)
const dialogOpen = ref(false)
const pending = ref<{ keys: string[]; action: Action } | null>(null)

const PASSWORD = import.meta.env.VITE_MODEL_CONTROL_PASSWORD ?? ''

/**
 * Password-gated start/stop, single or bulk. The first protected action opens a
 * confirm dialog; once the correct password is entered the gate stays unlocked
 * for the session. Mount <ModelControlDialog /> once (in App.vue) to render it.
 */
export function useModelControl() {
  const models = useModelsStore()

  async function runOne(key: string, action: Action) {
    const name = key.split('::')[0]
    try {
      if (action === 'start') {
        await models.start(key)
        toast.success(`Starting ${name}`, { description: 'Waiting for /health to pass…' })
      } else {
        await models.stop(key)
        toast.info(`Stopping ${name}`, { description: 'Releasing GPU resources…' })
      }
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e)
      toast.error(`Failed to ${action} ${name}`, { description: msg })
    }
  }

  function execute(keys: string[], action: Action) {
    for (const key of keys) void runOne(key, action)
  }

  function requestMany(keys: string[], action: Action) {
    if (!keys.length) return
    if (!PASSWORD || unlocked.value) {
      execute(keys, action)
      return
    }
    pending.value = { keys, action }
    dialogOpen.value = true
  }

  function request(key: string, action: Action) {
    requestMany([key], action)
  }

  function submitPassword(input: string): boolean {
    if (input !== PASSWORD) return false
    unlocked.value = true
    dialogOpen.value = false
    if (pending.value) {
      execute(pending.value.keys, pending.value.action)
      pending.value = null
    }
    return true
  }

  return { unlocked, dialogOpen, pending, request, requestMany, submitPassword }
}
