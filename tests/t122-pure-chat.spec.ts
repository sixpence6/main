import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/vue'
import type { ProjectDetailResponse, ProjectView } from '@/api/types'
import type { PromptPreviewView, WorkflowAction, WorkflowView } from '@/api/workflow'
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
    run_id: 'run-122',
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

function project(revision = 0): ProjectView {
  return sampleProject({
    id: PROJECT_A,
    title: '新对话',
    revision,
    prompt_revision: revision,
    confirmed_revision: revision,
    brief_status: 'draft',
    brief: null as never,
  })
}

function detail(revision = 0, messages = [] as ProjectDetailResponse['messages']): ProjectDetailResponse {
  return {
    project: project(revision),
    messages,
    generations: [],
  }
}

interface FlowState {
  project: ProjectView
  view: WorkflowView
  detailCalls: number
}

function promptView(canConfirm = true): WorkflowView {
  return view({
    stage: 'S07',
    status: 'needs_input',
    project_revision: 5,
    requirement_revision: 3,
    artifacts: [FINAL_BRIEF],
    available_actions: [action('confirm_prompt', '确认使用以上提示词生成')],
    prompt_preview: { ...PROMPT_PREVIEW, can_confirm: canConfirm },
  })
}

function completedView(): WorkflowView {
  return view({
    stage: 'S10',
    status: 'completed',
    project_revision: 6,
    requirement_revision: 3,
    artifacts: [FINAL_BRIEF],
    available_actions: [],
    prompt_preview: null,
    delivery: {
      id: 'delivery-1',
      status: 'published',
      asset_id: 'asset-result-1',
      filename: 'bag.png',
      caption: '米色亚麻托特包主图',
      published_at: '2026-11-01T00:01:00.000Z',
      created_at: '2026-11-01T00:00:30.000Z',
    },
    versions: [{
      run_id: 'run-122',
      sequence_no: 1,
      requirement_revision: 3,
      status: 'published',
      generation_job_id: 'job-1',
      asset_id: 'asset-result-1',
      filename: 'bag.png',
      caption: '米色亚麻托特包主图',
      published_at: '2026-11-01T00:01:00.000Z',
      brief: { product_description: '米色亚麻托特包' },
      prompt: { positive_prompt: PROMPT_PREVIEW.request.positive_prompt },
      quality: { decision: 'pass' },
      can_download: true,
      created_at: '2026-11-01T00:00:30.000Z',
    }],
  })
}

async function mountFirstSendFlow(): Promise<{ state: FlowState; calls: string[] }> {
  const state: FlowState = {
    project: project(0),
    view: view({ stage: 'S03', status: 'active', available_actions: [action('compose_brief', '整理需求')] }),
    detailCalls: 0,
  }
  const calls: string[] = []
  const actionViews: Record<string, WorkflowView> = {
    submit_request: view({ stage: 'S03', status: 'active', project_revision: 1, requirement_revision: 1, available_actions: [action('compose_brief', '整理需求')] }),
    compose_brief: view({ stage: 'S04', status: 'active', project_revision: 2, requirement_revision: 2, available_actions: [action('plan_creative', '构思画面')] }),
    plan_creative: view({ stage: 'S05', status: 'active', project_revision: 3, requirement_revision: 2, available_actions: [action('plan_visual', '规划视觉')] }),
    plan_visual: view({ stage: 'S06', status: 'active', project_revision: 4, requirement_revision: 3, available_actions: [action('finalize_requirements', '整理需求卡')] }),
    finalize_requirements: view({ stage: 'S06', status: 'needs_input', project_revision: 5, requirement_revision: 3, artifacts: [FINAL_BRIEF], available_actions: [action('confirm_requirements', '确认需求')] }),
    confirm_requirements: promptView(),
    confirm_prompt: completedView(),
  }

  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/',
    setup: (fetch) => {
      fetch.json('POST', '/api/projects', state.project)
      fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
      fetch.route('GET', DETAIL_PATH, () => {
        state.detailCalls += 1
        return { body: detail(state.project.revision, [sampleMessage({ id: 'message-' + state.detailCalls, content: '请做一张商品主图' })]) }
      })
      fetch.route('GET', WORKFLOW_PATH, () => ({ body: state.view }))
      fetch.route('POST', MESSAGES_PATH, () => ({
        body: {
          project: state.project,
          user_message: sampleMessage({ id: 'message-user-2', content: '继续调整', turn_kind: 'conversation' }),
          assistant_message: sampleMessage({ id: 'message-assistant-2', role: 'assistant', content: '收到。', turn_kind: 'conversation' }),
          workflow: state.view,
        },
      }))
      fetch.route('POST', ACTIONS_ROUTE, (request) => {
        const id = request.path.slice(ACTIONS_PATH.length)
        calls.push(id)
        const next = actionViews[id]
        if (!next) throw new Error('unexpected action ' + id)
        state.view = next
        state.project = { ...state.project, revision: next.project_revision }
        return { body: next }
      })
    },
  })
  return { state, calls }
}

