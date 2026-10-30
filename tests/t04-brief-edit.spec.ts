import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleBrief,
  sampleMessage,
  sampleProject,
} from './support/fixtures'
import type { MessageCreateResponse, ProjectDetailResponse, ProjectView } from '@/api/types'

let harness: WorkspaceHarness | undefined
let messageId = 0

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = '/api/projects/' + PROJECT_A
const briefPath = `/api/projects/${PROJECT_A}/brief`
const confirmPath = `/api/projects/${PROJECT_A}/confirm`

function detailResponse(project: ProjectView, messages: ProjectDetailResponse['messages'] = []): ProjectDetailResponse {
  return { project, messages, generations: [] }
}

function reviewProject(overrides: Partial<ProjectView> = {}): ProjectView {
  return sampleProject({ id: PROJECT_A, revision: 3, prompt_revision: 3, confirmed_revision: 3, brief_status: 'review', ...overrides })
}

function messageResponse(project: ProjectView, assistantContent: string): MessageCreateResponse {
  messageId += 1
  return {
    project,
    user_message: sampleMessage({
      id: 't04-user-' + messageId,
      sequence_no: messageId * 2,
      role: 'user',
      content: '请按当前已保存的需求重新整理。',
      project_revision: project.revision,
    }),
    assistant_message: sampleMessage({
      id: 't04-assistant-' + messageId,
      sequence_no: messageId * 2 + 1,
      role: 'assistant',
      content: assistantContent,
      project_revision: project.revision,
      brief_snapshot: project.brief,
    }),
  }
}

async function mountReviewProject(
  project: ProjectView = reviewProject(),
  getResponses = [{ body: detailResponse(project) }],
  messages: ProjectDetailResponse['messages'] = [],
) {
  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: `/?project=${PROJECT_A}`,
    routes: [{ method: 'GET', url: detailPath, responses: getResponses }],
  })
  await screen.findByRole('region', { name: '当前需求' })
  return harness
}

function emptyDetail(project: ProjectView, messages: ProjectDetailResponse['messages'] = []): ProjectDetailResponse {
  return detailResponse(project, messages)
}

function currentCard(): HTMLElement {
  return screen.getByRole('region', { name: '当前需求' })
}

