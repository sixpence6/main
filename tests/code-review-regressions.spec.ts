import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
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

const detailPathA = '/api/projects/' + PROJECT_A
const detailPathB = '/api/projects/' + PROJECT_B
const generationsPathA = detailPathA + '/generations'

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
    ...overrides,
  })
}

function succeededGeneration(overrides: Partial<GenerationView> = {}): GenerationView {
  const asset = sampleAsset({ id: 'asset-1', kind: 'generated' })
  return sampleGeneration({
    id: 'generation-1',
    project_id: PROJECT_A,
    version_no: 1,
    status: 'succeeded',
    stage: 'done',
    result_asset_id: asset.id,
    result_asset: asset,
    ...overrides,
  })
}

async function mountProject(project: ProjectView, generations: GenerationView[] = [], config = configuredConfig()) {
  harness = await mountWorkspace({
    config,
    viewportWidth: 1440,
    url: '/?project=' + PROJECT_A,
    routes: [{ method: 'GET', url: detailPathA, responses: [{ body: detail(project, generations) }] }],
  })
  return harness
}

function pngBytes(): Uint8Array<ArrayBuffer> {
  const base64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
  return Uint8Array.from(atob(base64), (char) => char.charCodeAt(0)) as Uint8Array<ArrayBuffer>
}

function jpegBytes(trimEnd = false): Uint8Array<ArrayBuffer> {
  const bytes = Uint8Array.from([
    0xff, 0xd8,
    0xff, 0xc0, 0x00, 0x0b, 0x08, 0x00, 0x01, 0x00, 0x01, 0x01, 0x01, 0x11, 0x00,
    0xff, 0xda, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3f, 0x00,
    0xff, 0xd9,
  ])
  return (trimEnd ? bytes.slice(0, -2) : bytes) as Uint8Array<ArrayBuffer>
}

async function chooseFile(file: File): Promise<void> {
  const input = screen.getByLabelText('选择参考图文件')
  await waitFor(() => expect(input).toBeEnabled())
  await fireEvent.change(input, { target: { files: [file] } })
}

