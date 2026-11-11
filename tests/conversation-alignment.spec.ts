import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/vue'
import type { WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, configuredConfig, sampleMessage, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined
afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const path = '/api/projects/' + PROJECT_A
const question = {
  question_id: 'question-current', field: 'purpose', text: '这张图主要用在哪里？',
  requirement_revision: 3, message_id: 'question-message',
  options: [{ id: 'A', label: '商品展示' }, { id: 'B', label: '商务送礼' }, { id: 'C', label: '你帮我选' }],
}
function workflow(pending = true): WorkflowView {
  return {
    interaction_mode: 'direct', conversation_question: pending ? question : null,
    project_id: PROJECT_A, run_id: 'run-1', stage: 'S03', status: 'needs_input',
    project_revision: pending ? 3 : 4, requirement_revision: 1,
    intent_route: 'image_request', intent_action: 'new_request', issue: null,
    artifacts: [], approvals: [], available_actions: [], legacy: [], generation_job: null,
    delivery: null, updated_at: '2026-11-10T05:00:00Z',
  }
}
const assistant = sampleMessage({ id: 'question-message', role: 'assistant', content: question.text })
const historical = sampleMessage({ id: 'old-question', role: 'assistant', content: '旧问题：请选择背景' })

async function mountQuestion(pending = true, onSend?: (body: Record<string, unknown>) => Promise<{ body: unknown }> | { body: unknown }) {
  return mountWorkspace({
    config: configuredConfig(), viewportWidth: 390, url: '/projects/' + PROJECT_A + '/chat',
    setup(fetch) {
      fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
      fetch.route('GET', path, () => ({ body: { project: sampleProject({ id: PROJECT_A, revision: 3 }), messages: [historical, assistant], generations: [] } }))
      fetch.route('GET', path + '/workflow', () => ({ body: workflow(pending) }))
      if (onSend) fetch.route('POST', path + '/messages', (request) => onSend(request.body as Record<string, unknown>))
    },
  })
}

describe('对话选项', () => {
  it('刷新恢复当前问题，只有当前问题有可操作选项', async () => {
    harness = await mountQuestion()
    expect(await screen.findByRole('button', { name: 'A. 商品展示' })).toBeEnabled()
    expect(screen.getByRole('group', { name: question.text })).toBeVisible()
    expect(screen.getAllByRole('group', { name: question.text })).toHaveLength(1)
    expect(screen.getByText('旧问题：请选择背景')).toBeVisible()
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toBeVisible()
    expect(harness.fetch.count('POST', path + '/messages')).toBe(0)
    harness.dispose()
    harness = await mountQuestion()
    expect(await screen.findByRole('button', { name: 'B. 商务送礼' })).toBeEnabled()
    expect(harness.fetch.count('POST', path + '/messages')).toBe(0)
  })

  it('点击携带问题版本与消息绑定，等待时禁止重复点击并保留自由输入草稿', async () => {
    let captured: Record<string, unknown> | undefined
    let finish!: (value: { body: unknown }) => void
    harness = await mountQuestion(true, (body) => {
      captured = body
      return new Promise((resolve) => { finish = resolve })
    })
    const button = await screen.findByRole('button', { name: 'A. 商品展示' })
    await harness.user.type(screen.getByRole('textbox', { name: '描述商品与图片需求' }), '但别太暗')
    await harness.user.click(button)
    await waitFor(() => expect(captured).toBeDefined())
    expect(captured).toMatchObject({ content: 'A', expected_revision: 3, clarification_answer: {
      question_id: question.question_id, option_id: 'A', requirement_revision: 3, message_id: question.message_id,
    } })
    expect(button).toBeDisabled()
    expect(screen.getByRole('button', { name: 'B. 商务送礼' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue('但别太暗')
    await harness.user.click(button)
    expect(harness.fetch.count('POST', path + '/messages')).toBe(1)
    finish({ body: {
      project: sampleProject({ id: PROJECT_A, revision: 4 }),
      user_message: sampleMessage({ id: 'selected-message', role: 'user', content: 'A', project_revision: 4 }),
      assistant_message: sampleMessage({ id: 'answer-message', role: 'assistant', content: '已记录商品展示方向', project_revision: 4 }),
      workflow: workflow(false),
    } })
    expect(await screen.findByText('已记录商品展示方向')).toBeVisible()
  })

  it('历史问题保留文字但不显示失效按钮', async () => {
    harness = await mountQuestion(false)
    expect(await screen.findByText(question.text)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'A. 商品展示' })).not.toBeInTheDocument()
  })
})
