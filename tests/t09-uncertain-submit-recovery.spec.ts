import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/vue'
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

const CREDENTIAL_KEY = 'ecommerce-generation-pending'
const detailPath = '/api/projects/' + PROJECT_A
const generationsPath = '/api/projects/' + PROJECT_A + '/generations'
const requestId = 'request-restored-1'

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
    id: 'generation-restored',
    version_no: 2,
    request_id: requestId,
    status: 'queued',
    stage: 'waiting_slot',
    result_asset_id: null,
    result_asset: null,
    completed_at: null,
    ...overrides,
  })
}

function succeededGeneration(overrides: Partial<GenerationView> = {}): GenerationView {
  const asset = sampleAsset({ id: 'asset-generated-1', kind: 'generated' })
  return sampleGeneration({
    id: 'generation-restored',
    version_no: 1,
    request_id: requestId,
    status: 'succeeded',
    stage: 'done',
    result_asset_id: asset.id,
    result_asset: asset,
    ...overrides,
  })
}

function credential(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    projectId: PROJECT_A,
    requestId,
    confirmedRevision: 3,
    acknowledgeUnknownPresent: false,
    acknowledgeUnknownValue: null,
    generationId: null,
    ...overrides,
  }
}

function sessionState(value: Record<string, unknown>): Record<string, string> {
  return { [CREDENTIAL_KEY]: JSON.stringify(value) }
}

async function mountRefresh(options: {
  project?: ProjectView
  generations?: GenerationView[]
  stored?: Record<string, string>
  setupStorage?: (storage: WorkspaceHarness['storage']) => void
  fakeClock?: boolean
  expectResults?: boolean
  knownGeneration?: GenerationView
} = {}): Promise<WorkspaceHarness> {
  harness = await mountWorkspace({
    config: configuredConfig(),
    fakeClock: options.fakeClock,
    viewportWidth: 1440,
    url: '/?project=' + PROJECT_A,
    sessionState: options.stored,
    setupStorage: options.setupStorage,
    routes: [
      {
        method: 'GET',
        url: detailPath,
        responses: [{ body: detail(options.project ?? confirmedProject(), options.generations ?? []) }],
      },
      ...(options.knownGeneration
        ? [
            {
              method: 'GET' as const,
              url: '/api/generations/' + options.knownGeneration.id,
              responses: [{ body: options.knownGeneration }],
            },
          ]
        : []),
    ],
  })
  if (options.expectResults) {
    await screen.findByRole('img', { name: '生成结果图' })
  } else {
    await screen.findByRole('heading', { name: '春季托特包主图' })
  }
  return harness
}

async function createAmbiguousCredential(current: WorkspaceHarness): Promise<Record<string, string>> {
  current.fetch.route('POST', generationsPath, () => ({ networkError: 'Failed to fetch' }))
  await current.user.click(screen.getByRole('button', { name: '生成首图' }))
  await screen.findByText('提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。')
  return current.storage.session.entries()
}