describe('代码审查修复回归', () => {
  it('打开有成功版本的项目默认展示最新结果，而不是先回到对话', async () => {
    const generation = succeededGeneration()
    await mountProject(confirmedProject(), [generation])

    expect(await screen.findByRole('heading', { name: '查看结果' })).toBeVisible()
    expect(await screen.findByRole('img', { name: '生成结果图' })).toHaveAttribute(
      'src',
      '/api/assets/' + generation.result_asset_id,
    )
  })

  it('最近一次生成失败时默认打开对话，保留失败信息供用户处理', async () => {
    const failed = sampleGeneration({
      status: 'failed',
      stage: 'done',
      result_asset_id: null,
      result_asset: null,
      error: { code: 'VISUAL_TASK_FAILED', message: '模型处理失败' },
    })
    await mountProject(confirmedProject(), [failed])

    expect(await screen.findByRole('heading', { name: '春季托特包主图' })).toBeVisible()
    expect(screen.getByText('最近生成失败：模型处理失败')).toBeVisible()
    const nav = screen.getByRole('navigation', { name: '工作阶段' })
    expect(within(nav).getByRole('button', { name: '查看结果' })).toHaveAttribute('aria-disabled', 'true')
  })

  it('有生成记录时生成图片阶段解锁，并把焦点移到生成概览', async () => {
    await mountProject(sampleProject({ brief_status: 'draft' }), [succeededGeneration()])
    await screen.findByRole('heading', { name: '查看结果' })

    const nav = screen.getByRole('navigation', { name: '工作阶段' })
    const generate = within(nav).getByRole('button', { name: '生成图片' })
    await waitFor(() => expect(generate).not.toHaveAttribute('aria-disabled', 'true'))
    await harness!.user.click(generate)

    const heading = await screen.findByRole('heading', { name: '生成概览' })
    await waitFor(() => expect(document.activeElement).toBe(heading))
  })

  it('签名正确但 CRC 损坏的 PNG 在本地被拒绝，不创建项目或上传', async () => {
    await mountProject(sampleProject())
    await screen.findByRole('heading', { name: '春季托特包主图' })
    const bytes = pngBytes()
    bytes[45] = bytes[45] ^ 0xff
    await chooseFile(new File([bytes], 'broken.png', { type: 'image/png' }))

    expect(
      await screen.findByText('无法读取这张图片，请选择未损坏的 PNG 或 JPEG 文件。'),
    ).toBeVisible()
    expect(harness!.fetch.writes).toHaveLength(0)
  })

  it('缺少结束标记的 JPEG 在本地被拒绝，不创建项目或上传', async () => {
    await mountProject(sampleProject())
    await screen.findByRole('heading', { name: '春季托特包主图' })
    await chooseFile(new File([jpegBytes(true)], 'broken.jpg', { type: 'image/jpeg' }))

    expect(
      await screen.findByText('无法读取这张图片，请选择未损坏的 PNG 或 JPEG 文件。'),
    ).toBeVisible()
    expect(harness!.fetch.writes).toHaveLength(0)
  })

  it('模型只声明 WebP 参考图时说明不支持 PNG/JPEG，并禁用选择', async () => {
    const config = configuredConfig()
    config.visual.capabilities.reference_mime_types = ['image/webp']
    await mountProject(sampleProject(), [], config)
    await screen.findByRole('heading', { name: '春季托特包主图' })

    expect(screen.getByText('当前模型未声明支持 PNG 或 JPEG 参考图。')).toBeVisible()
    expect(screen.getByLabelText('选择参考图文件')).toBeDisabled()
  })

  it('消息冲突时保留未发送文字，可查看只读副本或加载最新需求', async () => {
    const project = confirmedProject()
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        {
          method: 'GET',
          url: detailPathA,
          responses: [{ body: detail(project) }, { body: detail(project) }],
        },
      ],
    })
    harness.fetch.route('POST', detailPathA + '/messages', () => ({
      status: 409,
      body: { error: { code: 'REVISION_CONFLICT', message: '修订冲突' } },
    }))
    await screen.findByRole('heading', { name: '春季托特包主图' })

    const draft = '这条消息没有发送，但必须保留。'
    await harness.user.type(screen.getByLabelText('描述商品与图片需求'), draft)
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    const conflictTitle = await screen.findByText('需求已在别处更新，这条消息未发送。')
    const conflict = conflictTitle.closest('.composer-conflict')
    expect(conflict).not.toBeNull()
    await harness.user.click(within(conflict as HTMLElement).getByRole('button', { name: '只读查看本地内容' }))
    expect(screen.getByLabelText('未发送文字的只读本地副本')).toHaveValue(draft)

    await harness.user.click(within(conflict as HTMLElement).getByRole('button', { name: '加载最新需求' }))
    await waitFor(() => expect(screen.queryByText('需求已在别处更新，这条消息未发送。')).toBeNull())
    expect(harness.fetch.count('GET', detailPathA)).toBe(2)
    expect(screen.getByLabelText('描述商品与图片需求')).toHaveValue(draft)
    expect(harness.fetch.count('POST', detailPathA + '/messages')).toBe(1)
  })

  it('2xx 响应体不是合法 JSON 时按提交回执不明处理，并保留恢复凭据', async () => {
    const project = confirmedProject()
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [{ method: 'GET', url: detailPathA, responses: [{ body: detail(project) }] }],
    })
    harness.fetch.route('POST', generationsPathA, () => ({ status: 202, body: '{not-json' }))
    await screen.findByRole('heading', { name: '春季托特包主图' })

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))

    expect(
      await screen.findByText('提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。'),
    ).toBeVisible()
    expect(harness.fetch.count('POST', generationsPathA)).toBe(1)
    const entries = harness.storage.session.entries()
    expect(Object.keys(entries)).toContain('ecommerce-generation-pending:' + PROJECT_A)
  })

  it('不同项目的未确认提交凭据同时保留，不会互相覆盖', async () => {
    const projectB = confirmedProject({ id: PROJECT_B, title: '陶瓷马克杯', brief: sampleBrief({ product_description: '陶瓷马克杯' }) })
    const oldCredential = {
      projectId: PROJECT_A,
      requestId: 'request-a',
      confirmedRevision: 2,
      acknowledgeUnknownPresent: false,
      acknowledgeUnknownValue: null,
      generationId: null,
    }
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_B,
      sessionState: {
        ['ecommerce-generation-pending:' + PROJECT_A]: JSON.stringify(oldCredential),
      },
      routes: [{ method: 'GET', url: detailPathB, responses: [{ body: detail(projectB) }] }],
    })
    harness.fetch.networkError('POST', detailPathB + '/generations')
    await screen.findByRole('heading', { name: '陶瓷马克杯' })

    await harness.user.click(screen.getByRole('button', { name: '生成首图' }))
    await screen.findByText('提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。')

    const entries = harness.storage.session.entries()
    expect(entries['ecommerce-generation-pending:' + PROJECT_A]).toBe(JSON.stringify(oldCredential))
    expect(entries['ecommerce-generation-pending:' + PROJECT_B]).toBeTruthy()
    expect(entries['ecommerce-generation-pending']).toBeUndefined()
  })

  it('模型状态抽屉显示真实的健康检查结果', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      routes: [{ method: 'GET', url: '/api/health', responses: [{ body: { status: 'ok' } }] }],
    })

    await harness.user.click(screen.getByRole('button', { name: '模型状态' }))
    expect(await screen.findByText('服务健康检查：正常')).toBeVisible()
    expect(harness.fetch.count('GET', '/api/health')).toBe(1)
  })
})
