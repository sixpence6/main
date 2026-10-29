import type {
  AssetView,
  Brief,
  ConfigStatus,
  GenerationView,
  MessageView,
  ModelConfigStatus,
  ProjectSummary,
  ProjectView,
  VisualConfigStatus,
} from '@/api/types'

export const PROJECT_A = '11111111-1111-4111-8111-111111111111'
export const PROJECT_B = '22222222-2222-4222-8222-222222222222'
export const PROJECT_C = '33333333-3333-4333-8333-333333333333'

export function modelStatus(overrides: Partial<ModelConfigStatus> = {}): ModelConfigStatus {
  return {
    configured: false,
    ready: false,
    adapter_id: null,
    model: null,
    missing_fields: [],
    issues: [],
    timeout_seconds: 60,
    verification: 'not_checked',
    ...overrides,
  }
}

export function visualStatus(overrides: Partial<VisualConfigStatus> = {}): VisualConfigStatus {
  return {
    ...modelStatus(),
    ...overrides,
    capabilities: {
      text_to_image: null,
      reference_image: null,
      asynchronous: null,
      supported_image_sizes: null,
      selected_image_size: null,
      reference_mime_types: null,
      provider_max_reference_bytes: null,
      effective_reference_max_bytes: 10_000_000,
      ...(overrides.capabilities ?? {}),
    },
  }
}

export function emptyConfig(): ConfigStatus {
  return {
    llm: modelStatus({
      missing_fields: ['LLM_API_KEY', 'LLM_MODEL'],
    }),
    visual: visualStatus({
      missing_fields: ['VISUAL_API_KEY', 'VISUAL_MODEL'],
    }),
  }
}

export function unknownCapabilitiesConfig(): ConfigStatus {
  return {
    llm: modelStatus({
      configured: true,
      ready: true,
      adapter_id: 'openai-compatible',
      model: 'text-model',
    }),
    visual: visualStatus({
      configured: true,
      ready: true,
      adapter_id: 'image-adapter',
      model: 'image-model',
    }),
  }
}

export function configuredConfig(): ConfigStatus {
  return {
    llm: modelStatus({
      configured: true,
      ready: true,
      adapter_id: 'openai-compatible',
      model: 'text-model',
      timeout_seconds: 90,
    }),
    visual: visualStatus({
      configured: true,
      ready: true,
      adapter_id: 'image-adapter',
      model: 'image-model',
      timeout_seconds: 180,
      capabilities: {
        text_to_image: true,
        reference_image: true,
        asynchronous: true,
        supported_image_sizes: ['1024x1024', '1536x1536'],
        selected_image_size: '1024x1024',
        reference_mime_types: ['image/png', 'image/jpeg'],
        provider_max_reference_bytes: 5_000_000,
        effective_reference_max_bytes: 5_000_000,
      },
    }),
  }
}

export function malformedConfig(): Record<string, unknown> {
  return {
    llm: { configured: false },
    visual: { configured: false },
  }
}


export function sampleBrief(overrides: Partial<Brief> = {}): Brief {
  return {
    product_description: '米色亚麻托特包',
    purpose: '电商主图',
    style: '简洁暖色，突出材质',
    composition: '居中构图，浅米色背景',
    text_content: '',
    image_size: '1024x1024',
    reference_asset_id: null,
    prompt: '为米色亚麻托特包生成一张简洁主图，浅米色背景，居中构图。',
    ...overrides,
  }
}

export function sampleProject(overrides: Partial<ProjectView> = {}): ProjectView {
  return {
    id: PROJECT_A,
    title: '春季托特包主图',
    revision: 3,
    prompt_revision: 3,
    confirmed_revision: 3,
    brief_status: 'review',
    brief: sampleBrief(),
    reference_asset: null,
    brief_error: null,
    created_at: '2026-10-20T02:00:00.000Z',
    updated_at: '2026-10-21T06:30:00.000Z',
    ...overrides,
  }
}

export function sampleSummary(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    id: PROJECT_A,
    title: '春季托特包主图',
    brief_status: 'review',
    latest_generation_status: null,
    updated_at: '2026-10-21T06:30:00.000Z',
    ...overrides,
  }
}

export function sampleMessage(overrides: Partial<MessageView> = {}): MessageView {
  return {
    id: 'message-1',
    sequence_no: 1,
    role: 'user',
    content: '做一个米色亚麻托特包的电商主图',
    project_revision: 1,
    brief_snapshot: null,
    created_at: '2026-10-20T02:01:00.000Z',
    ...overrides,
  }
}

export function sampleGeneration(overrides: Partial<GenerationView> = {}): GenerationView {
  return {
    id: 'generation-1',
    project_id: PROJECT_A,
    version_no: 1,
    request_id: 'request-1',
    confirmed_revision: 3,
    status: 'succeeded',
    stage: 'done',
    brief_snapshot: sampleBrief(),
    prompt: sampleBrief().prompt,
    reference_asset_id: null,
    adapter_id: 'image-adapter',
    model: 'image-model',
    remote_task_id: 'remote-1',
    result_asset_id: 'asset-1',
    result_asset: null,
    error: null,
    can_retrieve_result: false,
    retrieval_unavailable_reason: null,
    created_at: '2026-10-21T06:00:00.000Z',
    started_at: '2026-10-21T06:00:01.000Z',
    completed_at: '2026-10-21T06:00:30.000Z',
    ...overrides,
  }
}

export function sampleAsset(overrides: Partial<AssetView> = {}): AssetView {
  return {
    id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    kind: 'reference',
    mime_type: 'image/png',
    width: 1024,
    height: 1024,
    byte_size: 234_567,
    created_at: '2026-10-20T02:00:00.000Z',
    availability: 'available',
    ...overrides,
  }
}
