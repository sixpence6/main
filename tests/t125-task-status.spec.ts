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
    await mountTask(pendingView, (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, () => {
        actionCalls += 1
        return { delayMs: 160, body: pendingView }
      })
    })

    const confirm = await screen.findByRole('button', { name: '确认需求并生成提示词' })
    await harness!.user.click(confirm)
    await waitFor(() => expect(actionCalls).toBe(1))

    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    expect(textarea).toBeEnabled()
    await harness!.user.type(textarea, '补充说明')
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()

    await new Promise((resolve) => setTimeout(resolve, 220))
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
      expect(within(region).getByText(item.label)).toBeVisible()
      expect(within(region).getByText(item.message)).toBeVisible()
      expect(screen.queryByText('RAW_ISSUE_SENTINEL_SHOULD_NOT_RENDER')).not.toBeInTheDocument()
      harness?.dispose()
      harness = undefined
    }
  })

  it('只显示服务端实际提供的恢复动作，只读核实与可能调用模型清晰区分，并需明确确认计数动作', async () => {
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
    let actionBody: unknown
    await mountTask(workflow({
      status: 'blocked',
      task_state: taskState(reason('remote_unknown', '远端状态尚未确认，系统不会自动重发。', 'unknown'), 'blocked'),
      available_actions: [verify, retry, unknown],
    }), (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, (request) => {
        actionBody = request.body
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
    expect(within(region).getByText('可能调用模型')).toBeVisible()
    expect(within(region).queryByRole('button', { name: '不应出现的动作' })).not.toBeInTheDocument()
    expect(harness!.fetch.writes).toEqual([])

    const retryButton = within(region).getByRole('button', { name: '重新生成' })
    expect(retryButton).toBeDisabled()
    await harness!.user.click(within(region).getByLabelText('我确认重新生成会新增一次受理计数并占用生成槽位'))
    expect(retryButton).toBeEnabled()
    await harness!.user.click(retryButton)

    await waitFor(() => expect(harness!.fetch.writes).toHaveLength(1))
    expect(actionBody).toMatchObject({
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
    expect(within(region).getByText('处理服务暂不可用，本次未自动重试。')).toBeVisible()
  })
})
