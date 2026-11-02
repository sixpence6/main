import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/vue'
import type { WorkflowVersion, WorkflowView } from '@/api/workflow'
import { sessionRevision } from '@/auth/session'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, PROJECT_B, configuredConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose?.()
  harness = undefined
  sessionRevision.value = 0
})

const RUN_CURRENT = 'run-current'

function currentVersion(): WorkflowVersion {
  return {
    run_id: RUN_CURRENT,
    sequence_no: 2,
    requirement_revision: 3,
    status: 'published',
    generation_job_id: 'job-current',
    asset_id: 'asset-current',
    filename: 'current.png',
    caption: '当前主图',
    published_at: '2026-10-21T06:02:00.000Z',
    brief: { product_description: '米色亚麻托特包' },
    prompt: { positive_prompt: '当前版本的已批准提示词', negative_prompt: '低清晰度' },
    quality: { status: 'passed' },
    can_download: true,
    created_at: '2026-10-21T06:00:00.000Z',
  }
}

function completedView(projectId = PROJECT_A): WorkflowView {
  return {
    project_id: projectId,
    run_id: RUN_CURRENT,
    stage: 'S11',
    status: 'completed',
    project_revision: 3,
    requirement_revision: 3,
    intent_route: null,
    intent_action: null,
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: {
      id: 'delivery-current',
      status: 'published',
      asset_id: 'asset-current',
      filename: 'current.png',
      caption: '当前主图',
      published_at: '2026-10-21T06:02:00.000Z',
      created_at: '2026-10-21T06:02:00.000Z',
    },
    versions: [currentVersion()],
    updated_at: '2026-10-21T06:02:00.000Z',
  }
}

function activeView(projectId = PROJECT_A): WorkflowView {
  return {
    ...completedView(projectId),
    run_id: 'run-active',
    stage: 'S10',
    status: 'active',
    generation_job: {
      id: 'job-active',
      status: 'running',
      stage: 'S10',
      created_at: '2026-10-21T06:00:00.000Z',
      updated_at: '2026-10-21T06:00:00.000Z',
    },
    delivery: null,
    versions: [],
    updated_at: '2026-10-21T06:00:30.000Z',
  }
}

function detail(projectId = PROJECT_A) {
  return {
    project: sampleProject({
      id: projectId,
      title: projectId === PROJECT_B ? '第二个项目' : '春季托特包主图',
      revision: 3,
      confirmed_revision: 3,
      brief_status: 'confirmed',
    }),
    messages: [],
    generations: [],
  }
}

async function mountChat(projectId: string, workflowResponses: WorkflowView[]) {
  return await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    fakeClock: true,
    url: '/projects/' + projectId + '/chat',
    routes: [
      { method: 'GET', url: '/api/projects/' + projectId, responses: [{ body: detail(projectId) }] },
      {
        method: 'GET',
        url: '/api/projects/' + projectId + '/workflow',
        responses: workflowResponses.map((body) => ({ body })),
      },
    ],
  })
}

describe('T16 发布时间跳转与迟到响应隔离', () => {
  it('首次加载已发布结果时以当前状态为基线，不自动跳转', async () => {
    harness = await mountChat(PROJECT_A, [completedView()])
    await screen.findByRole('region', { name: '需求工作台' })
    await harness.clock.advance(3000)
    expect(harness.router.currentRoute.value.name).toBe('project-chat')
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('当前聊天只观察到未发布到已发布的真实变化时跳转一次，重复轮询不重复跳转', async () => {
    harness = await mountChat(PROJECT_A, [activeView(), completedView(), completedView()])
    await screen.findByRole('region', { name: '需求工作台' })
    await harness.clock.advance(2600)
    await harness.waitFor(() => {
      expect(harness!.router.currentRoute.value.name).toBe('project-results')
      expect(harness!.router.currentRoute.value.params.projectId).toBe(PROJECT_A)
    })
    const workflowReads = harness.fetch.count('GET', /\/workflow$/)
    expect(workflowReads).toBeGreaterThanOrEqual(2)
    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })

  it('从结果返回原聊天后同一交付不会再次自动跳转', async () => {
    harness = await mountChat(PROJECT_A, [activeView(), completedView(), completedView()])
    await screen.findByRole('region', { name: '需求工作台' })
    await harness.clock.advance(2600)
    await harness.waitFor(() => expect(harness!.router.currentRoute.value.name).toBe('project-results'))
    await harness.router.push({ name: 'project-chat', params: { projectId: PROJECT_A } })
    await screen.findByRole('region', { name: '需求工作台' })
    await harness.clock.advance(6000)
    expect(harness.router.currentRoute.value.name).toBe('project-chat')
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('直接打开结果或历史地址不会触发发布时间跳转', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      fakeClock: true,
      url: '/projects/' + PROJECT_A + '/results?version=' + RUN_CURRENT,
      routes: [
        { method: 'GET', url: '/api/projects/' + PROJECT_A, responses: [{ body: detail() }] },
        { method: 'GET', url: '/api/projects/' + PROJECT_A + '/workflow', responses: [{ body: completedView() }] },
      ],
    })
    await screen.findByRole('heading', { name: '春季托特包主图', level: 1 })
    await harness.clock.advance(6000)
    expect(harness.router.currentRoute.value.name).toBe('project-results')
    expect(harness.router.currentRoute.value.query.version).toBe(RUN_CURRENT)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('登录生命周期变化后旧会话的发布响应不会触发跳转或抢占路由', async () => {
    harness = await mountChat(PROJECT_A, [activeView(), completedView()])
    await screen.findByRole('region', { name: '需求工作台' })
    sessionRevision.value += 1
    await harness.clock.advance(3000)
    expect(harness.router.currentRoute.value.name).not.toBe('project-results')
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('切换到另一个项目后，旧项目的发布响应不会抢占当前路由', async () => {
    harness = await mountChat(PROJECT_A, [activeView(), completedView(), completedView()])
    await screen.findByRole('region', { name: '需求工作台' })
    harness.fetch.json('GET', '/api/projects/' + PROJECT_B, detail(PROJECT_B))
    harness.fetch.sequence('GET', '/api/projects/' + PROJECT_B + '/workflow', [
      { body: completedView(PROJECT_B) },
    ])
    await harness.router.push({ name: 'project-chat', params: { projectId: PROJECT_B } })
    await harness.clock.advance(6000)
    expect(harness.router.currentRoute.value.name).toBe('project-chat')
    expect(harness.router.currentRoute.value.params.projectId).toBe(PROJECT_B)
    expect(harness.fetch.writes).toHaveLength(0)
  })
})
