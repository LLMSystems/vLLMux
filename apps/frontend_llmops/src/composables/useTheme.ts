import { computed } from 'vue'
import { useColorMode, usePreferredDark } from '@vueuse/core'

/**
 * App-wide light/dark theme, persisted to localStorage and synced to the
 * `.dark` class on <html> (which our CSS variables key off of).
 *
 * `mode` is the user's choice ('light' | 'dark' | 'auto'); `isDark` is the
 * resolved state actually applied — derived reactively from `mode` + system
 * preference so toggling stays in sync (don't read the DOM class here, it's
 * not a reactive source and the computed would cache stale values).
 */
const mode = useColorMode({
  storageKey: 'llmops-theme',
  emitAuto: true,
  modes: { light: 'light', dark: 'dark' },
})
const preferredDark = usePreferredDark()

export function useTheme() {
  const isDark = computed(
    () => mode.value === 'dark' || (mode.value === 'auto' && preferredDark.value),
  )

  function toggle() {
    mode.value = isDark.value ? 'light' : 'dark'
  }

  return { mode, isDark, toggle }
}
