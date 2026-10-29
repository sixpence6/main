import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  provide,
  ref,
  shallowRef,
  watch,
  type ComputedRef,
  type InjectionKey,
  type Ref,
} from 'vue'
import { ApiError, api, isAbortError } from '@/api/client'
import type {
  Brief,
  BriefFields,
  ConfigStatus,
  GenerationView,
  MessageCreateResponse,
  MessageView,
  PatchBriefRequest,
  ProjectDetailResponse,
  ProjectSummary,
  ProjectView,
  SubmitGenerationRequest,
} from '@/api/types'
import { editableBriefFields, isUuid, type EditableBriefKey } from '@/utils/format'
import { useViewport } from './useViewport'

export type StageId = 'describe' | 'confirm' | 'generate' | 'results'
export type BriefAction = 'save' | 'confirm' | 'organize' | 'reload'
export type ReferenceAction = 'upload' | 'remove' | 'reload'
export type ProjectErrorKind = 'invalid' | 'not-found' | 'network' | 'generic'

export interface PendingReference {
  file: File
  name: string
  previewUrl: string
  mimeType: string
  byteSize: number
  width: number
  height: number
  status: 'uploading' | 'failed'
}

export interface StageView {
  id: StageId
  label: string
  locked: boolean
  reason: string
}

export interface PendingGenerationRequest {
  projectId: string
  requestId: string
  confirmedRevision: number
  acknowledgeUnknownPresent: boolean
  acknowledgeUnknownValue: boolean | null
  generationId: string | null
}

type NavigationIntent =
  | { kind: 'new' }
  | { kind: 'project'; projectId: string }
  | { kind: 'history'; projectId: string | null }

export interface WorkspaceStore {
  draft: Ref<string>
  draftDirty: ComputedRef<boolean>
  hasUnsavedChanges: ComputedRef<boolean>
  navigationConfirmOpen: Ref<boolean>
  navigationIntentLabel: ComputedRef<string>
  confirmNavigation: () => void
  cancelNavigation: () => void
  projectParam: Ref<string | null>
  stage: Ref<StageId>
  stages: ComputedRef<StageView[]>
  configStatus: Ref<ConfigStatus | null>
  configError: Ref<string>
  configLoading: Ref<boolean>
  sidebarExpanded: Ref<boolean>
  sideDrawerOpen: Ref<boolean>
  modelDrawerOpen: Ref<boolean>
  projectDrawerOpen: Ref<boolean>
  announcement: Ref<string>
  breakpoint: ComputedRef<'narrow' | 'medium' | 'wide'>
  isMobile: ComputedRef<boolean>
  projectList: Ref<ProjectSummary[]>
  projectListLoading: Ref<boolean>
  projectListError: Ref<string>
  projectListMoreError: Ref<string>
  nextCursor: Ref<string | null>
  projectListLoadingMore: Ref<boolean>
  currentProject: Ref<ProjectView | null>
  projectMessages: Ref<MessageView[]>
  projectGenerations: Ref<GenerationView[]>
  projectLoading: Ref<boolean>
  projectError: Ref<string>
  projectErrorKind: Ref<ProjectErrorKind | null>
  sendBusy: Ref<boolean>
  sendError: Ref<string>
  creationUnknown: Ref<boolean>
  organizing: ComputedRef<boolean>
  sendDisabledReason: ComputedRef<string>
  briefDraft: Ref<BriefFields | null>
  briefEditing: Ref<boolean>
  briefFieldErrors: Ref<Partial<Record<EditableBriefKey, string>>>
  briefDirty: ComputedRef<boolean>
  briefAction: Ref<BriefAction | null>
  briefBusy: ComputedRef<boolean>
  briefSaveError: Ref<string>
  briefConfirmError: Ref<string>
  briefSaveNotice: Ref<string>
  briefConflict: Ref<boolean>
  briefLocalCopy: Ref<boolean>
  promptStale: ComputedRef<boolean>
  saveDisabledReason: ComputedRef<string>
  confirmDisabledReason: ComputedRef<string>
  referencePending: Ref<PendingReference | null>
  referenceError: Ref<string>
  referenceNotice: Ref<string>
  referenceAction: Ref<ReferenceAction | null>
  referenceBusy: ComputedRef<boolean>
  referenceConflict: Ref<boolean>
  referenceCapability: ComputedRef<boolean | null>
  referenceLimitBytes: ComputedRef<number>
  referenceMimeTypes: ComputedRef<string[]>
  referenceGenerationBlockedReason: ComputedRef<string>
  referenceUploadDisabledReason: ComputedRef<string>
  referenceRemoveDisabledReason: ComputedRef<string>
  generationSubmitBusy: Ref<boolean>
  generationSubmitError: Ref<string>
  generationSubmitNotice: Ref<string>
  generationUncertain: Ref<boolean>
  generationPendingRestored: Ref<boolean>
  generationRecoveryAvailable: ComputedRef<boolean>
  generationCredentialWarning: Ref<string>
  generationRecoveryAction: Ref<'verify' | 'resume' | null>
  generationRiskDialogOpen: Ref<boolean>
  generationRetrieveBusy: Ref<boolean>
  generationRetrieveError: Ref<string>
  generationRetrieveNotice: Ref<string>
  resultRetrievalGeneration: ComputedRef<GenerationView | null>
  resultRetrievalAvailable: ComputedRef<boolean>
  resultRetrievalUnavailableReason: ComputedRef<string>
  generationRetrieveDisabledReason: ComputedRef<string>
  retrieveGenerationResult: () => Promise<void>
  confirmGenerationRisk: () => Promise<void>
  cancelGenerationRisk: () => void
  generationQuerying: Ref<boolean>
  generationQueryError: Ref<string>
  statusQueryInterruptionTitle: ComputedRef<string>
  generationDisabledReason: ComputedRef<string>
  latestGenerationView: ComputedRef<GenerationView | null>
  latestSucceededGeneration: ComputedRef<GenerationView | null>
  activeGeneration: ComputedRef<GenerationView | null>
  latestGenerationError: ComputedRef<string>
  succeededGenerations: ComputedRef<GenerationView[]>
  selectedResultGenerationId: Ref<string | null>
  selectedResultGeneration: ComputedRef<GenerationView | null>
  selectResultGeneration: (id: string) => void
  submitGeneration: () => Promise<void>
  verifyGenerationSubmission: () => Promise<void>
  resumeGenerationSubmission: () => Promise<void>
  retryGenerationQuery: () => void
  chooseReferenceFiles: (files: File[]) => Promise<void>
  retryReferenceUpload: () => Promise<void>
  removeReference: () => Promise<void>
  reloadLatestReference: () => Promise<void>
  startBriefEdit: () => void
  cancelBriefEdit: () => void
  setBriefField: (key: EditableBriefKey, value: string) => void
  saveBrief: () => Promise<void>
  confirmBrief: () => Promise<void>
  reloadLatestBrief: () => Promise<void>
  viewLocalBriefCopy: () => void
  send: () => Promise<void>
  retryOrganize: () => Promise<void>
  toggleSidebar: () => void
  openSideDrawer: () => void
  closeSideDrawer: () => void
  openModelDrawer: () => void
  closeModelDrawer: () => void
  openProjectDrawer: () => void
  closeProjectDrawer: () => void
  loadProjects: () => Promise<void>
  loadMoreProjects: () => Promise<void>
  retryLoadMore: () => Promise<void>
  selectProject: (id: string) => void
  retryProject: () => void
  createNewProject: () => void
  fillExample: (text: string) => void
  refreshConfig: () => Promise<void>
  setStage: (id: StageId) => void
  announce: (message: string) => void
}

const WORKSPACE_KEY: InjectionKey<WorkspaceStore> = Symbol('workspace')

