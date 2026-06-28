import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, getAdminToken } from '@/lib/api'
import { useAuth } from '@/composables/useAuth'

describe('useAuth gate', () => {
  beforeEach(() => useAuth().logout())
  afterEach(() => vi.restoreAllMocks())

  it('ensureUnlocked resolves true when auth is disabled', async () => {
    vi.spyOn(api, 'authStatus').mockResolvedValue({ auth_enabled: false })
    const { ensureUnlocked, refreshStatus, authEnabled } = useAuth()
    await refreshStatus()
    expect(authEnabled.value).toBe(false)
    expect(await ensureUnlocked()).toBe(true)
  })

  it('submitToken stores a verified token and marks unlocked', async () => {
    vi.spyOn(api, 'whoamiWith').mockResolvedValue({ actor: 'alice', role: 'admin' })
    const { submitToken, hasToken, role } = useAuth()
    expect(await submitToken('good-token')).toBe(true)
    expect(getAdminToken()).toBe('good-token')
    expect(hasToken.value).toBe(true)
    expect(role.value).toBe('admin')
  })

  it('submitToken rejects an invalid token', async () => {
    vi.spyOn(api, 'whoamiWith').mockResolvedValue(null)
    const { submitToken, hasToken } = useAuth()
    expect(await submitToken('bad')).toBe(false)
    expect(hasToken.value).toBe(false)
  })

  it('hasRole respects the monotonic ladder', async () => {
    vi.spyOn(api, 'whoamiWith').mockResolvedValue({ actor: 'op', role: 'operator' })
    const { submitToken, hasRole, isAdmin, canOperate } = useAuth()
    await submitToken('op-token')
    expect(canOperate.value).toBe(true)
    expect(isAdmin.value).toBe(false)
    expect(hasRole('viewer')).toBe(true)
    expect(hasRole('admin')).toBe(false)
  })
})
