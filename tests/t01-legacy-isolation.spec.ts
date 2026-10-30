import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  emptyConfig,
  sampleAsset,
  sampleBrief,
  sampleGeneration,
  sampleMessage,
  sampleProject,
  sampleSummary,
} from './support/fixtures'
import type { ProjectDetailResponse, ProjectView } from '@/api/types'
import type { WorkflowView } from '@/api/workflow'
import { LEGACY_UNREVIEWED_NOTICE } from '@/utils/format'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = '/api/projects/' + PROJECT_A
const workflowPath = detailPath + '/workflow'
const OLD_ASSISTANT_TEXT = '旧模型正文：未经审查的历史文案'
const OLD_PROMPT = '旧模型提示词：不得出现在任何页面'
const OLD_ASSET_ID = 'asset-old-secret'

function emptyWorkflow(legacy = true): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: null,
    stage: null,
    status: 'none',
    project_revision: 1,
    requirement_revision: 0,
    intent_route: null,
    intent_action: null,
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: legacy
      ? [
          {
            id: 'legacy-1',
            kind: 'generation',
            ref_id: 'generation-old',
            status: 'legacy_unreviewed',
            note: LEGACY_UNREVIEWED_NOTICE,
            reviewed_at: null,
            created_at: '2026-01-01T00:00:00.000Z',
          },
        ]
      : [],
    generation_job: null,
    delivery: null,
    updated_at: '2026-01-01T00:00:00.000Z',
  }
}

function detail(
  project: ProjectView,
  messages: ProjectDetailResponse['messages'] = [],
  generations: ProjectDetailResponse['generations'] = [],
): ProjectDetailResponse {
  return { project, messages, generations }
}

function legacyProject(overrides: Partial<ProjectView> = {}): ProjectView {
  return sampleProject({
    id: PROJECT_A,
    title: '历史托特包项目',
    revision: 1,
    prompt_revision: null,
    confirmed_revision: null,
    brief_status: 'draft',
    brief: sampleBrief({
      product_description: '',
      purpose: '',
      style: '',
      composition: '',
      text_content: '',
      prompt: '',
      reference_asset_id: 'asset-ref',
    }),
    reference_asset: sampleAsset({ id: 'asset-ref', kind: 'reference' }),
    legacy_unreviewed: true,
    legacy_notice: LEGACY_UNREVIEWED_NOTICE,
    ...overrides,
  })
}

async function mountDetail(
  project: ProjectView,
  messages: ProjectDetailResponse['messages'] = [],
  generations: ProjectDetailResponse['generations'] = [],
  url = '/?project=' + PROJECT_A,
): Promise<WorkspaceHarness> {
  const mounted = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url,
    routes: [
      { method: 'GET', url: detailPath, responses: [{ body: detail(project, messages, generations) }] },
      { method: 'GET', url: workflowPath, responses: [{ body: emptyWorkflow(project.legacy_unreviewed) }] },
    ],
  })
  harness = mounted
  return mounted
}

