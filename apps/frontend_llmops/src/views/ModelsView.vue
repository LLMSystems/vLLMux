<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Plus, RefreshCw, Search } from '@lucide/vue'
import { api } from '@/lib/api'
import { useModelsStore } from '@/stores/models'
import { useAuth } from '@/composables/useAuth'
import type { GroupLoad } from '@/types/api'
import ModelGroupCard from '@/components/ModelGroupCard.vue'
import ModelDetailDrawer from '@/components/ModelDetailDrawer.vue'
import AddModelDialog from '@/components/AddModelDialog.vue'
import AddInstanceDialog from '@/components/AddInstanceDialog.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import type { ModelKind, ModelView } from '@/types/api'

const { t } = useI18n()
const models = useModelsStore()
const { ensureUnlocked } = useAuth()
const route = useRoute()

// Seed the search from ?q= so drilling in from the topology lands pre-filtered.
const query = ref(typeof route.query.q === 'string' ? route.query.q : '')
const kindFilter = ref<'all' | ModelKind>('all')
const engineFilter = ref<'all' | string>('all')

// Colour per engine — kept in sync with ModelGroupCard's badge palette so the
// filter dot and the card badge read as the same thing.
const ENGINE_COLORS: Record<string, string> = {
  vllm: 'var(--chart-1)',
  sglang: 'var(--chart-2)',
  llamacpp: 'var(--chart-3)',
  trtllm: 'var(--chart-4)',
}
const ENGINE_ORDER = ['vllm', 'sglang', 'llamacpp', 'trtllm']
const engineOf = (m: ModelView) => m.engine ?? 'vllm'
const drawerOpen = ref(false)
const selectedKey = ref<string | null>(null)
const addOpen = ref(false)
const editOpen = ref(false)
const editKey = ref<string | null>(null)
const addInstanceOpen = ref(false)
const addInstanceGroup = ref<string | null>(null)

// Per-group live load (queue depth, asleep counts) — polled independently of the
// model list so the cards show saturation at a glance. Best-effort; empty on error.
const load = ref<Record<string, GroupLoad>>({})
let loadPoll: ReturnType<typeof setInterval> | null = null
async function refreshLoad() {
  try {
    load.value = await api.groupLoad()
  } catch {
    /* router/load not ready yet — leave the last snapshot */
  }
}
onMounted(() => {
  void refreshLoad()
  loadPoll = setInterval(refreshLoad, 5000)
})
onUnmounted(() => {
  if (loadPoll) clearInterval(loadPoll)
})

function onCreated() {
  // Pull fresh model list + config so the new instance (and its GPU/params) show.
  void models.refresh()
  void models.loadConfig()
}

async function openAdd() {
  if (await ensureUnlocked()) addOpen.value = true
}

async function openEdit(key: string) {
  if (!(await ensureUnlocked())) return
  editKey.value = key
  drawerOpen.value = false
  editOpen.value = true
}

async function openAddInstance(group: string) {
  if (!(await ensureUnlocked())) return
  addInstanceGroup.value = group
  addInstanceOpen.value = true
}

// Live instances of the group the add-instance dialog targets.
const addInstanceInstances = computed(() =>
  addInstanceGroup.value
    ? models.models.filter((m) => (m.key.split('::')[0] ?? m.key) === addInstanceGroup.value)
    : [],
)

const filtered = computed(() =>
  models.models.filter((m) => {
    if (kindFilter.value !== 'all' && m.kind !== kindFilter.value) return false
    // Engine filter only bites LLM groups — embedding servers have no engine of
    // their own, so they show under "All" but drop out when an engine is picked.
    if (engineFilter.value !== 'all' && (m.kind !== 'llm' || engineOf(m) !== engineFilter.value))
      return false
    if (query.value && !m.key.toLowerCase().includes(query.value.toLowerCase())) return false
    return true
  }),
)

// Collapse instances into their model groups, then cluster same-engine groups
// together (vLLM, then SGLang, then others) with embedding servers sinking to the
// end — so a mixed fleet doesn't interleave engines across the masonry.
const groups = computed(() => {
  const map = new Map<string, ModelView[]>()
  for (const m of filtered.value) {
    const g = m.key.split('::')[0] ?? m.key
    if (!map.has(g)) map.set(g, [])
    map.get(g)!.push(m)
  }
  const rank = (m?: ModelView) => {
    if (!m) return 99
    if (m.kind !== 'llm') return 90 // embedding / rerank groups last
    const i = ENGINE_ORDER.indexOf(engineOf(m))
    return i < 0 ? 80 : i
  }
  return [...map.entries()]
    .map(([group, instances]) => ({ group, instances }))
    .sort((a, b) => rank(a.instances[0]) - rank(b.instances[0]))
})

// Engines actually present across LLM groups, with a group count each. Drives the
// engine segmented control, which only appears once the fleet is mixed.
const engineOptions = computed(() => {
  const counts = new Map<string, number>()
  const seen = new Set<string>()
  for (const m of models.models) {
    if (m.kind !== 'llm') continue
    const g = m.key.split('::')[0] ?? m.key
    if (seen.has(g)) continue
    seen.add(g)
    const e = engineOf(m)
    counts.set(e, (counts.get(e) ?? 0) + 1)
  }
  const present = [...counts.keys()].sort(
    (a, b) => (ENGINE_ORDER.indexOf(a) + 1 || 99) - (ENGINE_ORDER.indexOf(b) + 1 || 99),
  )
  return present.map((e) => ({
    value: e,
    label: e,
    count: counts.get(e) ?? 0,
    color: ENGINE_COLORS[e] ?? 'var(--chart-1)',
  }))
})
const showEngineFilter = computed(() => engineOptions.value.length > 1)

