import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { MessageView, ProjectDetailResponse, ProjectView } from '@/api/types'
import type { PromptPreviewView, WorkflowAction, WorkflowView } from '@/api/workflow'
import type { FetchController } from './support/fetch'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
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
const ACTIONS_PATH = WORKFLOW_PATH + '/actions/'
const ACTIONS_ROUTE = new RegExp('^' + ACTIONS_PATH + '[^/]+$')

const POSITIVE_PROMPT = '米色亚麻托特包，暖色棚拍，居中构图，文案 WARM MORNING'
const NEGATIVE_PROMPT = '不要凌乱背景，不要多余文字'

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

function detail(messages: MessageView[] = []): ProjectDetailResponse {
  return {
    project: project(),
    messages,
    generations: [],
  }
}

function promptPreview(overrides: Partial<PromptPreviewView> = {}): PromptPreviewView {
  return {
    id: 'preview-148',
    status: 'awaiting_confirmation',
    request_digest: 'digest-preview-148',
    request: {
      contract_version: 'prompt-request-1.1.0',
      positive_prompt: POSITIVE_PROMPT,
      negative_prompt: NEGATIVE_PROMPT,
      prompt: POSITIVE_PROMPT,
      output: { count: 1, size: '1024x1024', response_format: 'b64_json' },
      references: [{
        asset_id: 'asset-reference-1',
        usage: 'product',
        content_version: 'abcdef1234567890',
      }],
    },
    created_at: '2026-11-01T00:00:00.000Z',
    can_confirm: true,
    command_context: { expected_revision: 5, nonce: 'context-148' },
    ...overrides,
  }
}

function view(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: 'run-148',
    stage: 'S07',
    status: 'needs_input',
    project_revision: 5,
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

function promptView(preview: PromptPreviewView = promptPreview()): WorkflowView {
  return view({
    available_actions: [action('confirm_prompt', '确认使用以上提示词生成')],
    prompt_preview: preview,
  })
}

function completedView(): WorkflowView {
  return view({
    stage: 'S10',
    status: 'completed',
    available_actions: [],
    prompt_preview: null,
  })
}

function registerShell(fetch: FetchController): void {
  fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
}

async function mountPromptCard(options: {
  viewportWidth?: number
  preview?: PromptPreviewView
  fakeClock?: boolean
  confirmDelayMs?: number
  onConfirm?: (state: { view: WorkflowView }) => WorkflowView
} = {}): Promise<{ state: { view: WorkflowView } }> {
  const state = { view: promptView(options.preview) }
  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: options.viewportWidth ?? 1440,
    fakeClock: options.fakeClock,
    url: '/projects/' + PROJECT_A + '/chat',
    setup: (fetch) => {
      registerShell(fetch)
      fetch.route('GET', DETAIL_PATH, () => ({ body: detail() }))
      fetch.route('GET', WORKFLOW_PATH, () => ({ body: state.view }))
      fetch.route('POST', ACTIONS_ROUTE, () => {
        state.view = options.onConfirm?.(state) ?? completedView()
        return { body: state.view, delayMs: options.confirmDelayMs ?? 0 }
      })
    },
  })
  return { state }
}

async function promptCard(): Promise<HTMLElement> {
  const region = await screen.findByRole('region', { name: '提示词确认' })
  const card = region.querySelector<HTMLElement>('.prompt-card')
  expect(card).not.toBeNull()
  return card as HTMLElement
}

