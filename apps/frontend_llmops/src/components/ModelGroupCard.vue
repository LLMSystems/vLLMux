<script setup lang="ts">
import { computed, ref } from 'vue'
import { Box, ChevronDown, Loader2, Play, Plus, Power, RotateCw, Sparkles, Square } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Tooltip from '@/components/ui/Tooltip.vue'
import StatusDot from '@/components/StatusDot.vue'
import { useModelsStore } from '@/stores/models'
import { useTrafficStore } from '@/stores/traffic'
import { useModelControl } from '@/composables/useModelControl'
import type { GroupLoad, ModelState, ModelView } from '@/types/api'

const props = defineProps<{ group: string; instances: ModelView[]; load?: GroupLoad }>()
const emit = defineEmits<{ open: [key: string]; 'add-instance': [group: string] }>()

const { t } = useI18n()
const models = useModelsStore()
const traffic = useTrafficStore()
const control = useModelControl()

const COLLAPSE_AT = 4
const expanded = ref(false)
const collapsible = computed(() => props.instances.length > COLLAPSE_AT + 1)
const visibleInstances = computed(() =>
  collapsible.value && !expanded.value ? props.instances.slice(0, COLLAPSE_AT) : props.instances,
)
const hiddenCount = computed(() => props.instances.length - visibleInstances.value.length)

const kind = computed(() => props.instances[0]?.kind ?? 'llm')
const modelTag = computed<string | null>(() => {
  const tag =
    props.instances[0]?.model_tag ??
    models.engineConfig(props.instances[0]?.key ?? '')?.settings?.model_tag
  return tag != null ? String(tag) : null
})

const embeddingServer = computed(() => models.config?.embedding_server ?? null)
const servedModels = computed(() => {
  const server = embeddingServer.value
  if (kind.value !== 'embedding' || !server) return []
  return [
    ...Object.entries(server.embedding_models).map(([name, params]) => ({
      name,
      type: 'embed' as const,
      params,
    })),
    ...Object.entries(server.reranking_models).map(([name, params]) => ({
      name,
      type: 'rerank' as const,
      params,
    })),
  ]
})
const subtitle = computed(() => {
  if (kind.value === 'llm') return modelTag.value ?? 'embedding / reranker'
  const server = embeddingServer.value
  if (!server) return 'embedding / reranker'
  const parts: string[] = []
  const embedCount = Object.keys(server.embedding_models).length
  const rerankCount = Object.keys(server.reranking_models).length
  if (embedCount) parts.push(`${embedCount} ${t('modelGroup.embedding')}`)
  if (rerankCount) parts.push(`${rerankCount} ${t('modelGroup.reranking')}`)
  return parts.join(' / ') || 'embedding / reranker'
})

const readyCount = computed(() => props.instances.filter((m) => m.state === 'ready').length)
const total = computed(() => props.instances.length)

const headerState = computed<ModelState>(() => {
  const states = props.instances.map((m) => m.state)
  if (states.includes('failed')) return 'failed'
  if (states.some((state) => state === 'starting' || state === 'stopping')) return 'starting'
  if (states.every((state) => state === 'ready')) return 'ready'
  return 'stopped'
})

// Tint the ready/total count by overall group health for an at-a-glance read.
const countClass = computed(
  () =>
    ({
      ready: 'text-status-ready',
      starting: 'text-status-starting',
      stopping: 'text-status-starting',
      failed: 'text-status-failed',
      stopped: 'text-muted-foreground',
      sleeping: 'text-status-sleeping',
    })[headerState.value],
)

// When every instance sits on the same GPU, surface it once in the header instead of
// repeating an identical "GPU n" badge on every row.
const uniformGpu = computed(() => {
  const vals = props.instances.map((m) => models.gpuForKey(m.key, m.kind))
  return vals.length && vals.every((v) => v !== null && v === vals[0]) ? vals[0] : null
})

const startableKeys = computed(() =>
  props.instances.filter((m) => !['ready', 'starting'].includes(m.state)).map((m) => m.key),
)
const stoppableKeys = computed(() =>
  props.instances.filter((m) => m.managed && m.state !== 'stopped').map((m) => m.key),
)

function metricsFor(model: ModelView) {
  if (model.state !== 'ready' && model.state !== 'starting') return null
  const instance = model.key.split('::')[1] ?? ''
  return traffic.metrics[props.group]?.[instance] ?? null
}

function loadPct(model: ModelView): number {
  const metrics = metricsFor(model)
  if (!metrics) return 0
  const score =
    (metrics.waiting ?? 0) * 10 +
    (metrics.running ?? 0) * 3 +
    (metrics.kv_cache_usage_perc ?? 0) * 100
  return Math.min(100, score)
}

