import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import type {
  CapabilityStatus,
  ConfigCapabilities,
  ConfigStatus,
  GenerationGate,
  ImageGenerationConfigStatus,
  VisionConfigStatus,
} from '@/api/types'
import { modelStatus } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

function imageCapabilities(overrides: Partial<ConfigCapabilities> = {}): ConfigCapabilities {
  return {
    text_to_image: true,
    reference_image: true,
    asynchronous: true,
    supported_image_sizes: ['1024x1024', '1536x1536'],
    selected_image_size: '1024x1024',
    reference_mime_types: ['image/png', 'image/jpeg'],
    provider_max_reference_bytes: 5_000_000,
    effective_reference_max_bytes: 5_000_000,
    local_reference_max_bytes: 10_000_000,
    effective_reference_mime_types: ['image/png', 'image/jpeg'],
    usable_image_size: '1024x1024',
    ...overrides,
  }
}

function capability(overrides: Partial<CapabilityStatus> = {}): CapabilityStatus {
  return {
    capability: 'TEXT',
    configured: false,
    ready: false,
    adapter_id: null,
    model: null,
    missing_fields: [],
    issues: [],
    timeout_seconds: 60,
    verification: 'not_checked',
    model_fingerprint: null,
    declared_capabilities: [],
    ...overrides,
  }
}

function gate(missing: Array<'TEXT' | 'VISION' | 'IMAGE_GEN'>): GenerationGate {
  return {
    allowed: missing.length === 0,
    missing_capabilities: missing,
    issues: missing.map((name) => ({
      code: `${name}_NOT_READY`,
      message: `${name} 能力未就绪`,
    })),
    verification: 'not_checked',
  }
}

function visionCapability(overrides: Partial<VisionConfigStatus> = {}): VisionConfigStatus {
  return {
    ...capability({
      capability: 'VISION',
      configured: true,
      ready: true,
      adapter_id: 'openai-chat',
      model: 'vision-model',
      model_fingerprint: 'openai-chat:vision-model:beefcafe',
      declared_capabilities: ['text', 'image_input'],
    }),
    endpoint_source: 'shared_text',
    credential_source: 'shared_text',
    ...overrides,
  } as VisionConfigStatus
}

function imageGenerationStatus(
  overrides: Partial<ImageGenerationConfigStatus> = {},
): ImageGenerationConfigStatus {
  return {
    ...capability({
      capability: 'IMAGE_GEN',
      configured: true,
      ready: true,
      adapter_id: 'openai-images',
      model: 'image-model',
      timeout_seconds: 300,
      model_fingerprint: 'openai-images:image-model:feedface',
      declared_capabilities: ['text_to_image', 'reference_image', 'asynchronous'],
    }),
    capabilities: imageCapabilities(),
    ...overrides,
  } as ImageGenerationConfigStatus
}

function baseConfig(): ConfigStatus {
  const text = capability({
    configured: true,
    ready: true,
    adapter_id: 'openai-chat',
    model: 'text-model',
    model_fingerprint: 'openai-chat:text-model:deadbeef',
    declared_capabilities: ['text'],
  })
  const vision = visionCapability()
  const image = imageGenerationStatus()
  return {
    llm: modelStatus({
      configured: true,
      ready: true,
      adapter_id: 'openai-chat',
      model: 'text-model',
    }),
    visual: {
      ...modelStatus({
        configured: true,
        ready: true,
        adapter_id: 'openai-images',
        model: 'image-model',
        timeout_seconds: 300,
      }),
      capabilities: image.capabilities,
    },
    text,
    vision,
    image_generation: image,
    generation: gate([]),
  }
}

