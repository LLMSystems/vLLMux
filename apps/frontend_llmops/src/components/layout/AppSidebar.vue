<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  Activity,
  ArrowLeftRight,
  BookOpen,
  ClipboardCheck,
  Cpu,
  Database,
  Gauge,
  KeyRound,
  Layers,
  LayoutDashboard,
  LineChart,
  Package,
  Receipt,
  ScrollText,
  Server,
  TerminalSquare,
  Users,
} from '@lucide/vue'
import { useModelsStore } from '@/stores/models'
import { useAuth } from '@/composables/useAuth'
import StatusDot from '@/components/StatusDot.vue'
import type { ModelState } from '@/types/api'

const route = useRoute()
const models = useModelsStore()
const { isAdmin } = useAuth()
const { t } = useI18n()

// Roster grouped by model group (dedupes multi-instance groups), with a
// ready/total count and a worst-wins status dot.
const roster = computed(() => {
  const map = new Map<string, { ready: number; total: number; states: ModelState[] }>()
  for (const m of models.models) {
    const g = m.key.split('::')[0] ?? m.key
    const e = map.get(g) ?? { ready: 0, total: 0, states: [] }
    e.total++
    if (m.state === 'ready') e.ready++
    e.states.push(m.state)
    map.set(g, e)
  }
  return [...map.entries()].map(([group, e]) => {
    const worst: ModelState = e.states.includes('failed')
      ? 'failed'
      : e.states.some((s) => s === 'starting' || s === 'stopping')
        ? 'starting'
        : e.states.every((s) => s === 'ready')
          ? 'ready'
          : 'stopped'
    return { group, ready: e.ready, total: e.total, worst }
  })
})

const nav = computed(() => {
  const items = [
    { to: '/', label: t('sidebar.overview'), icon: LayoutDashboard },
    { to: '/models', label: t('sidebar.models'), icon: Server },
    { to: '/traffic', label: t('sidebar.traffic'), icon: ArrowLeftRight },
    { to: '/requests', label: t('sidebar.requests'), icon: Receipt },
    { to: '/monitoring', label: t('sidebar.monitoring'), icon: LineChart },
    { to: '/playground', label: t('sidebar.playground'), icon: TerminalSquare },
    { to: '/benchmark', label: t('sidebar.benchmark'), icon: Gauge },
    { to: '/eval', label: t('sidebar.eval'), icon: ClipboardCheck },
    { to: '/library', label: t('sidebar.library'), icon: Package },
    { to: '/lora-library', label: t('sidebar.loraLibrary'), icon: Layers },
    { to: '/datasets', label: t('sidebar.datasets'), icon: Database },
    { to: '/keys', label: t('sidebar.keys'), icon: KeyRound },
    // Admin-only control-plane user management + audit trail.
    ...(isAdmin.value
      ? [
          { to: '/operators', label: t('sidebar.operators'), icon: Users },
          { to: '/audit', label: t('sidebar.audit'), icon: ScrollText },
        ]
      : []),
    { to: '/usage', label: t('sidebar.usage'), icon: BookOpen },
    { to: '/resources', label: t('sidebar.resources'), icon: Cpu },
    { to: '/activity', label: t('sidebar.activity'), icon: Activity },
  ]
  return items
})
</script>

<template>
  <aside
    class="flex h-screen w-60 shrink-0 flex-col border-r border-border/70 bg-sidebar text-sidebar-foreground"
  >
    <!-- Brand -->
    <div class="flex items-center gap-2.5 px-5 py-5">
      <img src="/icon2_rb.png" alt="vLLMux" class="size-11 object-contain" />
      <div class="leading-tight">
        <p class="text-sm font-semibold">vLLMux</p>
        <p class="text-[10px] uppercase tracking-widest text-muted-foreground">{{ $t('sidebar.console') }}</p>
      </div>
    </div>

    <!-- Nav -->
    <nav class="flex-1 space-y-1 px-3 py-2">
      <RouterLink
        v-for="item in nav"
        :key="item.to"
        :to="item.to"
        class="group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        :class="
          route.path === item.to &&
          'bg-sidebar-accent text-sidebar-accent-foreground shadow-sm ring-1 ring-border/60'
        "
      >
        <component
          :is="item.icon"
          class="size-4.5 transition-colors"
          :class="route.path === item.to ? 'text-[var(--chart-1)]' : ''"
        />
        {{ item.label }}
        <span
          v-if="item.to === '/models' && models.hasFailures"
          class="ml-auto flex size-1.5 rounded-full bg-status-failed"
        />
      </RouterLink>
    </nav>

    <!-- Footer: live model roster -->
    <div class="border-t border-border/70 px-4 py-3">
      <p class="mb-2 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
        {{ $t('sidebar.modelCount', { ready: models.readyCount, total: models.total }) }}
      </p>
      <ul class="max-h-40 space-y-1 overflow-y-auto pr-1">
        <li
          v-for="g in roster"
          :key="g.group"
          class="flex items-center gap-2 text-xs text-muted-foreground"
        >
          <StatusDot :state="g.worst" size="sm" />
          <span class="truncate" :title="g.group">{{ g.group }}</span>
          <span class="ml-auto shrink-0 tabular text-[10px] text-muted-foreground/70">
            {{ g.ready }}/{{ g.total }}
          </span>
        </li>
        <li v-if="!models.total" class="text-xs text-muted-foreground/60">{{ $t('sidebar.noModels') }}</li>
      </ul>
    </div>
  </aside>
</template>
