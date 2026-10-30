import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type {
  WorkflowApproval,
  WorkflowArtifact,
  WorkflowView,
} from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, configuredConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const DIGEST = 'a'.repeat(64)

const confirmAction = {
  id: 'confirm_requirements',
  label: '确认需求',
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
  text_label: '说明需要修改的需求内容',
  requires_confirmation: false,
}

function briefPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    product_description: '米色亚麻托特包',
    purpose: '电商主图',
    facts: ['用户描述为米色亚麻托特包'],
    creative_direction: '简洁暖色，突出材质',
    style_keywords: ['简洁', '暖色'],
    composition: '居中构图，浅米色背景',
    lighting: '柔和自然光',
    texts: ['春季新品'],
    must_include: ['托特包主体完整', '提手可见'],
    must_preserve: ['亚麻材质纹理'],
    forbidden: ['水印', '夸张阴影'],
    size: '1024x1024',
    reference_asset_id: null,
    reference_usage: 'none',
    assumptions: ['未指定品牌'],
    unresolved: [],
    ...overrides,
  }
}

function finalBrief(overrides: Partial<WorkflowArtifact> = {}): WorkflowArtifact {
  return {
    id: 'artifact-final-1',
    kind: 'final_brief',
    revision_no: 1,
    status: 'approved',
    digest: DIGEST,
    payload: briefPayload(),
    created_at: '2026-10-30T10:00:00.000Z',
    ...overrides,
  }
}

function confirmationApproval(
  overrides: Partial<WorkflowApproval> = {},
  snapshot: Record<string, unknown> = {},
): WorkflowApproval {
  return {
    id: 'approval-1',
    approval_type: 'requirements_card',
    artifact_id: 'artifact-final-1',
    artifact_digest: DIGEST,
    approved_by: 'user',
    requirement_snapshot_digest: DIGEST,
    referenced_material_digest: DIGEST,
    prompt_version: 'not_applicable',
    rules_version: '2.0.0',
    model_fingerprint: 'not_applicable',
    checks: {
      confirmation: {
        artifact_id: 'artifact-final-1',
        artifact_digest: DIGEST,
        artifact_revision_no: 1,
        project_revision: 3,
        requirement_revision: 2,
        ...snapshot,
      },
    },
    created_at: '2026-10-30T10:05:00.000Z',
    revoked_at: null,
    revocation_reason: null,
    ...overrides,
  }
}

function workflowView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-1',
    stage: 'S06',
    status: 'active',
    project_revision: 3,
    requirement_revision: 2,
    intent_route: 'image_request',
    intent_action: 'new_request',
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: null,
    updated_at: '2026-10-30T10:00:00.000Z',
    ...overrides,
  }
}

function projectRoute() {
  return {
    method: 'GET',
    url: '/api/projects/' + PROJECT_A,
    responses: [{
      body: {
        project: sampleProject({
          id: PROJECT_A,
          revision: 3,
          prompt_revision: 3,
          confirmed_revision: null,
          brief_status: 'draft',
        }),
        messages: [],
        generations: [],
      },
    }],
  }
}

