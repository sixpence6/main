import { afterEach, describe, expect, it } from 'vitest'
import { screen } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import {
  PROJECT_A,
  configuredConfig,
  sampleAsset,
  sampleBrief,
  sampleGeneration,
  sampleProject,
} from './support/fixtures'
import type { GenerationView, ProjectDetailResponse } from '@/api/types'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

const detailPath = '/api/projects/' + PROJECT_A

function expiredGeneration(): GenerationView {
  const asset = sampleAsset({ id: 'asset-expired', kind: 'generated', availability: 'available' })
  return sampleGeneration({
    id: 'generation-expired',
    version_no: 1,
    status: 'succeeded',
    stage: 'done',
    result_asset_id: asset.id,
    result_asset: asset,
    retention_status: 'expired',
    retention_expires_at: '2026-10-22T06:00:30.000Z',
  })
}

function detail(generation: GenerationView): ProjectDetailResponse {
  return {
    project: sampleProject({
      id: PROJECT_A,
      brief_status: 'confirmed',
      revision: 3,
      prompt_revision: 3,
      confirmed_revision: 3,
      brief: sampleBrief(),
    }),
    messages: [],
    generations: [generation],
  }
}

describe('T20 保留期', () => {
  it('到期候选显示固定过期提示，不展示图片、缩略图或下载入口，也不触发生成', async () => {
    harness = await mountWorkspace({
      config: configuredConfig(),
      viewportWidth: 1440,
      url: '/?project=' + PROJECT_A,
      routes: [{ method: 'GET', url: detailPath, responses: [{ body: detail(expiredGeneration()) }] }],
    })

    expect(await screen.findByRole('heading', { name: '查看结果' })).toBeVisible()
    expect(await screen.findByText('结果已过保留期')).toBeVisible()
    expect(screen.getByText('该候选图已按保留期清理，无法预览或下载。继续编辑不会自动重新生成。')).toBeVisible()
    expect(screen.queryByRole('img', { name: '生成结果图' })).toBeNull()
    expect(screen.queryByRole('img', { name: '第 1 版缩略图' })).toBeNull()
    expect(screen.queryByRole('link', { name: '下载当前图片' })).toBeNull()
    expect(screen.queryByText('结果文件缺失')).toBeNull()
    expect(harness.fetch.count('POST', '/api/projects/' + PROJECT_A + '/generations')).toBe(0)
  })
})