async function fillAndSend(text: string): Promise<void> {
  const composer = screen.getByRole('textbox', { name: '描述商品与图片需求' })
  await harness!.user.type(composer, text)
  await harness!.user.click(screen.getByRole('button', { name: '发送' }))
}

describe('T10 纯对话入口与首次文生图闭环', () => {
  it('新对话只显示欢迎语和一个输入框，不创建项目也不显示旧工作台', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/',
      setup: (fetch) => {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
      },
    })

    expect(screen.getByRole('heading', { name: '发来商品图片，或告诉我你想做什么样的电商图片' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toBeVisible()
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
    expect(screen.getByText('电商生图工作台')).toBeVisible()
    expect(screen.getByText('账号与用量')).toBeVisible()
    expect(screen.getByText('数据使用说明')).toBeVisible()
    expect(screen.queryByRole('region', { name: '需求工作台' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '提示词确认' })).not.toBeInTheDocument()
    expect(harness.fetch.count('POST')).toBe(0)
  })

  it('首次发送创建项目并逐级自动推进到需求确认，未确认前不请求图片', async () => {
    const { calls } = await mountFirstSendFlow()
    await fillAndSend('做一张米色亚麻托特包的电商主图，文案为 WARM MORNING')

    const requirement = await screen.findByRole('region', { name: '需求确认' })
    expect(requirement).toBeVisible()
    expect(screen.getByText('米色亚麻托特包')).toBeVisible()
    expect(screen.getByText('WARM MORNING')).toBeVisible()
    expect(screen.getByText('本次生成会占用 1 次额度；今日已受理 1 / 20 次。')).toBeVisible()
    expect(screen.getByRole('button', { name: '确认需求并生成提示词' })).toBeVisible()

    expect(harness!.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness!.fetch.last('POST', ACTIONS_PATH + 'submit_request')?.body).toEqual({
      expected_revision: 0,
      text: '做一张米色亚麻托特包的电商主图，文案为 WARM MORNING',
    })
    expect(calls).toEqual([
      'submit_request',
      'compose_brief',
      'plan_creative',
      'plan_visual',
      'finalize_requirements',
    ])
    expect(harness!.fetch.count('POST', ACTIONS_PATH + 'confirm_requirements')).toBe(0)
    expect(harness!.fetch.count('POST', ACTIONS_PATH + 'confirm_prompt')).toBe(0)
  })

  it('需求确认后展示实际提示词全文、输出与额度，可复制，点击后才提交最终确认并内联显示结果', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    const { calls } = await mountFirstSendFlow()
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    await fillAndSend('做一张米色亚麻托特包的电商主图，文案为 WARM MORNING')
    await screen.findByRole('region', { name: '需求确认' })

    await harness!.user.click(screen.getByRole('button', { name: '确认需求并生成提示词' }))
    const prompt = await screen.findByRole('region', { name: '提示词确认' })
    expect(prompt).toBeVisible()
    expect(screen.getByText('实际提示词')).toBeVisible()
    expect(screen.getByText(PROMPT_PREVIEW.request.positive_prompt as string)).toBeVisible()
    expect(screen.getByText('1 张 · 1024x1024')).toBeVisible()
    expect(screen.getByText('确认使用以上提示词生成')).toBeVisible()

    const confirmRequirements = harness!.fetch.last('POST', ACTIONS_PATH + 'confirm_requirements')
    expect(confirmRequirements?.body).toMatchObject({
      expected_revision: 5,
      payload: {
        artifact_id: FINAL_BRIEF.id,
        artifact_digest: FINAL_BRIEF.digest,
        requirement_revision: 3,
      },
    })
    expect((confirmRequirements?.body as { payload: { request_id: string } }).payload.request_id).toMatch(
      /^[0-9a-f-]{36}$/,
    )
    expect(harness!.fetch.count('POST', ACTIONS_PATH + 'confirm_prompt')).toBe(0)
    expect(screen.queryByRole('img', { name: '米色亚麻托特包主图' })).not.toBeInTheDocument()

    await harness!.user.click(screen.getByRole('button', { name: '复制提示词' }))
    expect(writeText).toHaveBeenCalledWith(PROMPT_PREVIEW.request.positive_prompt)
    expect(screen.getByRole('button', { name: '已复制' })).toBeVisible()

    await harness!.user.click(screen.getByRole('button', { name: '确认使用以上提示词生成' }))
    const result = await screen.findByRole('img', { name: '米色亚麻托特包主图' })
    expect(result).toHaveAttribute('src', '/api/assets/asset-result-1')
    expect(screen.getByText('图片已经生成，可以保存使用了。')).toBeVisible()
    expect(harness!.router.currentRoute.value.name).toBe('project-chat')
    expect(calls).toContain('confirm_prompt')

    const finalConfirm = harness!.fetch.last('POST', ACTIONS_PATH + 'confirm_prompt')
    expect(finalConfirm?.body).toMatchObject({
      expected_revision: 5,
      payload: {
        snapshot_id: PROMPT_PREVIEW.id,
        snapshot_digest: PROMPT_PREVIEW.request_digest,
        command_context: PROMPT_PREVIEW.command_context,
      },
    })
    expect((finalConfirm?.body as { payload: { command_id: string } }).payload.command_id).toMatch(
      /^[0-9a-f-]{36}$/,
    )
  })

  it('提示词尚不可确认时展示不可用说明且不提交最终确认', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup: (fetch) => {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', DETAIL_PATH, () => ({ body: detail(5, []) }))
        fetch.route('GET', WORKFLOW_PATH, () => ({ body: promptView(false) }))
      },
    })

    const prompt = await screen.findByRole('region', { name: '提示词确认' })
    expect(prompt).toBeVisible()
    expect(screen.getByText('提示词正在检查，确认按钮暂不可用。')).toBeVisible()
    expect(screen.getByRole('button', { name: '提示词检查中…' })).toBeDisabled()
    expect(harness.fetch.count('POST', ACTIONS_PATH + 'confirm_prompt')).toBe(0)
  })

  it('需求审查失败时展示错误与恢复动作，不再显示正在整理', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup: (fetch) => {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', DETAIL_PATH, () => ({ body: detail(1, []) }))
        fetch.route('GET', WORKFLOW_PATH, () => ({
          body: view({
            status: 'failed',
            issue: { code: 'REVIEW_UNAVAILABLE', message: '审查暂不可用，内容尚未放行。', template_id: 'REVIEW_UNAVAILABLE', params: {} },
            available_actions: [action('continue_workflow', '继续流程')],
          }),
        }))
      },
    })

    expect(await screen.findByText('审查暂不可用，内容尚未放行。')).toBeVisible()
    expect(screen.getByRole('button', { name: '继续流程' })).toBeVisible()
    expect(screen.queryByText('正在整理需求…')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: '任务状态' }).querySelector('.typing-dots')).toBeNull()
  })

  it('打开已有对话不会自动执行业务动作', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup: (fetch) => {
        fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        fetch.route('GET', DETAIL_PATH, () => ({ body: detail(1, []) }))
        fetch.route('GET', WORKFLOW_PATH, () => ({
          body: view({ available_actions: [action('compose_brief', '整理需求')] }),
        }))
      },
    })

    await screen.findByRole('region', { name: '生图对话' })
    await new Promise((resolve) => setTimeout(resolve, 150))
    const actionPosts = harness.fetch.requests.filter(
      (request) => request.method === 'POST' && request.path.startsWith(ACTIONS_PATH),
    )
    expect(actionPosts).toHaveLength(0)
  })
})
