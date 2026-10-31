import type { ProjectView } from '@/api/types'
import type { WorkflowApproval, WorkflowArtifact, WorkflowView } from '@/api/workflow'

export type ConfirmationState = 'none' | 'reviewing' | 'pending' | 'confirmed' | 'stale'
export type UserState = 'u1' | 'u2' | 'u3'

export interface WorkflowNextStep {
  title: string
  detail: string
  actionId: string | null
}

export function latestFinalBrief(view: WorkflowView | null | undefined): WorkflowArtifact | null {
  if (!view) return null
  let latest: WorkflowArtifact | null = null
  for (const artifact of view.artifacts) {
    if (artifact.kind !== 'final_brief') continue
    if (!latest || artifact.revision_no > latest.revision_no) latest = artifact
  }
  return latest
}

export function confirmationApprovalsOf(view: WorkflowView | null | undefined): WorkflowApproval[] {
  if (!view) return []
  return view.approvals
    .filter((approval) => approval.approval_type === 'requirements_card')
    .slice()
    .sort((left, right) => left.created_at.localeCompare(right.created_at))
}

function activeConfirmationOf(
  view: WorkflowView | null | undefined,
  brief: WorkflowArtifact | null,
): WorkflowApproval | null {
  const digest = brief?.digest
  if (!digest) return null
  const approvals = confirmationApprovalsOf(view)
  for (let index = approvals.length - 1; index >= 0; index -= 1) {
    const approval = approvals[index]
    if (approval.revoked_at === null && approval.artifact_digest === digest) return approval
  }
  return null
}

function confirmationSnapshotOf(approval: WorkflowApproval | null): Record<string, unknown> | null {
  const snapshot = approval?.checks?.confirmation
  return snapshot && typeof snapshot === 'object' ? (snapshot as Record<string, unknown>) : null
}

export function confirmationStateOf(view: WorkflowView | null | undefined): ConfirmationState {
  const brief = latestFinalBrief(view)
  if (!brief) return 'none'
  if (brief.status !== 'approved' || !brief.payload) return 'reviewing'
  const snapshot = confirmationSnapshotOf(activeConfirmationOf(view, brief))
  if (
    activeConfirmationOf(view, brief) &&
    snapshot?.artifact_digest === brief.digest &&
    snapshot?.project_revision === view?.project_revision &&
    snapshot?.requirement_revision === view?.requirement_revision
  ) {
    return 'confirmed'
  }
  if (confirmationApprovalsOf(view).length > 0) return 'stale'
  return 'pending'
}

export function userStateOf(
  project: ProjectView | null | undefined,
  view: WorkflowView | null | undefined,
): UserState {
  if (
    project &&
    view?.delivery?.status === 'published' &&
    view.project_revision === project.revision
  ) {
    return 'u3'
  }
  if (view?.intent && ['accepted', 'executing'].includes(view.intent.status)) return 'u2'
  const confirmation = confirmationStateOf(view)
  if (confirmation === 'pending' || confirmation === 'confirmed') return 'u2'
  return 'u1'
}

export function nextStepForWorkflow(
  project: ProjectView | null | undefined,
  view: WorkflowView | null | undefined,
): WorkflowNextStep {
  if (!project) {
    return {
      title: '描述商品与用途',
      detail: '在左侧输入商品、用途、画面风格和文字要求；发送后会整理当前需求卡。未发送内容只保留在当前页面。',
      actionId: null,
    }
  }
  if (!view?.run_id) {
    return {
      title: '先描述商品与用途',
      detail: '提交后会开始范围判断并整理当前需求卡；在需求卡通过独立审查前不能确认或生成图片。',
      actionId: null,
    }
  }
  if (view.issue) {
    return {
      title: '当前问题需要处理',
      detail: '请查看下方流程提示，并按可用操作处理；系统不会自动重发模型调用，也不会把未知结果当成未受理。',
      actionId: view.available_actions[0]?.id ?? null,
    }
  }
  if (view.delivery?.status === 'published' && view.project_revision === project.revision) {
    return {
      title: '当前交付已发布',
      detail: '可以查看或下载当前交付，也可以基于当前需求反馈新版本。历史图片仅作历史预览，不代表当前需求状态。',
      actionId: null,
    }
  }
  const brief = latestFinalBrief(view)
  if (brief && (brief.status !== 'approved' || !brief.payload)) {
    return {
      title: '等待独立审查',
      detail: '最新需求卡正在独立审查，通过前不能确认或生成图片，也不会展示未批准的报告内容。',
      actionId: null,
    }
  }
  const confirmation = confirmationStateOf(view)
  if (confirmation === 'pending') {
    return {
      title: '核对并确认当前需求卡',
      detail: '当前需求卡已通过独立审查。核对商品、参考用途和尺寸后，点击确认按钮开始生成；每次只生成一张图。',
      actionId: 'confirm_requirements',
    }
  }
  if (confirmation === 'confirmed' || view.intent) {
    return {
      title: '生成流程处理中',
      detail: '本次授权已受理，下方展示真实编制、生成、审查或恢复状态。重复点击不会再次计数，也不会自动重发。',
      actionId: null,
    }
  }
  if (confirmation === 'stale') {
    return {
      title: '需要重新确认需求',
      detail: '需求内容、参考图或版本已经变化，原确认不能继续使用。请核对最新需求卡后重新确认。',
      actionId: null,
    }
  }
  return {
    title: '继续完善当前需求',
    detail: '在左侧补充商品、用途、风格或文字要求；每次修改都会对齐当前需求版本。需求卡通过独立审查后才能确认。',
    actionId: null,
  }
}
