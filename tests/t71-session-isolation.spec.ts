import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/vue'
import { defineComponent, h, ref } from 'vue'
import { api } from '@/api/client'
import { useWorkflow } from '@/composables/useWorkflow'
import type { WorkflowView } from '@/api/workflow'
import { clearSession, currentAccount, ensureSession, sessionEstablished, sessionInvalidated } from '@/auth/session'
import type { AccountView, ProjectDetailResponse } from '@/api/types'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, emptyConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

const ACCOUNT_A: AccountView = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  email: 'a@example.com',
  is_admin: false,
  status: 'active',
  created_at: '2026-10-01T00:00:00.000Z',
  last_login_at: null,
}
const ACCOUNT_B: AccountView = {
  ...ACCOUNT_A,
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  email: 'b@example.com',
}

function authBody(account: AccountView, sessionId: string) {
  return {
    account,
    session_id: sessionId,
    csrf_token: 'csrf-' + sessionId,
    expires_at: '2026-11-02T00:00:00.000Z',
  }
}

function detailBody(): ProjectDetailResponse {
  return {
    project: sampleProject({ id: PROJECT_A, title: '项目甲' }),
    messages: [],
    generations: [],
  }
}

beforeEach(() => {
  clearSession()
  sessionInvalidated.value = false
  sessionEstablished.value = false
})

afterEach(() => {
  clearSession()
  harness?.dispose()
  harness = undefined
})

