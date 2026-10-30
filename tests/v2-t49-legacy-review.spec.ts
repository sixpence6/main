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

function view(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: null,
    stage: null,
    status: 'none',
    project_revision: 1,
    requirement_revision: 1,
    intent_route: null,
    intent_action: null,
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

const legacyItem = {
  id: 'legacy-message-1',
  kind: 'message' as const,
  ref_id: 'message-1',
  status: 'legacy_unreviewed' as const,
  note: null,
  reviewed_at: null,
  created_at: '2026-10-30T00:00:00.000Z',
}

describe('T19 主动补审历史内容', () => {
  it('只对用户选择的历史目标发起补审，批准后才显示已补审状态', async () => {
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
                  revision: 1,
                  brief_status: 'draft',
                  legacy_unreviewed: true,
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
          responses: [{ body: view({ legacy: [legacyItem] }) }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/review_legacy_item',
          responses: [
            {
              body: view({
                legacy: [
                  {
                    ...legacyItem,
                    status: 'approved' as const,
                    reviewed_at: '2026-10-30T00:00:01.000Z',
                  },
                ],
              }),
            },
          ],
        },
      ],
    })

    const button = await screen.findByRole('button', { name: '补审此历史内容' })
    fireEvent.click(button)

    expect(await screen.findByText('已补审')).toBeVisible()
    expect(
      harness.fetch.count('POST', new RegExp('/workflow/actions/review_legacy_item$')),
    ).toBe(1)
    expect(harness.fetch.last('POST', /review_legacy_item$/)?.body).toEqual({
      expected_revision: 1,
      text: null,
      payload: { item_id: 'legacy-message-1' },
    })
    expect(harness.fetch.count('POST', /submit_generation/)).toBe(0)
    expect(harness.fetch.count('POST', new RegExp('/generations'))).toBe(0)
  })
})
