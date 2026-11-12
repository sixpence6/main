import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/vue'
import type { ProjectDetailResponse, ProjectView } from '@/api/types'
import type {
  WorkflowAction,
  WorkflowReasonCategory,
  WorkflowSafeReason,
  WorkflowTaskState,
  WorkflowView,
} from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, emptyConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const DETAIL_A = '/api/projects/' + PROJECT_A
const WORKFLOW_A = DETAIL_A + '/workflow'
const ACTIONS_ROUTE = new RegExp('^' + DETAIL_A + '/workflow/actions/[^/]+$')

function project(): ProjectView {
  return sampleProject({ id: PROJECT_A, title: '项目甲', revision: 3 })
}

function detail(): ProjectDetailResponse {
  return { project: project(), messages: [], generations: [] }
}

function reason(
  category: WorkflowReasonCategory,
  message: string,
  severity: WorkflowSafeReason['severity'],
): WorkflowSafeReason {
  return {
    category,
    code: 'TEST_REASON',
    message,
    template_id: null,
    params: {},
    severity,
    requires_user_input: severity !== 'unknown',
    requires_new_version: category === 'true_drift' || category === 'hard_failure' || category === 'content_rejection',
    requires_evidence: category === 'missing_evidence',
    remote_unknown: category === 'remote_unknown',
    auto_retry: false,
  }
}

function taskState(nextReason: WorkflowSafeReason | null, status: WorkflowView['status'] = 'blocked'): WorkflowTaskState {
  return {
    progress: {
      phase: 'generation',
      stage: 'S08',
      stage_label: '内部阶段-生成图片',
      status,
      awaiting_user: Boolean(nextReason),
      terminal: status === 'failed',
    },
    reason: nextReason,
    binding: {
      project_id: PROJECT_A,
      run_id: 'run-123',
      project_revision: 3,
      requirement_revision: 3,
      intent_id: 'intent-123',
      intent_status: 'accepted',
      intent_requirement_revision: 3,
      generation_job_id: 'job-123',
      generation_job_status: 'unknown',
      confirmed_snapshot_digest: 'snapshot-digest',
      execution_snapshot_digest: 'snapshot-digest',
      delivery_id: null,
      delivery_status: null,
      current: true,
    },
    constraints: [],
    resource_state: {
      current_run_id: 'run-123',
      current_state: 'unknown',
      current_version: null,
      history: [],
    },
  }
}

function workflow(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-123',
    stage: 'S08',
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
    usage: {
      daily_limit: 20,
      used: 1,
      remaining: 19,
      window_started_at: '2026-11-01T00:00:00.000Z',
      window_expires_at: '2026-11-02T00:00:00.000Z',
      personal_active: 1,
      personal_limit: 2,
      instance_active: 1,
      instance_limit: 4,
      project_active: 1,
      project_limit: 1,
    },
    updated_at: '2026-11-01T00:00:00.000Z',
    ...overrides,
  }
}

function action(id: string, label: string, overrides: Partial<WorkflowAction> = {}): WorkflowAction {
  return {
    id,
    label,
    kind: 'button',
    requires_text: false,
    text_label: null,
    requires_confirmation: false,
    effect: 'write',
    read_only: false,
    requires_model_call: false,
    retry_mode: 'none',
    automatic_retry: false,
    ...overrides,
  }
}

function finalBriefArtifact(): WorkflowView['artifacts'][number] {
  return {
    id: 'artifact-final-approved',
    kind: 'final_brief',
    revision_no: 1,
    status: 'approved',
    digest: 'digest-final-approved',
    payload: {
      product_description: '米色亚麻托特包',
      purpose: '电商主图',
      texts: ['WARM MORNING'],
      size: '1024x1024',
    },
    created_at: '2026-11-01T00:00:00.000Z',
  }
}

async function mountTask(
  view: WorkflowView,
  setup?: (fetch: WorkspaceHarness['fetch']) => void,
): Promise<WorkspaceHarness> {
  const mounted = await mountWorkspace({
    config: emptyConfig(),
    viewportWidth: 1440,
    url: '/projects/' + PROJECT_A + '/chat',
    setup(fetch) {
      fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
      fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
      fetch.route('GET', WORKFLOW_A, () => ({ body: view }))
      setup?.(fetch)
    },
  })
  harness = mounted
  return mounted
}

function taskRegion(): Promise<HTMLElement> {
  return screen.findByRole('region', { name: '任务状态' })
}