describe('V3 提示词确认卡', () => {
  it('待确认状态展示标题、版本、状态标签、正文、避免块、事实与单一主操作', async () => {
    await mountPromptCard()
    const card = await promptCard()
    const cardQueries = within(card)
    const region = screen.getByRole('region', { name: '提示词确认' })

    expect(card.parentElement).toBe(region)
    expect(region.querySelector('.message-bubble')).toBeNull()
    expect(cardQueries.getByText('实际提示词')).toBeVisible()
    expect(cardQueries.getByText('v1.1')).toBeVisible()
    expect(cardQueries.getByText('待确认')).toBeVisible()
    expect(card.querySelector('.prompt-card-pill')).toHaveClass('is-pending')
    expect(cardQueries.getByText(POSITIVE_PROMPT)).toBeVisible()
    expect(cardQueries.getByText('避免')).toBeVisible()
    expect(card.querySelector('.prompt-card-danger-icon')).not.toBeNull()
    expect(cardQueries.getByText(NEGATIVE_PROMPT)).toBeVisible()
    expect(cardQueries.getByText('输出')).toBeVisible()
    expect(cardQueries.getByText('1 张 · 1024x1024')).toBeVisible()
    expect(cardQueries.getByText('参考图')).toBeVisible()
    expect(cardQueries.getByText('产品图 · 版本 abcdef123456')).toBeVisible()

    const cta = cardQueries.getByRole('button', { name: '确认使用以上提示词生成' })
    expect(cta).toBeEnabled()
    expect(cardQueries.getByRole('button', { name: '复制提示词' })).toBeEnabled()
  })

  it('检查中时 CTA 禁用但复制保持可用，重复点击不提交请求', async () => {
    await mountPromptCard({ preview: promptPreview({ can_confirm: false }) })
    const card = await promptCard()
    const cardQueries = within(card)

    expect(cardQueries.getByText('检查中')).toBeVisible()
    expect(card.querySelector('.prompt-card-pill')).toHaveClass('is-checking')
    const cta = cardQueries.getByRole('button', { name: '提示词检查中…' })
    expect(cta).toBeDisabled()
    expect(cta).toHaveClass('prompt-cta')
    expect(cardQueries.getByRole('button', { name: '复制提示词' })).toBeEnabled()

    await harness!.user.click(cta)
    await harness!.user.click(cta)
    expect(harness!.fetch.count('POST', ACTIONS_PATH + 'confirm_prompt')).toBe(0)
  })

  it('复制写入卡片当前显示的正文，按钮显示已复制并在 2000ms 后复原', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    await mountPromptCard({ fakeClock: true })
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })

    const copy = await screen.findByRole('button', { name: '复制提示词' })
    const quote = document.querySelector('.prompt-card-quote')
    expect(quote).not.toBeNull()
    expect(quote?.textContent).toBe(POSITIVE_PROMPT)
    await harness!.user.click(copy)
    await harness!.clock.advance(0)

    expect(writeText).toHaveBeenCalledWith(quote?.textContent)
    expect(screen.getByRole('button', { name: '已复制' })).toBeVisible()
    await harness!.clock.advance(1999)
    expect(screen.getByRole('button', { name: '已复制' })).toBeVisible()
    await harness!.clock.advance(1)
    expect(screen.getByRole('button', { name: '复制提示词' })).toBeVisible()
  })

  it('确认后只提交一次，动作区收起并持续保留冻结正文与执行状态', async () => {
    await mountPromptCard({ fakeClock: true, confirmDelayMs: 500 })
    const card = await promptCard()
    const cta = within(card).getByRole('button', { name: '确认使用以上提示词生成' })

    await harness!.user.click(cta)
    await harness!.clock.advance(0)

    expect(harness!.fetch.count('POST', ACTIONS_PATH + 'confirm_prompt')).toBe(1)
    expect(card).toHaveClass('is-confirmed')
    expect(within(card).queryAllByRole('button')).toHaveLength(0)
    const execution = within(card).getByRole('status')
    expect(execution).toHaveTextContent('已确认，正在执行…')
    expect(execution.parentElement).toBe(card)
    expect(card.querySelector('.prompt-card-quote')?.textContent).toBe(POSITIVE_PROMPT)

    cta.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await harness!.clock.advance(0)
    expect(harness!.fetch.count('POST', ACTIONS_PATH + 'confirm_prompt')).toBe(1)

    // The frozen receipt must survive the preview being cleared by the server.
    await harness!.clock.advance(3500)
    expect(screen.getByRole('region', { name: '提示词确认' })).toBeVisible()
    expect(card.querySelector('.prompt-card-quote')?.textContent).toBe(POSITIVE_PROMPT)
    expect(within(card).getByRole('status')).toBeVisible()
    expect(within(card).queryAllByRole('button')).toHaveLength(0)
  })

  it('无负面提示词时不渲染避免块，版本号回退为 v1', async () => {
    const preview = promptPreview()
    const request = { ...preview.request }
    delete request.contract_version
    delete request.negative_prompt
    preview.request = request
    await mountPromptCard({ preview })

    const card = await promptCard()
    expect(within(card).getByText('v1')).toBeVisible()
    expect(within(card).queryByText('避免')).not.toBeInTheDocument()
  })

  it('超长正文保留全文、使用内部滚动容器且动作区位于其中', async () => {
    const longPrompt = '超长提示词：' + '暖色棚拍，居中构图，'.repeat(240)
    const preview = promptPreview()
    preview.request = { ...preview.request, positive_prompt: longPrompt }
    await mountPromptCard({ preview, viewportWidth: 320 })

    const card = await promptCard()
    const body = card.querySelector<HTMLElement>('.prompt-card-body')
    expect(body).not.toBeNull()
    expect(within(body as HTMLElement).getByText(longPrompt)).toBeVisible()
    expect(body!.querySelector('.prompt-card-actions')?.parentElement).toBe(body)
    expect(card).toHaveClass('is-narrow')
  })

  it('320 与 768 断点切换窄屏布局类，事实与动作区始终存在', async () => {
    await mountPromptCard({ viewportWidth: 320 })
    const card = await promptCard()

    expect(card).toHaveClass('is-narrow')
    expect(card.querySelector('.prompt-card-facts')).not.toBeNull()
    expect(card.querySelector('.prompt-card-actions')).not.toBeNull()

    await harness!.setViewportWidth(768)
    expect(card).not.toHaveClass('is-narrow')
    expect(card.querySelector('.prompt-card-facts')).not.toBeNull()
    expect(card.querySelector('.prompt-card-actions')).not.toBeNull()
  })
})
