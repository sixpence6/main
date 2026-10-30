export type BriefStatus = 'draft' | 'organizing' | 'needs_input' | 'review' | 'confirmed'
export type GenerationStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'unknown'
export type AssetKind = 'reference' | 'generated'
export type AssetReviewStatus =
  | 'reference'
  | 'quarantined'
  | 'approved'
  | 'rejected'
  | 'legacy_unreviewed'
export type GenerationStage =
  | 'waiting_slot'
  | 'submitting'
  | 'waiting_remote'
  | 'fetching_result'
  | 'done'

export interface AssetView {
  id: string
  kind: AssetKind
  mime_type: string
  width: number
  height: number
  byte_size: number
  created_at: string
  availability: 'available' | 'missing'
  review_status: AssetReviewStatus
}

export interface BriefFields {
  product_description: string
  purpose: string
  style: string
  composition: string
  text_content: string
}

export interface Brief extends BriefFields {
  image_size: string | null
  reference_asset_id: string | null
  prompt: string
}

export type PatchBriefRequest = Partial<BriefFields> & {
  expected_revision: number
}

export interface ConfirmBriefRequest {
  revision: number
}

export interface ProjectView {
  id: string
  title: string
  revision: number
  prompt_revision: number | null
  confirmed_revision: number | null
  brief_status: BriefStatus
  brief: Brief
  reference_asset: AssetView | null
  brief_error: ApiIssue | null
  legacy_unreviewed: boolean
  legacy_notice: string | null
  created_at: string
  updated_at: string
}

export interface MessageView {
  id: string
  sequence_no: number
  role: 'user' | 'assistant' | 'system'
  content: string
  project_revision: number
  brief_snapshot: Brief | null
  legacy_unreviewed: boolean
  legacy_notice: string | null
  created_at: string
}

export type GenerationRetentionStatus = 'active' | 'preserved' | 'expired'

export interface GenerationView {
  id: string
  project_id: string
  version_no: number
  request_id: string
  confirmed_revision: number
  status: GenerationStatus
  stage: GenerationStage
  brief_snapshot: Brief | null
  prompt: string
  reference_asset_id: string | null
  adapter_id: string
  model: string
  remote_task_id: string | null
  result_asset_id: string | null
  result_asset: AssetView | null
  error: ApiIssue | null
  can_retrieve_result: boolean
  retrieval_unavailable_reason: string | null
  retention_status: GenerationRetentionStatus | null
  retention_expires_at: string | null
  legacy_unreviewed: boolean
  legacy_notice: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface SubmitGenerationRequest {
  request_id: string
  confirmed_revision: number
  acknowledge_unknown?: boolean
}

export interface GenerationSubmitResponse {
  generation: GenerationView
}

export interface ProjectSummary {
  id: string
  title: string | null
  brief_status: BriefStatus
  latest_generation_status: GenerationStatus | null
  legacy_unreviewed: boolean
  legacy_notice: string | null
  updated_at: string
}

export interface ProjectListResponse {
  items: ProjectSummary[]
  next_cursor: string | null
}

export interface ProjectDetailResponse {
  project: ProjectView
  messages: MessageView[]
  generations: GenerationView[]
}

export interface ApiIssue {
  code: string
  message: string
}

export type CapabilityName = 'TEXT' | 'VISION' | 'IMAGE_GEN'

export interface ConfigCapabilities {
  text_to_image: boolean | null
  reference_image: boolean | null
  asynchronous: boolean | null
  supported_image_sizes: string[] | null
  selected_image_size: string | null
  reference_mime_types: string[] | null
  provider_max_reference_bytes: number | null
  effective_reference_max_bytes: number | null
  local_reference_max_bytes: number | null
  effective_reference_mime_types: string[] | null
  usable_image_size: string | null
}

export interface ModelConfigStatus {
  configured: boolean
  ready: boolean
  adapter_id: string | null
  model: string | null
  missing_fields: string[]
  issues: ApiIssue[]
  timeout_seconds: number
  verification: 'not_checked'
}

export interface CapabilityStatus extends ModelConfigStatus {
  capability: CapabilityName
  model_fingerprint: string | null
  declared_capabilities: string[]
}

export interface VisionConfigStatus extends CapabilityStatus {
  endpoint_source: 'shared_text' | 'independent' | 'missing'
  credential_source: 'shared_text' | 'independent' | 'missing'
}

export interface ImageGenerationConfigStatus extends CapabilityStatus {
  capabilities: ConfigCapabilities
}

export interface GenerationGate {
  allowed: boolean
  missing_capabilities: CapabilityName[]
  issues: ApiIssue[]
  verification: 'not_checked'
}

export interface VisualConfigStatus extends ModelConfigStatus {
  capabilities: ConfigCapabilities
}

export interface ConfigStatus {
  llm: ModelConfigStatus
  visual: VisualConfigStatus
  text: CapabilityStatus
  vision: VisionConfigStatus
  image_generation: ImageGenerationConfigStatus
  generation: GenerationGate
}

export interface HealthStatus {
  status: 'ok'
}

export interface ApiErrorBody {
  error: ApiIssue
}

export interface MessageCreateResponse {
  project: ProjectView
  user_message: MessageView
  assistant_message: MessageView
}