// Fall back to "All" if the picked engine vanishes (last SGLang group deleted) or
// the fleet stops being mixed — otherwise the grid would silently show nothing.
watch(engineOptions, (opts) => {
  if (engineFilter.value !== 'all' && !opts.some((o) => o.value === engineFilter.value)) {
    engineFilter.value = 'all'
  }
})

function openDetail(key: string) {
  selectedKey.value = key
  drawerOpen.value = true
}

const kinds = computed<{ value: 'all' | ModelKind; label: string }[]>(() => [
  { value: 'all', label: t('models.all') },
  { value: 'llm', label: t('models.llm') },
  { value: 'embedding', label: t('models.embedding') },
])
</script>

<template>
  <div class="space-y-6 p-6">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-3">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input v-model="query" :placeholder="$t('models.searchPlaceholder')" class="w-56 pl-8 lg:w-72" />
      </div>
      <div class="inline-flex rounded-lg border border-border/60 bg-muted/40 p-0.5">
        <button
          v-for="k in kinds"
          :key="k.value"
          class="rounded-md px-3 py-1 text-sm font-medium transition-colors"
          :class="
            kindFilter === k.value
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          "
          @click="kindFilter = k.value"
        >
          {{ k.label }}
        </button>
      </div>
      <!-- Engine filter — only shown on a mixed fleet (more than one engine). -->
      <div
        v-if="showEngineFilter"
        class="inline-flex rounded-lg border border-border/60 bg-muted/40 p-0.5"
      >
        <button
          class="rounded-md px-3 py-1 text-sm font-medium transition-colors"
          :class="
            engineFilter === 'all'
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          "
          @click="engineFilter = 'all'"
        >
          {{ $t('models.all') }}
        </button>
        <button
          v-for="e in engineOptions"
          :key="e.value"
          class="flex items-center gap-1.5 rounded-md px-3 py-1 text-sm font-medium transition-colors"
          :class="
            engineFilter === e.value
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground'
          "
          @click="engineFilter = e.value"
        >
          <span class="size-1.5 rounded-full" :style="{ backgroundColor: e.color }" />
          <span class="font-mono">{{ e.label }}</span>
          <span class="tabular text-xs text-muted-foreground">{{ e.count }}</span>
        </button>
      </div>
      <div class="ml-auto flex items-center gap-3 text-sm text-muted-foreground">
        <div class="hidden items-center gap-1.5 tabular text-xs sm:flex">
          <span class="rounded-md bg-muted px-2 py-0.5 text-muted-foreground">
            {{ $t('models.groups', { n: groups.length }) }}
          </span>
          <span
            class="rounded-md border border-status-ready/30 bg-status-ready/12 px-2 py-0.5 text-status-ready"
          >
            {{ $t('models.readyStat', { n: models.counts.ready }) }}
          </span>
          <span
            v-if="models.counts.failed"
            class="rounded-md border border-status-failed/30 bg-status-failed/12 px-2 py-0.5 text-status-failed"
          >
            {{ $t('models.failedStat', { n: models.counts.failed }) }}
          </span>
        </div>
        <Button variant="outline" size="sm" @click="models.refresh()">
          <RefreshCw class="size-3.5" />{{ $t('common.refresh') }}
        </Button>
        <Button size="sm" @click="openAdd"><Plus class="size-4" />{{ $t('models.addModel') }}</Button>
      </div>
    </div>

    <!-- Grouped masonry: CSS columns pack variable-height cards with no row gaps. -->
    <div
      v-if="groups.length"
      class="gap-4 [column-fill:_balance] md:columns-2 2xl:columns-3 [&>*]:mb-4 [&>*]:break-inside-avoid"
    >
      <ModelGroupCard
        v-for="g in groups"
        :key="g.group"
        :group="g.group"
        :instances="g.instances"
        :load="load[g.group]"
        @open="openDetail"
        @add-instance="openAddInstance"
      />
    </div>
    <div
      v-else
      class="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/70 py-20 text-center"
    >
      <p class="text-sm font-medium">{{ $t('models.noMatch') }}</p>
      <p class="mt-1 text-sm text-muted-foreground">
        {{ models.total ? $t('models.clearFilter') : $t('models.noConfig') }}
      </p>
    </div>

    <ModelDetailDrawer
      v-model:open="drawerOpen"
      :model-key="selectedKey"
      @deleted="onCreated"
      @edit="openEdit"
    />
    <AddModelDialog v-model:open="addOpen" @created="onCreated" />
    <AddModelDialog v-model:open="editOpen" mode="edit" :edit-key="editKey" @updated="onCreated" />
    <AddInstanceDialog
      v-if="addInstanceGroup"
      v-model:open="addInstanceOpen"
      :group="addInstanceGroup"
      :instances="addInstanceInstances"
      @created="onCreated"
    />
  </div>
</template>
