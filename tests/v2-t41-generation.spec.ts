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
    run_id: 'run-1',
    stage: 'S08',
    status: 'active',
    project_revision: 1,
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

describe('T11 候选生成结果待审核展示', () => {
  it('审核前只显示待人工审核，不泄露候选图片、私有地址或下载入口', async () => {
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
              project: sampleProject({ id: PROJECT_A, revision: 1, brief_status: 'confirmed', confirmed_revision: 1 }),
              messages: [],
              generations: [],
            },
          }],
        },
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{
            body: workflowView({
              generation_job: {
                id: 'job-1',
                status: 'quarantined',
                request_id: 'request-1',
                submit_attempted: true,
                result_asset_id: 'private-asset-secret',
                error_code: null,
                error_message: null,
                created_at: '2026-10-30T00:00:00.000Z',
                updated_at: '2026-10-30T00:00:01.000Z',
              },
            }),
          }],
        },
      ],
    })

    expect(await screen.findByText('生成任务')).toBeVisible()
    expect(screen.getByText('待人工审核')).toBeVisible()
    expect(screen.getByRole('note')).toHaveTextContent('候选图片已保存在后台，待人工审核')

    const job = screen.getByText('生成任务').closest('.workflow-job')
    expect(job).not.toBeNull()
    expect(job?.querySelector('img')).toBeNull()
    expect(job?.querySelector('a')).toBeNull()
    expect(job?.textContent).not.toContain('private-asset-secret')
    expect(document.body.innerHTML).not.toContain('data:image')
  })
})