describe('T04 需求卡编辑与独立保存', () => {
  it('当前需求默认只读，进入编辑后只展示五个可编辑字段', async () => {
    harness = await mountReviewProject()
    const card = currentCard()
    expect(within(card).getByText('米色亚麻托特包')).toBeVisible()
    expect(within(card).queryByLabelText('商品描述')).toBeNull()

    await harness.user.click(within(card).getByRole('button', { name: '编辑需求' }))

    expect(within(card).getByLabelText('商品描述')).toHaveValue('米色亚麻托特包')
    expect(within(card).getByLabelText('图片用途')).toHaveValue('电商主图')
    expect(within(card).getByLabelText('风格')).toHaveValue('简洁暖色，突出材质')
    expect(within(card).getByLabelText('构图与场景')).toHaveValue('居中构图，浅米色背景')
    expect(within(card).getByLabelText('图片中的文字')).toHaveValue('')
    expect(within(card).getAllByRole('textbox')).toHaveLength(5)
    expect(within(card).queryByText('1024x1024')).toBeVisible()
  })

  it('保存只发送变更字段的 PATCH，成功后更新卡片并提示旧提示词过期', async () => {
    const updated = reviewProject({
      revision: 4,
      prompt_revision: null,
      confirmed_revision: null,
      brief_status: 'draft',
      brief: sampleBrief({ product_description: '更新后的托特包' }),
    })
    harness = await mountReviewProject()
    harness.fetch.json('PATCH', briefPath, updated)

    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const product = screen.getByLabelText('商品描述')
    await harness.user.clear(product)
    await harness.user.type(product, '更新后的托特包')

    expect(screen.getByText('有未保存的修改')).toBeVisible()
    expect(screen.getByRole('button', { name: '确认当前需求' })).toBeDisabled()
    await harness.user.click(screen.getByRole('button', { name: '保存需求' }))

    expect(await screen.findByText('需求已保存。旧提示词已过期，请重新整理需求后再确认。')).toBeVisible()
    expect(within(currentCard()).getByText('更新后的托特包')).toBeVisible()
    expect(screen.queryByLabelText('商品描述')).toBeNull()
    const patch = harness.fetch.last('PATCH', briefPath)
    expect(patch?.body).toEqual({ expected_revision: 3, product_description: '更新后的托特包' })
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(0)
    expect(harness.fetch.count('POST', /\/confirm$/)).toBe(0)
    expect(screen.getByText('该提示词已过期，请重新整理需求后再确认。')).toBeVisible()
    expect(screen.getByRole('button', { name: '确认当前需求' })).toBeDisabled()
  })

  it('取消编辑恢复服务端卡片，不发送保存请求', async () => {
    harness = await mountReviewProject()
    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const purpose = screen.getByLabelText('图片用途')
    await harness.user.clear(purpose)
    await harness.user.type(purpose, '临时修改')
    expect(screen.getByText('有未保存的修改')).toBeVisible()

    await harness.user.click(screen.getByRole('button', { name: '取消修改' }))

    expect(screen.queryByLabelText('图片用途')).toBeNull()
    expect(within(currentCard()).getByText('电商主图')).toBeVisible()
    expect(screen.queryByText('有未保存的修改')).toBeNull()
    expect(harness.fetch.count('PATCH', briefPath)).toBe(0)
  })

  it('保存失败保留编辑缓冲和本地内容', async () => {
    harness = await mountReviewProject()
    harness.fetch.route('PATCH', briefPath, () => ({
      status: 500,
      body: { error: { code: 'SAVE_FAILED', message: '保存失败。' } },
    }))

    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const style = screen.getByLabelText('风格')
    await harness.user.clear(style)
    await harness.user.type(style, '失败也要保留的风格')
    await harness.user.click(screen.getByRole('button', { name: '保存需求' }))

    expect(await screen.findByText('保存失败。')).toBeVisible()
    expect(screen.getByLabelText('风格')).toHaveValue('失败也要保留的风格')
    expect(screen.getByText('有未保存的修改')).toBeVisible()
    expect(harness.fetch.count('PATCH', briefPath)).toBe(1)
  })

  it('409 保留本地修改，用户加载最新需求后才替换编辑缓冲', async () => {
    const initial = reviewProject()
    const latest = reviewProject({
      revision: 6,
      prompt_revision: 6,
      confirmed_revision: null,
      brief_status: 'review',
      brief: sampleBrief({ product_description: '其他端已更新的商品', prompt: '其他端的最新提示词。' }),
    })
    harness = await mountReviewProject(initial, [
      { body: detailResponse(initial) },
      { body: detailResponse(latest) },
    ])
    harness.fetch.route('PATCH', briefPath, () => ({
      status: 409,
      body: { error: { code: 'REVISION_CONFLICT', message: '版本冲突。' } },
    }))

    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const product = screen.getByLabelText('商品描述')
    await harness.user.clear(product)
    await harness.user.type(product, '冲突时保留的本地内容')
    await harness.user.click(screen.getByRole('button', { name: '保存需求' }))

    expect(await screen.findByText(/需求已在别处更新，本地修改仍保留/)).toBeVisible()
    expect(screen.getByLabelText('商品描述')).toHaveValue('冲突时保留的本地内容')
    expect(screen.getByRole('button', { name: '加载最新需求' })).toBeVisible()
    expect(harness.fetch.count('PATCH', briefPath)).toBe(1)

    await harness.user.click(screen.getByRole('button', { name: '加载最新需求' }))

    expect(await screen.findByText('已加载最新需求。', { selector: '.status-notice' })).toBeVisible()
    expect(within(currentCard()).getByText('其他端已更新的商品')).toBeVisible()
    expect(harness.fetch.count('GET', detailPath)).toBe(2)
    expect(harness.fetch.count('PATCH', briefPath)).toBe(1)
  })

  it('基础校验定位字段，不截断超出长度限制的输入', async () => {
    harness = await mountReviewProject()
    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const product = screen.getByLabelText('商品描述')
    await harness.user.clear(product)
    await harness.user.click(screen.getByRole('button', { name: '保存需求' }))
    expect(await screen.findByText('商品描述不能为空。')).toBeVisible()
    expect(harness.fetch.count('PATCH', briefPath)).toBe(0)

    const longValue = '长'.repeat(8001)
    fireEvent.input(screen.getByLabelText('商品描述'), { target: { value: longValue } })
    await harness.user.click(screen.getByRole('button', { name: '保存需求' }))
    expect(await screen.findByText(/商品描述不能超过 8,000 个字符/)).toBeVisible()
    expect((screen.getByLabelText('商品描述') as HTMLTextAreaElement).value).toBe(longValue)
    expect(harness.fetch.count('PATCH', briefPath)).toBe(0)
  })
})

