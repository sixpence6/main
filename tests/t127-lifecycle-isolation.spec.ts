import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/vue'
import type { AccountView, ProjectDetailResponse, ProjectSummary, ProjectView } from '@/api/types'
import type { WorkflowVersion, WorkflowView } from '@/api/workflow'
import { api } from '@/api/client'
import {
  clearSession,
  currentAccount,
  ensureSession,
  installSessionLifecycle,
  sessionEstablished,
  sessionInvalidated,
} from '@/auth/session'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  PROJECT_B,
  emptyConfig,
  sampleProject,
  sampleSummary,
} from './support/fixtures'

let harness: WorkspaceHarness | undefined
let cleanupLifecycle: (() => void) | undefined
let originalBroadcastChannel: typeof window.BroadcastChannel | undefined

const DETAIL_A = '/api/projects/' + PROJECT_A
const DETAIL_B = '/api/projects/' + PROJECT_B
const WORKFLOW_A = DETAIL_A + '/workflow'
const MESSAGES_A = DETAIL_A + '/messages'
const RUN_CURRENT = 'run-current'
const RUN_OLD = 'run-old'

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

class FakeBroadcastChannel {
  static all: FakeBroadcastChannel[] = []
  static messages: unknown[] = []
  name: string
  onmessage: ((event: MessageEvent) => void) | null = null

  constructor(name: string) {
    this.name = name
    FakeBroadcastChannel.all.push(this)
  }

  postMessage(data: unknown): void {
    FakeBroadcastChannel.messages.push(data)
    for (const channel of FakeBroadcastChannel.all) {
      if (channel === this || channel.name !== this.name) continue
      channel.onmessage?.(new MessageEvent('message', { data }))
    }
  }

  close(): void {
    FakeBroadcastChannel.all = FakeBroadcastChannel.all.filter((item) => item !== this)
  }
}

function installFakeBroadcastChannel(): void {
  originalBroadcastChannel = window.BroadcastChannel
  FakeBroadcastChannel.all = []
  FakeBroadcastChannel.messages = []
  Object.defineProperty(window, 'BroadcastChannel', {
    configurable: true,
    writable: true,
    value: FakeBroadcastChannel,
  })
}

function pageShow(persisted: boolean): void {
  const event = new Event('pageshow')
  Object.defineProperty(event, 'persisted', { value: persisted })
  window.dispatchEvent(event)
}

function project(id = PROJECT_A, title = '项目甲'): ProjectView {
  return sampleProject({ id, title, revision: 3 })
}

function detail(id = PROJECT_A, title = '项目甲'): ProjectDetailResponse {
  return { project: project(id, title), messages: [], generations: [] }
}

function summary(id: string, title: string): ProjectSummary {
  return sampleSummary({ id, title })
}

function activeView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-active',
    stage: 'S03',
    status: 'active',
    project_revision: 3,
    requirement_revision: 3,
    intent_route: 'image_request',
    intent_action: 'new_request',
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: null,
    versions: [],
    updated_at: '2026-11-01T00:00:00.000Z',
    ...overrides,
  }
}

function version(overrides: Partial<WorkflowVersion> = {}): WorkflowVersion {
  return {
    run_id: RUN_CURRENT,
    sequence_no: 2,
    requirement_revision: 3,
    status: 'published',
    generation_job_id: 'job-current',
    asset_id: 'asset-current',
    filename: 'current.png',
    width: 1536,
    height: 1024,
    caption: '当前版本主图',
    published_at: '2026-10-21T06:02:00.000Z',
    brief: { product_description: '米色亚麻托特包' },
    prompt: { positive_prompt: '当前版本的已批准提示词', negative_prompt: '低清晰度' },
    quality: { status: 'passed' },
    can_download: true,
    created_at: '2026-10-21T06:00:00.000Z',
    ...overrides,
  }
}

function completedView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: RUN_CURRENT,
    stage: 'S11',
    status: 'completed',
    project_revision: 3,
    requirement_revision: 3,
    intent_route: 'image_request',
    intent_action: 'new_request',
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: {
      id: 'delivery-current',
      status: 'published',
      asset_id: 'asset-current',
      filename: 'current.png',
      caption: '当前版本主图',
      published_at: '2026-10-21T06:02:00.000Z',
      created_at: '2026-10-21T06:02:00.000Z',
    },
    versions: [
      version({}),
      version({
        run_id: RUN_OLD,
        sequence_no: 1,
        requirement_revision: 2,
        generation_job_id: 'job-old',
        asset_id: 'asset-old',
        filename: 'old.png',
        width: 1024,
        height: 1024,
        caption: '旧版本主图',
        published_at: '2026-10-20T03:00:00.000Z',
        prompt: { positive_prompt: '旧版本的已批准提示词', negative_prompt: '不要多余文字' },
      }),
    ],
    updated_at: '2026-10-21T06:02:00.000Z',
    ...overrides,
  }
}

