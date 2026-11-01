import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleAsset,
  sampleBrief,
  sampleGeneration,
  sampleProject,
  unknownCapabilitiesConfig,
} from './support/fixtures'
import type { ProjectDetailResponse, ProjectView } from '@/api/types'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = '/api/projects/' + PROJECT_A
const referencePath = `/api/projects/${PROJECT_A}/reference`

const PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='

function pngFile(name = 'reference.png'): File {
  const bytes = Uint8Array.from(atob(PNG_BASE64), (char) => char.charCodeAt(0))
  return new File([bytes], name, { type: 'image/png' })
}

function detailResponse(project: ProjectView): ProjectDetailResponse {
  return { project, messages: [], generations: [] }
}

function projectWithReference(overrides: Partial<ProjectView> = {}): ProjectView {
  const asset = sampleAsset()
  return sampleProject({
    id: PROJECT_A,
    revision: 3,
    prompt_revision: 3,
    confirmed_revision: 3,
    brief_status: 'review',
    reference_asset: asset,
    brief: sampleBrief({ reference_asset_id: asset.id }),
    ...overrides,
  })
}

function projectAfterReferenceChange(overrides: Partial<ProjectView> = {}): ProjectView {
  const asset = sampleAsset({ id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' })
  return sampleProject({
    id: PROJECT_A,
    revision: 4,
    prompt_revision: null,
    confirmed_revision: null,
    brief_status: 'draft',
    reference_asset: asset,
    brief: sampleBrief({ reference_asset_id: asset.id, prompt: '' }),
    ...overrides,
  })
}

async function mountProject(project: ProjectView, responses = [{ body: detailResponse(project) }]) {
  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: `/?project=${PROJECT_A}`,
    routes: [{ method: 'GET', url: detailPath, responses }],
  })
  await screen.findByRole('heading', { name: '春季托特包主图' })
  return harness
}

async function chooseFile(file: File): Promise<void> {
  const input = screen.getByLabelText('选择参考图文件')
  await waitFor(() => expect(input).toBeEnabled())
  await fireEvent.change(input, { target: { files: [file] } })
}