function kvText(model: ModelView): string {
  const kv = metricsFor(model)?.kv_cache_usage_perc
  return kv == null ? '--' : `${(kv * 100).toFixed(0)}%`
}

const Icon = computed(() => (kind.value === 'llm' ? Sparkles : Box))
// Kind accent — LLM vs embedding read at a glance from the tinted icon chip.
const kindVar = computed(() => (kind.value === 'llm' ? 'var(--chart-1)' : 'var(--chart-4)'))
const kindChipStyle = computed(() => ({
  color: kindVar.value,
  backgroundColor: `color-mix(in oklch, ${kindVar.value} 12%, transparent)`,
  borderColor: `color-mix(in oklch, ${kindVar.value} 30%, transparent)`,
}))

function isBusy(key: string) {
  return models.pending.has(key)
}

function isRunning(state: ModelState) {
  return state === 'ready' || state === 'starting'
}

function canStop(model: ModelView) {
  return isRunning(model.state) || (model.state === 'failed' && model.pid != null)
}

const startLocked = computed(() => kind.value === 'llm' && control.isLlmStarting.value)
const startLockTitle = computed(() =>
  startLocked.value
    ? t('modelDetail.startLocked', { name: control.startingLlmName() })
    : t('modelDetail.startLabel'),
)
</script>

