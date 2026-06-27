<script setup lang="ts">
import { computed, ref } from 'vue'
import { Box, ChevronDown, Gauge, Loader2, Play, Plus, Power, RotateCw, Shuffle, Sparkles, Square } from '@lucide/vue'
import { useI18n } from 'vue-i18n'
import Card from '@/components/ui/Card.vue'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Tooltip from '@/components/ui/Tooltip.vue'
import Dialog from '@/components/ui/Dialog.vue'
import Input from '@/components/ui/Input.vue'
import StatusDot from '@/components/StatusDot.vue'
import { useModelsStore } from '@/stores/models'
import { useTrafficStore } from '@/stores/traffic'
import { useModelControl } from '@/composables/useModelControl'
import { useAuth } from '@/composables/useAuth'
import { api } from '@/lib/api'
import { toast } from '@/lib/toast'
import type { GroupLoad, ModelState, ModelView } from '@/types/api'

const props = defineProps<{ group: string; instances: ModelView[]; load?: GroupLoad }>()
const emit = defineEmits<{ open: [key: string]; 'add-instance': [group: string] }>()

const { t } = useI18n()
const models = useModelsStore()
const traffic = useTrafficStore()
const control = useModelControl()
const { ensureUnlocked } = useAuth()

// Group-level autoscaling policy (read from the static config summary). When on,
// the autoscaler owns the instances and manual start/stop is disabled (409).
const autoscale = computed(() => {
  const k = props.instances[0]?.key
  return k ? (models.engineConfig(k)?.autoscale ?? null) : null
})
const isAutoscaled = computed(() => !!autoscale.value?.enabled)

const asOpen = ref(false)
const asEnabled = ref(false)
const asMinReady = ref(1)
const asMaxReady = ref<number>(1)
const asSaving = ref(false)

function openAutoscale() {
  const a = autoscale.value
  asEnabled.value = !!a?.enabled
  asMinReady.value = a?.min_ready ?? 1
  asMaxReady.value = a?.max_ready ?? props.instances.length
  asOpen.value = true
}

async function saveAutoscale() {
  if (asSaving.value || !(await ensureUnlocked())) return
  asSaving.value = true
  try {
    await api.setAutoscale(props.group, {
      enabled: asEnabled.value,
      min_ready: asMinReady.value,
      max_ready: asMaxReady.value,
    })
    await models.loadConfig()
    void models.refresh()
    asOpen.value = false
    toast.success(t(asEnabled.value ? 'modelGroup.autoscaleOn' : 'modelGroup.autoscaleOff', { group: props.group }))
  } catch (e) {
    toast.error(t('modelGroup.autoscaleFailed'), { description: String(e) })
  } finally {
    asSaving.value = false
  }
}

// Cross-model fallback chain: other group names to try when this group is down.
const fallback = computed(() => {
  const k = props.instances[0]?.key
  return k ? (models.engineConfig(k)?.fallback ?? null) : null
})
const otherGroups = computed(() =>
  [...new Set(Object.keys(models.config?.LLM_engines ?? {}).map((k) => k.split('::')[0] ?? k))].filter(
    (g) => g !== props.group,
  ),
)
const fbOpen = ref(false)
const fbSelected = ref<string[]>([])
const fbSaving = ref(false)

