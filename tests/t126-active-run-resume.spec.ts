import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/vue'
import type { ProjectDetailResponse } from '@/api/types'
import type { WorkflowAction, WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, emptyConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const DETAIL_A = '/api/projects/' + PROJECT_A
const WORKFLOW_A = DETAIL_A + '/workflow'
const ACTIONS_ROUTE = new RegExp('^' + DETAIL_A + '/workflow/actions/[^/]+$')

function detail(): ProjectDetailResponse {
  return {
    project: sampleProject({ id: PROJECT_A, title: '项目甲', revision: 1 }),
    messages: [],
    generations: [],
  }
}

function action(id: string, label: string): WorkflowAction {
  return {
    id,
    label,
    kind: 'button',
    requires_text: false,
    text_label: null,
    requires_confirmation: false,
    effect: 'write',
    read_only: false,
    requires_model_call: false,
    retry_mode: 'none',
    automatic_retry: false,
  }
}

function approvedVisual(): WorkflowView['artifacts'][number] {
  return {
    id: 'artifact-visual-approved',
    kind: 'visual_plan',
    revision_no: 1,
    status: 'approved',
    digest: 'digest-visual-approved',
    payload: { composition: '马克杯居中' },
    created_at: '2026-11-09T04:43:16.413Z',
  }
}

function approvedFinalBrief(): WorkflowView['artifacts'][number] {
  return {
    id: 'artifact-final-approved',
    kind: 'final_brief',
    revision_no: 1,
    status: 'approved',
    digest: 'digest-final-approved',
    payload: { product_description: '白色陶瓷马克杯' },
    created_at: '2026-11-09T04:50:00.000Z',
  }
}

function view(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-s05-resume',
    stage: 'S05',
    status: 'active',
    project_revision: 1,
    requirement_revision: 1,
    intent_route: 'image_request',
    intent_action: 'new_request',
    issue: null,
    artifacts: [approvedVisual()],
    approvals: [],
    available_actions: [
      action('revise_visual', '修改画面方案'),
      action('finalize_requirements', '确认需求并继续'),
    ],
    legacy: [],
    generation_job: null,
    delivery: null,
    versions: [],
    usage: {
      daily_limit: 20,
      used: 1,
      remaining: 19,
      window_started_at: '2026-11-01T00:00:00.000Z',
      window_expires_at: '2026-11-02T00:00:00.000Z',
      personal_active: 1,
      personal_limit: 2,
      instance_active: 1,
      instance_limit: 4,
      project_active: 1,
      project_limit: 1,
    },
    updated_at: '2026-11-09T04:43:16.413Z',
    ...overrides,
  }
}

function afterFinalize(): WorkflowView {
  return view({
    stage: 'S06',
    status: 'active',
    project_revision: 2,
    artifacts: [approvedFinalBrief()],
    available_actions: [action('confirm_requirements', '确认需求卡')],
  })
}

async function mountS05(
  setup: (fetch: WorkspaceHarness['fetch']) => void,
  initial: WorkflowView = view(),
): Promise<WorkspaceHarness> {
  const mounted = await mountWorkspace({
    config: emptyConfig(),
    viewportWidth: 1440,
    url: '/projects/' + PROJECT_A + '/chat',
    setup(fetch) {
      fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
      fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
      fetch.route('GET', WORKFLOW_A, () => ({ body: initial }))
      setup(fetch)
    },
  })
  harness = mounted
  return mounted
}

function actionPosts(mounted: WorkspaceHarness): string[] {
  return mounted.fetch.writes.map((request) => request.path)
}

describe('T126 打开已有 active run 时恢复当前步骤', () => {
  it('初次加载 S05 approved 只自动执行一次 finalize_requirements', async () => {
    let posts = 0
    const mounted = await mountS05((fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, () => {
        posts += 1
        return { body: afterFinalize() }
      })
    })

    await waitFor(() => expect(posts).toBe(1))
    expect(actionPosts(mounted)).toHaveLength(1)
    expect(actionPosts(mounted)[0]).toContain('/workflow/actions/finalize_requirements')

    await new Promise((resolve) => setTimeout(resolve, 200))
    expect(posts).toBe(1)
    expect(actionPosts(mounted)).toHaveLength(1)
  })

  it('路由离开再返回时按当前 project + stage + revision 再恢复一次，不重复发请求', async () => {
    let posts = 0
    const mounted = await mountS05((fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, () => {
        posts += 1
        return { body: afterFinalize() }
      })
    })

    await waitFor(() => expect(posts).toBe(1))
    await mounted.router.push('/')
    await waitFor(() => expect(mounted.router.currentRoute.value.name).toBe('describe'))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(posts).toBe(1)

    await mounted.router.push('/projects/' + PROJECT_A + '/chat')
    await waitFor(() => expect(posts).toBe(2))
    await new Promise((resolve) => setTimeout(resolve, 200))
    expect(posts).toBe(2)
  })

  it('刷新后重新加载仍恢复一次，不因重新挂载产生重复 POST', async () => {
    let posts = 0
    const setup = (fetch: WorkspaceHarness['fetch']): void => {
      fetch.route('POST', ACTIONS_ROUTE, () => {
        posts += 1
        return { body: afterFinalize() }
      })
    }

    const first = await mountS05(setup)
    await waitFor(() => expect(posts).toBe(1))
    first.dispose()
    harness = undefined

    const second = await mountS05(setup)
    await waitFor(() => expect(posts).toBe(2))
    await new Promise((resolve) => setTimeout(resolve, 200))
    expect(posts).toBe(2)
    expect(actionPosts(second)).toHaveLength(1)
  })

  it('409 后不循环请求，并保留可点击的“继续当前步骤”入口', async () => {
    let posts = 0
    const mounted = await mountS05((fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, () => {
        posts += 1
        return {
          status: 409,
          delayMs: 120,
          body: {
            error: {
              code: 'REVISION_CONFLICT',
              message: '项目已被更新，请按最新状态继续。',
            },
          },
        }
      })
    })

    await waitFor(() => expect(posts).toBe(1))
    const resume = await screen.findByRole('button', { name: '继续当前步骤' })
    await waitFor(() => expect(resume).toBeEnabled())
    await new Promise((resolve) => setTimeout(resolve, 200))
    expect(posts).toBe(1)

    await mounted.user.click(resume)
    await mounted.user.click(resume)
    await waitFor(() => expect(posts).toBe(2))
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(posts).toBe(2)
  })
})
