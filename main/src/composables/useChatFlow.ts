import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { ApiError, api } from '@/api/client'
import { sessionRevision } from '@/auth/session'
import {
  runWorkflowAction,
  type PromptPreviewView,
  type WorkflowAction,
  type WorkflowArtifact,
} from '@/api/workflow'
import type { MessageCreateResponse, StagedMessageAttachmentView } from '@/api/types'
import { useWorkspace } from '@/composables/useWorkspace'
import { useWorkflow } from '@/composables/useWorkflow'

interface PendingAttachment {
  file: File
  objectUrl: string | null
  previewUrl: string
  staged: StagedMessageAttachmentView | null
  stagedProjectId: string | null
}

interface ImageHistoryPrompt {
  positive: string
  negative: string
}

interface ImageHistoryItem {
  runId: string
  sequenceNo: number
  requirementRevision: number
  assetId: string | null
  width: number | null
  height: number | null
  caption: string
  publishedAt: string | null
  createdAt: string
  prompt: ImageHistoryPrompt | null
  canDownload: boolean
  isCurrent: boolean
  isLatest: boolean
}

const AUTO_ACTION_IDS = new Set([
  'compose_brief',
  'plan_creative',
  'plan_visual',
  'finalize_requirements',
  'prepare_delivery',
  'approve_delivery',
  'publish_delivery',
])

// A page reload must not leave an already-authorized text stage spinning
// forever. These actions can safely continue an active run; delivery approval
// and publishing stay explicit, and image submission is never auto-replayed.
const RESUMABLE_AUTO_ACTION_IDS = new Set([
  'compose_brief',
  'plan_creative',
  'plan_visual',
  'finalize_requirements',
])

const TASK_ACTION_IDS = new Set([
  'verify_generation',
  'recover_generation',
  'retrieve_original_result',
  'recover_submission',
  'resume_after_restart',
  'continue_workflow',
  'close_generation_tracking',
  'start_new_generation',
  'retry_generation',
  'retry_rejected_generation',
  'revise_prompt_and_retry',
  'revise_requirements',
  'revise_prompt',
  'compile_prompt',
  'recheck_candidate',
  'abandon_generation',
])

const TASK_REASON_LABELS: Record<string, string> = {
  missing_information: '需要补充信息',
  repairable_composition: '内容需要修复',
  true_drift: '需要重新确认',
  hard_failure: '硬约束不满足',
  content_rejection: '内容未通过检查',
  missing_evidence: '缺少必要证据',
  protocol_error: '处理结果异常',
  technical_failure: '技术故障',
  remote_unknown: '结果未知',
  authorization_invalid: '授权已失效',
}

const TASK_ACTION_HELP: Record<string, string> = {
  verify_generation: '只核实原任务和远端状态，不创建新任务、不新增计数，也不会自动重发。',
  recover_generation: '只在原任务已有可找回结果时读取原结果，不提交新生成、不新增计数。',
  retrieve_original_result: '只找回原任务的结果，不新增计数。',
  recover_submission: '查询原提交状态，不会重复提交。',
  resume_after_restart: '恢复中断前的原任务并继续，不会自动补发已提交的请求。',
  continue_workflow: '从已保存的检查点继续原流程，不新建生成意图。',
  close_generation_tracking: '只结束本地跟踪并释放已证实占用；不会声称远端已取消，已计入的受理次数不变。',
  start_new_generation: '这是新任务，会重新计数并占用槽位；旧任务仍可能在远端执行或计费。',
  retry_generation: '重新生成会新增一次受理计数并占用生成槽位，请明确确认后再操作。',
  retry_rejected_generation: '会按当前已批提示词重新生成一张图，新增一次受理计数并占用生成槽位。',
  revise_prompt_and_retry: '先修改并重新审查提示词，再生成一张新图；会新增一次受理计数并占用生成槽位。',
  revise_requirements: '修改需求会让旧提示词和授权失效；确认新需求前不会继续生成。',
  revise_prompt: '修改提示词后会重新审查；确认前不会生成图片。',
  compile_prompt: '按当前已确认需求重新编制提示词；不会提交生图，也不会新增受理次数。',
  recheck_candidate: '只重新核对当前已生成的图片，不会重新生成，也不会新增受理次数或占用生成槽位。',
  plan_creative: '按当前已确认需求重新整理创意方向；不会生成图片，也不会新增生成次数。',
  abandon_generation: '放弃本次生成只结束当前尝试；已计入的受理次数不会退回。',
}

