import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  PROJECT_B,
  configuredConfig,
  sampleBrief,
  sampleGeneration,
  sampleMessage,
  sampleProject,
  sampleSummary,
} from './support/fixtures'
import type {
  MessageCreateResponse,
  ProjectDetailResponse,
  ProjectView,
} from '@/api/types'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPathA = `/api/projects/${PROJECT_A}`
const detailPathB = `/api/projects/${PROJECT_B}`
const messagesPathA = `/api/projects/${PROJECT_A}/messages`
const briefPathA = `/api/projects/${PROJECT_A}/brief`

function detailResponse(
  project: ProjectDetailResponse['project'],
  messages: ProjectDetailResponse['messages'] = [],
  generations: ProjectDetailResponse['generations'] = [],
): ProjectDetailResponse {
  return { project, messages, generations }
}

function projectA(overrides: Partial<ProjectView> = {}): ProjectView {
  return sampleProject({
    id: PROJECT_A,
    title: '春季托特包主图',
    ...overrides,
  })
}

function projectB(): ProjectView {
  return sampleProject({
    id: PROJECT_B,
    title: '陶瓷马克杯',
    brief: sampleBrief({ product_description: '陶瓷马克杯' }),
  })
}

function messageResponse(project: ProjectView): MessageCreateResponse {
  return {
    project,
    user_message: sampleMessage({
      id: 'message-user-late',
      sequence_no: 2,
      role: 'user',
      content: '迟到的用户消息',
      project_revision: 4,
    }),
    assistant_message: sampleMessage({
      id: 'message-assistant-late',
      sequence_no: 3,
      role: 'assistant',
      content: '迟到的助手回复',
      project_revision: 4,
    }),
  }
}

async function openProjectDrawer(): Promise<HTMLElement> {
  await harness!.user.click(screen.getByRole('button', { name: '已有项目' }))
  return screen.findByRole('dialog', { name: '已有项目' })
}

async function mountProjectA(extraRoutes: NonNullable<Parameters<typeof mountWorkspace>[0]>['routes'] = []) {
  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: `/?project=${PROJECT_A}`,
    routes: [
      { method: 'GET', url: detailPathA, responses: [{ body: detailResponse(projectA()) }] },
      ...extraRoutes,
    ],
  })
  await screen.findByRole('heading', { name: '春季托特包主图' })
  return harness
}

function currentCard(): HTMLElement {
  return screen.getByRole('region', { name: '当前需求' })
}

function beforeUnloadEvent(): Event {
  return new Event('beforeunload', { cancelable: true })
}

