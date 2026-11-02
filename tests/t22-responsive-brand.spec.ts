import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/vue'
import type { ProjectDetailResponse, ProjectView } from '@/api/types'
import type { WorkflowView } from '@/api/workflow'
import { clearSession } from '@/auth/session'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleMessage,
  sampleProject,
  sampleSummary,
} from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  clearSession()
  harness?.dispose()
  harness = undefined
})

function project(): ProjectView {
  return sampleProject({ id: PROJECT_A, title: '春季托特包主图' })
}

function detail(message = false): ProjectDetailResponse {
  return {
    project: project(),
    messages: message
      ? [
          sampleMessage({ id: 'user-1', role: 'user', content: '米色亚麻托特包，简洁主图' }),
          sampleMessage({ id: 'assistant-1', role: 'assistant', content: '已整理为需求卡，请确认。' }),
        ]
      : [],
    generations: [],
  }
}

function workflowView(): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: null,
    stage: null,
    status: 'none',
    project_revision: 3,
    requirement_revision: 3,
    intent_route: null,
    intent_action: null,
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: null,
    versions: [],
    updated_at: null,
  }
}

async function mountAt(width: number, messages = false, extraRoutes: NonNullable<Parameters<typeof mountWorkspace>[0]>['routes'] = []) {
  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: width,
    url: '/?project=' + PROJECT_A,
    routes: [
      { method: 'GET', url: '/api/projects', responses: [{ body: { items: [sampleSummary({ id: PROJECT_A, title: '春季托特包主图' })], next_cursor: null } }] },
      { method: 'GET', url: '/api/projects/' + PROJECT_A, responses: [{ body: detail(messages) }] },
      { method: 'GET', url: '/api/projects/' + PROJECT_A + '/workflow', responses: [{ body: workflowView() }] },
      ...extraRoutes,
    ],
  })
  await screen.findByRole('heading', { name: '春季托特包主图' })
  return harness
}

describe('T22 响应式单主体与品牌呈现', () => {
  it('产品名使用工作台品牌，助手消息显示智能生图助手签名', async () => {
    await mountAt(1440, true)
    expect(document.querySelector('.app-header')).toHaveTextContent('电商生图工作台')
    const assistant = screen.getByLabelText('助手的消息')
    expect(assistant).toHaveTextContent('智能生图助手')
    expect(assistant).toHaveTextContent('已整理为需求卡，请确认。')
    expect(assistant.closest('.message-item')).toHaveClass('is-assistant')
    const user = screen.getByLabelText('用户的消息')
    expect(user.closest('.message-item')).toHaveClass('is-user')
  })

  it('输入区是对话主体底部的单一可见输入，不在对话卡片内且没有独立反馈表单', async () => {
    await mountAt(1440)
    const region = screen.getByRole('region', { name: '消息输入区' })
    const panel = document.querySelector('.conversation-panel')
    const scroll = document.querySelector('.chat-scroll')
    const page = document.querySelector('.chat-page')
    expect(panel).not.toBeNull()
    expect(scroll).not.toBeNull()
    expect(page).not.toBeNull()
    expect(panel!.contains(region)).toBe(false)
    expect(scroll!.contains(region)).toBe(false)
    expect(page!.contains(region)).toBe(true)
    expect(screen.getByText('描述商品与图片需求', { selector: '.composer-label' })).toBeVisible()
    expect(screen.getByText('支持文字描述与图片参考')).toBeVisible()
    expect(screen.getAllByRole('textbox', { name: '描述商品与图片需求' })).toHaveLength(1)
    expect(document.querySelector('.workflow-feedback')).toBeNull()
  })

  it('768 至 1023 使用抽屉，1024 起显示桌面侧栏，窄屏只有一个菜单入口', async () => {
    await mountAt(1023)
    expect(screen.queryByRole('button', { name: '收起侧栏' })).toBeNull()
    expect(screen.getByRole('button', { name: '打开侧栏' })).toBeVisible()

    await harness!.setViewportWidth(768)
    expect(screen.getByRole('button', { name: '打开侧栏' })).toBeVisible()
    expect(document.querySelectorAll('.app-header button, .mobile-topbar button')).toHaveLength(1)

    await harness!.setViewportWidth(1024)
    expect(screen.getByRole('button', { name: '收起侧栏' })).toBeVisible()
    expect(screen.queryByRole('button', { name: '打开侧栏' })).toBeNull()

    await harness!.setViewportWidth(320)
    expect(screen.getByRole('button', { name: '打开侧栏' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toBeVisible()
  })
})
