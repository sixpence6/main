import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen } from '@testing-library/vue'
import { sessionRevision } from '@/auth/session'
import type {
  MessageAttachmentView,
  MessageView,
  ProjectDetailResponse,
  ProjectView,
  StagedMessageAttachmentView,
} from '@/api/types'
import type { WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  PROJECT_B,
  emptyConfig,
  sampleAsset,
  sampleMessage,
  sampleProject,
  sampleSummary,
} from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const DETAIL_A = '/api/projects/' + PROJECT_A
const DETAIL_B = '/api/projects/' + PROJECT_B
const WORKFLOW_A = DETAIL_A + '/workflow'
const WORKFLOW_B = DETAIL_B + '/workflow'
const STAGE_A = DETAIL_A + '/message-attachments'
const STAGE_B = DETAIL_B + '/message-attachments'
const MESSAGES_A = DETAIL_A + '/messages'
const MESSAGES_B = DETAIL_B + '/messages'
const ASSET_ONE = 'asset-attachment-one'
const ASSET_TWO = 'asset-attachment-two'
const ATTACHMENT_ONE = 'attachment-one'

function workflow(projectId: string, overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: projectId,
    run_id: 'run-124',
    stage: 'S03',
    status: 'active',
    project_revision: 3,
    requirement_revision: 3,
    intent_route: 'image_request',
    intent_action: 'new_request',
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: null,
    versions: [],
    usage: {
      daily_limit: 20,
      used: 1,
      remaining: 19,
      window_started_at: '2026-11-01T00:00:00.000Z',
      window_expires_at: '2026-11-02T00:00:00.000Z',
      personal_active: 1,
      personal_limit: 2,
      instance_active: 1,
      instance_limit: 4,
      project_active: 1,
      project_limit: 1,
    },
    updated_at: '2026-11-01T00:00:00.000Z',
    ...overrides,
  }
}

function project(id = PROJECT_A, revision = 3): ProjectView {
  return sampleProject({ id, title: id === PROJECT_A ? '项目甲' : '项目乙', revision })
}

function detail(
  messages: MessageView[] = [],
  id = PROJECT_A,
  revision = 3,
): ProjectDetailResponse {
  return {
    project: project(id, revision),
    messages,
    generations: [],
  }
}

function staged(assetId = ASSET_ONE, previewUrl = '/api/assets/' + assetId): StagedMessageAttachmentView {
  return {
    asset: sampleAsset({ id: assetId, kind: 'reference' }),
    usage: 'none',
    content_version: 'sha256-' + assetId,
    sha256: 'sha256-' + assetId,
    preview_url: previewUrl,
  }
}

function attachment(usage: MessageAttachmentView['usage'] = 'product'): MessageAttachmentView {
  return {
    id: ATTACHMENT_ONE,
    message_id: 'message-attachment-1',
    asset_id: ASSET_ONE,
    usage,
    content_version: 'sha256-' + ASSET_ONE,
    sha256: 'sha256-' + ASSET_ONE,
    mime_type: 'image/png',
    width: 1024,
    height: 1024,
    byte_size: 1234,
    preview_url: '/api/assets/' + ASSET_ONE,
    created_at: '2026-11-01T00:00:00.000Z',
  }
}

function userWithAttachment(usage: MessageAttachmentView['usage'] = 'product'): MessageView {
  return sampleMessage({
    id: 'message-attachment-1',
    project_revision: 4,
    content: '把这张图作为产品图参考，做一张春季主图',
    turn_kind: 'workflow',
    attachment: attachment(usage),
  })
}

function file(name: string, type = 'image/png'): File {
  return new File([new Uint8Array([137, 80, 78, 71])], name, { type })
}

async function waitForUploaded(name: string): Promise<void> {
  expect(await screen.findByText(name)).toBeVisible()
  expect(await screen.findByText('将随这条消息一起发送')).toBeVisible()
}

function composer(): HTMLTextAreaElement {
  return screen.getByRole('textbox', { name: '描述商品与图片需求' }) as HTMLTextAreaElement
}