describe('T01 旧产物默认隔离', () => {
  it('项目列表只显示固定未审占位与安全元信息', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    harness.fetch.json('GET', '/api/projects', {
      items: [
        sampleSummary({
          id: PROJECT_A,
          title: '历史托特包项目',
          brief_status: 'confirmed',
          latest_generation_status: 'succeeded',
          legacy_unreviewed: true,
          legacy_notice: LEGACY_UNREVIEWED_NOTICE,
        }),
      ],
      next_cursor: null,
    })

    await harness.user.click(screen.getByRole('button', { name: '已有项目' }))
    const dialog = await screen.findByRole('dialog', { name: '已有项目' })
    const item = await within(dialog).findByRole('button', { name: /历史托特包项目/ })

    expect(within(item).getByText(LEGACY_UNREVIEWED_NOTICE)).toBeVisible()
    expect(within(item).getByText('历史内容尚未审查')).toBeVisible()
    expect(within(item).queryByText('需求已确认')).toBeNull()
    expect(within(item).queryByText('生成：成功')).toBeNull()
    expect(within(item).queryByText(OLD_ASSISTANT_TEXT)).toBeNull()
  })

  it('详情与对话只显示固定占位，保留用户原文、参考图预览和安全元信息', async () => {
    const userText = '<script>alert("旧原文")</script> 这是我当年输入的商品需求'
    const project = legacyProject()
    harness = await mountDetail(project, [
      sampleMessage({ id: 'user-old', sequence_no: 1, role: 'user', content: userText }),
      sampleMessage({
        id: 'assistant-old',
        sequence_no: 2,
        role: 'assistant',
        content: OLD_ASSISTANT_TEXT,
        brief_snapshot: sampleBrief({ prompt: OLD_PROMPT }),
        legacy_unreviewed: true,
        legacy_notice: LEGACY_UNREVIEWED_NOTICE,
      }),
    ])

    await screen.findByRole('heading', { name: '历史托特包项目' })
    const banner = screen.getByRole('status', { name: '历史内容隔离提示' })
    expect(banner).toHaveTextContent(LEGACY_UNREVIEWED_NOTICE)

    const userMessage = screen.getByLabelText('用户的消息')
    expect(userMessage).toHaveTextContent(userText)
    expect(userMessage.querySelector('script')).toBeNull()
    const assistantMessage = screen.getByLabelText('助手的消息')
    expect(within(assistantMessage).getByText(LEGACY_UNREVIEWED_NOTICE)).toBeVisible()
    expect(within(assistantMessage).queryByText(OLD_ASSISTANT_TEXT)).toBeNull()
    expect(screen.queryByText(OLD_PROMPT)).toBeNull()

    const reference = screen.getByRole('img', { name: '参考图' })
    expect(reference).toHaveAttribute('src', '/api/assets/asset-ref')
    expect(screen.queryByText('生成已批准')).toBeNull()

    expect(await screen.findByText('历史内容')).toBeVisible()
    expect(screen.getByText('尚未通过 v2 审查')).toBeVisible()
    expect(harness.fetch.count('GET', '/api/assets/' + OLD_ASSET_ID)).toBe(0)
  })

  it('历史直达结果页自动选择允许的最近成功版，且不渲染或请求旧候选图', async () => {
    const allowedAsset = sampleAsset({
      id: 'asset-new-approved',
      kind: 'generated',
      review_status: 'approved',
      width: 1024,
      height: 1024,
    })
    const oldGeneration = sampleGeneration({
      id: 'generation-old',
      version_no: 1,
      request_id: 'request-old',
      status: 'succeeded',
      result_asset_id: OLD_ASSET_ID,
      result_asset: sampleAsset({
        id: OLD_ASSET_ID,
        kind: 'generated',
        review_status: 'legacy_unreviewed',
      }),
      legacy_unreviewed: true,
      legacy_notice: LEGACY_UNREVIEWED_NOTICE,
    })
    const newGeneration = sampleGeneration({
      id: 'generation-new',
      version_no: 2,
      request_id: 'request-new',
      status: 'succeeded',
      result_asset_id: allowedAsset.id,
      result_asset: allowedAsset,
      legacy_unreviewed: false,
      legacy_notice: null,
      completed_at: '2026-10-22T06:00:30.000Z',
    })
    harness = await mountDetail(
      legacyProject(),
      [],
      [newGeneration, oldGeneration],
      '/results?project=' + PROJECT_A,
    )

    await screen.findByRole('heading', { name: '查看结果' })
    await waitFor(() => expect(harness!.router.currentRoute.value.name).toBe('results'))
    expect(await screen.findByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/' + allowedAsset.id,
    )
    expect(screen.getByText('共 1 个成功版本')).toBeVisible()
    expect(screen.queryByText('第 1 版')).toBeNull()
    expect(screen.queryByRole('img', { name: '第 1 版缩略图' })).toBeNull()

    const imageSources = Array.from(document.querySelectorAll('img')).map((img) => img.getAttribute('src'))
    expect(imageSources).not.toContain('/api/assets/' + OLD_ASSET_ID)
    expect(harness.fetch.count('GET', '/api/assets/' + OLD_ASSET_ID)).toBe(0)
    expect(document.body.textContent).not.toContain(OLD_ASSET_ID)
  })

  it('只有未审历史版本时结果页不展示旧图、缩略图和下载入口', async () => {
    const oldGeneration = sampleGeneration({
      id: 'generation-old',
      version_no: 1,
      request_id: 'request-old',
      status: 'unknown',
      stage: 'done',
      result_asset_id: null,
      result_asset: null,
      legacy_unreviewed: true,
      legacy_notice: LEGACY_UNREVIEWED_NOTICE,
    })
    harness = await mountDetail(legacyProject(), [], [oldGeneration])

    // 没有可展示的已批准版本时，旧直达地址只能安全回落到描述页，不能停留在结果页。
    await screen.findByRole('heading', { name: '历史托特包项目' })
    expect(harness.router.currentRoute.value.name).toBe('describe')
    expect(screen.getByRole('status', { name: '历史内容隔离提示' })).toHaveTextContent(
      LEGACY_UNREVIEWED_NOTICE,
    )
    expect(screen.queryByRole('img', { name: '生成结果图' })).toBeNull()
    expect(screen.queryByRole('link', { name: '下载当前图片' })).toBeNull()
    expect(screen.queryByRole('img', { name: '第 1 版缩略图' })).toBeNull()
    expect(document.body.textContent).not.toContain(OLD_ASSET_ID)
    expect(harness.fetch.count('GET', '/api/assets/' + OLD_ASSET_ID)).toBe(0)
  })
})
