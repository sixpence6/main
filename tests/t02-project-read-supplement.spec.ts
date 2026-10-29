import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import {
  PROJECT_A,
  emptyConfig,
  sampleAsset,
  sampleBrief,
  sampleMessage,
  sampleProject,
} from './support/fixtures'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import type { ProjectDetailResponse } from '@/api/types'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = '/api/projects/' + PROJECT_A

async function openDrawer(): Promise<HTMLElement> {
  await harness!.user.click(screen.getByRole('button', { name: '已有项目' }))
  return screen.findByRole('dialog', { name: '已有项目' })
}

/** 直接链接项目详情；路由在挂载前注册，避免测试替身晚于首屏请求。 */
async function mountProjectDetail(body: ProjectDetailResponse | unknown, status = 200): Promise<WorkspaceHarness> {
  harness = await mountWorkspace({
    config: emptyConfig(),
    url: '/?project=' + PROJECT_A,
    viewportWidth: 1440,
    setup: (fetch) => {
      fetch.json('GET', detailPath, body, status)
    },
  })
  return harness
}

describe('T02 补充验收：加载、刷新、快照与参考图', () => {
  it('首屏加载显示 role=status；在途重复打开不重复请求', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    harness.fetch.sequence('GET', '/api/projects', [
      { body: { items: [], next_cursor: null }, delayMs: 500 },
    ])

    const dialog = await openDrawer()
    expect(within(dialog).getByRole('status')).toHaveTextContent('正在读取项目')

    await harness.user.keyboard('{Escape}')
    await harness.waitFor(() => expect(screen.queryByRole('dialog', { name: '已有项目' })).not.toBeInTheDocument())
    const reopened = await openDrawer()
    expect(harness.fetch.count('GET', '/api/projects')).toBe(1)
    await within(reopened).findByText('暂无项目。新建项目后会显示在这里。')
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('刷新式重新挂载直接链接仍只发 GET，不重发消息或生成', async () => {
    const response: ProjectDetailResponse = {
      project: sampleProject({ id: PROJECT_A, title: '刷新项目' }),
      messages: [
        sampleMessage({ id: 'm1', role: 'user', content: '刷新前的用户消息' }),
        sampleMessage({ id: 'm2', role: 'assistant', content: '刷新前的助手回复' }),
      ],
      generations: [],
    }
    await mountProjectDetail(response)

    expect(await screen.findByRole('heading', { name: '刷新项目' })).toBeVisible()
    expect(screen.getByText('刷新前的用户消息')).toBeVisible()
    expect(harness!.fetch.count('GET', detailPath)).toBe(1)
    expect(harness!.fetch.writes).toHaveLength(0)

    harness!.dispose()
    harness = undefined

    await mountProjectDetail(response)

    expect(await screen.findByRole('heading', { name: '刷新项目' })).toBeVisible()
    expect(harness!.fetch.count('GET', detailPath)).toBe(1)
    expect(harness!.fetch.count('POST', /\/messages$/)).toBe(0)
    expect(harness!.fetch.count('POST', /\/generations$/)).toBe(0)
    expect(harness!.fetch.writes).toHaveLength(0)
  })

  it('空快照不渲染历史卡，有内容的快照卡不出现可操作按钮', async () => {
    await mountProjectDetail({
      project: sampleProject({ id: PROJECT_A, title: '快照边界项目' }),
      messages: [
        sampleMessage({
          id: 'm1',
          role: 'assistant',
          content: '空快照回复',
          brief_snapshot: sampleBrief({ product_description: '', purpose: '', style: '', composition: '', text_content: '', prompt: '' }),
        }),
        sampleMessage({
          id: 'm2',
          role: 'assistant',
          content: '有内容快照回复',
          brief_snapshot: sampleBrief({ product_description: '快照商品', prompt: '快照提示词' }),
        }),
      ],
      generations: [],
    })

    await screen.findByRole('heading', { name: '快照边界项目' })
    const cards = screen.getAllByRole('region', { name: '当时的需求快照' })
    expect(cards).toHaveLength(1)
    expect(within(cards[0]!).getByText('快照商品')).toBeVisible()
    expect(within(cards[0]!).getByText('快照提示词')).toBeVisible()
    expect(within(cards[0]!).queryAllByRole('button')).toHaveLength(0)
    expect(harness!.fetch.writes).toHaveLength(0)
  })

  it('参考图 missing 明确文件缺失且无伪造预览，available 才显示 img', async () => {
    const missingAsset = sampleAsset({ availability: 'missing' })
    await mountProjectDetail({
      project: sampleProject({
        id: PROJECT_A,
        title: '缺失参考图项目',
        brief: sampleBrief({ reference_asset_id: missingAsset.id }),
        reference_asset: missingAsset,
      }),
      messages: [],
      generations: [],
    })

    await screen.findByRole('heading', { name: '缺失参考图项目' })
    expect(screen.getByText('参考图文件缺失')).toBeVisible()
    expect(screen.queryByRole('img', { name: '参考图' })).not.toBeInTheDocument()

    harness!.dispose()
    harness = undefined

    const availableAsset = sampleAsset({ availability: 'available' })
    await mountProjectDetail({
      project: sampleProject({
        id: PROJECT_A,
        title: '可用参考图项目',
        brief: sampleBrief({ reference_asset_id: availableAsset.id }),
        reference_asset: availableAsset,
      }),
      messages: [],
      generations: [],
    })

    await screen.findByRole('heading', { name: '可用参考图项目' })
    const image = screen.getByRole('img', { name: '参考图' })
    expect(image).toHaveAttribute('src', '/api/assets/' + availableAsset.id)
    expect(screen.queryByText('参考图文件缺失')).not.toBeInTheDocument()
  })

  it('空白 project 参数按无效链接处理，不发详情 GET；404 安全文本不泄露', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), url: '/?project=%20', viewportWidth: 1440 })
    expect(await screen.findByText(/项目链接无效/)).toBeVisible()
    expect(harness.fetch.requests.filter((request) => request.path.startsWith('/api/projects/'))).toHaveLength(0)
    harness.dispose()
    harness = undefined

    await mountProjectDetail(
      { error: { code: 'PROJECT_NOT_FOUND', message: 'sk-secret Bearer https://vendor.example stack trace at foo (bar)' } },
      404,
    )
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('项目不存在或已被删除')
    expect(alert).not.toHaveTextContent('sk-secret')
    expect(alert).not.toHaveTextContent('Bearer')
    expect(alert).not.toHaveTextContent('https://vendor.example')
    expect(alert).not.toHaveTextContent('stack')
    expect(within(alert).getByRole('button', { name: '打开已有项目' })).toBeEnabled()
    expect(within(alert).getByRole('button', { name: '新建项目' })).toBeEnabled()
    expect(harness!.fetch.writes).toHaveLength(0)
  })

  it('harness 的 clock、visibility 与 storage 边界在 T02 场景可用', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), fakeClock: true })
    const before = Date.now()
    await harness.clock.advance(1000)
    expect(Date.now() - before).toBe(1000)
    harness.visibility.hide()
    expect(document.hidden).toBe(true)
    harness.visibility.show()
    expect(document.hidden).toBe(false)
    harness.storage.session.setItem('t02', 'ok')
    expect(harness.storage.session.getItem('t02')).toBe('ok')
    harness.storage.session.failWrites = true
    expect(() => harness!.storage.session.setItem('x', '1')).toThrow()
    expect(harness.fetch.writes).toHaveLength(0)
  })
})
