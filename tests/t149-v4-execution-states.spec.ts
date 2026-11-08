import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
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
const chatSource = readFileSync(resolve(__dirname, '../main/src/pages/ChatPage.vue'), 'utf8')
const chatStyle = chatSource.slice(chatSource.indexOf('<style scoped>'))

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
    requires_new_version:
      category === 'true_drift' || category === 'hard_failure' || category === 'content_rejection',
    requires_evidence: category === 'missing_evidence',
    remote_unknown: category === 'remote_unknown',
    auto_retry: false,
  }
}

function taskState(
  nextReason: WorkflowSafeReason | null,
  status: WorkflowView['status'] = 'blocked',
): WorkflowTaskState {
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

describe('V4 执行、失败与恢复状态', () => {
  it('执行中只显示一条静态状态行，不展示阶段、百分比或打字动画', async () => {
    await mountTask(
      workflow({
        stage: 'S08',
        status: 'active',
        task_state: taskState(null, 'active'),
      }),
    )

    const region = await taskRegion()
    const card = region.querySelector('.task-status-card')
    expect(card).not.toBeNull()
    expect(region).toHaveClass('is-executing')
    expect(within(region).getByText('正在生成图片…')).toBeVisible()
    expect(region.querySelector('.message-bubble')).toBeNull()
    expect(region.querySelector('.typing-dots')).toBeNull()
    expect(region.querySelector('.task-status-icon')).toHaveClass('is-executing')
    expect(within(region).queryByText(/内部阶段/)).not.toBeInTheDocument()
    expect(within(region).queryByText(/%/)).not.toBeInTheDocument()

    const cardRule = chatStyle.match(/\.task-status-card\s*\{[^}]*\}/)?.[0] ?? ''
    expect(cardRule).toContain('background: var(--surface-inset)')
    expect(cardRule).toContain('border-radius: var(--radius-md)')
    expect(cardRule).toContain('padding: var(--space-3) var(--space-4)')
    expect(chatStyle).toMatch(/\.task-status-icon\.is-executing\s*\{[^}]*color:\s*var\(--state-info\)/)
  })

  it('确认动作使用 20×20 复选框，Space 可切换，未勾选时发送不产生请求', async () => {
    let actionBody: unknown
    await mountTask(
      workflow({
        stage: 'S07',
        status: 'blocked',
        task_state: taskState(
          reason('authorization_invalid', '当前授权已失效，不能访问此内容。', 'blocked'),
          'blocked',
        ),
        available_actions: [
          action('revise_prompt', '修改提示词', {
            kind: 'text',
            requires_text: true,
            text_label: '修改说明',
            requires_confirmation: true,
            effect: 'model_call',
            requires_model_call: true,
          }),
        ],
      }),
      (fetch) => {
        fetch.route('POST', ACTIONS_ROUTE, (request) => {
          actionBody = request.body
          return {
            body: workflow({
              stage: 'S07',
              status: 'active',
              task_state: taskState(null, 'active'),
              available_actions: [],
            }),
          }
        })
      },
    )

    const region = await taskRegion()
    const checkbox = within(region).getByRole('checkbox', {
      name: '我已核对当前内容并确认继续。',
    })
    expect(checkbox).toHaveClass('task-confirm-box')
    expect(checkbox).not.toBeChecked()

    const revise = within(region).getByRole('button', { name: '修改提示词' })
    expect(revise).toBeDisabled()
    await harness!.user.click(checkbox)
    expect(checkbox).toBeChecked()
    expect(revise).toBeEnabled()
    await harness!.user.click(revise)
    expect(harness!.fetch.writes).toEqual([])
    expect(within(region).getByText(/请在下方输入框说明/)).toBeVisible()

    await harness!.user.click(checkbox)
    expect(checkbox).not.toBeChecked()

    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness!.user.type(textarea, '改成暖色背景')
    const send = screen.getByRole('button', { name: '发送' })
    expect(send).toBeDisabled()
    await harness!.user.click(send)
    expect(harness!.fetch.writes).toEqual([])

    checkbox.focus()
    await harness!.user.keyboard(' ')
    expect(checkbox).toBeChecked()
    expect(send).toBeEnabled()
    await harness!.user.click(send)

    await waitFor(() => expect(harness!.fetch.writes).toHaveLength(1))
    expect(actionBody).toMatchObject({
      expected_revision: 3,
      text: '改成暖色背景',
      payload: null,
    })
    expect(String(harness!.fetch.writes[0].path)).toContain('/workflow/actions/revise_prompt')

    const checkboxRule = chatStyle.match(/\.task-confirm-box\s*\{[^}]*\}/)?.[0] ?? ''
    expect(checkboxRule).toMatch(/width:\s*20px/)
    expect(checkboxRule).toMatch(/height:\s*20px/)
    expect(checkboxRule).toContain('border: 1px solid var(--line-control)')
    expect(checkboxRule).toContain('border-radius: var(--radius-sm)')
  })

  it('失败在原位显示原因；只有服务端允许时才出现恢复动作，点击后转为执行中', async () => {
    const failed = workflow({
      status: 'failed',
      task_state: taskState(
        reason('hard_failure', '本次结果未通过必须满足项检查，未放行且不会自动重试。', 'failed'),
        'failed',
      ),
      available_actions: [],
    })
    await mountTask(failed)

    let region = await taskRegion()
    expect(region).toHaveClass('is-failed')
    expect(region.querySelector('.message-bubble')).toBeNull()
    expect(within(region).getByText('硬约束不满足')).toBeVisible()
    expect(within(region).getByText('本次结果未通过必须满足项检查，未放行且不会自动重试。')).toBeVisible()
    expect(region.querySelector('.task-status-icon')).toHaveClass('is-failed')
    expect(within(region).queryByRole('button')).not.toBeInTheDocument()
    expect(chatStyle).toMatch(/\.task-status-icon\.is-failed\s*\{[^}]*color:\s*var\(--state-danger\)/)

    harness?.dispose()
    harness = undefined
    await mountTask(
      workflow({
        status: 'failed',
        task_state: taskState(
          reason('technical_failure', '处理服务暂不可用，本次未自动重试。', 'failed'),
          'failed',
        ),
        available_actions: [action('recover_generation', '恢复原任务')],
      }),
      (fetch) => {
        fetch.route('POST', ACTIONS_ROUTE, () => ({
          delayMs: 80,
          body: workflow({
            stage: 'S08',
            status: 'active',
            task_state: taskState(null, 'active'),
            available_actions: [],
          }),
        }))
      },
    )

    region = await taskRegion()
    const recover = within(region).getByRole('button', { name: '恢复原任务' })
    expect(recover).toBeVisible()
    await harness!.user.click(recover)
    expect(region).toHaveClass('is-executing')
    expect(within(region).getByText('正在恢复…')).toBeVisible()

    await waitFor(() => expect(within(region).getByText('正在生成图片…')).toBeVisible())
    expect(region).toHaveClass('is-executing')
    expect(harness!.fetch.writes).toHaveLength(1)
  })

  it('结果未知时显示「结果未知，正在核实」，只呈现服务端给出的核实与恢复动作', async () => {
    await mountTask(
      workflow({
        status: 'blocked',
        task_state: taskState(
          reason('remote_unknown', '远端状态尚未确认，系统不会自动重发。', 'unknown'),
          'blocked',
        ),
        available_actions: [
          action('verify_generation', '核实原任务', { effect: 'read_only', read_only: true }),
          action('retrieve_original_result', '获取原结果', { effect: 'read_only', read_only: true }),
        ],
      }),
    )

    const region = await taskRegion()
    expect(region).toHaveClass('is-unknown')
    expect(region).toHaveTextContent('结果未知，正在核实')
    expect(within(region).getByText('远端状态尚未确认，系统不会自动重发。')).toBeVisible()
    const buttons = within(region).getAllByRole('button')
    expect(buttons.map((button) => button.textContent?.trim())).toEqual(['核实原任务', '获取原结果'])
    expect(within(region).queryByText(/已取消/)).not.toBeInTheDocument()
    expect(region.querySelector('.task-status-icon')).toHaveClass('is-unknown')
  })

  it('恢复成功后原任务块在原位更新为成功态，不追加第二条状态消息', async () => {
    const failed = workflow({
      status: 'failed',
      task_state: taskState(
        reason('technical_failure', '处理服务暂不可用，本次未自动重试。', 'failed'),
        'failed',
      ),
      available_actions: [action('recover_generation', '恢复原任务')],
    })
    await mountTask(failed, (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, () => ({
        body: workflow({
          stage: 'S10',
          status: 'completed',
          task_state: null,
          available_actions: [],
        }),
      }))
    })

    const region = await taskRegion()
    const originalRegion = region
    expect(within(region).getByRole('button', { name: '恢复原任务' })).toBeVisible()
    await harness!.user.click(within(region).getByRole('button', { name: '恢复原任务' }))

    await waitFor(() => expect(originalRegion).toHaveClass('is-recovered'))
    expect(originalRegion.isConnected).toBe(true)
    expect(screen.getAllByRole('region', { name: '任务状态' })).toHaveLength(1)
    expect(within(originalRegion).getByText('已恢复')).toBeVisible()
    expect(within(originalRegion).getByText('这一轮已完成。')).toBeVisible()
    expect(originalRegion.querySelector('.task-status-icon')).toHaveClass('is-recovered')
    expect(within(originalRegion).queryByRole('button')).not.toBeInTheDocument()
  })

  it('减少动态效果时任务块没有动画或循环过渡', async () => {
    await mountTask(
      workflow({
        stage: 'S08',
        status: 'active',
        task_state: taskState(null, 'active'),
      }),
    )

    const region = await taskRegion()
    expect(region.querySelector('.typing-dots')).toBeNull()
    expect(chatStyle).not.toContain('@keyframes typing')

    const reduced = chatStyle.match(/@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\n\}/)?.[0] ?? ''
    expect(reduced).toContain('.task-status-card')
    expect(reduced).toContain('.task-status-icon')
    expect(reduced).toContain('animation: none')
    expect(reduced).toContain('transition: none')

    const baseTaskStyle = chatStyle.slice(
      chatStyle.indexOf('.task-status-card'),
      chatStyle.indexOf('@media (prefers-reduced-motion: reduce)'),
    )
    expect(baseTaskStyle).not.toMatch(/animation\s*:/)
  })
})
