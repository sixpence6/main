import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  PROJECT_B,
  configuredConfig,
  emptyConfig,
  sampleBrief,
  sampleMessage,
  sampleProject,
  sampleSummary,
} from './support/fixtures'
import type { MessageCreateResponse, ProjectDetailResponse, ProjectView } from '@/api/types'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = /^\/api\/projects\/[^/]+$/

function detailResponse(
  project: ProjectView,
  messages: ProjectDetailResponse['messages'] = [],
): ProjectDetailResponse {
  return { project, messages, generations: [] }
}

function emptyBrief() {
  return sampleBrief({
    product_description: '',
    purpose: '',
    style: '',
    composition: '',
    text_content: '',
    image_size: null,
    reference_asset_id: null,
    prompt: '',
  })
}

function draftProject(overrides: Partial<ProjectView> = {}): ProjectView {
  return sampleProject({
    id: PROJECT_A,
    title: '未命名项目',
    revision: 0,
    prompt_revision: null,
    confirmed_revision: null,
    brief_status: 'draft',
    brief: emptyBrief(),
    reference_asset: null,
    brief_error: null,
    ...overrides,
  })
}

function reviewProject(overrides: Partial<ProjectView> = {}): ProjectView {
  return sampleProject({
    id: PROJECT_A,
    title: '米色亚麻托特包主图',
    revision: 1,
    prompt_revision: 1,
    confirmed_revision: null,
    brief_status: 'review',
    brief: sampleBrief({ prompt: '为米色亚麻托特包生成一张简洁主图。' }),
    ...overrides,
  })
}

let messageId = 0
function messageResponse(project: ProjectView, content: string): MessageCreateResponse {
  messageId += 1
  const userMessage = sampleMessage({
    id: 'user-' + messageId,
    sequence_no: messageId * 2,
    role: 'user',
    content,
    project_revision: project.revision,
  })
  const assistantMessage = sampleMessage({
    id: 'assistant-' + messageId,
    sequence_no: messageId * 2 + 1,
    role: 'assistant',
    content: '已整理为可确认的需求。',
    project_revision: project.revision,
    brief_snapshot: project.brief,
  })
  return { project, user_message: userMessage, assistant_message: assistantMessage }
}

async function sendText(h: WorkspaceHarness, text: string): Promise<void> {
  const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
  await h.user.type(textarea, text)
  const send = screen.getByRole('button', { name: '发送' })
  await h.waitFor(() => expect(send).toBeEnabled())
  await h.user.click(send)
}

describe('T03 首次描述创建项目', () => {
  it('首次有效发送只创建一次项目，更新 URL 并提交原消息与 expected_revision', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    harness.fetch.route('POST', '/api/projects', () => ({
      status: 201,
      body: draftProject(),
      delayMs: 40,
    }))
    harness.fetch.route('POST', /\/messages$/, () => ({
      body: messageResponse(reviewProject(), '米色亚麻托特包，简洁主图'),
      delayMs: 40,
    }))

    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    const content = '米色亚麻托特包，简洁主图'
    await harness.user.type(textarea, content)
    const send = screen.getByRole('button', { name: '发送' })
    await harness.waitFor(() => expect(send).toBeEnabled())
    fireEvent.click(send)
    fireEvent.click(send)

    expect(await screen.findByText('已整理为可确认的需求。')).toBeVisible()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(1)
    const create = harness.fetch.last('POST', '/api/projects')
    expect(create?.body).toEqual({})
    const message = harness.fetch.last('POST', /\/messages$/)
    expect(message?.body).toEqual({ content, expected_revision: 0 })
    expect(harness.router.currentRoute.value.name).toBe('project-chat')
    expect(harness.router.currentRoute.value.params.projectId).toBe(PROJECT_A)
    expect(screen.getByRole('heading', { name: '米色亚麻托特包主图' })).toBeVisible()
    expect(screen.getByText('待确认')).toBeVisible()
    expect(screen.getByText(content)).toBeVisible()
    expect(screen.queryByRole('navigation', { name: '工作阶段' })).toBeNull()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(1)
  })

  it('创建回执不明时保留输入、停止再次创建并引导核实已有项目', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    harness.fetch.networkError('POST', '/api/projects', 'Failed to fetch')

    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '不要丢失的首次描述')
    const send = screen.getByRole('button', { name: '发送' })
    await harness.waitFor(() => expect(send).toBeEnabled())
    await harness.user.click(send)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('项目创建结果未确认')
    expect(alert).toHaveTextContent('已有项目')
    expect((textarea as HTMLTextAreaElement).value).toBe('不要丢失的首次描述')
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(0)
    expect(send).toBeDisabled()

    fireEvent.click(send)
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
  })

  it('创建成功但整理失败时复用同一项目，保留原文并由用户再次发送', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    harness.fetch.json('POST', '/api/projects', draftProject(), 201)
    let attempts = 0
    harness.fetch.route('POST', /\/messages$/, () => {
      attempts += 1
      if (attempts === 1) {
        return {
          status: 503,
          body: { error: { code: 'LLM_UNAVAILABLE', message: '模型暂时不可用。' } },
        }
      }
      return { body: messageResponse(reviewProject({ title: '复用项目' }), '保留的原文') }
    })

    await sendText(harness, '保留的原文')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('LLM 尚未配置或当前不可用')
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue('保留的原文')
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(1)

    await harness.user.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByText('已整理为可确认的需求。')).toBeVisible()
    expect(screen.getByRole('heading', { name: '复用项目' })).toBeVisible()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(2)
    expect(harness.fetch.last('POST', /\/messages$/)?.body).toEqual({
      content: '保留的原文',
      expected_revision: 0,
    })
  })
})

