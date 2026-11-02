import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/vue'
import type { AccountView } from '@/api/types'
import type { WorkflowVersion, WorkflowView } from '@/api/workflow'
import {
  clearSession,
  ensureSession,
  installSessionLifecycle,
  sessionEstablished,
  sessionInvalidated,
} from '@/auth/session'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  PROJECT_B,
  configuredConfig,
  sampleAsset,
  sampleGeneration,
  sampleProject,
} from './support/fixtures'

let harness: WorkspaceHarness | undefined
let cleanupLifecycle: (() => void) | undefined
let originalBroadcastChannel: typeof window.BroadcastChannel | undefined

const ACCOUNT_A: AccountView = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  email: 'a@example.com',
  is_admin: false,
  status: 'active',
  created_at: '2026-10-01T00:00:00.000Z',
  last_login_at: null,
}

class FakeBroadcastChannel {
  static all: FakeBroadcastChannel[] = []
  name: string
  onmessage: ((event: MessageEvent) => void) | null = null
  constructor(name: string) {
    this.name = name
    FakeBroadcastChannel.all.push(this)
  }
  postMessage(data: unknown): void {
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
  Object.defineProperty(window, 'BroadcastChannel', {
    configurable: true,
    writable: true,
    value: FakeBroadcastChannel,
  })
}

afterEach(() => {
  cleanupLifecycle?.()
  cleanupLifecycle = undefined
  clearSession()
  sessionInvalidated.value = false
  sessionEstablished.value = false
  harness?.dispose()
  harness = undefined
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

const RUN_CURRENT = 'run-current'
const RUN_OLD = 'run-old'

function version(overrides: Partial<WorkflowVersion>): WorkflowVersion {
  return {
    run_id: RUN_CURRENT,
    sequence_no: 2,
    requirement_revision: 3,
    status: 'published',
    generation_job_id: 'job-current',
    asset_id: 'asset-current',
    filename: 'current.png',
    caption: '当前主图',
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
    intent_route: null,
    intent_action: null,
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
      caption: '当前主图',
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
        caption: '历史主图',
        published_at: '2026-10-20T03:00:00.000Z',
        prompt: { positive_prompt: '旧版本的已批准提示词', negative_prompt: '' },
      }),
    ],
    updated_at: '2026-10-21T06:02:00.000Z',
    ...overrides,
  }
}

function projectDetail(projectId = PROJECT_A, title = '春季托特包主图', withGeneration = true) {
  const resultAsset = sampleAsset({
    id: 'asset-current',
    kind: 'generated',
    width: 1536,
    height: 1024,
  })
  return {
    project: sampleProject({
      id: projectId,
      title,
      revision: 3,
      confirmed_revision: 3,
      brief_status: 'confirmed',
    }),
    messages: [],
    generations: withGeneration
      ? [
          sampleGeneration({
            id: 'generation-current',
            project_id: projectId,
            version_no: 2,
            confirmed_revision: 3,
            result_asset_id: 'asset-current',
            result_asset: resultAsset,
          }),
        ]
      : [],
  }
}

function mountChat(projectId = PROJECT_A) {
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/projects/' + projectId + '/chat',
    routes: [
      { method: 'GET', url: '/api/projects/' + projectId, responses: [{ body: projectDetail(projectId) }] },
      { method: 'GET', url: '/api/projects/' + projectId + '/workflow', responses: [{ body: completedView() }] },
    ],
  })
}

function mountResults(query = '', view: WorkflowView = completedView()) {
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/projects/' + PROJECT_A + '/results' + query,
    routes: [
      { method: 'GET', url: '/api/projects/' + PROJECT_A, responses: [{ body: projectDetail() }] },
      { method: 'GET', url: '/api/projects/' + PROJECT_A + '/workflow', responses: [{ body: view }] },
    ],
  })
}

  it.each([
    ['尚未发布', { status: 'candidate', can_download: true }],
    ['已撤销或已过期', { status: 'published', can_download: false }],
  ])('%s 的显式历史版本只显示安全状态，不展示图片', async (_label, overrides) => {
    const unavailable = completedView({
      versions: [version({ run_id: 'run-unavailable', asset_id: 'asset-unavailable', ...overrides })],
    })
    harness = await mountResults('?version=run-unavailable', unavailable)
    expect(await screen.findByText('结果不可用')).toBeVisible()
    expect(harness.container.querySelector('.results-image')).toBeNull()
    expect(harness.fetch.writes).toHaveLength(0)
  })