const TASK_CONFIRMATION_TEXT: Record<string, string> = {
  close_generation_tracking: '我确认结束本地跟踪；原受理计数保留，且这不代表取消远端任务',
  start_new_generation: '我确认这是新任务，将重新计数并占用槽位；旧任务仍可能在远端执行或计费',
  retry_generation: '我确认重新生成会新增一次受理计数并占用生成槽位',
  retry_rejected_generation: '我确认重新生成会新增一次受理计数并占用生成槽位',
  revise_prompt_and_retry: '我确认重新生成会新增一次受理计数并占用生成槽位',
  abandon_generation: '我确认放弃本次生成，已计入的受理次数不会退回',
}

const TASK_ACKNOWLEDGEMENT: Record<string, string> = {
  close_generation_tracking: 'acknowledge_local_close',
  start_new_generation: 'acknowledge_risk',
  retry_generation: 'acknowledge_new_attempt',
  retry_rejected_generation: 'acknowledge_new_attempt',
  revise_prompt_and_retry: 'acknowledge_new_attempt',
}

function makeId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (value) => {
    const random = Math.floor(Math.random() * 16)
    const digit = value === 'x' ? random : (random & 0x3) | 0x8
    return digit.toString(16)
  })
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function list(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => text(item)).filter(Boolean)
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function firstRecord(value: unknown): Record<string, unknown> {
  return Array.isArray(value) && value.length ? record(value[0]) : {}
}