function health(fetch: { route: (method: string, path: string, handler: () => { body: unknown }) => unknown }): void {
  fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
}

function storageKeys() {
  return {
    request: 'v3-generation-request:' + PROJECT_A + ':run-71:3:digest',
    pending: 'ecommerce-generation-pending:' + PROJECT_A,
  }
}

async function typeDraft(value: string): Promise<HTMLTextAreaElement> {
  const textarea = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
  await harness!.user.type(textarea, value)
  return textarea as HTMLTextAreaElement
}

async function searchFor(query: string): Promise<HTMLInputElement> {
  const input = (await screen.findByRole('searchbox', { name: '按对话标题搜索' })) as HTMLInputElement
  await harness!.user.clear(input)
  await harness!.user.type(input, query + '{Enter}')
  return input
}

async function openProjectA(): Promise<void> {
  await searchFor('项目甲')
  await harness!.user.click(await screen.findByRole('button', { name: /项目甲/ }))
  await screen.findByRole('heading', { name: '项目甲' })
}

beforeEach(() => {
  clearSession()
  sessionInvalidated.value = false
  sessionEstablished.value = false
  installFakeBroadcastChannel()
})

afterEach(() => {
  cleanupLifecycle?.()
  cleanupLifecycle = undefined
  clearSession()
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
  if (originalBroadcastChannel) {
    Object.defineProperty(window, 'BroadcastChannel', {
      configurable: true,
      writable: true,
      value: originalBroadcastChannel,
    })
  } else {
    delete (window as unknown as Record<string, unknown>).BroadcastChannel
  }
})