function emptyConfig(): ConfigStatus {
  const text = capability({
    missing_fields: ['adapter', 'base_url', 'api_key', 'model'],
  })
  const missingAll = ['TEXT', 'VISION', 'IMAGE_GEN'] as const
  return {
    llm: modelStatus({ missing_fields: ['adapter', 'base_url', 'api_key', 'model'] }),
    visual: {
      ...modelStatus({ timeout_seconds: 300 }),
      capabilities: imageCapabilities({
        text_to_image: null,
        reference_image: null,
        asynchronous: null,
        supported_image_sizes: null,
        selected_image_size: null,
        reference_mime_types: null,
        provider_max_reference_bytes: null,
        effective_reference_max_bytes: 10_000_000,
        local_reference_max_bytes: 10_000_000,
        effective_reference_mime_types: null,
        usable_image_size: null,
      }),
    },
    text,
    vision: visionCapability({
      configured: false,
      ready: false,
      adapter_id: null,
      model: null,
      missing_fields: ['adapter', 'base_url', 'api_key', 'vision_model'],
      model_fingerprint: null,
      declared_capabilities: [],
      endpoint_source: 'missing',
      credential_source: 'missing',
    }),
    image_generation: imageGenerationStatus({
      configured: false,
      ready: false,
      adapter_id: null,
      model: null,
      timeout_seconds: 300,
      missing_fields: ['adapter', 'base_url', 'api_key', 'model'],
      model_fingerprint: null,
      declared_capabilities: [],
      capabilities: imageCapabilities({
        text_to_image: null,
        reference_image: null,
        asynchronous: null,
        supported_image_sizes: null,
        selected_image_size: null,
        reference_mime_types: null,
        provider_max_reference_bytes: null,
        effective_reference_max_bytes: 10_000_000,
        local_reference_max_bytes: 10_000_000,
        effective_reference_mime_types: null,
        usable_image_size: null,
      }),
    }),
    generation: gate([...missingAll]),
  }
}

async function openModelDrawer(): Promise<HTMLElement> {
  await harness!.user.click(screen.getByRole('button', { name: '模型状态' }))
  return screen.findByRole('dialog', { name: '模型状态' })
}

