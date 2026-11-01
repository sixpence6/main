import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { WorkflowVersion, WorkflowView } from '@/api/workflow'
import {
  currentDeliveryBlocked,
  currentPublishedVersion,
  nextStepForWorkflow,
  userStateOf,
} from '@/workflow/projection'
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
const FEEDBACK_PATH = WORKFLOW_PATH + '/actions/request_new_version'

const feedbackAction = {
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
    brief: {
      product_description: '米色托特包',
      purpose: '电商主图',
      size: '3840x2160',
    },
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
    brief: {
      product_description: '米色亚麻托特包',
      purpose: '电商主图',
      size: '3840x2160',
    },
    prompt: { positive_prompt: '米色亚麻托特包，浅灰背景，居中构图' },
    published_at: '2026-11-01T00:00:00.000Z',
    created_at: '2026-11-01T00:00:00.000Z',
    ...overrides,
  })
}

function workflowView(overrides: Partial<WorkflowView> = {}): WorkflowView {
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
    available_actions: [feedbackAction],
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

function projectDetail(overrides: Parameters<typeof sampleProject>[0] = {}) {
  return {
    project: sampleProject({ id: PROJECT_A, revision: 3, prompt_revision: 3, confirmed_revision: 3, ...overrides }),
    messages: [],
    generations: [],
  }
}

function mountU3(options: {
  view: WorkflowView
  project?: Parameters<typeof projectDetail>[0]
  viewportWidth?: number
  setup?: (fetch: import('./support/fetch').FetchController) => void
}) {
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth: options.viewportWidth ?? 1440,
    url: '/?project=' + PROJECT_A,
    routes: [
      {
        method: 'GET',
        url: DETAIL_PATH,
        responses: [{ body: projectDetail(options.project) }],
      },
      { method: 'GET', url: WORKFLOW_PATH, responses: [{ body: options.view }] },
    ],
    setup: options.setup,
  })
}

async function findPanel() {
  const panel = await screen.findByRole('region', { name: '准备交付' })
  expect(panel.classList.contains('workflow-panel')).toBe(true)
  return panel
}