describe('T09 生成提交核实与恢复', () => {
  it('回执不明时只写入最小原始提交凭据，不保存卡片、图片或草稿', async () => {
    harness = await mountRefresh({
      setupStorage: undefined,
    })
    let requestBody: Record<string, unknown> = {}
    harness.fetch.route('POST', generationsPath, (request) => {
      requestBody = request.body as Record<string, unknown>
      return { networkError: 'Failed to fetch' }
    })

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。')

    expect(harness.fetch.count('POST', generationsPath)).toBe(1)
    expect(requestBody).toEqual({
      request_id: expect.stringMatching(
        /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      ),
      confirmed_revision: 3,
    })
    const entries = harness.storage.session.entries()
    expect(Object.keys(entries)).toEqual([CREDENTIAL_KEY])
    const saved = JSON.parse(entries[CREDENTIAL_KEY]) as Record<string, unknown>
    expect(Object.keys(saved).sort()).toEqual(
      [
        'acknowledgeUnknownPresent',
        'acknowledgeUnknownValue',
        'confirmedRevision',
        'generationId',
        'projectId',
        'requestId',
      ].sort(),
    )
    expect(saved).toEqual({
      projectId: PROJECT_A,
      requestId: requestBody.request_id,
      confirmedRevision: 3,
      acknowledgeUnknownPresent: false,
      acknowledgeUnknownValue: null,
      generationId: null,
    })
    expect(JSON.stringify(saved)).not.toContain('米色亚麻托特包')
    expect(JSON.stringify(saved)).not.toContain('prompt')
  })

  it('刷新后按原 request_id 未匹配记录时保留待核实，不自动重发', async () => {
    harness = await mountRefresh({ stored: sessionState(credential()) })

    expect(
      screen.getByText('提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。'),
    ).toBeVisible()
    expect(screen.getByText('已恢复上次未确认的原始提交参数。可先只读核实，或明确恢复本次提交。')).toBeVisible()
    expect(screen.getByText('仍未查到这次生成提交的记录；这不代表失败。可先只读核实，或明确恢复本次提交。')).toBeVisible()
    expect(screen.getByRole('button', { name: '核实提交结果' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '恢复本次提交' })).toBeVisible()
    expect(
      screen.getByText('恢复本次提交仅重放原始请求参数；如果原请求未被受理，可能执行这次生成。'),
    ).toBeVisible()
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
    expect(harness.storage.session.getItem(CREDENTIAL_KEY)).not.toBeNull()
  })

  it('刷新后按原 request_id 恢复已有生成记录并继续读取，不自动 POST', async () => {
    harness = await mountRefresh({
      stored: sessionState(credential()),
      generations: [queuedGeneration()],
      fakeClock: true,
    })

    expect(
      await screen.findByText('已恢复上次生成提交，正在继续读取任务状态。', {
        selector: '.status-notice',
      }),
    ).toBeVisible()
    expect(screen.getByText('当前生成：第 2 版，等待生成。')).toBeVisible()
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
    expect(harness.storage.session.getItem(CREDENTIAL_KEY)).toBeNull()
  })

  it('刷新后匹配已成功记录时恢复结果视图并清理凭据', async () => {
    harness = await mountRefresh({
      stored: sessionState(credential()),
      generations: [succeededGeneration()],
      expectResults: true,
    })

    expect(await screen.findByRole('heading', { name: '查看结果' })).toBeVisible()
    expect(screen.getByRole('img', { name: '生成结果图' })).toBeVisible()
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
    expect(harness.storage.session.getItem(CREDENTIAL_KEY)).toBeNull()
  })

  it('凭据含生成记录 ID 时，项目详情未包含该记录仍能核实并恢复', async () => {
    const known = queuedGeneration({ id: 'generation-known', version_no: 4 })
    harness = await mountRefresh({
      stored: sessionState(credential({ generationId: 'generation-known' })),
      knownGeneration: known,
    })

    expect(
      await screen.findByText('已恢复上次生成提交，正在继续读取任务状态。', {
        selector: '.status-notice',
      }),
    ).toBeVisible()
    expect(harness.fetch.count('GET', '/api/generations/generation-known')).toBe(1)
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
    expect(harness.storage.session.getItem(CREDENTIAL_KEY)).toBeNull()
    expect(screen.getByText('当前生成：第 4 版，等待生成。')).toBeVisible()
  })

  it('只读核实未命中不判失败，保留待核实与原始凭据', async () => {
    harness = await mountRefresh({ stored: sessionState(credential()) })
    const verify = screen.getByRole('button', { name: '核实提交结果' })
    await harness.user.click(verify)

    expect(
      await screen.findByText('仍未查到这次生成提交的记录；这不代表失败，请稍后再核实，不要重复提交。'),
    ).toBeVisible()
    expect(
      screen.getByText('提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。'),
    ).toBeVisible()
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
    expect(harness.storage.session.getItem(CREDENTIAL_KEY)).not.toBeNull()
  })

  it.each([
    ['缺席', false, null, {}],
    ['显式 false', true, false, { acknowledge_unknown: false }],
    ['显式 true', true, true, { acknowledge_unknown: true }],
  ])(
    '手动恢复时原样重放 acknowledge_unknown %s，并使用已变化的当前项目下原 confirmed_revision',
    async (_label, present, value, expectedFlag) => {
      harness = await mountRefresh({
        stored: sessionState(
          credential({
            acknowledgeUnknownPresent: present,
            acknowledgeUnknownValue: value,
          }),
        ),
        project: confirmedProject({ revision: 9, prompt_revision: 9, confirmed_revision: 9 }),
      })
      let body: Record<string, unknown> | null = null
      harness.fetch.route('POST', generationsPath, (request) => {
        body = request.body as Record<string, unknown>
        return { status: 202, body: { generation: queuedGeneration() } }
      })

      await harness.user.click(screen.getByRole('button', { name: '恢复本次提交' }))
      expect(
        await screen.findByText('已恢复本次提交，生成记录已恢复。', { selector: '.status-notice' }),
      ).toBeVisible()

      expect(harness.fetch.count('POST', generationsPath)).toBe(1)
      expect(body).toEqual({
        request_id: requestId,
        confirmed_revision: 3,
        ...expectedFlag,
      })
      expect(harness.storage.session.getItem(CREDENTIAL_KEY)).toBeNull()
      expect(screen.getByText('当前生成：第 2 版，等待生成。')).toBeVisible()
    },
  )

  it('存储写入失败时仍保留内存原请求，并可恢复同一次提交', async () => {
    harness = await mountRefresh()
    harness.storage.session.failWrites = true
    let calls = 0
    harness.fetch.route('POST', generationsPath, () => {
      calls += 1
      if (calls === 1) return { networkError: 'Failed to fetch' }
      return { status: 202, body: { generation: queuedGeneration() } }
    })

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    expect(
      await screen.findByText('无法写入会话存储；本次提交仍保留在当前页面，刷新或关闭页面后将无法恢复。'),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: '恢复本次提交' })).toBeVisible()

    harness.storage.session.failWrites = false
    await harness.user.click(screen.getByRole('button', { name: '恢复本次提交' }))
    expect(
      await screen.findByText('已恢复本次提交，生成记录已恢复。', { selector: '.status-notice' }),
    ).toBeVisible()
    expect(harness.fetch.count('POST', generationsPath)).toBe(2)
  })

  it('恢复操作防重，不创建新的 request_id', async () => {
    harness = await mountRefresh({ stored: sessionState(credential()) })
    let body: Record<string, unknown> | null = null
    harness.fetch.route('POST', generationsPath, (request) => {
      body = request.body as Record<string, unknown>
      return {
        delayMs: 80,
        status: 202,
        body: { generation: queuedGeneration() },
      }
    })
    const resume = screen.getByRole('button', { name: '恢复本次提交' })
    fireEvent.click(resume)
    fireEvent.click(resume)

    expect(
      await screen.findByText('已恢复本次提交，生成记录已恢复。', { selector: '.status-notice' }),
    ).toBeVisible()
    expect(harness.fetch.count('POST', generationsPath)).toBe(1)
    expect(body).toEqual({ request_id: requestId, confirmed_revision: 3 })
  })

  it.each([
    ['畸形 JSON', '{broken'],
    ['项目不匹配', JSON.stringify(credential({ projectId: '33333333-3333-4333-8333-333333333333' }))],
    ['缺少 requestId', JSON.stringify(credential({ requestId: '' }))],
  ])('无效凭据（%s）不触发恢复、不显示提交异常也不发请求', async (_label, raw) => {
    harness = await mountRefresh({ stored: { [CREDENTIAL_KEY]: raw } })

    expect(
      screen.queryByText('提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。'),
    ).toBeNull()
    expect(screen.queryByRole('button', { name: '恢复本次提交' })).toBeNull()
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
    expect(harness.fetch.requests.some((item) => item.path.endsWith('/messages'))).toBe(false)
  })

  it('会话存储读取失败时工作台仍可用，不误判为待核实', async () => {
    harness = await mountRefresh({
      setupStorage: (storage) => {
        storage.session.failReads = true
      },
    })

    expect(
      screen.queryByText('提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。'),
    ).toBeNull()
    expect(screen.getByRole('button', { name: '生成首图' })).toBeEnabled()
    expect(harness.fetch.count('POST', generationsPath)).toBe(0)
  })
})
