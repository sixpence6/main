import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/vue'
import type { MessageView, ProjectDetailResponse, ProjectView } from '@/api/types'
import type { WorkflowVersion, WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, emptyConfig, sampleMessage, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const DETAIL_A = '/api/projects/' + PROJECT_A
const WORKFLOW_A = DETAIL_A + '/workflow'
const RUN_CURRENT = 'run-current'
const RUN_OLD = 'run-old'
const RUN_LATEST = 'run-latest'

const chatSource = readFileSync(resolve(__dirname, '../main/src/pages/ChatPage.vue'), 'utf8')
const chatStyle = chatSource.slice(chatSource.indexOf('<style scoped>'))
const lightboxSource = readFileSync(
  resolve(__dirname, '../main/src/components/ImageLightbox.vue'),
  'utf8',
)
const lightboxStyle = lightboxSource.slice(lightboxSource.indexOf('<style scoped>'))
const tokenSource = readFileSync(resolve(__dirname, '../main/src/styles/tokens.css'), 'utf8')

function cssRule(source: string, selector: string): string {
  const start = source.indexOf(selector + ' {')
  if (start < 0) return ''
  const open = source.indexOf('{', start)
  const close = source.indexOf('}', open)
  return source.slice(open + 1, close)
}

function cssRuleContaining(source: string, selector: string): string {
  const start = source.indexOf(selector)
  if (start < 0) return ''
  const open = source.indexOf('{', start)
  const close = source.indexOf('}', open)
  return source.slice(open + 1, close)
}

function project(): ProjectView {
  return sampleProject({ id: PROJECT_A, title: '项目甲', revision: 3 })
}

function detail(messages: MessageView[] = []): ProjectDetailResponse {
  return { project: project(), messages, generations: [] }
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

function completedView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: RUN_CURRENT,
    stage: 'S11',
    status: 'completed',
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
    delivery: {
      id: 'delivery-current',
      status: 'published',
      asset_id: 'asset-current',
      filename: 'current.png',
      caption: '当前版本主图',
      published_at: '2026-10-21T06:02:00.000Z',
      created_at: '2026-10-21T06:02:00.000Z',
    },
    versions: [version({})],
    updated_at: '2026-10-21T06:02:00.000Z',
    ...overrides,
  }
}

function multiVersionView(): WorkflowView {
  return completedView({
    versions: [
      version({
        run_id: RUN_OLD,
        sequence_no: 1,
        requirement_revision: 1,
        asset_id: 'asset-old',
        filename: 'old.png',
        width: 1024,
        height: 1024,
        caption: '历史版本主图',
        published_at: '2026-10-19T03:00:00.000Z',
      }),
      version({}),
      version({
        run_id: RUN_LATEST,
        sequence_no: 3,
        requirement_revision: 4,
        asset_id: 'asset-latest',
        filename: 'latest.png',
        caption: '最新发布主图',
        published_at: '2026-10-22T03:00:00.000Z',
      }),
    ],
  })
}

async function mountChat(view: WorkflowView = completedView(), messages: MessageView[] = []) {
  harness = await mountWorkspace({
    config: emptyConfig(),
    viewportWidth: 1440,
    url: '/projects/' + PROJECT_A + '/chat',
    setup(fetch) {
      fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
      fetch.route('GET', DETAIL_A, () => ({ body: detail(messages) }))
      fetch.route('GET', WORKFLOW_A, () => ({ body: view }))
    },
  })
  return harness
}

async function historyRegion(): Promise<HTMLElement> {
  return screen.findByRole('region', { name: '图片历史' })
}

function resultCard(sequenceNo: number): HTMLElement {
  const region = screen.getByRole('region', { name: '图片历史' })
  return within(region).getByRole('article', { name: '版本 ' + sequenceNo + ' 结果' })
}

function attachmentMessage(): MessageView {
  return sampleMessage({
    id: 'message-attachment-1',
    sequence_no: 1,
    role: 'user',
    content: '请参考这张图做一张秋季主图。',
    attachment: {
      id: 'attachment-1',
      message_id: 'message-attachment-1',
      asset_id: 'asset-reference-1',
      usage: 'product',
      content_version: 'sha256-1',
      sha256: 'sha256-1',
      mime_type: 'image/png',
      width: 1024,
      height: 1024,
      byte_size: 2048,
      preview_url: '/api/assets/asset-reference-1',
      created_at: '2026-11-01T00:00:00.000Z',
    },
  })
}

