import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, configuredConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const verifyAction = {
  id: 'verify_generation',
  label: '核实原任务',
  kind: 'button' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: false,
}

const recoverAction = {
  id: 'recover_generation',
  label: '找回原结果',
  kind: 'button' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: false,
}

function blockedView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-t68',
    stage: 'S08',
    status: 'blocked',
    project_revision: 3,
    requirement_revision: 3,
    intent_route: 'image_request',
    intent_action: 'new_request',
    issue: {
      code: 'GENERATION_UNKNOWN',
      message: '远端生成结果未知，任务与一次计数已保留。',
      template_id: null,
      params: {},
    },
    artifacts: [],
    approvals: [],
    available_actions: [verifyAction, recoverAction],
    legacy: [],
    generation_job: {
      id: 'job-t68',
      status: 'unknown',
      request_id: '33333333-3333-4333-8333-333333333333',
      submit_attempted: true,
      result_asset_id: null,
      error_code: 'VISUAL_OUTCOME_UNKNOWN',
      error_message: '远端生成结果未知',
      created_at: '2026-11-01T00:00:00.000Z',
      updated_at: '2026-11-01T00:01:00.000Z',
    },
    delivery: null,
    updated_at: '2026-11-01T00:01:00.000Z',
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
          confirmed_revision: 3,
          brief_status: 'confirmed',
        }),
        messages: [],
        generations: [],
      },
    }],
  }
}

describe('T16 原任务核实、结果找回与重启恢复', () => {
  it('阻塞任务只展示核实与找回动作，点击不会提交新的生图任务', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        projectRoute(),
        {
          method: 'GET',
          url: '/api/projects/' + PROJECT_A + '/workflow',
          responses: [{ body: blockedView() }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/verify_generation',
          responses: [{ body: blockedView() }],
        },
        {
          method: 'POST',
          url: '/api/projects/' + PROJECT_A + '/workflow/actions/recover_generation',
          responses: [{ body: blockedView({
            status: 'needs_input',
            stage: 'S10',
            issue: null,
            available_actions: [],
            generation_job: { ...blockedView().generation_job!, status: 'quarantined' },
          }) }],
        },
      ],
    })

    const panel = await screen.findByRole('region', { name: '生成图片' })
    expect(within(panel).getByText(/任务与一次计数已保留/)).toBeVisible()
    await harness.user.click(within(panel).getByRole('button', { name: '核实原任务' }))
    await harness.waitFor(() => {
      expect(harness?.fetch.last('POST', /\/workflow\/actions\/verify_generation$/)).toBeTruthy()
    })
    await harness.user.click(within(panel).getByRole('button', { name: '找回原结果' }))
    await harness.waitFor(() => {
      expect(harness?.fetch.last('POST', /\/workflow\/actions\/recover_generation$/)).toBeTruthy()
    })
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
    expect(harness.fetch.count('POST', /\/workflow\/actions\/submit_generation$/)).toBe(0)
  })
})
