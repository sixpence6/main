import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { WorkflowArtifact, WorkflowUsage, WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, configuredConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.sessionStorage.clear()
})

const DIGEST = 'b'.repeat(64)

const confirmAction = {
  id: 'confirm_requirements',
  label: '确认需求',
  kind: 'button' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: false,
}

function finalBrief(): WorkflowArtifact {
  return {
    id: 'artifact-final-1',
    kind: 'final_brief',
    revision_no: 1,
    status: 'approved',
    digest: DIGEST,
    payload: {
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
    },
    created_at: '2026-10-30T10:00:00.000Z',
  }
}

function usage(overrides: Partial<WorkflowUsage> = {}): WorkflowUsage {
  return {
    daily_limit: 20,
    used: 3,
    remaining: 17,
    window_started_at: '2026-10-30T00:00:00.000Z',
    window_expires_at: '2026-10-31T00:00:00.000Z',
    personal_active: 1,
    personal_limit: 1,
    instance_active: 1,
    instance_limit: 2,
    project_active: 1,
    project_limit: 1,
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
    artifacts: [finalBrief()],
    approvals: [],
    available_actions: [confirmAction],
    legacy: [],
    generation_job: null,
    delivery: null,
    updated_at: '2026-10-30T10:00:00.000Z',
    ...overrides,
  }
}

describe('T13 一次确认生成与本人用量', () => {
  it('v3 确认主操作展示本人额度并携带稳定请求标识', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A,
          responses: [{ body: {
            project: sampleProject({
              id: PROJECT_A,
              revision: 3,
              prompt_revision: 3,
              confirmed_revision: null,
              brief_status: 'draft',
            }),
            messages: [],
            generations: [],
          } }],
        },
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{ body: workflowView({ usage: usage() }) }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/confirm_requirements',
          responses: [{ body: workflowView({
            stage: 'S07',
            available_actions: [],
            usage: usage(),
            intent: {
              id: 'intent-1',
              request_id: '11111111-1111-4111-8111-111111111111',
              status: 'accepted',
              artifact_digest: DIGEST,
              requirement_revision: 2,
              created_at: '2026-10-30T10:05:00.000Z',
            },
          }) }],
        },
      ],
    })

    const panel = await screen.findByRole('region', { name: '审查需求卡' })
    expect(within(panel).getByText(/今日已受理/)).toHaveTextContent('今日已受理 3 / 20 次')
    expect(within(panel).getByText(/进行中：个人/)).toHaveTextContent('个人 1 / 1')
    const button = within(panel).getByRole('button', { name: '确认以上需求并生成图片' })
    await harness.user.click(button)

    await harness.waitFor(() => {
      expect(harness?.fetch.last('POST', /\/workflow\/actions\/confirm_requirements$/)).toBeTruthy()
    })
    const body = harness.fetch.last('POST', /\/workflow\/actions\/confirm_requirements$/)
    const payload = body?.body as { payload?: { request_id?: string } } | undefined
    expect(payload?.payload?.request_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    )
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
    expect(await within(panel).findByText(/本次生成已受理/)).toBeVisible()
  })
})
