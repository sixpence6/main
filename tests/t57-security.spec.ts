import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createMemoryHistory } from 'vue-router'
import AccountSecurityPage from '@/pages/AccountSecurityPage.vue'
import { createAppRouter } from '@/router'
import { clearSession, currentAccount } from '@/auth/session'
import { setCsrfToken } from '@/api/csrf'
import { FetchController } from './support/fetch'

let fetchController: FetchController | undefined

afterEach(() => {
  fetchController?.restore()
  fetchController = undefined
  clearSession()
})

const account = {
  id: 'account-1',
  email: 'user@example.com',
  is_admin: false,
  status: 'active' as const,
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
}

const currentSession = {
  id: 'session-current',
  current: true,
  created_at: '2026-01-03T08:00:00Z',
  last_activity_at: '2026-01-03T09:00:00Z',
  idle_expires_at: '2026-01-03T09:30:00Z',
  absolute_expires_at: '2026-01-03T20:00:00Z',
  client_info: '当前设备',
}

const otherSession = {
  id: 'session-other',
  current: false,
  created_at: '2026-01-02T08:00:00Z',
  last_activity_at: '2026-01-02T09:00:00Z',
  idle_expires_at: '2026-01-02T09:30:00Z',
  absolute_expires_at: '2026-01-02T20:00:00Z',
  client_info: '其他设备',
}

async function mountPage(): Promise<ReturnType<typeof createAppRouter>> {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/account/security')
  await router.isReady()
  currentAccount.value = account
  setCsrfToken('session-csrf')
  render(AccountSecurityPage, { global: { plugins: [router] } })
  return router
}

describe('T57 本人改密与多端会话', () => {
  it('修改密码提交当前密码与新密码，成功后回到登录页并清除本地会话', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/auth/sessions', { items: [currentSession] })
    fetchController.route('POST', '/api/auth/password/change', () => ({ status: 204 }))

    const router = await mountPage()
    expect(await screen.findByText('当前设备')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('当前密码'), 'old password 42')
    await user.type(screen.getByLabelText('新密码'), 'a very good new password 42')
    await user.type(screen.getByLabelText('再次输入新密码'), 'a very good new password 42')
    await user.click(screen.getByRole('button', { name: '修改密码' }))

    await waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    const request = fetchController.last('POST', '/api/auth/password/change')
    expect(request?.headers.get('x-csrf-token')).toBe('session-csrf')
    expect(request?.body).toEqual({
      current_password: 'old password 42',
      new_password: 'a very good new password 42',
    })
    expect(currentAccount.value).toBeNull()
  })

  it('可撤销指定其他会话，页面只显示安全字段', async () => {
    fetchController = new FetchController().install()
    let listCalls = 0
    fetchController.route('GET', '/api/auth/sessions', () => {
      listCalls += 1
      return { body: { items: listCalls === 1 ? [currentSession, otherSession] : [currentSession] } }
    })
    fetchController.route('DELETE', '/api/auth/sessions/session-other', () => ({ status: 204 }))

    await mountPage()
    expect(await screen.findByText('其他设备')).toBeInTheDocument()
    expect(screen.getByText('当前会话')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('session-current')
    expect(document.body.textContent).not.toContain('session-other')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '撤销会话 其他设备' }))
    expect(await screen.findByText('所选会话已撤销。')).toBeInTheDocument()
    expect(fetchController.last('DELETE', '/api/auth/sessions/session-other')?.headers.get('x-csrf-token')).toBe('session-csrf')
    await waitFor(() => expect(listCalls).toBe(2))
  })

  it('撤销其他会话只保留当前会话；撤销当前会话后回到登录页', async () => {
    fetchController = new FetchController().install()
    let listCalls = 0
    fetchController.route('GET', '/api/auth/sessions', () => {
      listCalls += 1
      return { body: { items: listCalls === 1 ? [currentSession, otherSession] : [currentSession] } }
    })
    fetchController.route('POST', '/api/auth/sessions/revoke-others', () => ({ status: 204 }))
    fetchController.route('DELETE', '/api/auth/sessions/session-current', () => ({ status: 204 }))

    const router = await mountPage()
    expect(await screen.findByText('其他设备')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '撤销其他会话' }))
    expect(await screen.findByText('其他会话已全部撤销。')).toBeInTheDocument()
    const revokeOthers = fetchController.last('POST', '/api/auth/sessions/revoke-others')
    expect(revokeOthers?.body).toEqual({})
    expect(revokeOthers?.headers.get('x-csrf-token')).toBe('session-csrf')

    await user.click(screen.getByRole('button', { name: '撤销当前会话' }))
    await waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    expect(currentAccount.value).toBeNull()
  })

  it('账号安全路由要求先登录', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/auth/session', {
      error: { code: 'AUTH_REQUIRED', message: '请先登录。' },
    }, 401)

    const router = createAppRouter(createMemoryHistory())
    const { installAuthGuard } = await import('@/auth/session')
    installAuthGuard(router)
    await router.push('/account/security')
    await router.isReady()
    expect(router.currentRoute.value.name).toBe('login')
  })
})
