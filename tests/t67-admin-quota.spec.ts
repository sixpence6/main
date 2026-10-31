import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createMemoryHistory } from 'vue-router'
import AdminAccountsPage from '@/pages/AdminAccountsPage.vue'
import { createAppRouter } from '@/router'
import { currentAccount } from '@/auth/session'
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

const active = {
  id: 'acc-active',
  email: 'active@example.com',
  is_admin: false,
  status: 'active' as const,
  created_at: '2026-01-03T00:00:00Z',
  last_login_at: null,
}

const defaults = { daily_limit: 20, personal_slots: 1, instance_slots: 2, project_slots: 1 }
const emptyPolicy = { daily_limit: null, personal_slots: null, instance_slots: null, updated_at: null }

async function mountAdmin(): Promise<void> {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/admin/accounts')
  await router.isReady()
  currentAccount.value = admin
  setCsrfToken('admin-csrf')
  render(AdminAccountsPage, { global: { plugins: [router] } })
}

describe('T67 本人用量与管理员额度调整', () => {
  it('管理员保存实例额度时提交三个正整数且不发送角色字段', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/admin/accounts', { items: [active] })
    fetchController.json('GET', '/api/admin/security-events', { items: [] })
    fetchController.json('GET', '/api/admin/generation-limits', {
      policy: emptyPolicy,
      effective: defaults,
      defaults,
    })
    fetchController.route('PUT', '/api/admin/generation-limits', (request) => ({
      body: {
        policy: { daily_limit: 5, personal_slots: 2, instance_slots: 3, updated_at: '2026-01-05T00:00:00Z' },
        effective: { daily_limit: 5, personal_slots: 2, instance_slots: 3, project_slots: 1 },
        defaults,
      },
    }))

    await mountAdmin()
    expect(await screen.findByText('当前生效：每日 20 次 · 个人 1 · 实例 2 · 单项目 1')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('每日受理次数'), '5')
    await user.type(screen.getByLabelText('个人活跃槽位'), '2')
    await user.type(screen.getByLabelText('实例并发槽位'), '3')
    await user.click(screen.getByRole('button', { name: '保存实例额度' }))

    await waitFor(() => expect(fetchController!.count('PUT', '/api/admin/generation-limits')).toBe(1))
    const request = fetchController.last('PUT', '/api/admin/generation-limits')
    expect(request?.headers.get('x-csrf-token')).toBe('admin-csrf')
    expect(request?.body).toEqual({ daily_limit: 5, personal_slots: 2, instance_slots: 3 })
    expect(JSON.stringify(request?.body)).not.toContain('is_admin')
    expect(await screen.findByText('当前生效：每日 5 次 · 个人 2 · 实例 3 · 单项目 1')).toBeInTheDocument()
    expect(await screen.findByText('实例生成额度已保存，变更已写入审计。')).toBeInTheDocument()
  })

  it('账号额度对话框显示本人用量与限制并只提交两个覆盖值', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/admin/accounts', { items: [active] })
    fetchController.json('GET', '/api/admin/security-events', { items: [] })
    fetchController.json('GET', '/api/admin/generation-limits', {
      policy: emptyPolicy,
      effective: defaults,
      defaults,
    })
    fetchController.json('GET', '/api/admin/accounts/acc-active/generation-limits', {
      account_id: 'acc-active',
      email: 'active@example.com',
      policy: { daily_limit: null, personal_slots: null, instance_slots: null, updated_at: null },
      effective: { daily_limit: 20, personal_slots: 1, instance_slots: 2, project_slots: 1 },
      usage: {
        used: 3,
        remaining: 17,
        daily_limit: 20,
        personal_active: 0,
        personal_limit: 1,
        instance_active: 1,
        instance_limit: 2,
        window_started_at: '2026-01-04T00:00:00Z',
        window_expires_at: '2026-01-05T00:00:00Z',
      },
    })
    fetchController.route('PUT', '/api/admin/accounts/acc-active/generation-limits', () => ({
      body: {
        account_id: 'acc-active',
        email: 'active@example.com',
        policy: { daily_limit: 7, personal_slots: 1, instance_slots: null, updated_at: '2026-01-05T00:00:00Z' },
        effective: { daily_limit: 7, personal_slots: 1, instance_slots: 2, project_slots: 1 },
        usage: {
          used: 3,
          remaining: 4,
          daily_limit: 7,
          personal_active: 0,
          personal_limit: 1,
          instance_active: 1,
          instance_limit: 2,
          window_started_at: '2026-01-04T00:00:00Z',
          window_expires_at: '2026-01-05T00:00:00Z',
        },
      },
    }))

    await mountAdmin()
    expect(await screen.findByText('active@example.com')).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '调整额度' }))
    expect(await screen.findByText(/当前生效：每日 20 次 · 个人 1 · 已用 3 · 剩余 17/)).toBeInTheDocument()
    expect(screen.getByText(/活跃：个人 0 \/ 1 · 实例 1 \/ 2/)).toBeInTheDocument()

    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('每日受理次数'), '7')
    await user.click(within(dialog).getByRole('button', { name: '保存账号额度' }))

    await waitFor(() => expect(fetchController!.count('PUT', '/api/admin/accounts/acc-active/generation-limits')).toBe(1))
    const request = fetchController.last('PUT', '/api/admin/accounts/acc-active/generation-limits')
    expect(request?.body).toEqual({ daily_limit: 7, personal_slots: null })
    expect(await screen.findByText('账号生成额度已保存，变更已写入审计。')).toBeInTheDocument()
    expect(screen.getByText(/当前生效：每日 7 次 · 个人 1 · 已用 3 · 剩余 4/)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('project_id')
  })

  it('安全事件显示额度变更前后值与范围，普通用户没有调整入口', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/admin/accounts', { items: [active] })
    fetchController.json('GET', '/api/admin/security-events', {
      items: [
        {
          id: 'event-quota',
          occurred_at: '2026-01-05T00:00:00Z',
          event_type: 'generation_limits_updated',
          result: 'success',
          reason_code: null,
          actor_kind: 'admin',
          actor_ref: 'actor-ref',
          actor_label: 'admin@example.com',
          target_kind: 'generation_limits',
          target_ref: 'target-ref',
          target_label: '实例生成额度',
          subject_ref: null,
          before: { daily_limit: 20, personal_slots: 1, instance_slots: 2 },
          after: { daily_limit: 5, personal_slots: 2, instance_slots: 3 },
          detail: { scope: 'instance' },
        },
      ],
    })
    fetchController.json('GET', '/api/admin/generation-limits', {
      policy: { daily_limit: 5, personal_slots: 2, instance_slots: 3, updated_at: '2026-01-05T00:00:00Z' },
      effective: { daily_limit: 5, personal_slots: 2, instance_slots: 3, project_slots: 1 },
      defaults,
    })

    await mountAdmin()
    expect(await screen.findByText('调整生成额度')).toBeInTheDocument()
    expect(screen.getByText('每日 20 → 5 · 个人 1 → 2 · 实例 2 → 3')).toBeInTheDocument()
    expect(screen.getByText(/范围 实例/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '调整额度' })).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('target-ref')
  })
})