function openFallback() {
  fbSelected.value = [...(fallback.value ?? [])]
  fbOpen.value = true
}
function toggleFb(g: string) {
  const i = fbSelected.value.indexOf(g)
  if (i >= 0) fbSelected.value.splice(i, 1)
  else fbSelected.value.push(g) // selection order = fallback order
}
async function saveFallback() {
  if (fbSaving.value || !(await ensureUnlocked())) return
  fbSaving.value = true
  try {
    await api.setFallback(props.group, fbSelected.value)
    await models.loadConfig()
    fbOpen.value = false
    toast.success(t('modelGroup.fallbackSaved', { group: props.group }))
  } catch (e) {
    toast.error(t('modelGroup.fallbackFailed'), { description: String(e) })
  } finally {
    fbSaving.value = false
  }
}

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
        <button
          v-if="kind === 'llm'"
          class="flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium transition-colors"
          :class="
            isAutoscaled
              ? 'border-[var(--chart-2)]/40 bg-[var(--chart-2)]/10 text-[var(--chart-2)]'
              : 'border-border/60 text-muted-foreground hover:text-foreground'
          "
          :title="$t('modelGroup.autoscaleConfigure')"
          @click.stop="openAutoscale"
        >
          <Gauge class="size-3" />{{ isAutoscaled ? $t('modelGroup.autoBadge') : $t('modelGroup.autoOff') }}
        </button>
        <button
          v-if="kind === 'llm'"
          class="flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium transition-colors"
          :class="
            fallback && fallback.length
              ? 'border-[var(--chart-3)]/40 bg-[var(--chart-3)]/10 text-[var(--chart-3)]'
              : 'border-border/60 text-muted-foreground hover:text-foreground'
          "
          :title="fallback && fallback.length ? `${$t('modelGroup.fallbackTitleShort')}: ${fallback.join(' → ')}` : $t('modelGroup.fallbackConfigure')"
          @click.stop="openFallback"
        >
          <Shuffle class="size-3" />{{ fallback && fallback.length ? $t('modelGroup.fallbackCount', { n: fallback.length }) : $t('modelGroup.fallbackOff') }}
        </button>
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
            :disabled="isBusy(model.key) || startLocked || isAutoscaled"
            :title="isAutoscaled ? $t('modelGroup.autoscaledLocked') : startLockTitle"
            @click.stop="control.request(model.key, 'start')"
          >
            <Loader2 v-if="isBusy(model.key)" class="size-3.5 animate-spin" />
            <Play v-else class="size-3.5" />
          </Button>
          <Button
            v-else
            size="icon-sm"
            variant="outline"
            :disabled="!model.managed || model.state === 'stopping' || isAutoscaled"
            :title="
              isAutoscaled
                ? $t('modelGroup.autoscaledLocked')
                : !model.managed
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
      <div
        v-if="isAutoscaled"
        class="flex flex-1 items-center justify-center gap-1.5 py-1 text-[11px] text-muted-foreground"
      >
        <Gauge class="size-3.5 text-[var(--chart-2)]" />{{ $t('modelGroup.autoscaledFooter') }}
      </div>
      <template v-else>
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
      </template>
    </div>
  </Card>

  <!-- Autoscale config -->
  <Dialog v-model:open="asOpen" :title="$t('modelGroup.autoscaleTitle', { group })">
    <div class="space-y-4">
      <label class="flex items-center gap-3">
        <input v-model="asEnabled" type="checkbox" class="size-4 accent-[var(--chart-2)]" />
        <span class="text-sm font-medium">{{ $t('modelGroup.autoscaleEnable') }}</span>
      </label>
      <p class="text-[11px] text-muted-foreground">{{ $t('modelGroup.autoscaleHint') }}</p>
      <div class="grid grid-cols-2 gap-3" :class="!asEnabled && 'pointer-events-none opacity-50'">
        <label>
          <span class="text-xs text-muted-foreground">{{ $t('modelGroup.minReady') }}</span>
          <Input v-model.number="asMinReady" type="number" min="0" class="mt-1" />
        </label>
        <label>
          <span class="text-xs text-muted-foreground">{{ $t('modelGroup.maxReady') }}</span>
          <Input v-model.number="asMaxReady" type="number" min="1" class="mt-1" />
        </label>
      </div>
      <div class="flex justify-end gap-2">
        <Button variant="outline" size="sm" @click="asOpen = false">{{ $t('common.cancel') }}</Button>
        <Button size="sm" :disabled="asSaving" @click="saveAutoscale">
          <Loader2 v-if="asSaving" class="size-4 animate-spin" />{{ $t('common.save') }}
        </Button>
      </div>
    </div>
  </Dialog>

  <!-- Cross-model fallback -->
  <Dialog v-model:open="fbOpen" :title="$t('modelGroup.fallbackTitle', { group })">
    <div class="space-y-4">
      <p class="text-[11px] text-muted-foreground">{{ $t('modelGroup.fallbackHint') }}</p>
      <p v-if="!otherGroups.length" class="text-sm text-muted-foreground">
        {{ $t('modelGroup.fallbackNoGroups') }}
      </p>
      <div v-else class="flex flex-wrap gap-2">
        <button
          v-for="g in otherGroups"
          :key="g"
          class="flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm transition-colors"
          :class="
            fbSelected.includes(g)
              ? 'border-[var(--chart-3)] bg-[var(--chart-3)]/10 text-[var(--chart-3)]'
              : 'border-input text-muted-foreground hover:text-foreground'
          "
          @click="toggleFb(g)"
        >
          <span v-if="fbSelected.includes(g)" class="tabular text-xs font-semibold">{{ fbSelected.indexOf(g) + 1 }}</span>
          {{ g }}
        </button>
      </div>
      <p v-if="fbSelected.length" class="text-xs text-muted-foreground">
        {{ $t('modelGroup.fallbackOrder') }}: <span class="font-mono text-foreground">{{ group }} → {{ fbSelected.join(' → ') }}</span>
      </p>
      <div class="flex justify-end gap-2">
        <Button variant="outline" size="sm" @click="fbOpen = false">{{ $t('common.cancel') }}</Button>
        <Button size="sm" :disabled="fbSaving" @click="saveFallback">
          <Loader2 v-if="fbSaving" class="size-4 animate-spin" />{{ $t('common.save') }}
        </Button>
      </div>
    </div>
  </Dialog>
</template>
