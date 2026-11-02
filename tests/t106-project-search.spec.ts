import { afterEach, describe, expect, it } from 'vitest'
import { within } from '@testing-library/vue'
import { clearSession, sessionRevision } from '@/auth/session'
import type { ProjectDetailResponse } from '@/api/types'
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
})

function detailResponse(project: ProjectDetailResponse['project']): ProjectDetailResponse {
  return { project, messages: [], generations: [] }
}

async function openDrawer(h: WorkspaceHarness) {
  await h.user.click(h.screen.getByRole('button', { name: '已有项目' }))
  return h.screen.findByRole('dialog', { name: '已有项目' })
}

describe('T20 owner project search and rename', () => {
  it('search runs on the server before pagination and exposes empty and cleared states', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    const queries: string[] = []
    harness.fetch.route('GET', '/api/projects', (request) => {
      const params = new URLSearchParams(request.search)
      const q = params.get('q') ?? ''
      queries.push(q)
      if (q === '后页项目') {
        return { body: { items: [sampleSummary({ id: PROJECT_C, title: '后页项目' })], next_cursor: null } }
      }
      if (q === '不存在') return { body: { items: [], next_cursor: null } }
      return {
        body: {
          items: [
            sampleSummary({ id: PROJECT_A, title: '项目甲' }),
            sampleSummary({ id: PROJECT_B, title: '项目乙' }),
          ],
          next_cursor: 'page-2',
        },
      }
    })

    let dialog = await openDrawer(harness)
    await within(dialog).findByText('项目甲')
    const search = within(dialog).getByRole('searchbox', { name: '按项目标题搜索' })
    await harness.user.type(search, '后页项目')
    await harness.user.keyboard('{Enter}')

    await within(dialog).findByText('后页项目')
    expect(within(dialog).queryByText('项目甲')).not.toBeInTheDocument()
    const request = harness.fetch.last('GET', '/api/projects')
    const params = new URLSearchParams(request?.search ?? '')
    expect(params.get('q')).toBe('后页项目')
    expect(params.get('limit')).toBe('20')
    expect(queries).toContain('后页项目')

    await harness.user.clear(search)
    await harness.user.type(search, '不存在')
    await harness.user.keyboard('{Enter}')
    expect(await within(dialog).findByText(/没有标题匹配“不存在”的项目/)).toBeVisible()

    await harness.user.click(within(dialog).getByRole('button', { name: '清空' }))
    await within(dialog).findByText('项目甲')
    expect(new URLSearchParams(harness.fetch.last('GET', '/api/projects')?.search ?? '').get('q')).toBeNull()
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('late search and load-more responses cannot cross the current query', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    harness.fetch.route('GET', '/api/projects', (request) => {
      const params = new URLSearchParams(request.search)
      const q = params.get('q') ?? ''
      const cursor = params.get('cursor')
      if (q === '旧查询') {
        return { body: { items: [sampleSummary({ id: PROJECT_C, title: '旧查询结果' })], next_cursor: null }, delayMs: 140 }
      }
      if (q === '当前查询') {
        if (cursor === 'current-next') {
          return { body: { items: [sampleSummary({ id: PROJECT_B, title: '当前后页' })], next_cursor: null } }
        }
        return { body: { items: [sampleSummary({ id: PROJECT_A, title: '当前项目' })], next_cursor: 'current-next' }, delayMs: 10 }
      }
      return { body: { items: [sampleSummary({ id: PROJECT_C, title: '初始项目' })], next_cursor: null } }
    })

    const dialog = await openDrawer(harness)
    await within(dialog).findByText('初始项目')
    const search = within(dialog).getByRole('searchbox', { name: '按项目标题搜索' })
    await harness.user.type(search, '旧查询')
    await harness.user.keyboard('{Enter}')
    await harness.user.clear(search)
    await harness.user.type(search, '当前查询')
    await harness.user.keyboard('{Enter}')

    await within(dialog).findByText('当前项目')
    await new Promise((resolve) => setTimeout(resolve, 180))
    expect(within(dialog).queryByText('旧查询结果')).not.toBeInTheDocument()
    expect(within(dialog).getByText('当前项目')).toBeVisible()

    await harness.user.click(within(dialog).getByRole('button', { name: '加载更多' }))
    await within(dialog).findByText('当前后页')
    expect(within(dialog).queryByText('旧查询结果')).not.toBeInTheDocument()
    const last = harness.fetch.last('GET', '/api/projects')
    expect(new URLSearchParams(last?.search ?? '').get('q')).toBe('当前查询')
    expect(new URLSearchParams(last?.search ?? '').get('cursor')).toBe('current-next')
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('explicit rename PATCHes metadata, updates the current workspace and survives reopen', async () => {
    let savedTitle = '原项目'
    const detailPath = '/api/projects/' + PROJECT_A
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      setup: (fetch) => {
        fetch.route('GET', '/api/projects', () => ({
          body: { items: [sampleSummary({ id: PROJECT_A, title: savedTitle })], next_cursor: null },
        }))
        fetch.route('GET', detailPath, () => ({
          body: detailResponse(sampleProject({ id: PROJECT_A, title: savedTitle })),
        }))
        fetch.route('PATCH', detailPath, (request) => {
          const body = (request.body ?? {}) as { title?: string }
          savedTitle = body.title ?? savedTitle
          return { body: sampleProject({ id: PROJECT_A, title: savedTitle }) }
        })
      },
    })
    expect(await harness.screen.findByRole('heading', { name: '原项目' })).toBeVisible()
    let dialog = await openDrawer(harness)
    await harness.user.click(within(dialog).getByRole('button', { name: '重命名项目' }))
    const input = within(dialog).getByRole('textbox', { name: '项目标题' })
    await harness.user.clear(input)
    await harness.user.type(input, '春季主图改名')
    await harness.user.click(within(dialog).getByRole('button', { name: '保存' }))

    expect(await harness.screen.findByRole('heading', { name: '春季主图改名' })).toBeVisible()
    expect(within(dialog).getByText('春季主图改名')).toBeVisible()
    const patch = harness.fetch.last('PATCH', detailPath)
    expect(patch?.body).toEqual({ title: '春季主图改名' })
    expect(harness.fetch.writes).toHaveLength(1)

    await harness.user.click(within(dialog).getByRole('button', { name: '关闭项目列表' }))
    dialog = await openDrawer(harness)
    expect(await within(dialog).findByText('春季主图改名')).toBeVisible()
    expect(within(dialog).queryByText('原项目')).not.toBeInTheDocument()
  })

  it('rename failure shows the safe error and never claims the old title was saved', async () => {
    const detailPath = '/api/projects/' + PROJECT_A
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      setup: (fetch) => {
        fetch.route('GET', '/api/projects', () => ({
          body: { items: [sampleSummary({ id: PROJECT_A, title: '不能改名的项目' })], next_cursor: null },
        }))
        fetch.route('GET', detailPath, () => ({
          body: detailResponse(sampleProject({ id: PROJECT_A, title: '不能改名的项目' })),
        }))
        fetch.route('PATCH', detailPath, () => ({
          status: 500,
          body: { error: { code: 'INTERNAL', message: '标题保存失败，请重试。' } },
        }))
      },
    })
    expect(await harness.screen.findByRole('heading', { name: '不能改名的项目' })).toBeVisible()
    const dialog = await openDrawer(harness)
    await harness.user.click(within(dialog).getByRole('button', { name: '重命名项目' }))
    const input = within(dialog).getByRole('textbox', { name: '项目标题' })
    await harness.user.clear(input)
    await harness.user.type(input, '不应显示的标题')
    await harness.user.click(within(dialog).getByRole('button', { name: '保存' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('标题保存失败，请重试。')
    expect(harness.screen.getByRole('heading', { name: '不能改名的项目' })).toBeVisible()
    expect(within(dialog).getByRole('button', { name: '保存' })).toBeVisible()
    expect(harness.screen.queryByText('项目标题已更新。')).not.toBeInTheDocument()
  })

  it('a rename response arriving after account switch is ignored', async () => {
    const detailPath = '/api/projects/' + PROJECT_A
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      setup: (fetch) => {
        fetch.route('GET', '/api/projects', () => ({
          body: { items: [sampleSummary({ id: PROJECT_A, title: '账号甲项目' })], next_cursor: null },
        }))
        fetch.route('GET', detailPath, () => ({
          body: detailResponse(sampleProject({ id: PROJECT_A, title: '账号甲项目' })),
        }))
        fetch.route('PATCH', detailPath, () => ({
          body: sampleProject({ id: PROJECT_A, title: '晚到标题' }),
          delayMs: 150,
        }))
      },
    })
    expect(await harness.screen.findByRole('heading', { name: '账号甲项目' })).toBeVisible()
    const dialog = await openDrawer(harness)
    await harness.user.click(within(dialog).getByRole('button', { name: '重命名项目' }))
    const input = within(dialog).getByRole('textbox', { name: '项目标题' })
    await harness.user.clear(input)
    await harness.user.type(input, '晚到标题')
    await harness.user.click(within(dialog).getByRole('button', { name: '保存' }))
    clearSession()
    sessionRevision.value += 1

    await new Promise((resolve) => setTimeout(resolve, 250))
    expect(harness.screen.queryByText('晚到标题')).not.toBeInTheDocument()
    expect(harness.fetch.count('PATCH', detailPath)).toBe(1)
    expect(harness.router.currentRoute.value.name).toBe('describe')
  })
})
