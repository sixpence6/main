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

const LEGACY_PATHS = ['/describe', '/confirm', '/generate', '/results']

function workflowView(): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-t77-gate',
    stage: 'S01',
    status: 'active',
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
    delivery: null,
    updated_at: '2026-11-01T00:00:00.000Z',
  }
}

function mountAt(path: string, viewportWidth = 1440) {
  const detail = {
    project: sampleProject({ id: PROJECT_A, title: 'R3 关口托特包', revision: 3, brief_status: 'draft' }),
    messages: [],
    generations: [],
  }
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth,
    url: path + '?project=' + PROJECT_A,
    routes: [
      { method: 'GET', url: '/api/projects/' + PROJECT_A, responses: [{ body: detail }] },
      { method: 'GET', url: '/api/projects/' + PROJECT_A + '/workflow', responses: [{ body: workflowView() }] },
    ],
  })
}

async function expectWorkbench() {
  await harness!.waitFor(() => {
    expect(harness!.router.currentRoute.value.name).toBe('describe')
    expect(harness!.router.currentRoute.value.query.project).toBe(PROJECT_A)
  })
  expect(await screen.findByRole('heading', { name: 'R3 关口托特包' })).toBeVisible()
  expect(await screen.findByRole('region', { name: '需求工作台' })).toBeVisible()
  expect(screen.queryByRole('navigation', { name: '工作阶段' })).not.toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: '查看结果' })).not.toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: '确认需求' })).not.toBeInTheDocument()
}

describe('T25 R3 多视口验收与旧四页收缩关口', () => {
  for (const path of LEGACY_PATHS) {
    it('外部旧地址 ' + path + ' 无副作用重定向到工作台', async () => {
      harness = await mountAt(path)
      await expectWorkbench()
      expect(window.location.pathname).toBe('/')
      expect(harness.fetch.writes).toHaveLength(0)
      expect(harness.fetch.count('GET', /\/generations(?:\/|$)/)).toBe(0)
      expect(harness.fetch.count('GET', /\/assets\//)).toBe(0)
      expect(document.body.textContent).not.toContain('结果已过保留期')
    })
  }

  it('旧生成地址携带旧动作参数也不会重放动作或请求结果', async () => {
    harness = await mountAt('/generate?project=' + PROJECT_A + '&action=start_new_generation&request_id=legacy-request')
    await expectWorkbench()
    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
    expect(harness.fetch.count('GET', /\/generations(?:\/|$)/)).toBe(0)
    expect(harness.fetch.count('GET', /\/assets\//)).toBe(0)
  })

  for (const width of [320, 768, 1440]) {
    it('工作台在 ' + width + ' CSS px 下保持可操作语义与控制标签', async () => {
      harness = await mountAt('/', width)
      await expectWorkbench()
      const workbench = screen.getByRole('region', { name: '需求工作台' })
      expect(workbench.querySelectorAll('.u1-column')).toHaveLength(2)
      expect(workbench.querySelector('.u1-conversation')).not.toBeNull()
      expect(workbench.querySelector('.u1-requirement')).not.toBeNull()
      const composer = screen.getByLabelText('描述商品与图片需求')
      expect(composer).toBeVisible()
      composer.focus()
      expect(composer).toHaveFocus()
      expect(screen.queryByRole('navigation', { name: '工作阶段' })).not.toBeInTheDocument()
      expect(harness.fetch.writes).toHaveLength(0)
    })
  }
})
