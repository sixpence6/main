import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import type { MessageView, ProjectDetailResponse, ProjectView } from '@/api/types'
import type { WorkflowAction, WorkflowVersion, WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  emptyConfig,
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

const DETAIL_A = '/api/projects/' + PROJECT_A
const WORKFLOW_A = DETAIL_A + '/workflow'
const MESSAGES_A = DETAIL_A + '/messages'
const ACTIONS_ROUTE = new RegExp('^' + DETAIL_A + '/workflow/actions/[^/]+$')
const RUN_CURRENT = 'run-current'
const RUN_OLD = 'run-old'

function project(revision = 3): ProjectView {
  return sampleProject({ id: PROJECT_A, title: '项目甲', revision })
}

function detail(messages: MessageView[] = []): ProjectDetailResponse {
  return { project: project(), messages, generations: [] }
}

function usage() {
  return {
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
  }
}

function workflow(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-128',
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
    usage: usage(),
    updated_at: '2026-11-01T00:00:00.000Z',
    ...overrides,
  }
}

function action(id: string, label: string, overrides: Partial<WorkflowAction> = {}): WorkflowAction {
  return {
    id,
    label,
    kind: 'button',
    requires_text: false,
    text_label: null,
    requires_confirmation: false,
    effect: 'write',
    read_only: false,
    requires_model_call: false,
    retry_mode: 'none',
    automatic_retry: false,
    ...overrides,
  }
}

function finalBriefArtifact(): WorkflowView['artifacts'][number] {
  return {
    id: 'artifact-final-approved',
    kind: 'final_brief',
    revision_no: 1,
    status: 'approved',
    digest: 'digest-final-approved',
    payload: {
      product_description: '米色亚麻托特包',
      purpose: '电商主图',
      texts: ['WARM MORNING'],
      size: '1024x1024',
    },
    created_at: '2026-11-01T00:00:00.000Z',
  }
}

function requirementsView(): WorkflowView {
  return workflow({
    stage: 'S06',
    status: 'needs_input',
    project_revision: 4,
    artifacts: [finalBriefArtifact()],
    available_actions: [
      action('confirm_requirements', '确认需求并生成提示词', {
        kind: 'confirm',
        requires_confirmation: true,
      }),
    ],
  })
}

function promptView(canConfirm = true): WorkflowView {
  return workflow({
    stage: 'S07',
    status: 'needs_input',
    project_revision: 5,
    artifacts: [finalBriefArtifact()],
    available_actions: [
      action('confirm_prompt', '确认使用以上提示词生成', {
        kind: 'confirm',
        requires_confirmation: true,
      }),
    ],
    prompt_preview: {
      id: 'preview-1',
      status: 'ready',
      request_digest: 'digest-preview-1',
      request: {
        positive_prompt: '米色亚麻托特包，暖色棚拍，居中构图',
        negative_prompt: '不要凌乱背景',
        prompt: '米色亚麻托特包，暖色棚拍，居中构图',
        output: { count: 1, size: '1024x1024', response_format: 'b64_json' },
        references: [],
      },
      created_at: '2026-11-01T00:00:00.000Z',
      can_confirm: canConfirm,
      command_context: { expected_revision: 5, nonce: 'context-1' },
    },
  })
}

function version(overrides: Partial<WorkflowVersion> = {}): WorkflowVersion {
  return {
    run_id: RUN_CURRENT,
    sequence_no: 2,
    requirement_revision: 3,
    status: 'published',
    generation_job_id: 'job-current',
    asset_id: 'asset-current',
    filename: 'current.png',
    width: 1536,
    height: 1024,
    caption: '当前版本主图',
    published_at: '2026-10-21T06:02:00.000Z',
    brief: { product_description: '米色亚麻托特包' },
    prompt: { positive_prompt: '当前版本的已批准提示词', negative_prompt: '低清晰度' },
    quality: { status: 'passed' },
    can_download: true,
    created_at: '2026-10-21T06:00:00.000Z',
    ...overrides,
  }
}

