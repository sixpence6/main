import { afterEach, describe, expect, it } from 'vitest'
import { screen, within, waitFor } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  PROJECT_B,
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
const detailBPath = '/api/projects/' + PROJECT_B
const generationsPath = '/api/projects/' + PROJECT_A + '/generations'
const generationPath = '/api/generations/generation-2'

function detail(project: ProjectView, generations: GenerationView[] = []): ProjectDetailResponse {
  return { project, messages: [], generations }
}

function organizingProject(overrides: Partial<ProjectView> = {}): ProjectView {
  return sampleProject({
    id: PROJECT_A,
    brief_status: 'organizing',
    revision: 1,
    prompt_revision: null,
    confirmed_revision: null,
    brief: sampleBrief({ prompt: '' }),
    ...overrides,
  })
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

function successGeneration(overrides: Partial<GenerationView> = {}): GenerationView {
  const asset = sampleAsset({ id: 'asset-old', kind: 'generated' })
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

function mountWithDetail(
  project: ProjectView,
  responses = [{ body: detail(project) }],
): Promise<WorkspaceHarness> {
  return mountWorkspace({
    config: configuredConfig(),
    fakeClock: true,
    viewportWidth: 1440,
    url: '/?project=' + PROJECT_A,
    routes: [{ method: 'GET', url: detailPath, responses }],
  })
}

describe('T10 状态轮询、断连与恢复', () => {
  it('需求整理中每约两秒读取项目，完成后停止自动查询且不发送消息', async () => {
    harness = await mountWithDetail(organizingProject(), [
      { body: detail(organizingProject()) },
      { body: detail(sampleProject({ brief_status: 'review' })) },
    ])
    await screen.findByRole('heading', { name: '春季托特包主图' })
    expect(screen.getByText('正在整理需求…')).toBeVisible()
    expect(harness.fetch.count('GET', detailPath)).toBe(1)

    await harness.clock.advance(1999)
    expect(harness.fetch.count('GET', detailPath)).toBe(1)
    await harness.clock.advance(1)
    await waitFor(() => expect(harness?.fetch.count('GET', detailPath)).toBe(2))
    await waitFor(() => expect(screen.queryByText('正在整理需求…')).toBeNull())
    expect(screen.getByText('待确认', { selector: '.status-badge' })).toBeVisible()

    await harness.clock.advance(4000)
    expect(harness.fetch.count('GET', detailPath)).toBe(2)
    expect(harness.fetch.requests.some((request) => request.path.endsWith('/messages'))).toBe(false)
  })

  it('刷新恢复排队中的生成记录，并在约两秒后读取该记录', async () => {
    harness = await mountWithDetail(sampleProject({
      brief_status: 'review',
      brief: sampleBrief(),
    }), [{ body: detail(sampleProject({ brief_status: 'review', brief: sampleBrief() }), [queuedGeneration()]) }])
    await screen.findByRole('heading', { name: '春季托特包主图' })
    const nav = screen.getByRole('navigation', { name: '工作阶段' })
    await harness.user.click(within(nav).getByRole('button', { name: '查看结果' }))
    expect(await screen.findByRole('heading', { name: '查看结果' })).toBeVisible()
    expect(screen.getByText('当前生成：第 2 版，等待生成。')).toBeVisible()
    expect(harness.fetch.count('GET', generationPath)).toBe(0)
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)

    await harness.clock.advance(2000)
    await waitFor(() => expect(harness?.fetch.count('GET', generationPath)).toBe(1))
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
  })

  it('自动查询单飞：上一次响应前不会叠加第二次查询', async () => {
    harness = await mountWithDetail(confirmedProject(), [
      { body: detail(confirmedProject(), [queuedGeneration()]) },
    ])
    harness.fetch.route('GET', generationPath, () => ({
      body: queuedGeneration({ status: 'running', stage: 'waiting_remote' }),
      delayMs: 1500,
    }))
    await screen.findByRole('heading', { name: '春季托特包主图' })

    await harness.clock.advance(2000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    await harness.clock.advance(1000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    await harness.clock.advance(499)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    await harness.clock.advance(1)
    await waitFor(() => expect(harness?.fetch.count('GET', generationPath)).toBe(1))
    await harness.clock.advance(1999)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    await harness.clock.advance(1)
    await waitFor(() => expect(harness?.fetch.count('GET', generationPath)).toBe(2))
  })

  it('连续三次失败后持久提示并保留旧图，手动查询只发 GET 并在成功后恢复轮询', async () => {
    harness = await mountWithDetail(confirmedProject(), [
      { body: detail(confirmedProject(), [successGeneration(), queuedGeneration()]) },
    ])
    harness.fetch.sequence('GET', generationPath, [
      { networkError: 'Failed to fetch' },
      { networkError: 'Failed to fetch' },
      { networkError: 'Failed to fetch' },
      { body: queuedGeneration({ status: 'running', stage: 'waiting_remote' }) },
    ])
    await screen.findByRole('heading', { name: '春季托特包主图' })
    await harness.user.click(screen.getByRole('button', { name: /查看结果/ }))
    await screen.findByRole('img', { name: '生成结果图' })

    await harness.clock.advance(2000)
    await harness.clock.advance(2000)
    await harness.clock.advance(2000)
    expect(await screen.findByText('连接中断，生成状态暂不可知。')).toBeVisible()
    expect(screen.getByText('暂时无法读取生成状态。已停止自动查询，请手动重试。')).toBeVisible()
    expect(screen.getByRole('img', { name: '生成结果图' })).toHaveAttribute('src', '/api/assets/asset-old')
    expect(harness.fetch.count('GET', generationPath)).toBe(3)
    expect(harness.fetch.writes).toHaveLength(0)

    harness.visibility.hide()
    harness.visibility.show()
    await harness.clock.advance(4000)
    expect(harness.fetch.count('GET', generationPath)).toBe(3)

    await harness.user.click(screen.getByRole('button', { name: '重新查询状态' }))
    await waitFor(() => expect(harness?.fetch.count('GET', generationPath)).toBe(4))
    await waitFor(() => expect(screen.queryByText('连接中断，生成状态暂不可知。')).toBeNull())
    expect(harness.fetch.writes).toHaveLength(0)
    await harness.clock.advance(2000)
    await waitFor(() => expect(harness?.fetch.count('GET', generationPath)).toBe(5))
  })

  it('生成记录不存在显示资源错误并停止自动查询', async () => {
    harness = await mountWithDetail(confirmedProject(), [
      { body: detail(confirmedProject(), [queuedGeneration()]) },
    ])
    harness.fetch.route('GET', generationPath, () => ({
      status: 404,
      body: { error: { code: 'GENERATION_NOT_FOUND', message: 'not found' } },
    }))
    await screen.findByRole('heading', { name: '春季托特包主图' })

    await harness.clock.advance(2000)
    expect(await screen.findByText('资源不存在：生成记录不可用。')).toBeVisible()
    expect(screen.getByText('资源不存在：生成记录不存在或已被删除，无法继续读取状态。')).toBeVisible()
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    await harness.clock.advance(4000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
  })

  it('一次成功会把连续失败次数清零', async () => {
    harness = await mountWithDetail(confirmedProject(), [
      { body: detail(confirmedProject(), [queuedGeneration()]) },
    ])
    harness.fetch.sequence('GET', generationPath, [
      { networkError: 'Failed to fetch' },
      { networkError: 'Failed to fetch' },
      { body: queuedGeneration({ status: 'running', stage: 'waiting_remote' }) },
      { networkError: 'Failed to fetch' },
      { networkError: 'Failed to fetch' },
      { body: queuedGeneration({ status: 'running', stage: 'waiting_remote' }) },
    ])
    await screen.findByRole('heading', { name: '春季托特包主图' })

    for (let index = 0; index < 5; index += 1) {
      await harness.clock.advance(2000)
    }
    expect(harness.fetch.count('GET', generationPath)).toBe(5)
    expect(screen.queryByText('连接中断，生成状态暂不可知。')).toBeNull()
    await harness.clock.advance(2000)
    expect(harness.fetch.count('GET', generationPath)).toBe(6)
    expect(screen.queryByText('连接中断，生成状态暂不可知。')).toBeNull()
  })

  it('业务失败是终态，不会显示为连接中断也不会继续查询', async () => {
    const failed = queuedGeneration({
      status: 'failed',
      stage: 'done',
      error: { code: 'MODEL_ERROR', message: '模型超时' },
    })
    harness = await mountWithDetail(confirmedProject(), [
      { body: detail(confirmedProject(), [successGeneration(), queuedGeneration()]) },
    ])
    harness.fetch.route('GET', generationPath, () => ({ body: failed }))
    await screen.findByRole('heading', { name: '春季托特包主图' })

    await harness.clock.advance(2000)
    expect(await screen.findByText('最近生成失败：模型超时', { selector: '.generation-latest-error' })).toBeVisible()
    expect(screen.queryByText('连接中断，生成状态暂不可知。')).toBeNull()
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    await harness.clock.advance(4000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
  })

  it('业务结果未确认也是终态，保留提示并停止自动查询', async () => {
    const unknown = queuedGeneration({
      status: 'unknown',
      stage: 'done',
      error: null,
    })
    harness = await mountWithDetail(confirmedProject(), [
      { body: detail(confirmedProject(), [queuedGeneration()]) },
    ])
    harness.fetch.route('GET', generationPath, () => ({ body: unknown }))
    await screen.findByRole('heading', { name: '春季托特包主图' })

    await harness.clock.advance(2000)
    expect(
      await screen.findByText('最近生成结果未确认；上次请求可能已提交并可能已计费。再次生成前需确认风险。', {
        selector: '.generation-latest-error',
      }),
    ).toBeVisible()
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    await harness.clock.advance(4000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
  })

  it('隐藏页面停止并中止查询且不计失败，恢复可见后立即读取', async () => {
    harness = await mountWithDetail(confirmedProject(), [
      { body: detail(confirmedProject(), [queuedGeneration()]) },
    ])
    harness.fetch.route('GET', generationPath, () => ({
      body: queuedGeneration({ status: 'running', stage: 'waiting_remote' }),
      delayMs: 3000,
    }))
    await screen.findByRole('heading', { name: '春季托特包主图' })

    await harness.clock.advance(1000)
    harness.visibility.hide()
    await harness.clock.advance(5000)
    expect(harness.fetch.count('GET', generationPath)).toBe(0)

    harness.visibility.show()
    await harness.clock.advance(0)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    await harness.clock.advance(1000)
    harness.visibility.hide()
    await harness.clock.advance(5000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    harness.visibility.show()
    await harness.clock.advance(0)
    await waitFor(() => expect(harness?.fetch.count('GET', generationPath)).toBe(2))
    expect(screen.queryByText('连接中断，生成状态暂不可知。')).toBeNull()
  })

  it('切换到其他项目后旧查询不会回写到新项目', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      fakeClock: true,
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        { method: 'GET', url: detailPath, responses: [{ body: detail(confirmedProject(), [queuedGeneration()]) }] },
        { method: 'GET', url: detailBPath, responses: [{ body: detail(sampleProject({ id: PROJECT_B, title: '夏季水杯主图', brief_status: 'review' })) }] },
      ],
    })
    harness.fetch.route('GET', generationPath, () => ({
      body: queuedGeneration({ status: 'running', stage: 'waiting_remote' }),
      delayMs: 4000,
    }))
    await screen.findByRole('heading', { name: '春季托特包主图' })
    await harness.clock.advance(2000)
    expect(harness.fetch.count('GET', generationPath)).toBe(1)

    window.history.pushState({}, '', '/?project=' + PROJECT_B)
    window.dispatchEvent(new PopStateEvent('popstate'))
    await screen.findByRole('heading', { name: '夏季水杯主图' })
    await harness.clock.advance(5000)
    expect(screen.getByRole('heading', { name: '夏季水杯主图' })).toBeVisible()
    expect(screen.queryByText('当前生成：第 2 版，正在生成。')).toBeNull()
    expect(harness.fetch.count('GET', generationPath)).toBe(1)
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
  })

  it('整理中断按服务端错误显示，不重新发送消息', async () => {
    const interrupted = sampleProject({
      brief_status: 'needs_input',
      revision: 1,
      prompt_revision: null,
      confirmed_revision: null,
      brief_error: { code: 'LLM_ERROR', message: '模型配置缺失' },
      brief: sampleBrief({ prompt: '' }),
    })
    harness = await mountWithDetail(organizingProject(), [
      { body: detail(organizingProject()) },
      { body: detail(interrupted) },
    ])
    await screen.findByRole('heading', { name: '春季托特包主图' })
    await harness.clock.advance(2000)

    expect(await screen.findByText('需求整理失败')).toBeVisible()
    expect(screen.getByText('模型配置缺失')).toBeVisible()
    expect(harness.fetch.requests.some((request) => request.path.endsWith('/messages'))).toBe(false)
    await harness.clock.advance(4000)
    expect(harness.fetch.count('GET', detailPath)).toBe(2)
  })
})
