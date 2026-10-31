import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createMemoryHistory } from 'vue-router'
import AdminAccountsPage from '@/pages/AdminAccountsPage.vue'
import SetPasswordPage from '@/pages/SetPasswordPage.vue'
import { createAppRouter } from '@/router'
import { currentAccount, installAuthGuard } from '@/auth/session'
import { setCsrfToken } from '@/api/csrf'
import { FetchController } from './support/fetch'

let fetchController: FetchController | undefined

afterEach(() => {
  fetchController?.restore()
  fetchController = undefined
  currentAccount.value = null
  setCsrfToken(null)
})

const admin = {
  id: 'account-admin',
  email: 'admin@example.com',
  is_admin: true,
  status: 'active' as const,
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
}

const pending = {
  id: 'acc-pending',
  email: 'pending@example.com',
  is_admin: false,
  status: 'pending_setup' as const,
  created_at: '2026-01-02T00:00:00Z',
  last_login_at: null,
}

const active = {
  id: 'acc-active',
  email: 'active@example.com',
  is_admin: false,
  status: 'active' as const,
  created_at: '2026-01-03T00:00:00Z',
  last_login_at: null,
}

describe('T56 账号创建与一次性设置链接', () => {
  it('设置密码页只从 hash 读取令牌，提交后不建立登录会话并清除地址栏令牌', async () => {
    fetchController = new FetchController().install()
    let statusHeader: string | null = null
    fetchController.route('GET', '/api/auth/link', (request) => {
      statusHeader = request.headers.get('x-link-token')
      return {
        body: {
          account_email: 'p***@example.com',
          purpose: 'setup',
          expires_at: '2026-01-02T00:30:00Z',
        },
      }
    })
    fetchController.json('GET', '/api/auth/csrf', { csrf_token: 'preauth-csrf' })
    fetchController.json('POST', '/api/auth/link', { status: 'ok' })

    const router = createAppRouter(createMemoryHistory())
    await router.push('/set-password#token=raw-token')
    await router.isReady()
    render(SetPasswordPage, { global: { plugins: [router] } })

    expect(await screen.findByText(/p\*\*\*@example\.com/)).toBeInTheDocument()
    expect(statusHeader).toBe('raw-token')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('新密码'), 'a very good user password 42')
    await user.type(screen.getByLabelText('再次输入新密码'), 'a very good user password 42')
    await user.click(screen.getByRole('button', { name: '设置密码' }))

    expect(await screen.findByText('密码已设置成功，请使用新密码登录。')).toBeInTheDocument()
    expect(fetchController.last('GET', '/api/auth/link')?.headers.get('x-link-token')).toBe('raw-token')
    const submit = fetchController.last('POST', '/api/auth/link')
    expect(submit?.headers.get('x-csrf-token')).toBe('preauth-csrf')
    expect(submit?.body).toEqual({
      token: 'raw-token',
      password: 'a very good user password 42',
      purpose: 'setup',
    })
    expect(fetchController.count('POST', '/api/auth/login')).toBe(0)
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
    await waitFor(() => expect(router.currentRoute.value.hash).toBe(''))
  })

  it('管理员列表可创建账号并只显示一次链接，关闭后从页面丢弃', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/admin/accounts', { items: [pending, active] })
    fetchController.json('POST', '/api/admin/accounts', {
      account: {
        id: 'acc-new',
        email: 'new@example.com',
        is_admin: false,
        status: 'pending_setup',
        created_at: '2026-01-04T00:00:00Z',
        last_login_at: null,
      },
    }, 201)
    fetchController.json('POST', '/api/admin/accounts/acc-pending/links', {
      account_id: 'acc-pending',
      link: 'https://testserver/set-password#token=secret',
      purpose: 'setup',
      expires_at: '2026-01-02T00:30:00Z',
    }, 201)

    const router = createAppRouter(createMemoryHistory())
    await router.push('/admin/accounts')
    await router.isReady()
    render(AdminAccountsPage, { global: { plugins: [router] } })

    expect(await screen.findByText('pending@example.com')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '生成设置链接' }))
    expect(
      await screen.findByDisplayValue('https://testserver/set-password#token=secret'),
    ).toBeInTheDocument()
    expect(fetchController.last('POST', '/api/admin/accounts/acc-pending/links')?.body).toEqual({
      purpose: 'setup',
    })

    await user.click(screen.getByRole('button', { name: '关闭并丢弃' }))
    expect(
      screen.queryByDisplayValue('https://testserver/set-password#token=secret'),
    ).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('邮箱'), 'new@example.com')
    await user.click(screen.getByRole('button', { name: '创建账号' }))
    await waitFor(() => {
      expect(fetchController!.count('POST', '/api/admin/accounts')).toBe(1)
    })
    expect(fetchController.last('POST', '/api/admin/accounts')?.body).toEqual({
      email: 'new@example.com',
    })
    expect(window.localStorage.length).toBe(0)
  })

  it('设置密码页匿名可达，普通账号不能进入账号管理页', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/auth/session', {
      account: { ...active },
      csrf_token: 'session-csrf',
      expires_at: '2026-01-03T12:00:00Z',
    })

    const publicRouter = createAppRouter(createMemoryHistory())
    installAuthGuard(publicRouter)
    await publicRouter.push('/set-password#token=x')
    await publicRouter.isReady()
    expect(publicRouter.currentRoute.value.name).toBe('set-password')
    expect(fetchController.count('GET', '/api/auth/session')).toBe(0)

    const adminRouter = createAppRouter(createMemoryHistory())
    installAuthGuard(adminRouter)
    await adminRouter.push('/admin/accounts')
    await adminRouter.isReady()
    expect(adminRouter.currentRoute.value.name).toBe('describe')
  })
})
