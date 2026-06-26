import { onUnmounted, ref, watch, type Ref } from 'vue'

/**
 * Animate a number toward its target whenever the source changes (and once on
 * mount, from 0). Gives KPI tiles a "live" roll instead of hard value jumps.
 *
 * Honors `prefers-reduced-motion`: reduced-motion users get the value set
 * instantly with no animation. The returned ref carries intermediate floats —
 * round/format at the call site.
 */
export function useCountUp(source: () => number, duration = 700): Ref<number> {
  const reduce =
    typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
  const current = ref(reduce ? source() : 0)
  let raf: number | null = null

  function animateTo(to: number) {
    if (raf) cancelAnimationFrame(raf)
    const from = current.value
    const delta = to - from
    if (reduce || delta === 0 || Number.isNaN(to)) {
      current.value = Number.isNaN(to) ? 0 : to
      return
    }
    const start = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - t, 3) // easeOutCubic
      current.value = from + delta * eased
      if (t < 1) {
        raf = requestAnimationFrame(tick)
      } else {
        current.value = to
        raf = null
      }
    }
    raf = requestAnimationFrame(tick)
  }

  watch(source, animateTo, { immediate: true })
  onUnmounted(() => raf && cancelAnimationFrame(raf))
  return current
}
