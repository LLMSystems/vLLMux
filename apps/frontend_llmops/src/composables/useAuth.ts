import { computed, ref } from 'vue'
import { api, clearAdminToken, getAdminToken, setAdminToken } from '@/lib/api'
import type { Me, Role } from '@/types/api'

// Module-level so the session is shared app-wide: the token is entered once,
// resolved to an identity+role by the backend, then attached to every write by
// api.ts. Any role's token works (viewer / operator / admin) — the backend
// enforces what each may do; the role here only tailors the UI chrome.
const token = ref(getAdminToken())
const authEnabled = ref<boolean | null>(null) // null = not yet queried
const me = ref<Me | null>(null)
const dialogOpen = ref(false)
let resolver: ((ok: boolean) => void) | null = null

// Monotonic: admin ⊃ operator ⊃ viewer.
const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 }

/**
 * Access gate. The backend decides whether auth is required
 * (`/api/auth/status`) and who the caller is (`/api/me`). When auth is disabled
 * the gate is transparent and the caller resolves as local-dev/admin.
 */
export function useAuth() {
  async function refreshStatus() {
    try {
      authEnabled.value = (await api.authStatus()).auth_enabled
    } catch {
      authEnabled.value = false // backend unreachable — don't block the UI
    }
    await refreshMe()
  }

  /** Resolve the current token's identity (or clear it if invalid/absent). */
  async function refreshMe() {
    if (authEnabled.value && !token.value) {
      me.value = null
      return
    }
    try {
      me.value = await api.whoami()
    } catch {
      me.value = null
    }
  }

  const needsUnlock = computed(() => authEnabled.value === true && !token.value)

  /** Resolve true once the operator is unlocked, opening the dialog if needed. */
  async function ensureUnlocked(): Promise<boolean> {
    if (authEnabled.value === null) await refreshStatus()
    if (!authEnabled.value || token.value) return true
    dialogOpen.value = true
    return new Promise<boolean>((res) => {
      resolver = res
    })
  }

  /** Sign in with a candidate token of any role; on success store it. */
  async function submitToken(input: string): Promise<boolean> {
    const identity = await api.whoamiWith(input)
    if (!identity || !identity.role) return false
    token.value = input
    setAdminToken(input)
    me.value = identity
    dialogOpen.value = false
    resolver?.(true)
    resolver = null
    return true
  }

  function cancel() {
    dialogOpen.value = false
    resolver?.(false)
    resolver = null
  }

  function logout() {
    token.value = ''
    me.value = null
    clearAdminToken()
  }

  const role = computed<Role | null>(() => me.value?.role ?? null)
  /** True when the current identity meets at least `min` (admin in open dev). */
  function hasRole(min: Role): boolean {
    const r = role.value
    return r != null && RANK[r] >= RANK[min]
  }

  return {
    authEnabled,
    dialogOpen,
    needsUnlock,
    me,
    role,
    hasToken: computed(() => !!token.value),
    isAdmin: computed(() => hasRole('admin')),
    canOperate: computed(() => hasRole('operator')),
    hasRole,
    refreshStatus,
    refreshMe,
    ensureUnlocked,
    submitToken,
    cancel,
    logout,
  }
}
