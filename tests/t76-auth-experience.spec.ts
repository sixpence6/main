import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createMemoryHistory } from 'vue-router'
import LoginPage from '@/pages/LoginPage.vue'
import SetPasswordPage from '@/pages/SetPasswordPage.vue'
import AccountSecurityPage from '@/pages/AccountSecurityPage.vue'
import AdminAccountsPage from '@/pages/AdminAccountsPage.vue'
import { createAppRouter } from '@/router'
import { clearSession, currentAccount } from '@/auth/session'
import { setCsrfToken } from '@/api/csrf'
import { FetchController } from './support/fetch'

let fetchController: FetchController | undefined

afterEach(() => {
  fetchController?.restore()
  fetchController = undefined
  clearSession()
  setCsrfToken(null)
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const admin = {
  id: 'account-admin',
  email: 'admin@example.com',
  is_admin: true,
  status: 'active' as const,
  created_at: '2026-01-01T00:00:00Z',
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

const pending = {
  id: 'acc-pending',
  email: 'pending@example.com',
  is_admin: false,
  status: 'pending_setup' as const,
  created_at: '2026-01-02T00:00:00Z',
  last_login_at: null,
}

const disabled = {
  id: 'acc-disabled',
  email: 'disabled@example.com',
  is_admin: false,
  status: 'disabled' as const,
  created_at: '2026-01-04T00:00:00Z',
  last_login_at: null,
}

const linkPolicy = {
  policy: { daily_limit: null, personal_slots: null, instance_slots: null, updated_at: null },
  effective: { daily_limit: 20, personal_slots: 1, instance_slots: 2, project_slots: 1 },
  defaults: { daily_limit: 20, personal_slots: 1, instance_slots: 2, project_slots: 1 },
}

async function mountWithRouter(component: unknown, path: string) {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  const rendered = render(component as never, { global: { plugins: [router] } })
  return { router, ...rendered }
}

function expectNoRemoteAssets(container: Element): void {
  expect(container.querySelectorAll('img, picture, iframe, script[src], link[rel="stylesheet"]')).toHaveLength(0)
}

describe('T76 品牌登录与个人、管理页面交互', () => {
  it('登录页提供品牌外壳、自动填充提示与可切换的密码可见性', async () => {
    fetchController = new FetchController().install()
    const { container } = await mountWithRouter(LoginPage, '/login')

    expect(screen.getByRole('heading', { name: '登录工作台' })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: '无法登录时的帮助' })).toHaveTextContent(
      '请联系管理员生成一次性重置链接',
    )
    expect(screen.getByText(/本页面不会把密码写入浏览器存储/)).toBeInTheDocument()

    const email = screen.getByLabelText('邮箱')
    expect(email).toHaveAttribute('autocomplete', 'username')
    expect(email).toHaveAttribute('inputmode', 'email')

    const password = screen.getByLabelText('密码')
    expect(password).toHaveAttribute('autocomplete', 'current-password')
    expect(password).toHaveAttribute('type', 'password')
    expect(password.getAttribute('aria-describedby')).toMatch(/-hint$/) 

    const user = userEvent.setup()
    await user.type(password, 'correct horse battery staple')
    await user.click(screen.getByRole('button', { name: '显示密码' }))
    expect(password).toHaveAttribute('type', 'text')
    expect(password).toHaveValue('correct horse battery staple')
    expect(screen.getByRole('button', { name: '隐藏密码' })).toHaveAttribute('aria-pressed', 'true')

    await user.click(screen.getByRole('button', { name: '隐藏密码' }))
    expect(password).toHaveAttribute('type', 'password')
    expect(password).toHaveValue('correct horse battery staple')
    expectNoRemoteAssets(container)
  })

  it('登录页在空值和服务器失败时关联错误，401 与 429 给出可操作提示', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/auth/csrf', { csrf_token: 'preauth-csrf' })
    fetchController.sequence('POST', '/api/auth/login', [
      { status: 401, body: { error: { code: 'INVALID_CREDENTIALS', message: 'invalid' } } },
      { status: 429, body: { error: { code: 'LOGIN_LIMITED', message: 'limited' } } },
    ])
    await mountWithRouter(LoginPage, '/login')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('请输入邮箱地址。')).toBeInTheDocument()
    expect(screen.getByText('请输入密码。')).toBeInTheDocument()
    const email = screen.getByLabelText('邮箱')
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email.getAttribute('aria-describedby')).toContain('login-email-error')
    expect(fetchController.count('POST', '/api/auth/login')).toBe(0)

    await user.type(email, 'user@example.com')
    await user.type(screen.getByLabelText('密码'), 'correct horse battery staple')
    await user.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('邮箱或密码不正确。')).toBeInTheDocument()
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email.getAttribute('aria-describedby')).toContain('login-form-error')

    await user.click(screen.getByRole('button', { name: '登录' }))
    expect(
      await screen.findByText('尝试次数过多，请稍后再试。为避免连续撞库，请稍后再试或联系管理员。'),
    ).toBeInTheDocument()
    expect(fetchController.count('POST', '/api/auth/login')).toBe(2)
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
  })

  it('一次性链接失效时显示联系管理员指引，不渲染密码表单', async () => {
    fetchController = new FetchController().install()
    fetchController.route('GET', '/api/auth/link', () => ({
      status: 400,
      body: { error: { code: 'LINK_INVALID', message: '链接无效、已过期或已被使用，请联系管理员重新获取。' } },
    }))
    await mountWithRouter(SetPasswordPage, '/set-password#token=expired-token')

    expect(
      await screen.findByText('链接无效、已过期或已被使用，请联系管理员重新获取。'),
    ).toBeInTheDocument()
    expect(screen.getByText(/一次性链接通常只有 30 分钟有效期/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回登录' })).toBeInTheDocument()
    expect(screen.queryByLabelText('新密码')).not.toBeInTheDocument()
  })

  it('设置密码页校验不一致并实时显示密码要求，提交前不发送请求', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/auth/link', {
      account_email: 'p***@example.com',
      purpose: 'setup',
      expires_at: '2026-01-02T00:30:00Z',
    })
    await mountWithRouter(SetPasswordPage, '/set-password#token=raw-token')

    expect(await screen.findByText('p***@example.com')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '设置登录密码' })).toBeInTheDocument()
    expect(screen.getByText(/可以使用浏览器或密码管理器生成并保存密码/)).toBeInTheDocument()
    expect(screen.getByText(/系统不会通过邮件发送链接/)).toBeInTheDocument()

    const user = userEvent.setup()
    const password = screen.getByLabelText('新密码')
    const confirmation = screen.getByLabelText('再次输入新密码')
    expect(password).toHaveAttribute('autocomplete', 'new-password')
    expect(confirmation).toHaveAttribute('autocomplete', 'new-password')
    expect(password).toHaveAttribute('minlength', '15')
    expect(password).toHaveAttribute('maxlength', '128')

    await user.type(password, 'a very good user password 42')
    const minimum = screen.getByText('至少 15 个 Unicode 码点').closest('li')
    expect(minimum).toHaveClass('password-policy-met')
    await user.type(confirmation, 'a different user password 42')

    await user.click(screen.getByRole('button', { name: '显示新密码' }))
    expect(password).toHaveAttribute('type', 'text')
    await user.click(screen.getByRole('button', { name: '设置密码' }))

    expect(await screen.findByText('两次输入的密码不一致。')).toBeInTheDocument()
    expect(confirmation).toHaveAttribute('aria-invalid', 'true')
    expect(confirmation.getAttribute('aria-describedby')).toContain('-error')
    expect(fetchController.count('POST', '/api/auth/link')).toBe(0)
    expect(password).toHaveValue('a very good user password 42')
  })

  it('账号安全页用可切换字段校验本人改密，并提供管理员重置指引', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/auth/sessions', {
      items: [
        {
          id: 'session-current',
          current: true,
          created_at: '2026-01-03T08:00:00Z',
          last_activity_at: '2026-01-03T09:00:00Z',
          idle_expires_at: '2026-01-03T09:30:00Z',
          absolute_expires_at: '2026-01-03T20:00:00Z',
          client_info: '当前设备',
        },
      ],
    })
    fetchController.json('GET', '/api/auth/security-events', { items: [] })
    currentAccount.value = active
    setCsrfToken('user-csrf')
    await mountWithRouter(AccountSecurityPage, '/account/security')

    expect(await screen.findByText('当前设备')).toBeInTheDocument()
    expect(screen.getByText(/管理员无法读取你的密码/)).toBeInTheDocument()
    const current = screen.getByLabelText('当前密码')
    const next = screen.getByLabelText('新密码')
    const confirm = screen.getByLabelText('再次输入新密码')
    expect(current).toHaveAttribute('autocomplete', 'current-password')
    expect(next).toHaveAttribute('autocomplete', 'new-password')
    expect(confirm).toHaveAttribute('autocomplete', 'new-password')

    const user = userEvent.setup()
    await user.type(current, 'same password 42 x')
    await user.type(next, 'same password 42 x')
    await user.type(confirm, 'same password 42 x')
    await user.click(screen.getByRole('button', { name: '修改密码' }))
    expect(await screen.findByText('新密码不能与当前密码相同。')).toBeInTheDocument()
    expect(next).toHaveAttribute('aria-invalid', 'true')
    expect(fetchController.count('POST', '/api/auth/password/change')).toBe(0)

    await user.click(screen.getByRole('button', { name: '显示新密码' }))
    expect(next).toHaveAttribute('type', 'text')
    expect(next).toHaveValue('same password 42 x')
  })

  it('管理页显示账号概览、状态和移动卡片标签，邮箱错误就地关联', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/admin/generation-limits', linkPolicy)
    fetchController.json('GET', '/api/admin/accounts', { items: [admin, active, pending, disabled] })
    fetchController.json('GET', '/api/admin/security-events', { items: [] })
    const { container } = await mountWithRouter(AdminAccountsPage, '/admin/accounts')

    expect(await screen.findByText('pending@example.com')).toBeInTheDocument()
    const summary = screen.getByLabelText('账号概览')
    expect(within(summary).getByText('账号总数').previousElementSibling).toHaveTextContent('4')
    expect(within(summary).getByText('正常').previousElementSibling).toHaveTextContent('2')
    expect(within(summary).getByText('待设置密码').previousElementSibling).toHaveTextContent('1')
    expect(screen.getAllByText('已停用').length).toBeGreaterThanOrEqual(2)
    expect(container.querySelector('td[data-label="邮箱"]')).not.toBeNull()
    expect(container.querySelector('td[data-label="操作"]')).not.toBeNull()
    expect(screen.getByRole('button', { name: '恢复账号' })).toBeInTheDocument()
    expect(screen.getByText('由本机 CLI 管理')).toBeInTheDocument()

    const user = userEvent.setup()
    const email = screen.getByLabelText('邮箱')
    await user.type(email, 'not-an-email')
    await user.click(screen.getByRole('button', { name: '创建账号' }))
    expect(await screen.findByText('请输入有效的邮箱地址。')).toBeInTheDocument()
    expect(email).toHaveAttribute('aria-invalid', 'true')
    expect(email.getAttribute('aria-describedby')).toContain('create-account-error')
    expect(fetchController.count('POST', '/api/admin/accounts')).toBe(0)
    expectNoRemoteAssets(container)
  })

  it('一次性链接弹层以邮箱标识目标并可用 Escape 关闭且丢弃链接', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/admin/generation-limits', linkPolicy)
    fetchController.json('GET', '/api/admin/accounts', { items: [pending, active] })
    fetchController.json('GET', '/api/admin/security-events', { items: [] })
    fetchController.json(
      'POST',
      '/api/admin/accounts/acc-pending/links',
      {
        account_id: 'acc-pending',
        link: 'https://testserver/set-password#token=one-time-secret',
        purpose: 'setup',
        expires_at: '2026-01-02T00:30:00Z',
      },
      201,
    )
    await mountWithRouter(AdminAccountsPage, '/admin/accounts')

    expect(await screen.findByText('pending@example.com')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '生成设置链接' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/pending@example.com/)).toBeInTheDocument()
    expect(within(dialog).getByDisplayValue('https://testserver/set-password#token=one-time-secret')).toBeInTheDocument()
    expect(fetchController.last('POST', '/api/admin/accounts/acc-pending/links')?.body).toEqual({ purpose: 'setup' })

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByDisplayValue('https://testserver/set-password#token=one-time-secret')).not.toBeInTheDocument()
  })
})
