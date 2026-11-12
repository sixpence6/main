import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/vue'
import { sessionRevision } from '@/auth/session'
import type { ProjectDetailResponse, ProjectSummary, ProjectView } from '@/api/types'
import type { WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  PROJECT_B,
  PROJECT_C,
  emptyConfig,
  sampleProject,
  sampleSummary,
} from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
  window.localStorage.clear()
  window.sessionStorage.clear()
})

const DETAIL_A = '/api/projects/' + PROJECT_A
const DETAIL_B = '/api/projects/' + PROJECT_B
const WORKFLOW_A = DETAIL_A + '/workflow'
const WORKFLOW_B = DETAIL_B + '/workflow'

function workflow(projectId: string): WorkflowView {
  return {
    project_id: projectId,
    run_id: 'run-123',
    stage: 'S03',
    status: 'active',
    project_revision: 3,
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
  }
}

function project(id: string, title: string): ProjectView {
  return sampleProject({ id, title, revision: 3 })
}

function detail(id: string, title: string): ProjectDetailResponse {
  return { project: project(id, title), messages: [], generations: [] }
}

function summary(id: string, title: string): ProjectSummary {
  return sampleSummary({ id, title })
}

function health(fetch: { route: (method: string, path: string, handler: () => { body: unknown }) => unknown }): void {
  fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
}

async function openProjectSearch(): Promise<HTMLInputElement> {
  return (await screen.findByRole('searchbox', { name: '按对话标题搜索' })) as HTMLInputElement
}

async function searchFor(query: string): Promise<void> {
  const input = await openProjectSearch()
  await harness!.user.clear(input)
  await harness!.user.type(input, query + '{Enter}')
}

