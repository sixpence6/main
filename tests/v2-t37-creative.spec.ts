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
    run_id: 'run-creative',
    stage: 'S04',
    status: 'active',
    project_revision: 0,
    requirement_revision: 0,
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

const planCreative = {
  id: 'plan_creative',
  label: '制定创意方向',
  kind: 'button' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: false,
}

const approvedBrief = {
  id: 'brief-1',
  kind: 'brief_draft',
  revision_no: 1,
  status: 'approved' as const,
  digest: 'digest-brief',
  payload: { product_description: '用户提供的陶瓷杯', texts: [] },
  created_at: '2026-10-30T00:00:00.000Z',
}

const candidateCreative = {
  id: 'creative-candidate',
  kind: 'creative_direction',
  revision_no: 1,
  status: 'candidate' as const,
  digest: 'digest-candidate',
  payload: { direction: '未获批创意不得展示', selling_points: [], copy_lines: [] },
  created_at: '2026-10-30T00:00:01.000Z',
}

const approvedCreative = {
  id: 'creative-approved',
  kind: 'creative_direction',
  revision_no: 1,
  status: 'approved' as const,
  digest: 'digest-creative',
  payload: { direction: '简洁背景突出杯子外观', selling_points: [], copy_lines: [] },
  created_at: '2026-10-30T00:00:01.000Z',
}

function projectRoute() {
  return {
    method: 'GET',
    url: '/api/projects/' + PROJECT_A,
    responses: [
      {
        body: {
          project: sampleProject({ id: PROJECT_A, revision: 0, brief_status: 'draft', confirmed_revision: null }),
          messages: [],
          generations: [],
        },
      },
    ],
  }
}

describe('T07 创意方向只在独立审查通过后显示', () => {
  it('用户点击制定创意后，获批方向才出现，且没有提交生图', async () => {
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
                available_actions: [planCreative],
                artifacts: [approvedBrief, candidateCreative],
              }),
            },
          ],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/plan_creative',
          responses: [
            {
              body: workflowView({
                stage: 'S05',
                status: 'active',
                available_actions: [],
                artifacts: [approvedBrief, approvedCreative],
              }),
            },
          ],
        },
      ],
    })

    expect(await screen.findByRole('heading', { name: '策划创意' })).toBeVisible()
    // A candidate with a payload is still never rendered.
    expect(screen.queryByText(/未获批创意不得展示/)).not.toBeInTheDocument()
    expect(screen.queryByText('创意方向')).not.toBeInTheDocument()

    const button = screen.getByRole('button', { name: '制定创意方向' })
    fireEvent.click(button)

    expect(await screen.findByRole('heading', { name: '制定视觉方案' })).toBeVisible()
    expect(await screen.findByText(/简洁背景突出杯子外观/)).toBeVisible()
    expect(screen.queryByText(/未获批创意不得展示/)).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', /\/workflow\/actions\/plan_creative$/)).toBe(1)
    expect(harness.fetch.last('POST', /\/workflow\/actions\/plan_creative$/)?.body).toEqual({
      expected_revision: 0,
      text: null,
      payload: {},
    })
    // The creative stage is text only: no image generation request is made.
    expect(harness.fetch.count('POST', /submit_generation/)).toBe(0)
    expect(harness.fetch.count('POST', /\/generations/)).toBe(0)
  })

  it('审查 unknown 时只显示固定故障文案，候选不渲染且不生图', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        projectRoute(),
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{ body: workflowView({ available_actions: [planCreative], artifacts: [approvedBrief] }) }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/plan_creative',
          responses: [
            {
              body: workflowView({
                status: 'blocked',
                issue: {
                  code: 'REVIEW_UNKNOWN',
                  message: '审查暂不可用，内容尚未放行。',
                  template_id: 'REVIEW_UNAVAILABLE',
                  params: {},
                },
                artifacts: [approvedBrief, candidateCreative],
              }),
            },
          ],
        },
      ],
    })

    const button = await screen.findByRole('button', { name: '制定创意方向' })
    fireEvent.click(button)

    expect(await screen.findByText('审查暂不可用，内容尚未放行。')).toBeVisible()
    expect(screen.queryByText(/未获批创意不得展示/)).not.toBeInTheDocument()
    expect(screen.queryByText('创意方向')).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', /submit_generation/)).toBe(0)
    expect(harness.fetch.count('POST', /\/generations/)).toBe(0)
  })
})
