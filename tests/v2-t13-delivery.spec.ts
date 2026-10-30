import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/vue'
import type { WorkflowDelivery, WorkflowView } from '@/api/workflow'
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
    run_id: 'run-t13',
    stage: 'S10',
    status: 'needs_input',
    project_revision: 1,
    requirement_revision: 1,
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

function delivery(status: WorkflowDelivery['status']): WorkflowDelivery {
  return {
    id: 'delivery-1',
    status,
    asset_id: 'asset-published-1',
    filename: 'delivery-asset-pub-r1.png',
    caption: '主图交付，请以实际检查结论为准。',
    published_at: status === 'published' ? '2026-10-30T01:00:00.000Z' : null,
    created_at: '2026-10-30T00:00:00.000Z',
  }
}

async function mountDelivery(status: WorkflowDelivery['status']): Promise<WorkspaceHarness> {
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/?project=' + PROJECT_A,
    routes: [
      {
        method: 'GET',
        url: '/api/projects/' + PROJECT_A,
        responses: [{
          body: {
            project: sampleProject({ id: PROJECT_A, revision: 1 }),
            messages: [],
            generations: [],
          },
        }],
      },
      {
        method: 'GET',
        url: '/api/projects/' + PROJECT_A + '/workflow',
        responses: [{ body: workflowView({ delivery: delivery(status) }) }],
      },
    ],
  })
}

describe('T13 交付状态与受控预览下载', () => {
  it('待发布时只显示状态，不提前展示候选图片或下载入口', async () => {
    harness = await mountDelivery('approved')

    expect(await screen.findByRole('heading', { name: '准备交付' })).toBeVisible()
    expect(screen.getByText('待发布')).toBeVisible()
    expect(screen.getByText('交付尚未发布，暂不提供查看或下载。')).toBeVisible()
    expect(document.querySelector('.workflow-delivery-image')).toBeNull()
    expect(screen.queryByRole('link', { name: '下载已发布图片' })).toBeNull()
  })

  it('已发布时使用受控资产地址提供真实预览和下载，并显示发布状态', async () => {
    harness = await mountDelivery('published')

    expect(await screen.findByRole('heading', { name: '准备交付' })).toBeVisible()
    expect(screen.getByText('已发布')).toBeVisible()
    const image = screen.getByRole('img', { name: /主图交付/ })
    expect(image).toHaveAttribute('src', '/api/assets/asset-published-1')
    const link = screen.getByRole('link', { name: '下载已发布图片' })
    expect(link).toHaveAttribute('href', '/api/assets/asset-published-1?download=true')
    expect(link).toHaveAttribute('download', 'delivery-asset-pub-r1.png')
    expect(screen.queryByText('交付尚未发布，暂不提供查看或下载。')).toBeNull()
  })

  it('交付不可用时固定提示且不保留任何图片或下载入口', async () => {
    harness = await mountDelivery('revoked')

    expect(await screen.findByRole('heading', { name: '准备交付' })).toBeVisible()
    expect(screen.getByText('不可用')).toBeVisible()
    expect(
      screen.getByText('当前内容不可访问或批准已失效，请检查状态或重新审查。'),
    ).toBeVisible()
    expect(document.querySelector('.workflow-delivery-image')).toBeNull()
    expect(screen.queryByRole('link', { name: '下载已发布图片' })).toBeNull()
  })
})
