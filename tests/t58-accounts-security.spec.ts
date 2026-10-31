import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createMemoryHistory } from 'vue-router'
import AdminAccountsPage from '@/pages/AdminAccountsPage.vue'
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

async function mountAdmin(): Promise<ReturnType<typeof createAppRouter>> {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/admin/accounts')
  await router.isReady()
  currentAccount.value = admin
  setCsrfToken('admin-csrf')
  render(AdminAccountsPage, { global: { plugins: [router] } })
  return router
}

async function mountSecurity(): Promise<ReturnType<typeof createAppRouter>> {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/account/security')
  await router.isReady()
  currentAccount.value = active
  setCsrfToken('user-csrf')
  render(AccountSecurityPage, { global: { plugins: [router] } })
  return router
}

describe('T58 账号状态与安全事件视图', () => {
  it('管理员可停用并恢复普通账号，状态操作带 CSRF 且不发送角色字段', async () => {
    fetchController = new FetchController().install()
    type StatusFixture = Omit<typeof active, 'status'> & { status: 'active' | 'disabled' }
    let items: StatusFixture[] = [active]
    fetchController.route('GET', '/api/admin/accounts', () => ({ body: { items } }))
    fetchController.route('GET', '/api/admin/security-events', () => ({ body: { items: [] } }))
    fetchController.route('POST', '/api/admin/accounts/acc-active/disable', () => {
      items = [{ ...active, status: 'disabled' as const }]
      return { body: { account: items[0] } }
    })
    fetchController.route('POST', '/api/admin/accounts/acc-active/restore', () => {
      items = [active]
      return { body: { account: active } }
    })

    await mountAdmin()
    expect(await screen.findByText('active@example.com')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '停用账号' }))
    expect(await screen.findByText('账号已停用，其登录会话和未使用的一次性链接已撤销。')).toBeInTheDocument()
    const disableRequest = fetchController.last('POST', '/api/admin/accounts/acc-active/disable')
    expect(disableRequest?.headers.get('x-csrf-token')).toBe('admin-csrf')
    expect(disableRequest?.body).toEqual({})
    expect(await screen.findByRole('button', { name: '恢复账号' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '恢复账号' }))
    expect(await screen.findByText('账号已恢复；旧会话和旧链接不会恢复，本人需重新登录。')).toBeInTheDocument()
    expect(fetchController.count('POST', '/api/admin/accounts/acc-active/restore')).toBe(1)
    expect(await screen.findByRole('button', { name: '停用账号' })).toBeInTheDocument()
    expect(JSON.stringify(fetchController.writes.map((request) => request.body))).not.toContain('is_admin')
  })

  it('管理员安全事件显示前后状态、脱敏未知账号和必要计数', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/admin/accounts', { items: [admin] })
    fetchController.json('GET', '/api/admin/security-events', {
      items: [
        {
          id: 'event-1',
          occurred_at: '2026-01-04T00:00:00Z',
          event_type: 'account_disabled',
          result: 'success',
          reason_code: null,
          actor_kind: 'admin',
          actor_ref: 'actor-ref',
          actor_label: 'admin@example.com',
          target_kind: 'account',
          target_ref: 'target-ref',
          target_label: '未知账号（deadbeef）',
          subject_ref: 'subject-ref',
          before: { status: 'active', is_disabled: false, session_epoch: 1 },
          after: { status: 'disabled', is_disabled: true, session_epoch: 2 },
          detail: { sessions_revoked: 2, links_revoked: 1 },
        },
      ],
    })

    await mountAdmin()
    expect(await screen.findByText('停用账号')).toBeInTheDocument()
    expect(screen.getAllByText('admin@example.com')).toHaveLength(2)
    expect(screen.getByText(/未知账号（deadbeef）/)).toBeInTheDocument()
    expect(screen.getByText('正常 → 已停用')).toBeInTheDocument()
    expect(screen.getByText(/撤销会话 2 · 撤销链接 1/)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('target-ref')
    expect(document.body.textContent).not.toContain('secret-token')
  })

  it('个人安全事件只显示本人必要字段，不显示会话凭证或创作内容', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/auth/sessions', { items: [] })
    fetchController.json('GET', '/api/auth/security-events', {
      items: [
        {
          id: 'event-2',
          occurred_at: '2026-01-04T00:00:00Z',
          event_type: 'account_disabled',
          result: 'success',
          reason_code: null,
          actor_kind: 'admin',
          detail: null,
        },
        {
          id: 'event-3',
          occurred_at: '2026-01-04T00:00:00Z',
          event_type: 'account_restored',
          result: 'success',
          reason_code: null,
          actor_kind: 'admin',
          detail: { sessions_revoked: 1 },
        },
      ],
    })

    await mountSecurity()
    expect(await screen.findByText('账号停用')).toBeInTheDocument()
    expect(screen.getByText('账号恢复')).toBeInTheDocument()
    expect(screen.getAllByText('成功')).toHaveLength(2)
    expect(screen.getAllByText(/操作者：管理员/)).toHaveLength(2)
    expect(document.body.textContent).not.toContain('session-current')
    expect(document.body.textContent).not.toContain('event-2')
    expect(document.body.textContent).not.toContain('sessions_revoked')
  })
})