describe('T71 登录生命周期与页面隔离', () => {
  it('A→B→A 和同账号新会话都清空草稿、项目与旧恢复引用', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      setup(fetch) {
        fetch.sequence('GET', '/api/auth/session', [
          { body: authBody(ACCOUNT_A, 'session-a1') },
          { body: authBody(ACCOUNT_B, 'session-b1') },
          { body: authBody(ACCOUNT_A, 'session-a2') },
          { body: authBody(ACCOUNT_A, 'session-a3') },
        ])
        fetch.json('GET', '/api/projects/' + PROJECT_A, detailBody())
      },
    })

    await ensureSession(true)
    window.history.pushState({}, '', '/?project=' + PROJECT_A)
    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()

    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '账号 A 的未发送草稿')
    const requestKey = 'v3-generation-request:' + PROJECT_A + ':run-71:3:digest'
    const credentialKey = 'ecommerce-generation-pending:' + PROJECT_A
    harness.storage.local.setItem(requestKey, 'request-a')
    harness.storage.session.setItem(credentialKey, JSON.stringify({ projectId: PROJECT_A, requestId: 'request-a' }))

    await ensureSession(true)
    window.history.pushState({}, '', '/')
    window.dispatchEvent(new PopStateEvent('popstate'))
    await harness.waitFor(() => {
      expect(screen.getByRole('heading', { name: '从一个商品想法开始' })).toBeVisible()
    })
    expect(screen.queryByRole('heading', { name: '项目甲' })).not.toBeInTheDocument()
    await harness.waitFor(() => {
      expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue('')
    })
    expect(harness.storage.local.getItem(requestKey)).toBeNull()
    expect(harness.storage.session.getItem(credentialKey)).toBeNull()

    await ensureSession(true)
    expect(screen.queryByRole('heading', { name: '项目甲' })).not.toBeInTheDocument()
    await harness.waitFor(() => {
      expect(screen.getByRole('heading', { name: '从一个商品想法开始' })).toBeVisible()
    })
    await harness.waitFor(() => {
      expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue('')
    })

    // 同一账号重新登录也是新的登录生命周期，不能恢复上一个生命周期的工作台。
    await ensureSession(true)
    expect(screen.queryByRole('heading', { name: '项目甲' })).not.toBeInTheDocument()
    await harness.waitFor(() => {
      expect(screen.getByRole('heading', { name: '从一个商品想法开始' })).toBeVisible()
    })
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
    expect(harness.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })

  it('迟到的项目响应不因 abort 缺失而重新显示旧业务', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      setup(fetch) {
        fetch.json('GET', '/api/auth/session', authBody(ACCOUNT_A, 'session-a1'))
        fetch.route('GET', '/api/projects/' + PROJECT_A, async () => {
          await new Promise((resolve) => setTimeout(resolve, 120))
          return { body: detailBody() }
        })
      },
    })
    await ensureSession(true)
    window.history.pushState({}, '', '/?project=' + PROJECT_A)
    window.dispatchEvent(new PopStateEvent('popstate'))
    await harness.waitFor(() => expect(harness?.fetch.count('GET', '/api/projects/' + PROJECT_A)).toBe(1))

    clearSession()
    await new Promise((resolve) => setTimeout(resolve, 180))

    expect(currentAccount.value).toBeNull()
    expect(sessionEstablished.value).toBe(false)
    expect(screen.queryByRole('heading', { name: '项目甲' })).not.toBeInTheDocument()
    expect(screen.getByText('正在重新验证登录状态…')).toBeVisible()
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
  })

  it('迟到的流程响应在登录生命周期变化后不写入旧视图', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      setup(fetch) {
        fetch.json('GET', '/api/auth/session', authBody(ACCOUNT_A, 'session-a1'))
        fetch.route('GET', '/api/projects/' + PROJECT_A + '/workflow', async () => {
          await new Promise((resolve) => setTimeout(resolve, 120))
          return {
            body: {
              project_id: PROJECT_A,
              run_id: 'run-old',
              stage: 'S08',
              status: 'active',
              project_revision: 3,
              requirement_revision: 3,
              intent_route: null,
              intent_action: null,
              issue: null,
              artifacts: [],
              approvals: [],
              available_actions: [],
              legacy: [],
              generation_job: null,
              delivery: null,
              updated_at: null,
            } satisfies WorkflowView,
          }
        })
      },
    })
    await ensureSession(true)
    const Probe = defineComponent({
      setup() {
        const projectId = ref<string | null>(PROJECT_A)
        const flow = useWorkflow(projectId)
        return () => h('div', { 'data-testid': 'workflow-stage' }, flow.view.value?.stage ?? 'empty')
      },
    })
    const probe = render(Probe)
    await harness.waitFor(() => expect(harness?.fetch.count('GET', '/api/projects/' + PROJECT_A + '/workflow')).toBe(1))

    clearSession()
    await new Promise((resolve) => setTimeout(resolve, 180))

    expect(probe.getByTestId('workflow-stage')).toHaveTextContent('empty')
    expect(currentAccount.value).toBeNull()
    expect(harness.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
    probe.unmount()
  })

  it('401 清除会话与私有存储，403 和供应商 503 不当作退出', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      setup(fetch) {
        fetch.json('GET', '/api/auth/session', authBody(ACCOUNT_A, 'session-a1'))
        fetch.sequence('GET', '/api/projects', [
          { status: 403, body: { error: { code: 'PERMISSION_DENIED', message: '无权限' } } },
          { status: 503, body: { error: { code: 'SERVICE_UNAVAILABLE', message: '供应商暂不可用' } } },
          { status: 401, body: { error: { code: 'AUTH_REQUIRED', message: '请先登录' } } },
        ])
      },
    })
    await ensureSession(true)
    const requestKey = 'v3-generation-request:' + PROJECT_A + ':run-71:3:digest'
    harness.storage.local.setItem(requestKey, 'request-a')

    await expect(api.listProjects()).rejects.toMatchObject({ status: 403, code: 'PERMISSION_DENIED' })
    expect(currentAccount.value?.id).toBe(ACCOUNT_A.id)
    expect(sessionEstablished.value).toBe(true)
    expect(harness.storage.local.getItem(requestKey)).toBe('request-a')

    await expect(api.listProjects()).rejects.toMatchObject({ status: 503, code: 'SERVICE_UNAVAILABLE' })
    expect(currentAccount.value?.id).toBe(ACCOUNT_A.id)
    expect(sessionEstablished.value).toBe(true)

    await expect(api.listProjects()).rejects.toMatchObject({ status: 401, code: 'AUTH_REQUIRED' })
    expect(currentAccount.value).toBeNull()
    expect(sessionEstablished.value).toBe(false)
    expect(harness.storage.local.getItem(requestKey)).toBeNull()
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
  })
})
