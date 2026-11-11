import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/vue'
import type { ProjectDetailResponse, ProjectView } from '@/api/types'
import type { PromptPreviewView, WorkflowAction, WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, PROJECT_B, configuredConfig, sampleMessage, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const ACTIONS_ROUTE = /^\/api\/projects\/[^/]+\/workflow\/actions\/[^/]+$/
const MESSAGE_A = 'A 的新商品主图'
const MESSAGE_B = 'B 的新品场景图'
const promptText = (projectId: string) => `${projectId} 的专属提示词：制作简洁明亮的电商主图`

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

function action(id: string): WorkflowAction {
  return {
    id,
    label: id,
    kind: 'button',
    requires_text: false,
    text_label: null,
    requires_confirmation: false,
  }
}

function workflow(projectId: string, active = false): WorkflowView {
  return {
    project_id: projectId,
    run_id: active ? 'run-' + projectId : null,
    stage: active ? 'S03' : null,
    status: active ? 'active' : 'none',
    project_revision: active ? 1 : 0,
    requirement_revision: active ? 1 : 0,
    intent_route: active ? 'image_request' : null,
    intent_action: active ? 'new_request' : null,
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: active ? [action('compose_brief')] : [],
    legacy: [],
    generation_job: null,
    delivery: null,
    updated_at: '2026-11-12T00:00:00Z',
  }
}

function promptWorkflow(projectId: string): WorkflowView {
  const positive = promptText(projectId)
  const preview: PromptPreviewView = {
    id: 'preview-' + projectId,
    status: 'awaiting_confirmation',
    request_digest: 'digest-' + projectId,
    request: {
      positive_prompt: positive,
      negative_prompt: '避免多余文字',
      prompt: positive,
      output: { count: 1, size: '1024x1024' },
      references: [],
    },
    created_at: '2026-11-12T00:00:00Z',
    can_confirm: true,
    command_context: { expected_revision: 5, nonce: 'context-' + projectId },
  }
  return {
    ...workflow(projectId),
    run_id: 'run-' + projectId,
    stage: 'S07',
    status: 'needs_input',
    project_revision: 5,
    requirement_revision: 1,
    available_actions: [action('confirm_prompt')],
    prompt_preview: preview,
  }
}

function project(projectId: string, revision = 0): ProjectView {
  const title = projectId === PROJECT_A ? '会话 A' : '会话 B'
  return sampleProject({ id: projectId, title, revision, prompt_revision: revision, confirmed_revision: revision })
}

function detail(projectId: string, revision = 0): ProjectDetailResponse {
  return { project: project(projectId, revision), messages: [], generations: [] }
}

function messageResponse(projectId: string, content: string) {
  return {
    project: project(projectId, 1),
    user_message: sampleMessage({ id: 'user-' + projectId, role: 'user', content }),
    assistant_message: sampleMessage({ id: 'assistant-' + projectId, role: 'assistant', content: '已收到。' }),
    workflow: null,
  }
}

function completedWorkflow(projectId: string): WorkflowView {
  return {
    ...workflow(projectId, true),
    stage: 'S04',
    status: 'needs_input',
    project_revision: 2,
    available_actions: [],
  }
}

async function switchToProject(projectId: string): Promise<void> {
  await harness!.router.push('/projects/' + projectId + '/chat')
  const discard = screen.queryByRole('button', { name: '放弃修改' })
  if (discard) await harness!.user.click(discard)
  await screen.findByRole('heading', { name: projectId === PROJECT_A ? '会话 A' : '会话 B' })
}

describe('ChatPage conversation request scope', () => {
  it('isolates pending send and busy state when an earlier conversation responds late', async () => {
    const actionsA = deferred<{ body: WorkflowView }>()
    const actionsB = deferred<{ body: WorkflowView }>()
    const actionRequests: Array<{ projectId: string }> = []
    const messages = new Map<string, ReturnType<typeof sampleMessage>[]>()

    harness = await mountWorkspace({
      config: configuredConfig(),
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', /^\/api\/projects\/[^/]+$/, (request) => {
          const projectId = request.path.split('/')[3]!
          return { body: { ...detail(projectId), messages: messages.get(projectId) ?? [] } }
        })
        fetch.route('GET', /^\/api\/projects\/[^/]+\/workflow$/, (request) => {
          const projectId = request.path.split('/')[3]!
          return { body: workflow(projectId) }
        })
        fetch.route('POST', /^\/api\/projects\/[^/]+\/messages$/, (request) => {
          const projectId = request.path.split('/')[3]!
          const body = request.body as { content: string; request_id: string }
          const nextWorkflow = workflow(projectId, true)
          const nextProject = project(projectId, 1)
          const userMessage = sampleMessage({
            id: 'user-' + projectId,
            role: 'user',
            content: body.content,
            request_id: body.request_id,
          })
          const assistantMessage = sampleMessage({
            id: 'assistant-' + projectId,
            role: 'assistant',
            content: '开始整理需求。',
            request_id: body.request_id,
          })
          messages.set(projectId, [userMessage, assistantMessage])
          return {
            body: {
              project: nextProject,
              user_message: userMessage,
              assistant_message: assistantMessage,
              workflow: nextWorkflow,
            },
          }
        })
        fetch.route('POST', ACTIONS_ROUTE, (request) => {
          const projectId = request.path.split('/')[3]!
          actionRequests.push({ projectId })
          return projectId === PROJECT_A ? actionsA.promise : actionsB.promise
        })
      },
    })

    const composer = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, MESSAGE_A)
    await harness.user.click(screen.getByRole('button', { name: '发送' }))
    await harness.waitFor(() => expect(actionRequests.map((request) => request.projectId)).toContain(PROJECT_A))
    expect(document.querySelector('.message-list')?.textContent).toContain(MESSAGE_A)
    expect(screen.getByText('处理中…')).toBeVisible()

    await switchToProject(PROJECT_B)
    const projectBComposer = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    await harness.waitFor(() => expect(screen.queryByText(MESSAGE_A)).not.toBeInTheDocument())
    expect(screen.queryByText('处理中…')).not.toBeInTheDocument()
    await harness.user.type(projectBComposer, MESSAGE_B)
    await harness.user.click(screen.getByRole('button', { name: '发送' }))
    await harness.waitFor(() => expect(actionRequests.map((request) => request.projectId)).toContain(PROJECT_B))
    expect(document.querySelector('.message-list')?.textContent).toContain(MESSAGE_B)
    expect(screen.getByText('处理中…')).toBeVisible()

    actionsA.resolve({ body: completedWorkflow(PROJECT_A) })
    await harness.waitFor(() => expect(screen.getByText('处理中…')).toBeVisible())
    expect(document.querySelector('.message-list')?.textContent).toContain(MESSAGE_B)
    expect(screen.queryByText(MESSAGE_A)).not.toBeInTheDocument()

    actionsB.resolve({ body: completedWorkflow(PROJECT_B) })
    await harness.waitFor(() => expect(screen.queryByText('处理中…')).not.toBeInTheDocument())
    expect(document.querySelector('.message-list')?.textContent).toContain(MESSAGE_B)
  })

  it('does not let A prompt confirmation release B busy state', async () => {
    const confirmA = deferred<{ body: WorkflowView }>()
    const confirmB = deferred<{ body: WorkflowView }>()
    const actionProjects: string[] = []

    harness = await mountWorkspace({
      config: configuredConfig(),
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', /^\/api\/projects\/[^/]+$/, (request) => ({
          body: detail(request.path.split('/')[3]!, 5),
        }))
        fetch.route('GET', /^\/api\/projects\/[^/]+\/workflow$/, (request) => ({
          body: promptWorkflow(request.path.split('/')[3]!),
        }))
        fetch.route('POST', ACTIONS_ROUTE, (request) => {
          const projectId = request.path.split('/')[3]!
          actionProjects.push(projectId)
          return projectId === PROJECT_A ? confirmA.promise : confirmB.promise
        })
      },
    })

    expect(await screen.findByRole('region', { name: '提示词确认' })).toBeVisible()
    await harness.user.click(screen.getByRole('button', { name: '确认使用以上提示词生成' }))
    await harness.waitFor(() => expect(actionProjects).toContain(PROJECT_A))
    expect(screen.getByText('处理中…')).toBeVisible()

    await switchToProject(PROJECT_B)
    expect(await screen.findByRole('region', { name: '提示词确认' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: '正向提示词' })).toHaveValue(
      `${PROJECT_B} 的专属提示词：制作简洁明亮的电商主图`,
    )
    expect(screen.queryByText('处理中…')).not.toBeInTheDocument()
    await harness.user.click(screen.getByRole('button', { name: '确认使用以上提示词生成' }))
    await harness.waitFor(() => expect(actionProjects).toContain(PROJECT_B))
    expect(screen.getByText('处理中…')).toBeVisible()

    confirmA.resolve({ body: completedWorkflow(PROJECT_A) })
    await harness.waitFor(() => expect(screen.getByText('处理中…')).toBeVisible())
    expect(screen.getByText(promptText(PROJECT_B), { exact: true })).toBeVisible()
    expect(screen.queryByText(promptText(PROJECT_A), { exact: true })).not.toBeInTheDocument()

    confirmB.resolve({ body: completedWorkflow(PROJECT_B) })
    await harness.waitFor(() => expect(screen.queryByText('处理中…')).not.toBeInTheDocument())
  })

  it('keeps B pending send when the send response from A arrives late', async () => {
    const sendA = deferred<{ body: unknown }>()
    const sendB = deferred<{ body: unknown }>()

    harness = await mountWorkspace({
      config: configuredConfig(),
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', /^\/api\/projects\/[^/]+$/, (request) => ({
          body: detail(request.path.split('/')[3]!),
        }))
        fetch.route('GET', /^\/api\/projects\/[^/]+\/workflow$/, (request) => ({
          body: workflow(request.path.split('/')[3]!),
        }))
        fetch.route('POST', /^\/api\/projects\/[^/]+\/messages$/, (request) =>
          request.path.split('/')[3] === PROJECT_A ? sendA.promise : sendB.promise,
        )
      },
    })

    const composer = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, MESSAGE_A)
    await harness.user.click(screen.getByRole('button', { name: '发送' }))
    await harness.waitFor(() => expect(harness!.fetch.count('POST', /^\/api\/projects\/[^/]+\/messages$/)).toBe(1))
    expect(screen.getByText(MESSAGE_A)).toBeVisible()

    await switchToProject(PROJECT_B)
    const projectBComposer = await screen.findByRole('textbox', { name: '描述商品与图片需求' })
    await harness.waitFor(() => expect(screen.queryByText(MESSAGE_A)).not.toBeInTheDocument())
    expect(screen.queryByText('处理中…')).not.toBeInTheDocument()
    await harness.user.type(projectBComposer, MESSAGE_B)
    await harness.user.click(screen.getByRole('button', { name: '发送' }))
    await harness.waitFor(() => expect(screen.getByText(MESSAGE_B)).toBeVisible())
    expect(screen.getByText('发送中…')).toBeVisible()
    expect(screen.getByText('处理中…')).toBeVisible()

    sendA.resolve({ body: messageResponse(PROJECT_A, MESSAGE_A) })
    await harness.waitFor(() => expect(screen.getByText('发送中…')).toBeVisible())
    expect(screen.getByText(MESSAGE_B)).toBeVisible()
    expect(screen.getByText('处理中…')).toBeVisible()

    sendB.resolve({ body: messageResponse(PROJECT_B, MESSAGE_B) })
    await harness.waitFor(() => expect(screen.queryByText('发送中…')).not.toBeInTheDocument())
  })
})
