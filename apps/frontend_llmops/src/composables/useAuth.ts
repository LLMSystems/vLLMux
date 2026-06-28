import { computed, ref } from 'vue'
import { api, clearAdminToken, getAdminToken, setAdminToken } from '@/lib/api'
import type { Me, Role } from '@/types/api'

// Module-level so the session is shared app-wide: the token is entered once,
// resolved to an identity+role by the backend, then attached to every write by
// api.ts. Any role's token works (viewer / operator / admin) — the backend
// enforces what each may do; the role here only tailors the UI chrome.
const token = ref(getAdminToken())
const authEnabled = ref<boolean | null>(null) // null = not yet queried
const ssoEnabled = ref(false)
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
      const s = await api.authStatus()
      authEnabled.value = s.auth_enabled
      ssoEnabled.value = !!s.sso_enabled
    } catch {
      authEnabled.value = false // backend unreachable — don't block the UI
      ssoEnabled.value = false
    }
    await refreshMe()
  }

  // Auth is required when a token is configured OR SSO is on. SSO sessions live
  // in an HttpOnly cookie (JS can't read it), so "logged in" is driven by whether
  // /api/me resolves an identity, not by a local token.
  const authRequired = computed(() => authEnabled.value === true || ssoEnabled.value)
  const signedIn = computed(() => !!me.value?.actor)

  /** Resolve the current identity from a token OR an SSO cookie. */
  async function refreshMe() {
    // With a token-only setup and no token, there's nothing to resolve. With SSO
    // we always ask — the session cookie (if any) is sent automatically.
    if (authEnabled.value && !ssoEnabled.value && !token.value) {
      me.value = null
      return
    }
    try {
      me.value = await api.whoami()
    } catch {
      me.value = null
    }
  }

  const needsUnlock = computed(() => authRequired.value && !signedIn.value)

  /** Resolve true once unlocked; opens the token dialog (which also offers SSO). */
  async function ensureUnlocked(): Promise<boolean> {
    if (authEnabled.value === null) await refreshStatus()
    if (!authRequired.value || token.value || signedIn.value) return true
    dialogOpen.value = true
    return new Promise<boolean>((res) => {
      resolver = res
    })
  }

  /** Full-page redirect into the IdP; returns to `next` after login. */
  function loginSso(next: string) {
    window.location.href = `${api.base}/api/auth/sso/login?next=${encodeURIComponent(next)}`
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

  async function logout() {
    if (ssoEnabled.value) {
      try {
        await api.ssoLogout() // clear the HttpOnly session cookie server-side
      } catch {
        /* best-effort */
      }
    }
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
    ssoEnabled,
    authRequired,
    signedIn,
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
    loginSso,
    submitToken,
    cancel,
    logout,
  }
}
