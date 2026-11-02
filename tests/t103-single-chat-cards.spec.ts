import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type {
  WorkflowApproval,
  WorkflowArtifact,
  WorkflowTaskState,
  WorkflowView,
} from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, configuredConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const DIGEST = 'b'.repeat(64)

function briefPayload(): Record<string, unknown> {
  return {
    product_description: '米色亚麻托特包',
    purpose: '电商主图',
    facts: ['用户描述为米色亚麻托特包'],
    creative_direction: '简洁暖色，突出材质',
    style_keywords: ['简洁', '暖色'],
    composition: '居中构图，浅米色背景',
    lighting: '柔和自然光',
    texts: ['春季新品'],
    must_include: ['托特包主体完整'],
    must_preserve: ['亚麻材质纹理'],
    forbidden: ['水印'],
    size: '1024x1024',
    reference_asset_id: null,
    reference_usage: 'none',
    assumptions: [],
    unresolved: [],
  }
}

function finalBrief(): WorkflowArtifact {
  return {
    id: 'artifact-final-17',
    kind: 'final_brief',
    revision_no: 1,
    status: 'approved',
    digest: DIGEST,
    payload: briefPayload(),
    created_at: '2026-10-30T10:00:00.000Z',
  }
}

function approval(): WorkflowApproval {
  return {
    id: 'approval-17',
    approval_type: 'requirements_card',
    artifact_id: 'artifact-final-17',
    artifact_digest: DIGEST,
    approved_by: 'user',
    requirement_snapshot_digest: '',
    referenced_material_digest: '',
    prompt_version: 'not_applicable',
    rules_version: 'v3.rules.2',
    model_fingerprint: 'not_applicable',
    checks: {
      confirmation: {
        artifact_id: 'artifact-final-17',
        artifact_digest: DIGEST,
        artifact_revision_no: 1,
        project_revision: 3,
        requirement_revision: 2,
      },
    },
    created_at: '2026-10-30T10:05:00.000Z',
    revoked_at: null,
    revocation_reason: null,
  }
}

const confirmAction = {
  id: 'confirm_requirements',
  label: '确认需求',
  kind: 'confirm' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: false,
}

function taskState(reasonMessage: string | null = null): WorkflowTaskState {
  return {
    progress: {
      phase: 'requirements',
      stage: 'S06',
      stage_label: '审查需求卡',
      status: 'needs_input',
      awaiting_user: true,
      terminal: false,
    },
    reason: reasonMessage
      ? {
          category: 'missing_information',
          code: 'NEED_BRAND_AUTHORIZATION',
          message: reasonMessage,
          template_id: 'missing_information',
          params: {},
          severity: 'needs_input',
          requires_user_input: true,
          requires_new_version: false,
          requires_evidence: true,
          remote_unknown: false,
          auto_retry: false,
        }
      : null,
    binding: {
      project_id: PROJECT_A,
      run_id: 'run-17',
      project_revision: 3,
      requirement_revision: 2,
      intent_id: null,
      intent_status: null,
      intent_requirement_revision: null,
      generation_job_id: null,
      generation_job_status: null,
      confirmed_snapshot_digest: null,
      execution_snapshot_digest: null,
      delivery_id: null,
      delivery_status: null,
      current: true,
    },
    constraints: [
      {
        constraint_id: 'constraint-text-1',
        path: 'texts',
        label: '图片文字',
        strength: 'hard',
        source: 'user',
        user_lock_basis: null,
        rule_id: 'hard:text_logo',
        value: ['春季新品'],
      },
      {
        constraint_id: 'constraint-style-1',
        path: 'style_keywords',
        label: '风格关键词',
        strength: 'soft',
        source: 'model_suggestion',
        user_lock_basis: null,
        rule_id: null,
        value: ['简洁', '暖色'],
      },
    ],
    resource_state: {
      current_run_id: 'run-17',
      current_state: 'pending',
      current_version: null,
      history: [],
    },
  }
}

