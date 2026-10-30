import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  PROJECT_B,
  configuredConfig,
  sampleAsset,
  sampleBrief,
  sampleGeneration,
  sampleMessage,
  sampleProject,
} from './support/fixtures'
import type { GenerationView, ProjectDetailResponse, ProjectView } from '@/api/types'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPathA = '/api/projects/' + PROJECT_A
const detailPathB = '/api/projects/' + PROJECT_B
const messagesPathA = detailPathA + '/messages'
const confirmPathA = detailPathA + '/confirm'
const generationsPathA = detailPathA + '/generations'
const generationPath2 = '/api/generations/generation-2'
const referencePathA = detailPathA + '/reference'

function detail(project: ProjectView, generations: GenerationView[] = []): ProjectDetailResponse {
  return { project, messages: [], generations }
}

function currentProject(overrides: Partial<ProjectView> = {}): ProjectView {
  return sampleProject({
    id: PROJECT_A,
    title: '春季托特包主图',
    brief_status: 'confirmed',
    revision: 5,
    prompt_revision: 5,
    confirmed_revision: 5,
    brief: sampleBrief({
      product_description: '当前最新商品描述',
      style: '当前最新风格',
      reference_asset_id: 'ref-asset',
    }),
    reference_asset: sampleAsset({ id: 'ref-asset', kind: 'reference' }),
    ...overrides,
  })
}

function assetFor(version: number, overrides = {}) {
  return sampleAsset({
    id: 'asset-v' + version,
    kind: 'generated',
    width: 1024,
    height: 1024 + version,
    ...overrides,
  })
}

function succeededVersion(version: number, overrides: Partial<GenerationView> = {}): GenerationView {
  const asset = assetFor(version)
  return sampleGeneration({
    id: 'generation-' + version,
    version_no: version,
    request_id: 'request-' + version,
    confirmed_revision: 3,
    status: 'succeeded',
    stage: 'done',
    brief_snapshot: sampleBrief({
      product_description: '第 ' + version + ' 版商品描述',
      style: '第 ' + version + ' 版风格',
    }),
    prompt: '第 ' + version + ' 版完整提示词。',
    result_asset_id: asset.id,
    result_asset: asset,
    created_at: '2026-10-2' + version + 'T06:00:00.000Z',
    completed_at: '2026-10-2' + version + 'T06:00:30.000Z',
    ...overrides,
  })
}

async function mountResults(
  project: ProjectView,
  generations: GenerationView[],
  options: { fakeClock?: boolean; extraRoutes?: NonNullable<Parameters<typeof mountWorkspace>[0]>['routes'] } = {},
) {
  harness = await mountWorkspace({
    config: configuredConfig(),
    fakeClock: options.fakeClock,
    viewportWidth: 1440,
    url: '/?project=' + PROJECT_A,
    routes: [
      { method: 'GET', url: detailPathA, responses: [{ body: detail(project, generations) }] },
      ...(options.extraRoutes ?? []),
    ],
  })
  const nav = await screen.findByRole('navigation', { name: '工作阶段' })
  const results = within(nav).getByRole('button', { name: /查看结果/ })
  await waitFor(() => expect(results).not.toHaveAttribute('aria-disabled', 'true'))
  await harness.user.click(results)
  await screen.findByRole('heading', { name: '查看结果' })
  return harness
}

async function openConversation(title = '春季托特包主图') {
  const nav = await screen.findByRole('navigation', { name: '工作阶段' })
  const describe = within(nav).getByRole('button', { name: '描述需求' })
  await waitFor(() => expect(describe).not.toHaveAttribute('aria-disabled', 'true'))
  await harness!.user.click(describe)
  await screen.findByRole('heading', { name: '描述需求' })
  await waitFor(() => expect(harness!.router.currentRoute.value.name).toBe('describe'))
  await screen.findByRole('heading', { name: title })
}