function completedView(): WorkflowView {
  return workflow({
    stage: 'S11',
    status: 'completed',
    project_revision: 5,
    run_id: RUN_CURRENT,
    delivery: {
      id: 'delivery-current',
      status: 'published',
      asset_id: 'asset-current',
      filename: 'current.png',
      caption: '当前版本主图',
      published_at: '2026-10-21T06:02:00.000Z',
      created_at: '2026-10-21T06:02:00.000Z',
    },
    versions: [
      version({}),
      version({
        run_id: RUN_OLD,
        sequence_no: 1,
        requirement_revision: 2,
        generation_job_id: 'job-old',
        asset_id: 'asset-old',
        filename: 'old.png',
        width: 1024,
        height: 1024,
        caption: '旧版本主图',
        published_at: '2026-10-20T03:00:00.000Z',
        prompt: { positive_prompt: '旧版本的已批准提示词', negative_prompt: '不要多余文字' },
      }),
    ],
  })
}

async function mountProject(view: WorkflowView, setup?: (fetch: WorkspaceHarness['fetch']) => void): Promise<WorkspaceHarness> {
  const mounted = await mountWorkspace({
    config: emptyConfig(),
    viewportWidth: 1440,
    url: '/projects/' + PROJECT_A + '/chat',
    setup(fetch) {
      fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
      fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
      fetch.route('GET', WORKFLOW_A, () => ({ body: view }))
      setup?.(fetch)
    },
  })
  harness = mounted
  return mounted
}

function composer(): HTMLTextAreaElement {
  return screen.getByRole('textbox', { name: '描述商品与图片需求' }) as HTMLTextAreaElement
}

