import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { WorkflowAction, WorkflowView } from '@/api/workflow'
import { userStateOf } from '@/workflow/projection'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleAsset,
  sampleBrief,
  sampleGeneration,
  sampleProject,
} from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const confirmAction: WorkflowAction = {
  id: 'confirm_requirements',
  label: '确认并生成图片',
  kind: 'confirm',
  requires_text: false,
  text_label: null,
  requires_confirmation: true,
}

const verifyAction: WorkflowAction = {
  id: 'verify_generation',
  label: '核实原任务',
  kind: 'button',
  requires_text: false,
  text_label: null,
  requires_confirmation: false,
}

function workflowView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-t73',
    stage: 'S06',
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
    updated_at: '2026-11-01T00:00:00.000Z',
    ...overrides,
  }
}

function candidateReport() {
  return {
    id: 'artifact-report-candidate',
    kind: 'reference_report',
    revision_no: 1,
    status: 'candidate' as const,
    digest: 'digest-report-candidate',
    payload: { observations: [{ description: '未批准参考观察不得提前显示' }] },
    created_at: '2026-10-31T00:00:00.000Z',
  }
}

function candidateFinalBrief() {
  return {
    id: 'artifact-final-candidate',
    kind: 'final_brief',
    revision_no: 2,
    status: 'candidate' as const,
    digest: 'digest-final-candidate',
    payload: { product_description: '未批准最终需求内容不得提前显示' },
    created_at: '2026-10-31T00:01:00.000Z',
  }
}

function approvedFinalBrief() {
  return {
    id: 'artifact-final-approved',
    kind: 'final_brief',
    revision_no: 2,
    status: 'approved' as const,
    digest: 'digest-final-approved',
    payload: {
      product_description: '米色亚麻托特包',
      purpose: '电商主图',
      image_size: '3840x2160',
    },
    created_at: '2026-10-31T00:01:00.000Z',
  }
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
    generations: [] as ReturnType<typeof sampleGeneration>[],
  }
}

function mountU1(options: {
  view: WorkflowView
  project?: Parameters<typeof projectDetail>[0]
  generations?: ReturnType<typeof sampleGeneration>[]
  viewportWidth?: number
}) {
  const detail = projectDetail(options.project)
  detail.generations = options.generations ?? []
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth: options.viewportWidth ?? 1440,
    url: '/?project=' + PROJECT_A,
    routes: [
      { method: 'GET', url: '/api/projects/' + PROJECT_A, responses: [{ body: detail }] },
      {
        method: 'GET',
        url: '/api/projects/' + PROJECT_A + '/workflow',
        responses: [{ body: options.view }],
      },
    ],
  })
}


async function findU1() {
  const workbench = await screen.findByRole('region', { name: '需求工作台' })
  await harness!.waitFor(() => {
    expect(workbench.querySelector('.workflow-panel')).not.toBeNull()
  })
  return workbench
}