async function goToGenerate(): Promise<void> {
  const nav = await screen.findByRole('navigation', { name: '工作阶段' })
  const generate = within(nav).getByRole('button', { name: '生成图片' })
  await waitFor(() => expect(generate).not.toHaveAttribute('aria-disabled', 'true'))
  await harness!.user.click(generate)
  await screen.findByRole('heading', { name: '生成图片' })
  await waitFor(() => expect(harness!.router.currentRoute.value.name).toBe('generate'))
}

function selectedVersionButton(version: number): HTMLElement {
  return screen.getByRole('button', { name: new RegExp('^第 ' + version + ' 版，') })
}

describe('T08 版本浏览与反馈重生', () => {
  it('成功版本按版本号排序，默认展示最新版并可逐版预览和下载', async () => {
    const versions = [succeededVersion(2), succeededVersion(1)]
    harness = await mountResults(currentProject(), versions)

    const strip = screen.getByRole('group', { name: '历史成功版本' })
    const buttons = within(strip).getAllByRole('button')
    expect(buttons).toHaveLength(2)
    expect(buttons[0]).toHaveAccessibleName(/^第 1 版，/)
    expect(buttons[1]).toHaveAccessibleName(/^第 2 版，/)
    expect(buttons[1]).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('共 2 个成功版本')).toBeVisible()
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-v2',
    )
    expect(screen.getByText('实际尺寸：1024 × 1026')).toBeVisible()
    expect(screen.queryByRole('region', { name: '当前需求' })).toBeNull()
    expect(screen.queryByLabelText('该版本提示词')).toBeNull()

    const latestLink = screen.getByRole('link', { name: '下载当前图片' })
    expect(latestLink).toHaveAttribute('href', '/api/assets/asset-v2?download=true')
    expect(latestLink).toHaveAttribute('download', 'project-' + PROJECT_A + '-v2.png')

    buttons[1].focus()
    await harness.user.keyboard('{ArrowLeft}')
    expect(buttons[0]).toHaveFocus()
    await harness.user.keyboard('{Enter}')
    await waitFor(() => expect(selectedVersionButton(1)).toHaveAttribute('aria-pressed', 'true'))
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-v1',
    )
    expect(screen.getByRole('link', { name: '下载当前图片' })).toHaveAttribute(
      'href',
      '/api/assets/asset-v1?download=true',
    )
    expect(screen.getByRole('link', { name: '下载当前图片' })).toHaveAttribute(
      'download',
      'project-' + PROJECT_A + '-v1.png',
    )
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('浏览旧版只改变预览和下载，不写入项目或需求', async () => {
    harness = await mountResults(currentProject(), [succeededVersion(1), succeededVersion(2)])

    expect(screen.queryByRole('region', { name: '当前需求' })).toBeNull()
    expect(screen.getByText('共 2 个成功版本')).toBeVisible()

    await harness.user.click(selectedVersionButton(1))

    expect(await screen.findByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-v1',
    )
    expect(screen.getByText('实际尺寸：1024 × 1025')).toBeVisible()
    expect(screen.getByRole('link', { name: '下载当前图片' })).toHaveAttribute(
      'href',
      '/api/assets/asset-v1?download=true',
    )
    expect(screen.queryByRole('region', { name: '当前需求' })).toBeNull()
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('某个成功版本文件缺失时只影响该版本，其他版本仍可预览和下载', async () => {
    const missing = succeededVersion(2, {
      result_asset: sampleAsset({
        id: 'asset-v2',
        kind: 'generated',
        availability: 'missing',
        width: 1024,
        height: 1024,
      }),
    })
    harness = await mountResults(currentProject(), [succeededVersion(1), missing])

    expect(screen.getByText('结果文件缺失')).toBeVisible()
    expect(screen.queryByRole('img', { name: '生成结果图' })).toBeNull()
    expect(screen.queryByRole('link', { name: '下载当前图片' })).toBeNull()
    expect(within(screen.getByRole('group', { name: '历史成功版本' })).getByText('文件缺失')).toBeVisible()

    await harness.user.click(selectedVersionButton(1))
    expect(await screen.findByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-v1',
    )
    expect(screen.getByRole('link', { name: '下载当前图片' })).toHaveAttribute(
      'href',
      '/api/assets/asset-v1?download=true',
    )

    await harness.user.click(selectedVersionButton(2))
    expect(screen.getByText('结果文件缺失')).toBeVisible()
    expect(screen.queryByRole('link', { name: '下载当前图片' })).toBeNull()
  })

  it('查看旧成功图不会遮盖最近一次失败的生成任务', async () => {
    const failed = sampleGeneration({
      id: 'generation-2',
      version_no: 2,
      request_id: 'request-2',
      status: 'failed',
      stage: 'done',
      result_asset_id: null,
      result_asset: null,
      error: { code: 'MODEL_ERROR', message: '模型超时' },
    })
    harness = await mountResults(currentProject(), [succeededVersion(1), failed])

    expect(screen.getByText('最近生成失败：模型超时', { selector: '.generation-latest-error' })).toBeVisible()
    expect(screen.getByText('第 1 版 · 状态：成功')).toBeVisible()
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-v1',
    )

    await harness.user.click(selectedVersionButton(1))
    expect(screen.getByText('最近生成失败：模型超时', { selector: '.generation-latest-error' })).toBeVisible()
    expect(screen.getByText('第 1 版 · 状态：成功')).toBeVisible()
  })

  it('版本缺少快照或提示词时仍按结果资产展示图片', async () => {
    const incomplete = succeededVersion(1, {
      brief_snapshot: null,
      prompt: '',
    })
    harness = await mountResults(currentProject(), [incomplete])

    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-v1',
    )
    expect(screen.getByRole('link', { name: '下载当前图片' })).toHaveAttribute(
      'href',
      '/api/assets/asset-v1?download=true',
    )
    expect(screen.queryByText('当前最新商品描述')).toBeNull()
    expect(screen.queryByLabelText('该版本提示词')).toBeNull()
  })

  it('结果页不提供继续编辑，可通过导航回到描述页且不发请求', async () => {
    harness = await mountResults(currentProject(), [succeededVersion(1)])
    expect(screen.queryByRole('button', { name: '继续编辑' })).toBeNull()

    await openConversation()

    expect(screen.getByLabelText('描述商品与图片需求')).toBeVisible()
    expect(harness.fetch.count('POST', messagesPathA)).toBe(0)
    expect(harness.fetch.count('POST', generationsPathA)).toBe(0)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('生成进行中主动查看旧版时，新成功结果到达仍保留旧版选择并提示新结果', async () => {
    const oldVersion = succeededVersion(1)
    harness = await mountWorkspace({
      config: configuredConfig(),
      fakeClock: true,
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [{ method: 'GET', url: detailPathA, responses: [{ body: detail(currentProject(), [oldVersion]) }] }],
    })
    await openConversation()
    await goToGenerate()

    const queued = sampleGeneration({
      id: 'generation-2',
      version_no: 2,
      request_id: 'request-2',
      confirmed_revision: 5,
      status: 'queued',
      stage: 'waiting_slot',
      brief_snapshot: sampleBrief({ product_description: '第 2 版商品描述' }),
      prompt: '第 2 版完整提示词。',
      result_asset_id: null,
      result_asset: null,
      completed_at: null,
    })
    const newVersion = succeededVersion(2, { confirmed_revision: 5 })
    harness.fetch.route('POST', generationsPathA, () => ({
      status: 202,
      body: { generation: queued },
    }))
    harness.fetch.route('GET', generationPath2, () => ({ body: newVersion }))
    harness.fetch.route('GET', detailPathA, () => ({ body: detail(currentProject(), [oldVersion, newVersion]) }))

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('生成请求已提交，正在等待模型处理。', { selector: '.status-notice' })
    await harness.user.click(screen.getByRole('button', { name: /查看结果/ }))
    await screen.findByRole('heading', { name: '查看结果' })
    await harness.user.click(selectedVersionButton(1))

    await harness.clock.advance(2000)
    expect(await screen.findByText('新结果：第 2 版已生成。当前仍显示第 1 版。', { selector: '.status-notice' })).toBeVisible()
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-v1',
    )
    expect(selectedVersionButton(1)).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /^第 2 版，/ })).toBeVisible()

    await harness.user.click(selectedVersionButton(2))
    expect(await screen.findByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-v2',
    )
  })

  it('生成结果仍在轮询时切换到其他项目，不会跳回原项目或覆盖其预览', async () => {
    const oldVersion = succeededVersion(1)
    harness = await mountWorkspace({
      config: configuredConfig(),
      fakeClock: true,
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        { method: 'GET', url: detailPathA, responses: [{ body: detail(currentProject(), [oldVersion]) }] },
        {
          method: 'GET',
          url: detailPathB,
          responses: [{ body: detail(sampleProject({ id: PROJECT_B, title: '陶瓷马克杯', brief: sampleBrief({ product_description: '陶瓷马克杯' }) })) }],
        },
      ],
    })
    await openConversation()
    await goToGenerate()
    const queued = sampleGeneration({
      id: 'generation-2',
      version_no: 2,
      request_id: 'request-2',
      status: 'queued',
      stage: 'waiting_slot',
      result_asset_id: null,
      result_asset: null,
      completed_at: null,
    })
    harness.fetch.route('POST', generationsPathA, () => ({ status: 202, body: { generation: queued } }))
    harness.fetch.route('GET', generationPath2, () => ({ body: succeededVersion(2), delayMs: 5000 }))

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('生成请求已提交，正在等待模型处理。', { selector: '.status-notice' })
    await harness.clock.advance(2000)
    expect(harness.fetch.count('GET', generationPath2)).toBe(1)

    window.history.pushState({}, '', '/?project=' + PROJECT_B)
    window.dispatchEvent(new PopStateEvent('popstate'))
    await screen.findByRole('heading', { name: '陶瓷马克杯' })
    await harness.clock.advance(5000)

    expect(window.location.search).toBe('?project=' + PROJECT_B)
    expect(screen.getByRole('heading', { name: '陶瓷马克杯' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: '查看结果' })).toBeNull()
  })

  it('首图之后继续反馈、整理、确认并生成新版，旧版和新版保持独立下载', async () => {
    const initial = currentProject({
      revision: 3,
      prompt_revision: 3,
      confirmed_revision: 3,
      brief: sampleBrief({
        product_description: '米色亚麻托特包',
        reference_asset_id: 'ref-asset',
      }),
    })
    const oldVersion = succeededVersion(1)
    const feedbackProject = currentProject({
      revision: 4,
      prompt_revision: null,
      confirmed_revision: null,
      brief_status: 'draft',
      brief: sampleBrief({
        product_description: '浅灰亚麻托特包',
        style: '浅灰色背景',
        prompt: '',
        reference_asset_id: 'ref-asset',
      }),
    })
    const organizedProject = currentProject({
      revision: 5,
      prompt_revision: 5,
      confirmed_revision: null,
      brief_status: 'review',
      brief: sampleBrief({
        product_description: '浅灰亚麻托特包',
        style: '浅灰色背景',
        prompt: '为浅灰亚麻托特包生成一张简洁主图。',
        reference_asset_id: 'ref-asset',
      }),
    })
    const confirmedProject = currentProject({
      revision: 5,
      prompt_revision: 5,
      confirmed_revision: 5,
      brief_status: 'confirmed',
      brief: sampleBrief({
        product_description: '浅灰亚麻托特包',
        style: '浅灰色背景',
        prompt: '为浅灰亚麻托特包生成一张简洁主图。',
        reference_asset_id: 'ref-asset',
      }),
    })
    const newVersion = succeededVersion(2, {
      confirmed_revision: 5,
      brief_snapshot: organizedProject.brief,
      prompt: organizedProject.brief.prompt,
    })

    harness = await mountWorkspace({
      config: configuredConfig(),
      fakeClock: true,
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [{ method: 'GET', url: detailPathA, responses: [{ body: detail(initial, [oldVersion]) }] }],
    })
    await waitFor(() => expect(harness!.router.currentRoute.value.name).toBe('results'))
    await openConversation()

    let messageCalls = 0
    harness.fetch.route('POST', messagesPathA, (request) => {
      messageCalls += 1
      const content = (request.body as { content: string }).content
      const project = messageCalls === 1 ? feedbackProject : organizedProject
      const assistantContent = messageCalls === 1 ? '已更新需求。' : '已重新整理当前需求。'
      return {
        body: {
          project,
          user_message: sampleMessage({ id: 'user-' + messageCalls, role: 'user', content }),
          assistant_message: sampleMessage({
            id: 'assistant-' + messageCalls,
            role: 'assistant',
            content: assistantContent,
            brief_snapshot: project.brief,
          }),
        },
      }
    })
    harness.fetch.route('POST', confirmPathA, () => ({ body: confirmedProject }))
    const queued = sampleGeneration({
      id: 'generation-2',
      version_no: 2,
      request_id: 'request-2',
      confirmed_revision: 5,
      status: 'queued',
      stage: 'waiting_slot',
      brief_snapshot: organizedProject.brief,
      prompt: organizedProject.brief.prompt,
      result_asset_id: null,
      result_asset: null,
      completed_at: null,
    })
    harness.fetch.route('POST', generationsPathA, () => ({ status: 202, body: { generation: queued } }))
    harness.fetch.route('GET', generationPath2, () => ({ body: newVersion }))
    harness.fetch.route('GET', detailPathA, () => ({ body: detail(confirmedProject, [oldVersion, newVersion]) }))

    const nav = screen.getByRole('navigation', { name: '工作阶段' })
    const composer = screen.getByLabelText('描述商品与图片需求')
    await harness.user.type(composer, '把背景改成浅灰色')
    await harness.user.keyboard('{Control>}{Enter}{/Control}')
    await screen.findByText('已更新需求。')

    expect(harness.fetch.last('POST', messagesPathA)?.body).toMatchObject({
      content: '把背景改成浅灰色',
      expected_revision: 3,
    })

    await harness.user.click(within(nav).getByRole('button', { name: '确认需求' }))
    await screen.findByRole('heading', { name: '确认需求' })

    await harness.user.click(screen.getByRole('button', { name: '重新整理需求' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认当前需求' })).toBeEnabled())
    expect(harness.fetch.last('POST', messagesPathA)?.body).toMatchObject({
      content: '请按当前已保存的需求重新整理。',
      expected_revision: 4,
    })

    await harness.user.click(screen.getByRole('button', { name: '确认当前需求' }))
    await screen.findByText('需求已确认。', { selector: '.status-notice' })

    await harness.user.click(within(nav).getByRole('button', { name: '生成图片' }))
    await screen.findByRole('heading', { name: '生成图片' })
    await waitFor(() => expect(harness!.router.currentRoute.value.name).toBe('generate'))
    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('生成请求已提交，正在等待模型处理。', { selector: '.status-notice' })
    expect(harness.fetch.last('POST', generationsPathA)?.body).toMatchObject({
      confirmed_revision: 5,
    })

    await harness.clock.advance(2000)
    await screen.findByRole('heading', { name: '查看结果' })
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-v2',
    )
    expect(screen.getByText('共 2 个成功版本')).toBeVisible()
    expect(screen.getByRole('link', { name: '下载当前图片' })).toHaveAttribute(
      'download',
      'project-' + PROJECT_A + '-v2.png',
    )

    await harness.user.click(selectedVersionButton(1))
    expect(await screen.findByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-v1',
    )
    expect(screen.getByText('第 1 版 · 状态：成功')).toBeVisible()
    expect(screen.getByRole('link', { name: '下载当前图片' })).toHaveAttribute(
      'href',
      '/api/assets/asset-v1?download=true',
    )
    expect(harness.fetch.count('POST', referencePathA)).toBe(0)
    expect(harness.fetch.count('DELETE', referencePathA)).toBe(0)
  })
})