async function upload(name: string, type = 'image/png'): Promise<void> {
  const input = screen.getByLabelText('选择参考图文件') as HTMLInputElement
  await harness!.user.upload(input, file(name, type))
}

describe('T12 唯一输入框的单图附件与自然语言用途', () => {
  it('选择、预览、替换和移除严格只有一张图，不出现用途下拉或第二输入表单', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: workflow(PROJECT_A) }))
        fetch.route('POST', STAGE_A, (request) => {
          const body = request.body as { file: File }
          const uploaded = body.file
          const assetId = uploaded.name === '第二张.jpg' ? ASSET_TWO : ASSET_ONE
          return { body: staged(assetId) }
        })
        fetch.route('DELETE', new RegExp('^' + STAGE_A + '/[^/]+$'), (request) => ({
          body: { asset_id: request.path.split('/').at(-1), discarded: true },
        }))
      },
    })

    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()
    await upload('第一张.png')
    await waitForUploaded('第一张.png')

    expect(harness.fetch.count('POST', STAGE_A)).toBe(1)
    expect((harness.fetch.last('POST', STAGE_A)?.body as { file: File }).file).toMatchObject({
      name: '第一张.png',
      type: 'image/png',
    })
    await harness.waitFor(() => {
      expect(screen.getByAltText('待发送的图片预览')).toHaveAttribute('src', '/api/assets/' + ASSET_ONE)
    })
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()

    await upload('第二张.jpg', 'image/jpeg')
    await waitForUploaded('第二张.jpg')
    expect(screen.getAllByAltText('待发送的图片预览')).toHaveLength(1)
    await harness.waitFor(() => {
      expect(screen.getByAltText('待发送的图片预览')).toHaveAttribute('src', '/api/assets/' + ASSET_TWO)
    })
    expect(harness.fetch.count('POST', STAGE_A)).toBe(2)
    expect(harness.fetch.count('DELETE', STAGE_A + '/' + ASSET_ONE)).toBe(1)

    await harness.user.click(screen.getByRole('button', { name: '移除图片' }))
    await harness.waitFor(() => {
      expect(screen.queryByAltText('待发送的图片预览')).not.toBeInTheDocument()
    })
    expect(harness.fetch.count('DELETE', STAGE_A + '/' + ASSET_TWO)).toBe(1)
  })

  it('粘贴和拖放替换同一张待发送图片，根页面首次发送时才创建项目并暂存', async () => {
    const order: string[] = []
    const requests: Array<{ method: string; path: string; body: unknown }> = []
    let sent = false
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('POST', '/api/projects', () => {
          order.push('create')
          return { body: project(PROJECT_A, 0) }
        })
        fetch.route('GET', DETAIL_A, () => ({
          body: detail(sent ? [userWithAttachment('product')] : [], PROJECT_A, sent ? 4 : 0),
        }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: workflow(PROJECT_A) }))
        fetch.route('POST', STAGE_A, (request) => {
          order.push('stage')
          requests.push({ method: request.method, path: request.path, body: request.body })
          return { body: staged() }
        })
        fetch.route('POST', MESSAGES_A, (request) => {
          order.push('message')
          requests.push({ method: request.method, path: request.path, body: request.body })
          sent = true
          return {
            body: {
              project: project(PROJECT_A, 4),
              user_message: userWithAttachment('product'),
              assistant_message: sampleMessage({
                id: 'assistant-attachment-1',
                role: 'assistant',
                content: '已记录这张产品图，接下来会按用途准备需求。',
                project_revision: 4,
              }),
              workflow: workflow(PROJECT_A, { project_revision: 4 }),
            },
          }
        })
      },
    })

    const input = composer()
    const first = file('粘贴图.png')
    fireEvent.paste(input, { clipboardData: { files: [first], items: [] } })
    await waitForUploaded('粘贴图.png')
    expect(harness.fetch.count('POST', '/api/projects')).toBe(0)
    expect(harness.fetch.count('POST', STAGE_A)).toBe(0)

    const second = file('拖放图.jpg', 'image/jpeg')
    const shell = input.closest('.composer-shell') as HTMLElement
    fireEvent.drop(shell, { dataTransfer: { files: [second], items: [] } })
    await waitForUploaded('拖放图.jpg')
    expect(screen.getAllByAltText('待发送的图片预览')).toHaveLength(1)
    expect(harness.fetch.count('POST', '/api/projects')).toBe(0)
    expect(harness.fetch.count('POST', STAGE_A)).toBe(0)

    await harness.user.type(input, '用这张图做一张春季托特包主图')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByAltText('随消息发送的图片，用途：product')).toBeVisible()
    expect(order).toEqual(['create', 'stage', 'message'])
    expect(harness.fetch.count('POST', STAGE_A)).toBe(1)
    expect(harness.fetch.count('POST', MESSAGES_A)).toBe(1)
    expect((requests[0]?.body as { file: File }).file).toMatchObject({ name: '拖放图.jpg', type: 'image/jpeg' })
    expect(requests[1]?.body).toMatchObject({
      content: '用这张图做一张春季托特包主图',
      expected_revision: 0,
      attachment_asset_id: ASSET_ONE,
    })
    expect((requests[1]?.body as { request_id: string }).request_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    )
    expect(harness.router.currentRoute.value.name).toBe('project-chat')
    expect(harness.router.currentRoute.value.params.projectId).toBe(PROJECT_A)
  })

  it('明确自然语言用途后消息关联真实附件，历史图片来自消息而不是当前项目缩略图', async () => {
    const requests: unknown[] = []
    let sent = false
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', DETAIL_A, () => ({
          body: detail(sent ? [userWithAttachment('product'), sampleMessage({ id: 'assistant-usage-1', role: 'assistant', content: '已按产品图记录这张附件。', project_revision: 4 })] : []),
        }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: workflow(PROJECT_A) }))
        fetch.route('POST', STAGE_A, () => ({ body: staged() }))
        fetch.route('POST', MESSAGES_A, (request) => {
          requests.push(request.body)
          sent = true
          return {
            body: {
              project: project(PROJECT_A, 4),
              user_message: userWithAttachment('product'),
              assistant_message: sampleMessage({
                id: 'assistant-usage-1',
                role: 'assistant',
                content: '已按产品图记录这张附件。',
                project_revision: 4,
              }),
              workflow: workflow(PROJECT_A, { project_revision: 4 }),
            },
          }
        })
      },
    })

    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()
    await upload('商品图.png')
    await waitForUploaded('商品图.png')
    const input = composer()
    await harness.user.type(input, '把这张图作为产品图参考，做一张春季主图')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByAltText('随消息发送的图片，用途：product')).toBeVisible()
    expect(screen.getByText('已按产品图记录这张附件。')).toBeVisible()
    expect(harness.fetch.count('POST', MESSAGES_A)).toBe(1)
    expect(requests[0]).toMatchObject({
      attachment_asset_id: ASSET_ONE,
      content: '把这张图作为产品图参考，做一张春季主图',
    })
    expect(harness.fetch.count('POST', /\/reference$/)).toBe(0)
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
  })

  it('用途不明确时只请求澄清，不猜测业务用途', async () => {
    let sent = false
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', DETAIL_A, () => ({
          body: detail(sent ? [userWithAttachment('none'), sampleMessage({ id: 'assistant-clarify-1', role: 'assistant', content: '这张图片要用作产品图、风格参考还是构图参考？', project_revision: 4, turn_kind: 'conversation' })] : []),
        }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: workflow(PROJECT_A) }))
        fetch.route('POST', STAGE_A, () => ({ body: staged() }))
        fetch.route('POST', MESSAGES_A, () => {
          sent = true
          return {
            body: {
              project: project(PROJECT_A, 4),
              user_message: userWithAttachment('none'),
              assistant_message: sampleMessage({
                id: 'assistant-clarify-1',
                role: 'assistant',
                content: '这张图片要用作产品图、风格参考还是构图参考？',
                project_revision: 4,
                turn_kind: 'conversation',
              }),
              workflow: workflow(PROJECT_A, { project_revision: 4 }),
            },
          }
        })
      },
    })

    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()
    await upload('不确定用途.png')
    await waitForUploaded('不确定用途.png')
    await harness.user.type(composer(), '这张图怎么用比较好？')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('这张图片要用作产品图、风格参考还是构图参考？')).toBeVisible()
    expect(await screen.findByAltText('随消息发送的图片，用途：none')).toBeVisible()
    expect(harness.fetch.count('POST', MESSAGES_A)).toBe(1)
  })

  it('上传失败显示安全原因并阻止无附件发送', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: workflow(PROJECT_A) }))
        fetch.route('POST', STAGE_A, () => ({
          status: 422,
          body: { error: { code: 'INVALID_IMAGE', message: '图片内容无法识别，请重新选择。' } },
        }))
      },
    })

    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()
    await upload('损坏.png')
    expect(await screen.findByRole('alert')).toHaveTextContent('图片内容无法识别，请重新选择。')
    expect(screen.getByAltText('待发送的图片预览')).toBeVisible()

    await harness.user.type(composer(), '用这张图做产品图')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))
    await new Promise((resolve) => setTimeout(resolve, 30))
    expect(harness.fetch.count('POST', STAGE_A)).toBe(2)
    expect(harness.fetch.count('POST', MESSAGES_A)).toBe(0)
    expect(screen.getByRole('alert')).toHaveTextContent('图片内容无法识别，请重新选择。')
  })

  it('切换对话前提醒未发送图片，放弃后清除预览和暂存附件', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', '/api/projects', () => ({
          body: { items: [sampleSummary({ id: PROJECT_A, title: '项目甲' }), sampleSummary({ id: PROJECT_B, title: '项目乙' })], next_cursor: null },
        }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail([], PROJECT_A) }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: workflow(PROJECT_A) }))
        fetch.route('GET', DETAIL_B, () => ({ body: detail([], PROJECT_B) }))
        fetch.route('GET', WORKFLOW_B, () => ({ body: workflow(PROJECT_B) }))
        fetch.route('POST', STAGE_A, () => ({ body: staged() }))
        fetch.route('DELETE', STAGE_A + '/' + ASSET_ONE, () => ({ body: { asset_id: ASSET_ONE, discarded: true } }))
      },
    })

    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()
    await upload('待切换.png')
    await waitForUploaded('待切换.png')
    await harness.user.click(screen.getByRole('button', { name: /项目乙/ }))

    const dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    expect(dialog).toHaveTextContent('尚未发送的图片')
    expect(screen.getByRole('heading', { name: '项目甲' })).toBeVisible()

    await harness.user.click(screen.getByRole('button', { name: '放弃修改' }))
    expect(await screen.findByRole('heading', { name: '项目乙' })).toBeVisible()
    expect(screen.queryByAltText('待发送的图片预览')).not.toBeInTheDocument()
    expect(harness.fetch.count('DELETE', STAGE_A + '/' + ASSET_ONE)).toBe(1)
    expect(harness.router.currentRoute.value.params.projectId).toBe(PROJECT_B)
  })

  it('登录生命周期变化清除待发送图片，晚到上传不写入新上下文', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: workflow(PROJECT_A) }))
        fetch.route('POST', STAGE_A, () => ({ delayMs: 120, body: staged() }))
        fetch.route('DELETE', STAGE_A + '/' + ASSET_ONE, () => ({ body: { asset_id: ASSET_ONE, discarded: true } }))
      },
    })

    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()
    await upload('迟到上传.png')
    expect(await screen.findByText('正在上传…')).toBeVisible()
    sessionRevision.value += 1
    await new Promise((resolve) => setTimeout(resolve, 180))

    expect(screen.queryByAltText('待发送的图片预览')).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', MESSAGES_A)).toBe(0)
    expect(harness.fetch.count('DELETE', STAGE_A + '/' + ASSET_ONE)).toBe(1)
  })
})