export function useChatFlow() {
  const workspace = useWorkspace()
  const workflow = useWorkflow(workspace.projectParam)

  const busy = ref(false)
  const flowError = ref('')
  const copied = ref(false)
  const autoBusy = ref(false)
  const pendingAttachment = shallowRef<PendingAttachment | null>(null)
  const attachmentBusy = ref(false)
  const attachmentError = ref('')
  const pendingRequirementRequestId = ref('')
  const pendingPromptCommandId = ref('')
  const pendingTaskAction = ref<WorkflowAction | null>(null)
  const taskConfirmations = ref<Record<string, boolean>>({})
  let lastAutoAction = ''
  let autoArmed = false
  let copyTimer: ReturnType<typeof setTimeout> | null = null
  let attachmentToken = 0
  let stageTask: Promise<StagedMessageAttachmentView> | null = null
  let stageTaskKey = ''
  let keepAttachmentOnProjectChange = false

  /**
   * Sending no longer disables the composer, so a response must not erase text
   * the user started typing for the next turn while the request was in flight.
   */
  function clearDraftIfUnchanged(sent: string): void {
    const current = workspace.draft.value
    if (current === sent || current.trim() === sent.trim()) workspace.draft.value = ''
  }

  const view = workflow.view
  const finalBrief = computed<WorkflowArtifact | null>(() => {
    const artifacts = view.value?.artifacts ?? []
    return artifacts.find((artifact) => artifact.kind === 'final_brief' && artifact.status === 'approved') ?? null
  })
  const requirementPayload = computed(() => record(finalBrief.value?.payload))
  const requirementRows = computed(() => {
    const payload = requirementPayload.value
    const rows: Array<{ label: string; value: string }> = []
    const add = (label: string, value: unknown): void => {
      const rendered = Array.isArray(value) ? list(value).join('、') : text(value)
      if (rendered) rows.push({ label, value: rendered })
    }
    add('商品', payload.product_description)
    add('用途', payload.purpose)
    add('创作方向', payload.creative_direction)
    add('画面风格', payload.style_keywords)
    add('构图', payload.composition)
    add('光线', payload.lighting)
    add('文案', payload.texts)
    add('必须包含', payload.must_include)
    add('必须保留', payload.must_preserve)
    add('避免', payload.forbidden)
    add('尺寸', payload.size)
    return rows
  })
  const canConfirmRequirements = computed(() =>
    Boolean(
      finalBrief.value &&
      view.value?.available_actions.some((action) => action.id === 'confirm_requirements'),
    ),
  )
  const promptPreview = computed<PromptPreviewView | null>(() => view.value?.prompt_preview ?? null)
  const promptRequest = computed(() => record(promptPreview.value?.request))
  const positivePrompt = computed(() =>
    text(promptRequest.value.positive_prompt) || text(promptRequest.value.prompt),
  )
  const negativePrompt = computed(() => text(promptRequest.value.negative_prompt))
  const promptOutput = computed(() => {
    const output = record(promptRequest.value.output)
    const count = output.count ?? 1
    const size = text(output.size)
    return [count + ' 张', size].filter(Boolean).join(' · ')
  })
  const referenceInfo = computed(() => {
    const reference = firstRecord(promptRequest.value.references)
    if (!reference.asset_id) return ''
    const labels: Record<string, string> = {
      product: '产品图',
      style: '风格参考',
      composition: '构图参考',
      none: '参考图',
    }
    const usage = labels[text(reference.usage)] ?? '参考图'
    const version = text(reference.content_version)
    return version ? usage + ' · 版本 ' + version.slice(0, 12) : usage
  })
  function promptOfVersion(prompt: Record<string, unknown> | null): ImageHistoryPrompt | null {
    if (!prompt) return null
    const positive = text(prompt.positive_prompt) || text(prompt.prompt)
    const negative = text(prompt.negative_prompt)
    if (!positive && !negative) return null
    return { positive, negative }
  }

  const publishedAssetId = computed(() => {
    const current = view.value
    if (!current) return null
    if (current.delivery?.status !== 'published') return null
    const versions = current.versions ?? []
    const currentVersion = versions.find(
      (version) => version.run_id === current.run_id && version.asset_id,
    )
    if (currentVersion?.asset_id) return currentVersion.asset_id
    // 旧数据或尚未提供版本列表时，才回退到交付记录本身。
    if (versions.length === 0 && current.delivery.asset_id) return current.delivery.asset_id
    return null
  })

  const imageHistory = computed<ImageHistoryItem[]>(() => {
    const current = view.value
    if (!current) return []
    const versions = [...(current.versions ?? [])]
      .filter((version) => version.status === 'published')
      .sort((left, right) => left.sequence_no - right.sequence_no)
    if (versions.length === 0) {
      if (current.delivery?.status === 'published' && current.delivery.asset_id) {
        return [{
          runId: current.run_id ?? 'current-delivery',
          sequenceNo: 1,
          requirementRevision: current.requirement_revision,
          assetId: current.delivery.asset_id,
          width: null,
          height: null,
          caption: current.delivery.caption || current.delivery.filename || '已完成的电商图片',
          publishedAt: current.delivery.published_at,
          createdAt: current.delivery.created_at,
          prompt: null,
          canDownload: true,
          isCurrent: true,
          isLatest: true,
        }]
      }
      return []
    }
    return versions.map((version, index) => ({
      runId: version.run_id,
      sequenceNo: version.sequence_no,
      requirementRevision: version.requirement_revision,
      assetId: version.asset_id,
      width: version.width ?? null,
      height: version.height ?? null,
      caption: version.caption || version.filename || ('版本 ' + version.sequence_no + ' 生成图片'),
      publishedAt: version.published_at,
      createdAt: version.created_at,
      prompt: promptOfVersion(version.prompt),
      canDownload: version.can_download,
      isCurrent: Boolean(current.run_id && version.run_id === current.run_id),
      isLatest: index === versions.length - 1,
    }))
  })

  const resultUnavailableText = computed(() => {
    const current = view.value
    if (!current?.delivery) return ''
    if (current.delivery.status === 'revoked') {
      return '当前交付已撤销，图片不可访问；历史记录（如有）仅作只读回看，系统不会用其他版本替代。'
    }
    if (current.delivery.status === 'published' && !publishedAssetId.value) {
      return '当前交付图片已过期、文件缺失或未通过内容门禁，不能预览或下载；系统不会用其他版本替代。'
    }
    return ''
  })
  const publishedCaption = computed(() => {
    const current = view.value
    if (!current) return ''
    return current.delivery?.caption || current.delivery?.filename || '已完成的电商图片'
  })
  const questionText = computed(() => {
    const current = view.value
    if (!current) return ''
    if (current.status !== 'needs_input' && current.status !== 'blocked' && current.status !== 'failed') {
      return ''
    }
    return current.issue?.message || current.task_state?.reason?.message || ''
  })
  const statusText = computed(() => {
    const current = view.value
    if (!current) return ''
    if (questionText.value) return ''
    if (promptPreview.value) return ''
    if (publishedAssetId.value) return '图片已生成并发布。'
    if (current.status === 'completed') return '这一轮已完成。'
    if (current.status !== 'active') return ''
    if (current.stage === 'S03') return '正在整理需求…'
    if (current.stage === 'S04') return '正在构思画面…'
    if (current.stage === 'S05') return '正在检查画面方案…'
    if (current.stage === 'S07') return '正在准备提示词…'
    if (current.stage === 'S08' || current.stage === 'S09') return '正在生成图片…'
    if (current.stage === 'S10') return '正在整理交付图片…'
    return ''
  })
  // The backend exposes reviewed S03 clarification questions in issue.message.
  // Keep other statuses on the safe task reason copy.
  const clarificationText = computed(() => {
    const current = view.value
    if (!current || current.stage !== 'S03' || current.status !== 'needs_input') return ''
    if (current.issue?.code !== 'MISSING_INPUT') return ''
    const reason = current.task_state?.reason
    if (reason && reason.category !== 'missing_information') return ''
    return current.issue?.message || ''
  })
  const taskReason = computed(() => view.value?.task_state?.reason ?? null)
  const recoverablePromptBlock = computed(() => {
    const current = view.value
    return Boolean(
      current &&
        current.stage === 'S07' &&
        current.status === 'blocked' &&
        current.issue?.code === 'PROMPT_REQUIRES_RECONFIRMATION',
    )
  })
  const recoverableCreativeBlock = computed(() => {
    const current = view.value
    return Boolean(
      current &&
        current.stage === 'S04' &&
        current.status === 'blocked' &&
        current.issue?.code === 'REQUIREMENT_DRIFT',
    )
  })
  const recoverableImageBlock = computed(() => {
    const current = view.value
    return Boolean(
      current &&
        current.stage === 'S09' &&
        current.status === 'blocked' &&
        current.issue?.code === 'IMAGE_NOT_APPROVED',
    )
  })
  const taskReasonLabel = computed(() => {
    if (recoverablePromptBlock.value) return '提示词需要重新编制'
    if (recoverableCreativeBlock.value) return '创意方向需要重新整理'
    if (recoverableImageBlock.value) return '图片需要重新检查'
    if (taskReason.value) {
      return TASK_REASON_LABELS[taskReason.value.category] || '任务需要处理'
    }
    if (clarificationText.value) return '需要补充信息'
    return questionText.value ? '任务需要处理' : ''
  })
  const taskStatusText = computed(() => {
    if (recoverablePromptBlock.value) {
      return '已确认需求没有变化，不需要修改需求。点“编制生图提示词”即可按现有需求重试；不会提交生图，也不会新增受理次数。'
    }
    if (recoverableCreativeBlock.value) {
      return '需求信息已经整理完成，不需要重新确认。点“制定创意方向”即可按现有需求重新整理；不会生成图片，也不会新增生成次数。'
    }
    if (recoverableImageBlock.value) {
      return '本次图片已经生成。点“重新检查这张图片”即可按简化后的规则重新核对；不会重新生成，也不会新增受理次数或占用生成槽位。'
    }
    return clarificationText.value || taskReason.value?.message || questionText.value || statusText.value
  })
  const taskActions = computed<WorkflowAction[]>(() => {
    const current = view.value
    if (!current) return []
    const allowed = new Set(TASK_ACTION_IDS)
    if (current.status === 'blocked') allowed.add('plan_creative')
    const actions = current.available_actions.filter((action) => allowed.has(action.id))
    if (current.status !== 'active' || busy.value) return actions
    const resumable = current.available_actions.find((action) =>
      RESUMABLE_AUTO_ACTION_IDS.has(action.id),
    )
    if (!resumable || actions.some((action) => action.id === resumable.id)) return actions
    // A refresh can leave a server-side stage active with no request in flight.
    // Surface an explicit continuation instead of an endless spinner; this does
    // not call a model until the user chooses it.
    return [{ ...resumable, label: '继续当前步骤' }, ...actions]
  })
  const isWorkingStatus = computed(() =>
    !taskReason.value &&
    !publishedAssetId.value &&
    !promptPreview.value &&
    view.value?.status === 'active',
  )
  const pendingTaskConfirmationMissing = computed(() => {
    const action = pendingTaskAction.value
    return Boolean(action?.requires_confirmation && !taskConfirmations.value[action.id])
  })
  const hasConversation = computed(() => Boolean(workspace.projectParam.value))
  const attachmentFileName = computed(() => pendingAttachment.value?.file.name ?? '')

  function objectUrlFor(file: File): string | null {
    try {
      if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
        return URL.createObjectURL(file)
      }
    } catch {
      // 预览失败不阻止选择文件，发送时仍以服务端暂存结果为准。
    }
    return null
  }

  function revokeObjectUrl(value: string | null | undefined): void {
    if (!value) return
    try {
      if (typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') {
        URL.revokeObjectURL(value)
      }
    } catch {
      // 已释放的临时 URL 不影响业务状态。
    }
  }

  function replacePendingAttachment(next: PendingAttachment | null): void {
    pendingAttachment.value = next
    workspace.setMessageAttachmentPending(next !== null)
  }

  function clearAttachmentState(discard: boolean): void {
    const current = pendingAttachment.value
    attachmentToken += 1
    attachmentBusy.value = false
    stageTask = null
    stageTaskKey = ''
    revokeObjectUrl(current?.objectUrl)
    replacePendingAttachment(null)
    attachmentError.value = ''
    if (discard && current?.staged && current.stagedProjectId) {
      void api.discardMessageAttachment(current.stagedProjectId, current.staged.asset.id).catch(() => undefined)
    }
  }

  async function ensureStagedAttachment(projectId: string): Promise<StagedMessageAttachmentView | null> {
    const current = pendingAttachment.value
    if (!current) return null
    if (current.staged && current.stagedProjectId === projectId) return current.staged
    const key = [projectId, current.file.name, current.file.size, current.file.lastModified].join(':')
    if (stageTask && stageTaskKey === key) return stageTask
    const token = attachmentToken
    attachmentBusy.value = true
    attachmentError.value = ''
    const task = api.stageMessageAttachment(projectId, current.file)
    stageTask = task
    stageTaskKey = key
    try {
      const staged = await task
      if (
        token !== attachmentToken ||
        pendingAttachment.value !== current ||
        workspace.currentProject.value?.id !== projectId
      ) {
        void api.discardMessageAttachment(projectId, staged.asset.id).catch(() => undefined)
        return null
      }
      revokeObjectUrl(current.objectUrl)
      replacePendingAttachment({
        ...current,
        objectUrl: null,
        previewUrl: staged.preview_url,
        staged,
        stagedProjectId: projectId,
      })
      return staged
    } catch (caught) {
      if (token === attachmentToken) {
        attachmentError.value = caught instanceof ApiError
          ? caught.message
          : '图片没有上传成功，请重试。'
      }
      return null
    } finally {
      if (token === attachmentToken) attachmentBusy.value = false
      if (stageTask === task) {
        stageTask = null
        stageTaskKey = ''
      }
    }
  }

  async function chooseAttachment(file: File): Promise<void> {
    const mime = file.type.toLowerCase()
    if (mime !== 'image/png' && mime !== 'image/jpeg') {
      attachmentError.value = '只支持 PNG 或 JPEG 图片。'
      return
    }
    if (attachmentBusy.value) return
    const previous = pendingAttachment.value
    const objectUrl = objectUrlFor(file)
    attachmentToken += 1
    attachmentError.value = ''
    replacePendingAttachment({
      file,
      objectUrl,
      previewUrl: objectUrl ?? '',
      staged: null,
      stagedProjectId: null,
    })
    if (previous?.staged && previous.stagedProjectId) {
      void api.discardMessageAttachment(previous.stagedProjectId, previous.staged.asset.id).catch(() => undefined)
    }
    const projectId = workspace.currentProject.value?.id
    if (projectId) await ensureStagedAttachment(projectId)
  }

  async function removeAttachment(): Promise<void> {
    if (attachmentBusy.value) return
    clearAttachmentState(true)
  }

  function clearAttachmentAfterSend(): void {
    clearAttachmentState(false)
  }

  function applyMessageResponse(response: MessageCreateResponse): void {
    if (response.workflow) workflow.adopt(response.workflow)
    else if (view.value) void workflow.refresh()
  }

  function actionById(id: string): WorkflowAction | null {
    return view.value?.available_actions.find((action) => action.id === id) ?? null
  }

  function taskActionEffectLabel(action: WorkflowAction): string {
    if (action.read_only || action.effect === 'read_only') return '只读核实'
    if (action.requires_model_call || action.effect === 'model_call') return '可能调用模型'
    return '更新状态'
  }

  function taskActionHelp(action: WorkflowAction): string {
    return TASK_ACTION_HELP[action.id] || action.label
  }

  function taskConfirmationText(action: WorkflowAction): string {
    return TASK_CONFIRMATION_TEXT[action.id] || '我已核对当前内容并确认继续。'
  }

  function taskActionPayload(action: WorkflowAction): Record<string, unknown> | null {
    const key = TASK_ACKNOWLEDGEMENT[action.id]
    return key ? { [key]: true } : null
  }

  function taskCountNote(action: WorkflowAction): string {
    if (action.read_only || action.effect === 'read_only') return '只读核实不会新增受理计数。'
    if (TASK_ACKNOWLEDGEMENT[action.id]) return '此操作会新增一次受理计数并占用生成槽位。'
    return ''
  }

  function taskActionDisabled(action: WorkflowAction): boolean {
    if (busy.value || workflow.actionBusy.value) return true
    return Boolean(action.requires_confirmation && !taskConfirmations.value[action.id])
  }

  async function executeTaskAction(action: WorkflowAction, taskText: string | null = null): Promise<boolean> {
    const projectId = workspace.projectParam.value
    if (!projectId || busy.value || workflow.actionBusy.value) return false
    if (action.requires_text && !taskText?.trim()) return false
    if (action.requires_confirmation && !taskConfirmations.value[action.id]) {
      flowError.value = '请先勾选确认后再执行这个动作。'
      return false
    }
    const sessionAtStart = sessionRevision.value
    const resumePipeline = RESUMABLE_AUTO_ACTION_IDS.has(action.id)
    autoArmed = false
    busy.value = true
    flowError.value = ''
    try {
      const ok = await workflow.runAction(action, taskText?.trim() ?? null, taskActionPayload(action))
      if (!ok) {
        if (sessionAtStart === sessionRevision.value) {
          flowError.value = workflow.error.value || '操作没有完成，请稍后重试。'
        }
        return false
      }
      if (sessionAtStart !== sessionRevision.value || workspace.projectParam.value !== projectId) {
        return false
      }
      delete taskConfirmations.value[action.id]
      pendingTaskAction.value = null
      if (taskText !== null) clearDraftIfUnchanged(taskText)
      await workspace.refreshProjectDetail()
      if (resumePipeline) {
        autoArmed = true
        await autoAdvance(true)
      }
      return true
    } finally {
      busy.value = false
    }
  }

  async function runTaskAction(action: WorkflowAction): Promise<void> {
    if (taskActionDisabled(action)) return
    if (action.requires_text) {
      pendingTaskAction.value = action
      flowError.value = ''
      return
    }
    await executeTaskAction(action)
  }

  async function submitTaskText(body: string): Promise<boolean> {
    const action = pendingTaskAction.value
    if (!action || !body.trim() || busy.value) return false
    return executeTaskAction(action, body)
  }

  function autoAction(): WorkflowAction | null {
    const current = view.value
    if (!current) return null
    for (const action of current.available_actions) {
      if (AUTO_ACTION_IDS.has(action.id)) return action
    }
    return null
  }

  async function autoAdvance(force = false): Promise<void> {
    if (!autoArmed || autoBusy.value || (busy.value && !force)) return
    autoBusy.value = true
    try {
      for (let step = 0; step < 8; step += 1) {
        const action = autoAction()
        const current = view.value
        if (!action || !current) return
        const key = [
          current.project_id,
          current.stage,
          current.status,
          current.project_revision,
          current.requirement_revision,
          action.id,
        ].join(':')
        if (key === lastAutoAction) return
        lastAutoAction = key
        const ok = await workflow.runAction(action)
        if (!ok) {
          lastAutoAction = ''
          return
        }
        await workspace.refreshProjectDetail()
      }
    } finally {
      autoBusy.value = false
    }
  }

  async function afterWorkflowChange(force = false): Promise<void> {
    await workspace.refreshProjectDetail()
    await autoAdvance(force)
  }

  async function begin(content: string): Promise<void> {
    const body = content.trim()
    if (!body || busy.value) return
    const sessionAtStart = sessionRevision.value
    autoArmed = true
    busy.value = true
    flowError.value = ''
    try {
      const project = await api.createProject()
      if (sessionAtStart !== sessionRevision.value) return
      workspace.adoptCreatedProject(project)
      autoArmed = true
      const next = await runWorkflowAction(project.id, 'submit_request', {
        expected_revision: project.revision,
        text: body,
      })
      if (sessionAtStart !== sessionRevision.value) return
      workflow.adopt(next)
      clearDraftIfUnchanged(body)
      await afterWorkflowChange(true)
    } catch (caught) {
      if (sessionAtStart !== sessionRevision.value) return
      flowError.value = caught instanceof ApiError
        ? caught.message
        : '没有成功建立这次对话，请稍后重试。'
      await workspace.refreshProjectDetail()
    } finally {
      busy.value = false
    }
  }

  async function beginWithAttachment(body: string): Promise<void> {
    const sessionAtStart = sessionRevision.value
    autoArmed = true
    busy.value = true
    flowError.value = ''
    try {
      const project = await api.createProject()
      if (sessionAtStart !== sessionRevision.value) return
      keepAttachmentOnProjectChange = true
      workspace.adoptCreatedProject(project)
      autoArmed = true
      const staged = await ensureStagedAttachment(project.id)
      if (!staged || sessionAtStart !== sessionRevision.value) return
      const response = await api.sendMessage(project.id, {
        content: body,
        expected_revision: project.revision,
        request_id: makeId(),
        attachment_asset_id: staged.asset.id,
      })
      if (sessionAtStart !== sessionRevision.value) return
      applyMessageResponse(response)
      clearDraftIfUnchanged(body)
      clearAttachmentAfterSend()
      await afterWorkflowChange(true)
    } catch (caught) {
      if (sessionAtStart !== sessionRevision.value) return
      flowError.value = caught instanceof ApiError
        ? caught.message
        : '没有成功建立这次对话，请稍后重试。'
      await workspace.refreshProjectDetail()
    } finally {
      busy.value = false
    }
  }

  async function send(content: string): Promise<void> {
    const body = content.trim()
    if (!body || busy.value) return
    if (pendingTaskAction.value) {
      await submitTaskText(body)
      return
    }
    if (!workspace.currentProject.value) {
      if (pendingAttachment.value) await beginWithAttachment(body)
      else await begin(body)
      return
    }
    const sessionAtStart = sessionRevision.value
    autoArmed = true
    busy.value = true
    flowError.value = ''
    const project = workspace.currentProject.value
    try {
      const staged = pendingAttachment.value
        ? await ensureStagedAttachment(project.id)
        : null
      if (pendingAttachment.value && !staged) return
      if (sessionAtStart !== sessionRevision.value) return
      const expectedRevision = view.value?.project_revision ?? project.revision
      const response = await api.sendMessage(project.id, {
        content: body,
        expected_revision: expectedRevision,
        request_id: makeId(),
        ...(staged ? { attachment_asset_id: staged.asset.id } : {}),
      })
      if (sessionAtStart !== sessionRevision.value) return
      applyMessageResponse(response)
      clearDraftIfUnchanged(body)
      if (staged) clearAttachmentAfterSend()
      await afterWorkflowChange(true)
    } catch (caught) {
      if (sessionAtStart !== sessionRevision.value) return
      flowError.value = caught instanceof ApiError
        ? caught.message
        : '消息没有发送成功，请稍后重试。'
      await workspace.refreshProjectDetail()
      await workflow.refresh()
    } finally {
      busy.value = false
    }
  }

  async function confirmRequirements(): Promise<void> {
    const current = view.value
    const artifact = finalBrief.value
    if (!current || !artifact || busy.value) return
    const action = actionById('confirm_requirements')
    if (!action) return
    const sessionAtStart = sessionRevision.value
    if (!pendingRequirementRequestId.value) pendingRequirementRequestId.value = makeId()
    autoArmed = true
    busy.value = true
    flowError.value = ''
    try {
      const ok = await workflow.runAction(action, null, {
        artifact_id: artifact.id,
        artifact_digest: artifact.digest,
        requirement_revision: current.requirement_revision,
        request_id: pendingRequirementRequestId.value,
      })
      if (sessionAtStart !== sessionRevision.value) return
      if (!ok) {
        flowError.value = workflow.error.value || '操作没有完成，请稍后重试。'
        return
      }
      pendingRequirementRequestId.value = ''
      await afterWorkflowChange(true)
    } finally {
      busy.value = false
    }
  }

  async function confirmPrompt(): Promise<void> {
    const preview = promptPreview.value
    if (!preview?.can_confirm || busy.value) return
    const action = actionById('confirm_prompt')
    if (!action) return
    const sessionAtStart = sessionRevision.value
    if (!pendingPromptCommandId.value) pendingPromptCommandId.value = makeId()
    autoArmed = true
    busy.value = true
    flowError.value = ''
    try {
      const ok = await workflow.runAction(action, null, {
        snapshot_id: preview.id,
        snapshot_digest: preview.request_digest,
        command_id: pendingPromptCommandId.value,
        command_context: preview.command_context,
      })
      if (sessionAtStart !== sessionRevision.value) return
      if (!ok) {
        flowError.value = workflow.error.value || '操作没有完成，请稍后重试。'
        return
      }
      pendingPromptCommandId.value = ''
      await afterWorkflowChange(true)
    } finally {
      busy.value = false
    }
  }

  async function copyPrompt(): Promise<void> {
    const value = positivePrompt.value
    if (!value) return
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
      await navigator.clipboard.writeText(value)
      copied.value = true
      if (copyTimer) clearTimeout(copyTimer)
      copyTimer = setTimeout(() => { copied.value = false }, 2500)
    } catch {
      flowError.value = '复制失败，请手动选择提示词文本。'
    }
  }

  async function retry(): Promise<void> {
    autoArmed = true
    flowError.value = ''
    await workflow.refresh()
    await workspace.refreshProjectDetail()
    await autoAdvance(true)
  }

  watch(
    () => view.value,
    () => { void autoAdvance() },
    { flush: 'post' },
  )
  watch(
    () => workspace.projectParam.value,
    () => {
      if (keepAttachmentOnProjectChange) {
        keepAttachmentOnProjectChange = false
      } else if (pendingAttachment.value) {
        clearAttachmentState(true)
      }
      lastAutoAction = ''
      autoArmed = false
      pendingRequirementRequestId.value = ''
      pendingPromptCommandId.value = ''
      pendingTaskAction.value = null
      taskConfirmations.value = {}
      flowError.value = ''
      copied.value = false
    },
    { flush: 'sync' },
  )
  watch(
    () => workspace.currentProject.value?.id ?? null,
    (projectId) => {
      if (projectId && pendingAttachment.value && !pendingAttachment.value.staged) {
        void ensureStagedAttachment(projectId)
      }
    },
    { flush: 'post' },
  )
  watch(
    sessionRevision,
    () => {
      if (pendingAttachment.value) clearAttachmentState(true)
      pendingRequirementRequestId.value = ''
      pendingPromptCommandId.value = ''
      pendingTaskAction.value = null
      taskConfirmations.value = {}
      flowError.value = ''
      attachmentError.value = ''
      lastAutoAction = ''
      copied.value = false
      if (copyTimer) {
        clearTimeout(copyTimer)
        copyTimer = null
      }
    },
    { flush: 'sync' },
  )
  onBeforeUnmount(() => {
    const current = pendingAttachment.value
    if (current?.staged && current.stagedProjectId) {
      void api.discardMessageAttachment(current.stagedProjectId, current.staged.asset.id).catch(() => undefined)
    }
    revokeObjectUrl(current?.objectUrl)
    workspace.setMessageAttachmentPending(false)
  })

  return {
    workflow,
    view,
    busy,
    flowError,
    copied,
    pendingAttachment,
    attachmentBusy,
    attachmentError,
    attachmentFileName,
    taskReason,
    taskReasonLabel,
    taskStatusText,
    taskActions,
    taskConfirmations,
    pendingTaskAction,
    pendingTaskConfirmationMissing,
    isWorkingStatus,
    chooseAttachment,
    removeAttachment,
    hasConversation,
    finalBrief,
    requirementRows,
    canConfirmRequirements,
    promptPreview,
    positivePrompt,
    negativePrompt,
    promptOutput,
    referenceInfo,
    publishedAssetId,
    publishedCaption,
    imageHistory,
    resultUnavailableText,
    questionText,
    statusText,
    send,
    confirmRequirements,
    confirmPrompt,
    copyPrompt,
    retry,
    taskActionEffectLabel,
    taskActionHelp,
    taskConfirmationText,
    taskCountNote,
    taskActionDisabled,
    runTaskAction,
  }
}