describe('T150 V5 结果卡与居中灯箱', () => {
  it('单图只占一个网格项，多图在 ≥768 两列 gap 16、<768 单列', async () => {
    await mountChat()
    const region = await historyRegion()
    const grid = region.querySelector('.result-grid')
    expect(grid).not.toBeNull()
    expect(grid!.querySelectorAll(':scope > .result-card')).toHaveLength(1)

    const gridRule = cssRule(chatStyle, '.result-grid')
    expect(gridRule).toContain('display: grid')
    expect(gridRule).toContain('grid-template-columns: minmax(0, 1fr)')
    expect(gridRule).toContain('gap: 16px')
    expect(chatStyle).toMatch(
      /@media \(min-width: 768px\) \{[\s\S]*?\.result-grid \{[\s\S]*?grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/,
    )

    harness!.dispose()
    harness = undefined
    await mountChat(multiVersionView())
    const multiRegion = await historyRegion()
    const multiGrid = multiRegion.querySelector('.result-grid')
    expect(multiGrid).not.toBeNull()
    expect(multiGrid!.querySelectorAll(':scope > .result-card')).toHaveLength(3)
  })

  it('结果卡 12px 内边距、md 圆角、3:4 媒体与 12px 版本徽标', async () => {
    await mountChat()
    await historyRegion()

    const cardRule = cssRule(chatStyle, '.result-card')
    expect(cardRule).toContain('padding: 12px')
    expect(cardRule).toContain('border-radius: var(--radius-md)')
    expect(cardRule).toContain('border: 1px solid var(--line-hairline)')
    expect(cardRule).toContain('background: var(--surface-panel)')

    const mediaRule = cssRule(chatStyle, '.result-media')
    expect(mediaRule).toContain('aspect-ratio: 3 / 4')
    expect(mediaRule).toContain('background: var(--surface-media)')
    expect(mediaRule).toContain('border-radius: var(--radius-sm)')

    const badgeRule = cssRule(chatStyle, '.result-badge')
    expect(badgeRule).toContain('font-size: var(--text-caption)')
    expect(badgeRule).toContain('border-radius: var(--radius-pill)')
    expect(badgeRule).toContain('background: var(--surface-panel)')
    expect(badgeRule).toContain('border: 1px solid var(--line-hairline)')

    const headingRule = cssRule(chatStyle, '.result-heading')
    expect(headingRule).toContain('font-size: var(--text-caption)')

    expect(cssRule(chatStyle, '.result-caption')).toContain('font-size: var(--text-caption)')
    expect(cssRule(chatStyle, '.result-caption')).toContain('color: var(--text-muted)')

    const card = resultCard(2)
    expect(card.querySelector('.result-media')).not.toBeNull()
    expect(within(card).getByText('当前版本主图')).toBeVisible()
    expect(within(card).getByText('1536×1024')).toBeVisible()
  })

  it('hover 只改变边框，不加阴影、不位移、不缩放；焦点环在卡片外沿 offset 2', async () => {
    await mountChat()
    await historyRegion()

    const hoverRule = cssRule(chatStyle, '.result-card:hover')
    expect(hoverRule).toContain('border-color: var(--line-hover)')
    expect(hoverRule).not.toMatch(/box-shadow|transform|scale/)

    const focusRule = cssRule(chatStyle, '.result-card:focus-within')
    expect(focusRule).toContain('outline: 2px solid var(--focus-ring-light)')
    expect(focusRule).toContain('outline-offset: 2px')
  })

  it('当前版、最新已发布与历史版徽标同时可读', async () => {
    await mountChat(multiVersionView())
    await historyRegion()

    const current = resultCard(2)
    const latest = resultCard(3)
    const old = resultCard(1)
    expect(within(current).getByText('当前版本')).toBeVisible()
    expect(within(latest).getByText('最新已发布')).toBeVisible()
    expect(within(old).getByText('历史版本')).toBeVisible()
    for (const card of [current, latest, old]) {
      const badge = card.querySelector('.result-badge')
      expect(badge).not.toBeNull()
      expect(badge!.textContent!.trim()).not.toBe('')
    }
  })

  it('不可用图使用 inset 底、图标与单行说明，不回退到其他图片', async () => {
    await mountChat(
      completedView({
        versions: [
          version({
            asset_id: null,
            filename: null,
            can_download: false,
            caption: '不可用主图',
          }),
        ],
      }),
    )
    await historyRegion()
    const card = resultCard(2)
    expect(within(card).getByText('该版本图片不可用')).toBeVisible()
    const unavailable = card.querySelector('.result-unavailable')
    expect(unavailable).not.toBeNull()
    expect(unavailable!.querySelector('svg')).not.toBeNull()
    expect(unavailable!.querySelectorAll('p')).toHaveLength(1)
    expect(card.querySelector('img')).toBeNull()
    expect(within(card).queryByRole('link', { name: /^查看大图/ })).not.toBeInTheDocument()

    const rule = cssRule(chatStyle, '.result-unavailable')
    expect(rule).toContain('background: var(--surface-inset)')
    expect(rule).toContain('border-radius: var(--radius-sm)')
    expect(rule).toContain('color: var(--text-secondary)')
    expect(rule).not.toMatch(/linear-gradient|opacity/)
  })

  it('不可下载时渲染无链接的禁用下载控件', async () => {
    await mountChat(
      completedView({
        versions: [version({ can_download: false })],
      }),
    )
    await historyRegion()
    const card = resultCard(2)
    expect(within(card).queryByRole('link', { name: '下载原图' })).not.toBeInTheDocument()
    const disabled = within(card).getByRole('button', { name: '下载原图' })
    expect(disabled).toBeDisabled()
    expect(disabled).toHaveAttribute('aria-disabled', 'true')

    const disabledRule = cssRule(chatStyle, '.result-download:disabled')
    expect(disabledRule).toContain('color: var(--text-muted)')
    expect(disabledRule).toContain('opacity: 1')
  })

  it('灯箱居中无文字、role=dialog、aria-modal，打开后焦点在关闭按钮', async () => {
    await mountChat()
    const region = await historyRegion()
    const card = within(region).getByRole('article', { name: '版本 2 结果' })
    const link = within(card).getByRole('link', { name: '查看大图：当前版本主图' })

    await harness!.user.click(link)
    const dialog = await screen.findByRole('dialog', { name: '图片大图' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveClass('lightbox-panel')
    expect(dialog.closest('.lightbox-layer')).not.toBeNull()
    expect(dialog.closest('.lightbox-layer')!.querySelector('.lightbox-scrim')).not.toBeNull()
    expect(dialog.textContent!.trim()).toBe('')
    expect(within(dialog).getByRole('img', { name: '当前版本主图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-current',
    )
    const close = within(dialog).getByRole('button', { name: '关闭大图' })
    expect(close).toHaveFocus()

    const panelRule = cssRule(lightboxStyle, '.lightbox-panel')
    expect(panelRule).toContain('padding: 12px')
    expect(panelRule).toContain('background: var(--surface-panel)')
    expect(panelRule).toContain('box-shadow: var(--shadow-overlay)')
    expect(panelRule).toContain('border-radius: var(--radius-lg)')
    const imageRule = cssRule(lightboxStyle, '.lightbox-image')
    expect(imageRule).toContain('max-width: min(92vw, 1120px)')
    expect(imageRule).toContain('max-height: 86vh')
    const scrimRule = cssRule(lightboxStyle, '.lightbox-scrim')
    expect(scrimRule).toContain('background: var(--scrim)')
    expect(tokenSource).toContain('--scrim: rgba(12, 14, 17, 0.4)')
    const closeRule = cssRule(lightboxStyle, '.lightbox-close')
    expect(closeRule).toContain('width: 44px')
    expect(closeRule).toContain('height: 44px')
    expect(closeRule).toContain('top: 8px')
    expect(closeRule).toContain('right: 8px')
    expect(closeRule).toContain('border-radius: var(--radius-sm)')
    expect(cssRule(lightboxStyle, '.lightbox-close svg')).toContain('width: 20px')
    expect(lightboxStyle).toMatch(/@media \(max-width: 767px\) \{[\s\S]*?\.lightbox-layer \{[\s\S]*?padding: 16px/)
    expect(lightboxStyle).toMatch(/@media \(max-width: 767px\) \{[\s\S]*?\.lightbox-panel \{[\s\S]*?padding: 8px/)

    await harness!.user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
  })

  it('Esc、遮罩、关闭按钮三条路径关闭，焦点回到原卡片；Enter/Space 可打开', async () => {
    await mountChat()
    const region = await historyRegion()
    const card = within(region).getByRole('article', { name: '版本 2 结果' })
    const link = within(card).getByRole('link', { name: '查看大图：当前版本主图' })

    link.focus()
    await harness!.user.keyboard('{Enter}')
    expect(await screen.findByRole('dialog', { name: '图片大图' })).toBeVisible()
    await harness!.user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
    expect(link).toHaveFocus()

    link.focus()
    await harness!.user.keyboard(' ')
    expect(await screen.findByRole('dialog', { name: '图片大图' })).toBeVisible()
    await harness!.user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
    expect(link).toHaveFocus()

    await harness!.user.click(link)
    await screen.findByRole('dialog', { name: '图片大图' })
    await harness!.user.click(document.querySelector('.lightbox-scrim') as HTMLElement)
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
    expect(link).toHaveFocus()

    await harness!.user.click(link)
    const reopened = await screen.findByRole('dialog', { name: '图片大图' })
    await harness!.user.click(within(reopened).getByRole('button', { name: '关闭大图' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
    expect(link).toHaveFocus()
  })

  it('整卡可点开灯箱，但不劫持卡内链接、按钮和提示词展开', async () => {
    await mountChat()
    await historyRegion()
    const card = resultCard(2)
    const summary = within(card).getByText('该版本实际使用的已批准提示词')

    await harness!.user.click(summary)
    expect(within(card).getByText('当前版本的已批准提示词')).toBeVisible()
    expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()

    const download = within(card).getByRole('link', { name: '下载原图' })
    expect(download).toHaveAttribute('href', '/api/assets/asset-current?download=true')

    await harness!.user.click(within(card).getByText('当前版本主图'))
    expect(await screen.findByRole('dialog', { name: '图片大图' })).toBeVisible()
    await harness!.user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
    expect(card).toHaveFocus()
  })

  it('背景 inert + 滚动锁，关闭后恢复', async () => {
    await mountChat()
    const region = await historyRegion()
    const link = within(resultCard(2)).getByRole('link', { name: '查看大图：当前版本主图' })

    await harness!.user.click(link)
    await screen.findByRole('dialog', { name: '图片大图' })
    expect(document.body.style.overflow).toBe('hidden')
    expect(harness!.container).toHaveAttribute('inert')

    await harness!.user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
    expect(document.body.style.overflow).toBe('')
    expect(harness!.container).not.toHaveAttribute('inert')
    expect(region).toBeInTheDocument()
  })

  it('Tab 只在关闭按钮内循环', async () => {
    await mountChat()
    const region = await historyRegion()
    const link = within(resultCard(2)).getByRole('link', { name: '查看大图：当前版本主图' })
    await harness!.user.click(link)
    const dialog = await screen.findByRole('dialog', { name: '图片大图' })
    const close = within(dialog).getByRole('button', { name: '关闭大图' })
    expect(close).toHaveFocus()

    await harness!.user.tab()
    expect(close).toHaveFocus()
    await harness!.user.tab({ shift: true })
    expect(close).toHaveFocus()

    await harness!.user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
  })

  it('附件缩略图复用同一灯箱并保留 34×42 尺寸与回焦', async () => {
    await mountChat(
      completedView({ delivery: null, versions: [], stage: 'S03', status: 'active' }),
      [attachmentMessage()],
    )
    const thumb = await screen.findByRole('button', { name: '查看参考图' })
    const image = thumb.querySelector('img')
    expect(image).not.toBeNull()
    expect(image).toHaveAttribute('width', '34')
    expect(image).toHaveAttribute('height', '42')

    await harness!.user.click(thumb)
    const dialog = await screen.findByRole('dialog', { name: '图片大图' })
    expect(within(dialog).getByRole('img', { name: '参考图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-reference-1',
    )
    expect(within(dialog).getByRole('button', { name: '关闭大图' })).toHaveFocus()

    await harness!.user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: '图片大图' })).not.toBeInTheDocument()
    })
    expect(thumb).toHaveFocus()
  })

  it('灯箱 180ms 透明度过渡，reduced-motion 下即时出现', async () => {
    expect(lightboxStyle).toMatch(
      /\.lightbox-fade-enter-active,[\s\S]*?\.lightbox-fade-leave-active \{[\s\S]*?transition: opacity var\(--dur-base\)/,
    )
    expect(lightboxStyle).toMatch(
      /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?transition: none/,
    )
  })
})