describe('T03 澄清与需求卡', () => {
  it('已有 needs_input 项目可集中回答澄清，成功后展示同版本提示词和卡片', async () => {
    const needsInput = sampleProject({
      id: PROJECT_B,
      title: '待补充的托特包',
      revision: 2,
      prompt_revision: 1,
      confirmed_revision: null,
      brief_status: 'needs_input',
      brief: sampleBrief({ product_description: '米色亚麻托特包', prompt: '' }),
    })
    const reviewed = reviewProject({
      id: PROJECT_B,
      title: '待补充的托特包',
      revision: 3,
      brief: sampleBrief({
        product_description: '米色亚麻托特包',
        style: '简洁暖色',
        prompt: '使用补充后的完整提示词。',
      }),
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_B,
      routes: [
        { method: 'GET', url: '/api/projects/' + PROJECT_B, responses: [{ body: detailResponse(needsInput, [sampleMessage({ id: 'ask', role: 'assistant', content: '请确认使用场景和文字要求。' })]) }] },
        { method: 'POST', url: /\/messages$/, responses: [{ body: messageResponse(reviewed, '用于电商主图，不需要加字') }] },
      ],
    })

    expect(await screen.findByText('请确认使用场景和文字要求。')).toBeVisible()
    await sendText(harness, '用于电商主图，不需要加字')
    expect(await screen.findByText('已整理为可确认的需求。')).toBeVisible()
    expect(harness.fetch.last('POST', /\/messages$/)?.body).toEqual({
      content: '用于电商主图，不需要加字',
      expected_revision: 2,
    })
    expect(screen.queryByRole('navigation', { name: '工作阶段' })).toBeNull()
    expect(screen.getByText('待确认')).toBeVisible()
  })

  it('Enter 发送，Shift+Enter 换行，中文组合输入不误发，纯空白不可发送', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    harness.fetch.json('POST', '/api/projects', draftProject(), 201)
    harness.fetch.route('POST', /\/messages$/, () => ({ body: messageResponse(reviewProject(), '第一行\n第二行') }))

    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '第一行{shift>}{enter}{/shift}第二行')
    expect((textarea as HTMLTextAreaElement).value).toBe('第一行\n第二行')
    expect(harness.fetch.writes).toHaveLength(0)

    fireEvent.compositionStart(textarea)
    fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true })
    expect(harness.fetch.writes).toHaveLength(0)
    fireEvent.compositionEnd(textarea)

    await harness.user.keyboard('{Control>}{Enter}{/Control}')
    expect(await screen.findByText('已整理为可确认的需求。')).toBeVisible()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(1)

    harness.dispose()
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    const blank = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(blank, '   ')
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
    expect(harness.fetch.writes).toHaveLength(0)
  })
})

