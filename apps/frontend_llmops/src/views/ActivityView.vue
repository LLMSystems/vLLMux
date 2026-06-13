<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RefreshCw } from '@lucide/vue'
import { api } from '@/lib/api'
import { useModelsStore } from '@/stores/models'
import Card from '@/components/ui/Card.vue'
import CardContent from '@/components/ui/CardContent.vue'
import Button from '@/components/ui/Button.vue'
import StatusDot from '@/components/StatusDot.vue'
import Badge from '@/components/ui/Badge.vue'
import { formatTime, timeAgo } from '@/lib/utils'
import type { StateEvent } from '@/types/api'

const models = useModelsStore()
const events = ref<StateEvent[]>([])
const filter = ref('')
let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  try {
    events.value = await api.getEvents(200)
  } catch {
    /* ignore */
  }
}
onMounted(() => {
  void load()
  timer = setInterval(load, 5000)
})
onUnmounted(() => timer && clearInterval(timer))

const filtered = computed(() =>
  filter.value ? events.value.filter((e) => e.key.split('::')[0] === filter.value) : events.value,
)

const stateColor: Record<string, string> = {
  ready: 'text-status-ready',
  starting: 'text-status-starting',
  stopping: 'text-status-stopping',
  failed: 'text-status-failed',
  stopped: 'text-status-stopped',
}
</script>

<template>
  <div class="space-y-6 p-6">
    <div class="flex flex-wrap items-center gap-3">
      <select
        v-model="filter"
        class="h-8 rounded-md border border-input bg-background/40 px-2 text-xs"
      >
        <option value="">All models</option>
        <option v-for="m in models.models" :key="m.key" :value="m.key.split('::')[0]">
          {{ m.key.split('::')[0] }}
        </option>
      </select>
      <span class="text-sm text-muted-foreground tabular">{{ filtered.length }} events</span>
      <Button variant="outline" size="sm" class="ml-auto" @click="load"><RefreshCw class="size-3.5" />Refresh</Button>
    </div>

    <Card>
      <CardContent class="pt-5">
        <ol class="relative space-y-5 border-l border-border/70 pl-6">
          <li v-for="ev in filtered" :key="ev.id" class="relative">
            <StatusDot :state="ev.to_state" size="lg" class="absolute -left-[1.7rem] top-1" />
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-medium">{{ ev.key.split('::')[0] }}</span>
              <Badge variant="muted">{{ ev.kind.toUpperCase() }}</Badge>
              <span class="text-sm" :class="stateColor[ev.from_state]">{{ ev.from_state }}</span>
              <span class="text-muted-foreground">→</span>
              <span class="text-sm font-medium" :class="stateColor[ev.to_state]">{{ ev.to_state }}</span>
              <span class="ml-auto text-xs text-muted-foreground tabular"
                >{{ formatTime(ev.ts) }} · {{ timeAgo(ev.ts) }}</span
              >
            </div>
            <p
              v-if="ev.detail"
              class="mt-1.5 break-words rounded-md bg-muted/50 px-3 py-2 font-mono text-xs text-muted-foreground"
            >
              {{ ev.detail }}
            </p>
          </li>
          <li v-if="!filtered.length" class="text-sm text-muted-foreground">No events recorded.</li>
        </ol>
      </CardContent>
    </Card>
  </div>
</template>
