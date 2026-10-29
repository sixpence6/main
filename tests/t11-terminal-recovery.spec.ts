import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleAsset,
  sampleBrief,
  sampleGeneration,
  sampleProject,
} from './support/fixtures'
import type { GenerationView, ProjectDetailResponse, ProjectView } from '@/api/types'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = '/api/projects/' + PROJECT_A
const generationsPath = '/api/projects/' + PROJECT_A + '/generations'
const retrievePath = '/api/generations/generation-2/retrieve-result'
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

function succeededGeneration(overrides: Partial<GenerationView> = {}): GenerationView {
  const asset = sampleAsset({ id: overrides.result_asset_id as string ?? 'old-asset', kind: 'generated' })
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

function unknownGeneration(overrides: Partial<GenerationView> = {}): GenerationView {
  return sampleGeneration({
    id: 'generation-0',
    version_no: 1,
    request_id: 'request-old',
    status: 'unknown',
    stage: 'done',
    result_asset_id: null,
    result_asset: null,
    error: { code: 'VISUAL_OUTCOME_UNKNOWN', message: '模型结果未确认' },
    ...overrides,
  })
}

function retrievalFailedGeneration(overrides: Partial<GenerationView> = {}): GenerationView {
  return sampleGeneration({
    id: 'generation-2',
    version_no: 2,
    request_id: 'request-2',
    status: 'failed',
    stage: 'done',
    result_asset_id: null,
    result_asset: null,
    error: { code: 'RESULT_FETCH_FAILED', message: '结果文件读取失败' },
    can_retrieve_result: true,
    retrieval_unavailable_reason: null,
    ...overrides,
  })
}

function retrievalSucceeded(overrides: Partial<GenerationView> = {}): GenerationView {
  const asset = sampleAsset({ id: 'new-asset', kind: 'generated', width: 1536, height: 1536 })
  return sampleGeneration({
    ...retrievalFailedGeneration(),
    status: 'succeeded',
    error: null,
    result_asset_id: asset.id,
    result_asset: asset,
    can_retrieve_result: false,
    ...overrides,
  })
}

async function mountProject(
  project: ProjectView,
  generations: GenerationView[],
  extraDetailResponses: { body: ProjectDetailResponse }[] = [],
): Promise<WorkspaceHarness> {
  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/?project=' + PROJECT_A,
    routes: [
      {
        method: 'GET',
        url: detailPath,
        responses: [{ body: detail(project, generations) }, ...extraDetailResponses],
      },
    ],
  })
  const nav = await screen.findByRole('navigation', { name: '工作阶段' })
  await harness.user.click(within(nav).getByRole('button', { name: '描述需求' }))
  await screen.findByRole('heading', { name: '春季托特包主图' })
  return harness
}

