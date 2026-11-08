import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { MessageView, ProjectDetailResponse, ProjectView } from '@/api/types'
import type { PromptPreviewView, WorkflowAction, WorkflowView } from '@/api/workflow'
import type { FetchController } from './support/fetch'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleMessage,
  sampleProject,
} from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const DETAIL_PATH = '/api/projects/' + PROJECT_A
const WORKFLOW_PATH = DETAIL_PATH + '/workflow'
const MESSAGES_PATH = DETAIL_PATH + '/messages'
const ACTIONS_PATH = WORKFLOW_PATH + '/actions/'
const ACTIONS_ROUTE = new RegExp('^' + ACTIONS_PATH + '[^/]+$')

const SUGGESTIONS = [
  '把这张产品图换成秋日户外场景，保留包装质感',
  '为这组新品生成一张极简台面主图，冷白背景',
  '做一张 1:1 促销主视觉，石墨黑底，留出价格位置',
]

const FINAL_BRIEF = {
  id: 'artifact-final-approved',
  kind: 'final_brief',
  revision_no: 1,
  status: 'approved' as const,
  digest: 'digest-final-approved',
  payload: {
    product_description: '米色亚麻托特包',
    purpose: '电商主图',
    creative_direction: '温暖自然，突出材质',
    style_keywords: ['暖色', '简洁'],
    composition: '居中构图，浅米色背景',
    lighting: '柔和自然光',
    texts: 'WARM MORNING',
    size: '1024x1024',
  },
  created_at: '2026-11-01T00:00:00.000Z',
}

const PROMPT_PREVIEW: PromptPreviewView = {
  id: 'preview-1',
  status: 'awaiting_confirmation',
  request_digest: 'digest-preview-1',
  request: {
    positive_prompt: '米色亚麻托特包，暖色棚拍，居中构图，文案 WARM MORNING',
    negative_prompt: '不要凌乱背景，不要多余文字',
    prompt: '米色亚麻托特包，暖色棚拍，居中构图，文案 WARM MORNING',
    output: { count: 1, size: '1024x1024', response_format: 'b64_json' },
    references: [],
  },
  created_at: '2026-11-01T00:00:00.000Z',
  can_confirm: true,
  command_context: { expected_revision: 5, nonce: 'context-1' },
}

function action(id: string, label: string): WorkflowAction {
  return {
    id,
    label,
    kind: 'button',
    requires_text: false,
    text_label: null,
    requires_confirmation: false,
  }
}