describe('T16 键盘、滚动、窄屏抽屉与图片放大', () => {
  it('Enter、Ctrl 或 Meta+Enter 发送，Shift+Enter 换行，中文 composition 不发送', async () => {
    const sent: MessageView[] = []
    let count = 0
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail(sent) }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: workflow() }))
        fetch.route('POST', MESSAGES_A, (request) => {
          count += 1
          const body = request.body as { content: string }
          const user = sampleMessage({
            id: 'user-' + count,
            role: 'user',
            content: body.content,
            project_revision: 3 + count,
          })
          const assistant = sampleMessage({
            id: 'assistant-' + count,
            role: 'assistant',
            content: '收到第 ' + count + ' 条',
            project_revision: 3 + count,
          })
          sent.push(user, assistant)
          return {
            body: {
              project: project(3 + count),
              user_message: user,
              assistant_message: assistant,
              workflow: workflow({ project_revision: 4 + count }),
            },
          }
        })
      },
    })

    const input = composer()
    await harness.user.type(input, '第一条')
    await harness.user.keyboard('{Enter}')
    await harness.waitFor(() => expect(harness!.fetch.count('POST', MESSAGES_A)).toBe(1))
    expect(input).toHaveValue('')

    await harness.user.type(input, '第二行')
    await harness.user.keyboard('{Shift>}{Enter}{/Shift}')
    expect(harness.fetch.count('POST', MESSAGES_A)).toBe(1)
    expect(input).toHaveValue('第二行\n')

    await harness.user.keyboard('{Control>}{Enter}{/Control}')
    await harness.waitFor(() => expect(harness!.fetch.count('POST', MESSAGES_A)).toBe(2))
    expect(input).toHaveValue('')

    await harness.user.type(input, '第三行')
    fireEvent.compositionStart(input)
    await harness.user.keyboard('{Enter}')
    expect(harness.fetch.count('POST', MESSAGES_A)).toBe(2)
    fireEvent.compositionEnd(input)

    await harness.user.keyboard('{Meta>}{Enter}{/Meta}')
    await harness.waitFor(() => expect(harness!.fetch.count('POST', MESSAGES_A)).toBe(3))
    expect(input).toHaveValue('')
  })

  it('上滚阅读后新内容不强拉到底，出现新内容提示，点击后才回到底部', async () => {
    await mountProject(requirementsView(), (fetch) => {
      fetch.route('POST', ACTIONS_ROUTE, () => ({ body: promptView() }))
    })

    const scroller = await screen.findByLabelText('对话内容')
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, value: 1200 })
    Object.defineProperty(scroller, 'clientHeight', { configurable: true, value: 400 })
    scroller.scrollTop = 0
    fireEvent.scroll(scroller)

    await harness!.user.click(await screen.findByRole('button', { name: '确认需求并生成提示词' }))
    expect(await screen.findByRole('region', { name: '提示词确认' })).toBeVisible()
    await harness!.waitFor(() => {
      expect(screen.getByRole('button', { name: '有新内容，回到底部' })).toBeVisible()
    })
    expect(scroller.scrollTop).toBe(0)

    await harness!.user.click(screen.getByRole('button', { name: '有新内容，回到底部' }))
    expect(scroller.scrollTop).toBe(1200)
    expect(screen.queryByRole('button', { name: '有新内容，回到底部' })).not.toBeInTheDocument()
  })

  it('320 和 768 使用同一侧栏单层抽屉，锁背景滚动、关闭后焦点回到触发按钮；1440 常驻侧栏', async () => {
    for (const width of [320, 768]) {
      const mounted = await mountWorkspace({ config: emptyConfig(), viewportWidth: width, url: '/' })
      harness = mounted
      const menu = await screen.findByRole('button', { name: '打开侧栏' })
      expect(menu).toHaveAttribute('aria-controls', 'workspace-side-drawer')
      expect(menu).toHaveAttribute('aria-expanded', 'false')
      expect(screen.queryByLabelText('对话侧栏')).not.toBeInTheDocument()

      await mounted.user.click(menu)
      const dialog = await screen.findByRole('dialog', { name: '工作区菜单' })
      expect(within(dialog).getByLabelText('对话侧栏')).toBeVisible()
      expect(menu).toHaveAttribute('aria-expanded', 'true')
      expect(document.body.style.overflow).toBe('hidden')
      const close = within(dialog).getByRole('button', { name: '关闭侧栏' })
      expect(close).toHaveFocus()

      await mounted.user.click(close)
      await mounted.waitFor(() => {
        expect(screen.queryByRole('dialog', { name: '工作区菜单' })).not.toBeInTheDocument()
      })
      expect(document.body.style.overflow).toBe('')
      expect(menu).toHaveFocus()
      mounted.dispose()
      harness = undefined
    }

    const wide = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440, url: '/' })
    harness = wide
    expect(await screen.findByLabelText('对话侧栏')).toBeVisible()
    expect(screen.queryByRole('button', { name: '打开侧栏' })).not.toBeInTheDocument()
  })

  it('图片放大有可见标签和键盘焦点，关闭后回到原图链接', async () => {
    await mountProject(completedView())
    const region = await screen.findByRole('region', { name: '图片历史' })
    const current = within(region).getByRole('article', { name: '版本 2 结果' })
    const link = within(current).getByRole('link', { name: '查看大图：当前版本主图' })
    expect(within(current).getByText('查看大图')).toBeVisible()

    await harness!.user.click(link)
    const dialog = await screen.findByRole('dialog', { name: '图片大图' })
    expect(within(dialog).getByRole('img', { name: '当前版本主图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-current',
    )
    const close = within(dialog).getByRole('button', { name: '关闭大图' })
    expect(close).toHaveFocus()
    expect(document.body.style.overflow).toBe('hidden')

    await harness!.user.keyboard('{Escape}')
    await harness!.waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
    expect(link).toHaveFocus()
    expect(document.body.style.overflow).toBe('')

    await harness!.user.click(link)
    await harness!.user.click(await screen.findByRole('button', { name: '关闭大图' }))
    await harness!.waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
    expect(link).toHaveFocus()
  })

  it('聊天及提示词输入框和关键操作均有可见标签且可聚焦', async () => {
    await mountProject(promptView())
    const input = composer()
    expect(input).toBeEnabled()
    await harness!.user.click(input)
    expect(input).toHaveFocus()
    await harness!.user.type(input, '准备发送')
    const attach = screen.getByRole('button', { name: '添加参考图' })
    const copy = screen.getByRole('button', { name: '复制提示词' })
    const confirm = screen.getByRole('button', { name: '确认使用以上提示词生成' })
    expect(attach).toBeVisible()
    expect(copy).toBeVisible()
    expect(confirm).toBeVisible()
    attach.focus()
    expect(attach).toHaveFocus()
    copy.focus()
    expect(copy).toHaveFocus()
    confirm.focus()
    expect(confirm).toHaveFocus()
    expect(screen.getAllByRole('textbox')).toHaveLength(3)
    expect(screen.getByRole('textbox', { name: '正向提示词' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: '反向提示词' })).toBeVisible()

    input.focus()
    expect(input).toHaveFocus()
    await harness!.user.tab()
    expect(screen.getByRole('button', { name: '发送' })).toHaveFocus()
  })
})
