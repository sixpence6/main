import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { WorkflowArtifact, WorkflowUsage, WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { FetchController } from './support/fetch'
import {
  PROJECT_A,
  configuredConfig,
  sampleAsset,
  sampleProject,
} from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const DIGEST = 'd'.repeat(64)
const ACTION_PATH = '/api/projects/' + PROJECT_A + '/workflow/actions/'
const CONFIRM_PATH = ACTION_PATH + 'confirm_requirements'
const WORKFLOW_PATH = '/api/projects/' + PROJECT_A + '/workflow'
const WORKFLOW_GET = new RegExp('/workflow$')
const GENERATION_WRITE = new RegExp('/generations(?:/|$)')

const confirmAction = {
  id: 'confirm_requirements',
  label: '确认需求',
  kind: 'confirm' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: true,
}

const verifyAction = {
  id: 'verify_generation',
  label: '核实原任务',
  kind: 'button' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: false,
}

const recoverAction = {
  id: 'recover_generation',
  label: '找回原结果',
  kind: 'button' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: false,
}

const reviseAction = {
  id: 'revise_requirements',
  label: '修改需求',
  kind: 'text' as const,
  requires_text: true,
  text_label: '需求修改说明',
  requires_confirmation: false,
}

function usage(overrides: Partial<WorkflowUsage> = {}): WorkflowUsage {
  return {
    daily_limit: 20,
    used: 1,
    remaining: 19,
    window_started_at: '2026-11-01T00:00:00.000Z',
    window_expires_at: '2026-11-02T00:00:00.000Z',
    personal_active: 0,
    personal_limit: 1,
    instance_active: 0,
    instance_limit: 2,
    project_active: 0,
    project_limit: 1,
    ...overrides,
  }
}

function finalBrief(): WorkflowArtifact {
  return {
    id: 'artifact-final-t74',
    kind: 'final_brief',
    revision_no: 2,
    status: 'approved',
    digest: DIGEST,
    payload: {
      product_description: '米色亚麻托特包',
      purpose: '电商主图',
      composition: '居中构图，浅米色背景',
      size: '3840x2160',
    },
    created_at: '2026-10-31T00:01:00.000Z',
  }
}

function promptPlan(status: 'approved' | 'candidate' = 'approved'): WorkflowArtifact {
  return {
    id: status === 'approved' ? 'artifact-prompt-approved' : 'artifact-prompt-candidate',
    kind: 'prompt_plan',
    revision_no: 2,
    status,
    digest: 'digest-prompt-' + status,
    payload: {
      positive_prompt: status === 'approved' ? '实际已批准的正向提示词' : '未批准提示词不得显示',
      negative_prompt: '水印，变形',
      size: '3840x2160',
    },
    created_at: '2026-10-31T00:02:00.000Z',
  }
}

function workflowView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-t74',
    stage: 'S06',
    status: 'active',
    project_revision: 3,
    requirement_revision: 3,
    intent_route: 'image_request',
    intent_action: 'new_request',
    issue: null,
    artifacts: [finalBrief()],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: null,
    updated_at: '2026-11-01T00:00:00.000Z',
    ...overrides,
  }
}

function acceptedView(): WorkflowView {
  return workflowView({
    stage: 'S07',
    status: 'active',
    available_actions: [],
    usage: usage(),
    intent: {
      id: 'intent-t74',
      request_id: '55555555-5555-4555-8555-555555555555',
      status: 'accepted',
      artifact_digest: DIGEST,
      requirement_revision: 3,
      created_at: '2026-11-01T00:03:00.000Z',
    },
  })
}

function activeGenerationView(stage: 'S08' | 'S09' = 'S08'): WorkflowView {
  return workflowView({
    stage,
    status: 'active',
    artifacts: [finalBrief(), promptPlan('approved')],
    usage: usage({ personal_active: 1, instance_active: 1, project_active: 1 }),
    intent: {
      id: 'intent-t74',
      request_id: '55555555-5555-4555-8555-555555555555',
      status: 'executing',
      artifact_digest: DIGEST,
      requirement_revision: 3,
      created_at: '2026-11-01T00:03:00.000Z',
    },
    generation_job: {
      id: 'job-t74',
      status: stage === 'S08' ? 'running' : 'succeeded',
      request_id: '55555555-5555-4555-8555-555555555555',
      submit_attempted: true,
      result_asset_id: null,
      error_code: null,
      error_message: null,
      created_at: '2026-11-01T00:03:10.000Z',
      updated_at: '2026-11-01T00:04:00.000Z',
    },
  })
}

