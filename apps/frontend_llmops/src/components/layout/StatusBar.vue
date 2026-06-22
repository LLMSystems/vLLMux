<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Languages, Moon, Sun, Wifi, WifiOff } from '@lucide/vue'
import { setLocale, currentLocale } from '@/i18n'
import { useModelsStore } from '@/stores/models'
import { useResourcesStore } from '@/stores/resources'
import { useTheme } from '@/composables/useTheme'
import { formatPercent, timeAgo } from '@/lib/utils'
import Button from '@/components/ui/Button.vue'

const { t } = useI18n()
const route = useRoute()
const models = useModelsStore()
const resources = useResourcesStore()
const { isDark, toggle } = useTheme()

const title = computed(() => t('sidebar.' + ((route.meta.title as string) ?? 'overview')))
const languageTitle = computed(() =>
  currentLocale() === 'en' ? t('statusBar.switchToChinese') : t('statusBar.switchToEnglish'),
)
const updatedTitle = computed(() =>
  models.lastUpdated ? t('statusBar.updatedAgo', { ago: timeAgo(models.lastUpdated / 1000) }) : '',
)

const connMeta = computed(() => {
  switch (models.conn) {
    case 'live':
      return { label: t('statusBar.live'), cls: 'text-status-ready', icon: Wifi }
    case 'polling':
      return { label: t('statusBar.polling'), cls: 'text-status-starting', icon: Wifi }
    case 'connecting':
      return { label: t('statusBar.connecting'), cls: 'text-muted-foreground', icon: Wifi }
    default:
      return { label: t('statusBar.offline'), cls: 'text-status-failed', icon: WifiOff }
  }
})

function toggleLocale() {
  setLocale(currentLocale() === 'en' ? 'zh-TW' : 'en')
}
</script>

<template>
  <header
    class="sticky top-0 z-30 flex h-14 items-center gap-4 border-b border-border/70 glass px-6"
  >
    <h1 class="text-base font-semibold tracking-tight">{{ title }}</h1>

    <div class="ml-auto flex items-center gap-3 text-sm">
      <!-- GPU util pill -->
      <div
        v-if="resources.resources?.gpus.length"
        class="hidden items-center gap-2 rounded-md border border-border/60 bg-background/40 px-2.5 py-1 sm:flex"
        :title="$t('statusBar.gpuUtilTitle')"
      >
        <span class="text-xs text-muted-foreground">{{ $t('statusBar.gpu') }}</span>
        <div class="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
          <div
            class="h-full rounded-full bg-[var(--chart-1)] transition-all"
            :style="{ width: `${Math.min(100, resources.avgGpuUtil)}%` }"
          />
        </div>
        <span class="tabular text-xs font-medium">{{ formatPercent(resources.avgGpuUtil) }}</span>
      </div>

      <!-- Ready count -->
      <div
        class="hidden items-center gap-1.5 rounded-md border border-border/60 bg-background/40 px-2.5 py-1 text-xs md:flex"
      >
        <span class="size-1.5 rounded-full bg-status-ready" />
        <span class="tabular font-medium">{{ models.readyCount }}/{{ models.total }}</span>
        <span class="text-muted-foreground">{{ $t('statusBar.ready') }}</span>
      </div>

      <!-- Connection state -->
      <div
        class="flex items-center gap-1.5 rounded-md border border-border/60 bg-background/40 px-2.5 py-1 text-xs"
        :title="updatedTitle"
      >
        <component :is="connMeta.icon" class="size-3.5" :class="connMeta.cls" />
        <span :class="connMeta.cls">{{ connMeta.label }}</span>
      </div>

      <!-- Theme toggle -->
      <Button variant="ghost" size="icon-sm" :title="isDark ? $t('statusBar.switchLight') : $t('statusBar.switchDark')" @click="toggle">
        <Moon v-if="!isDark" class="size-4" />
        <Sun v-else class="size-4" />
      </Button>

      <!-- Language toggle -->
      <Button variant="ghost" size="icon-sm" :title="languageTitle" @click="toggleLocale">
        <Languages class="size-4" />
      </Button>
    </div>
  </header>
</template>