describe('T03 恢复与可访问状态', () => {
  it('刷新 organizing 项目只读等待，不自动重发消息', async () => {
    const organizing = sampleProject({
      id: PROJECT_B,
      title: '正在整理的项目',
      revision: 1,
      prompt_revision: null,
      confirmed_revision: null,
      brief_status: 'organizing',
      brief: emptyBrief(),
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      url: '/?project=' + PROJECT_B,
      routes: [
        { method: 'GET', url: '/api/projects/' + PROJECT_B, responses: [{ body: detailResponse(organizing) }] },
      ],
    })

    expect(await screen.findByText('正在整理需求…')).toBeVisible()
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toBeDisabled()
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(0)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('向上阅读时新增消息显示“有新内容”，点击后跟随到底部', async () => {
    const project = reviewProject({ id: PROJECT_B, title: '滚动项目' })
    const history = Array.from({ length: 12 }, (_, index) => sampleMessage({
      id: 'history-' + index,
      sequence_no: index + 1,
      role: index % 2 === 0 ? 'user' : 'assistant',
      content: '历史消息 ' + index,
      brief_snapshot: null,
    }))
    harness = await mountWorkspace({
      config: configuredConfig(),
      url: '/?project=' + PROJECT_B,
      routes: [
        { method: 'GET', url: '/api/projects/' + PROJECT_B, responses: [{ body: detailResponse(project, history) }] },
        { method: 'POST', url: /\/messages$/, responses: [{ body: messageResponse(project, '滚动后的新消息') }] },
      ],
    })

    expect(await screen.findByText('历史消息 0')).toBeVisible()
    const scroller = screen.getByLabelText('对话记录滚动区域')
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(scroller, 'clientHeight', { configurable: true, value: 200 })
    scroller.scrollTop = 0
    fireEvent.scroll(scroller)

    await sendText(harness, '滚动后的新消息')
    const newContent = await screen.findByRole('button', { name: '有新内容' })
    expect(newContent).toBeVisible()
    await harness.user.click(newContent)
    expect(scroller.scrollTop).toBe(1000)
  })

  it('缺少 LLM 配置时说明原因并保留输入，不产生写请求', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '需要保留的草稿')
    expect(screen.getByText(/LLM 尚未配置/)).toBeVisible()
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('创建成功但整理失败后刷新，读回项目而不自动重发', async () => {
    const project = draftProject({ id: PROJECT_B, title: '已创建项目' })
    harness = await mountWorkspace({
      config: configuredConfig(),
      url: '/?project=' + PROJECT_B,
      routes: [
        { method: 'GET', url: '/api/projects/' + PROJECT_B, responses: [{ body: detailResponse(project) }] },
      ],
    })
    expect(await screen.findByRole('heading', { name: '已创建项目' })).toBeVisible()
    expect(harness.fetch.count('GET', detailPath)).toBe(1)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(0)
    expect(harness.fetch.writes).toHaveLength(0)
  })
})


describe('T03 失败恢复与修订值', () => {
  it('整理失败持久显示，只有明确点击重新整理才通过消息接口重试', async () => {
    const failed = draftProject({
      id: PROJECT_B,
      title: '整理失败项目',
      brief_error: { code: 'BRIEF_FAILED', message: '整理失败，已保存的原文仍会保留。' },
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      url: '/?project=' + PROJECT_B,
      routes: [
        { method: 'GET', url: '/api/projects/' + PROJECT_B, responses: [{ body: detailResponse(failed) }] },
        {
          method: 'POST',
          url: /\/messages$/,
          responses: [{
            body: messageResponse(
              reviewProject({ id: PROJECT_B, title: '整理恢复', revision: 2 }),
              '请按当前已保存的需求重新整理。',
            ),
          }],
        },
      ],
    })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('需求整理失败')
    expect(alert).toHaveTextContent('整理失败，已保存的原文仍会保留。')
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(0)

    await harness.user.click(screen.getByRole('button', { name: '重新整理' }))
    expect(await screen.findByRole('heading', { name: '整理恢复' })).toBeVisible()
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(1)
    expect(harness.fetch.last('POST', /\/messages$/)?.body).toEqual({
      content: '请按当前已保存的需求重新整理。',
      expected_revision: 0,
    })
  })

  it('409 冲突保留原文并显示安全说明，不自动重发', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    harness.fetch.json('POST', '/api/projects', draftProject(), 201)
    harness.fetch.json(
      'POST',
      /\/messages$/,
      { error: { code: 'REVISION_CONFLICT', message: '需求已在其他页面更新。' } },
      409,
    )

    await sendText(harness, '冲突时不能丢的描述')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('需求已在别处更新')
    expect(alert).toHaveTextContent('输入内容已保留')
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue('冲突时不能丢的描述')
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(1)
    expect(harness.router.currentRoute.value.name).toBe('project-chat')
    expect(harness.router.currentRoute.value.params.projectId).toBe(PROJECT_A)
  })

  it('第二次发送使用响应中的新修订号，不再使用本地推算', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    harness.fetch.json('POST', '/api/projects', draftProject(), 201)
    let round = 0
    harness.fetch.route('POST', /\/messages$/, () => {
      round += 1
      if (round === 1) {
        return { body: messageResponse(reviewProject({ revision: 1 }), '第一轮描述') }
      }
      return {
        body: messageResponse(
          reviewProject({ title: '第二轮需求', revision: 2 }),
          '第二轮描述',
        ),
      }
    })

    await sendText(harness, '第一轮描述')
    expect(await screen.findByText('已整理为可确认的需求。')).toBeVisible()
    await sendText(harness, '第二轮描述')
    expect(await screen.findByRole('heading', { name: '第二轮需求' })).toBeVisible()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(2)
    expect(harness.fetch.last('POST', /\/messages$/)?.body).toEqual({
      content: '第二轮描述',
      expected_revision: 1,
    })
  })
})
