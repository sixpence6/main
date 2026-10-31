import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { WorkflowArtifact, WorkflowUsage, WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, configuredConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const DIGEST = 'c'.repeat(64)
const REQUEST_ID = '22222222-2222-4222-8222-222222222222'

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
    id: 'artifact-final-t66',
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
    created_at: '2026-11-01T00:00:00.000Z',
  }
}

function usage(): WorkflowUsage {
  return {
    daily_limit: 20,
    used: 1,
    remaining: 19,
    window_started_at: '2026-11-01T00:00:00.000Z',
    window_expires_at: '2026-11-02T00:00:00.000Z',
    personal_active: 1,
    personal_limit: 1,
    instance_active: 1,
    instance_limit: 2,
    project_active: 1,
    project_limit: 1,
  }
}

function workflowView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-t66',
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
    updated_at: '2026-11-01T00:00:00.000Z',
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

describe('T14 原意图重放与需求漂移', () => {
  it('刷新后复用同一稳定请求标识，只读查询不提交生成', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        projectRoute(),
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
              id: 'intent-t66',
              request_id: REQUEST_ID,
              status: 'accepted',
              artifact_digest: DIGEST,
              requirement_revision: 2,
              created_at: '2026-11-01T00:00:00.000Z',
            },
          }) }],
        },
      ],
      setupStorage: (storage) => {
        storage.local.setItem(
          ['v3-generation-request', PROJECT_A, 'run-t66', '2', DIGEST].join(':'),
          REQUEST_ID,
        )
      },
    })

    const panel = await screen.findByRole('region', { name: '审查需求卡' })
    expect(within(panel).getByText(/今日已受理/)).toHaveTextContent('今日已受理 1 / 20 次')
    await harness.user.click(within(panel).getByRole('button', { name: '确认以上需求并生成图片' }))
    await harness.waitFor(() => {
      expect(harness?.fetch.last('POST', /\/workflow\/actions\/confirm_requirements$/)).toBeTruthy()
    })
    const body = harness.fetch.last('POST', /\/workflow\/actions\/confirm_requirements$/)
    expect((body?.body as { payload?: { request_id?: string } }).payload?.request_id).toBe(REQUEST_ID)
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
    expect(await within(panel).findByText(/本次生成已受理/)).toBeVisible()
    expect(harness.fetch.count('POST', /\/workflow\/actions\/confirm_requirements$/)).toBe(1)
  })

  it('冲突与需求漂移都显示安全提示，不触发额外模型调用', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        projectRoute(),
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{ body: workflowView({ usage: usage() }) }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/confirm_requirements',
          responses: [{
            status: 409,
            body: {
              error: {
                code: 'INTENT_REPLAY_CONFLICT',
                message: '同一请求标识已绑定不同的生成参数。',
              },
            },
          }],
        },
      ],
    })

    const panel = await screen.findByRole('region', { name: '审查需求卡' })
    await harness.user.click(within(panel).getByRole('button', { name: '确认以上需求并生成图片' }))
    const note = await within(panel).findByRole('note')
    expect(note).toHaveTextContent('本次确认参数与该请求标识已受理的内容不一致')
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
    expect(harness.fetch.count('POST', /\/workflow\/actions\/confirm_requirements$/)).toBe(1)
  })

  it('需求漂移提示引导重新修改需求，不展示虚构最终提示词', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        projectRoute(),
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{ body: workflowView({
            stage: 'S07',
            status: 'blocked',
            issue: {
              code: 'PROMPT_REQUIRES_RECONFIRMATION',
              message: '提示词引入了尚未确认的卖点，未提交生图。',
              template_id: null,
              params: {},
            },
            available_actions: [{
              id: 'revise_requirements',
              label: '修改需求',
              kind: 'text',
              requires_text: true,
              text_label: '需求修改说明',
              requires_confirmation: false,
            }],
            artifacts: [finalBrief()],
          }) }],
        },
      ],
    })

    const panel = await screen.findByRole('region', { name: '编制生图提示词' })
    expect(within(panel).getAllByText(/提示词引入了尚未确认的卖点/).length).toBeGreaterThan(0)
    expect(within(panel).getByRole('button', { name: '修改需求' })).toBeVisible()
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
  })
})