describe('T04 单独整理与确认', () => {
  it('重新整理使用固定消息并保持独立保存动作', async () => {
    const reorganized = reviewProject({
      revision: 4,
      prompt_revision: 4,
      brief: sampleBrief({ product_description: '重新整理后的商品', prompt: '重新整理后的完整提示词。' }),
    })
    harness = await mountReviewProject()
    harness.fetch.route('POST', /\/messages$/, () => ({
      body: messageResponse(reorganized, '已重新整理当前需求。'),
      delayMs: 200,
    }))

    await harness.user.click(screen.getByRole('button', { name: '重新整理需求' }))

    expect(await screen.findByText('正在整理需求…')).toBeVisible()
    expect(await screen.findByText('已重新整理当前需求。')).toBeVisible()
    expect(harness.fetch.last('POST', /\/messages$/)?.body).toEqual({
      content: '请按当前已保存的需求重新整理。',
      expected_revision: 3,
    })
    expect(harness.fetch.count('PATCH', briefPath)).toBe(0)
    expect(harness.fetch.count('POST', /\/generations$/)).toBe(0)
  })

  it('存在未发送文字或未保存字段时不能确认', async () => {
    harness = await mountReviewProject()
    await harness.user.click(screen.getByRole('button', { name: '描述需求' }))
    const composer = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, '还未发送的补充')

    await harness.user.click(screen.getByRole('button', { name: '确认需求' }))
    expect(screen.getByRole('button', { name: '确认当前需求' })).toBeDisabled()
    expect(screen.getByText('请先发送或清空输入框中的文字。')).toBeVisible()

    await harness.user.click(screen.getByRole('button', { name: '描述需求' }))
    await harness.user.clear(screen.getByRole('textbox', { name: '描述商品与图片需求' }))
    await harness.user.click(screen.getByRole('button', { name: '确认需求' }))
    expect(screen.getByRole('button', { name: '确认当前需求' })).toBeEnabled()

    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const composition = screen.getByLabelText('构图与场景')
    await harness.user.clear(composition)
    await harness.user.type(composition, '修改后未保存')
    expect(screen.getByRole('button', { name: '确认当前需求' })).toBeDisabled()
    expect(screen.getByText('有未保存的修改，请先保存或取消修改。')).toBeVisible()

    await harness.user.click(screen.getByRole('button', { name: '取消修改' }))
    expect(screen.getByRole('button', { name: '确认当前需求' })).toBeEnabled()
  })

  it('确认只提交当前 revision，成功后显示已确认且不创建生成记录', async () => {
    const confirmed = reviewProject({ brief_status: 'confirmed', confirmed_revision: 3 })
    harness = await mountReviewProject()
    harness.fetch.json('POST', confirmPath, confirmed)

    await harness.user.click(screen.getByRole('button', { name: '确认当前需求' }))

    expect(await screen.findByText('需求已确认。', { selector: '.status-notice' })).toBeVisible()
    expect(harness.fetch.last('POST', confirmPath)?.body).toEqual({ revision: 3 })
    expect(harness.fetch.count('POST', /\/generations$/)).toBe(0)
    expect(screen.getByText('需求已确认', { selector: '.status-badge' })).toBeVisible()
    expect(screen.getByRole('button', { name: '确认当前需求' })).toBeDisabled()
  })

  it('快速重复点击确认最多发出一个请求', async () => {
    const confirmed = reviewProject({ brief_status: 'confirmed', confirmed_revision: 3 })
    harness = await mountReviewProject()
    harness.fetch.route('POST', confirmPath, () => ({ body: confirmed, delayMs: 50 }))
    const button = screen.getByRole('button', { name: '确认当前需求' })

    fireEvent.click(button)
    fireEvent.click(button)

    expect(await screen.findByText('需求已确认。')).toBeVisible()
    expect(harness.fetch.count('POST', confirmPath)).toBe(1)
  })

  it('长提示词首次展开，可收起并再次展开', async () => {
    const longPrompt = '用于测试折叠的完整提示词。'.repeat(30)
    harness = await mountReviewProject(reviewProject({ brief: sampleBrief({ prompt: longPrompt }) }))

    expect(screen.getByText(longPrompt)).toBeVisible()
    expect(screen.getByRole('button', { name: '收起提示词' })).toHaveAttribute('aria-expanded', 'true')

    await harness.user.click(screen.getByRole('button', { name: '收起提示词' }))
    expect(screen.getByRole('button', { name: '展开提示词' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText(longPrompt)).not.toBeVisible()

    await harness.user.click(screen.getByRole('button', { name: '展开提示词' }))
    expect(screen.getByText(longPrompt)).toBeVisible()
  })

  it('可用键盘进入编辑、移动到保存并保存', async () => {
    const updated = reviewProject({
      revision: 4,
      prompt_revision: null,
      confirmed_revision: null,
      brief_status: 'draft',
      brief: sampleBrief({ product_description: '键盘保存的商品' }),
    })
    harness = await mountReviewProject()
    harness.fetch.json('PATCH', briefPath, updated)
    const edit = screen.getByRole('button', { name: '编辑需求' })
    edit.focus()
    await harness.user.keyboard('{Enter}')

    const product = screen.getByLabelText('商品描述')
    expect(product).toHaveFocus()
    await harness.user.clear(product)
    await harness.user.type(product, '键盘保存的商品')
    for (let index = 0; index < 5; index += 1) await harness.user.tab()
    const save = screen.getByRole('button', { name: '保存需求' })
    expect(save).toHaveFocus()
    await harness.user.keyboard('{Enter}')

    expect(await screen.findByText('需求已保存。旧提示词已过期，请重新整理需求后再确认。')).toBeVisible()
    expect(harness.fetch.count('PATCH', briefPath)).toBe(1)
  })
})

describe('T04 需求卡一致性与失败边界', () => {
  it('只有最新需求卡可编辑，历史快照保持只读', async () => {
    const initial = reviewProject()
    const history = [
      sampleMessage({
        id: 't04-history',
        sequence_no: 1,
        role: 'assistant',
        content: '早先整理的需求。',
        brief_snapshot: sampleBrief({ product_description: '历史商品' }),
      }),
    ]
    harness = await mountReviewProject(initial, [{ body: emptyDetail(initial, history) }])

    await harness.user.click(screen.getByRole('button', { name: '描述需求' }))
    const historyCard = screen.getByRole('region', { name: '当时的需求快照' })
    expect(within(historyCard).getByText('历史商品')).toBeVisible()
    expect(within(historyCard).queryByRole('button', { name: '编辑需求' })).toBeNull()
    expect(within(historyCard).queryByRole('textbox')).toBeNull()

    await harness.user.click(screen.getByRole('button', { name: '确认需求' }))
    expect(within(currentCard()).getByRole('button', { name: '编辑需求' })).toBeVisible()
  })

  it('重新整理失败保留已保存卡片与原文，只由用户再次触发', async () => {
    harness = await mountReviewProject()
    harness.fetch.route('POST', /\/messages$/, () => ({
      status: 500,
      body: { error: { code: 'BRIEF_FAILED', message: '重新整理失败，已保存的原文仍会保留。' } },
    }))

    await harness.user.click(screen.getByRole('button', { name: '重新整理需求' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('重新整理失败，已保存的原文仍会保留。')
    expect(within(currentCard()).getByText('米色亚麻托特包')).toBeVisible()
    expect(within(currentCard()).getByText('电商主图')).toBeVisible()
    expect(screen.queryByLabelText('商品描述')).toBeNull()
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(1)
  })

  it('保存返回 413 时在需求区域提示并保留编辑缓冲', async () => {
    harness = await mountReviewProject()
    harness.fetch.route('PATCH', briefPath, () => ({
      status: 413,
      body: { error: { code: 'PAYLOAD_TOO_LARGE', message: '内容过长，无法保存。' } },
    }))

    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const product = screen.getByLabelText('商品描述')
    await harness.user.clear(product)
    await harness.user.type(product, '超过容量也要保留')
    await harness.user.click(screen.getByRole('button', { name: '保存需求' }))

    expect(await screen.findByText('保存内容过大，请精简字段后再试；本地修改仍保留。')).toBeVisible()
    expect(screen.getByLabelText('商品描述')).toHaveValue('超过容量也要保留')
    expect(harness.fetch.count('PATCH', briefPath)).toBe(1)
  })

  it('保存返回 422 时在需求区域提示并保留编辑缓冲', async () => {
    harness = await mountReviewProject()
    harness.fetch.route('PATCH', briefPath, () => ({
      status: 422,
      body: { error: { code: 'VALIDATION_FAILED', message: '字段不符合要求。' } },
    }))

    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const style = screen.getByLabelText('风格')
    await harness.user.clear(style)
    await harness.user.type(style, '待校验风格')
    await harness.user.click(screen.getByRole('button', { name: '保存需求' }))

    expect(await screen.findByText('部分字段不符合要求，请检查后再试；本地修改仍保留。')).toBeVisible()
    expect(screen.getByLabelText('风格')).toHaveValue('待校验风格')
    expect(harness.fetch.count('PATCH', briefPath)).toBe(1)
  })

  it('确认成功后解锁独立生成入口，键盘也能提交确认', async () => {
    const confirmed = reviewProject({ brief_status: 'confirmed', confirmed_revision: 3 })
    harness = await mountReviewProject()
    harness.fetch.json('POST', confirmPath, confirmed)
    const confirm = screen.getByRole('button', { name: '确认当前需求' })
    confirm.focus()
    await harness.user.keyboard('{Enter}')

    expect(await screen.findByText('需求已确认。', { selector: '.status-notice' })).toBeVisible()
    expect(harness.fetch.last('POST', confirmPath)?.body).toEqual({ revision: 3 })
    expect(screen.getByRole('button', { name: '生成图片' })).not.toHaveAttribute('aria-disabled', 'true')
  })
})
