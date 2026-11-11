import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { MessageView, ProjectDetailResponse, ProjectView } from '@/api/types'
import type { WorkflowView } from '@/api/workflow'
import { mountWorkspace, type MountWorkspaceOptions, type WorkspaceHarness } from './support/workspace'
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
const SEND_TEXT = '请做一个春季托特包主图'

const chatSource = readFileSync(resolve(__dirname, '../main/src/pages/ChatPage.vue'), 'utf8')
const chatStyle = chatSource.slice(chatSource.indexOf('<style scoped>'))

function workflow(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: null,
    stage: null,
    status: 'none',
    project_revision: 3,
    requirement_revision: 0,
    intent_route: null,
    intent_action: null,
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: null,
    updated_at: '2026-11-09T05:00:00.000Z',
    ...overrides,
  }
}

function project(revision = 3): ProjectView {
  return sampleProject({ id: PROJECT_A, revision, prompt_revision: revision, confirmed_revision: revision })
}

function detail(messages: MessageView[] = []): ProjectDetailResponse {
  return { project: project(), messages, generations: [] }
}

function successBody(requestId: string, content: string, assistantText: string) {
  return {
    project: project(4),
    user_message: sampleMessage({
      id: 'server-user-1',
      role: 'user',
      content,
      project_revision: 4,
      request_id: requestId,
    }),
    assistant_message: sampleMessage({
      id: 'server-assistant-1',
      role: 'assistant',
      content: assistantText,
      project_revision: 4,
      request_id: requestId,
    }),
    workflow: workflow({ project_revision: 4 }),
  }
}

async function mountChat(setup?: MountWorkspaceOptions['setup']): Promise<WorkspaceHarness> {
  return mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/projects/' + PROJECT_A + '/chat',
    setup(fetch) {
      fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
      fetch.route('GET', DETAIL_PATH, () => ({ body: detail() }))
      fetch.route('GET', WORKFLOW_PATH, () => ({ body: workflow() }))
      setup?.(fetch)
    },
  })
}

function composer(): HTMLElement {
  return screen.getByRole('textbox', { name: '描述商品与图片需求' })
}

function pendingRow(): HTMLElement {
  const row = document.querySelector<HTMLElement>('.message-row.is-pending')
  if (!row) throw new Error('pending message row not rendered')
  return row
}

