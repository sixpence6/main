import { requestJson } from './client'

export type WorkflowStatus =
  | 'none'
  | 'active'
  | 'needs_input'
  | 'blocked'
  | 'completed'
  | 'failed'

export interface WorkflowIssue {
  code: string
  message: string
  template_id: string | null
  params: Record<string, string>
}

export interface WorkflowArtifact {
  id: string
  kind: string
  revision_no: number
  status: 'candidate' | 'approved' | 'rejected' | 'superseded'
  digest: string
  payload: Record<string, unknown> | null
  created_at: string
}

export interface WorkflowApproval {
  id: string
  approval_type: string
  artifact_id: string | null
  artifact_digest: string
  approved_by: string
  requirement_snapshot_digest: string
  referenced_material_digest: string
  prompt_version: string
  rules_version: string
  model_fingerprint: string
  checks: Record<string, unknown>
  created_at: string
  revoked_at: string | null
  revocation_reason: string | null
}

export type WorkflowActionEffect = 'read_only' | 'write' | 'model_call'
export type WorkflowRetryMode = 'none' | 'manual_only'

export interface WorkflowAction {
  id: string
  label: string
  kind: 'button' | 'text' | 'confirm'
  requires_text: boolean
  text_label: string | null
  requires_confirmation: boolean
  effect?: WorkflowActionEffect
  read_only?: boolean
  requires_model_call?: boolean
  retry_mode?: WorkflowRetryMode
  automatic_retry?: boolean
}

export interface WorkflowLegacyItem {
  id: string
  kind: 'message' | 'generation' | 'asset'
  ref_id: string
  status: 'legacy_unreviewed' | 'approved'
  note: string | null
  reviewed_at: string | null
  created_at: string
}

export interface WorkflowGenerationJob {
  id: string
  status: string
  request_id: string
  submit_attempted: boolean
  result_asset_id: string | null
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface WorkflowDelivery {
  id: string
  status: 'approved' | 'published' | 'revoked'
  asset_id: string
  filename: string
  caption: string | null
  published_at: string | null
  created_at: string
}

export interface WorkflowUsage {
  daily_limit: number
  used: number
  remaining: number
  window_started_at: string | null
  window_expires_at: string | null
  personal_active: number
  personal_limit: number
  instance_active: number
  instance_limit: number
  project_active: number
  project_limit: number
}

export interface WorkflowIntent {
  id: string
  request_id: string
  status: 'accepted' | 'executing' | 'delivered' | 'failed' | 'abandoned' | 'closed'
  artifact_digest: string
  requirement_revision: number
  created_at: string
}

export interface WorkflowVersion {
  run_id: string
  sequence_no: number
  requirement_revision: number
  status: string
  generation_job_id: string | null
  asset_id: string | null
  filename: string | null
  width?: number | null
  height?: number | null
  caption: string | null
  published_at: string | null
  brief: Record<string, unknown> | null
  prompt: Record<string, unknown> | null
  quality: Record<string, unknown> | null
  can_download: boolean
  unavailable_reason?: string | null
  created_at: string
}

export type WorkflowPhase =
  | 'empty'
  | 'intake'
  | 'requirements'
  | 'prompt'
  | 'generation'
  | 'review'
  | 'delivery'
  | 'recovery'

export interface WorkflowProgress {
  phase: WorkflowPhase
  stage: string | null
  stage_label: string | null
  status: WorkflowStatus
  awaiting_user: boolean
  terminal: boolean
}

export type ConstraintStrength = 'hard' | 'soft' | 'not_applicable'

export interface WorkflowConstraint {
  constraint_id: string
  path: string
  label: string
  strength: ConstraintStrength
  source: string | null
  user_lock_basis: string | null
  rule_id: string | null
  value: unknown
}

export interface WorkflowTaskBinding {
  project_id: string
  run_id: string | null
  project_revision: number
  requirement_revision: number
  intent_id: string | null
  intent_status: string | null
  intent_requirement_revision: number | null
  generation_job_id: string | null
  generation_job_status: string | null
  confirmed_snapshot_digest: string | null
  execution_snapshot_digest: string | null
  delivery_id: string | null
  delivery_status: string | null
  current: boolean
}

export type WorkflowReasonCategory =
  | 'missing_information'
  | 'repairable_composition'
  | 'true_drift'
  | 'hard_failure'
  | 'content_rejection'
  | 'missing_evidence'
  | 'protocol_error'
  | 'technical_failure'
  | 'remote_unknown'
  | 'authorization_invalid'

export interface WorkflowSafeReason {
  category: WorkflowReasonCategory
  code: string
  message: string
  template_id: string | null
  params: Record<string, string>
  severity: 'needs_input' | 'blocked' | 'failed' | 'unknown'
  requires_user_input: boolean
  requires_new_version: boolean
  requires_evidence: boolean
  remote_unknown: boolean
  auto_retry: boolean
}

export type WorkflowResourceState =
  | 'none'
  | 'history_only'
  | 'pending'
  | 'published'
  | 'failed'
  | 'blocked'
  | 'unknown'
  | 'unavailable'

export interface WorkflowResourceView {
  current_run_id: string | null
  current_state: WorkflowResourceState
  current_version: WorkflowVersion | null
  history: WorkflowVersion[]
}

export interface WorkflowTaskState {
  progress: WorkflowProgress
  reason: WorkflowSafeReason | null
  binding: WorkflowTaskBinding
  constraints: WorkflowConstraint[]
  resource_state: WorkflowResourceView
}

export interface PromptPreviewView {
  id: string
  status: string
  request_digest: string
  request: Record<string, unknown>
  created_at: string
  can_confirm: boolean
  command_context: Record<string, unknown>
}

export interface ClarificationAnswer {
  question_id: string
  option_id: string
  requirement_revision: number
  message_id: string
}

export interface ConversationQuestion {
  question_id: string
  field: string
  text: string
  options: { id: string; label: string }[]
  requirement_revision: number
  message_id: string
}

export interface WorkflowView {
  conversation_question?: ConversationQuestion | null
  interaction_mode?: 'direct' | 'legacy'
  project_id: string
  run_id: string | null
  stage: string | null
  status: WorkflowStatus
  project_revision: number
  requirement_revision: number
  intent_route: string | null
  intent_action: string | null
  issue: WorkflowIssue | null
  artifacts: WorkflowArtifact[]
  approvals: WorkflowApproval[]
  available_actions: WorkflowAction[]
  legacy: WorkflowLegacyItem[]
  generation_job: WorkflowGenerationJob | null
  delivery: WorkflowDelivery | null
  prompt_preview?: PromptPreviewView | null
  execution_snapshot?: Record<string, unknown> | null
  execution_snapshot_digest?: string | null
  versions?: WorkflowVersion[]
  usage?: WorkflowUsage | null
  intent?: WorkflowIntent | null
  task_state?: WorkflowTaskState | null
  updated_at: string | null
}

export interface WorkflowActionInput {
  expected_revision: number
  text?: string | null
  payload?: Record<string, unknown> | null
}

export function getWorkflow(projectId: string, signal?: AbortSignal): Promise<WorkflowView> {
  return requestJson<WorkflowView>(
    `/api/projects/${encodeURIComponent(projectId)}/workflow`,
    { signal },
  )
}

export function runWorkflowAction(
  projectId: string,
  actionId: string,
  input: WorkflowActionInput,
): Promise<WorkflowView> {
  return requestJson<WorkflowView>(
    `/api/projects/${encodeURIComponent(projectId)}/workflow/actions/${encodeURIComponent(actionId)}`,
    { method: 'POST', body: JSON.stringify(input) },
  )
}
