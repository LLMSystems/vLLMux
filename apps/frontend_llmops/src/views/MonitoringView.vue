<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Activity, Cpu, ExternalLink, Gauge, Layers, LayoutDashboard, Server, TrendingUp } from '@lucide/vue'
import { useTheme } from '@/composables/useTheme'
import { useModelsStore } from '@/stores/models'

// Grafana is served same-origin under /grafana (nginx reverse proxy), so these
// dashboards embed in an iframe with no CORS/X-Frame issues. UIDs/slugs come
// from the provisioned dashboards (deploy/grafana/dashboards).
const BASE = '/grafana/d'

type DashboardId = 'overview' | 'autoscaling' | 'capacity' | 'perf' | 'query' | 'gpu' | 'host'

const ranges = [
  { label: '15m', from: 'now-15m' },
  { label: '1h', from: 'now-1h' },
  { label: '6h', from: 'now-6h' },
  { label: '24h', from: 'now-24h' },
] as const

const { t } = useI18n()
const dashboards = computed(
  () =>
    [
      { id: 'overview', label: t('monitoring.overview'), icon: LayoutDashboard, path: `${BASE}/vllm-overview/vllm-overview` },
      { id: 'autoscaling', label: t('monitoring.autoscaling'), icon: Layers, path: `${BASE}/llmops-autoscaling/autoscaling` },
      { id: 'capacity', label: t('monitoring.vllmCapacity'), icon: Gauge, path: `${BASE}/vllm-scheduling-capacity/vllm-scheduling-and-capacity` },
      { id: 'perf', label: t('monitoring.vllmPerf'), icon: TrendingUp, path: `${BASE}/performance-statistics/performance-statistics` },
      { id: 'query', label: t('monitoring.vllmQuery'), icon: Activity, path: `${BASE}/query-statistics4/query-statistics-new4` },
      { id: 'gpu', label: t('monitoring.gpu'), icon: Server, path: `${BASE}/Oxed_c6Wz/nvidia-dcgm-exporter-dashboard` },
      { id: 'host', label: t('monitoring.host'), icon: Cpu, path: `${BASE}/rYdddlPWk/node-exporter-full` },
    ] as const,
)
const active = ref<DashboardId>('overview')
const range = ref<(typeof ranges)[number]['from']>('now-1h')
const { isDark } = useTheme()

// kiosk hides Grafana's own variable dropdowns, so for the autoscaling dashboard
// (whose whole point is per-group) we drive its `group` template variable from an
// app-side selector via the URL (?var-group=…). Groups come from the live config.
const models = useModelsStore()
onMounted(() => void models.loadConfig())
const groups = computed(() => [
  ...new Set(Object.keys(models.config?.LLM_engines ?? {}).map((k) => k.split('::')[0])),
])
const selectedGroup = ref('')
watch(
  groups,
  (g) => {
    if (g.length && !g.includes(selectedGroup.value)) selectedGroup.value = g[0] ?? ''
  },
  { immediate: true },
)

const current = computed(() => dashboards.value.find((d) => d.id === active.value)!)

// kiosk hides Grafana's own chrome (nav/time-picker) for a clean embed; theme
// follows the app's light/dark so the panels don't clash with the surrounding
// UI. The src is fully reactive — switching tab/range/theme/group reloads the iframe.
const params = computed(() => {
  const groupVar =
    active.value === 'autoscaling' && selectedGroup.value
      ? `&var-group=${encodeURIComponent(selectedGroup.value)}`
      : ''
  return `?kiosk&theme=${isDark.value ? 'dark' : 'light'}&from=${range.value}&to=now&refresh=30s${groupVar}`
})
const src = computed(() => current.value.path + params.value)
const openUrl = computed(
  () =>
    current.value.path +
    `?from=${range.value}&to=now` +
    (active.value === 'autoscaling' && selectedGroup.value
      ? `&var-group=${encodeURIComponent(selectedGroup.value)}`
      : ''),
)
</script>

<template>
  <div class="flex h-full flex-col p-6">
    <!-- Toolbar -->
    <div class="mb-4 flex flex-wrap items-center gap-3">
      <div class="flex items-center gap-1 rounded-lg bg-muted/60 p-1">
        <button
          v-for="d in dashboards"
          :key="d.id"
          class="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors"
          :class="
            active === d.id
              ? 'bg-background text-foreground shadow-sm ring-1 ring-border/60'
              : 'text-muted-foreground hover:text-foreground'
          "
          @click="active = d.id"
        >
          <component :is="d.icon" class="size-4" />
          {{ d.label }}
        </button>
      </div>

      <!-- Group selector drives the autoscaling dashboard's `group` variable (kiosk
           hides Grafana's own dropdown). -->
      <label
        v-if="active === 'autoscaling' && groups.length"
        class="flex items-center gap-1.5 rounded-lg bg-muted/60 px-2 py-1 text-sm"
      >
        <Layers class="size-4 text-muted-foreground" />
        <select
          v-model="selectedGroup"
          class="bg-transparent text-sm font-medium text-foreground focus:outline-none"
        >
          <option
            v-for="g in groups"
            :key="g"
            :value="g"
            :style="{ backgroundColor: 'var(--popover)', color: 'var(--popover-foreground)' }"
          >
            {{ g }}
          </option>
        </select>
      </label>

      <div class="flex items-center gap-1 rounded-lg bg-muted/60 p-1">
        <button
          v-for="r in ranges"
          :key="r.from"
          class="rounded-md px-2.5 py-1.5 text-xs font-medium tabular transition-colors"
          :class="
            range === r.from
              ? 'bg-background text-foreground shadow-sm ring-1 ring-border/60'
              : 'text-muted-foreground hover:text-foreground'
          "
          @click="range = r.from"
        >
          {{ r.label }}
        </button>
      </div>

      <a
        :href="openUrl"
        target="_blank"
        rel="noopener"
        class="ml-auto flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      >
        <ExternalLink class="size-4" />
        {{ $t('monitoring.openGrafana') }}
      </a>
    </div>

    <!-- Embedded dashboard -->
    <iframe
      :key="active"
      :src="src"
      class="min-h-0 w-full flex-1 rounded-xl border border-border/70 bg-card"
      :title="current.label"
    />
  </div>
</template>
