import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  PROJECT_B,
  PROJECT_C,
  emptyConfig,
  sampleAsset,
  sampleBrief,
  sampleGeneration,
  sampleMessage,
  sampleProject,
  sampleSummary,
} from './support/fixtures'
import type { ProjectDetailResponse } from '@/api/types'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = /^\/api\/projects\/[^/]+$/

function detailResponse(
  project: ProjectDetailResponse['project'],
  messages: ProjectDetailResponse['messages'] = [],
  generations: ProjectDetailResponse['generations'] = [],
): ProjectDetailResponse {
  return { project, messages, generations }
}

async function openProjectDrawer(h: WorkspaceHarness) {
  await h.user.click(screen.getByRole('button', { name: '已有项目' }))
  return screen.findByRole('dialog', { name: '已有项目' })
}

describe('T02 已有项目列表', () => {
  it('打开抽屉才按 limit=20 读取，显示标题兜底、本地时间与真实状态徽标', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    harness.fetch.json('GET', '/api/projects', {
      items: [
        sampleSummary({ id: PROJECT_A, title: '   ', brief_status: 'review' }),
        sampleSummary({
          id: PROJECT_B,
          title: '陶瓷马克杯',
          brief_status: 'draft',
          latest_generation_status: 'succeeded',
          updated_at: '2026-10-19T01:05:00.000Z',
        }),
      ],
      next_cursor: null,
    })

    expect(harness.fetch.count('GET', '/api/projects')).toBe(0)
    const dialog = await openProjectDrawer(harness)

    const itemA = await within(dialog).findByRole('button', { name: /未命名项目/ })
    const itemB = within(dialog).getByRole('button', { name: /陶瓷马克杯/ })
    expect(within(itemA).getByText('待确认')).toBeVisible()
    expect(within(itemB).getByText('草稿')).toBeVisible()
    expect(within(itemB).getByText('生成：成功')).toBeVisible()

    const time = itemA.querySelector('time')
    expect(time).toHaveAttribute('datetime', '2026-10-21T06:30:00.000Z')
    expect(time?.textContent ?? '').toMatch(/\d/)
    expect(time?.textContent ?? '').not.toBe('时间未知')

    const list = within(dialog).getByRole('list', { name: '项目列表' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('未命名项目')
    expect(harness.fetch.last('GET', '/api/projects')?.search).toBe('?limit=20')
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('加载更多使用服务端 cursor、按 ID 去重并防重入', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    const cursors: Array<string | null> = []
    harness.fetch.route('GET', '/api/projects', (request) => {
      const cursor = new URLSearchParams(request.search).get('cursor')
      cursors.push(cursor)
      if (!cursor) {
        return {
          body: {
            items: [
              sampleSummary({ id: PROJECT_A, title: '项目甲' }),
              sampleSummary({ id: PROJECT_B, title: '项目乙' }),
            ],
            next_cursor: 'cursor-2',
          },
        }
      }
      return {
        body: {
          items: [
            sampleSummary({ id: PROJECT_B, title: '项目乙' }),
            sampleSummary({ id: PROJECT_C, title: '项目丙' }),
          ],
          next_cursor: null,
        },
        delayMs: 60,
      }
    })

    const dialog = await openProjectDrawer(harness)
    await within(dialog).findByText('项目甲')
    const more = within(dialog).getByRole('button', { name: '加载更多' })
    await harness.user.click(more)

    expect(more).toBeDisabled()
    await harness.user.click(more)
    await within(dialog).findByText('项目丙')

    const list = within(dialog).getByRole('list', { name: '项目列表' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(within(dialog).getAllByText('项目乙')).toHaveLength(1)
    expect(cursors).toEqual([null, 'cursor-2'])
    expect(harness.fetch.count('GET', '/api/projects')).toBe(2)
    expect(within(dialog).queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument()
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('后页失败保留已有列表，重试复用同一 cursor', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    const cursors: Array<string | null> = []
    let pageAttempts = 0
    harness.fetch.route('GET', '/api/projects', (request) => {
      const cursor = new URLSearchParams(request.search).get('cursor')
      cursors.push(cursor)
      if (!cursor) {
        return {
          body: { items: [sampleSummary({ id: PROJECT_A, title: '保留的项目' })], next_cursor: 'next-page' },
        }
      }
      pageAttempts += 1
      if (pageAttempts === 1) {
        return { status: 500, body: { error: { code: 'INTERNAL', message: '暂时无法读取更多项目。' } } }
      }
      return { body: { items: [sampleSummary({ id: PROJECT_C, title: '后页项目' })], next_cursor: null } }
    })

    const dialog = await openProjectDrawer(harness)
    await within(dialog).findByText('保留的项目')
    await harness.user.click(within(dialog).getByRole('button', { name: '加载更多' }))

    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent('暂时无法读取更多项目。')
    expect(within(dialog).getByText('保留的项目')).toBeVisible()
    expect(within(dialog).queryByText('后页项目')).not.toBeInTheDocument()

    await harness.user.click(within(dialog).getByRole('button', { name: '重试' }))
    await within(dialog).findByText('后页项目')

    expect(cursors).toEqual([null, 'next-page', 'next-page'])
    expect(within(dialog).getAllByRole('listitem')).toHaveLength(2)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('空列表提供新建入口，首屏失败显示安全错误并可重试', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    harness.fetch.sequence('GET', '/api/projects', [
      { status: 503, body: { error: { code: 'BUSY', message: 'sk-secret Bearer stack trace' } } },
      { body: { items: [], next_cursor: null } },
    ])

    const dialog = await openProjectDrawer(harness)
    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent('所需模型尚未配置或当前不可用。')
    expect(alert).not.toHaveTextContent('sk-secret')

    await harness.user.click(within(dialog).getByRole('button', { name: '重试' }))
    await within(dialog).findByText('暂无项目。新建项目后会显示在这里。')
    expect(within(dialog).getAllByRole('button', { name: '新建项目' }).length).toBeGreaterThanOrEqual(2)

    await harness.user.click(within(dialog).getAllByRole('button', { name: '新建项目' })[0]!)
    await harness.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(window.location.search).toBe('')
    expect(harness.fetch.writes).toHaveLength(0)
  })
})

describe('T02 只读恢复项目详情', () => {
  it('选择项目后关闭抽屉、更新 URL，恢复只读对话、当前需求、快照与生成状态', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    harness.fetch.json('GET', '/api/projects', {
      items: [sampleSummary({ id: PROJECT_A, title: '春季托特包主图' })],
      next_cursor: null,
    })
    const snapshot = sampleBrief({
      product_description: '快照里的商品描述',
      prompt: '快照中的提示词',
    })
    const project = sampleProject({
      id: PROJECT_A,
      title: '春季托特包主图',
      brief_status: 'review',
      brief: sampleBrief({ prompt: '当前确认用的完整提示词' }),
    })
    harness.fetch.json(
      'GET',
      `/api/projects/${PROJECT_A}`,
      detailResponse(project, [
        sampleMessage({ id: 'm1', sequence_no: 1, role: 'user', content: '做一个米色托特包主图' }),
        sampleMessage({
          id: 'm2',
          sequence_no: 2,
          role: 'assistant',
          content: '已整理为可确认的需求。',
          brief_snapshot: snapshot,
        }),
        sampleMessage({ id: 'm3', sequence_no: 3, role: 'assistant', content: '这是普通补充说明。', brief_snapshot: null }),
      ], [
        sampleGeneration({
          id: 'g1',
          project_id: PROJECT_A,
          version_no: 1,
          status: 'succeeded',
          result_asset_id: 'asset-1',
          result_asset: sampleAsset({ id: 'asset-1', kind: 'generated' }),
        }),
      ]),
    )

    const dialog = await openProjectDrawer(harness)
    await harness.user.click(within(dialog).getByRole('button', { name: /春季托特包主图/ }))
    await harness.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    expect(window.location.search).toBe(`?project=${PROJECT_A}`)
    expect(await screen.findByRole('heading', { name: '查看结果' })).toBeVisible()
    expect(screen.getByRole('img', { name: '生成结果图' })).toBeVisible()
    const nav = screen.getByRole('navigation', { name: '工作阶段' })
    await harness.user.click(within(nav).getByRole('button', { name: '描述需求' }))
    expect(await screen.findByRole('heading', { name: '春季托特包主图' })).toBeVisible()
    expect(harness.fetch.last('GET', detailPath)?.path).toBe(`/api/projects/${PROJECT_A}`)

    const currentBrief = screen.getByRole('region', { name: '当前需求' })
    expect(within(currentBrief).getByText('米色亚麻托特包')).toBeVisible()
    expect(within(currentBrief).getByText('当前确认用的完整提示词')).toBeVisible()
    expect(within(currentBrief).getByText('1024x1024')).toBeVisible()

    expect(screen.getByText('做一个米色托特包主图')).toBeVisible()
    expect(screen.getByText('已整理为可确认的需求。')).toBeVisible()
    expect(screen.getByText('这是普通补充说明。')).toBeVisible()
    expect(screen.getAllByRole('region', { name: '当时的需求快照' })).toHaveLength(1)
    const snapshotCard = screen.getByRole('region', { name: '当时的需求快照' })
    expect(within(snapshotCard).getByText('快照里的商品描述')).toBeVisible()
    expect(within(snapshotCard).getByText('快照中的提示词')).toBeVisible()

    expect(screen.getByText('生成记录 1 条')).toBeVisible()
    expect(screen.getByText('最近版本：第 1 版，状态：成功')).toBeVisible()
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('POST', /\/messages$/)).toBe(0)
    expect(harness.fetch.count('POST', /\/generations$/)).toBe(0)

    const results = within(nav).getByRole('button', { name: '查看结果' })
    expect(results).not.toHaveAttribute('aria-disabled', 'true')
    await harness.user.click(results)
    expect(harness.fetch.writes).toHaveLength(0)

    const reopened = await openProjectDrawer(harness)
    const current = await within(reopened).findByRole('button', { name: /春季托特包主图/ })
    expect(current).toHaveAttribute('aria-current', 'true')
    await harness.user.keyboard('{Escape}')
  })

  it('直接链接恢复，前进后退按 URL 切换且无参数时回到空白工作台', async () => {
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      url: `/?project=${PROJECT_A}`,
      routes: [
        { method: 'GET', url: `/api/projects/${PROJECT_A}`, responses: [{ body: detailResponse(sampleProject({ id: PROJECT_A, title: '项目甲' })) }] },
        { method: 'GET', url: `/api/projects/${PROJECT_B}`, responses: [{ body: detailResponse(sampleProject({ id: PROJECT_B, title: '项目乙' })) }] },
      ],
    })

    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()
    expect(harness.fetch.count('GET', '/api/projects')).toBe(0)
    expect(harness.fetch.writes).toHaveLength(0)

    window.history.pushState({}, '', `/?project=${PROJECT_B}`)
    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(await screen.findByRole('heading', { name: '项目乙' })).toBeVisible()
    expect(window.location.search).toBe(`?project=${PROJECT_B}`)

    window.history.pushState({}, '', '/')
    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(await screen.findByText('从一个商品想法开始')).toBeVisible()
    expect(window.location.search).toBe('')
    expect(harness.fetch.count('GET', detailPath)).toBe(2)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('快速切换项目时旧详情晚到不会覆盖当前项目或 URL', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    harness.fetch.json('GET', '/api/projects', {
      items: [sampleSummary({ id: PROJECT_A, title: '慢项目' }), sampleSummary({ id: PROJECT_B, title: '快项目' })],
      next_cursor: null,
    })
    harness.fetch.route('GET', detailPath, (request) => {
      const id = request.path.split('/').pop()
      return {
        body: detailResponse(
          sampleProject({ id: id === PROJECT_A ? PROJECT_A : PROJECT_B, title: id === PROJECT_A ? '慢项目' : '快项目' }),
        ),
        delayMs: id === PROJECT_A ? 150 : 10,
      }
    })

    let dialog = await openProjectDrawer(harness)
    await harness.user.click(within(dialog).getByRole('button', { name: /慢项目/ }))
    dialog = await openProjectDrawer(harness)
    await harness.user.click(within(dialog).getByRole('button', { name: /快项目/ }))

    expect(await screen.findByRole('heading', { name: '快项目' })).toBeVisible()
    await new Promise((resolve) => setTimeout(resolve, 180))
    expect(screen.getByRole('heading', { name: '快项目' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: '慢项目' })).not.toBeInTheDocument()
    expect(window.location.search).toBe(`?project=${PROJECT_B}`)
    expect(harness.fetch.count('GET', detailPath)).toBe(2)
    expect(harness.fetch.writes).toHaveLength(0)
  })
})

describe('T02 项目读取错误与无障碍', () => {
  it('无效项目 ID 不发详情请求，404 与网络错误给出不同说明并保留入口', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), url: '/?project=not-a-uuid' })
    expect(await screen.findByText(/项目链接无效/)).toBeVisible()
    expect(harness.fetch.count('GET', detailPath)).toBe(0)
    harness.dispose()

    harness = await mountWorkspace({
      config: emptyConfig(),
      url: `/?project=${PROJECT_A}`,
      setup: (fetch) => {
        fetch.route('GET', `/api/projects/${PROJECT_A}`, () => ({
          status: 404,
          body: { error: { code: 'PROJECT_NOT_FOUND', message: '项目不存在或已被删除，请从项目列表重新选择。' } },
        }))
      },
    })
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText('项目不存在或已被删除，请从项目列表重新选择。')).toBeVisible()
    expect(within(alert).getByRole('button', { name: '打开已有项目' })).toBeVisible()
    expect(within(alert).getByRole('button', { name: '新建项目' })).toBeVisible()
    harness.dispose()

    harness = await mountWorkspace({
      config: emptyConfig(),
      url: `/?project=${PROJECT_A}`,
      setup: (fetch) => {
        fetch.sequence('GET', `/api/projects/${PROJECT_A}`, [
          { networkError: 'Failed to fetch' },
          { body: detailResponse(sampleProject({ id: PROJECT_A, title: '重试成功的项目' })) },
        ])
      },
    })
    expect(await screen.findByText(/无法连接本机服务/)).toBeVisible()
    await harness.user.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('heading', { name: '重试成功的项目' })).toBeVisible()
    expect(harness.fetch.count('GET', detailPath)).toBe(2)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('项目抽屉键盘打开、焦点限制并在 Esc 关闭后返回触发按钮', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    harness.fetch.json('GET', '/api/projects', { items: [], next_cursor: null })
    const trigger = screen.getByRole('button', { name: '已有项目' })
    trigger.focus()
    await harness.user.keyboard('{Enter}')

    const dialog = await screen.findByRole('dialog', { name: '已有项目' })
    const close = within(dialog).getByRole('button', { name: '关闭项目列表' })
    await harness.waitFor(() => expect(close).toHaveFocus())

    for (let i = 0; i < 4; i += 1) {
      await harness.user.tab()
      expect(dialog.contains(document.activeElement)).toBe(true)
    }

    await harness.user.keyboard('{Escape}')
    await harness.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('窄屏从侧栏进入项目列表时只保留一个抽屉', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 500 })
    harness.fetch.json('GET', '/api/projects', { items: [], next_cursor: null })

    await harness.user.click(screen.getByRole('button', { name: '打开侧栏' }))
    const side = await screen.findByRole('dialog', { name: '工作区菜单' })
    await harness.user.click(within(side).getByRole('button', { name: '已有项目' }))

    const dialogs = screen.getAllByRole('dialog')
    expect(dialogs).toHaveLength(1)
    expect(dialogs[0]).toHaveAccessibleName('已有项目')
    await within(dialogs[0]!).findByText('暂无项目。新建项目后会显示在这里。')
    expect(harness.fetch.count('GET', '/api/projects')).toBe(1)
    expect(harness.fetch.writes).toHaveLength(0)
  })
})
