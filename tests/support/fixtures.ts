import type { ConfigStatus, ModelConfigStatus, VisualConfigStatus } from '@/api/types'

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
