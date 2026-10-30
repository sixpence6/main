import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen } from '@testing-library/vue'
import type { WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleProject,
} from './support/fixtures'

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

describe('T03 工作流入口与批准后一次性展示', () => {
  it('尚未开始时显示说明入口，提交期间与返回后都不展示候选内容，只展示已批准内容', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A,
          responses: [{
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
          }],
        },
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{
            body: workflowView({ available_actions: [submitRequest] }),
          }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/submit_request',
          responses: [{
            delayMs: 60,
            body: workflowView({
              run_id: 'run-1',
              stage: 'S03',
              status: 'active',
              project_revision: 1,
              intent_route: 'image_request',
              intent_action: 'new_request',
              artifacts: [
                {
                  id: 'candidate-1',
                  kind: 'brief_draft',
                  revision_no: 1,
                  status: 'candidate',
                  digest: 'digest-candidate',
                  payload: { label: '候选内容不得展示' },
                  created_at: '2026-10-30T00:00:00.000Z',
                },
                {
                  id: 'approved-1',
                  kind: 'final_brief',
                  revision_no: 1,
                  status: 'approved',
                  digest: 'digest-approved',
                  payload: { product_description: '已批准内容可以展示' },
                  created_at: '2026-10-30T00:00:01.000Z',
                },
              ],
            }),
          }],
        },
      ],
    })

    expect(await screen.findByRole('heading', { name: '当前流程' })).toBeVisible()
    expect(screen.getByText('尚未开始')).toBeVisible()
    const textarea = screen.getByRole('textbox', { name: '描述你想要的商品图' })
    await harness.user.type(textarea, '  做一个米色托特包主图  ')
    const submit = screen.getByRole('button', { name: '开始整理需求' })
    await harness.waitFor(() => expect(submit).toBeEnabled())
    fireEvent.click(submit)

    await harness.waitFor(() => expect(submit).toHaveTextContent('处理中…'))
    expect(screen.queryByText(/候选内容不得展示/)).not.toBeInTheDocument()
    expect(screen.queryByText(/已批准内容可以展示/)).not.toBeInTheDocument()

    expect(await screen.findByText(/已批准内容可以展示/)).toBeVisible()
    expect(screen.queryByText(/候选内容不得展示/)).not.toBeInTheDocument()
    expect(screen.queryByText('需求草稿')).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', /\/workflow\/actions\/submit_request$/)).toBe(1)
    expect(harness.fetch.last('POST', /\/workflow\/actions\/submit_request$/)?.body).toEqual({
      expected_revision: 0,
      text: '做一个米色托特包主图',
      payload: {},
    })
  })
})