describe('T07 导航确认、草稿保留与版本冲突', () => {
  it('干净状态新建项目不弹确认，直接进入空白工作台', async () => {
    harness = await mountProjectA()

    await harness.user.click(screen.getByRole('button', { name: '新建项目' }))

    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(window.location.search).toBe('')
    expect(screen.queryByRole('heading', { name: '春季托特包主图' })).toBeNull()
    expect(screen.getByText('从一个商品想法开始')).toBeVisible()
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('有未发送文字时新建项目先确认，取消保留原文，确认后清空', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    const composer = screen.getByLabelText('描述商品与图片需求')
    await harness.user.type(composer, '尚未发送的草稿')

    await harness.user.click(screen.getByRole('button', { name: '新建项目' }))
    const dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    expect(within(dialog).getByText('输入框中尚未发送的文字')).toBeVisible()
    expect(within(dialog).getByText(/将前往「新建项目」/)).toBeVisible()

    await harness.user.click(within(dialog).getByRole('button', { name: '继续编辑' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(screen.getByLabelText('描述商品与图片需求')).toHaveValue('尚未发送的草稿')
    expect(window.location.search).toBe('')

    await harness.user.click(screen.getByRole('button', { name: '新建项目' }))
    await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    await harness.user.click(screen.getByRole('button', { name: '放弃修改' }))

    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(screen.getByLabelText('描述商品与图片需求')).toHaveValue('')
    expect(window.location.search).toBe('')
  })

  it('切换项目时取消确认会恢复原项目、URL 和未保存的需求字段', async () => {
    harness = await mountProjectA([
      { method: 'GET', url: detailPathB, responses: [{ body: detailResponse(projectB()) }] },
      {
        method: 'GET',
        url: '/api/projects',
        responses: [{
          body: {
            items: [
              sampleSummary({ id: PROJECT_A, title: '春季托特包主图' }),
              sampleSummary({ id: PROJECT_B, title: '陶瓷马克杯' }),
            ],
            next_cursor: null,
          },
        }],
      },
    ])

    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const product = screen.getByLabelText('商品描述')
    await harness.user.clear(product)
    await harness.user.type(product, '本地尚未保存的商品描述')

    let drawer = await openProjectDrawer()
    await harness.user.click(within(drawer).getByRole('button', { name: /陶瓷马克杯/ }))
    const dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    expect(within(dialog).getByText('需求卡片中尚未保存的修改')).toBeVisible()
    expect(within(dialog).getByText(/将前往「切换项目」/)).toBeVisible()
    await harness.user.click(within(dialog).getByRole('button', { name: '继续编辑' }))

    expect(window.location.search).toBe(`?project=${PROJECT_A}`)
    expect(screen.getByRole('heading', { name: '春季托特包主图' })).toBeVisible()
    expect(screen.getByLabelText('商品描述')).toHaveValue('本地尚未保存的商品描述')
    expect(harness.fetch.count('GET', detailPathB)).toBe(0)

    drawer = await openProjectDrawer()
    await harness.user.click(within(drawer).getByRole('button', { name: /陶瓷马克杯/ }))
    await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    await harness.user.click(screen.getByRole('button', { name: '放弃修改' }))

    expect(await screen.findByRole('heading', { name: '陶瓷马克杯' })).toBeVisible()
    expect(window.location.search).toBe(`?project=${PROJECT_B}`)
    expect(screen.queryByLabelText('商品描述')).toBeNull()
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('浏览器后退遇到未保存内容时恢复原 URL，取消保留、确认才进入目标项目', async () => {
    harness = await mountProjectA([
      { method: 'GET', url: detailPathB, responses: [{ body: detailResponse(projectB()) }] },
    ])
    const composer = screen.getByLabelText('描述商品与图片需求')
    await harness.user.type(composer, '后退前的草稿')

    window.history.pushState({}, '', `/?project=${PROJECT_B}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
    const dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    expect(window.location.search).toBe(`?project=${PROJECT_A}`)
    expect(within(dialog).getByText(/将前往「离开当前项目」/)).toBeVisible()

    await harness.user.click(within(dialog).getByRole('button', { name: '继续编辑' }))
    expect(window.location.search).toBe(`?project=${PROJECT_A}`)
    expect(screen.getByRole('heading', { name: '春季托特包主图' })).toBeVisible()
    expect(composer).toHaveValue('后退前的草稿')
    expect(harness.fetch.count('GET', detailPathB)).toBe(0)

    window.history.pushState({}, '', `/?project=${PROJECT_B}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
    await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    await harness.user.click(screen.getByRole('button', { name: '放弃修改' }))

    expect(await screen.findByRole('heading', { name: '陶瓷马克杯' })).toBeVisible()
    expect(window.location.search).toBe(`?project=${PROJECT_B}`)
  })

  it('beforeunload 只在存在未发送文字或未保存字段时注册，保存后移除', async () => {
    const updated = projectA({
      revision: 4,
      prompt_revision: null,
      confirmed_revision: null,
      brief_status: 'draft',
      brief: sampleBrief({ product_description: '保存后的商品描述' }),
    })
    harness = await mountProjectA([
      { method: 'PATCH', url: briefPathA, responses: [{ body: updated }] },
    ])

    let event = beforeUnloadEvent()
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)

    const composer = screen.getByLabelText('描述商品与图片需求')
    await harness.user.type(composer, '临时草稿')
    event = beforeUnloadEvent()
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)

    await harness.user.clear(composer)
    event = beforeUnloadEvent()
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)

    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const product = screen.getByLabelText('商品描述')
    await harness.user.clear(product)
    await harness.user.type(product, '保存后的商品描述')
    event = beforeUnloadEvent()
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)

    await harness.user.click(screen.getByRole('button', { name: '保存需求' }))
    expect(await screen.findByText('需求已保存。旧提示词已过期，请重新整理需求后再确认。')).toBeVisible()
    event = beforeUnloadEvent()
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
  })

  it('离开项目后迟到的消息响应不会覆盖新工作台，返回原项目只读取详情', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: `/?project=${PROJECT_A}`,
      routes: [
        {
          method: 'GET',
          url: detailPathA,
          responses: [{ body: detailResponse(projectA()) }],
        },
        {
          method: 'POST',
          url: messagesPathA,
          responses: [{
            body: messageResponse(projectA({ revision: 4 })),
            delayMs: 250,
          }],
        },
      ],
    })
    await screen.findByRole('heading', { name: '春季托特包主图' })
    const composer = screen.getByLabelText('描述商品与图片需求')
    await harness.user.type(composer, '发送后立刻离开')

    await harness.user.click(screen.getByRole('button', { name: '发送' }))
    await harness.user.click(screen.getByRole('button', { name: '新建项目' }))
    await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    await harness.user.click(screen.getByRole('button', { name: '放弃修改' }))

    await harness.waitFor(() => expect(harness!.fetch.count('POST', messagesPathA)).toBe(1))
    await new Promise((resolve) => setTimeout(resolve, 320))
    expect(screen.queryByRole('heading', { name: '春季托特包主图' })).toBeNull()
    expect(screen.queryByText('迟到的助手回复')).toBeNull()
    expect(window.location.search).toBe('')

    window.history.pushState({}, '', `/?project=${PROJECT_A}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(await screen.findByRole('heading', { name: '春季托特包主图' })).toBeVisible()
    expect(screen.queryByText('迟到的助手回复')).toBeNull()
    expect(harness.fetch.count('GET', detailPathA)).toBe(2)
    expect(harness.fetch.count('POST', messagesPathA)).toBe(1)
  })

  it('离开项目后迟到的保存响应不会覆盖新工作台，也不自动重发', async () => {
    const initial = projectA()
    const updated = projectA({
      revision: 4,
      prompt_revision: null,
      confirmed_revision: null,
      brief_status: 'draft',
      brief: sampleBrief({ product_description: '迟到的保存结果' }),
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: `/?project=${PROJECT_A}`,
      routes: [
        { method: 'GET', url: detailPathA, responses: [{ body: detailResponse(initial) }] },
        { method: 'PATCH', url: briefPathA, responses: [{ body: updated, delayMs: 250 }] },
      ],
    })
    await screen.findByRole('heading', { name: '春季托特包主图' })
    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const product = screen.getByLabelText('商品描述')
    await harness.user.clear(product)
    await harness.user.type(product, '离开前保存的内容')

    await harness.user.click(screen.getByRole('button', { name: '保存需求' }))
    await harness.user.click(screen.getByRole('button', { name: '新建项目' }))
    await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    await harness.user.click(screen.getByRole('button', { name: '放弃修改' }))

    await harness.waitFor(() => expect(harness!.fetch.count('PATCH', briefPathA)).toBe(1))
    await new Promise((resolve) => setTimeout(resolve, 320))
    expect(screen.queryByRole('heading', { name: '春季托特包主图' })).toBeNull()

    window.history.pushState({}, '', `/?project=${PROJECT_A}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
    await screen.findByRole('heading', { name: '春季托特包主图' })
    expect(within(currentCard()).getByText('米色亚麻托特包')).toBeVisible()
    expect(harness.fetch.count('PATCH', briefPathA)).toBe(1)
    expect(harness.fetch.count('GET', detailPathA)).toBe(2)
  })

  it('版本冲突时保留可复制的本地副本，不自动重试，加载最新需求后替换', async () => {
    const initial = projectA()
    const latest = projectA({
      revision: 6,
      prompt_revision: 6,
      confirmed_revision: null,
      brief_status: 'review',
      brief: sampleBrief({ product_description: '其他端已更新的商品', prompt: '其他端的最新提示词。' }),
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: `/?project=${PROJECT_A}`,
      routes: [
        {
          method: 'GET',
          url: detailPathA,
          responses: [{ body: detailResponse(initial) }, { body: detailResponse(latest) }],
        },
        {
          method: 'PATCH',
          url: briefPathA,
          responses: [{
            status: 409,
            body: { error: { code: 'REVISION_CONFLICT', message: '版本冲突。' } },
          }],
        },
      ],
    })
    await screen.findByRole('heading', { name: '春季托特包主图' })
    await harness.user.click(screen.getByRole('button', { name: '编辑需求' }))
    const product = screen.getByLabelText('商品描述')
    await harness.user.clear(product)
    await harness.user.type(product, '冲突时保留的本地内容')

    await harness.user.click(screen.getByRole('button', { name: '保存需求' }))
    expect(await screen.findByText(/需求已在别处更新，本地修改仍保留/)).toBeVisible()
    expect(harness.fetch.count('PATCH', briefPathA)).toBe(1)

    await harness.user.click(screen.getByRole('button', { name: '只读查看本地副本' }))
    expect(await screen.findByText('正在查看未保存的本地副本（只读），可复制内容或加载最新需求。')).toBeVisible()
    const localCopy = screen.getByLabelText('商品描述')
    expect(localCopy).toHaveValue('冲突时保留的本地内容')
    expect(localCopy).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: '保存需求' })).toBeNull()

    await harness.user.click(screen.getByRole('button', { name: '加载最新需求' }))
    expect(await screen.findByText('已加载最新需求。', { selector: '.status-notice' })).toBeVisible()
    expect(within(currentCard()).getByText('其他端已更新的商品')).toBeVisible()
    expect(screen.queryByLabelText('商品描述')).toBeNull()
    expect(harness.fetch.count('GET', detailPathA)).toBe(2)
    expect(harness.fetch.count('PATCH', briefPathA)).toBe(1)
  })

  it('导航确认支持初始焦点、Tab 循环、Escape 关闭并恢复触发按钮焦点', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    await harness.user.type(screen.getByLabelText('描述商品与图片需求'), '键盘确认测试')
    const trigger = screen.getByRole('button', { name: '新建项目' })
    trigger.focus()
    await harness.user.click(trigger)

    const dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    const cancel = within(dialog).getByRole('button', { name: '继续编辑' })
    const confirm = within(dialog).getByRole('button', { name: '放弃修改' })
    expect(cancel).toHaveFocus()

    await harness.user.tab({ shift: true })
    expect(confirm).toHaveFocus()
    await harness.user.keyboard('{Escape}')

    expect(screen.queryByRole('alertdialog')).toBeNull()
    await harness.waitFor(() => expect(trigger).toHaveFocus())
    expect(screen.getByLabelText('描述商品与图片需求')).toHaveValue('键盘确认测试')
  })

  it('项目切换界面不提供取消生成的入口，生成状态仍由服务器记录', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: `/?project=${PROJECT_A}`,
      routes: [
        {
          method: 'GET',
          url: detailPathA,
          responses: [{
            body: detailResponse(projectA(), [], [sampleGeneration({ status: 'running', stage: 'waiting_remote' })]),
          }],
        },
      ],
    })
    await screen.findByRole('heading', { name: '春季托特包主图' })

    expect(screen.queryByRole('button', { name: /取消生成/ })).toBeNull()
    expect(screen.queryByText(/取消生成/)).toBeNull()
  })
})
