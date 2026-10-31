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

const closeAction = {
  id: 'close_generation_tracking',
  label: '结束本地跟踪',
  kind: 'confirm' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: true,
}

const startAction = {
  id: 'start_new_generation',
  label: '确认风险并生成新任务',
  kind: 'confirm' as const,
  requires_text: false,
  text_label: null,
  requires_confirmation: true,
}

function baseView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-t69',
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
    available_actions: [closeAction],
    legacy: [],
    generation_job: {
      id: 'job-t69',
      status: 'unknown',
      request_id: '44444444-4444-4444-8444-444444444444',
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

function closedView(): WorkflowView {
  return baseView({
    issue: {
      code: 'LOCAL_TRACKING_CLOSED',
      message: '本地跟踪已结束，原受理计数已保留；本系统未取消远端任务，远端任务可能仍在执行或计费，也不会自动续发。',
      template_id: null,
      params: {},
    },
    available_actions: [startAction],
  })
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

describe('T17 本地跟踪结案与风险确认新任务', () => {
  it('结案保留计数且不声称取消远端，随后仅以新确认开启新任务', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [projectRoute()],
      setup(fetch) {
        let closed = false
        fetch.route('GET', '/api/projects/' + PROJECT_A + '/workflow', () => ({
          body: closed ? closedView() : baseView(),
        }))
        fetch.route('POST', '/api/projects/' + PROJECT_A + '/workflow/actions/close_generation_tracking', () => {
          closed = true
          return { body: closedView() }
        })
        fetch.route('POST', '/api/projects/' + PROJECT_A + '/workflow/actions/start_new_generation', () => ({
          body: baseView({ status: 'active', issue: null, available_actions: [] }),
        }))
      },
    })

    await harness.waitFor(() => {
      expect(harness?.fetch.count('GET', /\/workflow$/)).toBe(2)
    })
    const closeConfirm = await screen.findByLabelText(/原受理计数保留/)
    await harness.user.click(closeConfirm)
    await harness.waitFor(() => expect((closeConfirm as HTMLInputElement).checked).toBe(true))
    await harness.user.click(screen.getByRole('button', { name: '结束本地跟踪' }))
    await harness.waitFor(() => {
      expect(harness?.fetch.last('POST', /\/workflow\/actions\/close_generation_tracking$/)).toBeTruthy()
    })
    const closeRequest = harness.fetch.last(
      'POST',
      /\/workflow\/actions\/close_generation_tracking$/,
    )
    expect(closeRequest?.body).toMatchObject({
      expected_revision: 3,
      payload: { acknowledge_local_close: true },
    })
    expect((closeRequest?.body as { payload: Record<string, unknown> }).payload).not.toHaveProperty('acknowledge_risk')

    expect((await screen.findAllByText(/本系统未取消远端任务/))[0]).toBeVisible()
    const startConfirm = await screen.findByLabelText(/旧任务仍可能在远端执行或计费/)
    await harness.user.click(startConfirm)
    await harness.user.click(screen.getByRole('button', { name: '确认风险并生成新任务' }))
    await harness.waitFor(() => {
      expect(harness?.fetch.last('POST', /\/workflow\/actions\/start_new_generation$/)).toBeTruthy()
    })
    const startRequest = harness.fetch.last(
      'POST',
      /\/workflow\/actions\/start_new_generation$/,
    )
    expect(startRequest?.body).toMatchObject({
      expected_revision: 3,
      payload: { acknowledge_risk: true },
    })
    expect((startRequest?.body as { payload: Record<string, unknown> }).payload).not.toHaveProperty('acknowledge_local_close')
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
  })
})