function workflowView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-17',
    stage: 'S06',
    status: 'needs_input',
    project_revision: 3,
    requirement_revision: 2,
    intent_route: 'image_request',
    intent_action: 'new_request',
    issue: null,
    artifacts: [finalBrief()],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: null,
    usage: {
      daily_limit: 20,
      used: 0,
      remaining: 20,
      window_started_at: null,
      window_expires_at: null,
      personal_active: 0,
      personal_limit: 1,
      instance_active: 0,
      instance_limit: 2,
      project_active: 0,
      project_limit: 1,
    },
    task_state: taskState(),
    updated_at: '2026-10-30T10:00:00.000Z',
    ...overrides,
  }
}

function mountChat(view: WorkflowView, extraViews: WorkflowView[] = []) {
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    fakeClock: true,
    url: '/projects/' + PROJECT_A + '/chat',
    routes: [
      { method: 'GET', url: '/api/projects/' + PROJECT_A, responses: [{ body: {
        project: sampleProject({
          id: PROJECT_A,
          revision: 3,
          prompt_revision: 3,
          confirmed_revision: null,
          brief_status: 'draft',
        }),
        messages: [],
        generations: [],
      } }] },
      {
        method: 'GET',
        url: '/api/projects/' + PROJECT_A + '/workflow',
        responses: [view, ...extraViews].map((body) => ({ body })),
      },
    ],
  })
}

describe('T17 单一消息流中的需求卡和任务卡', () => {
  it('需求卡明确区分硬约束与软偏好，只提供一个当前有效主确认入口', async () => {
    harness = await mountChat(workflowView({ available_actions: [confirmAction] }))
    const panel = await screen.findByRole('region', { name: '审查需求卡' })

    expect(within(panel).getByRole('heading', { name: '必须满足（硬约束）' })).toBeVisible()
    expect(within(panel).getByRole('heading', { name: '偏好（软约束，可调整）' })).toBeVisible()
    const hard = within(panel).getByRole('region', { name: '必须满足（硬约束）' })
    const soft = within(panel).getByRole('region', { name: '偏好（软约束，可调整）' })
    expect(within(hard).getByText('图片文字')).toBeVisible()
    expect(within(hard).getByText(/春季新品/)).toBeVisible()
    expect(within(soft).getByText('风格关键词')).toBeVisible()
    expect(within(soft).getByText('风格关键词').closest('li')?.textContent).toContain('暖色')
    expect(panel.querySelectorAll('.workflow-confirmation')).toHaveLength(1)
    expect(within(panel).getAllByRole('button', { name: '确认以上需求并生成图片' })).toHaveLength(1)
  })

  it('刷新同一业务事实不会复制需求卡、任务卡或确认入口', async () => {
    harness = await mountChat(workflowView({ available_actions: [confirmAction] }), [
      workflowView({ available_actions: [confirmAction] }),
    ])
    const panel = await screen.findByRole('region', { name: '审查需求卡' })
    await harness.user.click(within(panel).getByRole('button', { name: '刷新' }))
    await harness.waitFor(() => expect(harness!.fetch.count('GET', /\/workflow$/)).toBe(2))
    expect(panel.querySelectorAll('.workflow-confirmation')).toHaveLength(1)
    expect(panel.querySelectorAll('.workflow-task-card')).toHaveLength(1)
    expect(within(panel).getAllByRole('button', { name: '确认以上需求并生成图片' })).toHaveLength(1)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('已确认的当前卡片不再提供重复主确认入口', async () => {
    harness = await mountChat(workflowView({ available_actions: [confirmAction], approvals: [approval()] }))
    const panel = await screen.findByRole('region', { name: '审查需求卡' })

    expect(within(panel).getByText('已确认')).toBeVisible()
    expect(within(panel).queryByRole('button', { name: '确认以上需求并生成图片' })).not.toBeInTheDocument()
  })

  it('任务卡展示服务端安全的进度与原因，不再重复当前问题标题', async () => {
    harness = await mountChat(workflowView({
      task_state: taskState('请补充品牌授权信息后再生成。'),
    }))
    const panel = await screen.findByRole('region', { name: '审查需求卡' })
    expect(within(panel).getByText('任务卡')).toBeVisible()
    expect(within(panel).getByText('请补充品牌授权信息后再生成。')).toBeVisible()
    expect(within(panel).queryByText('当前问题与下一步')).not.toBeInTheDocument()
    expect(within(panel).queryAllByText('当前问题需要处理')).toHaveLength(0)
  })
})
