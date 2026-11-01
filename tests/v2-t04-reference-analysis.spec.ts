import { afterEach, describe, expect, it } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/vue'
import type { WorkflowView } from '@/api/workflow'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleAsset,
  sampleBrief,
  sampleProject,
} from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = '/api/projects/' + PROJECT_A
const workflowPath = '/api/projects/' + PROJECT_A + '/workflow'
const analyzePath = workflowPath + '/actions/analyze_reference'
const usagePath = workflowPath + '/actions/confirm_reference_usage'
const referencePath = '/api/projects/' + PROJECT_A + '/reference'

const PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='

function pngFile(name = 'reference.png'): File {
  const bytes = Uint8Array.from(atob(PNG_BASE64), (char) => char.charCodeAt(0))
  return new File([bytes], name, { type: 'image/png' })
}

function workflowView(overrides: Partial<WorkflowView> = {}): WorkflowView {
  return {
    project_id: PROJECT_A,
    run_id: null,
    stage: null,
    status: 'none',
    project_revision: 0,
    requirement_revision: 0,
    intent_route: null,
    intent_action: null,
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: null,
    updated_at: null,
    ...overrides,
  }
}

function detail(project: ReturnType<typeof sampleProject>) {
  return { project, messages: [], generations: [] }
}

async function mountProject(
  project: ReturnType<typeof sampleProject>,
  workflow: WorkflowView,
) {
  harness = await mountWorkspace({
    config: configuredConfig(),
    viewportWidth: 1440,
    url: '/?project=' + PROJECT_A,
    routes: [
      { method: 'GET', url: detailPath, responses: [{ body: detail(project) }] },
      { method: 'GET', url: workflowPath, responses: [{ body: workflow }, { body: workflow }] },
    ],
  })
  await screen.findByRole('heading', { name: '春季托特包主图' })
  return harness
}