function view(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-147',
    stage: 'S03',
    status: 'active',
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

function project(revision = 5): ProjectView {
  return sampleProject({
    id: PROJECT_A,
    title: '米色亚麻托特包',
    revision,
    prompt_revision: revision,
    confirmed_revision: revision,
    brief_status: 'review',
    brief: null as never,
  })
}

function detail(messages: MessageView[] = [], revision = 5): ProjectDetailResponse {
  return {
    project: project(revision),
    messages,
    generations: [],
  }
}

function promptView(): WorkflowView {
  return view({
    stage: 'S07',
    status: 'needs_input',
    project_revision: 5,
    requirement_revision: 3,
    artifacts: [FINAL_BRIEF],
    available_actions: [action('confirm_prompt', '确认使用以上提示词生成')],
    prompt_preview: PROMPT_PREVIEW,
  })
}

function completedView(): WorkflowView {
  return view({
    stage: 'S10',
    status: 'completed',
    project_revision: 5,
    requirement_revision: 3,
    available_actions: [],
    prompt_preview: null,
  })
}

function requirementView(): WorkflowView {
  return view({
    stage: 'S06',
    status: 'needs_input',
    project_revision: 5,
    requirement_revision: 3,
    artifacts: [FINAL_BRIEF],
    available_actions: [action('confirm_requirements', '确认需求')],
  })
}

function registerShell(fetch: FetchController): void {
  fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
}

describe('V2 空态与连接状态', () => {
  it('空态展示三条推荐提示词，点击第二条只填入草稿与聚焦，不发送请求', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/',
      setup: registerShell,
    })

    expect(screen.getByRole('heading', { name: '发来商品图片，或告诉我你想做什么样的电商图片' })).toBeVisible()
    expect(screen.getByText('用一句话描述商品、用途和画面感觉即可。需要确认时，我会把要点整理给你。')).toBeVisible()
    for (const suggestion of SUGGESTIONS) {
      expect(screen.getByRole('button', { name: suggestion })).toBeVisible()
    }
    expect(harness.fetch.count('POST')).toBe(0)

    const composer = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.waitFor(() => expect(composer).toHaveFocus())

    await harness.user.click(screen.getByRole('button', { name: SUGGESTIONS[1]! }))
    expect(composer).toHaveValue(SUGGESTIONS[1])
    expect(composer).toHaveFocus()
    expect(screen.queryByRole('button', { name: SUGGESTIONS[0]! })).not.toBeInTheDocument()
    expect(harness.fetch.count('POST')).toBe(0)

    await harness.user.clear(composer)
    expect(screen.getByRole('button', { name: SUGGESTIONS[0]! })).toBeVisible()

    await harness.user.type(composer, '继续调整')
    expect(screen.queryByRole('button', { name: SUGGESTIONS[0]! })).not.toBeInTheDocument()
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('读取中只有一条无动画状态行，读取失败显示行内错误并可单次重试', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 320,
      url: '/projects/' + PROJECT_A + '/chat',
      setup: (fetch) => {
        registerShell(fetch)
        fetch.sequence('GET', DETAIL_PATH, [
          { status: 500, body: { error: { code: 'INTERNAL', message: '暂时无法读取对话。' } } },
          { body: detail([sampleMessage({ id: 'message-after-retry', content: '已恢复的对话' })]), delayMs: 120 },
        ])
        fetch.route('GET', WORKFLOW_PATH, () => ({ body: completedView() }))
      },
    })

    const loadingLine = screen.getByText('正在读取对话…')
    expect(loadingLine).toHaveAttribute('role', 'status')
    expect(loadingLine.closest('.chat-loading')).not.toBeNull()

    const error = await screen.findByText('暂时无法读取对话。')
    expect(error.closest('[role="alert"]')).not.toBeNull()
    expect(error.closest('.chat-load-error')).not.toBeNull()
    expect(screen.queryByText(/stack/i)).not.toBeInTheDocument()

    const retry = screen.getByRole('button', { name: '重试' })
    await harness.user.click(retry)
    expect(retry).toBeDisabled()
    await harness.user.click(retry)

    await harness.waitFor(() => expect(harness!.fetch.count('GET', DETAIL_PATH)).toBe(2))
    await harness.waitFor(() => expect(retry).not.toBeDisabled())
    expect(harness.fetch.count('GET', DETAIL_PATH)).toBe(2)
    expect(harness.fetch.count('POST')).toBe(0)
  })
})

