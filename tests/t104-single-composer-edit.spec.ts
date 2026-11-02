import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { WorkflowVersion, WorkflowView } from '@/api/workflow'
import type { ProjectDetailResponse } from '@/api/types'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, configuredConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const DETAIL_PATH = '/api/projects/' + PROJECT_A
const WORKFLOW_PATH = DETAIL_PATH + '/workflow'
const NEW_VERSION_PATH = WORKFLOW_PATH + '/actions/request_new_version'

const requestNewVersion = {
  id: 'request_new_version',
  label: '基于反馈生成新版本',
  kind: 'text' as const,
  requires_text: true,
  text_label: '说明本次修改要求',
  requires_confirmation: false,
}

function version(overrides: Partial<WorkflowVersion> = {}): WorkflowVersion {
  return {
    run_id: 'run-old',
    sequence_no: 1,
    requirement_revision: 1,
    status: 'published',
    generation_job_id: 'job-old',
    asset_id: 'asset-old',
    filename: 'bag-v1.png',
    caption: '第一版交付。',
    published_at: '2026-10-30T00:00:00.000Z',
    brief: { product_description: '米色托特包', purpose: '电商主图', size: '3840x2160' },
    prompt: { positive_prompt: '米色托特包，正面居中，简洁棚拍' },
    quality: { decision: 'pass' },
    can_download: true,
    created_at: '2026-10-30T00:00:00.000Z',
    ...overrides,
  }
}

function currentVersion(overrides: Partial<WorkflowVersion> = {}): WorkflowVersion {
  return version({
    run_id: 'run-current',
    sequence_no: 2,
    requirement_revision: 3,
    generation_job_id: 'job-current',
    asset_id: 'asset-current',
    filename: 'bag-v3.png',
    caption: '第三版交付，已通过检查。',
    brief: { product_description: '米色亚麻托特包', purpose: '电商主图', size: '3840x2160' },
    prompt: { positive_prompt: '米色亚麻托特包，浅灰背景，居中构图' },
    published_at: '2026-11-01T00:00:00.000Z',
    created_at: '2026-11-01T00:00:00.000Z',
    ...overrides,
  })
}

function completedView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-current',
    stage: 'S10',
    status: 'completed',
    project_revision: 3,
    requirement_revision: 3,
    intent_route: 'image_request',
    intent_action: 'new_request',
    issue: {
      code: 'DELIVERY_PUBLISHED',
      message: '交付已发布。',
      template_id: null,
      params: {},
    },
    artifacts: [],
    approvals: [],
    available_actions: [requestNewVersion],
    legacy: [],
    generation_job: null,
    delivery: {
      id: 'delivery-current',
      status: 'published',
      asset_id: 'asset-current',
      filename: 'bag-v3.png',
      caption: '第三版交付，已通过检查。',
      published_at: '2026-11-01T00:00:00.000Z',
      created_at: '2026-11-01T00:00:00.000Z',
    },
    versions: [currentVersion(), version()],
    updated_at: '2026-11-01T00:00:00.000Z',
    ...overrides,
  }
}

function approvedView(): WorkflowView {
  const base = completedView()
  return completedView({
    versions: [],
    delivery: { ...base.delivery!, status: 'approved', published_at: null },
  })
}

function newRevisionView(): WorkflowView {
  return completedView({
    run_id: 'run-new',
    stage: 'S03',
    status: 'active',
    project_revision: 4,
    requirement_revision: 4,
    issue: null,
    delivery: null,
    versions: [version()],
    available_actions: [
      {
        id: 'compose_brief',
        label: '整理需求卡',
        kind: 'button',
        requires_text: false,
        text_label: null,
        requires_confirmation: false,
      },
    ],
    updated_at: '2026-11-02T00:00:00.000Z',
  })
}

function detail(revision = 3): ProjectDetailResponse {
  return {
    project: sampleProject({
      id: PROJECT_A,
      revision,
      prompt_revision: revision,
      confirmed_revision: revision,
    }),
    messages: [],
    generations: [],
  }
}

function mountChat(options: {
  view?: WorkflowView
  details?: ProjectDetailResponse[]
  setup?: (fetch: import('./support/fetch').FetchController) => void
} = {}) {
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/projects/' + PROJECT_A + '/chat',
    routes: [
      {
        method: 'GET',
        url: DETAIL_PATH,
        responses: (options.details ?? [detail()]).map((body) => ({ body })),
      },
      {
        method: 'GET',
        url: WORKFLOW_PATH,
        responses: [{ body: options.view ?? completedView() }],
      },
    ],
    setup: options.setup,
  })
}

async function findPanel() {
  const panel = await screen.findByRole('region', { name: '准备交付' })
  expect(panel.classList.contains('workflow-panel')).toBe(true)
  return panel
}

