<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  Activity,
  ArrowLeftRight,
  Bell,
  BookOpen,
  ClipboardCheck,
  Coins,
  Cpu,
  Database,
  Eye,
  Gauge,
  History,
  KeyRound,
  Layers,
  LayoutDashboard,
  LineChart,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
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
const { isAdmin, canOperate } = useAuth()
const { t } = useI18n()

// Collapse to an icon-only rail; persisted so it survives reloads.
const STORAGE_KEY = 'sidebar-collapsed'
const collapsed = ref(localStorage.getItem(STORAGE_KEY) === '1')
function toggleCollapsed() {
  collapsed.value = !collapsed.value
  localStorage.setItem(STORAGE_KEY, collapsed.value ? '1' : '0')
}

type NavItem = {
  to: string
  label: string
  icon: unknown
  badge?: boolean // a small red dot (something needs attention)
  hint?: boolean // a read-only marker (visible but not actionable for this role)
}

// Grouped so the (long) list reads as a map, not a wall. A section with no
// visible items (role-gated) renders nothing — no mid-list layout jumps.
const sections = computed<{ key: string; items: NavItem[] }[]>(() => {
  const raw: { key: string; items: (NavItem | false)[] }[] = [
    {
      key: 'operate',
      items: [
        { to: '/', label: t('sidebar.overview'), icon: LayoutDashboard },
        { to: '/models', label: t('sidebar.models'), icon: Server, badge: models.hasFailures },
        { to: '/traffic', label: t('sidebar.traffic'), icon: ArrowLeftRight },
        { to: '/requests', label: t('sidebar.requests'), icon: Receipt },
        { to: '/monitoring', label: t('sidebar.monitoring'), icon: LineChart },
      ],
    },
    {
      key: 'lab',
      items: [
        { to: '/playground', label: t('sidebar.playground'), icon: TerminalSquare },
        { to: '/benchmark', label: t('sidebar.benchmark'), icon: Gauge },
        { to: '/eval', label: t('sidebar.eval'), icon: ClipboardCheck },
      ],
    },
    {
      key: 'library',
      items: [
        { to: '/library', label: t('sidebar.library'), icon: Package },
        { to: '/lora-library', label: t('sidebar.loraLibrary'), icon: Layers },
        { to: '/datasets', label: t('sidebar.datasets'), icon: Database },
      ],
    },
    {
      key: 'admin',
      items: [
        { to: '/keys', label: t('sidebar.keys'), icon: KeyRound },
        // Cost is operator-readable; admin-only to set prices.
        canOperate.value && {
          to: '/cost',
          label: t('sidebar.cost'),
          icon: Coins,
          hint: !isAdmin.value,
        },
        isAdmin.value && { to: '/operators', label: t('sidebar.operators'), icon: Users },
        isAdmin.value && { to: '/audit', label: t('sidebar.audit'), icon: ScrollText },
        isAdmin.value && { to: '/notifications', label: t('sidebar.notifications'), icon: Bell },
        // Config Versions is operator-readable; admin-only to import / roll back.
        canOperate.value && {
          to: '/config',
          label: t('sidebar.config'),
          icon: History,
          hint: !isAdmin.value,
        },
      ],
    },
    {
      key: 'system',
      items: [
        { to: '/usage', label: t('sidebar.usage'), icon: BookOpen },
        { to: '/resources', label: t('sidebar.resources'), icon: Cpu },
        { to: '/activity', label: t('sidebar.activity'), icon: Activity },
      ],
    },
  ]
  return raw
    .map((s) => ({ key: s.key, items: s.items.filter(Boolean) as NavItem[] }))
    .filter((s) => s.items.length > 0)
})

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
</script>

<template>
  <aside
    class="flex h-screen shrink-0 flex-col border-r border-border/70 bg-sidebar text-sidebar-foreground transition-[width] duration-200"
    :class="collapsed ? 'w-16' : 'w-60'"
  >
    <!-- Brand + collapse toggle -->
    <div
      class="flex items-center px-3 py-5"
      :class="collapsed ? 'justify-center' : 'gap-2.5 px-5'"
    >
      <img src="/icon2_rb.png" alt="vLLMux" class="size-11 shrink-0 object-contain" />
      <div v-if="!collapsed" class="leading-tight">
        <p class="text-sm font-semibold">vLLMux</p>
        <p class="text-[10px] uppercase tracking-widest text-muted-foreground">{{ $t('sidebar.console') }}</p>
      </div>
      <button
        v-if="!collapsed"
        type="button"
        class="ml-auto rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        :title="$t('sidebar.collapse')"
        @click="toggleCollapsed"
      >
        <PanelLeftClose class="size-4" />
      </button>
    </div>

    <button
      v-if="collapsed"
      type="button"
      class="mx-auto mb-1 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
      :title="$t('sidebar.expand')"
      @click="toggleCollapsed"
    >
      <PanelLeftOpen class="size-4" />
    </button>

    <!-- Nav (sectioned) -->
    <nav class="flex-1 space-y-0.5 overflow-y-auto px-3 py-2">
      <template v-for="(section, si) in sections" :key="section.key">
        <p
          v-if="!collapsed"
          class="px-3 pb-1 pt-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground/80"
        >
          {{ $t(`sidebar.section.${section.key}`) }}
        </p>
        <div v-else-if="si > 0" class="mx-2 my-2 border-t border-border/50" />

        <RouterLink
          v-for="item in section.items"
          :key="item.to"
          :to="item.to"
          :title="collapsed ? item.label : undefined"
          class="group relative flex items-center rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          :class="[
            collapsed ? 'justify-center' : 'gap-3',
            route.path === item.to && 'bg-sidebar-accent text-sidebar-accent-foreground shadow-sm ring-1 ring-border/60',
          ]"
        >
          <component
            :is="item.icon"
            class="size-4.5 shrink-0 transition-colors"
            :class="route.path === item.to ? 'text-[var(--chart-1)]' : ''"
          />
          <template v-if="!collapsed">
            <span class="truncate">{{ item.label }}</span>
            <Eye v-if="item.hint" class="ml-auto size-3.5 text-muted-foreground/50" :title="$t('sidebar.readonly')" />
            <span
              v-if="item.badge"
              class="flex size-1.5 rounded-full bg-status-failed"
              :class="item.hint ? 'ml-1.5' : 'ml-auto'"
            />
          </template>
          <!-- Collapsed: a corner dot still flags attention. -->
          <span
            v-else-if="item.badge"
            class="absolute right-1.5 top-1.5 size-1.5 rounded-full bg-status-failed"
          />
        </RouterLink>
      </template>
    </nav>

    <!-- Footer: live model roster (hidden when collapsed) -->
    <div v-if="!collapsed" class="shrink-0 border-t border-border/70 px-4 py-3">
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