describe('T21 U1 沟通、参考输入与当前需求卡', () => {
  it('桌面把对话与需求放在同一双列工作台，候选报告和候选最终卡不提前显示', async () => {
    harness = await mountU1({
      view: workflowView({
        artifacts: [candidateReport(), candidateFinalBrief()],
        available_actions: [confirmAction],
      }),
    })

    const workbench = await findU1()
    expect(workbench.querySelector('.u1-conversation .conversation-panel')).not.toBeNull()
    expect(workbench.querySelector('.u1-requirement .workflow-panel')).not.toBeNull()
    expect(within(workbench).getByRole('heading', { name: '春季托特包主图' })).toBeVisible()
    expect(within(workbench).getByText('等待独立审查')).toBeVisible()
    expect(
      within(workbench).getByText('需求卡内容正在审查，通过后会在此完整展示，不能提前确认。'),
    ).toBeVisible()
    expect(within(workbench).queryByText(/未批准参考观察不得提前显示/)).not.toBeInTheDocument()
    expect(within(workbench).queryByText(/未批准最终需求内容不得提前显示/)).not.toBeInTheDocument()
    expect(within(workbench).queryByRole('button', { name: /确认.*生成图片/ })).not.toBeInTheDocument()
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('只有审查通过且待确认时给出唯一确认动作，下一步与当前状态一致', async () => {
    harness = await mountU1({
      view: workflowView({
        artifacts: [approvedFinalBrief()],
        available_actions: [confirmAction],
        usage: {
          daily_limit: 20,
          used: 1,
          remaining: 19,
          window_started_at: '2026-10-31T00:00:00.000Z',
          window_expires_at: '2026-11-01T00:00:00.000Z',
          personal_active: 0,
          personal_limit: 1,
          instance_active: 0,
          instance_limit: 2,
          project_active: 0,
          project_limit: 1,
        },
      }),
    })

    const workbench = await findU1()
    const nextStep = within(workbench).getByRole('status', {
      name: '核对并确认当前需求卡',
    })
    expect(nextStep).toHaveTextContent('核对并确认当前需求卡')
    expect(within(nextStep).getByText('下一步操作：确认以上需求并生成图片')).toBeVisible()
    expect(within(workbench).getByText('米色亚麻托特包')).toBeVisible()
    expect(within(workbench).getAllByRole('button', { name: '确认以上需求并生成图片' })).toHaveLength(1)
    const checks = within(workbench).getByRole('checkbox')
    expect(checks).not.toBeChecked()
  })

  it('阻塞原任务时当前问题只出现一次，下一步指向核实原任务', async () => {
    harness = await mountU1({
      view: workflowView({
        stage: 'S08',
        status: 'blocked',
        issue: {
          code: 'GENERATION_UNKNOWN',
          message: '远端生成结果未知，任务与一次计数已保留。',
          template_id: null,
          params: {},
        },
        available_actions: [verifyAction],
      }),
    })

    const workbench = await findU1()
    expect(within(workbench).getByText('当前问题需要处理')).toBeVisible()
    expect(within(workbench).getByText('下一步操作：核实原任务')).toBeVisible()
    expect(
      within(workbench).getAllByText('远端生成结果未知，任务与一次计数已保留。'),
    ).toHaveLength(1)
    expect(within(workbench).getByRole('button', { name: '核实原任务' })).toBeVisible()
  })

  it('历史图片只显示已成功且文件可用的旧版本，并明确不代表当前状态', async () => {
    const availableTwo = sampleAsset({
      id: 'asset-history-2',
      kind: 'generated',
      availability: 'available',
    })
    const availableFive = sampleAsset({
      id: 'asset-history-5',
      kind: 'generated',
      availability: 'available',
    })
    harness = await mountU1({
      view: workflowView({ artifacts: [approvedFinalBrief()] }),
      generations: [
        sampleGeneration({ id: 'generation-1', version_no: 1, result_asset: availableTwo }),
        sampleGeneration({
          id: 'generation-2',
          version_no: 2,
          result_asset: sampleAsset({ id: 'asset-missing-2', kind: 'generated', availability: 'missing' }),
        }),
        sampleGeneration({
          id: 'generation-3',
          version_no: 3,
          status: 'failed',
          result_asset: sampleAsset({ id: 'asset-failed-3', kind: 'generated' }),
        }),
        sampleGeneration({ id: 'generation-4', version_no: 4, result_asset: null }),
        sampleGeneration({ id: 'generation-5', version_no: 5, result_asset: availableFive }),
      ],
    })

    const history = await screen.findByRole('region', { name: '历史图片预览' })
    expect(
      within(history).getByText(/以下旧图仅作历史参考，不代表当前需求状态/),
    ).toBeVisible()
    const images = within(history).getAllByRole('img')
    expect(images.map((image) => image.getAttribute('alt'))).toEqual([
      '第 5 次生成的历史预览',
      '第 1 次生成的历史预览',
    ])
    expect(within(history).getByText('第 5 次生成 · 历史预览，不代表当前需求状态')).toBeVisible()
    expect(within(history).queryByText(/第 2 次生成/)).not.toBeInTheDocument()
    expect(within(history).queryByText(/第 3 次生成/)).not.toBeInTheDocument()
    expect(within(history).queryByText(/第 4 次生成/)).not.toBeInTheDocument()
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('未发送文字只留在页面内存，参考图操作提示当前项目修订', async () => {
    const reference = sampleAsset({ id: 'reference-t73' })
    harness = await mountU1({
      view: workflowView({ artifacts: [approvedFinalBrief()] }),
      project: {
        brief_status: 'draft',
        reference_asset: reference,
        brief: sampleBrief({ reference_asset_id: reference.id }),
      },
    })

    await findU1()
    const composer = screen.getByLabelText('描述商品与图片需求') as HTMLTextAreaElement
    await harness.user.click(composer)
    await harness.user.type(composer, '仅留在当前页面的未发送草稿')
    expect(composer).toHaveFocus()
    expect(composer.value).toBe('仅留在当前页面的未发送草稿')
    expect(Object.values(harness.storage.local.entries()).join(' ')).not.toContain('仅留在当前页面')
    expect(Object.values(harness.storage.session.entries()).join(' ')).not.toContain('仅留在当前页面')
    expect(screen.getByRole('button', { name: '替换参考图' })).toBeVisible()
    expect(screen.getByRole('button', { name: '移除参考图' })).toBeVisible()
    expect(screen.getByText('当前参考图已关联。替换或移除会使用当前项目修订。')).toBeVisible()
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('320 像素窄屏仍保留同一工作台和页面内输入，不产生额外写请求', async () => {
    harness = await mountU1({
      view: workflowView({ artifacts: [approvedFinalBrief()] }),
      viewportWidth: 320,
    })

    const workbench = await findU1()
    expect(window.innerWidth).toBe(320)
    expect(workbench.querySelectorAll('.u1-column')).toHaveLength(2)
    expect(screen.getByLabelText('描述商品与图片需求')).toBeVisible()
    expect(harness.fetch.count('POST')).toBe(0)
  })
})

describe('T21 U1/U2/U3 状态投影', () => {
  it('当前交付与修订一致是 U3，已受理或待确认是 U2，其余是 U1', () => {
    const project = sampleProject({ revision: 3 })
    const base = workflowView()
    expect(userStateOf(project, base)).toBe('u1')
    expect(
      userStateOf(project, workflowView({
        artifacts: [approvedFinalBrief()],
        available_actions: [confirmAction],
      })),
    ).toBe('u2')
    expect(
      userStateOf(project, workflowView({
        artifacts: [approvedFinalBrief()],
        intent: {
          id: 'intent-1',
          request_id: 'request-1',
          status: 'accepted',
          artifact_digest: 'digest-final-approved',
          requirement_revision: 3,
          created_at: '2026-10-31T00:02:00.000Z',
        },
      })),
    ).toBe('u2')
    expect(
      userStateOf(project, workflowView({
        artifacts: [approvedFinalBrief()],
        delivery: {
          id: 'delivery-1',
          status: 'published',
          asset_id: 'asset-delivery',
          filename: 'delivery.png',
          caption: null,
          published_at: '2026-10-31T00:03:00.000Z',
          created_at: '2026-10-31T00:02:30.000Z',
        },
      })),
    ).toBe('u3')
  })
})
