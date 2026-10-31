import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleAsset,
  sampleBrief,
  sampleGeneration,
  sampleProject,
  unknownCapabilitiesConfig,
} from './support/fixtures'
import type { GenerationView, ProjectDetailResponse, ProjectView } from '@/api/types'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = '/api/projects/' + PROJECT_A
const generationsPath = '/api/projects/' + PROJECT_A + '/generations'
const generationPath = '/api/generations/generation-2'

function detail(project: ProjectView, generations: GenerationView[] = []): ProjectDetailResponse {
  return { project, messages: [], generations }
}

function confirmedProject(overrides: Partial<ProjectView> = {}): ProjectView {
  return sampleProject({
    id: PROJECT_A,
    brief_status: 'confirmed',
    revision: 3,
    prompt_revision: 3,
    confirmed_revision: 3,
    brief: sampleBrief(),
    ...overrides,
  })
}

function queuedGeneration(overrides: Partial<GenerationView> = {}): GenerationView {
  return sampleGeneration({
    id: 'generation-2',
    version_no: 2,
    request_id: 'request-2',
    status: 'queued',
    stage: 'waiting_slot',
    result_asset_id: null,
    result_asset: null,
    completed_at: null,
    ...overrides,
  })
}

function succeededGeneration(overrides: Partial<GenerationView> = {}): GenerationView {
  const asset = sampleAsset({ id: 'asset-1', kind: 'generated' })
  return sampleGeneration({
    id: 'generation-1',
    version_no: 1,
    status: 'succeeded',
    stage: 'done',
    result_asset_id: asset.id,
    result_asset: asset,
    ...overrides,
  })
}

async function mountProject(
  project: ProjectView,
  detailResponses = [{ body: detail(project) }],
): Promise<WorkspaceHarness> {
  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/generate?project=' + PROJECT_A,
    routes: [{ method: 'GET', url: detailPath, responses: detailResponses }],
  })
  const nav = await screen.findByRole('navigation', { name: '工作阶段' })
  await harness.user.click(within(nav).getByRole('button', { name: '生成图片' }))
  await screen.findByRole('heading', { name: '生成概览' })
  return harness
}