describe('T09 最终需求卡确认', () => {
  it('展示当前完整需求卡与待确认状态，确认时绑定卡片摘要和版本且不触发生成', async () => {
    const before = workflowView({
      artifacts: [finalBrief()],
      available_actions: [confirmAction],
    })
    const after = workflowView({
      stage: 'S07',
      artifacts: [finalBrief()],
      approvals: [confirmationApproval()],
      available_actions: [],
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        projectRoute(),
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{ body: before }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/confirm_requirements',
          responses: [{ delayMs: 40, body: after }],
        },
      ],
    })

    const panel = await screen.findByRole('region', { name: '审查需求卡' })
    expect(within(panel).getByText('需求待确认')).toBeVisible()
    expect(within(panel).getByRole('heading', { name: '当前最终需求卡' })).toBeVisible()
    expect(within(panel).getByText('米色亚麻托特包')).toBeVisible()
    expect(within(panel).getByText('1024x1024')).toBeVisible()
    expect(within(panel).getAllByText(/第 1 次修订/).length).toBeGreaterThan(0)
    expect(within(panel).getAllByText(new RegExp('摘要 ' + DIGEST.slice(0, 12))).length).toBeGreaterThan(0)
    expect(within(panel).getByText(/项目版本 3 · 需求版本 2/)).toBeVisible()

    const button = within(panel).getByRole('button', { name: '确认需求' })
    expect(button).toBeEnabled()
    expect(button).toHaveAttribute('aria-busy', 'false')
    await harness.user.click(button)

    await harness.waitFor(() => {
      expect(harness?.fetch.last('POST', /\/workflow\/actions\/confirm_requirements$/)).toBeTruthy()
    })
    expect(harness.fetch.last('POST', /\/workflow\/actions\/confirm_requirements$/ )?.body).toEqual({
      expected_revision: 3,
      text: null,
      payload: {
        artifact_id: 'artifact-final-1',
        artifact_digest: DIGEST,
        requirement_revision: 2,
      },
    })

    expect(await within(panel).findByText('已确认')).toBeVisible()
    expect(within(panel).queryByRole('button', { name: '确认需求' })).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', /\/workflow\/actions\/confirm_requirements$/)).toBe(1)
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
    expect(harness.fetch.writes).toHaveLength(1)
  })

  it('原确认撤销后显示需重新确认，不把旧确认当作当前批准', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        projectRoute(),
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{
            body: workflowView({
              artifacts: [finalBrief()],
              approvals: [confirmationApproval({
                revoked_at: '2026-10-30T10:10:00.000Z',
                revocation_reason: 'requirements_changed',
              })],
              available_actions: [confirmAction],
            }),
          }],
        },
      ],
    })

    const panel = await screen.findByRole('region', { name: '审查需求卡' })
    expect(within(panel).getByText('需重新确认')).toBeVisible()
    expect(within(panel).getByText(/原确认已失效/)).toBeVisible()
    expect(within(panel).queryByRole('button', { name: '确认需求' })).not.toBeInTheDocument()
  })

  it('未通过独立审查的需求卡不展示完整内容、确认入口或未发布图片', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        projectRoute(),
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{
            body: workflowView({
              artifacts: [finalBrief({
                status: 'candidate',
                payload: briefPayload({ product_description: '未审查候选内容不得展示' }),
              })],
              available_actions: [confirmAction],
              generation_job: {
                id: 'job-1',
                status: 'succeeded',
                request_id: 'request-1',
                submit_attempted: true,
                result_asset_id: 'asset-candidate',
                error_code: null,
                error_message: null,
                created_at: '2026-10-30T10:00:00.000Z',
                updated_at: '2026-10-30T10:01:00.000Z',
              },
              delivery: {
                id: 'delivery-1',
                status: 'approved',
                asset_id: 'asset-candidate',
                filename: 'candidate.png',
                caption: '未经发布审查的图片',
                published_at: null,
                created_at: '2026-10-30T10:01:00.000Z',
              },
            }),
          }],
        },
      ],
    })

    const panel = await screen.findByRole('region', { name: '审查需求卡' })
    expect(within(panel).getByText('需求卡待审查')).toBeVisible()
    expect(within(panel).getByText(/不能提前确认/)).toBeVisible()
    expect(within(panel).getByText('图片待审查')).toBeVisible()
    expect(within(panel).queryByText('未审查候选内容不得展示')).not.toBeInTheDocument()
    expect(within(panel).queryByRole('button', { name: '确认需求' })).not.toBeInTheDocument()
    expect(within(panel).queryByRole('img')).not.toBeInTheDocument()
    expect(within(panel).queryByRole('link', { name: /下载/ })).not.toBeInTheDocument()
    expect(within(panel).getByText(/交付尚未发布/)).toBeVisible()
  })

  it('确认冲突显示服务端安全文案并重新读取当前流程', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        projectRoute(),
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [
            { body: workflowView({ artifacts: [finalBrief()], available_actions: [confirmAction] }) },
            {
              body: workflowView({
                artifacts: [finalBrief()],
                approvals: [confirmationApproval({
                  revoked_at: '2026-10-30T10:10:00.000Z',
                  revocation_reason: 'requirements_changed',
                })],
                available_actions: [confirmAction],
              }),
            },
          ],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/confirm_requirements',
          responses: [{
            status: 409,
            body: {
              error: {
                code: 'REQUIREMENTS_CHANGED',
                message: '需求已变化，请刷新后重新确认当前版本。',
              },
            },
          }],
        },
      ],
    })

    const panel = await screen.findByRole('region', { name: '审查需求卡' })
    const button = within(panel).getByRole('button', { name: '确认需求' })
    await harness.user.click(button)

    expect(await within(panel).findByText('需求已变化，请刷新后重新确认当前版本。')).toBeVisible()
    await harness.waitFor(() => expect(harness?.fetch.count('GET', '/api/projects/' + PROJECT_A + '/workflow')).toBe(2))
    expect(await within(panel).findByText('需重新确认')).toBeVisible()
  })

  it('确认后修改需求通过 v2 工作流动作发起并进入 S03 重新审查', async () => {
    const before = workflowView({
      stage: 'S07',
      artifacts: [finalBrief()],
      approvals: [confirmationApproval()],
      available_actions: [reviseAction],
    })
    const after = workflowView({
      stage: 'S03',
      status: 'active',
      project_revision: 4,
      requirement_revision: 3,
      artifacts: [finalBrief()],
      approvals: [confirmationApproval({
        revoked_at: '2026-10-30T10:10:00.000Z',
        revocation_reason: 'requirements_changed',
      })],
      available_actions: [],
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        projectRoute(),
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{ body: before }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/revise_requirements',
          responses: [{ delayMs: 40, body: after }],
        },
      ],
    })

    const panel = await screen.findByRole('region', { name: '编制生图提示词' })
    const textarea = within(panel).getByLabelText('说明需要修改的需求内容')
    const editText = '改成黑色帆布托特包，保留简洁暖色风格，图片文字去掉。'
    await harness.user.type(textarea, editText)
    await harness.user.click(within(panel).getByRole('button', { name: '修改需求' }))

    await harness.waitFor(() => {
      expect(harness?.fetch.last('POST', /\/workflow\/actions\/revise_requirements$/)).toBeTruthy()
    })
    expect(harness.fetch.last('POST', /\/workflow\/actions\/revise_requirements$/)?.body).toEqual({
      expected_revision: 3,
      text: editText,
      payload: {},
    })
    expect(await within(panel).findByRole('heading', { name: '整理需求' })).toBeVisible()
    expect(within(panel).getByText('需重新确认')).toBeVisible()
    expect(within(panel).getByText(/原确认已失效/)).toBeVisible()
    expect(within(panel).queryByRole('button', { name: '修改需求' })).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
  })
})
