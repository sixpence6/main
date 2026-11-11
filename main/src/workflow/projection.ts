import type { ProjectView } from '@/api/types'
import type {
  WorkflowApproval,
  WorkflowArtifact,
  WorkflowVersion,
  WorkflowView,
} from '@/api/workflow'

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

function referenceBindingIsStale(
  view: WorkflowView | null | undefined,
  brief: WorkflowArtifact,
  project: ProjectView | null | undefined,
): boolean {
  if (!project) return false
  if (view && view.project_revision !== project.revision) return true
  const expected = brief.payload?.reference_asset_id
  const expectedId = typeof expected === 'string' && expected ? expected : null
  const currentId = project.reference_asset?.id ?? null
  if (expectedId !== currentId) return true
  if (project.reference_asset?.availability === 'missing') return true
  const snapshot = confirmationSnapshotOf(activeConfirmationOf(view, brief))
  if (snapshot && 'reference_asset_id' in snapshot && snapshot.reference_asset_id !== currentId) {
    return true
  }
  return false
}

export function confirmationStateOf(
  view: WorkflowView | null | undefined,
  project?: ProjectView | null,
): ConfirmationState {
  const brief = latestFinalBrief(view)
  if (!brief) return 'none'
  if (brief.status !== 'approved' || !brief.payload) return 'reviewing'
  const approval = activeConfirmationOf(view, brief)
  const snapshot = confirmationSnapshotOf(approval)
  const referenceStale = referenceBindingIsStale(view, brief, project)
  if (
    approval &&
    !referenceStale &&
    snapshot?.artifact_digest === brief.digest &&
    snapshot?.project_revision === view?.project_revision &&
    snapshot?.requirement_revision === view?.requirement_revision
  ) {
    return 'confirmed'
  }
  if (confirmationApprovalsOf(view).length > 0 || referenceStale) return 'stale'
  return 'pending'
}

export function currentPublishedVersion(
  project: ProjectView | null | undefined,
  view: WorkflowView | null | undefined,
): WorkflowVersion | null {
  if (!project || !view) return null
  if (view.status !== 'completed') return null
  if (project && view.project_revision < project.revision) return null
  if (view.delivery?.status !== 'published') return null
  const deliveryAsset = view.delivery.asset_id
  const versions = view.versions ?? []
  return (
    versions.find((version) => (
      version.run_id === view.run_id &&
      version.status === 'published' &&
      version.can_download &&
      version.asset_id === deliveryAsset &&
      Boolean(version.filename)
    )) ?? null
  )
}

export function currentDeliveryBlocked(
  project: ProjectView | null | undefined,
  view: WorkflowView | null | undefined,
): boolean {
  if (!project || !view) return false
  if (view.status !== 'completed') return false
  if (project && view.project_revision < project.revision) return false
  return view.delivery?.status === 'published' && currentPublishedVersion(project, view) === null
}

export function userStateOf(
  project: ProjectView | null | undefined,
  view: WorkflowView | null | undefined,
): UserState {
  if (currentPublishedVersion(project, view)) return 'u3'
  if (currentDeliveryBlocked(project, view)) return 'u1'
  if (view?.intent && ['accepted', 'executing'].includes(view.intent.status)) return 'u2'
  const confirmation = confirmationStateOf(view, project)
  if (confirmation === 'pending' || confirmation === 'confirmed') return 'u2'
  return 'u1'
}

export function deliveryUnavailableText(view: WorkflowView): string {
  const current = view.versions?.find((version) => version.run_id === view.run_id)
  switch (current?.unavailable_reason) {
    case 'ASSET_FILE_MISSING':
      return '当前图片文件缺失，暂时无法预览或下载。'
    case 'DIGEST_MISMATCH':
      return '当前图片文件与交付记录不一致，暂时无法预览或下载。'
    case 'ASSET_REJECTED':
      return '当前图片已被标记为不可用，暂时无法预览或下载。'
    default:
      return '当前图片暂时无法读取，请稍后刷新；仍无法打开时请联系维护人员。'
  }
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
  const currentVersion = currentPublishedVersion(project, view)
  if (currentVersion) {
    const feedbackAvailable = view.available_actions.some(
      (action) => action.id === 'request_new_version',
    )
    return {
      title: '当前交付已发布',
      detail: '当前第 ' + currentVersion.requirement_revision + ' 版已发布。可以查看或下载当前交付，切换历史预览，或基于本次交付反馈新版本。历史图片仅作历史预览，不代表当前需求状态。',
      actionId: feedbackAvailable ? 'request_new_version' : null,
    }
  }
  if (view && currentDeliveryBlocked(project, view)) {
    return {
      title: '当前交付不可用',
      detail: deliveryUnavailableText(view),
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
  const brief = latestFinalBrief(view)
  if (brief && (brief.status !== 'approved' || !brief.payload)) {
    return {
      title: '等待独立审查',
      detail: '最新需求卡正在独立审查，通过前不能确认或生成图片，也不会展示未批准的报告内容。',
      actionId: null,
    }
  }
  const confirmation = confirmationStateOf(view, project)
  if (confirmation === 'pending') {
    return {
      title: '核对并确认当前需求卡',
      detail: '当前需求卡已通过独立审查。核对商品、参考用途和尺寸后，点击确认按钮生成图片提示词；你确认提示词后才会生成图片。',
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
  if (view.stage === 'S04') {
    return {
      title: '正在整理创意方向',
      detail: '需求信息已对齐，系统正在整理创意方向；完成后会继续生成视觉方案和可确认的需求卡。',
      actionId: null,
    }
  }
  if (view.stage === 'S05') {
    return {
      title: '正在整理视觉方案',
      detail: '创意方向已就绪，系统正在整理视觉方案；完成后会生成可确认的需求卡。',
      actionId: null,
    }
  }
  if (view.stage === 'S06') {
    return {
      title: '正在整理需求卡',
      detail: '系统正在整理可确认的需求卡，请稍候；需求卡就绪后会显示确认按钮。',
      actionId: null,
    }
  }
  return {
    title: '继续完善当前需求',
    detail: '在左侧补充商品、用途、风格或文字要求；每次修改都会对齐当前需求版本。需求卡通过独立审查后才能确认。',
    actionId: null,
  }
}