function currentProjectParam(): string | null {
  if (typeof window === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  if (!params.has('project')) return null
  return (params.get('project') ?? '').trim()
}

function dedupeProjects(items: ProjectSummary[]): ProjectSummary[] {
  const seen = new Set<string>()
  return items.filter((item) => {
    if (seen.has(item.id)) return false
    seen.add(item.id)
    return true
  })
}

function createWorkspace(): WorkspaceStore {
  const { breakpoint, isMobile } = useViewport()

  const draft = ref('')
  const projectParam = ref<string | null>(currentProjectParam())
  const stage = ref<StageId>('describe')
  const sidebarExpanded = ref(breakpoint.value === 'wide')
  const sideDrawerOpen = ref(false)
  const modelDrawerOpen = ref(false)
  const projectDrawerOpen = ref(false)
  const announcement = ref('')
  const navigationConfirmOpen = ref(false)
  const navigationIntent = ref<NavigationIntent | null>(null)
  const navigationIntentLabel = computed(() => {
    if (navigationIntent.value?.kind === 'new') return '新建项目'
    if (navigationIntent.value?.kind === 'project') return '切换项目'
    if (navigationIntent.value?.kind === 'history') return '离开当前项目'
    return '继续操作'
  })

  const configStatus = ref<ConfigStatus | null>(null)
  const configError = ref('')
  const configLoading = ref(false)
  let configController: AbortController | null = null
  let lastAnnouncement = ''

  const projectList = ref<ProjectSummary[]>([])
  const projectListLoading = ref(false)
  const projectListError = ref('')
  const projectListMoreError = ref('')
  const nextCursor = ref<string | null>(null)
  const projectListLoadingMore = ref(false)
  let listController: AbortController | null = null
  let moreController: AbortController | null = null

  const currentProject = ref<ProjectView | null>(null)
  const projectMessages = ref<MessageView[]>([])
  const projectGenerations = ref<GenerationView[]>([])
  const projectLoading = ref(false)
  const projectError = ref('')
  const projectErrorKind = ref<ProjectErrorKind | null>(null)
  let detailController: AbortController | null = null
  let detailToken = 0
  let projectContextToken = 0

  const sendBusy = ref(false)
  const sendError = ref('')
  const creationUnknown = ref(false)
  const organizing = computed(() => currentProject.value?.brief_status === 'organizing')

  const generationSubmitBusy = ref(false)
  const generationSubmitError = ref('')
  const generationSubmitNotice = ref('')
  const generationUncertain = ref(false)
  const generationPendingRestored = ref(false)
  const generationCredentialWarning = ref('')
  const generationRecoveryAvailable = computed(
    () =>
      generationUncertain.value &&
      pendingGenerationRequest !== null &&
      currentProject.value?.id === pendingGenerationRequest.projectId,
  )
  const generationRecoveryAction = ref<'verify' | 'resume' | null>(null)
  const generationRiskDialogOpen = ref(false)
  const generationRetrieveBusy = ref(false)
  const generationRetrieveError = ref('')
  const generationRetrieveNotice = ref('')
  const generationQuerying = ref(false)
  const generationQueryError = ref('')
  const statusQueryTarget = ref<'project' | 'generation' | null>(null)
  const statusQueryFailureKind = ref<'connection' | 'resource' | null>(null)
  const statusQueryInterruptionTitle = computed(() => {
    if (!generationQueryError.value) return ''
    if (statusQueryFailureKind.value === 'resource') {
      return statusQueryTarget.value === 'project' ? '资源不存在：项目不可用。' : '资源不存在：生成记录不可用。'
    }
    return statusQueryTarget.value === 'project'
      ? '连接中断，项目状态暂不可知。'
      : '连接中断，生成状态暂不可知。'
  })
  let generationToken = 0
  let generationPollTimer: ReturnType<typeof setTimeout> | null = null
  let generationQueryController: AbortController | null = null
  let generationRefreshController: AbortController | null = null
  let generationRetrieveController: AbortController | null = null
  let generationQueryFailures = 0
  let pendingGenerationRequest: PendingGenerationRequest | null = null

  const latestGenerationView = computed<GenerationView | null>(() => {
    let latest: GenerationView | null = null
    for (const item of projectGenerations.value) {
      if (!latest || item.version_no > latest.version_no) latest = item
    }
    return latest
  })
  const latestSucceededGeneration = computed<GenerationView | null>(() => {
    let latest: GenerationView | null = null
    for (const item of projectGenerations.value) {
      if (item.status !== 'succeeded') continue
      if (!latest || item.version_no > latest.version_no) latest = item
    }
    return latest
  })
  const activeGeneration = computed<GenerationView | null>(() => {
    let active: GenerationView | null = null
    for (const item of projectGenerations.value) {
      if (item.status !== 'queued' && item.status !== 'running') continue
      if (!active || item.version_no > active.version_no) active = item
    }
    return active
  })
  const latestGenerationError = computed(() => {
    const latest = latestGenerationView.value
    if (!latest) return ''
    if (latest.status === 'failed') {
      return latest.error?.message
        ? `最近生成失败：${latest.error.message}`
        : '最近生成失败，已保留上一次成功图片。'
    }
    if (latest.status === 'unknown') {
      return '最近生成结果未确认；上次请求可能已提交并可能已计费。再次生成前需确认风险。'
    }
    return ''
  })

  const resultRetrievalGeneration = computed<GenerationView | null>(() => {
    const latest = latestGenerationView.value
    if (!latest || latest.status !== 'failed') return null
    if (latest.error?.code !== 'RESULT_FETCH_FAILED') return null
    return latest
  })
  const resultRetrievalAvailable = computed(
    () => resultRetrievalGeneration.value?.can_retrieve_result === true,
  )
  const resultRetrievalUnavailableReason = computed(() => {
    const record = resultRetrievalGeneration.value
    if (!record || record.can_retrieve_result) return ''
    return record.retrieval_unavailable_reason?.trim() || '服务端未提供可重取信息，当前不能重取结果。'
  })

  const succeededGenerations = computed<GenerationView[]>(() => {
    return projectGenerations.value
      .filter((item) => item.status === 'succeeded')
      .slice()
      .sort((a, b) => {
        if (a.version_no !== b.version_no) return a.version_no - b.version_no
        const aTime = Date.parse(a.completed_at ?? a.created_at)
        const bTime = Date.parse(b.completed_at ?? b.created_at)
        if (Number.isFinite(aTime) && Number.isFinite(bTime) && aTime !== bTime) return aTime - bTime
        return a.id.localeCompare(b.id)
      })
  })
  const selectedResultGenerationId = ref<string | null>(null)
  let resultSelectionPinned = false

  const selectedResultGeneration = computed<GenerationView | null>(() => {
    const id = selectedResultGenerationId.value
    if (id) {
      const match = projectGenerations.value.find((item) => item.id === id && item.status === 'succeeded')
      if (match) return match
    }
    return latestSucceededGeneration.value
  })

  function reconcileResultSelection(followLatest: boolean): void {
    const latest = latestSucceededGeneration.value
    if (!latest) {
      selectedResultGenerationId.value = null
      resultSelectionPinned = false
      return
    }
    const currentId = selectedResultGenerationId.value
    const currentExists = Boolean(
      currentId && projectGenerations.value.some((item) => item.id === currentId && item.status === 'succeeded'),
    )
    if (followLatest || !currentExists) {
      selectedResultGenerationId.value = latest.id
      resultSelectionPinned = false
    }
  }

  function selectResultGeneration(id: string): void {
    const target = projectGenerations.value.find((item) => item.id === id && item.status === 'succeeded')
    if (!target) return
    selectedResultGenerationId.value = target.id
    resultSelectionPinned = true
    if (generationSubmitNotice.value.startsWith('新结果：')) generationSubmitNotice.value = ''
  }

  const briefDraft = ref<BriefFields | null>(null)
  const briefEditing = ref(false)
  const briefFieldErrors = ref<Partial<Record<EditableBriefKey, string>>>({})
  const briefAction = ref<BriefAction | null>(null)
  const briefSaveError = ref('')
  const briefConfirmError = ref('')
  const briefSaveNotice = ref('')
  const briefConflict = ref(false)
  const briefLocalCopy = ref(false)

  const referencePending = shallowRef<PendingReference | null>(null)
  const referenceError = ref('')
  const referenceNotice = ref('')
  const referenceAction = ref<ReferenceAction | null>(null)
  const referenceChecking = ref(false)
  const referenceConflict = ref(false)
  let referenceToken = 0
  const referenceCapability = computed<boolean | null>(
    () => configStatus.value?.visual.capabilities?.reference_image ?? null,
  )
  const referenceBusy = computed(
    () => referenceChecking.value || referenceAction.value !== null,
  )
  const referenceLimitBytes = computed(() => {
    const caps = configStatus.value?.visual.capabilities
    let limit = 10_000_000
    for (const value of [caps?.provider_max_reference_bytes, caps?.effective_reference_max_bytes]) {
      if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
        limit = Math.min(limit, value)
      }
    }
    return Math.max(0, limit)
  })
  const referenceMimeTypes = computed<string[]>(() => {
    const configured = configStatus.value?.visual.capabilities?.reference_mime_types
    if (!configured?.length) return ['image/png', 'image/jpeg']
    const normalized = configured
      .map((value) => value.trim().toLowerCase())
      .map((value) => (value === 'image/jpg' ? 'image/jpeg' : value))
      .filter((value) => value === 'image/png' || value === 'image/jpeg')
    return normalized.length ? [...new Set(normalized)] : ['image/png', 'image/jpeg']
  })
  const referenceGenerationBlockedReason = computed(() => {
    const project = currentProject.value
    if (!project?.reference_asset) return ''
    if (project.reference_asset.availability === 'missing') {
      return '参考图文件缺失；请重新上传或移除参考图后再生成图片。'
    }
    if (referenceCapability.value === false) {
      return '当前模型明确不支持参考图；请先移除参考图再生成图片。'
    }
    if (referenceCapability.value !== true) {
      return '参考图能力尚未核实；带参考图时不能生成图片。'
    }
    return ''
  })
  const referenceUploadDisabledReason = computed(() => {
    if (generationSubmitBusy.value || generationRetrieveBusy.value || activeGeneration.value) return '生成任务进行中，完成前不能修改参考图。'
    if (referenceBusy.value) return '正在处理参考图，请稍候。'
    if (creationUnknown.value) return '项目创建结果尚未确认，请先从已有项目核实。'
    if (referenceConflict.value) return '参考图已在别处更新，请先加载最新项目。'
    if (!configStatus.value) return '正在读取模型配置，请稍候。'
    if (referenceCapability.value === false) return '当前模型明确不支持参考图。'
    return ''
  })
  const referenceRemoveDisabledReason = computed(() => {
    if (generationSubmitBusy.value || generationRetrieveBusy.value || activeGeneration.value) return '生成任务进行中，完成前不能修改参考图。'
    if (referenceBusy.value) return '正在处理参考图，请稍候。'
    if (!currentProject.value) return '尚未选择项目。'
    if (!currentProject.value.reference_asset) return '当前没有参考图。'
    if (referenceConflict.value) return '参考图已在别处更新，请先加载最新项目。'
    return ''
  })
  const briefBusy = computed(() => briefAction.value !== null)
  const briefDirty = computed(() => {
    const project = currentProject.value
    const fields = briefDraft.value
    if (!project || !fields) return false
    return editableBriefFields.some((field) => fields[field.key] !== project.brief[field.key])
  })
  const draftDirty = computed(() => draft.value.trim().length > 0)
  const hasUnsavedChanges = computed(() => draftDirty.value || briefDirty.value)
  const promptStale = computed(() => {
    const project = currentProject.value
    if (!project) return false
    return !project.brief.prompt.trim() || project.prompt_revision !== project.revision
  })

  function briefFieldsFrom(brief: Brief): BriefFields {
    return {
      product_description: brief.product_description,
      purpose: brief.purpose,
      style: brief.style,
      composition: brief.composition,
      text_content: brief.text_content,
    }
  }

  function resetBriefDraft(project: ProjectView | null): void {
    briefDraft.value = project ? briefFieldsFrom(project.brief) : null
    briefFieldErrors.value = {}
    briefLocalCopy.value = false
  }

  const sendDisabledReason = computed(() => {
    if (sendBusy.value) return '正在发送消息，请稍候。'
    if (generationSubmitBusy.value) return '正在提交生成请求，完成前不能发送消息。'
    if (generationRetrieveBusy.value) return '正在重取结果，完成前不能发送消息。'
    if (activeGeneration.value) return '生成任务进行中，完成前不能发送消息。'
    if (referenceBusy.value) return '正在处理参考图，完成前不能发送消息。'
    if (briefBusy.value) return '正在处理需求卡片，完成前不能发送新消息。'
    if (organizing.value) return '正在整理需求，完成前不能发送新消息。'
    if (briefConflict.value) return '需求已在别处更新，请先加载最新需求，再发送消息。'
    if (briefLocalCopy.value) return '正在查看未保存的本地副本，请先加载最新需求，再发送消息。'
    if (briefDirty.value) return '有未保存的需求修改，请先保存或取消修改，再发送消息。'
    if (creationUnknown.value) return '项目创建结果尚未确认，请先从已有项目核实。'
    const llm = configStatus.value?.llm
    if (!llm) return '正在读取模型配置，请稍候。'
    if (!llm.configured) return 'LLM 尚未配置，当前无法整理需求；输入内容会保留。'
    if (!llm.ready) return 'LLM 当前不可用，当前无法整理需求；输入内容会保留。'
    if (!draft.value.trim()) return '请输入商品与图片需求。'
    return ''
  })

  const saveDisabledReason = computed(() => {
    if (briefBusy.value) return '正在处理需求操作，请稍候。'
    if (generationSubmitBusy.value || generationRetrieveBusy.value || activeGeneration.value) return '生成任务进行中，完成前不能保存需求。'
    if (referenceBusy.value) return '正在处理参考图，完成前不能保存需求。'
    if (sendBusy.value) return '正在发送消息，完成前不能保存需求。'
    if (!currentProject.value) return '尚未选择项目。'
    if (briefConflict.value) return '需求已在别处更新，请先加载最新需求。'
    if (briefLocalCopy.value) return '正在查看未保存的本地副本，请先加载最新需求。'
    if (!briefEditing.value) return '请先进入编辑状态。'
    if (!briefDirty.value) return '尚未修改需求。'
    return ''
  })

  const confirmDisabledReason = computed(() => {
    if (sendBusy.value) return '正在发送消息，完成前不能确认需求。'
    if (generationSubmitBusy.value || generationRetrieveBusy.value || activeGeneration.value) return '生成任务进行中，完成前不能确认需求。'
    if (referenceBusy.value) return '正在处理参考图，完成前不能确认需求。'
    if (briefBusy.value) return '正在处理需求操作，请稍候。'
    const project = currentProject.value
    if (!project) return '尚未选择项目。'
    if (briefConflict.value) return '需求已在别处更新，请先加载最新需求。'
    if (briefLocalCopy.value) return '正在查看未保存的本地副本，请先加载最新需求。'
    if (briefDirty.value) return '有未保存的修改，请先保存或取消修改。'
    if (draft.value.trim()) return '请先发送或清空输入框中的文字。'
    if (project.brief_status === 'confirmed') return '需求已经确认，无需重复确认。'
    if (project.brief_status === 'organizing') return '正在整理需求，完成前不能确认。'
    if (project.brief_status === 'needs_input') return '还有必需澄清未完成，请先在对话中补充。'
    if (project.brief_status !== 'review') return '当前需求还不能确认，请先重新整理需求。'
    const fields = briefDraft.value ?? briefFieldsFrom(project.brief)
    if (!fields.product_description.trim()) return '商品描述不能为空。'
    if (!project.brief.prompt.trim() || project.prompt_revision !== project.revision) {
      return '完整提示词不是当前版本，请重新整理需求后再确认。'
    }
    if (!project.brief.image_size) return '图片尺寸尚未配置，无法确认需求。'
    return ''
  })

  const generationDisabledReason = computed(() => {
    if (generationSubmitBusy.value) return '正在提交生成请求，请稍候。'
    if (generationRetrieveBusy.value) return '正在重取结果，完成前不能生成图片。'
    if (sendBusy.value) return '正在发送消息，完成前不能生成图片。'
    if (briefBusy.value) return '正在处理需求操作，完成前不能生成图片。'
    if (referenceBusy.value) return '正在处理参考图，完成前不能生成图片。'
    if (organizing.value) return '正在整理需求，完成前不能生成图片。'
    const project = currentProject.value
    if (!project) return '尚未选择项目。'
    if (generationUncertain.value) return '上一次生成提交结果尚未确认，请先核实。'
    if (creationUnknown.value) return '项目创建结果尚未确认，请先从已有项目核实。'
    if (briefConflict.value) return '需求已在别处更新，请先加载最新需求，再生成图片。'
    if (briefLocalCopy.value) return '正在查看未保存的本地副本，请先加载最新需求，再生成图片。'
    if (draft.value.trim()) return '请先发送或清空输入框中的文字，再生成图片。'
    if (briefEditing.value) return '请先完成或取消需求编辑，再生成图片。'
    if (briefDirty.value) return '有未保存的需求修改，请先保存或取消修改，再生成图片。'
    if (project.brief_status !== 'confirmed') return '需求尚未确认，确认后才能生成图片。'
    if (project.revision !== project.prompt_revision || project.revision !== project.confirmed_revision) {
      return '需求或提示词已变化，请重新整理并确认需求。'
    }
    if (!project.brief.prompt.trim()) return '完整提示词尚未生成，请先重新整理需求。'
    const visual = configStatus.value?.visual
    if (!visual) return '正在读取模型配置，请稍候。'
    if (!visual.configured) return '图片模型尚未配置，当前无法生成图片。'
    if (!visual.ready) return '图片模型当前不可用，请稍后重试。'
    if (visual.capabilities?.text_to_image === false) return '当前模型不支持文生图，不能生成图片。'
    if (visual.capabilities?.text_to_image !== true) return '文生图能力尚未核实，不能生成图片。'
    if (!project.brief.image_size) return '图片尺寸尚未确定，不能生成图片。'
    if (
      visual.capabilities?.supported_image_sizes?.length &&
      !visual.capabilities.supported_image_sizes.includes(project.brief.image_size)
    ) {
      return `当前模型不支持 ${project.brief.image_size} 尺寸。`
    }
    if (referenceGenerationBlockedReason.value) return referenceGenerationBlockedReason.value
    if (activeGeneration.value) {
      return activeGeneration.value.status === 'queued'
        ? '已有生成任务正在排队，请等待完成。'
        : '已有生成任务正在进行，请等待完成。'
    }
    return ''
  })

  const generationRetrieveDisabledReason = computed(() => {
    if (generationRetrieveBusy.value) return '正在重取结果，请稍候。'
    if (generationSubmitBusy.value) return '正在提交生成请求，完成前不能重取结果。'
    if (sendBusy.value || briefBusy.value || referenceBusy.value) {
      return '正在处理其他操作，完成前不能重取结果。'
    }
    if (organizing.value) return '正在整理需求，完成前不能重取结果。'
    if (generationUncertain.value) return '生成提交结果尚未确认，请先核实。'
    if (!currentProject.value) return '尚未选择项目。'
    if (briefConflict.value || briefLocalCopy.value) {
      return '存在未处理的本地需求副本，请先加载最新需求再重取结果。'
    }
    if (briefEditing.value || briefDirty.value) {
      return '有未保存的需求修改，请先保存或取消修改，再重取结果。'
    }
    const record = resultRetrievalGeneration.value
    if (!record) return '没有可重取的结果记录。'
    if (!record.can_retrieve_result) return resultRetrievalUnavailableReason.value
    return ''
  })

  const stages = computed<StageView[]>(() => {
    const project = currentProject.value
    const hasProject = Boolean(projectParam.value)
    const hasReviewableBrief = Boolean(
      project && (project.brief_status === 'review' || project.brief_status === 'confirmed'),
    )
    const hasConfirmedBrief = project?.brief_status === 'confirmed'
    const hasGeneration = projectGenerations.value.some((item) => item.status === 'succeeded')
    const referenceBlocked = referenceGenerationBlockedReason.value
    return [
      { id: 'describe', label: '描述需求', locked: false, reason: '' },
      {
        id: 'confirm',
        label: '确认需求',
        locked: !hasReviewableBrief,
        reason: hasProject
          ? '当前项目还没有可确认的需求，请先完成需求整理。'
          : '尚未选择项目，确认需求将在选择项目后解锁。',
      },
      {
        id: 'generate',
        label: '生成图片',
        locked: !hasConfirmedBrief || Boolean(referenceBlocked),
        reason: referenceBlocked || (hasProject
          ? '需求尚未确认，确认后才能生成图片。'
          : '尚未选择项目，生成图片将在需求确认后解锁。'),
      },
      {
        id: 'results',
        label: '查看结果',
        locked: !hasGeneration,
        reason: '尚未生成图片，暂无可查看的结果。',
      },
    ]
  })

  function announce(message: string): void {
    if (!message || message === lastAnnouncement) return
    lastAnnouncement = message
    announcement.value = ''
    void nextTick(() => {
      announcement.value = message
    })
  }

  function updateProjectUrl(id: string | null, mode: 'push' | 'replace'): void {
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    if (id) url.searchParams.set('project', id)
    else url.searchParams.delete('project')
    const next = url.pathname + url.search + url.hash
    if (mode === 'push') window.history.pushState({ project: id }, '', next)
    else window.history.replaceState({ project: id }, '', next)
  }

  function openNavigationConfirm(intent: NavigationIntent): void {
    closeProjectDrawer()
    closeSideDrawer()
    navigationIntent.value = intent
    navigationConfirmOpen.value = true
    announce('当前有未保存的修改，请确认是否放弃。')
  }

  function cancelNavigation(): void {
    navigationIntent.value = null
    navigationConfirmOpen.value = false
    announce('已保留当前修改。')
  }

  function confirmNavigation(): void {
    const intent = navigationIntent.value
    navigationIntent.value = null
    navigationConfirmOpen.value = false
    if (!intent) return
    if (intent.kind === 'new') {
      performCreateNewProject()
      return
    }
    if (intent.kind === 'project') {
      performSelectProject(intent.projectId)
      return
    }
    performHistoryNavigation(intent.projectId)
  }

  function clearProjectDetail(): void {
    detailController?.abort()
    detailController = null
    detailToken += 1
    projectContextToken += 1
    resetGenerationState()
    currentProject.value = null
    projectMessages.value = []
    projectGenerations.value = []
    resetBriefDraft(null)
    briefEditing.value = false
    briefSaveError.value = ''
    briefConfirmError.value = ''
    briefSaveNotice.value = ''
    briefConflict.value = false
    clearReferenceState()
    projectLoading.value = false
    projectError.value = ''
    projectErrorKind.value = null
  }

  function createNewProject(): void {
    if (hasUnsavedChanges.value) {
      openNavigationConfirm({ kind: 'new' })
      return
    }
    performCreateNewProject()
  }

  function performCreateNewProject(): void {
    creationUnknown.value = false
    sendError.value = ''
    briefAction.value = null
    clearProjectDetail()
    projectParam.value = null
    draft.value = ''
    stage.value = 'describe'
    sideDrawerOpen.value = false
    projectDrawerOpen.value = false
    updateProjectUrl(null, 'replace')
    announce('已新建空白工作台，请输入商品与图片需求。')
  }

  function fillExample(text: string): void {
    draft.value = text
    announce('示例已填入输入框，尚未发送。')
  }

  function toggleSidebar(): void {
    if (isMobile.value) {
      sideDrawerOpen.value = !sideDrawerOpen.value
      return
    }
    sidebarExpanded.value = !sidebarExpanded.value
  }

  function openSideDrawer(): void {
    projectDrawerOpen.value = false
    sideDrawerOpen.value = true
  }
  function closeSideDrawer(): void {
    sideDrawerOpen.value = false
  }
  function openModelDrawer(): void {
    sideDrawerOpen.value = false
    projectDrawerOpen.value = false
    modelDrawerOpen.value = true
  }
  function closeModelDrawer(): void {
    modelDrawerOpen.value = false
  }
  function openProjectDrawer(): void {
    sideDrawerOpen.value = false
    modelDrawerOpen.value = false
    projectDrawerOpen.value = true
    void loadProjects()
  }
  function closeProjectDrawer(): void {
    projectDrawerOpen.value = false
  }

  async function loadProjects(): Promise<void> {
    if (projectListLoading.value) return
    listController?.abort()
    moreController?.abort()
    const controller = new AbortController()
    listController = controller
    projectListLoading.value = true
    projectListLoadingMore.value = false
    projectListError.value = ''
    projectListMoreError.value = ''
    try {
      const response = await api.listProjects({ limit: 20 }, controller.signal)
      if (controller.signal.aborted) return
      projectList.value = dedupeProjects(response.items)
      nextCursor.value = response.next_cursor
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) return
      projectListError.value =
        error instanceof ApiError ? error.message : '项目列表读取失败，请稍后重试。'
    } finally {
      if (listController === controller) {
        listController = null
        projectListLoading.value = false
      }
    }
  }

  async function appendPage(cursor: string): Promise<void> {
    if (projectListLoadingMore.value || !cursor) return
    moreController?.abort()
    const controller = new AbortController()
    moreController = controller
    projectListLoadingMore.value = true
    projectListMoreError.value = ''
    try {
      const response = await api.listProjects({ limit: 20, cursor }, controller.signal)
      if (controller.signal.aborted) return
      projectList.value = dedupeProjects([...projectList.value, ...response.items])
      nextCursor.value = response.next_cursor
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) return
      projectListMoreError.value =
        error instanceof ApiError ? error.message : '加载更多项目失败，请稍后重试。'
    } finally {
      if (moreController === controller) {
        moreController = null
        projectListLoadingMore.value = false
      }
    }
  }

  async function loadMoreProjects(): Promise<void> {
    const cursor = nextCursor.value
    if (!cursor) return
    await appendPage(cursor)
  }

  async function retryLoadMore(): Promise<void> {
    const cursor = nextCursor.value
    if (!cursor) return
    projectListMoreError.value = ''
    await appendPage(cursor)
  }

  async function loadProject(id: string): Promise<void> {
    if (!isUuid(id)) {
      clearProjectDetail()
      projectErrorKind.value = 'invalid'
      projectError.value = '项目链接无效，请检查地址或从项目列表重新选择。'
      return
    }
    const restoredCredential = readGenerationCredential(id)
    detailController?.abort()
    const controller = new AbortController()
    detailController = controller
    const token = ++detailToken
    projectContextToken += 1
    resetGenerationState()
    stage.value = 'describe'
    projectLoading.value = true
    projectError.value = ''
    projectErrorKind.value = null
    currentProject.value = null
    projectMessages.value = []
    projectGenerations.value = []
    resetBriefDraft(null)
    briefEditing.value = false
    clearReferenceState()
    try {
      const response = await api.getProject(id, controller.signal)
      if (controller.signal.aborted || token !== detailToken) return
      currentProject.value = response.project
      projectMessages.value = response.messages
      projectGenerations.value = response.generations
      reconcileResultSelection(true)
      resetBriefDraft(response.project)
      briefEditing.value = false
      briefSaveError.value = ''
      briefConfirmError.value = ''
      briefSaveNotice.value = ''
      briefConflict.value = false
      creationUnknown.value = false
      await restoreGenerationCredential(restoredCredential, response.generations, controller.signal)
      if (
        !controller.signal.aborted &&
        token === detailToken &&
        currentProject.value?.id === response.project.id &&
        (organizing.value || activeGeneration.value)
      ) {
        scheduleStatusQuery()
      }
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted || token !== detailToken) return
      if (error instanceof ApiError) {
        if (error.status === 404) {
          projectErrorKind.value = 'not-found'
          projectError.value = '项目不存在或已被删除，请从项目列表重新选择。'
        } else if (error.status === 0) {
          projectErrorKind.value = 'network'
          projectError.value = '无法连接本机服务，请确认服务已启动后重试。'
        } else {
          projectErrorKind.value = 'generic'
          projectError.value = error.message
        }
      } else {
        projectErrorKind.value = 'generic'
        projectError.value = '项目读取失败，请稍后重试。'
      }
    } finally {
      if (token === detailToken) {
        projectLoading.value = false
        if (detailController === controller) detailController = null
      }
    }
  }

  async function findKnownGeneration(
    request: PendingGenerationRequest,
    generations: GenerationView[],
    signal?: AbortSignal,
  ): Promise<GenerationView | null> {
    const inProject = generations.find((item) => item.request_id === request.requestId)
    if (inProject) return inProject
    if (!request.generationId) return null
    try {
      const known = await api.getGeneration(request.generationId, signal)
      if (known.request_id !== request.requestId) return null
      return known
    } catch {
      return null
    }
  }

  async function restoreGenerationCredential(
    credential: PendingGenerationRequest | null,
    generations: GenerationView[],
    signal?: AbortSignal,
  ): Promise<void> {
    if (!credential || signal?.aborted) return
    const match = await findKnownGeneration(credential, generations, signal)
    if (signal?.aborted || currentProject.value?.id !== credential.projectId) return
    pendingGenerationRequest = credential
    generationPendingRestored.value = true
    if (!match) {
      generationUncertain.value = true
      generationSubmitNotice.value = '已恢复上次未确认的生成提交。'
      generationSubmitError.value =
        '仍未查到这次生成提交的记录；这不代表失败。可先只读核实，或明确恢复本次提交。'
      return
    }
    upsertGeneration(match)
    pendingGenerationRequest = null
    generationUncertain.value = false
    generationPendingRestored.value = false
    clearGenerationCredential()
    if (match.status === 'queued' || match.status === 'running') {
      generationSubmitNotice.value = '已恢复上次生成提交，正在继续读取任务状态。'
      scheduleStatusQuery()
      announce(generationSubmitNotice.value)
      return
    }
    if (match.status === 'succeeded') {
      selectedResultGenerationId.value = match.id
      resultSelectionPinned = false
      stage.value = 'results'
      generationSubmitNotice.value = '已恢复上次生成结果，已切换到结果视图。'
      announce(generationSubmitNotice.value)
      return
    }
    generationSubmitNotice.value = '已恢复上次生成提交记录。'
    announce(generationSubmitNotice.value)
  }

  function modelConfigReason(): string {
    const llm = configStatus.value?.llm
    if (!llm) return '正在读取模型配置，请稍候。'
    if (!llm.configured) return 'LLM 尚未配置，当前无法整理需求；输入内容会保留。'
    if (!llm.ready) return 'LLM 当前不可用，当前无法整理需求；输入内容会保留。'
    return ''
  }

  function errorMessage(error: unknown, fallback: string): string {
    return error instanceof ApiError ? error.message : fallback
  }

  function writeErrorMessage(error: unknown, fallback: string): string {
    if (!(error instanceof ApiError)) return fallback
    if (error.status === 401 || error.status === 403) {
      return '认证失败或没有权限，请检查模型配置后重试；输入内容已保留。'
    }
    if (error.status === 409) {
      return '需求已在别处更新，请重新读取最新内容后再发送；输入内容已保留。'
    }
    if (error.status === 429) {
      return '请求过于频繁或额度不足，请稍后再试；输入内容已保留。'
    }
    if (error.status === 503) {
      return 'LLM 尚未配置或当前不可用，请检查模型状态；输入内容已保留。'
    }
    return error.message || fallback
  }

  function isUnconfirmedWrite(error: unknown): boolean {
    return error instanceof ApiError && (error.status === 0 || error.status >= 500)
  }

  const GENERATION_CREDENTIAL_KEY = 'ecommerce-generation-pending'

  function createGenerationRequestId(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
      const random = Math.floor(Math.random() * 16)
      const value = char === 'x' ? random : (random & 0x3) | 0x8
      return value.toString(16)
    })
  }

  function buildGenerationRequestBody(request: PendingGenerationRequest): SubmitGenerationRequest {
    const body: SubmitGenerationRequest = {
      request_id: request.requestId,
      confirmed_revision: request.confirmedRevision,
    }
    if (request.acknowledgeUnknownPresent) {
      body.acknowledge_unknown = request.acknowledgeUnknownValue === true
    }
    return body
  }

  function normalizeGenerationCredential(
    value: unknown,
    projectId: string,
  ): PendingGenerationRequest | null {
    if (!value || typeof value !== 'object') return null
    const record = value as Record<string, unknown>
    if (record.projectId !== projectId) return null
    if (typeof record.requestId !== 'string' || !record.requestId.trim()) return null
    if (record.requestId.length > 200) return null
    const revision = record.confirmedRevision
    if (typeof revision !== 'number' || !Number.isInteger(revision) || revision < 0) return null
    if (typeof record.acknowledgeUnknownPresent !== 'boolean') return null
    const present = record.acknowledgeUnknownPresent
    const acknowledgeValue = present ? record.acknowledgeUnknownValue : null
    if (present && typeof acknowledgeValue !== 'boolean') return null
    let generationId: string | null = null
    if (record.generationId !== undefined && record.generationId !== null) {
      if (typeof record.generationId !== 'string' || !record.generationId) return null
      generationId = record.generationId
    }
    return {
      projectId,
      requestId: record.requestId,
      confirmedRevision: revision,
      acknowledgeUnknownPresent: present,
      acknowledgeUnknownValue: present ? (acknowledgeValue as boolean) : null,
      generationId,
    }
  }

  /** 只读取同一项目的原请求；格式不完整、解析失败或读取失败一律返回 null。 */
  function readGenerationCredential(projectId: string): PendingGenerationRequest | null {
    if (typeof window === 'undefined') return null
    try {
      const raw = window.sessionStorage.getItem(GENERATION_CREDENTIAL_KEY)
      if (!raw) return null
      return normalizeGenerationCredential(JSON.parse(raw) as unknown, projectId)
    } catch {
      return null
    }
  }

  /** 返回是否成功写入；失败时调用方仍保留内存中的原请求。 */
  function saveGenerationCredential(request: PendingGenerationRequest): boolean {
    if (typeof window === 'undefined') return false
    try {
      window.sessionStorage.setItem(
        GENERATION_CREDENTIAL_KEY,
        JSON.stringify({
          projectId: request.projectId,
          requestId: request.requestId,
          confirmedRevision: request.confirmedRevision,
          acknowledgeUnknownPresent: request.acknowledgeUnknownPresent,
          acknowledgeUnknownValue: request.acknowledgeUnknownValue,
          generationId: request.generationId,
        }),
      )
      return true
    } catch {
      // 会话凭据只是刷新后的辅助信息，写入失败不影响当前提交。
      return false
    }
  }

  function clearGenerationCredential(): void {
    if (typeof window === 'undefined') return
    try {
      window.sessionStorage.removeItem(GENERATION_CREDENTIAL_KEY)
    } catch {
      // 清理失败不影响主流程。
    }
  }

  function clearSettledGenerationCredential(generation: GenerationView): void {
    const request = pendingGenerationRequest
    if (!request || request.requestId !== generation.request_id) return
    if (generation.status === 'queued' || generation.status === 'running') return
    pendingGenerationRequest = null
    generationUncertain.value = false
    generationPendingRestored.value = false
    generationCredentialWarning.value = ''
    clearGenerationCredential()
  }

  function clearGenerationPoll(): void {
    if (generationPollTimer !== null) {
      clearTimeout(generationPollTimer)
      generationPollTimer = null
    }
  }

  function pageIsHidden(): boolean {
    return typeof document !== 'undefined' && document.visibilityState === 'hidden'
  }

  function clearStatusQueryError(): void {
    generationQueryError.value = ''
    statusQueryTarget.value = null
    statusQueryFailureKind.value = null
  }

  function setStatusQueryFailure(
    kind: 'connection' | 'resource',
    target: 'project' | 'generation',
    message: string,
  ): void {
    generationQueryError.value = message
    statusQueryFailureKind.value = kind
    statusQueryTarget.value = target
  }

  function pauseStatusQueriesForHiddenPage(): void {
    clearGenerationPoll()
    generationQueryController?.abort()
    generationRefreshController?.abort()
  }

  function resetGenerationState(): void {
    generationToken += 1
    clearGenerationPoll()
    generationQueryController?.abort()
    generationQueryController = null
    generationRefreshController?.abort()
    generationRefreshController = null
    generationSubmitBusy.value = false
    generationQuerying.value = false
    generationSubmitError.value = ''
    generationSubmitNotice.value = ''
    generationUncertain.value = false
    generationPendingRestored.value = false
    generationCredentialWarning.value = ''
    generationRecoveryAction.value = null
    generationRiskDialogOpen.value = false
    generationRetrieveController?.abort()
    generationRetrieveController = null
    generationRetrieveBusy.value = false
    generationRetrieveError.value = ''
    generationRetrieveNotice.value = ''
    generationQueryError.value = ''
    statusQueryTarget.value = null
    statusQueryFailureKind.value = null
    generationQueryFailures = 0
    pendingGenerationRequest = null
    selectedResultGenerationId.value = null
    resultSelectionPinned = false
  }

  function upsertGeneration(generation: GenerationView): void {
    const index = projectGenerations.value.findIndex(
      (item) => item.id === generation.id || item.request_id === generation.request_id,
    )
    const previousSelected = selectedResultGenerationId.value
    const previousLatestId = latestSucceededGeneration.value?.id ?? null
    const wasFollowingLatest = !previousSelected || previousSelected === previousLatestId
    if (index < 0) {
      projectGenerations.value = [...projectGenerations.value, generation]
    } else {
      const next = [...projectGenerations.value]
      next[index] = generation
      projectGenerations.value = next
    }
    if (generation.status === 'succeeded' && wasFollowingLatest && !resultSelectionPinned) {
      selectedResultGenerationId.value = generation.id
      resultSelectionPinned = false
    } else if (!selectedResultGenerationId.value) {
      selectedResultGenerationId.value = latestSucceededGeneration.value?.id ?? null
    }
  }

  function writeGenerationErrorMessage(error: unknown): string {
    if (!(error instanceof ApiError)) return '生成请求提交失败，请稍后重试。'
    switch (error.code) {
      case 'REVISION_CONFLICT': return '需求已在别处更新，请重新读取最新需求后再生成。'
      case 'PROJECT_BUSY': return '项目正在处理其他操作，请稍后再生成。'
      case 'ACTIVE_GENERATION_EXISTS': return '已有生成任务正在进行，请等待完成后再生成。'
      case 'IDEMPOTENCY_CONFLICT': return '同一个请求标识已用于其他参数，请重新发起生成。'
      case 'UNKNOWN_ACK_REQUIRED': return '上一次生成结果未确认，需要明确确认风险后再生成。'
      case 'BRIEF_NOT_READY': return '当前需求尚未确认，不能生成图片。'
      case 'IMAGE_SIZE_CHANGED': return '图片尺寸已变化，请重新整理并确认需求后再生成。'
      case 'REFERENCE_NOT_SUPPORTED': return '当前模型不支持参考图，请移除参考图后再生成。'
      case 'MODEL_NOT_CONFIGURED': return '图片模型尚未配置，不能生成图片。'
      case 'ADAPTER_UNAVAILABLE': return '图片模型当前不可用，请稍后重试。'
      case 'MODEL_CAPABILITY_MISMATCH': return '当前模型能力与需求不匹配，请检查模型配置。'
      default: break
    }
    if (error.status === 401 || error.status === 403) return '认证失败或没有权限，请检查模型配置后再生成。'
    if (error.status === 404) return '项目不存在或已被删除，请刷新项目列表。'
    if (error.status === 413) return '生成请求内容过大，请精简参考图或需求后重试。'
    if (error.status === 422) return '生成参数不符合要求，请检查需求与参考图后重试。'
    if (error.status === 429) return '请求过于频繁或额度不足，请稍后再试。'
    if (error.status === 409) return '项目状态已变化，请加载最新状态后再生成。'
    if (error.status === 503) return '图片模型尚未配置或当前不可用，请检查模型状态。'
    return error.message || '生成请求提交失败，请稍后重试。'
  }

  function scheduleStatusQuery(delay = 2000): void {
    clearGenerationPoll()
    const project = currentProject.value
    if (!project || pageIsHidden()) return
    if (!organizing.value && !activeGeneration.value) return
    const token = generationToken
    const projectId = project.id
    generationPollTimer = setTimeout(() => {
      generationPollTimer = null
      if (token !== generationToken || projectParam.value !== projectId) return
      if (pageIsHidden()) return
      void runStatusQuery()
    }, delay)
  }

  /**
   * 工作台唯一的自动状态查询入口。每个时刻只查询一个对象：
   * 需求整理中读取项目，存在排队/运行中的生成时读取该生成记录。
   */
  async function runStatusQuery(manual = false): Promise<void> {
    if (generationQuerying.value) return
    const project = currentProject.value
    if (!project) return
    if (organizing.value) {
      await queryProjectStatus(manual)
      return
    }
    const active = activeGeneration.value
    if (active) await queryGeneration(active.id, manual)
  }

  async function queryProjectStatus(manual = false): Promise<void> {
    if (generationQuerying.value) return
    const project = currentProject.value
    if (!project) return
    clearGenerationPoll()
    const token = generationToken
    const projectId = project.id
    const controller = new AbortController()
    generationQueryController = controller
    generationQuerying.value = true
    if (manual) statusQueryTarget.value = 'project'
    try {
      const response = await api.getProject(projectId, controller.signal)
      if (token !== generationToken || controller.signal.aborted || projectParam.value !== projectId) return
      adoptProjectDetail(response)
      generationQueryFailures = 0
      clearStatusQueryError()
      if (response.project.brief_status === 'organizing') {
        scheduleStatusQuery()
        return
      }
      if (activeGeneration.value) {
        scheduleStatusQuery()
      } else if (response.project.brief_error) {
        announce('需求整理已中断，请查看最新错误信息。')
      } else {
        announce('需求整理已完成，已读取最新状态。')
      }
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted || token !== generationToken) return
      if (error instanceof ApiError && error.status === 404) {
        setStatusQueryFailure(
          'resource',
          'project',
          '资源不存在：项目不存在或已被删除，无法继续读取状态。',
        )
        return
      }
      generationQueryFailures += 1
      if (generationQueryFailures >= 3 || manual) {
        setStatusQueryFailure(
          'connection',
          'project',
          '暂时无法读取项目状态。已停止自动查询，请重新查询。',
        )
      } else {
        scheduleStatusQuery()
      }
    } finally {
      if (token === generationToken) generationQuerying.value = false
      if (generationQueryController === controller) generationQueryController = null
    }
  }

  async function queryGeneration(generationId: string, manual = false): Promise<void> {
    if (generationQuerying.value) return
    const project = currentProject.value
    if (!project) return
    clearGenerationPoll()
    const token = generationToken
    const controller = new AbortController()
    generationQueryController = controller
    generationQuerying.value = true
    if (manual) statusQueryTarget.value = 'generation'
    try {
      const generation = await api.getGeneration(generationId, controller.signal)
      if (token !== generationToken || controller.signal.aborted || projectParam.value !== project.id) return
      upsertGeneration(generation)
      clearSettledGenerationCredential(generation)
      generationQueryFailures = 0
      clearStatusQueryError()
      if (generation.status === 'queued' || generation.status === 'running') {
        scheduleStatusQuery()
      } else if (generation.status === 'succeeded') {
        await refreshGenerationProject(generation, token)
      }
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted || token !== generationToken) return
      if (error instanceof ApiError && error.status === 404) {
        setStatusQueryFailure(
          'resource',
          'generation',
          '资源不存在：生成记录不存在或已被删除，无法继续读取状态。',
        )
        return
      }
      generationQueryFailures += 1
      if (generationQueryFailures >= 3 || manual) {
        setStatusQueryFailure(
          'connection',
          'generation',
          '暂时无法读取生成状态。已停止自动查询，请手动重试。',
        )
      } else {
        scheduleStatusQuery()
      }
    } finally {
      if (token === generationToken) generationQuerying.value = false
      if (generationQueryController === controller) generationQueryController = null
    }
  }

  async function refreshGenerationProject(generation: GenerationView, token: number): Promise<void> {
    const projectId = generation.project_id
    const controller = new AbortController()
    generationRefreshController = controller
    const followLatest = !resultSelectionPinned || stage.value !== 'results'
    try {
      const response = await api.getProject(projectId, controller.signal)
      if (token !== generationToken || controller.signal.aborted || projectParam.value !== projectId) return
      adoptProjectDetail(response, { followLatestResult: followLatest })
      if (!projectGenerations.value.some((item) => item.id === generation.id || item.request_id === generation.request_id)) {
        upsertGeneration(generation)
      }
      const visible = selectedResultGeneration.value
      if (followLatest || !visible || visible.id === generation.id) {
        generationSubmitNotice.value = '图片已生成，已切换到结果视图。'
      } else {
        generationSubmitNotice.value = `新结果：第 ${generation.version_no} 版已生成。当前仍显示第 ${visible.version_no} 版。`
      }
      stage.value = 'results'
      announce(generationSubmitNotice.value)
      if (activeGeneration.value && activeGeneration.value.id !== generation.id) scheduleStatusQuery()
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted || token !== generationToken) return
      if (followLatest) {
        selectedResultGenerationId.value = generation.id
        resultSelectionPinned = false
      }
      upsertGeneration(generation)
      const visible = selectedResultGeneration.value
      if (visible && visible.id !== generation.id) {
        generationSubmitNotice.value = `新结果：第 ${generation.version_no} 版已生成。当前仍显示第 ${visible.version_no} 版。`
      } else {
        generationSubmitNotice.value = '图片已生成，但项目最新记录读取失败；已显示本次生成结果。'
      }
      stage.value = 'results'
      announce(generationSubmitNotice.value)
    } finally {
      if (generationRefreshController === controller) generationRefreshController = null
    }
  }

  async function submitGeneration(): Promise<void> {
    if (generationSubmitBusy.value) return
    const reason = generationDisabledReason.value
    if (reason) {
      generationSubmitError.value = reason
      return
    }
    if (latestGenerationView.value?.status === 'unknown') {
      generationRiskDialogOpen.value = true
      generationSubmitError.value = ''
      return
    }
    await performGenerationSubmit(false)
  }

  async function confirmGenerationRisk(): Promise<void> {
    if (!generationRiskDialogOpen.value) return
    generationRiskDialogOpen.value = false
    await performGenerationSubmit(latestGenerationView.value?.status === 'unknown')
  }

  function cancelGenerationRisk(): void {
    if (!generationRiskDialogOpen.value) return
    generationRiskDialogOpen.value = false
    announce('已返回查看，未发起新的生成。')
  }

  async function performGenerationSubmit(acknowledgeUnknown: boolean): Promise<void> {
    if (generationSubmitBusy.value) return
    const reason = generationDisabledReason.value
    if (reason) {
      generationSubmitError.value = reason
      return
    }
    if (!acknowledgeUnknown && latestGenerationView.value?.status === 'unknown') {
      generationRiskDialogOpen.value = true
      return
    }
    const project = currentProject.value
    if (!project) return
    const request: PendingGenerationRequest = {
      projectId: project.id,
      requestId: createGenerationRequestId(),
      confirmedRevision: project.revision,
      acknowledgeUnknownPresent: acknowledgeUnknown,
      acknowledgeUnknownValue: acknowledgeUnknown ? true : null,
      generationId: null,
    }
    const token = ++generationToken
    clearGenerationPoll()
    generationSubmitBusy.value = true
    generationRecoveryAction.value = null
    generationSubmitError.value = ''
    generationSubmitNotice.value = ''
    clearStatusQueryError()
    generationQueryFailures = 0
    generationPendingRestored.value = false
    pendingGenerationRequest = request
    const saved = saveGenerationCredential(request)
    generationCredentialWarning.value = saved
      ? ''
      : '无法写入会话存储；本次提交仍保留在当前页面，刷新或关闭页面后将无法恢复。'
    try {
      const response = await api.submitGeneration(project.id, buildGenerationRequestBody(request))
      if (token !== generationToken || projectParam.value !== project.id) return
      upsertGeneration(response.generation)
      clearSettledGenerationCredential(response.generation)
      if (response.generation.status === 'queued' || response.generation.status === 'running') {
        pendingGenerationRequest = null
        generationCredentialWarning.value = ''
        clearGenerationCredential()
      }
      generationSubmitNotice.value = '生成请求已提交，正在等待模型处理。'
      announce('生成请求已提交，正在等待模型处理。')
      if (response.generation.status === 'queued' || response.generation.status === 'running') {
        scheduleStatusQuery()
      } else if (response.generation.status === 'succeeded') {
        await refreshGenerationProject(response.generation, token)
      }
    } catch (error) {
      if (token !== generationToken) return
      if (isUnconfirmedWrite(error)) {
        generationUncertain.value = true
        generationSubmitError.value = ''
        announce('生成提交结果未确认，请先核实。')
      } else {
        pendingGenerationRequest = null
        generationPendingRestored.value = false
        generationCredentialWarning.value = ''
        clearGenerationCredential()
        generationSubmitError.value = writeGenerationErrorMessage(error)
      }
    } finally {
      if (token === generationToken) generationSubmitBusy.value = false
    }
  }

  async function verifyGenerationSubmission(): Promise<void> {
    if (generationSubmitBusy.value) return
    const request = pendingGenerationRequest
    if (!generationUncertain.value || !request) return
    const token = ++generationToken
    clearGenerationPoll()
    generationSubmitBusy.value = true
    generationRecoveryAction.value = 'verify'
    generationSubmitError.value = ''
    clearStatusQueryError()
    generationQueryFailures = 0
    try {
      const response = await api.getProject(request.projectId)
      if (token !== generationToken || projectParam.value !== request.projectId) return
      adoptProjectDetail(response)
      let match = response.generations.find((item) => item.request_id === request.requestId) ?? null
      if (!match) {
        const known = await findKnownGeneration(request, response.generations)
        if (token !== generationToken || projectParam.value !== request.projectId) return
        if (known) {
          upsertGeneration(known)
          match = known
        }
      }
      if (!match) {
        generationSubmitError.value = '仍未查到这次生成提交的记录；这不代表失败，请稍后再核实，不要重复提交。'
        return
      }
      generationUncertain.value = false
      generationPendingRestored.value = false
      generationCredentialWarning.value = ''
      pendingGenerationRequest = null
      clearGenerationCredential()
      generationSubmitNotice.value = '已核实生成提交，生成记录已恢复。'
      announce('已核实生成提交，生成记录已恢复。')
      if (match.status === 'queued' || match.status === 'running') {
        scheduleStatusQuery()
      } else if (match.status === 'succeeded') {
        await refreshGenerationProject(match, token)
      }
    } catch (error) {
      generationSubmitError.value = errorMessage(error, '核实生成提交结果失败，请稍后重试。')
    } finally {
      if (token === generationToken) generationSubmitBusy.value = false
      if (token === generationToken && generationRecoveryAction.value === 'verify') {
        generationRecoveryAction.value = null
      }
    }
  }

  /**
   * 手动恢复同一次提交：只使用上次保存的原 request_id、confirmed_revision
   * 与 acknowledge_unknown 存在性/值，绝不根据当前需求或配置重新计算。
   */
  async function resumeGenerationSubmission(): Promise<void> {
    if (generationSubmitBusy.value) return
    const request = pendingGenerationRequest
    const project = currentProject.value
    if (!generationUncertain.value || !request || !project || project.id !== request.projectId) return
    const token = ++generationToken
    clearGenerationPoll()
    generationSubmitBusy.value = true
    generationRecoveryAction.value = 'resume'
    generationSubmitError.value = ''
    generationSubmitNotice.value = ''
    clearStatusQueryError()
    generationQueryFailures = 0
    try {
      const response = await api.submitGeneration(
        request.projectId,
        buildGenerationRequestBody(request),
      )
      if (token !== generationToken || projectParam.value !== request.projectId) return
      upsertGeneration(response.generation)
      pendingGenerationRequest = null
      generationUncertain.value = false
      generationPendingRestored.value = false
      generationCredentialWarning.value = ''
      clearGenerationCredential()
      generationSubmitNotice.value = '已恢复本次提交，生成记录已恢复。'
      announce(generationSubmitNotice.value)
      if (response.generation.status === 'queued' || response.generation.status === 'running') {
        scheduleStatusQuery()
      } else if (response.generation.status === 'succeeded') {
        await refreshGenerationProject(response.generation, token)
      }
    } catch (error) {
      if (token !== generationToken) return
      if (isUnconfirmedWrite(error)) {
        generationUncertain.value = true
        generationSubmitError.value =
          '恢复提交的结果仍未确认；原始请求凭据仍保留，请稍后再核实，不要创建新的提交。'
        announce('恢复提交结果未确认，请保留当前凭据并稍后核实。')
      } else {
        generationSubmitError.value = writeGenerationErrorMessage(error)
      }
    } finally {
      if (token === generationToken) generationSubmitBusy.value = false
      if (token === generationToken && generationRecoveryAction.value === 'resume') {
        generationRecoveryAction.value = null
      }
    }
  }

  function retryGenerationQuery(): void {
    if (generationQuerying.value || generationSubmitBusy.value || generationRetrieveBusy.value) return
    if (organizing.value) {
      generationQueryFailures = 0
      void queryProjectStatus(true)
      return
    }
    const target = activeGeneration.value ?? latestGenerationView.value
    if (!target) return
    generationQueryFailures = 0
    void queryGeneration(target.id, true)
  }

  function writeRetrieveErrorMessage(error: unknown): string {
    if (!(error instanceof ApiError)) {
      return '重取结果失败，原记录与旧图片仍保留，请稍后重试。'
    }
    switch (error.code) {
      case 'PROJECT_BUSY':
        return '项目正在处理其他操作，暂时不能重取结果。'
      case 'RESULT_NOT_RETRIEVABLE':
        return '服务端表示该结果当前不能重取，请查看记录说明。'
      case 'RETRIEVE_CONFIG_CHANGED':
        return '模型配置已变化，无法重取这次结果；原记录和旧图片仍保留。'
      case 'RESULT_FETCH_FAILED':
        return '重取结果失败，原记录与旧图片仍保留，请稍后重试。'
      case 'RESULT_FETCH_TIMEOUT':
        return '重取结果超时，原记录与旧图片仍保留，请稍后重试。'
      case 'ADAPTER_UNAVAILABLE':
        return '图片模型当前不可用，暂时无法重取结果。'
      default:
        break
    }
    if (error.status === 401 || error.status === 403) return '认证失败或没有权限，无法重取结果。'
    if (error.status === 404) return '生成记录不存在或已被删除，无法重取结果。'
    if (error.status === 413) return '重取请求内容过大，无法处理。'
    if (error.status === 422) return '重取请求不符合要求，请检查生成记录。'
    if (error.status === 429) return '请求过于频繁或额度不足，请稍后再试。'
    if (error.status === 409) return '生成记录状态已变化，请加载最新状态后再试。'
    if (error.status === 503) return '图片模型或本地服务当前不可用，暂时无法重取结果。'
    return error.message || '重取结果失败，原记录与旧图片仍保留，请稍后重试。'
  }

  function isAmbiguousRetrieve(error: unknown): boolean {
    if (!(error instanceof ApiError)) return true
    if (error.status === 0) return true
    if (error.status < 500) return false
    return !['RESULT_FETCH_FAILED', 'RESULT_FETCH_TIMEOUT', 'ADAPTER_UNAVAILABLE'].includes(error.code)
  }

  async function refreshProjectAfterRetrieve(
    generation: GenerationView,
    token: number,
  ): Promise<void> {
    const projectId = generation.project_id
    const controller = new AbortController()
    generationRefreshController = controller
    const followLatest = !resultSelectionPinned
    try {
      const response = await api.getProject(projectId, controller.signal)
      if (token !== generationToken || controller.signal.aborted || projectParam.value !== projectId) return
      adoptProjectDetail(response, { followLatestResult: followLatest })
      if (
        !projectGenerations.value.some(
          (item) => item.id === generation.id || item.request_id === generation.request_id,
        )
      ) {
        upsertGeneration(generation)
      }
      const visible = selectedResultGeneration.value
      generationRetrieveNotice.value =
        followLatest || !visible || visible.id === generation.id
          ? '结果已重取，生成记录与版本已更新。'
          : `结果已重取：第 ${generation.version_no} 版已恢复。当前仍显示第 ${visible.version_no} 版。`
      announce(generationRetrieveNotice.value)
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted || token !== generationToken) return
      upsertGeneration(generation)
      generationRetrieveNotice.value = '结果已重取，但项目记录刷新失败；已更新本次结果。'
      announce(generationRetrieveNotice.value)
    } finally {
      if (generationRefreshController === controller) generationRefreshController = null
    }
  }

  async function verifyRetrieveOutcome(
    record: GenerationView,
    token: number,
  ): Promise<void> {
    try {
      const latest = await api.getGeneration(record.id)
      if (token !== generationToken || projectParam.value !== record.project_id) return
      upsertGeneration(latest)
      if (latest.status === 'succeeded') {
        generationRetrieveError.value = ''
        await refreshProjectAfterRetrieve(latest, token)
        return
      }
      generationRetrieveError.value =
        '重取结果的回执未确认，暂时无法判断是否成功。请稍后查看生成记录；不会自动重试。'
    } catch (error) {
      if (token !== generationToken || projectParam.value !== record.project_id) return
      if (error instanceof ApiError && error.status === 404) {
        generationRetrieveError.value = '生成记录不存在或已被删除，无法核实重取结果。'
        return
      }
      generationRetrieveError.value =
        '重取结果后无法读取生成记录，暂不能确认是否成功；原记录与旧图片仍保留。'
    }
  }

  async function retrieveGenerationResult(): Promise<void> {
    if (generationRetrieveBusy.value) return
    const reason = generationRetrieveDisabledReason.value
    if (reason) {
      generationRetrieveError.value = reason
      return
    }
    const record = resultRetrievalGeneration.value
    const project = currentProject.value
    if (!record || !project || record.project_id !== project.id) return
    const token = ++generationToken
    clearGenerationPoll()
    generationRetrieveBusy.value = true
    generationRetrieveError.value = ''
    generationRetrieveNotice.value = ''
    clearStatusQueryError()
    generationQueryFailures = 0
    const controller = new AbortController()
    generationRetrieveController = controller
    try {
      const updated = await api.retrieveGenerationResult(record.id, controller.signal)
      if (token !== generationToken || controller.signal.aborted || projectParam.value !== project.id) return
      upsertGeneration(updated)
      clearSettledGenerationCredential(updated)
      await refreshProjectAfterRetrieve(updated, token)
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted || token !== generationToken) return
      if (isAmbiguousRetrieve(error)) {
        await verifyRetrieveOutcome(record, token)
        return
      }
      generationRetrieveError.value = writeRetrieveErrorMessage(error)
    } finally {
      if (token === generationToken) generationRetrieveBusy.value = false
      if (generationRetrieveController === controller) generationRetrieveController = null
    }
  }


  function createPreviewUrl(file: File): string {
    if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return ''
    try {
      return URL.createObjectURL(file)
    } catch {
      return ''
    }
  }

  function releasePreview(url: string): void {
    if (!url || typeof URL === 'undefined' || typeof URL.revokeObjectURL !== 'function') return
    try {
      URL.revokeObjectURL(url)
    } catch {
      // 预览 URL 释放失败不影响上传流程。
    }
  }

  function clearReferencePending(): void {
    const pending = referencePending.value
    if (pending) releasePreview(pending.previewUrl)
    referencePending.value = null
  }

  function clearReferenceState(): void {
    referenceToken += 1
    clearReferencePending()
    referenceError.value = ''
    referenceNotice.value = ''
    referenceAction.value = null
    referenceChecking.value = false
    referenceConflict.value = false
  }

  interface ImageInfo {
    mimeType: 'image/png' | 'image/jpeg'
    width: number
    height: number
  }

  function readBlob(blob: Blob): Promise<ArrayBuffer> {
    if (typeof blob.arrayBuffer === 'function') return blob.arrayBuffer()
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        if (reader.result instanceof ArrayBuffer) resolve(reader.result)
        else reject(new Error('无法读取图片数据'))
      }
      reader.onerror = () => reject(reader.error ?? new Error('无法读取图片数据'))
      reader.readAsArrayBuffer(blob)
    })
  }

  function parsePng(bytes: Uint8Array): { width: number; height: number } | null {
    if (bytes.length < 24) return null
    const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
    if (!signature.every((value, index) => bytes[index] === value)) return null
    const chunkType = String.fromCharCode(bytes[12], bytes[13], bytes[14], bytes[15])
    if (chunkType !== 'IHDR') return null
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
    const width = view.getUint32(16)
    const height = view.getUint32(20)
    if (!width || !height) return null
    return { width, height }
  }

  function parseJpeg(bytes: Uint8Array): { width: number; height: number } | null {
    if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return null
    let index = 2
    while (index + 3 < bytes.length) {
      if (bytes[index] !== 0xff) {
        index += 1
        continue
      }
      let marker = bytes[index + 1]
      while (marker === 0xff && index + 2 < bytes.length) {
        index += 1
        marker = bytes[index + 1]
      }
      if (marker === 0xd8 || marker === 0xd9 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
        index += 2
        continue
      }
      if (marker === 0xda) return null
      if (index + 3 >= bytes.length) return null
      const segmentLength = (bytes[index + 2] << 8) | bytes[index + 3]
      if (segmentLength < 2 || index + 2 + segmentLength > bytes.length) return null
      const isStartOfFrame =
        marker >= 0xc0 &&
        marker <= 0xcf &&
        marker !== 0xc4 &&
        marker !== 0xc8 &&
        marker !== 0xcc
      if (isStartOfFrame) {
        if (index + 8 >= bytes.length) return null
        const height = (bytes[index + 5] << 8) | bytes[index + 6]
        const width = (bytes[index + 7] << 8) | bytes[index + 8]
        return width && height ? { width, height } : null
      }
      index += 2 + segmentLength
    }
    return null
  }

  async function inspectImage(file: File): Promise<ImageInfo | null> {
    const bytes = new Uint8Array(await readBlob(file.slice(0, 512 * 1024)))
    const png = parsePng(bytes)
    if (png) return { mimeType: 'image/png', ...png }
    const jpeg = parseJpeg(bytes)
    if (jpeg) return { mimeType: 'image/jpeg', ...jpeg }
    return null
  }

  function referenceFormatLabel(): string {
    return referenceMimeTypes.value
      .map((mime) => (mime === 'image/png' ? 'PNG' : 'JPEG'))
      .join(' 或 ')
  }

  function referenceWriteErrorMessage(error: unknown): string {
    if (!(error instanceof ApiError)) return '参考图操作失败，请稍后重试。'
    if (error.status === 413 || error.code === 'UPLOAD_TOO_LARGE') {
      return '参考图超过允许大小，已保留当前参考图。'
    }
    if (error.status === 409) {
      referenceConflict.value = true
      return '参考图已在别处更新，请加载最新项目后再操作。'
    }
    if (error.status === 404) return '项目不存在或已被删除，请刷新项目列表。'
    if (error.status === 503) return '本地文件存储暂时不可用，已保留当前参考图，请稍后重试。'
    if (error.code === 'REFERENCE_NOT_SUPPORTED') return '当前模型不支持参考图，已保留当前参考图。'
    if (error.code === 'UNSUPPORTED_REFERENCE_FORMAT') {
      return '该参考图格式不受当前模型支持，已保留当前参考图。'
    }
    if (error.code === 'INVALID_REFERENCE_IMAGE') {
      return '图片无法解码或已损坏，已保留当前参考图。'
    }
    return error.message || '参考图操作失败，已保留当前参考图，请稍后重试。'
  }

  function adoptProjectDetail(
    response: ProjectDetailResponse,
    options?: { followLatestResult?: boolean },
  ): void {
    currentProject.value = response.project
    projectMessages.value = response.messages
    projectGenerations.value = response.generations
    reconcileResultSelection(Boolean(options?.followLatestResult))
    resetBriefDraft(response.project)
    briefEditing.value = false
    briefSaveError.value = ''
    briefConfirmError.value = ''
    briefSaveNotice.value = ''
    briefConflict.value = false
  }

  function applyReferenceProject(project: ProjectView, notice: string): void {
    currentProject.value = project
    resetBriefDraft(project)
    briefEditing.value = false
    briefSaveError.value = ''
    briefConfirmError.value = ''
    briefSaveNotice.value = ''
    briefConflict.value = false
    referenceConflict.value = false
    referenceNotice.value = notice
    announce(notice)
  }

  async function verifyReferenceOutcome(
    projectId: string,
    kind: 'upload' | 'remove',
    previousId: string | null,
    previousRevision: number,
  ): Promise<boolean> {
    try {
      const response = await api.getProject(projectId)
      if (projectParam.value !== projectId) return true
      const nextId = response.project.reference_asset?.id ?? null
      const changedReference =
        kind === 'upload' &&
        (Boolean(nextId) && (nextId !== previousId || response.project.revision > previousRevision))
      const removedReference = kind === 'remove' && previousId !== null && nextId === null
      if (!changedReference && !removedReference) return false
      adoptProjectDetail(response)
      clearReferencePending()
      referenceConflict.value = false
      const notice =
        kind === 'upload'
          ? '已核实参考图上传成功。旧提示词与确认已失效，请重新整理并确认需求。'
          : '已核实参考图已移除。旧提示词与确认已失效，请重新整理并确认需求。'
      referenceNotice.value = notice
      announce(notice)
      return true
    } catch {
      return false
    }
  }

  async function uploadPendingReference(): Promise<void> {
    const pending = referencePending.value
    if (!pending) return
    const token = ++referenceToken
    referenceAction.value = 'upload'
    referenceError.value = ''
    referenceNotice.value = ''
    let project = currentProject.value
    if (!project) {
      try {
        project = await api.createProject()
      } catch (error) {
        if (isUnconfirmedWrite(error)) {
          creationUnknown.value = true
          referenceError.value =
            '项目创建结果未确认。请打开已有项目核实是否已创建；确认前不会再次创建项目。'
        } else {
          referenceError.value = errorMessage(error, '项目创建失败，请稍后重试。')
        }
        referencePending.value = { ...pending, status: 'failed' }
        return
      }
      currentProject.value = project
      projectParam.value = project.id
      updateProjectUrl(project.id, 'push')
    }
    if (!project) return
    const previousId = project.reference_asset?.id ?? null
    const previousRevision = project.revision
    try {
      const updated = await api.uploadReference(project.id, pending.file, project.revision)
      if (token !== referenceToken || projectParam.value !== project.id) return
      applyReferenceProject(
        updated,
        '参考图已上传。旧提示词与确认已失效，请重新整理并确认需求。',
      )
      clearReferencePending()
      creationUnknown.value = false
    } catch (error) {
      if (token !== referenceToken || projectParam.value !== project.id) return
      if (isUnconfirmedWrite(error)) {
        const resolved = await verifyReferenceOutcome(
          project.id,
          'upload',
          previousId,
          previousRevision,
        )
        if (resolved) return
        referenceError.value =
          '参考图上传结果未确认。请加载最新项目核实；当前仍保留原参考图。'
      } else {
        referenceError.value = referenceWriteErrorMessage(error)
      }
      referencePending.value = { ...pending, status: 'failed' }
    } finally {
      if (token === referenceToken) referenceAction.value = null
    }
  }

  async function chooseReferenceFiles(files: File[]): Promise<void> {
    if (referenceBusy.value) return
    if (!files.length) return
    referenceError.value = ''
    referenceNotice.value = ''
    if (files.length !== 1) {
      referenceError.value = '一次只能上传 1 张参考图，请重新选择。'
      return
    }
    if (referenceCapability.value === false) {
      referenceError.value = '当前模型明确不支持参考图。'
      return
    }
    if (!configStatus.value) {
      referenceError.value = '正在读取模型配置，请稍候。'
      return
    }
    const file = files[0]
    if (file.size === 0) {
      referenceError.value = '图片文件为空，请选择有效的 PNG 或 JPEG 文件。'
      return
    }
    if (file.size > referenceLimitBytes.value) {
      referenceError.value =
        '参考图超过允许大小（最多 ' +
        referenceLimitBytes.value.toLocaleString() +
        ' 字节），已保留当前参考图。'
      return
    }
    referenceChecking.value = true
    try {
      const info = await inspectImage(file)
      if (!info) {
        referenceError.value = '无法读取这张图片，请选择未损坏的 PNG 或 JPEG 文件。'
        return
      }
      if (!referenceMimeTypes.value.includes(info.mimeType)) {
        referenceError.value = '当前模型不支持该参考图格式；可选用 ' + referenceFormatLabel() + '。'
        return
      }
      clearReferencePending()
      referencePending.value = {
        file,
        name: file.name || '参考图',
        previewUrl: createPreviewUrl(file),
        mimeType: info.mimeType,
        byteSize: file.size,
        width: info.width,
        height: info.height,
        status: 'uploading',
      }
      referenceChecking.value = false
      await uploadPendingReference()
    } catch {
      referenceError.value = '无法读取这张图片，请选择未损坏的 PNG 或 JPEG 文件。'
    } finally {
      referenceChecking.value = false
    }
  }

  async function retryReferenceUpload(): Promise<void> {
    if (referenceBusy.value || !referencePending.value) return
    const reason = referenceUploadDisabledReason.value
    if (reason) {
      referenceError.value = reason
      return
    }
    referenceError.value = ''
    await uploadPendingReference()
  }

  async function removeReference(): Promise<void> {
    if (referenceBusy.value) return
    const project = currentProject.value
    if (!project?.reference_asset) return
    const reason = referenceRemoveDisabledReason.value
    if (reason) {
      referenceError.value = reason
      return
    }
    const token = ++referenceToken
    referenceAction.value = 'remove'
    referenceError.value = ''
    referenceNotice.value = ''
    const previousId = project.reference_asset.id
    const previousRevision = project.revision
    try {
      const updated = await api.removeReference(project.id, project.revision)
      if (token !== referenceToken || projectParam.value !== project.id) return
      if (updated.reference_asset) {
        referenceError.value = '参考图移除失败，服务仍返回参考图记录，请重试。'
        return
      }
      applyReferenceProject(
        updated,
        '参考图已移除。旧提示词与确认已失效，请重新整理并确认需求。',
      )
      clearReferencePending()
    } catch (error) {
      if (token !== referenceToken || projectParam.value !== project.id) return
      if (isUnconfirmedWrite(error)) {
        const resolved = await verifyReferenceOutcome(
          project.id,
          'remove',
          previousId,
          previousRevision,
        )
        if (resolved) return
        referenceError.value =
          '参考图移除结果未确认。请加载最新项目核实；当前仍保留原参考图。'
      } else {
        referenceError.value = referenceWriteErrorMessage(error)
      }
    } finally {
      if (token === referenceToken) referenceAction.value = null
    }
  }

  async function reloadLatestReference(): Promise<void> {
    if (referenceBusy.value) return
    const id = projectParam.value ?? currentProject.value?.id
    if (!id) return
    const token = ++referenceToken
    referenceAction.value = 'reload'
    referenceError.value = ''
    referenceNotice.value = ''
    try {
      const response = await api.getProject(id)
      if (token !== referenceToken || projectParam.value !== id) return
      adoptProjectDetail(response)
      clearReferencePending()
      referenceConflict.value = false
      referenceNotice.value = '已加载最新项目。'
      announce('已加载最新项目。')
    } catch (error) {
      referenceError.value = writeErrorMessage(error, '最新项目加载失败，请稍后重试。')
    } finally {
      if (token === referenceToken) referenceAction.value = null
    }
  }

  function applyMessageResponse(response: MessageCreateResponse): void {
    currentProject.value = response.project
    resetBriefDraft(response.project)
    briefEditing.value = false
    briefSaveError.value = ''
    briefConfirmError.value = ''
    briefSaveNotice.value = ''
    briefConflict.value = false
    projectMessages.value = [
      ...projectMessages.value,
      response.user_message,
      response.assistant_message,
    ]
    if (response.project.brief_status === 'organizing' || activeGeneration.value) {
      scheduleStatusQuery()
    }
  }

  async function send(): Promise<void> {
    if (sendBusy.value) return
    const reason = sendDisabledReason.value
    if (reason) {
      sendError.value = reason
      return
    }
    const content = draft.value
    const originalProject = currentProject.value
    const contextToken = projectContextToken
    sendBusy.value = true
    sendError.value = ''
    try {
      let project = originalProject
      if (!project) {
        try {
          project = await api.createProject()
        } catch (error) {
          if (isUnconfirmedWrite(error)) {
            creationUnknown.value = true
            sendError.value =
              '项目创建结果未确认。请打开已有项目核实是否已创建；确认前不会再次创建项目。'
          } else {
            sendError.value = errorMessage(error, '项目创建失败，请稍后重试。')
          }
          return
        }
        if (projectContextToken !== contextToken) return
        currentProject.value = project
        projectParam.value = project.id
        updateProjectUrl(project.id, 'push')
      }
      if (!project) return

      const response = await api.sendMessage(project.id, {
        content,
        expected_revision: project.revision,
      })
      if (projectContextToken !== contextToken || projectParam.value !== project.id) return
      applyMessageResponse(response)
      creationUnknown.value = false
      draft.value = ''
      sendError.value = ''
      announce('需求整理完成，已显示最新回复。')
    } catch (error) {
      if (projectContextToken !== contextToken) return
      sendError.value = writeErrorMessage(error, '消息发送失败，输入内容已保留，请稍后重试。')
    } finally {
      sendBusy.value = false
    }
  }

  async function retryOrganize(): Promise<void> {
    if (sendBusy.value || briefBusy.value || referenceBusy.value || generationSubmitBusy.value || generationRetrieveBusy.value || activeGeneration.value) return
    const project = currentProject.value
    if (!project) return
    if (briefDirty.value) {
      briefSaveError.value = '请先保存或取消当前修改，再重新整理需求。'
      return
    }
    if (briefConflict.value) {
      briefSaveError.value = '需求已在别处更新，请先加载最新需求，再重新整理。'
      return
    }
    if (briefLocalCopy.value) {
      briefSaveError.value = '正在查看未保存的本地副本，请先加载最新需求，再重新整理。'
      return
    }
    const reason = modelConfigReason()
    if (reason) {
      sendError.value = reason
      return
    }
    const contextToken = projectContextToken
    briefAction.value = 'organize'
    sendError.value = ''
    try {
      const response = await api.sendMessage(project.id, {
        content: '请按当前已保存的需求重新整理。',
        expected_revision: project.revision,
      })
      if (projectContextToken !== contextToken || projectParam.value !== project.id) return
      applyMessageResponse(response)
      announce('已重新请求整理当前需求。')
    } catch (error) {
      if (projectContextToken !== contextToken) return
      sendError.value = writeErrorMessage(error, '重新整理失败，已保存的卡片内容仍会保留。')
    } finally {
      briefAction.value = null
    }
  }

  function startBriefEdit(): void {
    const project = currentProject.value
    if (
      !project ||
      sendBusy.value ||
      briefBusy.value ||
      referenceBusy.value ||
      generationSubmitBusy.value ||
      generationRetrieveBusy.value ||
      activeGeneration.value ||
      project.brief_status === 'organizing'
    )
      return
    if (briefConflict.value) {
      briefSaveError.value = '需求已在别处更新，请先加载最新需求后再编辑。'
      return
    }
    if (briefLocalCopy.value) {
      briefSaveError.value = '正在查看未保存的本地副本，请先加载最新需求后再编辑。'
      return
    }
    briefDraft.value = briefFieldsFrom(project.brief)
    briefFieldErrors.value = {}
    briefEditing.value = true
    briefSaveError.value = ''
    briefConfirmError.value = ''
    briefSaveNotice.value = ''
  }

  function cancelBriefEdit(): void {
    const project = currentProject.value
    briefDraft.value = project ? briefFieldsFrom(project.brief) : null
    briefEditing.value = false
    briefFieldErrors.value = {}
    briefSaveError.value = ''
    briefConfirmError.value = ''
    briefSaveNotice.value = ''
  }

  function setBriefField(key: EditableBriefKey, value: string): void {
    if (!briefEditing.value || !briefDraft.value) return
    briefDraft.value = { ...briefDraft.value, [key]: value }
    if (briefFieldErrors.value[key]) {
      const next = { ...briefFieldErrors.value }
      delete next[key]
      briefFieldErrors.value = next
    }
    briefSaveError.value = ''
  }

  function validateBriefDraft(fields: BriefFields): Partial<Record<EditableBriefKey, string>> {
    const errors: Partial<Record<EditableBriefKey, string>> = {}
    if (!fields.product_description.trim()) {
      errors.product_description = '商品描述不能为空。'
    }
    for (const field of editableBriefFields) {
      const length = fields[field.key].length
      if (length > 8000) {
        errors[field.key] = `${field.label}不能超过 8,000 个字符（当前 ${length} 个）。`
      }
    }
    return errors
  }

  function buildBriefPatch(project: ProjectView, fields: BriefFields): PatchBriefRequest | null {
    const patch: PatchBriefRequest = { expected_revision: project.revision }
    let changed = false
    for (const field of editableBriefFields) {
      if (fields[field.key] !== project.brief[field.key]) {
        patch[field.key] = fields[field.key]
        changed = true
      }
    }
    return changed ? patch : null
  }

  async function saveBrief(): Promise<void> {
    if (sendBusy.value || briefBusy.value || referenceBusy.value) return
    const project = currentProject.value
    const fields = briefDraft.value
    if (!project || !fields || !briefEditing.value || !briefDirty.value) return
    const patch = buildBriefPatch(project, fields)
    if (!patch) return
    const errors = validateBriefDraft(fields)
    if (Object.keys(errors).length) {
      briefFieldErrors.value = errors
      briefSaveError.value = '请先修正标出的字段后再保存。'
      return
    }
    const contextToken = projectContextToken
    briefAction.value = 'save'
    briefSaveError.value = ''
    briefConfirmError.value = ''
    briefSaveNotice.value = ''
    try {
      const updated = await api.updateBrief(project.id, patch)
      if (projectContextToken !== contextToken || projectParam.value !== project.id) return
      currentProject.value = updated
      resetBriefDraft(updated)
      briefEditing.value = false
      const promptIsCurrent = Boolean(updated.brief.prompt.trim()) && updated.prompt_revision === updated.revision
      briefSaveNotice.value = promptIsCurrent
        ? '需求已保存。'
        : '需求已保存。旧提示词已过期，请重新整理需求后再确认。'
      announce('需求已保存。')
    } catch (error) {
      if (projectContextToken !== contextToken) return
      if (error instanceof ApiError && error.status === 409) {
        briefConflict.value = true
        briefSaveError.value = '需求已在别处更新，本地修改仍保留。请加载最新需求后再操作。'
      } else if (error instanceof ApiError && error.status === 413) {
        briefSaveError.value = '保存内容过大，请精简字段后再试；本地修改仍保留。'
      } else if (error instanceof ApiError && error.status === 422) {
        briefSaveError.value = '部分字段不符合要求，请检查后再试；本地修改仍保留。'
      } else {
        briefSaveError.value = writeErrorMessage(error, '需求保存失败，本地修改仍保留，请稍后重试。')
      }
    } finally {
      briefAction.value = null
    }
  }

  async function confirmBrief(): Promise<void> {
    if (sendBusy.value || briefBusy.value || referenceBusy.value) return
    const reason = confirmDisabledReason.value
    if (reason) {
      briefConfirmError.value = reason
      return
    }
    const project = currentProject.value
    if (!project) return
    const contextToken = projectContextToken
    briefAction.value = 'confirm'
    briefConfirmError.value = ''
    briefSaveError.value = ''
    briefSaveNotice.value = ''
    try {
      const updated = await api.confirmBrief(project.id, project.revision)
      if (projectContextToken !== contextToken || projectParam.value !== project.id) return
      currentProject.value = updated
      resetBriefDraft(updated)
      briefEditing.value = false
      briefSaveNotice.value = '需求已确认。'
      announce('需求已确认。')
    } catch (error) {
      if (projectContextToken !== contextToken) return
      if (error instanceof ApiError && error.status === 409) {
        briefConflict.value = true
        briefConfirmError.value = '需求已在别处更新或尚不能确认，请加载最新需求后再操作。'
      } else {
        briefConfirmError.value = writeErrorMessage(error, '需求确认失败，请稍后重试。')
      }
    } finally {
      briefAction.value = null
    }
  }

  async function reloadLatestBrief(): Promise<void> {
    if (sendBusy.value || briefBusy.value || referenceBusy.value) return
    const id = projectParam.value ?? currentProject.value?.id
    if (!id) return
    const contextToken = projectContextToken
    briefAction.value = 'reload'
    briefSaveError.value = ''
    briefConfirmError.value = ''
    try {
      const response = await api.getProject(id)
      if (projectContextToken !== contextToken || projectParam.value !== id) return
      currentProject.value = response.project
      projectMessages.value = response.messages
      projectGenerations.value = response.generations
      resetBriefDraft(response.project)
      briefEditing.value = false
      briefConflict.value = false
      briefSaveNotice.value = '已加载最新需求。'
      announce('已加载最新需求。')
    } catch (error) {
      if (projectContextToken !== contextToken) return
      briefSaveError.value = writeErrorMessage(error, '最新需求加载失败，请稍后重试。')
    } finally {
      briefAction.value = null
    }
  }

  function viewLocalBriefCopy(): void {
    if (!briefConflict.value || !briefDraft.value) return
    briefEditing.value = false
    briefLocalCopy.value = true
    briefSaveError.value = ''
    briefConfirmError.value = ''
    const notice = '正在查看未保存的本地副本（只读），可复制内容或加载最新需求。'
    briefSaveNotice.value = notice
    announce(notice)
  }

  function selectProject(id: string): void {
    if (id === projectParam.value && currentProject.value?.id === id) return
    if (hasUnsavedChanges.value) {
      openNavigationConfirm({ kind: 'project', projectId: id })
      return
    }
    performSelectProject(id)
  }

  function performSelectProject(id: string): void {
    creationUnknown.value = false
    sendError.value = ''
    draft.value = ''
    closeProjectDrawer()
    closeSideDrawer()
    projectParam.value = id
    updateProjectUrl(id, 'push')
    void loadProject(id)
  }

  function retryProject(): void {
    const id = projectParam.value
    if (id) void loadProject(id)
  }

  function syncProjectFromUrl(): void {
    const id = currentProjectParam()
    if (id === projectParam.value) return
    if (hasUnsavedChanges.value) {
      updateProjectUrl(projectParam.value, 'replace')
      openNavigationConfirm({ kind: 'history', projectId: id })
      return
    }
    projectParam.value = id
    if (id === null) {
      clearProjectDetail()
      stage.value = 'describe'
      return
    }
    void loadProject(id)
  }

  function performHistoryNavigation(id: string | null): void {
    creationUnknown.value = false
    sendError.value = ''
    draft.value = ''
    if (id === null) {
      clearProjectDetail()
      projectParam.value = null
      stage.value = 'describe'
      sideDrawerOpen.value = false
      projectDrawerOpen.value = false
      updateProjectUrl(null, 'push')
      announce('已切换到空白工作台。')
      return
    }
    projectParam.value = id
    updateProjectUrl(id, 'push')
    void loadProject(id)
  }

  function setStage(id: StageId): void {
    const target = stages.value.find((item) => item.id === id)
    if (!target || target.locked) {
      if (target?.locked) announce(target.reason)
      return
    }
    stage.value = id
  }

  async function refreshConfig(): Promise<void> {
    if (configLoading.value) return
    configController?.abort()
    const controller = new AbortController()
    configController = controller
    configLoading.value = true
    try {
      const next = await api.getConfigStatus(controller.signal)
      if (controller.signal.aborted) return
      configStatus.value = next
      configError.value = ''
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) return
      configError.value =
        error instanceof ApiError
          ? error.message
          : '配置状态读取失败，请稍后重试。'
      announce('配置状态读取失败。')
    } finally {
      if (!controller.signal.aborted) configLoading.value = false
      if (configController === controller) configController = null
    }
  }

  watch(
    breakpoint,
    (next) => {
      sidebarExpanded.value = next === 'wide'
      sideDrawerOpen.value = false
    },
    { flush: 'sync' },
  )

  function handleBeforeUnload(event: BeforeUnloadEvent): void {
    if (!hasUnsavedChanges.value) return
    event.preventDefault()
    event.returnValue = ''
  }

  function handleVisibilityChange(): void {
    if (pageIsHidden()) {
      pauseStatusQueriesForHiddenPage()
      return
    }
    if (generationQueryError.value) return
    if (organizing.value || activeGeneration.value) scheduleStatusQuery(0)
  }

  watch(
    hasUnsavedChanges,
    (dirty) => {
      if (typeof window === 'undefined') return
      if (dirty) window.addEventListener('beforeunload', handleBeforeUnload)
      else window.removeEventListener('beforeunload', handleBeforeUnload)
    },
    { flush: 'sync' },
  )

  onMounted(() => {
    void refreshConfig()
    if (hasUnsavedChanges.value) window.addEventListener('beforeunload', handleBeforeUnload)
    if (projectParam.value !== null) {
      void loadProject(projectParam.value)
    }
    window.addEventListener('popstate', syncProjectFromUrl)
    document.addEventListener('visibilitychange', handleVisibilityChange)
  })

  onBeforeUnmount(() => {
    configController?.abort()
    configController = null
    listController?.abort()
    listController = null
    moreController?.abort()
    moreController = null
    detailController?.abort()
    detailController = null
    resetGenerationState()
    clearReferenceState()
    window.removeEventListener('popstate', syncProjectFromUrl)
    window.removeEventListener('beforeunload', handleBeforeUnload)
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  })

  return {
    draft,
    draftDirty,
    hasUnsavedChanges,
    navigationConfirmOpen,
    navigationIntentLabel,
    confirmNavigation,
    cancelNavigation,
    projectParam,
    stage,
    stages,
    configStatus,
    configError,
    configLoading,
    sidebarExpanded,
    sideDrawerOpen,
    modelDrawerOpen,
    projectDrawerOpen,
    announcement,
    breakpoint,
    isMobile,
    projectList,
    projectListLoading,
    projectListError,
    projectListMoreError,
    nextCursor,
    projectListLoadingMore,
    currentProject,
    projectMessages,
    projectGenerations,
    projectLoading,
    projectError,
    projectErrorKind,
    sendBusy,
    sendError,
    creationUnknown,
    organizing,
    sendDisabledReason,
    briefDraft,
    briefEditing,
    briefFieldErrors,
    briefDirty,
    briefAction,
    briefBusy,
    briefSaveError,
    briefConfirmError,
    briefSaveNotice,
    briefConflict,
    briefLocalCopy,
    referencePending,
    referenceError,
    referenceNotice,
    referenceAction,
    referenceBusy,
    referenceConflict,
    referenceCapability,
    referenceLimitBytes,
    referenceMimeTypes,
    referenceGenerationBlockedReason,
    referenceUploadDisabledReason,
    referenceRemoveDisabledReason,
    generationSubmitBusy,
    generationSubmitError,
    generationSubmitNotice,
    generationUncertain,
    generationPendingRestored,
    generationRecoveryAvailable,
    generationCredentialWarning,
    generationRecoveryAction,
    generationRiskDialogOpen,
    generationRetrieveBusy,
    generationRetrieveError,
    generationRetrieveNotice,
    resultRetrievalGeneration,
    resultRetrievalAvailable,
    resultRetrievalUnavailableReason,
    generationRetrieveDisabledReason,
    retrieveGenerationResult,
    confirmGenerationRisk,
    cancelGenerationRisk,
    generationQuerying,
    generationQueryError,
    statusQueryInterruptionTitle,
    generationDisabledReason,
    latestGenerationView,
    latestSucceededGeneration,
    activeGeneration,
    latestGenerationError,
    succeededGenerations,
    selectedResultGenerationId,
    selectedResultGeneration,
    selectResultGeneration,
    submitGeneration,
    verifyGenerationSubmission,
    resumeGenerationSubmission,
    retryGenerationQuery,
    chooseReferenceFiles,
    retryReferenceUpload,
    removeReference,
    reloadLatestReference,
    promptStale,
    saveDisabledReason,
    confirmDisabledReason,
    startBriefEdit,
    cancelBriefEdit,
    setBriefField,
    saveBrief,
    confirmBrief,
    reloadLatestBrief,
    viewLocalBriefCopy,
    send,
    retryOrganize,
    toggleSidebar,
    openSideDrawer,
    closeSideDrawer,
    openModelDrawer,
    closeModelDrawer,
    openProjectDrawer,
    closeProjectDrawer,
    loadProjects,
    loadMoreProjects,
    retryLoadMore,
    selectProject,
    retryProject,
    createNewProject,
    fillExample,
    refreshConfig,
    setStage,
    announce,
  }
}

export function provideWorkspace(): WorkspaceStore {
  const workspace = createWorkspace()
  provide(WORKSPACE_KEY, workspace)
  return workspace
}

export function useWorkspace(): WorkspaceStore {
  const workspace = inject(WORKSPACE_KEY)
  if (!workspace) throw new Error('useWorkspace 必须在使用工作台内使用')
  return workspace
}
