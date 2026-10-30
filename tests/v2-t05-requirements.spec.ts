import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/vue'
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
    run_id: null,
    stage: null,
    status: 'none',
    project_revision: 0,
    requirement_revision: 0,
    intent_route: null,
    intent_action: null,
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: null,
    updated_at: null,
    ...overrides,
  }
}

const submitRequest = {
  id: 'submit_request',
  label: '开始整理需求',
  kind: 'text' as const,
  requires_text: true,
  text_label: '描述你想要的商品图',
  requires_confirmation: false,
}

const composeBrief = {
  id: 'compose_brief',
  label: '整理需求卡',
  kind: 'button' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: false,
}

const answerQuestion = {
  id: 'answer_requirements_clarification',
  label: '回答需求问题',
  kind: 'text' as const,
  requires_text: true,
  text_label: '补充当前缺少的商品主体、用途或事实',
  requires_confirmation: false,
}

const briefPayload = {
  product_description: '米色托特包',
  purpose: '电商商品主图',
  facts: [
    {
      text: '用户声明商品为米色托特包',
      basis: 'user_declared',
      evidence_ids: ['message-1'],
    },
  ],
  creative_direction: null,
  style_keywords: [],
  composition: null,
  lighting: null,
  texts: [],
  must_include: ['一只米色托特包'],
  must_preserve: [],
  forbidden: [],
  size: '1024x1024',
  reference_asset_id: null,
  reference_usage: 'none',
  assumptions: ['建议使用简洁背景'],
  unresolved: [],
}

describe('T05 需求草稿与集中澄清', () => {
  it('展示独立审查通过的需求草稿，并明确它不是最终确认或生成授权', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A,
          responses: [
            {
              body: {
                project: sampleProject({
                  id: PROJECT_A,
                  revision: 0,
                  brief_status: 'draft',
                  prompt_revision: null,
                  confirmed_revision: null,
                }),
                messages: [],
                generations: [],
              },
            },
          ],
        },
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{ body: workflowView({ available_actions: [submitRequest] }) }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/submit_request',
          responses: [
            {
              body: workflowView({
                run_id: 'run-1',
                stage: 'S03',
                status: 'active',
                project_revision: 1,
                intent_route: 'image_request',
                intent_action: 'new_request',
                available_actions: [composeBrief],
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
                run_id: 'run-1',
                stage: 'S04',
                status: 'active',
                project_revision: 1,
                intent_route: 'image_request',
                intent_action: 'new_request',
                artifacts: [
                  {
                    id: 'candidate-1',
                    kind: 'brief_draft',
                    revision_no: 2,
                    status: 'candidate',
                    digest: 'candidate-digest',
                    payload: { product_description: '未审候选不得展示' },
                    created_at: '2026-10-30T00:00:00.000Z',
                  },
                  {
                    id: 'draft-1',
                    kind: 'brief_draft',
                    revision_no: 1,
                    status: 'approved',
                    digest: 'approved-digest',
                    payload: briefPayload,
                    created_at: '2026-10-30T00:00:01.000Z',
                  },
                ],
              }),
            },
          ],
        },
      ],
    })

    expect(await screen.findByRole('heading', { name: '当前流程' })).toBeVisible()
    const textarea = screen.getByRole('textbox', { name: '描述你想要的商品图' })
    await harness.user.type(textarea, '做一个米色托特包主图')
    const submit = screen.getByRole('button', { name: '开始整理需求' })
    await harness.waitFor(() => expect(submit).toBeEnabled())
    await harness.user.click(submit)

    const compose = await screen.findByRole('button', { name: '整理需求卡' })
    expect(screen.queryByText(/未审候选不得展示/)).not.toBeInTheDocument()
    await harness.user.click(compose)

    expect(await screen.findByText(/需求草稿已通过独立审查/)).toBeVisible()
    expect(screen.getByText(/尚未成为最终需求卡，也未获得用户确认或生成授权/)).toBeVisible()
    expect(screen.getByText('需求草稿')).toBeVisible()
    expect(screen.getByText('product_description: 米色托特包')).toBeVisible()
    expect(screen.getByText('purpose: 电商商品主图')).toBeVisible()
    expect(screen.getByText('texts: []')).toBeVisible()
    expect(screen.getByText('size: 1024x1024')).toBeVisible()
    expect(screen.queryByText(/未审候选不得展示/)).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', /submit_generation/)).toBe(0)
    expect(harness.fetch.last('POST', /compose_brief$/)?.body).toEqual({
      expected_revision: 1,
      text: null,
      payload: {},
    })
  })

  it('只展示审查通过后的集中问题，候选与提交流程保持隔离', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A,
          responses: [
            {
              body: {
                project: sampleProject({
                  id: PROJECT_A,
                  revision: 0,
                  brief_status: 'draft',
                  prompt_revision: null,
                  confirmed_revision: null,
                }),
                messages: [],
                generations: [],
              },
            },
          ],
        },
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{ body: workflowView({ available_actions: [submitRequest] }) }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/submit_request',
          responses: [
            {
              body: workflowView({
                run_id: 'run-1',
                stage: 'S03',
                status: 'active',
                project_revision: 1,
                available_actions: [composeBrief],
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
                run_id: 'run-1',
                stage: 'S03',
                status: 'needs_input',
                project_revision: 1,
                issue: {
                  code: 'MISSING_INPUT',
                  message: '请说明要展示的商品主体是什么？',
                  template_id: null,
                  params: {},
                },
                artifacts: [
                  {
                    id: 'candidate-1',
                    kind: 'brief_draft',
                    revision_no: 1,
                    status: 'candidate',
                    digest: 'candidate-digest',
                    payload: { product_description: '未审候选不得展示' },
                    created_at: '2026-10-30T00:00:00.000Z',
                  },
                ],
                available_actions: [answerQuestion],
              }),
            },
          ],
        },
      ],
    })

    expect(await screen.findByRole('heading', { name: '当前流程' })).toBeVisible()
    const textarea = screen.getByRole('textbox', { name: '描述你想要的商品图' })
    await harness.user.type(textarea, '做一张商品图')
    const submit = screen.getByRole('button', { name: '开始整理需求' })
    await harness.waitFor(() => expect(submit).toBeEnabled())
    await harness.user.click(submit)

    const compose = await screen.findByRole('button', { name: '整理需求卡' })
    await harness.user.click(compose)

    expect(await screen.findByText('请说明要展示的商品主体是什么？')).toBeVisible()
    expect(screen.getByText('等待补充')).toBeVisible()
    expect(screen.getByText('需要补充的信息')).toBeVisible()
    expect(
      screen.getByRole('textbox', { name: '补充当前缺少的商品主体、用途或事实' }),
    ).toBeVisible()
    expect(screen.queryByText(/未审候选不得展示/)).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', /submit_generation/)).toBe(0)
  })
})