function blockedView(): WorkflowView {
  return workflowView({
    stage: 'S08',
    status: 'blocked',
    issue: {
      code: 'GENERATION_UNKNOWN',
      message: '远端生成结果未知，任务与一次计数已保留。',
      template_id: null,
      params: {},
    },
    usage: usage({ personal_active: 1, instance_active: 1, project_active: 1 }),
    intent: {
      id: 'intent-t74',
      request_id: '55555555-5555-4555-8555-555555555555',
      status: 'executing',
      artifact_digest: DIGEST,
      requirement_revision: 3,
      created_at: '2026-11-01T00:03:00.000Z',
    },
    available_actions: [verifyAction, recoverAction],
    generation_job: {
      id: 'job-t74',
      status: 'unknown',
      request_id: '55555555-5555-4555-8555-555555555555',
      submit_attempted: true,
      result_asset_id: null,
      error_code: 'VISUAL_OUTCOME_UNKNOWN',
      error_message: '远端生成结果未知',
      created_at: '2026-11-01T00:03:10.000Z',
      updated_at: '2026-11-01T00:04:00.000Z',
    },
  })
}

function projectDetail(overrides: Parameters<typeof sampleProject>[0] = {}) {
  return {
    project: sampleProject({
      id: PROJECT_A,
      revision: 3,
      prompt_revision: 3,
      confirmed_revision: 3,
      brief_status: 'draft',
      ...overrides,
    }),
    messages: [],
    generations: [],
  }
}

function mountU2(options: {
  view: WorkflowView
  project?: Parameters<typeof projectDetail>[0]
  fakeClock?: boolean
  viewportWidth?: number
  setup?: (fetch: FetchController) => void
}) {
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth: options.viewportWidth ?? 1440,
    fakeClock: options.fakeClock,
    url: '/?project=' + PROJECT_A,
    routes: [
      {
        method: 'GET',
        url: '/api/projects/' + PROJECT_A,
        responses: [{ body: projectDetail(options.project) }],
      },
      {
        method: 'GET',
        url: '/api/projects/' + PROJECT_A + '/workflow',
        responses: [{ body: options.view }],
      },
    ],
    setup: options.setup,
  })
}

async function findPanel(name: string) {
  const panel = await screen.findByRole('region', { name })
  expect(panel.classList.contains('workflow-panel')).toBe(true)
  return panel
}