describe('T18 单一输入框修改与新版本', () => {
  it('当前交付后的发送调用服务端新版本动作，不绕过修订、复审和确认', async () => {
    harness = await mountChat({ details: [detail(3), detail(4)] })
    harness.fetch.json('POST', NEW_VERSION_PATH, newRevisionView())
    const panel = await findPanel()

    expect(within(panel).queryByRole('heading', { name: '基于当前交付继续调整' })).toBeNull()
    expect(within(panel).queryByRole('button', { name: '基于反馈生成新版本' })).toBeNull()
    expect(screen.queryByText('说明本次修改要求')).not.toBeInTheDocument()
    expect(screen.getByText(/当前交付为第 3 版（bag-v3.png），尺寸 3840×2160/)).toBeVisible()
    expect(screen.getByText(/发送后会基于当前版本创建新的需求版本/)).toBeVisible()

    const composer = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, '把背景换成浅灰色')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    expect(harness.fetch.last('POST', NEW_VERSION_PATH)?.body).toEqual({
      expected_revision: 3,
      text: '把背景换成浅灰色',
      payload: {},
    })
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(0)
    expect(harness.fetch.count('POST', NEW_VERSION_PATH)).toBe(1)
    expect(await screen.findByRole('heading', { name: '整理需求' })).toBeVisible()
    expect(screen.queryByText(/当前交付为第 3 版/)).not.toBeInTheDocument()
    expect(composer).toHaveValue('')
    expect(harness.fetch.count('GET', DETAIL_PATH)).toBeGreaterThanOrEqual(2)
  })

  it('提交失败时保留原输入，不伪造新版本或成功状态', async () => {
    harness = await mountChat()
    harness.fetch.json(
      'POST',
      NEW_VERSION_PATH,
      { error: { code: 'TECHNICAL_FAILURE', message: '服务异常，修改未提交。' } },
      503,
    )
    await findPanel()
    const composer = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, '把背景换成浅灰色')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    expect(
      await screen.findByText('服务异常，修改未提交。', { selector: '.composer-error' }),
    ).toBeVisible()
    expect(composer).toHaveValue('把背景换成浅灰色')
    expect(screen.getByText(/当前交付为第 3 版/)).toBeVisible()
    expect(harness.fetch.count('POST', NEW_VERSION_PATH)).toBe(1)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(0)
    expect(harness.fetch.count('GET', DETAIL_PATH)).toBe(1)
  })

  it('历史预览不会改变当前编辑基线，发送仍绑定当前交付版本', async () => {
    harness = await mountChat()
    harness.fetch.json('POST', NEW_VERSION_PATH, newRevisionView())
    const panel = await findPanel()
    await harness.user.click(within(panel).getByRole('button', { name: '第 1 版' }))
    expect(within(panel).getByText(/当前正在查看第 1 版历史预览/)).toBeVisible()
    expect(screen.getByText(/当前交付为第 3 版（bag-v3.png）/)).toBeVisible()

    const composer = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, '只改背景')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))
    expect(harness.fetch.last('POST', NEW_VERSION_PATH)?.body).toEqual({
      expected_revision: 3,
      text: '只改背景',
      payload: {},
    })
  })

  it('提交进行中重复操作只产生一次新版本请求', async () => {
    harness = await mountChat({
      setup: (fetch) => {
        fetch.route('POST', NEW_VERSION_PATH, async () => {
          await new Promise((resolve) => setTimeout(resolve, 120))
          return { body: newRevisionView() }
        })
      },
    })
    await findPanel()
    const composer = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, '把背景换成浅灰色')
    const send = screen.getByRole('button', { name: '发送' })
    await harness.user.click(send)
    await harness.user.click(send)
    await screen.findByRole('heading', { name: '整理需求' })
    expect(harness.fetch.count('POST', NEW_VERSION_PATH)).toBe(1)
  })

  it('已批准未发布的当前交付同样从唯一输入框创建新版本', async () => {
    harness = await mountChat({ view: approvedView(), details: [detail(3), detail(4)] })
    harness.fetch.json('POST', NEW_VERSION_PATH, newRevisionView())
    await findPanel()

    expect(screen.getByText(/当前交付为第 3 版（bag-v3.png），尺寸 尺寸未记录/)).toBeVisible()
    const composer = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, '补充礼盒场景')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    expect(harness.fetch.last('POST', NEW_VERSION_PATH)?.body).toEqual({
      expected_revision: 3,
      text: '补充礼盒场景',
      payload: {},
    })
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(0)
    expect(await screen.findByRole('heading', { name: '整理需求' })).toBeVisible()
  })

  it('结果页不提供任何编辑入口，返回对话修改只导航并聚焦原输入框', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/results',
      routes: [
        { method: 'GET', url: DETAIL_PATH, responses: [{ body: detail() }] },
        { method: 'GET', url: WORKFLOW_PATH, responses: [{ body: completedView() }] },
      ],
    })
    await screen.findByRole('img', { name: '第三版交付，已通过检查。' })
    expect(screen.queryByRole('textbox', { name: '描述商品与图片需求' })).not.toBeInTheDocument()
    expect(harness.container.querySelector('.workflow-feedback')).toBeNull()
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
})