describe('T02 TEXT/VISION/IMAGE_GEN 能力与配置状态', () => {
  it('空配置也分别展示三种能力、真实等待上限和明确的付费生成门禁', async () => {
    harness = await mountWorkspace({ config: emptyConfig() })
    const dialog = await openModelDrawer()

    const text = within(dialog).getByRole('region', { name: '需求整理模型（LLM）' })
    expect(within(text).getByText('能力：TEXT（文字处理）')).toBeVisible()
    expect(within(text).getByText('60 秒')).toBeVisible()

    const vision = within(dialog).getByRole('region', { name: '视觉理解模型（VISION）' })
    expect(within(vision).getByText('能力：VISION（视觉理解）')).toBeVisible()
    expect(within(vision).getByText('60 秒')).toBeVisible()
    expect(within(vision).getByText('vision_model')).toBeVisible()

    const image = within(dialog).getByRole('region', { name: '图片生成模型（VISUAL）' })
    expect(within(image).getByText('能力：IMAGE_GEN（图片生成）')).toBeVisible()
    expect(within(image).getByText('300 秒')).toBeVisible()

    const blocked = within(dialog).getByRole('status', { name: '付费生成门禁' })
    expect(blocked).toHaveTextContent('付费生成门禁：禁止')
    expect(blocked).toHaveTextContent(
      '缺少能力：文字处理（TEXT）、视觉理解（VISION）、图片生成（IMAGE_GEN）',
    )
    expect(blocked).toHaveTextContent('不会发起任何模型调用')
    expect(dialog).not.toHaveTextContent('openai-compatible')
  })

  it('共享 TEXT 与独立配置的 VISION 都明确显示地址和凭据来源', async () => {
    const shared = baseConfig()
    harness = await mountWorkspace({ config: shared })
    let dialog = await openModelDrawer()
    let vision = within(dialog).getByRole('region', { name: '视觉理解模型（VISION）' })
    expect(within(vision).getByText('共享 TEXT 地址')).toBeVisible()
    expect(within(vision).getByText('共享 TEXT 凭据')).toBeVisible()
    expect(within(vision).getByText('vision-model')).toBeVisible()
    expect(within(vision).getByText('openai-chat:vision-model:beefcafe')).toBeVisible()

    harness.dispose()
    const independent = baseConfig()
    independent.vision = visionCapability({
      endpoint_source: 'independent',
      credential_source: 'independent',
      model_fingerprint: 'openai-chat:vision-model:independent',
    })
    harness = await mountWorkspace({ config: independent })
    dialog = await openModelDrawer()
    vision = within(dialog).getByRole('region', { name: '视觉理解模型（VISION）' })
    expect(within(vision).getByText('独立配置地址')).toBeVisible()
    expect(within(vision).getByText('独立配置凭据')).toBeVisible()
  })

  it('TEXT 与 IMAGE_GEN 就绪但缺少 VISION 时，界面明确禁止付费生成', async () => {
    const config = baseConfig()
    config.vision = visionCapability({
      configured: false,
      ready: false,
      model: null,
      missing_fields: ['vision_model'],
      model_fingerprint: null,
      declared_capabilities: [],
      endpoint_source: 'shared_text',
      credential_source: 'shared_text',
    })
    config.generation = gate(['VISION'])
    harness = await mountWorkspace({ config })
    const dialog = await openModelDrawer()

    const vision = within(dialog).getByRole('region', { name: '视觉理解模型（VISION）' })
    expect(within(vision).getByText('配置不完整')).toBeVisible()
    expect(within(vision).getByText('vision_model')).toBeVisible()
    const image = within(dialog).getByRole('region', { name: '图片生成模型（VISUAL）' })
    expect(within(image).getByText('配置完整')).toBeVisible()

    const blocked = within(dialog).getByRole('status', { name: '付费生成门禁' })
    expect(blocked).toHaveTextContent('付费生成门禁：禁止')
    expect(blocked).toHaveTextContent('缺少能力：视觉理解（VISION）')
    expect(blocked).toHaveTextContent('不会发起任何模型调用')
    expect(dialog).not.toHaveTextContent('生成成功')
  })

  it('展示适配器声明与更严格的本地/适配器实际限制，不伪造尺寸', async () => {
    const config = baseConfig()
    config.image_generation = imageGenerationStatus({
      capabilities: imageCapabilities({
        supported_image_sizes: ['1024x1024', '1536x1536'],
        selected_image_size: '1024x1024',
        reference_mime_types: ['image/png', 'image/webp'],
        effective_reference_mime_types: ['image/png'],
        provider_max_reference_bytes: 3_000_000,
        effective_reference_max_bytes: 3_000_000,
        local_reference_max_bytes: 10_000_000,
        usable_image_size: '1024x1024',
      }),
    })
    harness = await mountWorkspace({ config })
    const dialog = await openModelDrawer()
    const image = within(dialog).getByRole('region', { name: '图片生成模型（VISUAL）' })

    expect(within(image).getByText('image/png、image/webp')).toBeVisible()
    expect(within(image).getByText('image/png')).toBeVisible()
    expect(within(image).getAllByText('3,000,000 字节').length).toBeGreaterThanOrEqual(2)
    expect(within(image).getByText('10,000,000 字节')).toBeVisible()
    expect(within(image).getByText('1024x1024')).toBeVisible()
    expect(within(image).getByText('openai-images:image-model:feedface')).toBeVisible()
  })

  it('诊断信息过滤敏感内容、保持未验证状态且不产生写请求', async () => {
    const config = emptyConfig()
    config.vision = visionCapability({
      configured: false,
      ready: false,
      model: null,
      missing_fields: ['vision_model'],
      model_fingerprint: null,
      issues: [
        {
          code: 'PROVIDER_ERROR',
          message: 'sk-secret Bearer raw https://vision.internal.example/v1',
        },
      ],
    })
    harness = await mountWorkspace({ config })
    const dialog = await openModelDrawer()

    expect(dialog).toHaveTextContent('问题详情已省略（出于安全原因）')
    expect(dialog).not.toHaveTextContent('sk-secret')
    expect(dialog).not.toHaveTextContent('vision.internal.example')
    expect(dialog).not.toHaveTextContent('已验证')
    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('GET', '/api/config/status')).toBe(1)
  })
})
