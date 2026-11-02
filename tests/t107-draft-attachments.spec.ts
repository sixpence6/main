import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { clearSession, sessionRevision } from '@/auth/session'
import type { ProjectDetailResponse, ProjectView } from '@/api/types'
import type { WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  PROJECT_B,
  configuredConfig,
  sampleAsset,
  sampleBrief,
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

const detailPathA = '/api/projects/' + PROJECT_A
const detailPathB = '/api/projects/' + PROJECT_B
const referencePathA = '/api/projects/' + PROJECT_A + '/reference'
const referencePathB = '/api/projects/' + PROJECT_B + '/reference'
const messagesPathA = '/api/projects/' + PROJECT_A + '/messages'
const PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='

function pngFile(name = 'reference.png'): File {
  const bytes = Uint8Array.from(atob(PNG_BASE64), (char) => char.charCodeAt(0))
  return new File([bytes], name, { type: 'image/png' })
}

function detailResponse(project: ProjectView): ProjectDetailResponse {
  return { project, messages: [], generations: [] }
}

function projectA(overrides: Partial<ProjectView> = {}): ProjectView {
  return sampleProject({ id: PROJECT_A, title: '春季托特包主图', ...overrides })
}

function projectB(): ProjectView {
  return sampleProject({
    id: PROJECT_B,
    title: '陶瓷马克杯',
    brief: sampleBrief({ product_description: '陶瓷马克杯' }),
  })
}

function projectAfterReference(): ProjectView {
  const asset = sampleAsset({ id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' })
  return sampleProject({
    id: PROJECT_A,
    revision: 4,
    prompt_revision: null,
    confirmed_revision: null,
    brief_status: 'draft',
    reference_asset: asset,
    brief: sampleBrief({ reference_asset_id: asset.id, prompt: '' }),
  })
}

function workflowView(projectId = PROJECT_A): WorkflowView {
  return {
    project_id: projectId,
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

async function mountProjectA(extraRoutes: NonNullable<Parameters<typeof mountWorkspace>[0]>['routes'] = []) {
  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/?project=' + PROJECT_A,
    routes: [
      { method: 'GET', url: detailPathA, responses: [{ body: detailResponse(projectA()) }] },
      { method: 'GET', url: '/api/projects/' + PROJECT_A + '/workflow', responses: [{ body: workflowView() }] },
      ...extraRoutes,
    ],
  })
  await screen.findByRole('heading', { name: '春季托特包主图' })
  return harness
}

function pasteFiles(target: HTMLElement, files: File[]): boolean {
  const event = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(event, 'clipboardData', { value: { files } })
  return target.dispatchEvent(event)
}

async function openProjectDrawer(): Promise<HTMLElement> {
  await harness!.user.click(screen.getByRole('button', { name: '已有项目' }))
  return screen.findByRole('dialog', { name: '已有项目' })
}

describe('T21 单一输入与参考图生命周期', () => {
  it('输入框粘贴图片只准备一张待上传参考图，不发送消息也不生成', async () => {
    harness = await mountProjectA([
      { method: 'POST', url: referencePathA, responses: [{ body: projectAfterReference(), delayMs: 120 }] },
    ])
    const composer = screen.getByLabelText('描述商品与图片需求')
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })

    pasteFiles(textarea, [pngFile('clipboard.png')])
    expect(await screen.findByText('待上传')).toBeVisible()
    expect(screen.getByRole('img', { name: '待上传的参考图预览' })).toBeVisible()
    expect(await screen.findByText('参考图已上传。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' })).toBeVisible()
    expect(harness.fetch.count('POST', referencePathA)).toBe(1)
    expect((harness.fetch.last('POST', referencePathA)?.body as { file: File }).file.name).toBe('clipboard.png')
    expect((harness.fetch.last('POST', referencePathA)?.body as { expected_revision: string }).expected_revision).toBe('3')
    expect(harness.fetch.requests.some((item) => item.path.endsWith('/messages'))).toBe(false)
    expect(harness.fetch.requests.some((item) => item.path.includes('/generations'))).toBe(false)
    expect(composer).toHaveValue('')
  })

  it('普通文字粘贴不被吞掉，非图片拖放不导航也不上传', async () => {
    harness = await mountProjectA()
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    expect(pasteFiles(textarea, [])).toBe(true)
    expect(harness.fetch.writes).toHaveLength(0)

    const before = window.location.pathname
    await fireEvent.drop(textarea, {
      dataTransfer: { files: [new File(['plain'], 'note.txt', { type: 'text/plain' })] },
    })
    expect(window.location.pathname).toBe(before)
    expect(harness.fetch.writes).toHaveLength(0)
    expect(screen.queryByText('待上传')).toBeNull()
  })

  it('同一项目聊天与结果切换保留草稿和待上传参考图', async () => {
    harness = await mountProjectA([
      { method: 'POST', url: referencePathA, responses: [{ body: projectAfterReference(), delayMs: 2000 }] },
      { method: 'GET', url: '/api/projects/' + PROJECT_A + '/workflow', responses: [{ body: workflowView() }] },
    ])
    await harness.user.type(screen.getByLabelText('描述商品与图片需求'), '同一项目的跨路由草稿')
    pasteFiles(screen.getByRole('textbox', { name: '描述商品与图片需求' }), [pngFile('route.png')])
    expect(await screen.findByText('待上传')).toBeVisible()

    await harness.router.push({ name: 'project-results', params: { projectId: PROJECT_A } })
    expect(await screen.findByText('结果不可用')).toBeVisible()
    expect(screen.queryByRole('textbox', { name: '描述商品与图片需求' })).toBeNull()

    await harness.router.push({ name: 'project-chat', params: { projectId: PROJECT_A } })
    expect(await screen.findByRole('textbox', { name: '描述商品与图片需求' })).toHaveValue('同一项目的跨路由草稿')
    expect(screen.getByText('待上传')).toBeVisible()
    expect(screen.getByRole('img', { name: '待上传的参考图预览' })).toBeVisible()
    expect(harness.fetch.writes).toHaveLength(1)
  })

  it('只有待上传参考图时切换项目也会提示，取消保留，确认后清理且迟到响应不附着新项目', async () => {
    harness = await mountProjectA([
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
      { method: 'GET', url: detailPathB, responses: [{ body: detailResponse(projectB()) }] },
      { method: 'GET', url: '/api/projects/' + PROJECT_B + '/workflow', responses: [{ body: workflowView(PROJECT_B) }] },
      { method: 'POST', url: referencePathA, responses: [{ body: projectAfterReference(), delayMs: 2000 }] },
    ])
    pasteFiles(screen.getByRole('textbox', { name: '描述商品与图片需求' }), [pngFile('pending.png')])
    expect(await screen.findByText('待上传')).toBeVisible()

    let drawer = await openProjectDrawer()
    await harness.user.click(within(drawer).getByRole('button', { name: /陶瓷马克杯/ }))
    const dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    expect(within(dialog).getByText('尚未上传完成的参考图')).toBeVisible()
    await harness.user.click(within(dialog).getByRole('button', { name: '继续编辑' }))
    expect(screen.getByText('待上传')).toBeVisible()
    expect(harness.router.currentRoute.value.name).toBe('describe')
    expect(harness.router.currentRoute.value.query.project).toBe(PROJECT_A)

    drawer = await openProjectDrawer()
    await harness.user.click(within(drawer).getByRole('button', { name: /陶瓷马克杯/ }))
    await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    await harness.user.click(screen.getByRole('button', { name: '放弃修改' }))

    expect(await screen.findByRole('heading', { name: '陶瓷马克杯' })).toBeVisible()
    expect(screen.queryByText('待上传')).toBeNull()
    expect(screen.queryByRole('img', { name: '待上传的参考图预览' })).toBeNull()
    expect(harness.fetch.count('POST', referencePathA)).toBe(1)
    expect(harness.fetch.count('POST', referencePathB)).toBe(0)
    expect(harness.fetch.count('GET', detailPathB)).toBe(1)
  })

  it('账号生命周期变化清空旧草稿和待上传图片', async () => {
    harness = await mountProjectA([
      { method: 'POST', url: referencePathA, responses: [{ body: projectAfterReference(), delayMs: 2000 }] },
    ])
    await harness.user.type(screen.getByLabelText('描述商品与图片需求'), '旧账号草稿')
    pasteFiles(screen.getByRole('textbox', { name: '描述商品与图片需求' }), [pngFile('old-account.png')])
    expect(await screen.findByText('待上传')).toBeVisible()

    sessionRevision.value += 1
    await waitFor(() => expect(harness!.router.currentRoute.value.name).toBe('describe'))
    expect(screen.getByLabelText('描述商品与图片需求')).toHaveValue('')
    expect(screen.queryByText('待上传')).toBeNull()
    expect(harness.fetch.count('POST', referencePathA)).toBe(1)
  })

  it('发送中连续按 Enter 只提交一次', async () => {
    harness = await mountProjectA([
      {
        method: 'POST',
        url: messagesPathA,
        responses: [{
          body: {
            project: projectA({ revision: 4 }),
            user_message: sampleMessage({ id: 'user-1', role: 'user', content: '只发送一次' }),
            assistant_message: sampleMessage({ id: 'assistant-1', role: 'assistant', content: '已整理' }),
          },
          delayMs: 250,
        }],
      },
    ])
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '只发送一次')
    await fireEvent.keyDown(textarea, { key: 'Enter', code: 'Enter' })
    await fireEvent.keyDown(textarea, { key: 'Enter', code: 'Enter' })
    await waitFor(() => expect(harness!.fetch.count('POST', messagesPathA)).toBe(1))
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(harness.fetch.count('POST', messagesPathA)).toBe(1)
  })
})
