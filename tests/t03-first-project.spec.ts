import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  emptyConfig,
  sampleBrief,
  sampleMessage,
  sampleProject,
} from './support/fixtures'
import type { MessageCreateResponse, ProjectDetailResponse, ProjectView } from '@/api/types'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const messagePath = /^\/api\/projects\/[^/]+\/messages$/

function detailResponse(
  project: ProjectView,
  messages: ProjectDetailResponse['messages'] = [],
  generations: ProjectDetailResponse['generations'] = [],
): ProjectDetailResponse {
  return { project, messages, generations }
}

function createdProject(overrides: Partial<ProjectView> = {}): ProjectView {
  return sampleProject({
    id: PROJECT_A,
    title: '新项目',
    revision: 0,
    prompt_revision: null,
    confirmed_revision: null,
    brief_status: 'draft',
    brief: sampleBrief({
      product_description: '',
      purpose: '',
      style: '',
      composition: '',
      text_content: '',
      image_size: null,
      prompt: '',
    }),
    ...overrides,
  })
}

function messageResponse(content: string, overrides: Partial<ProjectView> = {}): MessageCreateResponse {
  const project = sampleProject({
    id: PROJECT_A,
    title: '新项目',
    revision: 1,
    prompt_revision: 1,
    confirmed_revision: null,
    brief_status: 'review',
    brief: sampleBrief({ prompt: '整理后的完整提示词。' }),
    ...overrides,
  })
  return {
    project,
    user_message: sampleMessage({
      id: 'user-message-1',
      sequence_no: 1,
      role: 'user',
      content,
      project_revision: 1,
    }),
    assistant_message: sampleMessage({
      id: 'assistant-message-1',
      sequence_no: 2,
      role: 'assistant',
      content: '已整理为待确认需求，请核对。',
      project_revision: 1,
      brief_snapshot: sampleBrief(),
    }),
  }
}

async function fillAndSend(content: string): Promise<void> {
  const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
  await harness!.user.clear(textarea)
  await harness!.user.type(textarea, content)
  await harness!.user.click(screen.getByRole('button', { name: '发送' }))
}

describe('T03 首次创建与消息发送', () => {
  it('首次有效发送只创建一个项目并随后发送一条消息，使用服务端修订号并更新 URL', async () => {
    const created = createdProject()
    const response = messageResponse('米色托特包，简洁主图')
    harness = await mountWorkspace({
      config: configuredConfig(),
      setup: (fetch) => {
        fetch.json('POST', '/api/projects', created, 201)
        fetch.json('POST', messagePath, response)
      },
    })
    await harness.waitFor(() => expect(harness!.fetch.count('GET', '/api/config/status')).toBe(1))
    await fillAndSend('米色托特包，简洁主图')

    await harness.waitFor(() => expect(harness!.fetch.count('POST', '/api/projects')).toBe(1))
    expect(harness.fetch.count('POST', messagePath)).toBe(1)
    expect(harness.fetch.last('POST', '/api/projects')?.body).toEqual({})
    expect(harness.fetch.last('POST', messagePath)?.body).toEqual({
      content: '米色托特包，简洁主图',
      expected_revision: 0,
    })
    expect(window.location.search).toBe(`?project=${PROJECT_A}`)
    expect(await screen.findByText('已整理为待确认需求，请核对。')).toBeVisible()
    expect(screen.getByText('米色托特包，简洁主图')).toBeVisible()
    await harness.waitFor(() =>
      expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue(''),
    )
  })

  it('同步连点发送只创建一个项目并只发一条消息', async () => {
    const created = createdProject()
    harness = await mountWorkspace({
      config: configuredConfig(),
      setup: (fetch) => {
        fetch.json('POST', '/api/projects', created, 201)
        fetch.json('POST', messagePath, messageResponse('连点一次'), 200)
      },
    })
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '连点一次')
    const send = screen.getByRole('button', { name: '发送' })
    send.click()
    send.click()

    await harness.waitFor(() => expect(harness!.fetch.count('POST', messagePath)).toBe(1))
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
  })

  it('创建结果不明时保留输入、停止重复创建并给出核实入口', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      setup: (fetch) => {
        fetch.networkError('POST', '/api/projects')
        fetch.json('GET', '/api/projects', { items: [], next_cursor: null })
      },
    })
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '不要重复创建的商品')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('项目创建结果未确认')
    expect(textarea).toHaveValue('不要重复创建的商品')
    expect(window.location.search).toBe('')
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)

    await harness.user.click(screen.getByRole('button', { name: '发送' }))
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.count('POST', messagePath)).toBe(0)

    await harness.user.click(within(alert).getByRole('button', { name: '打开已有项目' }))
    expect(await screen.findByRole('dialog', { name: '已有项目' })).toBeVisible()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
  })

  it('创建成功但消息失败后保留项目和输入，再次发送复用现有项目', async () => {
    const created = createdProject()
    harness = await mountWorkspace({
      config: configuredConfig(),
      setup: (fetch) => {
        fetch.json('POST', '/api/projects', created, 201)
        fetch.sequence('POST', messagePath, [
          {
            status: 500,
            body: { error: { code: 'LLM_FAILED', message: '需求整理失败，请稍后重试。' } },
          },
          { body: messageResponse('整理重试内容'), delayMs: 10 },
        ])
      },
    })
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '整理重试内容')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('需求整理失败')
    expect(textarea).toHaveValue('整理重试内容')
    expect(window.location.search).toBe(`?project=${PROJECT_A}`)
    expect(screen.getByRole('heading', { name: '新项目' })).toBeVisible()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)

    await harness.user.click(screen.getByRole('button', { name: '发送' }))
    await harness.waitFor(() => expect(harness!.fetch.count('POST', messagePath)).toBe(2))
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.last('POST', messagePath)?.body).toEqual({
      content: '整理重试内容',
      expected_revision: 0,
    })
    expect(await screen.findByText('已整理为待确认需求，请核对。')).toBeVisible()
  })
})

