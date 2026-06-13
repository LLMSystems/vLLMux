<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Plus, RefreshCw, Search } from '@lucide/vue'
import { useModelsStore } from '@/stores/models'
import ModelGroupCard from '@/components/ModelGroupCard.vue'
import ModelDetailDrawer from '@/components/ModelDetailDrawer.vue'
import AddModelDialog from '@/components/AddModelDialog.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import type { ModelKind, ModelView } from '@/types/api'

const models = useModelsStore()
const route = useRoute()

// Seed the search from ?q= so drilling in from the topology lands pre-filtered.
const query = ref(typeof route.query.q === 'string' ? route.query.q : '')
const kindFilter = ref<'all' | ModelKind>('all')
const drawerOpen = ref(false)
const selectedKey = ref<string | null>(null)
const addOpen = ref(false)

function onCreated() {
  // Pull fresh model list + config so the new instance (and its GPU/params) show.
  void models.refresh()
  void models.loadConfig()
}

const filtered = computed(() =>
  models.models.filter((m) => {
    if (kindFilter.value !== 'all' && m.kind !== kindFilter.value) return false
    if (query.value && !m.key.toLowerCase().includes(query.value.toLowerCase())) return false
    return true
  }),
)

// Collapse instances into their model groups (preserving first-seen order).
const groups = computed(() => {
  const map = new Map<string, ModelView[]>()
  for (const m of filtered.value) {
    const g = m.key.split('::')[0] ?? m.key
    if (!map.has(g)) map.set(g, [])
    map.get(g)!.push(m)
  }
  return [...map.entries()].map(([group, instances]) => ({ group, instances }))
})

function openDetail(key: string) {
  selectedKey.value = key
  drawerOpen.value = true
}

const kinds: { value: 'all' | ModelKind; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'llm', label: 'LLM' },
  { value: 'embedding', label: 'Embedding' },
]
</script>

<template>
  <div class="space-y-6 p-6">
    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-3">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input v-model="query" placeholder="Search models…" class="w-56 pl-8" />
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
      <div class="ml-auto flex items-center gap-3 text-sm text-muted-foreground">
        <span class="tabular"
          >{{ groups.length }} groups · {{ models.counts.ready }} ready · {{ models.counts.failed }} failed</span
        >
        <Button variant="outline" size="sm" @click="models.refresh()">
          <RefreshCw class="size-3.5" />Refresh
        </Button>
        <Button size="sm" @click="addOpen = true"><Plus class="size-4" />Add model</Button>
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
        @open="openDetail"
      />
    </div>
    <div
      v-else
      class="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/70 py-20 text-center"
    >
      <p class="text-sm font-medium">No models match.</p>
      <p class="mt-1 text-sm text-muted-foreground">
        {{ models.total ? 'Try clearing the filter.' : 'No models configured in config.yaml.' }}
      </p>
    </div>

    <ModelDetailDrawer v-model:open="drawerOpen" :model-key="selectedKey" @deleted="onCreated" />
    <AddModelDialog v-model:open="addOpen" @created="onCreated" />
  </div>
</template>