describe('T13 轻量任务状态、失败与真实恢复动作', () => {
  it('进行中只显示一条当前状态，不展示阶段卡、内部阶段或虚构百分比', async () => {
    await mountTask(workflow({
      stage: 'S04',
      status: 'active',
      task_state: {
        ...taskState(null, 'active'),
        progress: {
          phase: 'requirements',
          stage: 'S04',
          stage_label: '内部阶段-制定创意方向',
          status: 'active',
          awaiting_user: false,
          terminal: false,
        },
      },
      available_actions: [action('submit_generation', '提交生成', { effect: 'model_call', requires_model_call: true })],
    }))

    const region = await taskRegion()
    expect(within(region).getByText('正在构思画面…')).toBeVisible()
    expect(within(region).queryByText('内部阶段-制定创意方向')).not.toBeInTheDocument()
    expect(within(region).queryByText(/%/)).not.toBeInTheDocument()
    expect(within(region).queryByRole('button', { name: '提交生成' })).not.toBeInTheDocument()
    expect(screen.queryByText('需求工作台')).not.toBeInTheDocument()
    expect(screen.queryByText('工作流面板')).not.toBeInTheDocument()
  })

  it('等待生成时仍只显示一条状态，不出现阶段卡或自动提交', async () => {
    await mountTask(workflow({
      stage: 'S08',
      status: 'active',
      task_state: taskState(null, 'active'),
      generation_job: {
        id: 'job-waiting',
        status: 'queued',
        request_id: 'request-waiting',
        submit_attempted: true,
        result_asset_id: null,
        error_code: null,
        error_message: null,
        created_at: '2026-11-01T00:00:00.000Z',
        updated_at: '2026-11-01T00:00:00.000Z',
      },
      available_actions: [action('submit_generation', '提交生成', { effect: 'model_call', requires_model_call: true })],
    }))

    const region = await taskRegion()
    expect(within(region).getByText('正在生成图片…')).toBeVisible()
    expect(within(region).queryByText('提交生成')).not.toBeInTheDocument()
    expect(within(region).queryByText(/%/)).not.toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 60))
    expect(harness!.fetch.writes).toEqual([])
  })

  it('任务进行中草稿仍可编辑，但发送保持禁用且不会排队提交', async () => {
    let actionCalls = 0
    const pendingView = workflow({
      stage: 'S06',
      status: 'needs_input',
      artifacts: [finalBriefArtifact()],
      available_actions: [action('confirm_requirements', '确认需求卡', {
        kind: 'confirm',
        requires_confirmation: true,
        effect: 'write',
      })],
    })
    let finishAction!: () => void
    const actionFinished = new Promise<void>((resolve) => { finishAction = resolve })
    await mountTask(pendingView, (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, async () => {
        actionCalls += 1
        await actionFinished
        return { body: pendingView }
      })
    })

    const confirm = await screen.findByRole('button', { name: '确认需求并生成提示词' })
    await harness!.user.click(confirm)
    await waitFor(() => expect(actionCalls).toBe(1))

    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    expect(textarea).toBeEnabled()
    await harness!.user.type(textarea, '补充说明')
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()

    finishAction()
    await waitFor(() => expect(screen.getByRole('button', { name: '发送' })).toBeEnabled())
    expect(actionCalls).toBe(1)
  })

  it('安全原因来自服务端，并区分需补充、硬约束、编制修复、未知和技术故障', async () => {
    const cases: Array<{ category: WorkflowReasonCategory; label: string; message: string; severity: WorkflowSafeReason['severity'] }> = [
      { category: 'missing_information', label: '需要补充信息', message: '还需要补充信息后才能继续。', severity: 'needs_input' },
      { category: 'hard_failure', label: '硬约束不满足', message: '本次结果未通过必须满足项检查，未放行且不会自动重试。', severity: 'failed' },
      { category: 'repairable_composition', label: '内容需要修复', message: '内容需要调整后可重新审查。', severity: 'blocked' },
      { category: 'remote_unknown', label: '结果未知', message: '远端状态尚未确认，系统不会自动重发。', severity: 'unknown' },
      { category: 'technical_failure', label: '技术故障', message: '处理服务暂不可用，本次未自动重试。', severity: 'failed' },
    ]

    for (const item of cases) {
      await mountTask(workflow({
        status: item.severity === 'needs_input' ? 'needs_input' : item.severity === 'unknown' ? 'blocked' : item.severity === 'failed' ? 'failed' : 'blocked',
        issue: { code: 'RAW_ISSUE', message: 'RAW_ISSUE_SENTINEL_SHOULD_NOT_RENDER', template_id: null, params: {} },
        task_state: taskState(reason(item.category, item.message, item.severity), item.severity === 'needs_input' ? 'needs_input' : item.severity === 'unknown' ? 'blocked' : item.severity === 'failed' ? 'failed' : 'blocked'),
      }))

      const region = await taskRegion()
      const expectedLabel = item.label
      expect(within(region).getByText(expectedLabel)).toBeVisible()
      if (item.severity === 'failed' || item.severity === 'unknown') {
        expect(within(region).queryByText(item.message)).not.toBeInTheDocument()
      } else {
        expect(within(region).getByText(item.message)).toBeVisible()
      }
      expect(screen.queryByText('RAW_ISSUE_SENTINEL_SHOULD_NOT_RENDER')).not.toBeInTheDocument()
      harness?.dispose()
      harness = undefined
    }
  })

  it('S03 需求澄清时展示已审核的分步问题，而不是通用补充提示', async () => {
    const detailText = [
      '已了解你要做米色亚麻托特包的商品主图。',
      '1. 请确认这张图主要用在哪里？例如：淘宝主图、小红书封面或品牌官网。',
      '2. 请确认必须保留哪些商品特征？例如：浅米色、亚麻纹理或手提带形状。',
    ].join('\n')
    const base = taskState(reason('missing_information', '还需要补充信息后才能继续。', 'needs_input'), 'needs_input')
    await mountTask(workflow({
      stage: 'S03',
      status: 'needs_input',
      issue: { code: 'MISSING_INPUT', message: detailText, template_id: null, params: {} },
      task_state: {
        ...base,
        progress: {
          ...base.progress,
          phase: 'requirements',
          stage: 'S03',
          stage_label: '内部阶段-澄清需求',
          status: 'needs_input',
          awaiting_user: true,
          terminal: false,
        },
      },
    }))

    const region = await taskRegion()
    expect(within(region).getByText('需要补充信息')).toBeVisible()
    expect(within(region).getByText(/请确认这张图主要用在哪里/)).toBeVisible()
    expect(within(region).getByText(/请确认必须保留哪些商品特征/)).toBeVisible()
    expect(within(region).queryByText('还需要补充信息后才能继续。')).not.toBeInTheDocument()
    expect(screen.queryByText('正在整理需求…')).not.toBeInTheDocument()
  })

  it('结果未知时只保留只读核实动作；正常状态下的计数动作仍需明确确认', async () => {
    const verify = action('verify_generation', '核实原任务', {
      effect: 'read_only',
      read_only: true,
      retry_mode: 'none',
    })
    const retry = action('retry_generation', '重新生成', {
      kind: 'confirm',
      effect: 'model_call',
      requires_model_call: true,
      requires_confirmation: true,
      retry_mode: 'manual_only',
    })
    const unknown = action('unexpected_action', '不应出现的动作')
    let verifyBody: unknown
    await mountTask(workflow({
      status: 'blocked',
      task_state: taskState(reason('remote_unknown', '远端状态尚未确认，系统不会自动重发。', 'unknown'), 'blocked'),
      available_actions: [verify, retry, unknown],
    }), (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, (request) => {
        verifyBody = request.body
        return {
          body: workflow({
            status: 'blocked',
            task_state: taskState(reason('remote_unknown', '远端状态尚未确认，系统不会自动重发。', 'unknown'), 'blocked'),
            available_actions: [verify],
          }),
        }
      })
    })

    const region = await taskRegion()
    expect(within(region).getByRole('button', { name: '核实原任务' })).toBeVisible()
    expect(within(region).getByText('只读核实')).toBeVisible()
    expect(within(region).queryByRole('button', { name: '重新生成' })).not.toBeInTheDocument()
    expect(within(region).queryByText('可能调用模型')).not.toBeInTheDocument()
    expect(within(region).queryByRole('button', { name: '不应出现的动作' })).not.toBeInTheDocument()
    expect(harness!.fetch.writes).toEqual([])

    await harness!.user.click(within(region).getByRole('button', { name: '核实原任务' }))
    await waitFor(() => expect(harness!.fetch.writes).toHaveLength(1))
    expect(verifyBody).toMatchObject({
      expected_revision: 3,
      text: null,
      payload: null,
    })
    expect(String(harness!.fetch.writes[0].path)).toContain('/workflow/actions/verify_generation')

    harness?.dispose()
    harness = undefined
    let retryBody: unknown
    await mountTask(workflow({
      status: 'blocked',
      task_state: taskState(reason('missing_information', '还需要补充信息后才能继续。', 'needs_input'), 'blocked'),
      available_actions: [retry],
    }), (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, (request) => {
        retryBody = request.body
        return {
          body: workflow({
            status: 'active',
            task_state: taskState(null, 'active'),
            available_actions: [],
          }),
        }
      })
    })

    const normalRegion = await taskRegion()
    expect(within(normalRegion).getByText('可能调用模型')).toBeVisible()
    const retryButton = within(normalRegion).getByRole('button', { name: '重新生成' })
    expect(retryButton).toBeDisabled()
    await harness!.user.click(within(normalRegion).getByLabelText('我确认重新生成会新增一次受理计数并占用生成槽位'))
    expect(retryButton).toBeEnabled()
    await harness!.user.click(retryButton)

    await waitFor(() => expect(harness!.fetch.writes).toHaveLength(1))
    expect(retryBody).toMatchObject({
      expected_revision: 3,
      text: null,
      payload: { acknowledge_new_attempt: true },
    })
    expect(String(harness!.fetch.writes[0].path)).toContain('/workflow/actions/retry_generation')
  })

  it('文字修复动作回到唯一输入框，发送前不会调用动作，也不会出现第二个输入表单', async () => {
    let actionBody: unknown
    await mountTask(workflow({
      stage: 'S07',
      status: 'blocked',
      task_state: taskState(reason('authorization_invalid', '当前授权已失效，不能访问此内容。', 'blocked'), 'blocked'),
      available_actions: [action('revise_prompt', '修改提示词', {
        kind: 'text',
        requires_text: true,
        text_label: '修改说明',
        effect: 'model_call',
        requires_model_call: true,
      })],
    }), (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, (request) => {
        actionBody = request.body
        return { body: workflow({ stage: 'S07', status: 'active', available_actions: [] }) }
      })
    })

    const region = await taskRegion()
    expect(screen.getAllByRole('textbox', { name: '描述商品与图片需求' })).toHaveLength(1)
    await harness!.user.click(within(region).getByRole('button', { name: '修改提示词' }))
    expect(harness!.fetch.writes).toEqual([])
    expect(await screen.findByText(/请在下方输入框说明/)).toBeVisible()

    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness!.user.type(textarea, '改成暖色背景')
    await harness!.user.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(harness!.fetch.writes).toHaveLength(1))
    expect(actionBody).toMatchObject({
      expected_revision: 3,
      text: '改成暖色背景',
      payload: null,
    })
    expect(String(harness!.fetch.writes[0].path)).toContain('/workflow/actions/revise_prompt')
    expect(screen.getAllByRole('textbox', { name: '描述商品与图片需求' })).toHaveLength(1)
  })

  it('S07 历史提示词门禁阻断时直接提供重新编制提示词的入口', async () => {
    const blocked = workflow({
      stage: 'S07',
      status: 'blocked',
      issue: {
        code: 'PROMPT_REQUIRES_RECONFIRMATION',
        message: '提示词引入了尚未确认的卖点、文字或弱化了已确认要求，未提交生图。请修改需求并重新确认后再生成。',
        template_id: 'PROMPT_REQUIRES_RECONFIRMATION',
        params: {},
      },
      task_state: taskState(reason('true_drift', '当前状态不支持此操作，请先完成提示的步骤。', 'blocked'), 'blocked'),
      available_actions: [
        action('compile_prompt', '编制生图提示词', { effect: 'model_call', requires_model_call: true }),
        action('revise_requirements', '修改需求', { effect: 'model_call', requires_model_call: true }),
      ],
    })
    await mountTask(blocked, (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, () => ({
        body: workflow({
          stage: 'S07',
          status: 'needs_input',
          task_state: taskState(null, 'needs_input'),
          available_actions: [],
        }),
      }))
    })

    const region = await taskRegion()
    expect(within(region).getByText('提示词需要重新编制')).toBeVisible()
    expect(within(region).getByText(/已确认需求没有变化，不需要修改需求/)).toBeVisible()
    expect(within(region).queryByText('需要重新确认')).not.toBeInTheDocument()
    expect(within(region).queryByText('当前状态不支持此操作，请先完成提示的步骤。')).not.toBeInTheDocument()
    await harness!.user.click(within(region).getByRole('button', { name: '编制生图提示词' }))

    await waitFor(() => expect(harness!.fetch.writes).toHaveLength(1))
    expect(String(harness!.fetch.writes[0].path)).toContain('/workflow/actions/compile_prompt')
  })

  it('S04 被历史业务校验阻断时提供重新制定创意方向的入口', async () => {
    const blocked = workflow({
      stage: 'S04',
      status: 'blocked',
      issue: {
        code: 'REQUIREMENT_DRIFT',
        message: '创意方向与已批准需求不一致，未通过业务校验',
        template_id: null,
        params: {},
      },
      task_state: taskState(reason('true_drift', '当前状态不支持此操作，请先完成提示的步骤。', 'blocked'), 'blocked'),
      available_actions: [
        action('plan_creative', '制定创意方向', { effect: 'model_call', requires_model_call: true }),
      ],
    })
    await mountTask(blocked, (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, (request) => {
        if (String(request.path).includes('/plan_creative')) {
          return {
            body: workflow({
              stage: 'S05',
              status: 'active',
              task_state: taskState(null, 'active'),
              available_actions: [
                action('plan_visual', '制定视觉方案', { effect: 'model_call', requires_model_call: true }),
              ],
            }),
          }
        }
        return {
          body: workflow({ stage: 'S06', status: 'active', task_state: taskState(null, 'active') }),
        }
      })
    })

    const region = await taskRegion()
    expect(within(region).getByText('创意方向需要重新整理')).toBeVisible()
    expect(within(region).getByText(/需求信息已经整理完成，不需要重新确认/)).toBeVisible()
    expect(within(region).queryByText('需要重新确认')).not.toBeInTheDocument()
    expect(within(region).queryByText('当前状态不支持此操作，请先完成提示的步骤。')).not.toBeInTheDocument()
    await harness!.user.click(within(region).getByRole('button', { name: '制定创意方向' }))

    await waitFor(() => expect(harness!.fetch.writes).toHaveLength(2))
    expect(String(harness!.fetch.writes[0].path)).toContain('/workflow/actions/plan_creative')
    expect(String(harness!.fetch.writes[1].path)).toContain('/workflow/actions/plan_visual')
  })

  it('S09 历史图片检查阻断时可直接重新检查，不会重新生成', async () => {
    const blocked = workflow({
      stage: 'S09',
      status: 'blocked',
      issue: {
        code: 'IMAGE_NOT_APPROVED',
        message: '本次图片未通过检查，未展示或提供下载。你可以补充需求后重新确认并提交生成。',
        template_id: 'IMAGE_NOT_APPROVED',
        params: {},
      },
      task_state: taskState(reason('true_drift', '当前状态不支持此操作，请先完成提示的步骤。', 'blocked'), 'blocked'),
      available_actions: [
        action('recheck_candidate', '重新检查这张图片'),
        action('retry_rejected_generation', '重新生成', {
          kind: 'confirm',
          effect: 'model_call',
          requires_model_call: true,
          requires_confirmation: true,
        }),
      ],
    })
    await mountTask(blocked, (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, () => ({
        body: workflow({
          stage: 'S10',
          status: 'active',
          task_state: taskState(null, 'active'),
          available_actions: [],
        }),
      }))
    })

    const region = await taskRegion()
    expect(within(region).getByText('图片需要重新检查')).toBeVisible()
    expect(within(region).getByText('本次图片已经生成。点“重新检查这张图片”即可按简化后的规则重新核对；不会重新生成，也不会新增受理次数或占用生成槽位。')).toBeVisible()
    expect(within(region).queryByText('需要重新确认')).not.toBeInTheDocument()
    expect(within(region).queryByText('当前状态不支持此操作，请先完成提示的步骤。')).not.toBeInTheDocument()
    await harness!.user.click(within(region).getByRole('button', { name: '重新检查这张图片' }))

    await waitFor(() => expect(harness!.fetch.writes).toHaveLength(1))
    expect(String(harness!.fetch.writes[0].path)).toContain('/workflow/actions/recheck_candidate')
  })

  it('账号禁用时只显示安全失败，不自动重试也不宣称生成成功', async () => {
    await mountTask(workflow({
      status: 'blocked',
      task_state: taskState(reason('technical_failure', '处理服务暂不可用，本次未自动重试。', 'failed'), 'failed'),
      available_actions: [action('verify_generation', '核实原任务', { effect: 'read_only', read_only: true })],
    }), (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, () => ({
        status: 403,
        body: { error: { code: 'ACCOUNT_DISABLED', message: '账号已禁用，当前操作未执行。' } },
      }))
    })

    const region = await taskRegion()
    await harness!.user.click(within(region).getByRole('button', { name: '核实原任务' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('账号已禁用，当前操作未执行。')
    expect(harness!.fetch.writes).toHaveLength(1)
    expect(screen.queryByText(/图片已经生成，可以保存使用了/)).not.toBeInTheDocument()
    expect(within(region).getByText('技术故障')).toBeVisible()
    expect(within(region).queryByText('处理服务暂不可用，本次未自动重试。')).not.toBeInTheDocument()
  })
})