describe('V2 消息流与需求确认卡', () => {
  it('用户气泡、助手无气泡、附件灯箱、meta 时间与围栏代码块同时成立', async () => {
    const attachmentMessage = sampleMessage({
      id: 'message-user-attachment',
      sequence_no: 1,
      content: '请参考这张图做一张秋季主图。',
      attachment: {
        id: 'attachment-1',
        message_id: 'message-user-attachment',
        asset_id: 'asset-reference-1',
        usage: 'product',
        content_version: '1',
        sha256: 'a'.repeat(64),
        mime_type: 'image/png',
        width: 1024,
        height: 1024,
        byte_size: 120_000,
        preview_url: '/api/assets/asset-reference-1?preview=1',
        created_at: '2026-10-20T02:01:00.000Z',
      },
    })
    const codeMessage = sampleMessage({
      id: 'message-assistant-code',
      sequence_no: 2,
      role: 'assistant',
      content: '这是排版示例：\n```ts\nconst title = "秋日主图"\n```\n请保持正文可换行。',
    })

    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 320,
      url: '/projects/' + PROJECT_A + '/chat',
      setup: (fetch) => {
        registerShell(fetch)
        fetch.route('GET', DETAIL_PATH, () => ({ body: detail([attachmentMessage, codeMessage]) }))
        fetch.route('GET', WORKFLOW_PATH, () => ({ body: completedView() }))
      },
    })

    const userRow = (await screen.findByText('请参考这张图做一张秋季主图。')).closest('.message-row')
    expect(userRow).not.toBeNull()
    expect(userRow).toHaveClass('is-user')
    expect(userRow!.querySelector('.message-bubble')).not.toBeNull()
    expect(within(userRow as HTMLElement).getByText(/^你 · \d{2}:\d{2}$/)).toBeVisible()

    const assistantRow = screen.getByText(/^智能生图助手 · \d{2}:\d{2}$/).closest('.message-row')
    expect(assistantRow).not.toBeNull()
    expect(assistantRow).toHaveClass('is-assistant')
    expect(assistantRow!.querySelector('.message-bubble')).toBeNull()

    const code = assistantRow!.querySelector('pre.message-code')
    expect(code).not.toBeNull()
    expect(code).toHaveTextContent('const title = "秋日主图"')
    expect(code!.querySelector('code')).not.toBeNull()
    expect(code).toHaveClass('message-code')

    const attachment = within(userRow as HTMLElement).getByRole('button', { name: /参考图/ })
    await harness.user.click(attachment)
    const dialog = await screen.findByRole('dialog', { name: '图片大图' })
    expect(dialog).toBeVisible()
    expect(within(dialog).getByRole('img', { name: '参考图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-reference-1',
    )
    await harness.user.keyboard('{Escape}')
    expect(attachment).toHaveFocus()
  })

  it('需求确认卡只有一张主 CTA，确认后转只读状态且只提交一次', async () => {
    const calls: string[] = []
    let currentView = requirementView()
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup: (fetch) => {
        registerShell(fetch)
        fetch.route('GET', DETAIL_PATH, () => ({
          body: detail([sampleMessage({ id: 'message-requirement', content: '请做一张商品主图' })]),
        }))
        fetch.route('GET', WORKFLOW_PATH, () => ({ body: currentView }))
        fetch.route('POST', ACTIONS_ROUTE, (request) => {
          const id = request.path.slice(ACTIONS_PATH.length)
          calls.push(id)
          currentView = promptView()
          return { body: currentView, delayMs: 100 }
        })
      },
    })

    const card = await screen.findByRole('region', { name: '需求确认' })
    expect(within(card).getByRole('heading', { name: '需求确认' })).toBeVisible()
    expect(within(card).getByText('我先把你的描述整理成了这份需求，确认后我会准备提示词。')).toBeVisible()
    const cta = within(card).getByRole('button', { name: '确认需求并生成提示词' })
    expect(within(card).getAllByRole('button', { name: '确认需求并生成提示词' })).toHaveLength(1)

    await harness.user.click(cta)
    expect(cta).toBeDisabled()
    await harness.user.click(cta)

    await harness.waitFor(() => expect(within(card).getByRole('status')).toHaveTextContent('需求已确认'))
    expect(within(card).getByRole('status')).toHaveTextContent('正在准备提示词…')
    expect(within(card).queryByRole('button', { name: '确认需求并生成提示词' })).not.toBeInTheDocument()
    expect(calls).toEqual(['confirm_requirements'])
    expect(harness.fetch.count('POST', ACTIONS_PATH + 'confirm_requirements')).toBe(1)
  })

  it('发送中在 meta 追加提示，落库后不重复也不残留发送中', async () => {
    let sent = false
    const initialMessage = sampleMessage({ id: 'message-initial', content: '请做一张商品主图' })
    const userMessage = sampleMessage({ id: 'message-sent-user', content: '再亮一点' })
    const assistantMessage = sampleMessage({
      id: 'message-sent-assistant',
      role: 'assistant',
      content: '收到。',
    })

    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup: (fetch) => {
        registerShell(fetch)
        fetch.route('GET', DETAIL_PATH, () => ({
          body: sent
            ? detail([initialMessage, userMessage, assistantMessage])
            : detail([initialMessage]),
        }))
        fetch.route('GET', WORKFLOW_PATH, () => ({ body: completedView() }))
        fetch.route('POST', MESSAGES_PATH, () => {
          sent = true
          return {
            body: {
              project: project(),
              user_message: userMessage,
              assistant_message: assistantMessage,
              workflow: completedView(),
            },
            delayMs: 120,
          }
        })
      },
    })

    await screen.findByText('请做一张商品主图')
    expect(document.querySelectorAll('.message-row:not(.is-pending)')).toHaveLength(1)

    const composer = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, '再亮一点')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    await harness.waitFor(() => expect(document.querySelector('.message-row.is-pending')).not.toBeNull())
    const pending = document.querySelector('.message-row.is-pending') as HTMLElement
    expect(within(pending).getByText(/发送中…/)).toBeVisible()
    expect(within(pending).getByText(/^你 · \d{2}:\d{2}/)).toBeVisible()
    expect(pending.querySelector('.message-bubble')).not.toBeNull()
    expect(document.querySelectorAll('.message-row:not(.is-pending)')).toHaveLength(1)
    expect(screen.getAllByText('再亮一点')).toHaveLength(1)

    await screen.findByText('收到。')
    expect(document.querySelector('.message-row.is-pending')).toBeNull()
    expect(screen.queryByText(/发送中…/)).not.toBeInTheDocument()
    expect(screen.getAllByText('再亮一点')).toHaveLength(1)
  })
})
