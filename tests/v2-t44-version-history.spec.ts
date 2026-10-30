import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { WorkflowVersion, WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, configuredConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = '/api/projects/' + PROJECT_A
const workflowPath = detailPath + '/workflow'
const feedbackPath = workflowPath + '/actions/request_new_version'

function version(overrides: Partial<WorkflowVersion> = {}): WorkflowVersion {
  return {
    run_id: 'run-old',
    sequence_no: 1,
    requirement_revision: 1,
    status: 'published',
    generation_job_id: 'job-old',
    asset_id: 'asset-old',
    filename: 'bag-v1.png',
    caption: '已按第一版需求完成检查。',
    published_at: '2026-10-30T00:00:00.000Z',
    brief: {
      product_description: '米色托特包',
      purpose: '电商主图',
      texts: [],
      must_preserve: ['米色'],
    },
    prompt: { positive_prompt: '米色托特包，正面居中，简洁棚拍' },
    quality: { decision: 'pass' },
    can_download: true,
    created_at: '2026-10-30T00:00:00.000Z',
    ...overrides,
  }
}

function workflowView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-old',
    stage: 'S10',
    status: 'completed',
    project_revision: 1,
    requirement_revision: 1,
    intent_route: 'image_request',
    intent_action: 'new_request',
    issue: {
      code: 'DELIVERY_PUBLISHED',
      message: '交付已发布。',
      template_id: 'WAIT_STAGE',
      params: { stage_label: '准备交付' },
    },
    artifacts: [],
    approvals: [],
    available_actions: [
      {
        id: 'request_new_version',
        label: '基于反馈生成新版本',
        kind: 'text',
        requires_text: true,
        text_label: '说明本次修改要求',
        requires_confirmation: false,
      },
    ],
    legacy: [],
    generation_job: null,
    delivery: {
      id: 'delivery-old',
      status: 'published',
      asset_id: 'asset-old',
      filename: 'bag-v1.png',
      caption: '已按第一版需求完成检查。',
      published_at: '2026-10-30T00:00:00.000Z',
      created_at: '2026-10-30T00:00:00.000Z',
    },
    versions: [version()],
    updated_at: '2026-10-30T00:00:00.000Z',
    ...overrides,
  }
}

async function mount(view: WorkflowView) {
  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/?project=' + PROJECT_A,
    routes: [
      {
        method: 'GET',
        url: detailPath,
        responses: [{ body: { project: sampleProject({ id: PROJECT_A }), messages: [], generations: [] } }],
      },
      { method: 'GET', url: workflowPath, responses: [{ body: view }, { body: view }] },
    ],
  })
  const nav = await screen.findByRole('navigation', { name: '工作阶段' })
  await harness.user.click(within(nav).getByRole('button', { name: '描述需求' }))
  await screen.findByRole('heading', { name: '春季托特包主图' })
  return harness
}

describe('T14 反馈生成新版本与历史版本', () => {
  it('逐版显示已发布的正确需求、提示词、批准状态与下载入口', async () => {
    await mount(workflowView())
    expect(await screen.findByRole('heading', { name: '历史版本' })).toBeVisible()
    expect(screen.getByText('第 1 版')).toBeVisible()
    expect(screen.getByText('需求：米色托特包 · 电商主图')).toBeVisible()
    expect(screen.getByText('提示词：米色托特包，正面居中，简洁棚拍')).toBeVisible()
    const download = screen.getByRole('link', { name: '下载第 1 版' })
    expect(download).toHaveAttribute('href', '/api/assets/asset-old?download=true')
    expect(screen.queryByText(/候选/)).not.toBeInTheDocument()
  })

  it('反馈入口明确说明会生成新版本，并只提交反馈文本', async () => {
    const newView = workflowView({
      run_id: 'run-new',
      stage: 'S03',
      status: 'active',
      project_revision: 2,
      requirement_revision: 2,
      issue: null,
      available_actions: [
        {
          id: 'compose_brief',
          label: '整理需求卡',
          kind: 'button',
          requires_text: false,
          text_label: null,
          requires_confirmation: false,
        },
      ],
    })
    await mount(workflowView())
    harness!.fetch.json('POST', feedbackPath, newView)
    expect(await screen.findByText(/反馈会重新确认需求并生成一个新版本/)).toBeVisible()
    const textarea = screen.getByRole('textbox', { name: '说明本次修改要求' })
    await harness!.user.type(textarea, '把背景换成浅灰色')
    await harness!.user.click(screen.getByRole('button', { name: '基于反馈生成新版本' }))
    expect(harness!.fetch.last('POST', feedbackPath)?.body).toEqual({
      expected_revision: 1,
      text: '把背景换成浅灰色',
      payload: {},
    })
    expect(await screen.findByRole('heading', { name: '整理需求' })).toBeVisible()
  })
})