describe('T15 共享工作台外壳与结果路由', () => {
  it('根路由是唯一的空聊天工作台，先发送才创建项目', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    harness.fetch.json('POST', '/api/projects', projectDetail(PROJECT_A).project, 201)
    const workbench = await screen.findByRole('region', { name: '需求工作台' })
    expect(workbench).toBeVisible()
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toBeVisible()
    expect(screen.queryByRole('navigation', { name: '工作阶段' })).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(0)
    expect(window.location.pathname).toBe('/')
    expect(window.location.search).toBe('')
  })

  it('项目聊天与结果共用一个外壳，结果页没有输入框或反馈表单', async () => {
    harness = await mountChat()
    expect(await screen.findByRole('region', { name: '需求工作台' })).toBeVisible()
    expect(await screen.findByRole('heading', { name: '春季托特包主图' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toBeVisible()

    await harness.router.push({ name: 'project-results', params: { projectId: PROJECT_A } })
    expect(await screen.findByRole('heading', { name: '春季托特包主图', level: 1 })).toBeVisible()
    expect(screen.queryByRole('region', { name: '需求工作台' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: '描述商品与图片需求' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '提交反馈' })).not.toBeInTheDocument()
    expect(harness.router.currentRoute.value.name).toBe('project-results')
  })

  it('当前结果展示真实版本、尺寸、时间与原图下载，不显示旧版本提示词', async () => {
    harness = await mountResults()
    const image = await screen.findByRole('img', { name: '当前主图' })
    expect(image).toHaveAttribute('src', '/api/assets/asset-current')
    expect(screen.getByText('当前版本 · 版本 2 · 需求 v3')).toBeVisible()
    expect(screen.getByText('1536 × 1024')).toBeVisible()
    expect(screen.getByText(/当前版本的已批准提示词/)).toBeInTheDocument()
    expect(screen.queryByText(/旧版本的已批准提示词/)).not.toBeInTheDocument()
    const download = screen.getByRole('link', { name: '下载原图' })
    expect(download).toHaveAttribute('href', '/api/assets/asset-current?download=true')
    expect(download).toHaveAttribute('download')
  })

  it('显式历史版本展示该版本图片和该版本提示词，且不产生写请求', async () => {
    harness = await mountResults('?version=' + RUN_OLD)
    const image = await screen.findByRole('img', { name: '历史主图' })
    expect(image).toHaveAttribute('src', '/api/assets/asset-old')
    expect(screen.getByText('历史版本 · 版本 1 · 需求 v2')).toBeVisible()
    expect(screen.getByText(/旧版本的已批准提示词/)).toBeInTheDocument()
    expect(screen.queryByText(/当前版本的已批准提示词/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '下载原图' })).toHaveAttribute(
      'href',
      '/api/assets/asset-old?download=true',
    )
    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('GET', /\/workflow\/actions/)).toBe(0)
  })

  it('不存在或不可下载的版本显示安全状态，绝不回退到当前图片', async () => {
    harness = await mountResults('?version=run-missing')
    expect(await screen.findByText('结果不可用')).toBeVisible()
    expect(screen.getByText(/该版本不存在、尚未发布、已撤销或无权访问/)).toBeVisible()
    expect(harness.container.querySelector('.results-image')).toBeNull()
    expect(screen.queryByText('当前版本的已批准提示词')).not.toBeInTheDocument()
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('返回对话修改只做导航和聚焦，不提交消息、不确认、不调用模型', async () => {
    harness = await mountResults()
    await screen.findByRole('img', { name: '当前主图' })
    const writesBefore = harness.fetch.writes.length
    await harness.user.click(screen.getAllByRole('button', { name: '返回对话修改' })[0])
    await harness.waitFor(() => {
      expect(harness!.router.currentRoute.value.name).toBe('project-chat')
      expect(harness!.router.currentRoute.value.hash).toBe('#composer')
    })
    const composer = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    await harness.waitFor(() => expect(composer).toHaveFocus())
    expect(harness.fetch.writes.length).toBe(writesBefore)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(0)
    expect(harness.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })

  it('聊天与结果来回切换时保留同一项目草稿，URL 不携带需求正文', async () => {
    harness = await mountChat()
    const composer = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, '只属于当前项目的草稿')
    await harness.router.push({ name: 'project-results', params: { projectId: PROJECT_A } })
    await screen.findByRole('img', { name: '当前主图' })
    await harness.router.push({ name: 'project-chat', params: { projectId: PROJECT_A } })
    const restored = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    expect(restored).toHaveValue('只属于当前项目的草稿')
    expect(window.location.pathname).toBe('/projects/' + PROJECT_A + '/chat')
    expect(window.location.search).not.toContain(encodeURIComponent('只属于当前项目的草稿'))
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('切换账号生命周期后清理项目草稿，避免旧账号内容出现在新账号', async () => {
    clearSession()
    sessionInvalidated.value = false
    sessionEstablished.value = false
    installFakeBroadcastChannel()
    let sessionAccount = ACCOUNT_A
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/auth/session', () => ({
          body: {
            account: sessionAccount,
            session_id: 'session-' + sessionAccount.id.slice(0, 1),
            csrf_token: 'csrf-' + sessionAccount.id.slice(0, 1),
            expires_at: '2026-12-02T00:00:00.000Z',
          },
        }))
      },
      routes: [
        { method: 'GET', url: '/api/projects/' + PROJECT_A, responses: [{ body: projectDetail() }] },
        { method: 'GET', url: '/api/projects/' + PROJECT_A + '/workflow', responses: [{ body: completedView() }] },
      ],
    })
    cleanupLifecycle = installSessionLifecycle()
    await ensureSession(true)
    const composer = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, 'A 账号的私有草稿')

    sessionAccount = {
      ...ACCOUNT_A,
      id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      email: 'b@example.com',
    }
    const otherTab = new FakeBroadcastChannel('v3-session-lifecycle')
    otherTab.postMessage({ type: 'session-invalidated', nonce: 't15-switch' })

    await waitFor(() => expect(harness!.router.currentRoute.value.name).toBe('describe'))
    const cleared = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    expect((cleared as HTMLTextAreaElement).value).toBe('')
    expect(screen.queryByText('A 账号的私有草稿')).not.toBeInTheDocument()
    expect(harness.fetch.writes).toHaveLength(0)
  })
})
