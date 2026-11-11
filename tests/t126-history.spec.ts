import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import type { ProjectDetailResponse, ProjectView } from '@/api/types'
import type { WorkflowVersion, WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, emptyConfig, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const DETAIL_A = '/api/projects/' + PROJECT_A
const WORKFLOW_A = DETAIL_A + '/workflow'
const RUN_CURRENT = 'run-current'
const RUN_OLD = 'run-old'

function project(): ProjectView {
  return sampleProject({ id: PROJECT_A, title: '项目甲', revision: 3 })
}

function detail(): ProjectDetailResponse {
  return { project: project(), messages: [], generations: [] }
}

function version(overrides: Partial<WorkflowVersion> = {}): WorkflowVersion {
  return {
    run_id: RUN_CURRENT,
    sequence_no: 2,
    requirement_revision: 3,
    status: 'published',
    generation_job_id: 'job-current',
    asset_id: 'asset-current',
    filename: 'current.png',
    width: 1536,
    height: 1024,
    caption: '当前版本主图',
    published_at: '2026-10-21T06:02:00.000Z',
    brief: { product_description: '米色亚麻托特包' },
    prompt: { positive_prompt: '当前版本的已批准提示词', negative_prompt: '低清晰度' },
    quality: { status: 'passed' },
    can_download: true,
    created_at: '2026-10-21T06:00:00.000Z',
    ...overrides,
  }
}

function completedView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: RUN_CURRENT,
    stage: 'S11',
    status: 'completed',
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
    delivery: {
      id: 'delivery-current',
      status: 'published',
      asset_id: 'asset-current',
      filename: 'current.png',
      caption: '当前版本主图',
      published_at: '2026-10-21T06:02:00.000Z',
      created_at: '2026-10-21T06:02:00.000Z',
    },
    versions: [
      version({}),
      version({
        run_id: RUN_OLD,
        sequence_no: 1,
        requirement_revision: 2,
        generation_job_id: 'job-old',
        asset_id: 'asset-old',
        filename: 'old.png',
        width: 1024,
        height: 1024,
        caption: '旧版本主图',
        published_at: '2026-10-20T03:00:00.000Z',
        prompt: { positive_prompt: '旧版本的已批准提示词', negative_prompt: '不要多余文字' },
      }),
    ],
    updated_at: '2026-10-21T06:02:00.000Z',
    ...overrides,
  }
}

async function mountHistory(view: WorkflowView = completedView()): Promise<WorkspaceHarness> {
  const mounted = await mountWorkspace({
    config: emptyConfig(),
    viewportWidth: 1440,
    url: '/projects/' + PROJECT_A + '/chat',
    setup(fetch) {
      fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
      fetch.route('GET', DETAIL_A, () => ({ body: detail() }))
      fetch.route('GET', WORKFLOW_A, () => ({ body: view }))
    },
  })
  harness = mounted
  return mounted
}

function historyRegion(): Promise<HTMLElement> {
  return screen.findByRole('region', { name: '图片历史' })
}

