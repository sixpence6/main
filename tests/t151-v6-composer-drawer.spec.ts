import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import type {
  MessageView,
  ProjectDetailResponse,
  ProjectView,
  StagedMessageAttachmentView,
} from '@/api/types'
import type { WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  emptyConfig,
  sampleAsset,
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
const STAGE_A = DETAIL_A + '/message-attachments'
const ASSET_A = 'asset-v6-preview'

const chatSource = readFileSync(resolve(__dirname, '../main/src/pages/ChatPage.vue'), 'utf8')
const chatStyle = chatSource.slice(chatSource.indexOf('<style scoped>'))
const workspaceSource = readFileSync(resolve(__dirname, '../main/src/styles/workspace.css'), 'utf8')
const sideDrawerSource = readFileSync(
  resolve(__dirname, '../main/src/components/SideDrawer.vue'),
  'utf8',
)
const accessibleDrawerSource = readFileSync(
  resolve(__dirname, '../main/src/components/AccessibleDrawer.vue'),
  'utf8',
)

function cssRule(source: string, selector: string): string {
  const start = source.indexOf(selector + ' {')
  if (start < 0) return ''
  const open = source.indexOf('{', start)
  const close = source.indexOf('}', open)
  return source.slice(open + 1, close)
}

function project(revision = 3): ProjectView {
  return sampleProject({ id: PROJECT_A, title: '项目甲', revision })
}

function detail(messages: MessageView[] = []): ProjectDetailResponse {
  return { project: project(), messages, generations: [] }
}

function workflow(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-151',
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

function staged(assetId = ASSET_A): StagedMessageAttachmentView {
  return {
    asset: sampleAsset({ id: assetId, kind: 'reference' }),
    usage: 'none',
    content_version: 'sha256-' + assetId,
    sha256: 'sha256-' + assetId,
    preview_url: '/api/assets/' + assetId,
  }
}

function file(name: string, type = 'image/png'): File {
  return new File([new Uint8Array([137, 80, 78, 71])], name, { type })
}

function composer(): HTMLTextAreaElement {
  return screen.getByRole('textbox', { name: '描述商品与图片需求' }) as HTMLTextAreaElement
}

async function mountChat(
  setup?: (fetch: WorkspaceHarness['fetch']) => void,
  viewportWidth = 1440,
): Promise<WorkspaceHarness> {
  const mounted = await mountWorkspace({
    config: emptyConfig(),
    viewportWidth,
    url: '/projects/' + PROJECT_A + '/chat',
    setup(fetch) {
      fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
      fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
      fetch.route('GET', WORKFLOW_A, () => ({ body: workflow() }))
      setup?.(fetch)
    },
  })
  harness = mounted
  return mounted
}

async function upload(name: string, type = 'image/png'): Promise<void> {
  const input = screen.getByLabelText('选择参考图文件') as HTMLInputElement
  await harness!.user.upload(input, file(name, type))
}

describe('T151 V6 输入区与移动抽屉', () => {
  it('输入区几何、焦点环与禁用发送使用 v3 令牌，文本域最多 8 行', () => {
    const shell = cssRule(chatStyle, '.composer-shell')
    expect(shell).toContain('width: min(var(--conversation-width), 100%)')
    expect(shell).toContain('margin: 0 auto')
    expect(shell).toMatch(/padding:\s*12px 16px/)
    expect(shell).toContain('border: 1px solid var(--line-hairline)')
    expect(shell).toContain('border-radius: var(--radius-md)')
    expect(shell).toContain('background: var(--surface-panel)')
    expect(shell).toContain('box-shadow: var(--shadow-composer)')

    const focus = cssRule(chatStyle, '.composer-shell:focus-within')
    expect(focus).toContain('border-color: var(--line-control)')
    expect(focus).toContain('outline: 2px solid var(--focus-ring-light)')
    expect(focus).toContain('outline-offset: 2px')

    const input = cssRule(chatStyle, '.composer-input')
    expect(input).toContain('min-height: 44px')
    expect(input).toContain('max-height: 212px')
    expect(input).toContain('overflow-y: auto')
    expect(input).toContain('border: 0')
    expect(input).toContain('font-size: var(--text-reading)')
    expect(input).toContain('line-height: 24px')

    const send = cssRule(chatStyle, '.send-button')
    expect(send).toContain('width: 36px')
    expect(send).toContain('height: 36px')
    expect(send).toContain('border-radius: var(--radius-sm)')
    const disabled = cssRule(chatStyle, '.send-button:disabled')
    expect(disabled).toContain('background: var(--surface-inset)')
    expect(disabled).toContain('color: var(--text-muted)')
    expect(disabled).toContain('opacity: 1')

    const thumb = cssRule(chatStyle, '.attachment-thumb')
    expect(thumb).toContain('width: 34px')
    expect(thumb).toContain('height: 42px')
    expect(thumb).toContain('border-radius: var(--radius-sm)')
    expect(thumb).toContain('background: var(--surface-media)')

    const filename = cssRule(chatStyle, '.attachment-preview-copy strong')
    expect(filename).toContain('font-size: var(--text-caption)')
    expect(filename).toContain('text-overflow: ellipsis')
    expect(filename).toContain('white-space: nowrap')

    const remove = cssRule(chatStyle, '.attachment-remove')
    expect(remove).toContain('width: 24px')
    expect(remove).toContain('height: 24px')
    expect(remove).toContain('color: var(--state-danger)')

    expect(sideDrawerSource).toContain('width="min(264px, calc(100vw - 32px))"')
    const panel = cssRule(workspaceSource, '.drawer-panel')
    expect(panel).toContain('border-radius: var(--radius-lg) 0 0 var(--radius-lg)')
    expect(panel).not.toContain('!important')
    expect(workspaceSource).not.toMatch(/width:\s*calc\(100vw - 32px\)\s*!important/)
    expect(cssRule(workspaceSource, '.drawer-backdrop')).toContain('background: var(--scrim)')
    expect(cssRule(workspaceSource, '.drawer-close')).toContain('width: 44px')
    expect(cssRule(workspaceSource, '.drawer-close')).toContain('height: 44px')
    expect(accessibleDrawerSource).toContain('setBackgroundInert')
    expect(accessibleDrawerSource).toContain("setAttribute('inert'")
    expect(accessibleDrawerSource).toContain('name="drawer-slide"')
  })

  it('320 下发送键始终可见，运行时可输入并封顶 8 行高度', async () => {
    await mountChat(undefined, 320)
    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()

    const send = screen.getByRole('button', { name: '发送' })
    expect(send).toBeVisible()
    expect(send).toBeDisabled()

    const input = composer()
    await harness!.user.type(input, '为托特包写一张春季主图')
    expect(send).toBeEnabled()

    Object.defineProperty(input, 'scrollHeight', { configurable: true, value: 1200 })
    fireEvent.input(input)
    await harness!.waitFor(() => expect(input.style.height).toBe('212px'))
  })

  it('Enter 发送、Shift+Enter 换行、Ctrl/Meta+Enter 发送，IME 组合中的 Enter 不发送', async () => {
    let count = 0
    await mountChat((fetch) => {
      fetch.route('POST', MESSAGES_A, (request) => {
        count += 1
        const body = request.body as { content: string }
        const user = sampleMessage({
          id: 'user-151-' + count,
          role: 'user',
          content: body.content,
          project_revision: 3 + count,
        })
        const assistant = sampleMessage({
          id: 'assistant-151-' + count,
          role: 'assistant',
          content: '收到第 ' + count + ' 条',
          project_revision: 3 + count,
        })
        return {
          body: {
            project: project(3 + count),
            user_message: user,
            assistant_message: assistant,
            workflow: workflow({ project_revision: 4 + count }),
          },
        }
      })
    })
    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()
    const input = composer()

    await harness!.user.type(input, '第一条')
    await harness!.user.keyboard('{Enter}')
    await harness!.waitFor(() => expect(harness!.fetch.count('POST', MESSAGES_A)).toBe(1))
    expect(input).toHaveValue('')

    await harness!.user.type(input, '第二行')
    await harness!.user.keyboard('{Shift>}{Enter}{/Shift}')
    expect(harness!.fetch.count('POST', MESSAGES_A)).toBe(1)
    expect(input).toHaveValue('第二行\n')

    await harness!.user.keyboard('{Control>}{Enter}{/Control}')
    await harness!.waitFor(() => expect(harness!.fetch.count('POST', MESSAGES_A)).toBe(2))
    expect(input).toHaveValue('')

    await harness!.user.type(input, '组合输入')
    fireEvent.compositionStart(input)
    await harness!.user.keyboard('{Enter}')
    expect(harness!.fetch.count('POST', MESSAGES_A)).toBe(2)
    fireEvent.compositionEnd(input)

    await harness!.user.keyboard('{Meta>}{Enter}{/Meta}')
    await harness!.waitFor(() => expect(harness!.fetch.count('POST', MESSAGES_A)).toBe(3))
  })

  it('附件上传态纯文字，完成后显示 34×42 预览、单行文件名和 24px 移除按钮；失败为 role=alert', async () => {
    let stageCount = 0
    await mountChat((fetch) => {
      fetch.route('POST', STAGE_A, () => {
        stageCount += 1
        if (stageCount === 1) return { delayMs: 400, body: staged() }
        return {
          status: 422,
          body: { error: { code: 'INVALID_IMAGE', message: '图片内容无法识别，请重新选择。' } },
        }
      })
      fetch.route('DELETE', new RegExp('^' + STAGE_A + '/[^/]+$'), (request) => ({
        body: { asset_id: request.path.split('/').at(-1), discarded: true },
      }))
    })
    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()

    await upload('预览图.png')
    expect(await screen.findByText('正在上传…')).toBeVisible()
    expect(screen.queryByAltText('待发送的图片预览')).not.toBeInTheDocument()

    expect(await screen.findByText('预览图.png')).toBeVisible()
    expect(await screen.findByAltText('待发送的图片预览')).toBeVisible()
    const remove = screen.getByRole('button', { name: '移除图片' })
    expect(remove).toBeVisible()
    await harness!.user.click(remove)
    await harness!.waitFor(() => {
      expect(screen.queryByAltText('待发送的图片预览')).not.toBeInTheDocument()
    })

    await upload('损坏.png')
    expect(await screen.findByRole('alert')).toHaveTextContent('图片内容无法识别，请重新选择。')
    expect(screen.getByAltText('待发送的图片预览')).toBeVisible()
  })

  it('busy 时发送禁用并暴露「处理中…」，文本域可编辑，只有一行原因 caption 且无 toast', async () => {
    await mountChat((fetch) => {
      fetch.route('POST', MESSAGES_A, () => ({
        delayMs: 160,
        body: {
          project: project(4),
          user_message: sampleMessage({ id: 'user-busy', project_revision: 4, content: '生成主图' }),
          assistant_message: sampleMessage({ id: 'assistant-busy', project_revision: 4, content: '开始处理' }),
          workflow: workflow({ project_revision: 4 }),
        },
      }))
    })
    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()
    const input = composer()
    await harness!.user.type(input, '生成主图')

    const send = screen.getByRole('button', { name: '发送' })
    await harness!.user.click(send)
    await harness!.waitFor(() => expect(send).toBeDisabled())
    expect(send).toHaveAttribute('aria-busy', 'true')
    expect(send).toHaveAttribute('title', '处理中…')
    expect(send).toHaveAttribute('data-label', '处理中…')
    expect(screen.getByText('正在生成，完成后可发送')).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    expect(input).toBeEnabled()
    await harness!.user.type(input, '，补充一句')
    expect(input.value).toContain('补充一句')
    await harness!.waitFor(() => expect(send).toHaveAttribute('aria-busy', 'false'))
  })

  it('320/768 使用单层 264 抽屉，焦点进入/陷阱/回焦，Esc/遮罩/关闭可用，背景 inert 且滚动锁定；1024 常驻侧栏', async () => {
    for (const width of [320, 768]) {
      const mounted = await mountWorkspace({ config: emptyConfig(), viewportWidth: width, url: '/' })
      harness = mounted
      const menu = await screen.findByRole('button', { name: '打开侧栏' })
      expect(document.querySelectorAll('.workspace-topbar')).toHaveLength(1)

      await mounted.user.click(menu)
      const dialog = await screen.findByRole('dialog', { name: '工作区菜单' })
      expect(dialog.style.width).toContain('264px')
      expect(dialog.style.width).toContain('100vw')
      expect(dialog).toHaveAttribute('aria-modal', 'true')
      expect(menu).toHaveAttribute('aria-expanded', 'true')
      expect(document.body.style.overflow).toBe('hidden')
      expect(document.querySelector('.pure-shell')?.closest('[inert]')).not.toBeNull()

      const close = within(dialog).getByRole('button', { name: '关闭侧栏' })
      expect(close).toHaveFocus()

      const focusables = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      )
      const first = focusables[0]!
      const last = focusables[focusables.length - 1]!
      last.focus()
      await mounted.user.tab()
      expect(first).toHaveFocus()
      await mounted.user.tab({ shift: true })
      expect(last).toHaveFocus()

      if (width === 320) {
        await mounted.user.click(close)
        await mounted.waitFor(() => {
          expect(screen.queryByRole('dialog', { name: '工作区菜单' })).not.toBeInTheDocument()
        })
        expect(document.body.style.overflow).toBe('')
        expect(document.querySelector('.pure-shell')?.closest('[inert]')).toBeNull()
        expect(menu).toHaveFocus()

        await mounted.user.click(menu)
        const backdropDialog = await screen.findByRole('dialog', { name: '工作区菜单' })
        const backdrop = backdropDialog.parentElement?.querySelector('.drawer-backdrop') as HTMLElement
        await mounted.user.click(backdrop)
        await mounted.waitFor(() => {
          expect(screen.queryByRole('dialog', { name: '工作区菜单' })).not.toBeInTheDocument()
        })
        expect(menu).toHaveFocus()

        await mounted.user.click(menu)
        await screen.findByRole('dialog', { name: '工作区菜单' })
        await mounted.user.keyboard('{Escape}')
        await mounted.waitFor(() => {
          expect(screen.queryByRole('dialog', { name: '工作区菜单' })).not.toBeInTheDocument()
        })
        expect(menu).toHaveFocus()

        await mounted.user.click(menu)
        await screen.findByRole('dialog', { name: '工作区菜单' })
        mounted.dispose()
        harness = undefined
        expect(document.querySelector('[inert]')).toBeNull()
      } else {
        await mounted.user.click(close)
        await mounted.waitFor(() => {
          expect(screen.queryByRole('dialog', { name: '工作区菜单' })).not.toBeInTheDocument()
        })
        mounted.dispose()
        harness = undefined
      }
    }

    const wide = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1024, url: '/' })
    harness = wide
    expect(await screen.findByLabelText('对话侧栏')).toBeVisible()
    expect(screen.queryByRole('button', { name: '打开侧栏' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: '工作区菜单' })).not.toBeInTheDocument()
    expect(document.querySelectorAll('.workspace-topbar')).toHaveLength(1)
  })
})