<template>
  <Card
    glass
    class="overflow-hidden transition-all duration-200 hover:-translate-y-0.5 hover:border-border hover:shadow-md"
  >
    <div class="flex items-center justify-between gap-3 px-4 py-3">
      <div class="flex min-w-0 items-center gap-3">
        <div
          class="flex size-8 shrink-0 items-center justify-center rounded-lg border"
          :style="kindChipStyle"
        >
          <component :is="Icon" class="size-4" />
        </div>
        <div class="min-w-0">
          <p class="truncate text-sm font-semibold leading-tight" :title="group">{{ group }}</p>
          <p class="truncate font-mono text-[11px] text-muted-foreground" :title="modelTag ?? subtitle">
            {{ subtitle }}
          </p>
        </div>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <Badge
          v-if="uniformGpu !== null"
          variant="muted"
          class="px-1.5 py-0 text-[10px]"
        >
          GPU {{ uniformGpu }}
        </Badge>
        <Badge
          v-if="load && load.asleep_replicas"
          variant="sleeping"
          class="tabular"
          :title="$t('modelGroup.asleepTitle')"
        >
          {{ t('modelGroup.asleepCount', { n: load.asleep_replicas }) }}
        </Badge>
        <Badge
          v-if="load && load.waiting_total > 0"
          variant="starting"
          class="tabular"
          :title="$t('modelGroup.queueTitle')"
        >
          {{ t('modelGroup.queue', { n: Math.round(load.waiting_total) }) }}
        </Badge>
        <div class="flex items-center gap-1.5">
          <StatusDot :state="headerState" />
          <span class="tabular text-xs font-medium" :class="countClass">
            {{ t('modelGroup.readyCount', { ready: readyCount, total }) }}
          </span>
        </div>
      </div>
    </div>

    <div class="divide-y divide-border/40 border-t border-border/40">
      <div
        v-for="model in visibleInstances"
        :key="model.key"
        class="group/row flex cursor-pointer items-center gap-2.5 px-4 py-2 transition-colors hover:bg-accent/40"
        @click="emit('open', model.key)"
      >
        <StatusDot :state="model.state" size="sm" />
        <span class="font-mono text-[13px] font-medium">{{ model.key.split('::')[1] }}</span>
        <Badge
          v-if="uniformGpu === null && models.gpuForKey(model.key, model.kind) !== null"
          variant="muted"
          class="px-1.5 py-0 text-[10px]"
        >
          GPU {{ models.gpuForKey(model.key, model.kind) }}
        </Badge>
        <span class="font-mono text-[11px] text-muted-foreground">:{{ model.port }}</span>
        <Tooltip
          v-if="model.restart_count"
          :text="t('modelGroup.crashRestart', { n: model.restart_count })"
        >
          <span class="flex items-center gap-0.5 text-[10px] text-status-starting">
            <RotateCw class="size-3" />{{ model.restart_count }}
          </span>
        </Tooltip>

        <div class="ml-auto flex items-center gap-2.5">
          <Tooltip v-if="metricsFor(model)">
            <div class="hidden cursor-help items-center gap-1.5 sm:flex" @click.stop>
              <div class="h-1 w-10 overflow-hidden rounded-full bg-muted">
                <div
                  class="h-full rounded-full bg-[var(--chart-1)] transition-all"
                  :style="{ width: `${Math.max(4, loadPct(model))}%` }"
                />
              </div>
              <span class="w-16 tabular text-[11px] text-muted-foreground">
                {{ metricsFor(model)!.running ?? 0 }}r / {{ metricsFor(model)!.waiting ?? 0 }}w
              </span>
            </div>
            <template #content>
              <p class="font-medium">{{ t('modelGroup.liveLoad') }}</p>
              <ul class="mt-1 space-y-0.5 text-muted-foreground">
                <li>
                  <span class="tabular text-foreground">{{ metricsFor(model)!.running ?? '--' }}</span>
                  {{ t('modelGroup.runningDesc') }}
                </li>
                <li>
                  <span class="tabular text-foreground">{{ metricsFor(model)!.waiting ?? '--' }}</span>
                  {{ t('modelGroup.waitingDesc') }}
                </li>
                <li>{{ t('modelGroup.kvCacheUsed', { pct: kvText(model) }) }}</li>
              </ul>
            </template>
          </Tooltip>

          <Button
            v-if="!canStop(model)"
            size="icon-sm"
            variant="ghost"
            class="text-status-ready hover:bg-status-ready/15 hover:text-status-ready"
            :disabled="isBusy(model.key) || startLocked"
            :title="startLockTitle"
            @click.stop="control.request(model.key, 'start')"
          >
            <Loader2 v-if="isBusy(model.key)" class="size-3.5 animate-spin" />
            <Play v-else class="size-3.5" />
          </Button>
          <Button
            v-else
            size="icon-sm"
            variant="outline"
            :disabled="!model.managed || model.state === 'stopping'"
            :title="
              !model.managed
                ? t('modelGroup.externalNotManaged')
                : model.state === 'failed'
                  ? t('modelGroup.terminateHint')
                  : model.state === 'starting'
                    ? t('modelGroup.abortStartup')
                    : t('modelGroup.stopHint')
            "
            @click.stop="control.request(model.key, 'stop')"
          >
            <Loader2 v-if="isBusy(model.key)" class="size-3.5 animate-spin" />
            <Square v-else class="size-3.5" />
          </Button>
        </div>
      </div>

      <button
        v-if="collapsible"
        class="flex w-full items-center justify-center gap-1 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent/40 hover:text-foreground"
        @click="expanded = !expanded"
      >
        <ChevronDown class="size-3.5 transition-transform" :class="expanded && 'rotate-180'" />
        {{ expanded ? t('modelGroup.collapse') : t('modelGroup.showMore', { n: hiddenCount }) }}
      </button>
    </div>

    <div v-if="servedModels.length" class="border-t border-border/40 px-4 py-2.5">
      <p class="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {{ t('modelGroup.servedModels') }}
      </p>
      <div class="flex flex-wrap gap-1.5">
        <span
          v-for="served in servedModels"
          :key="served.name"
          class="flex items-center gap-1 rounded-md border border-border/60 bg-background/40 px-1.5 py-0.5"
        >
          <Badge
            variant="muted"
            class="px-1 py-0 text-[9px]"
            :class="served.type === 'embed' ? 'text-[var(--chart-4)]' : 'text-[var(--chart-2)]'"
          >
            {{ served.type === 'embed' ? t('modelGroup.embedding') : t('modelGroup.reranking') }}
          </Badge>
          <span class="font-mono text-[11px]">{{ served.name }}</span>
          <span v-if="served.params.max_length != null" class="text-[10px] text-muted-foreground">
            / len {{ served.params.max_length }}
          </span>
        </span>
      </div>
    </div>

    <div v-if="kind === 'llm' || instances.length > 1" class="flex gap-2 border-t border-border/40 p-3">
      <template v-if="instances.length > 1">
        <Button
          size="sm"
          variant="success"
          class="flex-1"
          :disabled="!startableKeys.length || startLocked"
          :title="startLocked ? startLockTitle : t('modelGroup.startAll')"
          @click="control.requestMany(startableKeys, 'start')"
        >
          <Play class="size-3.5" />{{ t('modelGroup.startAll') }}
        </Button>
        <Button
          size="sm"
          variant="outline"
          class="flex-1"
          :disabled="!stoppableKeys.length"
          @click="control.requestMany(stoppableKeys, 'stop')"
        >
          <Power class="size-3.5" />{{ t('modelGroup.stopAll') }}
        </Button>
      </template>
      <Button
        v-if="kind === 'llm'"
        size="sm"
        variant="outline"
        :class="instances.length > 1 ? '' : 'flex-1'"
        :title="t('modelGroup.addInstance')"
        @click="emit('add-instance', group)"
      >
        <Plus class="size-3.5" />{{ t('modelGroup.addInstance') }}
      </Button>
    </div>
  </Card>
</template>
