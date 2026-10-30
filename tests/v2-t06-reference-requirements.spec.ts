import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen } from '@testing-library/vue'
import type { WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, configuredConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

function workflowView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-requirements',
    stage: 'S03',
    status: 'active',
    project_revision: 2,
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
    updated_at: '2026-10-30T00:00:00.000Z',
    ...overrides,
  }
}

const composeBrief = {
  id: 'compose_brief',
  label: '整理需求卡',
  kind: 'button' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: false,
}

const approvedReference = {
  id: 'reference-report-1',
  kind: 'reference_report',
  revision_no: 1,
  status: 'approved' as const,
  digest: 'digest-reference',
  payload: {
    reference_usage: 'style',
    observations: [
      {
        kind: 'color',
        description: '参考图中的商品杯身为蓝色',
        confidence: 'clear',
        evidence_ids: ['reference:asset-1'],
      },
    ],
  },
  created_at: '2026-10-30T00:00:00.000Z',
}

const approvedBrief = {
  id: 'brief-1',
  kind: 'brief_draft',
  revision_no: 1,
  status: 'approved' as const,
  digest: 'digest-brief',
  payload: {
    product_description: '用户提供的陶瓷杯',
    reference_asset_id: 'asset-1',
    reference_usage: 'style',
    facts: [
      {
        text: '用户声明商品为陶瓷杯',
        basis: 'user_declared',
        evidence_ids: ['user:1'],
      },
    ],
  },
  created_at: '2026-10-30T00:00:01.000Z',
}

const candidateBrief = {
  id: 'brief-candidate',
  kind: 'brief_draft',
  revision_no: 2,
  status: 'candidate' as const,
  digest: 'digest-candidate',
  payload: { product_description: '未获批候选不得展示' },
  created_at: '2026-10-30T00:00:02.000Z',
}

function projectRoute() {
  return {
    method: 'GET',
    url: '/api/projects/' + PROJECT_A,
    responses: [
      {
        body: {
          project: sampleProject({ id: PROJECT_A, revision: 2, brief_status: 'draft', confirmed_revision: null }),
          messages: [],
          generations: [],
        },
      },
    ],
  }
}

describe('T06 参考报告整合后的需求展示', () => {
  it('批准后展示本版参考用途，候选草稿不显示且不生图', async () => {
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
            {
              body: workflowView({
                available_actions: [composeBrief],
                artifacts: [approvedReference],
              }),
            },
          ],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/compose_brief',
          responses: [
            {
              body: workflowView({
                stage: 'S04',
                status: 'active',
                available_actions: [],
                artifacts: [approvedReference, approvedBrief, candidateBrief],
              }),
            },
          ],
        },
      ],
    })

    expect(await screen.findByRole('heading', { name: '整理需求' })).toBeVisible()
    const button = screen.getByRole('button', { name: '整理需求卡' })
    fireEvent.click(button)

    expect(await screen.findByRole('heading', { name: '策划创意' })).toBeVisible()
    expect((await screen.findAllByText(/reference_usage: style/)).length).toBeGreaterThan(0)
    expect(screen.getByText(/reference_asset_id: asset-1/)).toBeVisible()
    expect(screen.queryByText(/未获批候选不得展示/)).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', /submit_generation/)).toBe(0)
    expect(harness.fetch.count('POST', /\/generations/)).toBe(0)
  })

  it('颜色/数量冲突只展示已审阅的集中问题与阻塞状态', async () => {
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
            {
              body: workflowView({
                status: 'needs_input',
                issue: {
                  code: 'CONFLICTING_INPUT',
                  message: '参考图显示杯身为蓝色且有两个杯子，而你描述为红色一个；请确认颜色和数量以哪个为准？',
                  template_id: null,
                  params: {},
                },
                available_actions: [
                  {
                    id: 'answer_requirements_clarification',
                    label: '回答需求问题',
                    kind: 'text',
                    requires_text: true,
                    text_label: '补充当前缺少的商品主体、用途或事实',
                    requires_confirmation: false,
                  },
                ],
                artifacts: [approvedReference],
              }),
            },
          ],
        },
      ],
    })

    expect(
      await screen.findByText(
        '参考图显示杯身为蓝色且有两个杯子，而你描述为红色一个；请确认颜色和数量以哪个为准？',
      ),
    ).toBeVisible()
    expect(screen.getByText('等待补充')).toBeVisible()
    expect(screen.getByRole('textbox', { name: '补充当前缺少的商品主体、用途或事实' })).toBeVisible()
    expect(screen.queryByText(/未获批候选不得展示/)).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', /submit_generation/)).toBe(0)
  })
})