describe('T05 参考图上传与首次创建', () => {
  it('首次上传先创建项目，再以 multipart 上传并只使用服务端返回的项目', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
    })
    const created = sampleProject({ id: PROJECT_A })
    const uploaded = projectAfterReferenceChange()
    harness.fetch.json('POST', '/api/projects', created, 201)
    harness.fetch.json('POST', referencePath, uploaded)

    await chooseFile(pngFile())

    expect(
      await screen.findByText('参考图已上传。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' }),
    ).toBeVisible()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    const request = harness.fetch.last('POST', referencePath)
    expect(request?.body).toMatchObject({ expected_revision: '3' })
    expect((request?.body as { file: File }).file).toBeInstanceOf(File)
    expect((request?.body as { file: File }).file.name).toBe('reference.png')
    expect(harness.fetch.requests.some((item) => item.path.endsWith('/messages'))).toBe(false)
    expect(harness.fetch.requests.some((item) => item.path.includes('/generations'))).toBe(false)
    expect(screen.getByRole('img', { name: '参考图' })).toBeVisible()
  })


  it('上传响应前显示待上传预览，成功后才成为当前参考图', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    harness.fetch.json('POST', '/api/projects', sampleProject({ id: PROJECT_A }), 201)
    harness.fetch.route('POST', referencePath, () => ({
      body: projectAfterReferenceChange(),
      delayMs: 250,
    }))

    const choosing = chooseFile(pngFile('pending.png'))
    expect(await screen.findByText('待上传')).toBeVisible()
    expect(screen.getByRole('img', { name: '待上传的参考图预览' })).toBeVisible()
    expect(screen.queryByRole('img', { name: '参考图' })).toBeNull()
    await choosing

    await screen.findByText('参考图已上传。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' })
    expect(screen.getByRole('img', { name: '参考图' })).toBeVisible()
    expect(screen.queryByText('待上传')).toBeNull()
  })

  it('有效图片通过拖放进入同一上传流程，不调用模型', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    const created = sampleProject({ id: PROJECT_A })
    harness.fetch.json('POST', '/api/projects', created, 201)
    harness.fetch.json('POST', referencePath, projectAfterReferenceChange())

    await waitFor(() => expect(screen.getByLabelText('选择参考图文件')).toBeEnabled())
    const dropzone = screen.getByLabelText('拖放参考图到此处')
    await fireEvent.drop(dropzone, { dataTransfer: { files: [pngFile('dropped.png')] } })

    await screen.findByText('参考图已上传。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' })
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect((harness.fetch.last('POST', referencePath)?.body as { file: File }).file.name).toBe(
      'dropped.png',
    )
    expect(harness.fetch.requests.some((item) => item.path.endsWith('/messages'))).toBe(false)
  })


  it('粘贴图片不会触发上传或创建项目', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    await waitFor(() => expect(screen.getByLabelText('选择参考图文件')).toBeEnabled())
    const dropzone = screen.getByLabelText('拖放参考图到此处')
    fireEvent.paste(dropzone, {
      clipboardData: { files: [pngFile('pasted.png')] },
    })
    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('POST', '/api/projects')).toBe(0)
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('取消选择不创建项目也不改变当前状态', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    const input = screen.getByLabelText('选择参考图文件')
    await fireEvent.change(input, { target: { files: [] } })
    expect(harness.fetch.count('POST', '/api/projects')).toBe(0)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('T05 参考图校验与失败恢复', () => {
  it('无效类型、超限、损坏和多文件都整批拒绝且不创建项目', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
    const input = screen.getByLabelText('选择参考图文件')
    await waitFor(() => expect(input).toBeEnabled())

    await fireEvent.change(input, {
      target: { files: [new File(['not an image'], 'note.txt', { type: 'text/plain' })] },
    })
    expect(await screen.findByText(/无法读取这张图片/)).toBeVisible()

    const oversized = new File([new Uint8Array(5_000_001)], 'large.png', { type: 'image/png' })
    await fireEvent.change(input, { target: { files: [oversized] } })
    expect(await screen.findByText(/参考图超过允许大小/)).toBeVisible()

    await fireEvent.change(input, {
      target: { files: [new File(['broken png'], 'broken.png', { type: 'image/png' })] },
    })
    expect(await screen.findByText(/无法读取这张图片/)).toBeVisible()

    await fireEvent.change(input, {
      target: { files: [pngFile('one.png'), pngFile('two.png')] },
    })
    expect(await screen.findByText('一次只能上传 1 张参考图，请重新选择。')).toBeVisible()
    expect(harness.fetch.count('POST', '/api/projects')).toBe(0)
    expect(harness.fetch.writes).toHaveLength(0)
  })


  it('已有参考图时，本地无效文件整批拒绝且不替换当前图', async () => {
    harness = await mountProject(projectWithReference())
    const oldImage = screen.getByRole('img', { name: '参考图' })
    await chooseFile(new File(['not an image'], 'note.txt', { type: 'text/plain' }))

    expect(await screen.findByText(/无法读取这张图片/)).toBeVisible()
    expect(screen.getByRole('img', { name: '参考图' })).toBe(oldImage)
    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('POST', '/api/projects')).toBe(0)
  })

  it('上传失败保留当前有效图，重试复用已创建的项目和同一修订', async () => {
    const created = sampleProject({ id: PROJECT_A })
    const uploaded = projectAfterReferenceChange()
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      setup(fetch) {
        fetch.json('POST', '/api/projects', created, 201)
        fetch.sequence('POST', referencePath, [
          {
            status: 422,
            body: { error: { code: 'INVALID_REFERENCE_IMAGE', message: '无法解码' } },
          },
          { body: uploaded },
        ])
      },
    })

    await chooseFile(pngFile())
    expect(await screen.findByText('图片无法解码或已损坏，已保留当前参考图。')).toBeVisible()
    expect(screen.getByText('上传失败，尚未成为当前参考图。')).toBeVisible()
    expect(screen.queryByRole('img', { name: '参考图' })).toBeNull()

    await harness.user.click(screen.getByRole('button', { name: '重试上传' }))
    await screen.findByText('参考图已上传。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' })
    expect(harness.fetch.count('POST', '/api/projects')).toBe(1)
    expect(harness.fetch.count('POST', referencePath)).toBe(2)
    expect(harness.fetch.last('POST', referencePath)?.body).toMatchObject({
      expected_revision: '3',
    })
  })

  it('替换失败保留旧参考图和当前项目修订', async () => {
    harness = await mountProject(projectWithReference())
    const oldImage = screen.getByRole('img', { name: '参考图' })
    harness.fetch.route('POST', referencePath, () => ({
      status: 422,
      body: { error: { code: 'INVALID_REFERENCE_IMAGE', message: '无法解码' } },
    }))

    await harness.user.click(screen.getByRole('button', { name: '替换参考图' }))
    await chooseFile(pngFile('replacement.png'))

    expect(await screen.findByText('图片无法解码或已损坏，已保留当前参考图。')).toBeVisible()
    expect(screen.getByRole('img', { name: '参考图' })).toBe(oldImage)
    expect(harness.fetch.last('POST', referencePath)?.body).toMatchObject({
      expected_revision: '3',
    })
    expect(harness.fetch.count('POST', '/api/projects')).toBe(0)
  })


  it('可用键盘聚焦替换按钮并完成替换选择', async () => {
    harness = await mountProject(projectWithReference())
    harness.fetch.json('POST', referencePath, projectAfterReferenceChange())

    const replaceButton = screen.getByRole('button', { name: '替换参考图' })
    replaceButton.focus()
    await harness.user.keyboard('{Enter}')
    await chooseFile(pngFile('keyboard.png'))

    expect(
      await screen.findByText('参考图已上传。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' }),
    ).toBeVisible()
    expect((harness.fetch.last('POST', referencePath)?.body as { file: File }).file.name).toBe(
      'keyboard.png',
    )
  })

  it('替换成功后使用服务端修订，并使旧提示词与确认失效', async () => {
    harness = await mountProject(projectWithReference())
    harness.fetch.json('POST', referencePath, projectAfterReferenceChange())

    await harness.user.click(screen.getByRole('button', { name: '替换参考图' }))
    await chooseFile(pngFile('replacement.png'))

    expect(
      await screen.findByText('参考图已上传。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' }),
    ).toBeVisible()
    expect(harness.fetch.last('POST', referencePath)?.body).toMatchObject({
      expected_revision: '3',
    })
    expect(harness.fetch.requests.some((item) => item.path.endsWith('/messages'))).toBe(false)
  })
})