describe('T04 上传后识图与参考报告审查', () => {
  it('报告未批准时只显示原图预览和固定阶段状态，并允许选择用途', async () => {
    const asset = sampleAsset({ id: 'reference-1' })
    const project = sampleProject({
      id: PROJECT_A,
      revision: 1,
      prompt_revision: null,
      confirmed_revision: null,
      brief_status: 'draft',
      reference_asset: asset,
      brief: sampleBrief({ reference_asset_id: asset.id, prompt: '' }),
    })
    const candidateReport = {
      id: 'report-candidate',
      kind: 'reference_report',
      revision_no: 1,
      status: 'candidate' as const,
      digest: 'digest-hidden',
      payload: { observations: [{ description: '秘密观察不得提前展示' }] },
      created_at: '2026-10-30T00:00:00.000Z',
    }
    const waiting = workflowView({
      run_id: 'run-1',
      stage: 'S02',
      status: 'needs_input',
      project_revision: 1,
      requirement_revision: 1,
      issue: {
        code: 'MISSING_INPUT',
        message: '请说明你要制作的商品图片及用途。',
        template_id: 'INTENT_UNCLEAR',
        params: {},
      },
      artifacts: [candidateReport],
      available_actions: [
        {
          id: 'confirm_reference_usage',
          label: '选择参考用途',
          kind: 'button',
          requires_text: false,
          text_label: null,
          requires_confirmation: false,
        },
      ],
    })
    await mountProject(project, waiting)

    expect(await screen.findByRole('heading', { name: '分析参考图' })).toBeVisible()
    expect(screen.getByText('等待补充')).toBeVisible()
    expect(screen.getByText('请说明你要制作的商品图片及用途。')).toBeVisible()
    expect(screen.getByRole('img', { name: '你的参考图' })).toBeVisible()
    expect(screen.queryByText(/秘密观察不得提前展示/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '参考图分析' })).not.toBeInTheDocument()

    harness!.fetch.json('POST', usagePath, workflowView({
      run_id: 'run-1',
      stage: 'S01',
      status: 'needs_input',
      project_revision: 1,
      requirement_revision: 1,
      issue: {
        code: 'MISSING_INPUT',
        message: '请说明你要制作的商品图片及用途。',
        template_id: 'INTENT_UNCLEAR',
        params: {},
      },
    }))
    const select = screen.getByLabelText('参考图用途') as HTMLSelectElement
    await harness!.user.selectOptions(select, 'style')
    await harness!.user.click(screen.getByRole('button', { name: '选择参考用途' }))
    const request = harness!.fetch.last('POST', usagePath)
    expect(request?.body).toEqual({
      expected_revision: 1,
      text: null,
      payload: { usage: 'style' },
    })
  })

  it('上传成功后立即请求独立分析，等待时仍只显示原图与阶段状态', async () => {
    const initial = sampleProject({
      id: PROJECT_A,
      revision: 3,
      prompt_revision: 3,
      confirmed_revision: 3,
      reference_asset: null,
      brief: sampleBrief({ reference_asset_id: null }),
    })
    const uploadedAsset = sampleAsset({ id: 'reference-new' })
    const uploaded = sampleProject({
      id: PROJECT_A,
      revision: 4,
      prompt_revision: null,
      confirmed_revision: null,
      brief_status: 'draft',
      reference_asset: uploadedAsset,
      brief: sampleBrief({ reference_asset_id: uploadedAsset.id, prompt: '' }),
    })
    const initialView = workflowView({
      project_revision: 3,
      available_actions: [
        {
          id: 'submit_request',
          label: '开始整理需求',
          kind: 'text',
          requires_text: true,
          text_label: '描述你想要的商品图',
          requires_confirmation: false,
        },
      ],
    })
    const afterUpload = workflowView({
      project_revision: 4,
      available_actions: [
        {
          id: 'analyze_reference',
          label: '分析参考图',
          kind: 'button',
          requires_text: false,
          text_label: null,
          requires_confirmation: false,
        },
      ],
    })
    const waiting = workflowView({
      run_id: 'run-s02',
      stage: 'S02',
      status: 'needs_input',
      project_revision: 4,
      requirement_revision: 4,
      issue: {
        code: 'MISSING_INPUT',
        message: '请说明你要制作的商品图片及用途。',
        template_id: 'INTENT_UNCLEAR',
        params: {},
      },
      artifacts: [
        {
          id: 'report-candidate',
          kind: 'reference_report',
          revision_no: 1,
          status: 'candidate',
          digest: 'digest-hidden',
          payload: { observations: [{ description: '未批准报告不得展示' }] },
          created_at: '2026-10-30T00:00:00.000Z',
        },
      ],
      available_actions: [
        {
          id: 'confirm_reference_usage',
          label: '选择参考用途',
          kind: 'button',
          requires_text: false,
          text_label: null,
          requires_confirmation: false,
        },
      ],
    })

    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [
        { method: 'GET', url: detailPath, responses: [{ body: detail(initial) }] },
        {
          method: 'GET',
          url: workflowPath,
          responses: [{ body: initialView }, { body: afterUpload }],
        },
        { method: 'POST', url: referencePath, responses: [{ body: uploaded }] },
        { method: 'POST', url: analyzePath, responses: [{ body: waiting }] },
      ],
    })
    await screen.findByRole('heading', { name: '春季托特包主图' })
    expect(await screen.findByText('尚未开始')).toBeVisible()

    const input = screen.getByLabelText('选择参考图文件')
    await harness.waitFor(() => expect(input).toBeEnabled())
    await fireEvent.change(input, { target: { files: [pngFile()] } })

    await harness!.waitFor(() => {
      expect(harness!.fetch.count('POST', analyzePath)).toBe(1)
    })
    expect(harness!.fetch.last('POST', analyzePath)?.body).toEqual({
      expected_revision: 4,
      text: null,
      payload: {},
    })
    expect(await screen.findByRole('heading', { name: '分析参考图' })).toBeVisible()
    expect(screen.getByRole('img', { name: '你的参考图' })).toBeVisible()
    expect(screen.queryByText(/未批准报告不得展示/)).not.toBeInTheDocument()
  })
})
