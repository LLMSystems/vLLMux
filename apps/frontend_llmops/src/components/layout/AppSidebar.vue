<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
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
  Package,
  Receipt,
  Server,
  TerminalSquare,
  TrendingUp,
} from '@lucide/vue'
import { useModelsStore } from '@/stores/models'
import StatusDot from '@/components/StatusDot.vue'
import type { ModelState } from '@/types/api'

const route = useRoute()
const models = useModelsStore()

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

const nav = [
  { to: '/', label: '總覽', icon: LayoutDashboard },
  { to: '/models', label: '模型', icon: Server },
  { to: '/traffic', label: '流量', icon: ArrowLeftRight },
  { to: '/trends', label: '趨勢', icon: TrendingUp },
  { to: '/requests', label: '請求', icon: Receipt },
  { to: '/playground', label: '測試台', icon: TerminalSquare },
  { to: '/benchmark', label: '壓測', icon: Gauge },
  { to: '/eval', label: '評測', icon: ClipboardCheck },
  { to: '/library', label: '模型庫', icon: Package },
  { to: '/lora-library', label: 'LoRA 庫', icon: Layers },
  { to: '/datasets', label: '資料集庫', icon: Database },
  { to: '/keys', label: 'API 金鑰', icon: KeyRound },
  { to: '/usage', label: '使用指南', icon: BookOpen },
  { to: '/resources', label: '資源', icon: Cpu },
  { to: '/activity', label: '活動', icon: Activity },
]
</script>

<template>
  <aside
    class="flex h-screen w-60 shrink-0 flex-col border-r border-border/70 bg-sidebar text-sidebar-foreground"
  >
    <!-- Brand -->
    <div class="flex items-center gap-2.5 px-5 py-5">
      <div
        class="flex size-8 items-center justify-center rounded-lg bg-gradient-to-br from-[var(--chart-1)] to-[var(--chart-4)] text-white shadow-sm"
      >
        <Server class="size-4.5" />
      </div>
      <div class="leading-tight">
        <p class="text-sm font-semibold">LLMOps</p>
        <p class="text-[10px] uppercase tracking-widest text-muted-foreground">控制台</p>
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
        模型 · {{ models.readyCount }}/{{ models.total }} 就緒
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
        <li v-if="!models.total" class="text-xs text-muted-foreground/60">尚未設定模型</li>
      </ul>
    </div>
  </aside>
</template>