describe('T05 参考图替换、移除与回执不明', () => {
  it('移除参考图携带当前修订，成功后清除参考图并提示重新整理', async () => {
    const removed = projectAfterReferenceChange({ reference_asset: null, brief: sampleBrief({ prompt: '' }) })
    harness = await mountProject(projectWithReference())
    harness.fetch.json('DELETE', referencePath, removed)

    const removeButton = screen.getByRole('button', { name: '移除参考图' })
    removeButton.focus()
    await harness.user.keyboard('{Enter}')

    expect(
      await screen.findByText('参考图已移除。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' }),
    ).toBeVisible()
    expect(harness.fetch.last('DELETE', referencePath)?.search).toBe('?expected_revision=3')
    expect(screen.queryByRole('img', { name: '参考图' })).toBeNull()
    expect(screen.queryByRole('button', { name: '移除参考图' })).toBeNull()
  })


  it('快速重复点击移除只发送一次 DELETE', async () => {
    const removed = projectAfterReferenceChange({
      reference_asset: null,
      brief: sampleBrief({ prompt: '' }),
    })
    harness = await mountProject(projectWithReference())
    harness.fetch.route('DELETE', referencePath, () => ({ body: removed, delayMs: 150 }))

    const removeButton = screen.getByRole('button', { name: '移除参考图' })
    fireEvent.click(removeButton)
    fireEvent.click(removeButton)

    await screen.findByText('参考图已移除。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' })
    expect(harness.fetch.count('DELETE', referencePath)).toBe(1)
  })

  it('上传回执不明时先 GET 核实，确认新图后才采用并清除待上传状态', async () => {
    const initial = sampleProject({ id: PROJECT_A, revision: 3 })
    const verified = projectAfterReferenceChange()
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: `/?project=${PROJECT_A}`,
      routes: [
        {
          method: 'GET',
          url: detailPath,
          responses: [{ body: detailResponse(initial) }, { body: detailResponse(verified) }],
        },
      ],
    })
    await screen.findByRole('heading', { name: '春季托特包主图' })
    harness.fetch.route('POST', referencePath, () => ({
      status: 503,
      body: { error: { code: 'LOCAL_STORAGE_UNAVAILABLE', message: '暂时不可用' } },
    }))

    await chooseFile(pngFile())

    expect(
      await screen.findByText('已核实参考图上传成功。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' }),
    ).toBeVisible()
    expect(harness.fetch.count('GET', detailPath)).toBe(2)
    expect(harness.fetch.count('POST', referencePath)).toBe(1)
    expect(screen.queryByText('正在上传参考图…')).toBeNull()
  })

  it('移除回执不明时以 GET 结果为准，不重复发送移除请求', async () => {
    const initial = projectWithReference()
    const removed = projectAfterReferenceChange({
      reference_asset: null,
      brief: sampleBrief({ prompt: '' }),
    })
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: `/?project=${PROJECT_A}`,
      routes: [
        {
          method: 'GET',
          url: detailPath,
          responses: [{ body: detailResponse(initial) }, { body: detailResponse(removed) }],
        },
      ],
    })
    await screen.findByRole('heading', { name: '春季托特包主图' })
    expect(await screen.findByRole('heading', { name: '春季托特包主图' })).toBeVisible()
    harness.fetch.route('DELETE', referencePath, () => ({
      status: 500,
      body: { error: { code: 'INTERNAL', message: '服务异常' } },
    }))

    await harness.user.click(screen.getByRole('button', { name: '移除参考图' }))

    expect(
      await screen.findByText('已核实参考图已移除。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' }),
    ).toBeVisible()
    expect(harness.fetch.count('DELETE', referencePath)).toBe(1)
    expect(harness.fetch.count('GET', detailPath)).toBe(2)
    expect(screen.queryByRole('img', { name: '参考图' })).toBeNull()
  })

  it('已有成功生成记录时，参考图仍指向原始资源而不是生成结果', async () => {
    const project = projectWithReference()
    const referenceId = project.reference_asset!.id
    const generation = sampleGeneration({
      reference_asset_id: referenceId,
      result_asset_id: 'generated-result-asset',
      result_asset: sampleAsset({ id: 'generated-result-asset', kind: 'generated' }),
    })
    harness = await mountProject(project, [
      { body: { project, messages: [], generations: [generation] } },
    ])

    const image = screen.getByRole('img', { name: '参考图' })
    expect(image.getAttribute('src')).toContain('/api/assets/' + referenceId)
    expect(image.getAttribute('src')).not.toContain('generated-result-asset')
  })
})