describe('T06 生成首图与结果', () => {
  it('已确认项目展示生成摘要和提示词入口，无图时没有假结果', async () => {
    harness = await mountProject(confirmedProject())

    const summary = screen.getByText('生成概览').closest('section') as HTMLElement
    expect(within(summary).getByText('第 3 修订 · 确认后的提示词')).toBeVisible()
    expect(within(summary).getByText('图片尺寸：1024x1024')).toBeVisible()
    expect(within(summary).getByLabelText('确认后的提示词')).toHaveValue(sampleBrief().prompt)
    expect(screen.getByRole('button', { name: '生成首图' })).toBeEnabled()
    expect(screen.queryByRole('img', { name: '生成结果图' })).toBeNull()
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
    expect(harness.fetch.requests.some((item) => item.path.endsWith('/messages'))).toBe(false)
  })

  it('点击生成发送 UUID request_id 与 confirmed_revision，只关联返回任务', async () => {
    harness = await mountProject(confirmedProject())
    const queued = queuedGeneration()
    harness.fetch.route('POST', generationsPath, (request) => {
      expect(request.body).toMatchObject({ confirmed_revision: 3 })
      return { status: 202, body: { generation: queued } }
    })

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))

    await screen.findByText('生成请求已提交，正在等待模型处理。', { selector: '.status-notice' })
    expect(harness.fetch.count('POST', generationsPath)).toBe(1)
    const body = harness.fetch.last('POST', generationsPath)?.body as Record<string, unknown>
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/)
    expect(body).not.toHaveProperty('acknowledge_unknown')
    expect(await screen.findByRole('heading', { name: '查看结果' })).toBeVisible()
    expect(screen.getByText('当前生成：第 2 版，等待生成。')).toBeVisible()
    expect(screen.queryByRole('button', { name: '生成首图' })).toBeNull()
    expect(harness.fetch.count('GET', generationPath)).toBe(0)
    expect(harness.fetch.count('POST', generationsPath)).toBe(1)
  })

  it('自动查询只读取当前任务，成功后在结果视图展示真实资源与下载', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      fakeClock: true,
      viewportWidth: 1440,
      url: '/generate?project=' + PROJECT_A,
      routes: [
        {
          method: 'GET',
          url: detailPath,
          responses: [
            { body: detail(confirmedProject()) },
            { body: detail(confirmedProject(), [succeededGeneration({ version_no: 2 })]) },
          ],
        },
      ],
    })
    await screen.findByRole('button', { name: '生成首图' })
    const running = queuedGeneration({ status: 'running', stage: 'waiting_remote' })
    const succeeded = succeededGeneration({ version_no: 2 })
    harness.fetch.route('POST', generationsPath, () => ({
      status: 202,
      body: { generation: queuedGeneration() },
    }))
    harness.fetch.sequence('GET', generationPath, [
      { body: running },
      { body: succeeded },
    ])

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('生成请求已提交，正在等待模型处理。', { selector: '.status-notice' })
    await screen.findByRole('heading', { name: '查看结果' })
    await harness.clock.advance(2000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    expect(screen.getByText('当前生成：第 2 版，正在生成。')).toBeVisible()
    expect(screen.queryByRole('button', { name: '生成首图' })).toBeNull()

    await harness.clock.advance(2000)
    expect(harness.fetch.count('GET', generationPath)).toBe(2)
    await screen.findByRole('heading', { name: '查看结果' })
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-1',
    )
    expect(screen.getByText('实际尺寸：1024 × 1024')).toBeVisible()
    const link = screen.getByRole('link', { name: '下载当前图片' })
    expect(link).toHaveAttribute('href', '/api/assets/asset-1?download=true')
    expect(link).toHaveAttribute('download', 'project-' + PROJECT_A + '-v2.png')
    expect(screen.getByText('第 2 版 · 状态：成功', { exact: false })).toBeVisible()
    expect(harness.fetch.count('POST', generationsPath)).toBe(1)
  })

  it('快速重复点击只产生一次生成请求', async () => {
    harness = await mountProject(confirmedProject())
    const queued = queuedGeneration()
    harness.fetch.route('POST', generationsPath, () => ({
      status: 202,
      body: { generation: queued },
      delayMs: 200,
    }))
    const button = screen.getByRole('button', { name: '生成首图' })
    fireEvent.click(button)
    fireEvent.click(button)
    await waitFor(() => expect(harness!.fetch.count('POST', generationsPath)).toBe(1))
    await screen.findByText('生成请求已提交，正在等待模型处理。', { selector: '.status-notice' })
    expect(harness.fetch.count('POST', generationsPath)).toBe(1)
  })

  it('业务失败保留成功旧图，最近生成异常与旧图分开显示', async () => {
    const oldAsset = sampleAsset({ id: 'old-asset', kind: 'generated' })
    const oldGeneration = succeededGeneration({
      id: 'generation-1',
      version_no: 1,
      result_asset_id: oldAsset.id,
      result_asset: oldAsset,
    })
    harness = await mountProject(confirmedProject(), [
      { body: detail(confirmedProject(), [oldGeneration]) },
    ])
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
    harness.fetch.json('POST', generationsPath, { generation: failed }, 202)

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('最近生成失败：模型超时', { selector: '.generation-latest-error' })
    expect(screen.getByText('最近版本：第 2 版，状态：生成失败')).toBeVisible()

    await harness.user.click(screen.getByRole('button', { name: /查看结果/ }))
    await screen.findByRole('img', { name: '生成结果图' })
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/old-asset',
    )
    expect(screen.getByText('最近生成失败：模型超时', { selector: '.generation-latest-error' })).toBeVisible()
  })

  it('结果资源缺失时显示交付异常，不提供图片或下载', async () => {
    const missingAsset = sampleAsset({ id: 'missing-asset', kind: 'generated', availability: 'missing' })
    const missingGeneration = succeededGeneration({
      result_asset_id: missingAsset.id,
      result_asset: missingAsset,
    })
    harness = await mountProject(confirmedProject(), [
      { body: detail(confirmedProject(), [missingGeneration]) },
    ])

    await harness.user.click(screen.getByRole('button', { name: /查看结果/ }))
    expect(await screen.findByText('结果文件缺失')).toBeVisible()
    expect(screen.queryByRole('img', { name: '生成结果图' })).toBeNull()
    expect(screen.queryByRole('link', { name: '下载当前图片' })).toBeNull()
  })

  it('刷新项目恢复已有成功记录，不重新生成或发送消息', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/results?project=' + PROJECT_A,
      routes: [{ method: 'GET', url: detailPath, responses: [{ body: detail(confirmedProject(), [succeededGeneration()]) }] }],
    })
    expect(await screen.findByRole('heading', { name: '查看结果' })).toBeVisible()
    expect(await screen.findByRole('img', { name: '生成结果图' })).toBeVisible()
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
    expect(harness.fetch.count('GET', generationPath)).toBe(0)
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
    expect(harness.fetch.requests.some((item) => item.path.endsWith('/messages'))).toBe(false)
  })

  it('回执不明时锁定新生成，核实后恢复任务且不重复提交', async () => {
    let submittedRequestId = ''
    let verifiedGeneration: GenerationView | null = null
    harness = await mountWorkspace({
      config: configuredConfig(),
      fakeClock: true,
      viewportWidth: 1440,
      url: '/generate?project=' + PROJECT_A,
      setup(fetch) {
        fetch.route('GET', detailPath, () => ({
          body: verifiedGeneration
            ? detail(confirmedProject(), [verifiedGeneration])
            : detail(confirmedProject()),
        }))
        fetch.route('POST', generationsPath, (request) => {
          submittedRequestId = (request.body as { request_id: string }).request_id
          return { networkError: 'Failed to fetch' }
        })
      },
    })
    await screen.findByRole('button', { name: '生成首图' })

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。')
    expect(screen.getByRole('button', { name: '生成首图' })).toBeDisabled()
    expect(harness.fetch.count('POST', generationsPath)).toBe(1)

    verifiedGeneration = queuedGeneration({ request_id: submittedRequestId })
    await harness.user.click(screen.getByRole('button', { name: '核实提交结果' }))
    await screen.findByText('已核实生成提交，生成记录已恢复。', { selector: '.status-notice' })
    expect(screen.queryByText('提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。')).toBeNull()
    expect(harness.fetch.count('POST', generationsPath)).toBe(1)
    expect(screen.getByText('当前生成：第 2 版，等待生成。')).toBeVisible()
  })

  it('状态查询失败显示独立错误并停止自动查询', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      fakeClock: true,
      viewportWidth: 1440,
      url: '/generate?project=' + PROJECT_A,
      routes: [{ method: 'GET', url: detailPath, responses: [{ body: detail(confirmedProject()) }] }],
    })
    await screen.findByRole('button', { name: '生成首图' })
    harness.fetch.route('POST', generationsPath, () => ({
      status: 202,
      body: { generation: queuedGeneration() },
    }))
    harness.fetch.networkError('GET', generationPath)

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('生成请求已提交，正在等待模型处理。', { selector: '.status-notice' })
    await harness.clock.advance(2000)
    await harness.clock.advance(2000)
    await harness.clock.advance(2000)
    expect(await screen.findByText('暂时无法读取生成状态。已停止自动查询，请手动重试。')).toBeVisible()
    expect(screen.getByRole('button', { name: '重新查询状态' })).toBeVisible()
    expect(screen.queryByText('生成失败')).toBeNull()
  })

  it('大图支持适应窗口、原始大小、Esc 关闭并返回焦点', async () => {
    harness = await mountProject(confirmedProject(), [
      { body: detail(confirmedProject(), [succeededGeneration()]) },
    ])
    await harness.user.click(screen.getByRole('button', { name: /查看结果/ }))
    await screen.findByRole('img', { name: '生成结果图' })

    const trigger = screen.getByRole('button', { name: '查看大图' })
    await harness.user.click(trigger)
    const dialog = await screen.findByRole('dialog', { name: '生成结果大图' })
    expect(within(dialog).getByRole('button', { name: '关闭大图' })).toBe(document.activeElement)

    await harness.user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '生成结果大图' })).toBeNull())
    expect(document.activeElement).toBe(trigger)

    expect(screen.getByRole('button', { name: '适应窗口' })).toHaveAttribute('aria-pressed', 'true')
    await harness.user.click(screen.getByRole('button', { name: '原始大小' }))
    expect(screen.getByRole('button', { name: '原始大小' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveClass('is-original')
  })

  it('文生图能力未知时明确阻塞生成', async () => {
    harness = await mountWorkspace({
      config: unknownCapabilitiesConfig(),
      viewportWidth: 1440,
      url: '/generate?project=' + PROJECT_A,
      routes: [{ method: 'GET', url: detailPath, responses: [{ body: detail(confirmedProject()) }] }],
    })
    await screen.findByRole('button', { name: '生成首图' })
    expect(screen.getByRole('button', { name: '生成首图' })).toBeDisabled()
    expect(screen.getByText('文生图能力尚未核实，不能生成图片。')).toBeVisible()
  })

  it('提交明确拒绝时只请求一次并显示安全错误', async () => {
    harness = await mountProject(confirmedProject())
    harness.fetch.json(
      'POST',
      generationsPath,
      { error: { code: 'REVISION_CONFLICT', message: '需求已更新' } },
      409,
    )
    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    expect(await screen.findByText('需求已在别处更新，请重新读取最新需求后再生成。')).toBeVisible()
    expect(harness.fetch.count('POST', generationsPath)).toBe(1)
    expect(harness.fetch.writes).toHaveLength(1)
  })

  it('阶段导航不会触发生成请求', async () => {
    harness = await mountProject(confirmedProject())
    await harness.user.click(screen.getByRole('button', { name: /确认需求/ }))
    await harness.user.click(screen.getByRole('button', { name: /生成图片/ }))
    await harness.user.click(screen.getByRole('button', { name: /查看结果/ }))
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
  })
  it('最近生成结果未确认时显示专门提示并锁住新生成', async () => {
    const unknown = sampleGeneration({
      id: 'generation-1',
      version_no: 1,
      status: 'unknown',
      stage: 'done',
      result_asset_id: null,
      result_asset: null,
      error: null,
    })
    harness = await mountProject(confirmedProject(), [
      { body: detail(confirmedProject(), [unknown]) },
    ])
    expect(
      screen.getByText('最近生成结果未确认；上次请求可能已提交并可能已计费。再次生成前需确认风险。', {
        selector: '.generation-latest-error',
      }),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: '生成首图' })).toBeEnabled()
  })

  it('自动状态查询在途时不会叠加查询，卸载后停止查询', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      fakeClock: true,
      viewportWidth: 1440,
      url: '/generate?project=' + PROJECT_A,
      routes: [{ method: 'GET', url: detailPath, responses: [{ body: detail(confirmedProject()) }] }],
    })
    await screen.findByRole('button', { name: '生成首图' })
    harness.fetch.route('POST', generationsPath, () => ({
      status: 202,
      body: { generation: queuedGeneration() },
    }))
    harness.fetch.route('GET', generationPath, () => ({
      body: queuedGeneration({ status: 'running', stage: 'waiting_remote' }),
      delayMs: 5000,
    }))

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('生成请求已提交，正在等待模型处理。', { selector: '.status-notice' })
    await harness.clock.advance(2000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    await harness.clock.advance(2000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)

    harness.unmount()
    await harness.clock.advance(5000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
  })

})