describe('T23 U3 当前交付、历史预览与反馈新版本', () => {
  it('只有当前版本的合法发布交付进入 U3，并给出真实下载入口', async () => {
    harness = await mountU3({ view: workflowView() })
    const panel = await findPanel()

    expect(within(panel).getByText('当前交付：bag-v3.png')).toBeVisible()
    expect(within(panel).getByText('当前第 3 版 · 尺寸 3840×2160')).toBeVisible()
    const image = within(panel).getByRole('img', { name: '第三版交付，已通过检查。' })
    expect(image).toHaveAttribute('src', '/api/assets/asset-current')
    expect(within(panel).getByRole('link', { name: '下载当前交付' })).toHaveAttribute(
      'href',
      '/api/assets/asset-current?download=true',
    )
    expect(within(panel).getByRole('heading', { name: '历史版本' })).toBeVisible()
    expect(within(panel).getByRole('button', { name: '第 3 版' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(panel).getByRole('button', { name: '第 1 版' })).toHaveAttribute('aria-pressed', 'false')
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('已发布但当前版本不可下载时不展示图片或下载，只说明真实阻断状态', async () => {
    const blocked = currentVersion({ can_download: false, asset_id: null, filename: null })
    harness = await mountU3({
      view: workflowView({ versions: [blocked, version()] }),
    })
    const panel = await findPanel()

    expect(within(panel).getByText('交付：bag-v3.png')).toBeVisible()
    const blockedAlert = panel.querySelector('.workflow-delivery-blocked')
    expect(blockedAlert?.textContent).toContain('当前交付不可用')
    expect(within(panel).queryByRole('link', { name: '下载当前交付' })).not.toBeInTheDocument()
    expect(panel.querySelector('.workflow-delivery-image')).toBeNull()
    expect(panel.querySelector('.workflow-delivery-blocked')).not.toBeNull()
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('切换历史版本只改变本地预览，不修改需求、确认或调用模型', async () => {
    harness = await mountU3({ view: workflowView() })
    const panel = await findPanel()
    const oldTab = within(panel).getByRole('button', { name: '第 1 版' })

    oldTab.focus()
    expect(oldTab).toHaveFocus()
    await harness.user.keyboard('{Enter}')
    expect(oldTab).toHaveAttribute('aria-pressed', 'true')
    expect(within(panel).getByText('需求：米色托特包 · 电商主图')).toBeVisible()
    expect(within(panel).getByText('提示词：米色托特包，正面居中，简洁棚拍')).toBeVisible()
    expect(within(panel).getByRole('link', { name: '下载第 1 版' })).toHaveAttribute(
      'href',
      '/api/assets/asset-old?download=true',
    )
    expect(within(panel).getByText(/当前正在查看第 1 版历史预览/)).toBeVisible()
    expect(within(panel).getByText('当前交付：bag-v3.png')).toBeVisible()
    expect(harness.fetch.count('POST')).toBe(0)
    expect(harness.fetch.writes).toHaveLength(0)

    await harness.user.click(within(panel).getByRole('button', { name: '第 3 版' }))
    expect(within(panel).getByRole('button', { name: '第 3 版' })).toHaveAttribute('aria-pressed', 'true')
    expect(panel.querySelector('.workflow-version-current')).not.toBeNull()
  })

  it('反馈明确基于当前版本并生成新版本，成功后回到 U1 整理需求', async () => {
    const newView = workflowView({
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
    })
    harness = await mountU3({ view: workflowView() })
    harness.fetch.json('POST', FEEDBACK_PATH, newView)
    const panel = await findPanel()

    const feedbackHeading = await within(panel).findByRole('heading', { name: '基于当前交付继续调整' })
    expect(feedbackHeading).toBeVisible()
    const feedbackSection = feedbackHeading.closest('.workflow-feedback') as HTMLElement
    expect(feedbackSection).not.toBeNull()
    expect(feedbackSection.textContent).toContain('当前交付为第 3 版（bag-v3.png），尺寸 3840×2160')
    expect(feedbackSection.textContent).toContain('不承诺局部像素不变或只修改指定区域')
    const textarea = within(panel).getByRole('textbox', { name: '说明本次修改要求' })
    textarea.focus()
    expect(textarea).toHaveFocus()
    await harness.user.type(textarea, '把背景换成浅灰色')
    await harness.user.click(within(panel).getByRole('button', { name: '基于反馈生成新版本' }))

    expect(harness.fetch.last('POST', FEEDBACK_PATH)?.body).toEqual({
      expected_revision: 3,
      text: '把背景换成浅灰色',
      payload: {},
    })
    expect(await screen.findByRole('heading', { name: '整理需求' })).toBeVisible()
    expect(screen.queryByText(/当前交付为第 3 版/)).not.toBeInTheDocument()
    expect(harness.fetch.count('POST')).toBe(1)
  })

  it('320 像素窄屏保留历史切换与反馈操作，键盘可达且不额外写请求', async () => {
    harness = await mountU3({ view: workflowView(), viewportWidth: 320 })
    const panel = await findPanel()
    expect(window.innerWidth).toBe(320)
    const tabs = within(panel).getByRole('navigation', { name: '切换历史版本' })
    expect(tabs.querySelectorAll('button')).toHaveLength(2)
    const oldTab = within(tabs).getByRole('button', { name: '第 1 版' })
    oldTab.focus()
    await harness.user.keyboard(' ')
    expect(oldTab).toHaveAttribute('aria-pressed', 'true')
    expect(within(panel).getByRole('textbox', { name: '说明本次修改要求' })).toBeVisible()
    expect(within(panel).getByRole('button', { name: '基于反馈生成新版本' })).toBeVisible()
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('投影函数只认定与当前版本、当前交付一致的已发布版本', () => {
    const project = sampleProject({ id: PROJECT_A, revision: 3 })
    const legal = workflowView()
    expect(currentPublishedVersion(project, legal)?.run_id).toBe('run-current')
    expect(currentDeliveryBlocked(project, legal)).toBe(false)
    expect(userStateOf(project, legal)).toBe('u3')
    expect(nextStepForWorkflow(project, legal).title).toBe('当前交付已发布')

    const blocked = workflowView({ versions: [currentVersion({ can_download: false, asset_id: null }), version()] })
    expect(currentPublishedVersion(project, blocked)).toBeNull()
    expect(currentDeliveryBlocked(project, blocked)).toBe(true)
    expect(userStateOf(project, blocked)).toBe('u1')
    expect(nextStepForWorkflow(project, blocked).title).toBe('当前交付不可用')

    const behind = workflowView({ project_revision: 2 })
    expect(currentPublishedVersion(project, behind)).toBeNull()
    expect(userStateOf(project, behind)).toBe('u1')
  })
})