describe('T22 U2 原位授权、执行状态与安全恢复', () => {
  it('核对最终需求卡后原地确认一次，不跳转旧确认页也不调用旧生成接口', async () => {
    const accepted = acceptedView()
    harness = await mountU2({
      view: workflowView({
        artifacts: [finalBrief()],
        available_actions: [confirmAction],
        usage: usage(),
      }),
      setup(fetch) {
        fetch.route('POST', ACTION_PATH + 'confirm_requirements', () => ({ body: accepted }))
      },
    })

    const panel = await findPanel('审查需求卡')
    expect(within(panel).getByText(/今日已受理/)).toHaveTextContent('今日已受理 1 / 20 次')
    expect(within(panel).getAllByRole('button', { name: '确认以上需求并生成图片' })).toHaveLength(1)
    const button = within(panel).getByRole('button', { name: '确认以上需求并生成图片' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', '请先勾选确认。')
    const checkbox = within(panel).getByRole('checkbox')
    await harness.user.click(checkbox)
    expect(button).toBeEnabled()
    await harness.user.click(button)
    await harness.waitFor(() => {
      expect(harness?.fetch.last('POST', CONFIRM_PATH)).toBeTruthy()
    })

    const request = harness.fetch.last('POST', CONFIRM_PATH)
    expect(request?.body).toMatchObject({
      expected_revision: 3,
      text: null,
      payload: {
        artifact_id: 'artifact-final-t74',
        artifact_digest: DIGEST,
        requirement_revision: 3,
        request_id: expect.any(String),
      },
    })
    expect(harness.fetch.count('POST', GENERATION_WRITE)).toBe(0)
    expect(harness.fetch.count('POST')).toBe(1)
    expect(harness.router.currentRoute.value.name).toBe('describe')
    expect(harness.router.currentRoute.value.query.project).toBe(PROJECT_A)
    expect(await screen.findByText(/本次生成已受理/)).toBeVisible()
    expect(await screen.findByRole('region', { name: '执行进度' })).toBeVisible()
    expect(within(panel).queryByRole('button', { name: '确认以上需求并生成图片' })).not.toBeInTheDocument()
  })

  it('执行进度按服务端状态展示四步，不显示虚构百分比，已批准提示词默认折叠', async () => {
    harness = await mountU2({ view: activeGenerationView('S08') })
    const panel = await findPanel('生成图片')
    const execution = within(panel).getByRole('region', { name: '执行进度' })
    expect(within(execution).getByText(/不显示虚构百分比/)).toBeVisible()
    expect(execution.textContent).not.toMatch(/%|百分之/)

    const steps = within(execution).getAllByRole('listitem')
    expect(steps).toHaveLength(4)
    expect(steps.map((step) => step.querySelector('strong')?.textContent)).toEqual([
      '编制提示词',
      '生成图片',
      '检查图片',
      '准备交付',
    ])
    expect(steps[0].className).toContain('execution-step-done')
    expect(steps[1].className).toContain('execution-step-current')
    expect(steps[2].className).toContain('execution-step-waiting')
    expect(steps[3].className).toContain('execution-step-waiting')
    expect(steps[1]).toHaveAttribute('aria-current', 'step')
    expect(steps[0]).not.toHaveAttribute('aria-current')
    expect(within(steps[0]).getByText('已完成')).toBeVisible()
    expect(within(steps[1]).getByText('进行中')).toBeVisible()
    expect(within(steps[2]).getByText('等待')).toBeVisible()

    const summary = within(panel).getByText('查看已批准的执行提示词（第 2 次）')
    const details = summary.closest('details')
    expect(details).not.toBeNull()
    expect(details?.open).toBe(false)
    await harness.user.click(summary)
    expect(details?.open).toBe(true)
    expect(await within(panel).findByText('positive_prompt: 实际已批准的正向提示词')).toBeVisible()
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('未批准的提示词不展示；需求漂移只引导修改需求并重新确认', async () => {
    harness = await mountU2({
      view: workflowView({
        stage: 'S07',
        status: 'blocked',
        issue: {
          code: 'PROMPT_REQUIRES_RECONFIRMATION',
          message: '提示词引入了尚未确认的卖点，未提交生图。',
          template_id: null,
          params: {},
        },
        artifacts: [finalBrief(), promptPlan('candidate')],
        available_actions: [reviseAction],
      }),
    })

    const panel = await findPanel('编制生图提示词')
    expect(within(panel).getByText('当前问题需要处理')).toBeVisible()
    expect(within(panel).getByText('下一步操作：修改需求')).toBeVisible()
    expect(within(panel).queryByText(/查看已批准的执行提示词/)).not.toBeInTheDocument()
    expect(within(panel).queryByText(/未批准提示词不得显示/)).not.toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: '修改需求' })).toBeVisible()
    expect(panel.querySelector('.execution-step-blocked')).not.toBeNull()
    expect(harness.fetch.count('POST')).toBe(0)
    expect(harness.fetch.count('POST', GENERATION_WRITE)).toBe(0)
  })

  it('阻塞未知任务只提供安全恢复说明和动作，等待期间不自动发请求', async () => {
    harness = await mountU2({ view: blockedView(), fakeClock: true })
    await harness.clock.advance(0)
    const panel = screen.getByRole('region', { name: '生成图片' })
    expect(panel.classList.contains('workflow-panel')).toBe(true)
    expect(within(panel).getByText(/安全恢复：以下核实、找回、继续或结束跟踪操作/)).toBeVisible()
    expect(
      within(panel).getByText('只核实原任务和远端状态，不创建新任务、不新增计数，也不会自动重发。'),
    ).toBeVisible()
    expect(
      within(panel).getByText('只在原任务已有可找回结果时读取原结果，不提交新生成、不新增计数。'),
    ).toBeVisible()
    const unknownStep = panel.querySelector('.execution-step-unknown')
    expect(unknownStep).not.toBeNull()
    expect(unknownStep?.textContent).toContain('结果未确认')
    expect(unknownStep?.textContent).toContain('系统不会自动重发')
    expect(within(panel).getByRole('button', { name: '核实原任务' })).toBeVisible()
    expect(within(panel).getByRole('button', { name: '找回原结果' })).toBeVisible()

    await harness.clock.advance(6000)
    expect(harness.fetch.count('GET', WORKFLOW_GET)).toBe(1)
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('后台执行只按节奏读取状态，遇到阻塞立即停止轮询且从不自动写请求', async () => {
    let index = 0
    harness = await mountU2({
      view: activeGenerationView('S08'),
      fakeClock: true,
      setup(fetch) {
        fetch.route('GET', WORKFLOW_PATH, () => {
          const views = [activeGenerationView('S08'), activeGenerationView('S09'), blockedView()]
          const body = views[Math.min(index, views.length - 1)]
          index += 1
          return { body }
        })
      },
    })

    await harness.waitFor(() => {
      expect(harness?.fetch.count('GET', WORKFLOW_GET)).toBe(1)
    })
    await harness.clock.advance(2500)
    expect(harness.fetch.count('GET', WORKFLOW_GET)).toBe(2)
    await harness.clock.advance(2500)
    expect(harness.fetch.count('GET', WORKFLOW_GET)).toBe(3)
    await harness.clock.advance(10000)
    expect(harness.fetch.count('GET', WORKFLOW_GET)).toBe(3)
    expect(harness.fetch.count('POST')).toBe(0)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('确认动作可用键盘聚焦、勾选并提交，状态提示始终可读', async () => {
    harness = await mountU2({
      view: workflowView({ artifacts: [finalBrief()], available_actions: [confirmAction], usage: usage() }),
      setup(fetch) {
        fetch.route('POST', ACTION_PATH + 'confirm_requirements', () => ({ body: acceptedView() }))
      },
    })

    const panel = await findPanel('审查需求卡')
    const checkbox = within(panel).getByRole('checkbox')
    const button = within(panel).getByRole('button', { name: '确认以上需求并生成图片' })
    checkbox.focus()
    expect(checkbox).toHaveFocus()
    await harness.user.keyboard(' ')
    expect(checkbox).toBeChecked()
    expect(button).toBeEnabled()
    expect(button).not.toHaveAttribute('title')
    await harness.user.tab()
    expect(button).toHaveFocus()
    await harness.user.keyboard('{Enter}')
    await harness.waitFor(() => {
      expect(harness?.fetch.last('POST', CONFIRM_PATH)).toBeTruthy()
    })
    expect(harness.fetch.count('POST')).toBe(1)
    expect(harness.router.currentRoute.value.name).toBe('describe')
  })

  it('加载已有参考图不会自动调用分析，手动刷新也仅读取状态', async () => {
    const reference = sampleAsset({ id: 'reference-t74' })
    harness = await mountU2({
      view: workflowView({
        stage: 'S02',
        status: 'none',
        available_actions: [{
          id: 'analyze_reference',
          label: '分析参考图',
          kind: 'button',
          requires_text: false,
          text_label: null,
          requires_confirmation: false,
        }],
      }),
      project: { reference_asset: reference },
    })

    const panel = await findPanel('分析参考图')
    expect(within(panel).getByRole('button', { name: '分析参考图' })).toBeVisible()
    await harness.user.click(within(panel).getByRole('button', { name: '刷新' }))
    await harness.waitFor(() => {
      expect(harness?.fetch.count('GET', WORKFLOW_GET)).toBe(2)
    })
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('320 像素窄屏保留执行进度和折叠提示词，不产生额外写请求', async () => {
    harness = await mountU2({ view: activeGenerationView('S08'), viewportWidth: 320 })
    const panel = await findPanel('生成图片')
    expect(window.innerWidth).toBe(320)
    const execution = within(panel).getByRole('region', { name: '执行进度' })
    expect(within(execution).getAllByRole('listitem')).toHaveLength(4)
    expect(within(panel).getByText('查看已批准的执行提示词（第 2 次）')).toBeVisible()
    expect(harness.fetch.count('POST')).toBe(0)
  })
})