describe('T152 发送后立即上屏与等待反馈', () => {
  it('请求返回前已经显示用户消息和助手等待提示，返回后只保留一条真实消息', async () => {
    let resolveSend!: (value: { body: unknown }) => void
    let sentRequestId = ''
    harness = await mountChat((fetch) => {
      fetch.route('POST', MESSAGES_PATH, (request) => {
        const body = request.body as { content: string; request_id: string }
        sentRequestId = body.request_id
        return new Promise<{ body: unknown }>((resolve) => {
          resolveSend = resolve
        })
      })
    })

    const input = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(input, SEND_TEXT)
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText(SEND_TEXT)).toBeVisible()
    expect(within(pendingRow()).getByText('发送中…')).toBeVisible()
    const waiting = screen.getByText('正在理解你的需求…')
    expect(waiting).toBeVisible()
    expect(waiting.closest('[role="status"]')).toHaveAttribute('aria-live', 'polite')
    expect(input).toHaveFocus()
    expect(pendingRow().querySelector('.message-content')?.textContent).toBe(SEND_TEXT)
    expect(harness.fetch.count('POST', MESSAGES_PATH)).toBe(1)

    resolveSend({ body: successBody(sentRequestId, SEND_TEXT, '收到，正在为你整理需求。') })

    expect(await screen.findByText('收到，正在为你整理需求。')).toBeVisible()
    expect(screen.queryByText('正在理解你的需求…')).not.toBeInTheDocument()
    expect(screen.queryByText('发送中…')).not.toBeInTheDocument()
    expect(screen.getAllByText(SEND_TEXT)).toHaveLength(1)
    expect(document.querySelector('.message-row.is-pending')).toBeNull()
  })

  it('失败时保留用户消息并允许重试一次，成功后正常替换', async () => {
    let attempt = 0
    let resolveRetry!: (value: { body: unknown }) => void
    let retryRequestId = ''
    harness = await mountChat((fetch) => {
      fetch.route('POST', MESSAGES_PATH, (request) => {
        attempt += 1
        if (attempt === 1) return { networkError: 'offline' }
        const body = request.body as { content: string; request_id: string }
        retryRequestId = body.request_id
        return new Promise<{ body: unknown }>((resolve) => {
          resolveRetry = resolve
        })
      })
    })

    await harness.user.type(composer(), SEND_TEXT)
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('发送失败')).toBeVisible()
    expect(screen.getByText(SEND_TEXT)).toBeVisible()
    const retry = screen.getByRole('button', { name: '重试发送' })
    expect(retry).toBeEnabled()
    expect(document.querySelector('.message-row.is-pending')).not.toBeNull()

    await harness.user.click(retry)
    expect(await screen.findByText('发送中…')).toBeVisible()
    expect(screen.queryByRole('button', { name: '重试发送' })).not.toBeInTheDocument()
    expect(screen.getAllByText(SEND_TEXT)).toHaveLength(1)
    expect(harness.fetch.count('POST', MESSAGES_PATH)).toBe(2)

    resolveRetry({ body: successBody(retryRequestId, SEND_TEXT, '这次已经收到。') })

    expect(await screen.findByText('这次已经收到。')).toBeVisible()
    expect(screen.queryByText('发送失败')).not.toBeInTheDocument()
    expect(screen.queryByText('发送中…')).not.toBeInTheDocument()
    expect(screen.getAllByText(SEND_TEXT)).toHaveLength(1)
    expect(harness.fetch.count('POST', MESSAGES_PATH)).toBe(2)
  })

  it('发送失败后仍可编辑并发送新的补充内容', async () => {
    let attempt = 0
    let secondContent = ''
    harness = await mountChat((fetch) => {
      fetch.route('POST', MESSAGES_PATH, (request) => {
        attempt += 1
        const body = request.body as { content: string; request_id: string }
        if (attempt === 1) return { networkError: 'offline' }
        secondContent = body.content
        return { body: successBody(body.request_id, body.content, '已收到新的补充。') }
      })
    })

    const input = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(input, SEND_TEXT)
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('发送失败')).toBeVisible()
    expect(input).toBeEnabled()
    const send = screen.getByRole('button', { name: '发送' })
    expect(send).toBeEnabled()

    await harness.user.clear(input)
    await harness.user.type(input, '补充：米色托特包，用于商品主图')
    expect(send).toBeEnabled()
    await harness.user.click(send)

    expect(await screen.findByText('已收到新的补充。')).toBeVisible()
    expect(secondContent).toBe('补充：米色托特包，用于商品主图')
    expect(harness.fetch.count('POST', MESSAGES_PATH)).toBe(2)
  })

  it('新会话首次发送也立即显示用户消息和等待提示', async () => {
    let resolveProject!: (value: { body: unknown }) => void
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('POST', '/api/projects', () =>
          new Promise<{ body: unknown }>((resolve) => {
            resolveProject = resolve
          }),
        )
        fetch.route('GET', DETAIL_PATH, () => ({
          body: {
            project: project(1),
            messages: [
              sampleMessage({
                id: 'first-user',
                role: 'user',
                content: SEND_TEXT,
                project_revision: 1,
                request_id: null,
              }),
            ],
            generations: [],
          },
        }))
        fetch.route('GET', WORKFLOW_PATH, () => ({ body: workflow() }))
        fetch.route('POST', MESSAGES_PATH, (request) => {
          const body = request.body as { content: string; request_id: string }
          return { body: successBody(body.request_id, body.content, '开始整理需求。') }
        })
      },
    })

    const input = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(input, SEND_TEXT)
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText(SEND_TEXT)).toBeVisible()
    expect(screen.getByText('发送中…')).toBeVisible()
    expect(screen.getByText('正在理解你的需求…')).toBeVisible()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)

    resolveProject({ body: project(0) })

    await harness.waitFor(() => {
      expect(document.querySelector('.message-row.is-pending')).toBeNull()
      expect(screen.queryByText('正在理解你的需求…')).not.toBeInTheDocument()
    })
    expect(screen.getAllByText(SEND_TEXT)).toHaveLength(1)
    expect(harness.fetch.count('POST', MESSAGES_PATH)).toBe(1)
    expect(harness.fetch.count('POST', ACTIONS_PATH + 'submit_request')).toBe(0)
  })

  it('减少动态效果时等待提示保留静态文字并停用脉冲动画', () => {
    const reduced = chatStyle.match(/@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\n\}/)?.[0] ?? ''
    expect(reduced).toContain('.pending-assistant-dots')
    expect(reduced).toContain('display: none')
    expect(reduced).toContain('animation: none')
    expect(chatStyle).toContain('.pending-assistant-text')
    expect(chatSource).toContain('正在理解你的需求…')
  })
})