describe('T11 侧栏真实会话列表、搜索与重命名', () => {
  it('侧栏直接展示服务端会话，搜索在分页前覆盖全部本人标题，清空恢复列表且不写业务', async () => {
    const queries: Array<{ q: string | null; limit: string | null }> = []
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/projects', (request) => {
          const params = new URLSearchParams(request.search)
          const q = params.get('q')
          queries.push({ q, limit: params.get('limit') })
          if (q === '后页项目') {
            return { body: { items: [summary(PROJECT_C, '后页项目')], next_cursor: null } }
          }
          if (q === '不存在') return { body: { items: [], next_cursor: null } }
          return {
            body: { items: [summary(PROJECT_A, '项目甲'), summary(PROJECT_B, '项目乙')], next_cursor: 'page-2' },
          }
        })
      },
    })

    expect(await screen.findByRole('button', { name: /项目甲/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /项目乙/ })).toBeVisible()
    expect(screen.getByRole('list', { name: '对话列表' })).toBeVisible()
    expect(queries[0]).toEqual({ q: null, limit: '20' })

    await searchFor('后页项目')
    expect(await screen.findByRole('button', { name: /后页项目/ })).toBeVisible()
    expect(screen.queryByRole('button', { name: /项目甲/ })).not.toBeInTheDocument()
    expect(queries.at(-1)).toEqual({ q: '后页项目', limit: '20' })

    await searchFor('不存在')
    expect(await screen.findByText('没有标题匹配“不存在”的对话')).toBeVisible()

    await harness.user.click(screen.getByRole('button', { name: '清空搜索' }))
    expect(await screen.findByRole('button', { name: /项目甲/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /项目乙/ })).toBeVisible()
    expect(queries.at(-1)).toEqual({ q: null, limit: '20' })
    expect(harness.fetch.writes).toEqual([])
  })

  it('加载更多按游标请求并去重，旧搜索晚到不能覆盖当前搜索', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/projects', (request) => {
          const params = new URLSearchParams(request.search)
          const q = params.get('q')
          const cursor = params.get('cursor')
          if (q === '旧查询') {
            return { delayMs: 140, body: { items: [summary(PROJECT_A, '旧查询结果')], next_cursor: null } }
          }
          if (q === '当前查询') {
            return { delayMs: 10, body: { items: [summary(PROJECT_C, '当前查询结果')], next_cursor: null } }
          }
          if (cursor === 'page-2') {
            return {
              body: {
                items: [summary(PROJECT_A, '项目甲'), summary(PROJECT_C, '项目丙')],
                next_cursor: null,
              },
            }
          }
          return {
            body: { items: [summary(PROJECT_A, '项目甲'), summary(PROJECT_B, '项目乙')], next_cursor: 'page-2' },
          }
        })
      },
    })

    expect(await screen.findByRole('button', { name: /项目甲/ })).toBeVisible()
    await harness.user.click(screen.getByRole('button', { name: '加载更多' }))
    expect(await screen.findByRole('button', { name: /项目丙/ })).toBeVisible()
    const list = screen.getByRole('list', { name: '对话列表' })
    expect(harness.within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(screen.getAllByRole('button', { name: /项目甲/ })).toHaveLength(1)

    await searchFor('旧查询')
    await searchFor('当前查询')
    expect(await screen.findByRole('button', { name: /当前查询结果/ })).toBeVisible()
    await new Promise((resolve) => setTimeout(resolve, 180))
    expect(screen.queryByRole('button', { name: /旧查询结果/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /项目甲/ })).not.toBeInTheDocument()
    expect(harness.fetch.writes).toEqual([])
  })

  it('重命名只发送标题元数据并同时更新侧栏和当前对话，不改变需求版本', async () => {
    let detailCalls = 0
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/projects', () => ({
          body: { items: [summary(PROJECT_A, '旧标题')], next_cursor: null },
        }))
        fetch.route('GET', DETAIL_A, () => {
          detailCalls += 1
          return { body: detail(PROJECT_A, '旧标题') }
        })
        fetch.route('GET', WORKFLOW_A, () => ({ body: workflow(PROJECT_A) }))
        fetch.route('PATCH', DETAIL_A, (request) => {
          const title = (request.body as { title: string }).title
          return { body: sampleProject({ id: PROJECT_A, title, revision: 3 }) }
        })
      },
    })

    expect(await screen.findByRole('heading', { name: '旧标题' })).toBeVisible()
    await harness.user.click(screen.getByRole('button', { name: '重命名对话' }))
    const titleInput = screen.getByRole('textbox', { name: '对话标题' })
    await harness.user.clear(titleInput)
    await harness.user.type(titleInput, '新标题')
    await harness.user.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByRole('heading', { name: '新标题' })).toBeVisible()
    expect(screen.getByRole('button', { name: /新标题/ })).toBeVisible()
    expect(harness.fetch.count('PATCH', DETAIL_A)).toBe(1)
    expect(harness.fetch.last('PATCH', DETAIL_A)?.body).toEqual({ title: '新标题' })
    expect(detailCalls).toBe(1)
    expect(harness.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
    expect((harness.fetch.writes[0]?.body as { title: string; revision?: number }).revision).toBeUndefined()
  })

  it('重命名失败保留原名并显示安全错误', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/projects', () => ({
          body: { items: [summary(PROJECT_A, '旧标题')], next_cursor: null },
        }))
        fetch.route('GET', DETAIL_A, () => ({ body: detail(PROJECT_A, '旧标题') }))
        fetch.route('GET', WORKFLOW_A, () => ({ body: workflow(PROJECT_A) }))
        fetch.route('PATCH', DETAIL_A, () => ({
          status: 500,
          body: { error: { code: 'INTERNAL', message: '标题保存失败，请重试' } },
        }))
      },
    })

    expect(await screen.findByRole('heading', { name: '旧标题' })).toBeVisible()
    await harness.user.click(screen.getByRole('button', { name: '重命名对话' }))
    const titleInput = screen.getByRole('textbox', { name: '对话标题' })
    await harness.user.clear(titleInput)
    await harness.user.type(titleInput, '新标题')
    await harness.user.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('标题保存失败，请重试')
    expect(screen.getByRole('heading', { name: '旧标题' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: '对话标题' })).toBeVisible()
  })

  it('直接链接恢复真实会话并标出当前项，切换前有未发送草稿会先确认', async () => {
    const detailCalls: string[] = []
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/projects/' + PROJECT_A + '/chat',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/projects', () => ({
          body: {
            items: [summary(PROJECT_A, '项目甲'), summary(PROJECT_B, '项目乙')],
            next_cursor: null,
          },
        }))
        fetch.route('GET', /^\/api\/projects\/(?:11111111-1111-4111-8111-111111111111|22222222-2222-4222-8222-222222222222)$/, (request) => {
          const id = request.path.endsWith(PROJECT_B) ? PROJECT_B : PROJECT_A
          detailCalls.push(id)
          return { body: detail(id, id === PROJECT_A ? '项目甲' : '项目乙') }
        })
        fetch.route('GET', /\/workflow$/, (request) => {
          const id = request.path.includes(PROJECT_B) ? PROJECT_B : PROJECT_A
          return { body: workflow(id) }
        })
      },
    })

    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()
    expect(screen.getByRole('button', { name: /项目甲/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('button', { name: /项目乙/ })).not.toHaveAttribute('aria-current')

    const composer = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(composer, '切换前还没发送的草稿')
    await harness.user.click(screen.getByRole('button', { name: /项目乙/ }))

    const dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    expect(dialog).toHaveTextContent('输入框中尚未发送的文字')
    expect(screen.getByRole('heading', { name: '项目甲' })).toBeVisible()
    expect(detailCalls).toEqual([PROJECT_A])

    await harness.user.click(screen.getByRole('button', { name: '放弃修改' }))
    expect(await screen.findByRole('heading', { name: '项目乙' })).toBeVisible()
    expect(harness.router.currentRoute.value.name).toBe('project-chat')
    expect(harness.router.currentRoute.value.params.projectId).toBe(PROJECT_B)
    expect(composer).toHaveValue('')
    expect(screen.getByRole('button', { name: /项目乙/ })).toHaveAttribute('aria-current', 'page')
    expect(harness.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })

  it('登录生命周期变化后，晚到的重命名结果不再写入侧栏', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/',
      setup(fetch) {
        health(fetch)
        fetch.route('GET', '/api/projects', () => ({
          body: { items: [summary(PROJECT_A, '旧标题')], next_cursor: null },
        }))
        fetch.route('PATCH', DETAIL_A, async () => {
          await new Promise((resolve) => setTimeout(resolve, 120))
          return { body: sampleProject({ id: PROJECT_A, title: '迟到的新标题', revision: 3 }) }
        })
      },
    })

    expect(await screen.findByRole('button', { name: /旧标题/ })).toBeVisible()
    await harness.user.click(screen.getByRole('button', { name: '重命名对话' }))
    const titleInput = screen.getByRole('textbox', { name: '对话标题' })
    await harness.user.clear(titleInput)
    await harness.user.type(titleInput, '迟到的新标题')
    await harness.user.click(screen.getByRole('button', { name: '保存' }))
    sessionRevision.value += 1
    await new Promise((resolve) => setTimeout(resolve, 180))

    expect(screen.queryByRole('button', { name: /迟到的新标题/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /旧标题/ })).not.toBeInTheDocument()
    expect(screen.getByText('暂无对话。新建对话后会显示在这里。')).toBeVisible()
  })
})
