import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/vue'
import type { WorkflowVersion, WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleAsset,
  sampleGeneration,
  sampleProject,
} from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const RUN_CURRENT = 'run-current'
const RUN_OLD = 'run-old'

function version(overrides: Partial<WorkflowVersion>): WorkflowVersion {
  return {
    run_id: RUN_CURRENT,
    sequence_no: 2,
    requirement_revision: 3,
    status: 'published',
    generation_job_id: 'job-current',
    asset_id: 'asset-current',
    filename: 'current.png',
    width: 1536,
    height: 1024,
    caption: '当前主图',
    published_at: '2026-10-21T06:02:00.000Z',
    brief: { product_description: '米色亚麻托特包' },
    prompt: { positive_prompt: '当前版本的已批准提示词', negative_prompt: '低清晰度' },
    quality: { status: 'passed' },
    can_download: true,
    created_at: '2026-10-21T06:00:00.000Z',
    ...overrides,
  }
}

function completedView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
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
    versions: [
      version({}),
      version({
        run_id: RUN_OLD,
        sequence_no: 1,
        requirement_revision: 2,
        generation_job_id: 'job-old',
        asset_id: 'asset-old',
        filename: 'old.png',
        width: 1024,
        height: 1024,
        caption: '历史主图',
        published_at: '2026-10-20T03:00:00.000Z',
        prompt: { positive_prompt: '旧版本的已批准提示词', negative_prompt: '' },
      }),
    ],
    updated_at: '2026-10-21T06:02:00.000Z',
    ...overrides,
  }
}

function projectDetail() {
  const resultAsset = sampleAsset({
    id: 'asset-current',
    kind: 'generated',
    width: 1536,
    height: 1024,
  })
  return {
    project: sampleProject({
      id: PROJECT_A,
      title: '春季托特包主图',
      revision: 3,
      confirmed_revision: 3,
      brief_status: 'confirmed',
    }),
    messages: [],
    generations: [
      sampleGeneration({
        id: 'generation-current',
        project_id: PROJECT_A,
        version_no: 2,
        confirmed_revision: 3,
        result_asset_id: 'asset-current',
        result_asset: resultAsset,
      }),
    ],
  }
}

function mountResults(query = '', view: WorkflowView = completedView()) {
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/projects/' + PROJECT_A + '/results' + query,
    routes: [
      { method: 'GET', url: '/api/projects/' + PROJECT_A, responses: [{ body: projectDetail() }] },
      { method: 'GET', url: '/api/projects/' + PROJECT_A + '/workflow', responses: [{ body: view }] },
    ],
  })
}

describe('T19 结果页历史与提示词', () => {
  it('历史版本列表与提示词默认折叠，展开后显示真实版本尺寸', async () => {
    harness = await mountResults()
    await screen.findByRole('img', { name: '当前主图' })

    const details = harness.container.querySelectorAll('details')
    expect(details.length).toBeGreaterThanOrEqual(2)
    for (const item of Array.from(details)) expect(item.open).toBe(false)

    const history = screen.getByRole('group', { name: /历史结果/ }) as HTMLDetailsElement
    expect(history.open).toBe(false)
    expect(history.querySelector('summary')?.textContent).toContain('历史结果')
    await harness.user.click(history.querySelector('summary')!)
    await waitFor(() => expect(history.open).toBe(true))

    expect(within(history).getByText('版本 1 · 需求 v2')).toBeVisible()
    expect(within(history).getByText(/1024 × 1024/)).toBeVisible()
    const currentButton = within(history).getByRole('button', { name: /版本 2 · 需求 v3/ })
    expect(currentButton).toHaveAttribute('aria-current', 'true')
  })

  it('历史版本页显示原版本尺寸、历史提示词和当前交付基准', async () => {
    harness = await mountResults('?version=' + RUN_OLD)
    expect(await screen.findByRole('img', { name: '历史主图' })).toBeVisible()

    const note = await screen.findByRole('note')
    expect(note).toHaveTextContent('第 1 版历史成果')
    expect(note).toHaveTextContent('修改仍基于当前第 2 版交付')

    expect(screen.getByText('1024 × 1024')).toBeVisible()
    expect(screen.getByText(/旧版本的已批准提示词/)).toBeInTheDocument()
    expect(screen.queryByText(/当前版本的已批准提示词/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '下载原图' })).toHaveAttribute(
      'href',
      '/api/assets/asset-old?download=true',
    )
    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })

  it('缺失历史提示词时明确说明，不用当前提示词解释旧图', async () => {
    const missingPrompt = completedView({
      versions: [
        version({}),
        version({
          run_id: RUN_OLD,
          sequence_no: 1,
          requirement_revision: 2,
          asset_id: 'asset-old',
          caption: '历史主图',
          prompt: null,
        }),
      ],
    })
    harness = await mountResults('?version=' + RUN_OLD, missingPrompt)
    expect(await screen.findByRole('img', { name: '历史主图' })).toBeVisible()
    expect(screen.getByText('该版本提示词不可用')).toBeVisible()
    expect(screen.getByText(/系统不会用当前提示词替代/)).toBeInTheDocument()
    expect(screen.queryByText(/当前版本的已批准提示词/)).not.toBeInTheDocument()
    expect(screen.queryByText(/旧版本的已批准提示词/)).not.toBeInTheDocument()
  })

  it('请求的版本不可用时只显示安全状态，历史仍可访问且不回退当前图', async () => {
    harness = await mountResults('?version=run-missing')
    expect(await screen.findByText('结果不可用')).toBeVisible()
    expect(harness.container.querySelector('.results-image')).toBeNull()
    expect(screen.getByRole('group', { name: /历史结果/ })).toBeInTheDocument()

    const history = screen.getByRole('group', { name: /历史结果/ }) as HTMLDetailsElement
    await harness.user.click(history.querySelector('summary')!)
    await harness.user.click(within(history).getByRole('button', { name: /版本 1 · 需求 v2/ }))
    await harness.waitFor(() => {
      expect(harness!.router.currentRoute.value.query.version).toBe(RUN_OLD)
    })
    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })
})