describe('T05 能力边界与资源释放', () => {
  it('能力明确不支持时说明原因并禁用选择，已有图仍可移除', async () => {
    const config = configuredConfig()
    config.visual.capabilities.reference_image = false
    config.visual.capabilities.reference_mime_types = ['image/png']
    harness = await mountWorkspace({
      config,
      viewportWidth: 1440,
      url: `/?project=${PROJECT_A}`,
      routes: [{ method: 'GET', url: detailPath, responses: [{ body: detailResponse(projectWithReference()) }] }],
    })
    await screen.findByRole('heading', { name: '春季托特包主图' })
    expect(await screen.findByRole('heading', { name: '春季托特包主图' })).toBeVisible()

    expect(screen.getByText('当前模型明确不支持参考图；已有参考图时可移除后继续纯文字流程。')).toBeVisible()
    expect(screen.getByLabelText('选择参考图文件')).toBeDisabled()
    expect(screen.getByRole('button', { name: '移除参考图' })).toBeEnabled()

    harness.fetch.json(
      'DELETE',
      referencePath,
      projectAfterReferenceChange({ reference_asset: null, brief: sampleBrief({ prompt: '' }) }),
    )
    await harness.user.click(screen.getByRole('button', { name: '移除参考图' }))
    await screen.findByText('参考图已移除。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' })
    expect(harness.fetch.count('DELETE', referencePath)).toBe(1)
  })

  it('能力未核实时显示待核实，带参考图时锁定生成图片阶段', async () => {
    const config = unknownCapabilitiesConfig()
    config.visual.capabilities.reference_mime_types = ['image/png', 'image/jpeg']
    harness = await mountWorkspace({
      config,
      viewportWidth: 1440,
      url: `/?project=${PROJECT_A}`,
      routes: [{ method: 'GET', url: detailPath, responses: [{ body: detailResponse(projectWithReference()) }] }],
    })
    await screen.findByRole('heading', { name: '春季托特包主图' })

    expect(screen.getByText('参考图能力待核实；带参考图时不能生成图片。')).toBeVisible()
    expect(harness.fetch.writes).toHaveLength(0)
  })


  it('卸载时释放尚未成功上传的待上传预览对象 URL', async () => {
    const createObjectURL = vi.fn(() => 'blob:t05-pending')
    const revokeObjectURL = vi.fn()
    const originalCreate = URL.createObjectURL
    const originalRevoke = URL.revokeObjectURL
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: createObjectURL,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: revokeObjectURL,
    })
    try {
      harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
      harness.fetch.json('POST', '/api/projects', sampleProject({ id: PROJECT_A }), 201)
      harness.fetch.route('POST', referencePath, () => ({
        status: 422,
        body: { error: { code: 'INVALID_REFERENCE_IMAGE', message: '无法解码' } },
      }))
      await chooseFile(pngFile('pending.png'))
      await screen.findByText('图片无法解码或已损坏，已保留当前参考图。')
      expect(revokeObjectURL).not.toHaveBeenCalled()

      harness.dispose()
      harness = undefined
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:t05-pending')
    } finally {
      Object.defineProperty(URL, 'createObjectURL', {
        configurable: true,
        writable: true,
        value: originalCreate,
      })
      Object.defineProperty(URL, 'revokeObjectURL', {
        configurable: true,
        writable: true,
        value: originalRevoke,
      })
    }
  })

  it('上传成功后释放临时预览对象 URL', async () => {
    const createObjectURL = vi.fn(() => 'blob:t05-preview')
    const revokeObjectURL = vi.fn()
    const originalCreate = URL.createObjectURL
    const originalRevoke = URL.revokeObjectURL
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: createObjectURL,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: revokeObjectURL,
    })
    try {
      harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
      harness.fetch.json('POST', '/api/projects', sampleProject({ id: PROJECT_A }), 201)
      harness.fetch.json('POST', referencePath, projectAfterReferenceChange())
      await chooseFile(pngFile())
      await screen.findByText('参考图已上传。旧提示词与确认已失效，请重新整理并确认需求。', { selector: '.status-notice' })
      expect(createObjectURL).toHaveBeenCalledTimes(1)
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:t05-preview')
    } finally {
      Object.defineProperty(URL, 'createObjectURL', {
        configurable: true,
        writable: true,
        value: originalCreate,
      })
      Object.defineProperty(URL, 'revokeObjectURL', {
        configurable: true,
        writable: true,
        value: originalRevoke,
      })
    }
  })
})