describe('T14 多轮图片历史与旧链接只读定位', () => {
  it('多轮发布的图片和各自提示词按版本保留，刷新后不会重复插入', async () => {
    await mountHistory()
    const region = await historyRegion()
    const oldArticle = within(region).getByRole('article', { name: '版本 1 结果' })
    const currentArticle = within(region).getByRole('article', { name: '版本 2 结果' })

    expect(within(oldArticle).getByRole('img', { name: '旧版本主图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-old',
    )
    expect(within(currentArticle).getByRole('img', { name: '当前版本主图' })).toHaveAttribute(
      'src',
      '/api/assets/asset-current',
    )
    expect(within(oldArticle).getByText('历史版本')).toBeVisible()
    expect(within(currentArticle).getByText('当前版本')).toBeVisible()

    await harness!.user.click(within(oldArticle).getByText('该版本实际使用的已批准提示词'))
    expect(within(oldArticle).getByText('旧版本的已批准提示词')).toBeVisible()
    expect(within(oldArticle).queryByText(/当前版本的已批准提示词/)).not.toBeInTheDocument()

    await harness!.user.click(within(currentArticle).getByText('该版本实际使用的已批准提示词'))
    expect(within(currentArticle).getByText('当前版本的已批准提示词')).toBeVisible()
    expect(within(currentArticle).queryByText(/旧版本的已批准提示词/)).not.toBeInTheDocument()

    expect(harness!.fetch.writes).toHaveLength(0)
    expect(harness!.fetch.count('POST', /\/workflow\/actions/)).toBe(0)

    harness!.dispose()
    harness = undefined
    await mountHistory()
    const refreshed = await historyRegion()
    expect(within(refreshed).getAllByRole('article')).toHaveLength(2)
    expect(within(refreshed).getAllByRole('img')).toHaveLength(2)
  })

  it('查看历史提示词不切换当前基准，也不出现第二输入框或业务动作', async () => {
    await mountHistory()
    const region = await historyRegion()
    const oldArticle = within(region).getByRole('article', { name: '版本 1 结果' })

    await harness!.user.click(within(oldArticle).getByText('该版本实际使用的已批准提示词'))
    expect(within(region).getByText('当前版本')).toBeVisible()
    expect(screen.getAllByRole('textbox')).toHaveLength(1)
    expect(screen.getByRole('textbox', { name: '描述商品与图片需求' })).toBeEnabled()
    expect(harness!.fetch.writes).toHaveLength(0)
    expect(harness!.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })

  it('不可访问的历史版本明确显示，不用当前图片替代旧图', async () => {
    const view = completedView({
      versions: [
        version({}),
        version({
          run_id: RUN_OLD,
          sequence_no: 1,
          requirement_revision: 2,
          asset_id: null,
          filename: null,
          can_download: false,
          caption: '旧版本主图',
          prompt: { positive_prompt: '旧版本的已批准提示词', negative_prompt: '' },
        }),
      ],
    })
    await mountHistory(view)
    const region = await historyRegion()
    const oldArticle = within(region).getByRole('article', { name: '版本 1 结果' })

    expect(within(oldArticle).getByText('该版本图片不可用')).toBeVisible()
    expect(within(oldArticle).queryByRole('img')).not.toBeInTheDocument()
    expect(within(oldArticle).queryByRole('link', { name: /^查看大图/ })).not.toBeInTheDocument()
    expect(within(oldArticle).queryByRole('link', { name: '下载原图' })).not.toBeInTheDocument()
    expect(oldArticle.querySelector('img')).toBeNull()
    await harness!.user.click(within(oldArticle).getByText('该版本实际使用的已批准提示词'))
    expect(within(oldArticle).getByText('旧版本的已批准提示词')).toBeVisible()
    expect(within(region).getByRole('img', { name: '当前版本主图' })).toBeVisible()
  })

  it('当前版本图片不可访问时不把旧版本当作当前成功', async () => {
    const view = completedView({
      versions: [
        version({
          run_id: RUN_OLD,
          sequence_no: 1,
          requirement_revision: 2,
          asset_id: 'asset-old',
          filename: 'old.png',
          caption: '旧版本主图',
          prompt: { positive_prompt: '旧版本的已批准提示词', negative_prompt: '' },
        }),
        version({
          asset_id: null,
          filename: null,
          can_download: false,
          caption: '当前版本主图',
          prompt: { positive_prompt: '当前版本新的提示词', negative_prompt: '' },
        }),
      ],
    })
    await mountHistory(view)

    const unavailable = await screen.findByRole('region', { name: '当前结果不可用' })
    expect(within(unavailable).getByText(/当前图片暂时无法读取/)).toBeVisible()
    const region = await historyRegion()
    const oldArticle = within(region).getByRole('article', { name: '版本 1 结果' })
    const currentArticle = within(region).getByRole('article', { name: '版本 2 结果' })
    expect(within(oldArticle).getByText('历史版本')).toBeVisible()
    expect(within(currentArticle).getByText('当前版本')).toBeVisible()
    expect(within(currentArticle).getByText('该版本图片不可用')).toBeVisible()
    expect(within(region).getByRole('img', { name: '旧版本主图' })).toBeVisible()
    expect(screen.queryByText('图片已经生成，可以保存使用了。')).not.toBeInTheDocument()
    expect(harness!.fetch.writes).toHaveLength(0)
  })

  it('文件缺失时显示真实原因，不误报为内容门禁', async () => {
    await mountHistory(completedView({
      versions: [version({
        asset_id: null,
        filename: null,
        can_download: false,
        unavailable_reason: 'ASSET_FILE_MISSING',
      })],
    }))
    const unavailable = await screen.findByRole('region', { name: '当前结果不可用' })
    expect(within(unavailable).getByText('当前图片文件缺失，暂时无法预览或下载。')).toBeVisible()
    expect(within(unavailable).queryByText(/门禁|过期/)).not.toBeInTheDocument()
  })

  it('当前交付已撤销时只显示安全状态，不展示旧图兜底', async () => {
    await mountHistory(completedView({
      delivery: {
        id: 'delivery-current',
        status: 'revoked',
        asset_id: 'asset-current',
        filename: 'current.png',
        caption: '当前版本主图',
        published_at: '2026-10-21T06:02:00.000Z',
        created_at: '2026-10-21T06:02:00.000Z',
      },
      versions: [],
    }))

    const unavailable = await screen.findByRole('region', { name: '当前结果不可用' })
    expect(within(unavailable).getByText(/当前交付已撤销/)).toBeVisible()
    expect(within(unavailable).queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '图片历史' })).not.toBeInTheDocument()
    expect(harness!.fetch.writes).toHaveLength(0)
  })

  it('下载和只读结果页定位保留真实版本链接且没有生成副作用', async () => {
    await mountHistory()
    const region = await historyRegion()
    const oldArticle = within(region).getByRole('article', { name: '版本 1 结果' })

    expect(within(oldArticle).getByRole('link', { name: '下载原图' })).toHaveAttribute(
      'href',
      '/api/assets/asset-old?download=true',
    )
    expect(within(oldArticle).getByRole('link', { name: '在结果页只读查看' })).toHaveAttribute(
      'href',
      '/projects/' + PROJECT_A + '/results?version=' + RUN_OLD,
    )
    expect(within(oldArticle).getByRole('link', { name: '查看大图：旧版本主图' })).toHaveAttribute(
      'href',
      '/api/assets/asset-old',
    )
    expect(harness!.fetch.writes).toHaveLength(0)
    expect(harness!.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })
  it('旧阶段链接只定位到对话入口，不触发准备或生成', async () => {
    for (const url of ['/describe', '/confirm', '/generate', '/results']) {
      const mounted = await mountWorkspace({
        config: emptyConfig(),
        viewportWidth: 1440,
        url,
        setup(fetch) {
          fetch.route('GET', '/api/health', () => ({ body: { status: 'ok' } }))
        },
      })
      harness = mounted
      expect(
        await screen.findByRole('heading', {
          name: '发来商品图片，或告诉我你想做什么样的电商图片',
        }),
      ).toBeVisible()
      expect(mounted.router.currentRoute.value.path).toBe('/')
      expect(mounted.fetch.writes).toHaveLength(0)
      mounted.dispose()
      harness = undefined
    }
  })

})