describe('T15 完整新对话体验的账号生命周期隔离', () => {
  it('A→B→A 与同账号新登录都清空列表、搜索、草稿和旧恢复引用', async () => {
    let sessionAccount = ACCOUNT_A
    let sessionId = 'session-a1'
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/auth/session', () => ({ body: authBody(sessionAccount, sessionId) }))
        fetch.route('GET', '/api/projects', () => ({
          body: {
            items: sessionAccount.id === ACCOUNT_A.id
              ? [summary(PROJECT_A, '项目甲')]
              : [summary(PROJECT_B, '项目乙')],
            next_cursor: null,
          },
        }))
      },
    })

    await ensureSession(true)
    await searchFor('项目甲')
    await typeDraft('账号 A 的未发送草稿')
    const keys = storageKeys()
    harness.storage.local.setItem(keys.request, 'request-a')
    harness.storage.session.setItem(keys.pending, JSON.stringify({ projectId: PROJECT_A }))
    await searchFor('项目甲')

    sessionAccount = ACCOUNT_B
    sessionId = 'session-b1'
    await ensureSession(true)

    expect(await screen.findByRole('button', { name: /项目乙/ })).toBeVisible()
    expect(screen.queryByRole('button', { name: /项目甲/ })).not.toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: '按对话标题搜索' })).toHaveValue('')
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue('')
    expect(harness.storage.local.getItem(keys.request)).toBeNull()
    expect(harness.storage.session.getItem(keys.pending)).toBeNull()

    sessionAccount = ACCOUNT_A
    sessionId = 'session-a2'
    await ensureSession(true)
    expect(await screen.findByRole('button', { name: /项目甲/ })).toBeVisible()
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue('')

    sessionId = 'session-a3'
    await ensureSession(true)
    expect(await screen.findByRole('button', { name: /项目甲/ })).toBeVisible()
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue('')
    expect(harness!.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
    expect(harness!.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
  })

  it('切换账号后迟到的建项目响应不能把旧项目带回新身份', async () => {
    let sessionAccount = ACCOUNT_A
    let sessionId = 'session-a1'
    let createStarted = false
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/auth/session', () => ({ body: authBody(sessionAccount, sessionId) }))
        fetch.route('GET', '/api/projects', () => ({
          body: {
            items: sessionAccount.id === ACCOUNT_A.id
              ? [summary(PROJECT_A, '项目甲')]
              : [summary(PROJECT_B, '项目乙')],
            next_cursor: null,
          },
        }))
        fetch.route('POST', '/api/projects', () => {
          createStarted = true
          return { body: project(PROJECT_A, '旧账号项目'), delayMs: 180 }
        })
        fetch.route('POST', /\/api\/projects\/[^/]+\/workflow\/actions\/[^/]+$/, () => ({
          body: activeView(),
        }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail(PROJECT_A, '旧账号项目') }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: activeView() }))
      },
    })

    await ensureSession(true)
    await searchFor('项目甲')
    await typeDraft('给账号 A 新建一个项目')
    await harness!.user.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(createStarted).toBe(true))

    sessionAccount = ACCOUNT_B
    sessionId = 'session-b1'
    await ensureSession(true)
    await new Promise((resolve) => setTimeout(resolve, 240))

    expect(currentAccount.value?.id).toBe(ACCOUNT_B.id)
    expect(harness!.router.currentRoute.value.fullPath).toBe('/')
    expect(screen.queryByRole('heading', { name: '旧账号项目' })).not.toBeInTheDocument()
    expect(screen.queryByText('旧账号项目')).not.toBeInTheDocument()
    expect(harness!.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
    expect(harness!.fetch.count('GET', DETAIL_A)).toBe(0)
  })

  it('跨标签失效信号只含随机信号，重新核验后隐藏旧图片并加载新账号列表', async () => {
    let sessionAccount = ACCOUNT_A
    let sessionId = 'session-a1'
    let sessionCalls = 0
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/auth/session', () => {
          sessionCalls += 1
          return {
            body: authBody(sessionAccount, sessionId),
            delayMs: sessionCalls > 1 ? 120 : 0,
          }
        })
        fetch.route('GET', '/api/projects', () => ({
          body: {
            items: sessionAccount.id === ACCOUNT_A.id
              ? [summary(PROJECT_A, '项目甲')]
              : [summary(PROJECT_B, '项目乙')],
            next_cursor: null,
          },
        }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: completedView() }))
      },
    })
    cleanupLifecycle = installSessionLifecycle()
    await ensureSession(true)
    await openProjectA()
    const history = await screen.findByRole('region', { name: '图片历史' })
    expect(within(history).getByRole('img', { name: '当前版本主图' })).toBeVisible()
    await typeDraft('账号 A 的私有草稿')
    await searchFor('项目甲')
    const keys = storageKeys()
    harness.storage.local.setItem(keys.request, 'request-a')

    sessionAccount = ACCOUNT_B
    sessionId = 'session-b1'
    const otherTab = new FakeBroadcastChannel('v3-session-lifecycle')
    otherTab.postMessage({ type: 'session-invalidated', nonce: 'signal-1' })

    expect(await screen.findByText('正在重新验证登录状态…')).toBeVisible()
    expect(await screen.findByRole('button', { name: /项目乙/ })).toBeVisible()
    expect(screen.queryByRole('heading', { name: '项目甲' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '图片历史' })).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: '当前版本主图' })).not.toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: '按对话标题搜索' })).toHaveValue('')
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue('')
    expect(harness.storage.local.getItem(keys.request)).toBeNull()
    await waitFor(() => expect(harness!.router.currentRoute.value.fullPath).toBe('/'))
    expect(currentAccount.value?.id).toBe(ACCOUNT_B.id)

    expect(FakeBroadcastChannel.messages.length).toBeGreaterThan(0)
    for (const message of FakeBroadcastChannel.messages) {
      expect(Object.keys(message as Record<string, unknown>).sort()).toEqual(['nonce', 'type'])
      const serialized = JSON.stringify(message)
      expect(serialized).not.toContain(ACCOUNT_A.email)
      expect(serialized).not.toContain(ACCOUNT_B.email)
      expect(serialized).not.toContain(PROJECT_A)
      expect(serialized).not.toContain('csrf')
      expect(serialized).not.toContain('session-a1')
    }
    expect(harness!.fetch.writes).toHaveLength(0)
  })

  it('浏览器返回先核验身份，旧项目和私有图片不会在新页面残留', async () => {
    let sessionCalls = 0
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/auth/session', () => {
          sessionCalls += 1
          return {
            body: authBody(ACCOUNT_A, 'session-a1'),
            delayMs: sessionCalls > 1 ? 100 : 0,
          }
        })
        fetch.route('GET', '/api/projects', () => ({
          body: { items: [summary(PROJECT_A, '项目甲')], next_cursor: null },
        }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: completedView() }))
      },
    })
    cleanupLifecycle = installSessionLifecycle()
    await ensureSession(true)
    await openProjectA()
    expect(await screen.findByRole('img', { name: '当前版本主图' })).toBeVisible()

    window.history.pushState({}, '', '/')
    window.dispatchEvent(new PopStateEvent('popstate'))

    expect(await screen.findByText('正在重新验证登录状态…')).toBeVisible()
    expect(await screen.findByRole('heading', { name: '发来商品图片，或告诉我你想做什么样的电商图片' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: '项目甲' })).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: '当前版本主图' })).not.toBeInTheDocument()
    expect(harness!.router.currentRoute.value.fullPath).toBe('/')
    expect(harness!.fetch.writes).toHaveLength(0)
  })

  it('显式退出清除账号、草稿、列表和旧恢复引用并回到登录页', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/auth/session', () => ({ body: authBody(ACCOUNT_A, 'session-a1') }))
        fetch.route('GET', '/api/projects', () => ({
          body: { items: [summary(PROJECT_A, '项目甲')], next_cursor: null },
        }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: activeView() }))
        fetch.route('POST', '/api/auth/logout', () => ({ status: 204 }))
      },
    })
    await ensureSession(true)
    await openProjectA()
    await typeDraft('退出前未发送草稿')
    const keys = storageKeys()
    harness.storage.local.setItem(keys.request, 'request-a')
    harness.storage.session.setItem(keys.pending, JSON.stringify({ projectId: PROJECT_A }))

    await harness!.user.click(screen.getByRole('button', { name: '退出登录' }))

    await waitFor(() => expect(harness!.router.currentRoute.value.name).toBe('login'))
    expect(currentAccount.value).toBeNull()
    expect(sessionEstablished.value).toBe(false)
    expect(harness.storage.local.getItem(keys.request)).toBeNull()
    expect(harness.storage.session.getItem(keys.pending)).toBeNull()
    expect(screen.queryByRole('heading', { name: '项目甲' })).not.toBeInTheDocument()
    expect(harness!.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
    expect(harness!.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
    expect(harness!.fetch.writes.filter((request) => request.path !== '/api/auth/logout')).toHaveLength(0)
  })

  it('403 和供应商 503 保持会话，只有 401 才清空账号与私有存储', async () => {
    currentAccount.value = ACCOUNT_A
    sessionEstablished.value = true
    harness = await mountWorkspace({
      config: emptyConfig(),
      setup(fetch) {
        fetch.route('GET', '/api/auth/session', () => ({ body: authBody(ACCOUNT_A, 'session-a1') }))
        fetch.sequence('GET', '/api/projects', [
          { body: { items: [], next_cursor: null } },
          { status: 403, body: { error: { code: 'PERMISSION_DENIED', message: '无权限' } } },
          { status: 503, body: { error: { code: 'SERVICE_UNAVAILABLE', message: '供应商暂不可用' } } },
          { status: 401, body: { error: { code: 'AUTH_REQUIRED', message: '请先登录' } } },
        ])
      },
    })
    await waitFor(() => expect(harness!.fetch.count('GET', '/api/projects')).toBe(1))
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
    expect(harness!.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })

  it('切换账号后迟到的发送失败不会留下旧错误或自动重试', async () => {
    let sessionAccount = ACCOUNT_A
    let sessionId = 'session-a1'
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/auth/session', () => ({ body: authBody(sessionAccount, sessionId) }))
        fetch.route('GET', '/api/projects', () => ({
          body: {
            items: sessionAccount.id === ACCOUNT_A.id
              ? [summary(PROJECT_A, '项目甲')]
              : [summary(PROJECT_B, '项目乙')],
            next_cursor: null,
          },
        }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: activeView() }))
        fetch.route('POST', MESSAGES_A, () => ({
          status: 409,
          body: { error: { code: 'REVISION_CONFLICT', message: '项目已更新，请重新读取后再发送。' } },
          delayMs: 160,
        }))
      },
    })
    await ensureSession(true)
    await openProjectA()
    await typeDraft('这是一条旧账号的修改')
    await harness!.user.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(harness!.fetch.count('POST', MESSAGES_A)).toBe(1))

    sessionAccount = ACCOUNT_B
    sessionId = 'session-b1'
    await ensureSession(true)
    await new Promise((resolve) => setTimeout(resolve, 220))

    expect(currentAccount.value?.id).toBe(ACCOUNT_B.id)
    expect(harness!.router.currentRoute.value.fullPath).toBe('/')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText('项目已更新，请重新读取后再发送。')).not.toBeInTheDocument()
    expect(harness!.fetch.count('POST', MESSAGES_A)).toBe(1)
    expect(harness!.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })
})
