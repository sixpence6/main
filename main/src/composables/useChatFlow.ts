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

const AUTO_ACTION_IDS = new Set([
  'compose_brief',
  'plan_creative',
  'plan_visual',
  'finalize_requirements',
  'prepare_delivery',
  'approve_delivery',
  'publish_delivery',
])

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
  let lastAutoAction = ''
  let autoArmed = false
  let copyTimer: ReturnType<typeof setTimeout> | null = null
  let attachmentToken = 0
  let stageTask: Promise<StagedMessageAttachmentView> | null = null
  let stageTaskKey = ''
  let keepAttachmentOnProjectChange = false

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
  const publishedAssetId = computed(() => {
    const current = view.value
    if (!current) return null
    if (current.delivery?.status === 'published' && current.delivery.asset_id) {
      return current.delivery.asset_id
    }
    const version = [...(current.versions ?? [])].reverse().find((item) => item.asset_id && item.published_at)
    return version?.asset_id ?? null
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
    if (current.stage === 'S03') return '正在整理需求…'
    if (current.stage === 'S04') return '正在构思画面…'
    if (current.stage === 'S05') return '正在检查画面方案…'
    if (current.stage === 'S07') return '正在准备提示词…'
    if (current.stage === 'S08' || current.stage === 'S09') return '正在生成图片…'
    if (current.stage === 'S10') return '正在整理交付图片…'
    return ''
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

  function fallbackAction(id: string, label: string): WorkflowAction {
    return {
      id,
      label,
      kind: 'button',
      requires_text: false,
      text_label: null,
      requires_confirmation: false,
    }
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
    autoArmed = true
    busy.value = true
    flowError.value = ''
    try {
      const project = await api.createProject()
      workspace.adoptCreatedProject(project)
      autoArmed = true
      const next = await runWorkflowAction(project.id, 'submit_request', {
        expected_revision: project.revision,
        text: body,
      })
      workflow.adopt(next)
      workspace.draft.value = ''
      await afterWorkflowChange(true)
    } catch (caught) {
      flowError.value = caught instanceof ApiError
        ? caught.message
        : '没有成功建立这次对话，请稍后重试。'
      await workspace.refreshProjectDetail()
    } finally {
      busy.value = false
    }
  }

  async function beginWithAttachment(body: string): Promise<void> {
    autoArmed = true
    busy.value = true
    flowError.value = ''
    try {
      const project = await api.createProject()
      keepAttachmentOnProjectChange = true
      workspace.adoptCreatedProject(project)
      autoArmed = true
      const staged = await ensureStagedAttachment(project.id)
      if (!staged) return
      const response = await api.sendMessage(project.id, {
        content: body,
        expected_revision: project.revision,
        request_id: makeId(),
        attachment_asset_id: staged.asset.id,
      })
      applyMessageResponse(response)
      workspace.draft.value = ''
      clearAttachmentAfterSend()
      await afterWorkflowChange(true)
    } catch (caught) {
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
    if (!workspace.currentProject.value) {
      if (pendingAttachment.value) await beginWithAttachment(body)
      else await begin(body)
      return
    }
    autoArmed = true
    busy.value = true
    flowError.value = ''
    const project = workspace.currentProject.value
    try {
      const staged = pendingAttachment.value
        ? await ensureStagedAttachment(project.id)
        : null
      if (pendingAttachment.value && !staged) return
      const expectedRevision = view.value?.project_revision ?? project.revision
      const response = await api.sendMessage(project.id, {
        content: body,
        expected_revision: expectedRevision,
        request_id: makeId(),
        ...(staged ? { attachment_asset_id: staged.asset.id } : {}),
      })
      applyMessageResponse(response)
      workspace.draft.value = ''
      if (staged) clearAttachmentAfterSend()
      await afterWorkflowChange(true)
    } catch (caught) {
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
    const action = actionById('confirm_requirements') ?? fallbackAction('confirm_requirements', '确认需求')
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
      if (ok) pendingRequirementRequestId.value = ''
      await afterWorkflowChange(true)
    } finally {
      busy.value = false
    }
  }

  async function confirmPrompt(): Promise<void> {
    const preview = promptPreview.value
    if (!preview?.can_confirm || busy.value) return
    const action = actionById('confirm_prompt') ?? fallbackAction('confirm_prompt', '确认提示词')
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
      if (ok) pendingPromptCommandId.value = ''
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
    questionText,
    statusText,
    send,
    confirmRequirements,
    confirmPrompt,
    copyPrompt,
    retry,
  }
}