describe('T03 需求状态、快捷键与错误恢复', () => {
  it('review 显示待确认、当前需求卡与同版本完整提示词，空文字显示不要求加字', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      url: `/?project=${PROJECT_A}`,
      setup: (fetch) => {
        fetch.json(
          'GET',
          `/api/projects/${PROJECT_A}`,
          detailResponse(
            sampleProject({
              id: PROJECT_A,
              title: '待确认项目',
              brief_status: 'review',
              brief: sampleBrief({ text_content: '', image_size: null, prompt: '同版本完整提示词。' }),
            }),
            [sampleMessage({ id: 'm1', content: '原始需求文字' })],
          ),
        )
      },
    })
    expect(await screen.findByRole('heading', { name: '待确认项目' })).toBeVisible()
    expect(screen.getByText('待确认：请核对当前需求卡与同一版本的完整提示词。')).toBeVisible()
    const card = screen.getByRole('region', { name: '当前需求' })
    expect(within(card).getByText('图片中的文字')).toBeVisible()
    expect(within(card).getByText('不要求加字')).toBeVisible()
    expect(within(card).getByText('同版本完整提示词。')).toBeVisible()
    expect(within(card).getByText('待配置')).toBeVisible()
    expect(within(card).queryByText('1024x1024')).not.toBeInTheDocument()
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('needs_input 显示助手问题与后续普通消息', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      url: `/?project=${PROJECT_A}`,
      setup: (fetch) => {
        fetch.json(
          'GET',
          `/api/projects/${PROJECT_A}`,
          detailResponse(
            sampleProject({
              id: PROJECT_A,
              title: '需要补充的项目',
              brief_status: 'needs_input',
              brief: sampleBrief({ product_description: '陶瓷杯' }),
            }),
            [
              sampleMessage({ id: 'm1', role: 'user', content: '做一个陶瓷杯主图' }),
              sampleMessage({ id: 'm2', role: 'assistant', content: '请补充杯子的材质与主要用途？' }),
              sampleMessage({ id: 'm3', role: 'user', content: '陶瓷，日常饮水场景。' }),
              sampleMessage({ id: 'm4', role: 'assistant', content: '收到，我会据此整理需求。' }),
            ],
          ),
        )
      },
    })
    expect(await screen.findByRole('heading', { name: '需要补充的项目' })).toBeVisible()
    expect(screen.getByText('请补充杯子的材质与主要用途？')).toBeVisible()
    expect(screen.getByText('陶瓷，日常饮水场景。')).toBeVisible()
    expect(screen.getByText('收到，我会据此整理需求。')).toBeVisible()
    expect(screen.getByText(/请用下方普通消息集中回答/)).toBeVisible()
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('缺少 LLM 配置时说明原因、保留输入且不创建项目', async () => {
    harness = await mountWorkspace({ config: emptyConfig() })
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '缺少配置时保留的商品描述')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('需求整理模型（LLM）尚未配置完成')
    expect(textarea).toHaveValue('缺少配置时保留的商品描述')
    expect(harness.fetch.count('POST', '/api/projects')).toBe(0)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('needs_input 回复后显示用户回答与更新后的 review 卡片', async () => {
    const initial = sampleProject({
      id: PROJECT_A,
      title: '澄清项目',
      revision: 2,
      prompt_revision: null,
      confirmed_revision: null,
      brief_status: 'needs_input',
      brief: sampleBrief({ product_description: '陶瓷杯', purpose: '', prompt: '' }),
    })
    const response = messageResponse('陶瓷，日常饮水场景。', {
      title: '澄清项目',
      revision: 3,
      prompt_revision: 3,
      brief_status: 'review',
      brief: sampleBrief({ product_description: '陶瓷杯', purpose: '日常饮水', prompt: '澄清后完整提示词。' }),
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      url: `/?project=${PROJECT_A}`,
      setup: (fetch) => {
        fetch.json(
          'GET',
          `/api/projects/${PROJECT_A}`,
          detailResponse(initial, [
            sampleMessage({ id: 'q1', role: 'assistant', content: '请补充杯子的材质与主要用途？' }),
          ]),
        )
        fetch.json('POST', messagePath, response, 200)
      },
    })
    expect(await screen.findByRole('heading', { name: '澄清项目' })).toBeVisible()
    await fillAndSend('陶瓷，日常饮水场景。')

    await harness.waitFor(() => expect(harness!.fetch.count('POST', messagePath)).toBe(1))
    expect(harness.fetch.last('POST', messagePath)?.body).toEqual({
      content: '陶瓷，日常饮水场景。',
      expected_revision: 2,
    })
    expect(screen.getByText('陶瓷，日常饮水场景。')).toBeVisible()
    expect(screen.getByText('已整理为待确认需求，请核对。')).toBeVisible()
    const card = screen.getByRole('region', { name: '当前需求' })
    expect(within(card).getByText('澄清后完整提示词。')).toBeVisible()
    expect(screen.getByText('待确认：请核对当前需求卡与同一版本的完整提示词。')).toBeVisible()
  })

  it('Enter 换行、Ctrl+Enter 发送、组合输入和纯空白均不误发', async () => {
    const created = createdProject()
    harness = await mountWorkspace({
      config: configuredConfig(),
      setup: (fetch) => {
        fetch.json('POST', '/api/projects', created, 201)
        fetch.json('POST', messagePath, messageResponse('最终发送'), 200)
      },
    })
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '第一行{enter}第二行')
    expect(textarea).toHaveValue('第一行\n第二行')
    expect(harness.fetch.writes).toHaveLength(0)

    await harness.user.clear(textarea)
    await harness.user.type(textarea, '组合输入内容')
    fireEvent.compositionStart(textarea)
    fireEvent.keyDown(textarea, { key: 'Enter', ctrlKey: true, isComposing: true, keyCode: 229 })
    fireEvent.compositionEnd(textarea)
    expect(harness.fetch.writes).toHaveLength(0)

    await harness.user.clear(textarea)
    await harness.user.type(textarea, '   ')
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
    fireEvent.keyDown(textarea, { key: 'Enter', ctrlKey: true, keyCode: 13 })
    expect(harness.fetch.writes).toHaveLength(0)

    await harness.user.clear(textarea)
    await harness.user.type(textarea, '最终发送')
    fireEvent.keyDown(textarea, { key: 'Enter', ctrlKey: true, keyCode: 13 })
    await harness.waitFor(() => expect(harness!.fetch.count('POST', messagePath)).toBe(1))
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.last('POST', messagePath)?.body).toEqual({
      content: '最终发送',
      expected_revision: 0,
    })
  })

  it('整理中显示 role=status，输入只读且刷新挂载不重发消息', async () => {
    const organizing = sampleProject({
      id: PROJECT_A,
      title: '整理中的项目',
      brief_status: 'organizing',
      brief_error: null,
    })
    const mountOrganizing = (): Promise<WorkspaceHarness> => {
      return mountWorkspace({
        config: emptyConfig(),
        url: `/?project=${PROJECT_A}`,
        setup: (fetch) => {
          fetch.json('GET', `/api/projects/${PROJECT_A}`, detailResponse(organizing))
        },
      })
    }
    harness = await mountOrganizing()
    expect(await screen.findByRole('heading', { name: '整理中的项目' })).toBeVisible()
    const status = screen.getByRole('status', { name: '需求整理状态' })
    expect(status).toHaveTextContent('正在整理需求…')
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
    expect(harness.fetch.count('POST', messagePath)).toBe(0)

    harness.dispose()
    harness = undefined
    harness = await mountOrganizing()
    expect(await screen.findByRole('heading', { name: '整理中的项目' })).toBeVisible()
    expect(harness.fetch.count('GET', `/api/projects/${PROJECT_A}`)).toBe(1)
    expect(harness.fetch.count('POST', messagePath)).toBe(0)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('brief_error 持久显示，只有点击重新整理才通过消息接口重试', async () => {
    const project = sampleProject({
      id: PROJECT_A,
      title: '整理失败项目',
      brief_status: 'review',
      brief_error: { code: 'ORGANIZATION_FAILED', message: '需求整理中断，请重新整理。' },
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      url: `/?project=${PROJECT_A}`,
      setup: (fetch) => {
        fetch.json('GET', `/api/projects/${PROJECT_A}`, detailResponse(project))
        fetch.json('POST', messagePath, messageResponse('', { title: '整理失败项目' }), 200)
      },
    })
    expect(await screen.findByRole('heading', { name: '整理失败项目' })).toBeVisible()
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('需求整理中断，请重新整理。')
    expect(harness.fetch.count('POST', messagePath)).toBe(0)

    await harness.user.click(within(alert).getByRole('button', { name: '重新整理' }))
    await harness.waitFor(() => expect(harness!.fetch.count('POST', messagePath)).toBe(1))
    expect(harness.fetch.last('POST', messagePath)?.body).toEqual({
      content: '请按当前需求卡重新整理需求。',
      expected_revision: 3,
    })
  })

  it('发送冲突保留输入并显示安全 alert，不创建新项目', async () => {
    const project = sampleProject({ id: PROJECT_A, title: '冲突项目', brief_status: 'review' })
    harness = await mountWorkspace({
      config: configuredConfig(),
      url: `/?project=${PROJECT_A}`,
      setup: (fetch) => {
        fetch.json('GET', `/api/projects/${PROJECT_A}`, detailResponse(project))
        fetch.json(
          'POST',
          messagePath,
          { error: { code: 'REVISION_CONFLICT', message: 'Bearer sk-secret 版本冲突' } },
          409,
        )
      },
    })
    expect(await screen.findByRole('heading', { name: '冲突项目' })).toBeVisible()
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '冲突时保留的内容')
    await harness.user.click(screen.getByRole('button', { name: '发送' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('需求已被更新')
    expect(alert).toHaveTextContent('记录已更新，请读取最新内容后再操作。')
    expect(alert).not.toHaveTextContent('sk-secret')
    expect(alert).not.toHaveTextContent('Bearer')
    expect(textarea).toHaveValue('冲突时保留的内容')
    expect(harness.fetch.count('POST', '/api/projects')).toBe(0)
  })

  it('消息列表向上阅读时保留 scrollTop 并显示有新内容入口，点击后到底', async () => {
    const project = sampleProject({
      id: PROJECT_A,
      title: '滚动项目',
      brief_status: 'review',
      revision: 4,
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      url: `/?project=${PROJECT_A}`,
      setup: (fetch) => {
        fetch.json(
          'GET',
          `/api/projects/${PROJECT_A}`,
          detailResponse(project, [sampleMessage({ id: 'm1', content: '已有消息' })]),
        )
        fetch.json('POST', messagePath, messageResponse('新反馈', { title: '滚动项目', revision: 5 }), 200)
      },
    })
    expect(await screen.findByRole('heading', { name: '滚动项目' })).toBeVisible()
    const log = screen.getByRole('log', { name: '对话消息' })
    Object.defineProperty(log, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(log, 'clientHeight', { configurable: true, value: 300 })
    log.scrollTop = 100
    fireEvent.scroll(log)

    await fillAndSend('新反馈')
    await harness.waitFor(() => expect(harness!.fetch.count('POST', messagePath)).toBe(1))
    const newContent = await screen.findByRole('button', { name: '有新内容' })
    expect(log.scrollTop).toBe(100)

    await harness.user.click(newContent)
    expect(log.scrollTop).toBe(700)
    await harness.waitFor(() =>
      expect(screen.queryByRole('button', { name: '有新内容' })).not.toBeInTheDocument(),
    )
  })
})