describe('T11 终态恢复：未知结果确认与重取结果', () => {
  it('业务结果未确认时先要求明确风险确认，取消与 Escape 都不写请求', async () => {
    harness = await mountProject(confirmedProject(), [unknownGeneration()])

    expect(
      screen.getByText('最近生成结果未确认；上次请求可能已提交并可能已计费。再次生成前需确认风险。', {
        selector: '.generation-latest-error',
      }),
    ).toBeVisible()
    const generate = screen.getByRole('button', { name: '生成首图' })
    expect(generate).toBeEnabled()
    await harness.user.click(generate)

    const dialog = await screen.findByRole('alertdialog', { name: '再次生成可能重复计费' })
    expect(within(dialog).getByText(/第 1 版\s*生成结果未确认，原请求可能已提交并已由模型服务计费。/)).toBeVisible()
    expect(within(dialog).getByText(/不会重试或恢复上一次请求/)).toBeVisible()
    const cancel = within(dialog).getByRole('button', { name: '返回查看' })
    await waitFor(() => expect(cancel).toHaveFocus())

    await harness.user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    await waitFor(() => expect(generate).toHaveFocus())
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
  })

  it('确认风险后使用新 request_id 与 acknowledge_unknown=true 重新生成', async () => {
    harness = await mountProject(confirmedProject(), [unknownGeneration()])
    let submitted: Record<string, unknown> | null = null
    harness.fetch.route('POST', generationsPath, (request) => {
      submitted = request.body as Record<string, unknown>
      return {
        status: 202,
        body: {
          generation: queuedGeneration({
            id: 'generation-2',
            version_no: 2,
            request_id: 'request-new',
          }),
        },
      }
    })

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    const dialog = await screen.findByRole('alertdialog', { name: '再次生成可能重复计费' })
    await harness.user.click(within(dialog).getByRole('button', { name: '我已了解并再次生成' }))

    await screen.findByText('生成请求已提交，正在等待模型处理。', { selector: '.status-notice' })
    expect(submitted).not.toBeNull()
    const submittedBody = submitted as unknown as Record<string, unknown>
    expect(submittedBody).toMatchObject({ confirmed_revision: 3, acknowledge_unknown: true })
    expect(submittedBody.request_id).toMatch(/^[0-9a-f-]{36}$/)
    expect(submittedBody.request_id).not.toBe('request-old')
    expect(harness.fetch.count('POST', generationsPath)).toBe(1)
  })

  it('普通明确失败不弹风险确认，再次生成使用新 request_id 且不带 acknowledge_unknown', async () => {
    const failed = sampleGeneration({
      id: 'generation-2',
      version_no: 2,
      request_id: 'request-failed',
      status: 'failed',
      stage: 'done',
      result_asset_id: null,
      result_asset: null,
      error: { code: 'MODEL_ERROR', message: '模型超时' },
    })
    harness = await mountProject(confirmedProject(), [succeededGeneration(), failed])
    let submitted: Record<string, unknown> | null = null
    harness.fetch.route('POST', generationsPath, (request) => {
      submitted = request.body as Record<string, unknown>
      return { status: 202, body: { generation: queuedGeneration({ version_no: 3 }) } }
    })

    expect(screen.getByText('最近生成失败：模型超时', { selector: '.generation-latest-error' })).toBeVisible()
    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('生成请求已提交，正在等待模型处理。', { selector: '.status-notice' })

    expect(screen.queryByRole('alertdialog')).toBeNull()
    const submittedBody = submitted as unknown as Record<string, unknown>
    expect(submittedBody).toMatchObject({ confirmed_revision: 3 })
    expect(submittedBody).not.toHaveProperty('acknowledge_unknown')
    expect(submittedBody.request_id).toMatch(/^[0-9a-f-]{36}$/)
    expect(submittedBody.request_id).not.toBe('request-failed')
  })

  it('RESULT_FETCH_FAILED 可重取同一记录，成功刷新版本且不提交新生成', async () => {
    const project = confirmedProject()
    const old = succeededGeneration()
    const failed = retrievalFailedGeneration()
    const updated = retrievalSucceeded()
    harness = await mountProject(project, [old, failed], [
      { body: detail(project, [old, updated]) },
    ])
    harness.fetch.route('POST', retrievePath, () => ({ body: updated }))

    const panel = screen.getByRole('heading', { name: '重取现有结果' }).closest('section') as HTMLElement
    expect(within(panel).getByText('原记录错误：结果文件读取失败（RESULT_FETCH_FAILED）')).toBeVisible()
    await harness.user.click(within(panel).getByRole('button', { name: '重取现有结果' }))

    await screen.findByText('结果已重取，生成记录与版本已更新。', { selector: '.status-notice' })
    expect(harness.fetch.count('POST', retrievePath)).toBe(1)
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)

    await harness.user.click(screen.getByRole('button', { name: /查看结果/ }))
    const image = await screen.findByRole('img', { name: '生成结果图' })
    expect(image).toHaveAttribute('src', '/api/assets/new-asset')
    expect(screen.getByRole('link', { name: '下载当前图片' })).toHaveAttribute(
      'href',
      '/api/assets/new-asset?download=true',
    )
  })

  it('重取成功不会覆盖用户主动选择的其他成功版本', async () => {
    const project = confirmedProject()
    const first = succeededGeneration({ id: 'generation-1', version_no: 1, result_asset_id: 'asset-1' })
    const latestSuccess = succeededGeneration({
      id: 'generation-3',
      version_no: 3,
      request_id: 'request-3',
      result_asset_id: 'asset-3',
    })
    const failed = retrievalFailedGeneration({ version_no: 4 })
    const updated = retrievalSucceeded({ version_no: 4 })
    harness = await mountProject(project, [first, latestSuccess, failed], [
      { body: detail(project, [first, latestSuccess, updated]) },
    ])
    harness.fetch.route('POST', retrievePath, () => ({ body: updated }))

    await harness.user.click(screen.getByRole('button', { name: /查看结果/ }))
    await screen.findByRole('img', { name: '生成结果图' })
    await harness.user.click(screen.getByRole('button', { name: /^第 1 版，/ }))
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute('src', '/api/assets/asset-1')

    await harness.user.click(screen.getByRole('button', { name: '重取现有结果' }))
    await screen.findByText(
      '结果已重取：第 4 版已恢复。当前仍显示第 1 版。',
      { selector: '.status-notice' },
    )
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute('src', '/api/assets/asset-1')
    expect(screen.getByRole('button', { name: /^第 1 版，/ })).toHaveAttribute('aria-pressed', 'true')
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
  })

  it('重取失败保留旧图与原记录错误，只请求一次重取', async () => {
    const project = confirmedProject()
    const old = succeededGeneration()
    const failed = retrievalFailedGeneration()
    harness = await mountProject(project, [old, failed])
    harness.fetch.route('POST', retrievePath, () => ({
      status: 502,
      body: { error: { code: 'RESULT_FETCH_FAILED', message: '下载失败' } },
    }))

    await harness.user.click(screen.getByRole('button', { name: '重取现有结果' }))
    await screen.findByText(
      '重取结果失败，原记录与旧图片仍保留，请稍后重试。',
      { selector: '.result-retrieval-error' },
    )
    await harness.user.click(screen.getByRole('button', { name: /查看结果/ }))
    expect(await screen.findByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/old-asset',
    )
    expect(screen.getByText('原记录错误：结果文件读取失败（RESULT_FETCH_FAILED）')).toBeVisible()
    expect(harness.fetch.count('POST', retrievePath)).toBe(1)
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
  })

  it('服务端表示不可重取时只显示原因，不显示重取按钮', async () => {
    const reason = '原始模型已不再可用，无法重新获取。'
    harness = await mountProject(confirmedProject(), [
      retrievalFailedGeneration({ can_retrieve_result: false, retrieval_unavailable_reason: reason }),
    ])

    expect(screen.getByRole('heading', { name: '重取现有结果' })).toBeVisible()
    expect(screen.getByText(reason)).toBeVisible()
    expect(screen.queryByRole('button', { name: '重取现有结果' })).toBeNull()
    expect(harness.fetch.count('POST', retrievePath)).toBe(0)
  })

  it('快速重复点击重取只发送一次请求', async () => {
    const project = confirmedProject()
    const old = succeededGeneration()
    const failed = retrievalFailedGeneration()
    const updated = retrievalSucceeded()
    harness = await mountProject(project, [old, failed], [
      { body: detail(project, [old, updated]) },
    ])
    harness.fetch.route('POST', retrievePath, () => ({ body: updated, delayMs: 250 }))

    const button = screen.getByRole('button', { name: '重取现有结果' })
    await harness.user.click(button)
    await harness.user.click(button)
    await screen.findByText('结果已重取，生成记录与版本已更新。', { selector: '.status-notice' })
    expect(harness.fetch.count('POST', retrievePath)).toBe(1)
  })

  it('重取回执不明时通过 GET 核实，成功后更新同一记录', async () => {
    const project = confirmedProject()
    const old = succeededGeneration()
    const failed = retrievalFailedGeneration()
    const updated = retrievalSucceeded()
    harness = await mountProject(project, [old, failed], [
      { body: detail(project, [old, updated]) },
    ])
    harness.fetch.route('POST', retrievePath, () => ({ networkError: 'Failed to fetch' }))
    harness.fetch.route('GET', generationPath, () => ({ body: updated }))

    await harness.user.click(screen.getByRole('button', { name: '重取现有结果' }))
    await screen.findByText('结果已重取，生成记录与版本已更新。', { selector: '.status-notice' })
    expect(harness.fetch.count('POST', retrievePath)).toBe(1)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
  })

  it('普通查看与下载结果不会创建生成或重取请求', async () => {
    harness = await mountProject(confirmedProject(), [succeededGeneration()])
    await harness.user.click(screen.getByRole('button', { name: /查看结果/ }))
    await screen.findByRole('img', { name: '生成结果图' })

    const download = screen.getByRole('link', { name: '下载当前图片' })
    expect(download).toHaveAttribute('href', '/api/assets/old-asset?download=true')
    expect(download).toHaveAttribute('download', 'project-' + PROJECT_A + '-v1.png')
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
    expect(harness.fetch.count('POST', retrievePath)).toBe(0)
  })
})
