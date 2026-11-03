import {
  computed,
  reactive,
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
  ConfigStatus,
  GenerationView,
  MessageCreateResponse,
  MessageView,
  ProjectDetailResponse,
  ProjectSummary,
  ProjectView,
} from '@/api/types'
import { isUuid } from '@/utils/format'
import { useRoute, useRouter } from 'vue-router'
import { sessionRevision } from '@/auth/session'
import { clearPrivateBrowserState } from '@/auth/privateData'
import { useViewport } from './useViewport'

const CAPABILITY_NAMES: Record<string, string> = {
  TEXT: '文字处理',
  VISION: '视觉理解',
  IMAGE_GEN: '图片生成',
}

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

type NavigationIntent =
  | { kind: 'new' }
  | { kind: 'project'; projectId: string }
  | { kind: 'history'; projectId: string | null }

export interface WorkspaceStore {
  draft: Ref<string>
  draftDirty: ComputedRef<boolean>
  attachmentPending: ComputedRef<boolean>
  hasUnsavedChanges: ComputedRef<boolean>
  navigationConfirmOpen: Ref<boolean>
  navigationIntentLabel: ComputedRef<string>
  confirmNavigation: () => void
  cancelNavigation: () => void
  projectParam: Ref<string | null>
  configStatus: Ref<ConfigStatus | null>
  configError: Ref<string>
  configLoading: Ref<boolean>
  healthStatus: Ref<'ok' | 'error' | null>
  sidebarExpanded: Ref<boolean>
  sideDrawerOpen: Ref<boolean>
  modelDrawerOpen: Ref<boolean>
  projectDrawerOpen: Ref<boolean>
  announcement: Ref<string>
  breakpoint: ComputedRef<'narrow' | 'medium' | 'wide'>
  isMobile: ComputedRef<boolean>
  isCompact: ComputedRef<boolean>
  projectList: Ref<ProjectSummary[]>
  projectListLoading: Ref<boolean>
  projectListError: Ref<string>
  projectListMoreError: Ref<string>
  nextCursor: Ref<string | null>
  projectListLoadingMore: Ref<boolean>
  projectQuery: Ref<string>
  projectSearchBusy: Ref<boolean>
  projectSearchError: Ref<string>
  projectRenameBusy: Ref<boolean>
  projectRenameError: Ref<string>
  setProjectQuery: (query: string) => void
  renameProject: (id: string, title: string) => Promise<boolean>
  currentProject: Ref<ProjectView | null>
  projectMessages: Ref<MessageView[]>
  projectGenerations: Ref<GenerationView[]>
  projectLoading: Ref<boolean>
  projectError: Ref<string>
  projectErrorKind: Ref<ProjectErrorKind | null>
  sendBusy: Ref<boolean>
  sendError: Ref<string>
  messageConflict: Ref<boolean>
  messageConflictText: Ref<string>
  messageLocalCopy: Ref<boolean>
  showMessageLocalCopy: () => void
  loadLatestAfterMessageConflict: () => Promise<void>
  refreshProjectDetail: () => Promise<boolean>
  adoptCreatedProject: (project: ProjectView) => void
  creationUnknown: Ref<boolean>
  organizing: ComputedRef<boolean>
  sendDisabledReason: ComputedRef<string>
  referencePending: Ref<PendingReference | null>
  referenceError: Ref<string>
  referenceNotice: Ref<string>
  referenceAction: Ref<ReferenceAction | null>
  referenceBusy: ComputedRef<boolean>
  referenceConflict: Ref<boolean>
  referenceCapability: ComputedRef<boolean | null>
  referenceLimitBytes: ComputedRef<number>
  referenceMimeTypes: ComputedRef<string[]>
  referenceUploadDisabledReason: ComputedRef<string>
  referenceRemoveDisabledReason: ComputedRef<string>
  latestGenerationView: ComputedRef<GenerationView | null>
  activeGeneration: ComputedRef<GenerationView | null>
  latestGenerationError: ComputedRef<string>
  chooseReferenceFiles: (files: File[]) => Promise<void>
  retryReferenceUpload: () => Promise<void>
  removeReference: () => Promise<void>
  reloadLatestReference: () => Promise<void>
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
  announce: (message: string) => void
}

const WORKSPACE_KEY: InjectionKey<WorkspaceStore> = Symbol('workspace')

function currentProjectParam(): string | null {
  if (typeof window === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  if (!params.has('project')) return null
  return (params.get('project') ?? '').trim()
}

function routeProjectId(routeParams: unknown, query: unknown): string | null {
  const params = routeParams && typeof routeParams === 'object' ? routeParams as Record<string, unknown> : null
  const queryObject = query && typeof query === 'object' ? query as Record<string, unknown> : null
  let present = false
  let raw: unknown = undefined
  if (params && Object.prototype.hasOwnProperty.call(params, 'projectId')) {
    present = true
    raw = params.projectId
  } else if (queryObject && Object.prototype.hasOwnProperty.call(queryObject, 'project')) {
    present = true
    raw = queryObject.project
  }
  if (!present) return null
  return typeof raw === 'string' ? raw.trim() : ''
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
  const { breakpoint, isMobile, isCompact } = useViewport()
  const router = useRouter()
  const route = useRoute()
  let workspaceSessionRevision = sessionRevision.value

  const drafts = reactive(new Map<string, string>())
  const projectParam = ref<string | null>(routeProjectId(route.params, route.query))
  const draftKey = computed(() => projectParam.value ?? '__new__')
  const draft = computed<string>({
    get: () => drafts.get(draftKey.value) ?? '',
    set: (value: string) => {
      if (value) drafts.set(draftKey.value, value)
      else drafts.delete(draftKey.value)
    },
  })
  const projectQuery = ref('')
  const projectSearchBusy = ref(false)
  const projectSearchError = ref('')
  const projectRenameBusy = ref(false)
  const projectRenameError = ref('')
  const sidebarExpanded = ref(breakpoint.value !== 'narrow')
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
  const configUnavailableReason = computed(() =>
    configError.value
      ? '模型配置读取失败，请确认后端服务已启动后重试。'
      : '正在读取模型配置，请稍候。',
  )
  const healthStatus = ref<'ok' | 'error' | null>(null)
  let configController: AbortController | null = null
  let lastAnnouncement = ''

  const projectList = ref<ProjectSummary[]>([])
  const projectListLoading = ref(false)
  const projectListError = ref('')
  const projectListMoreError = ref('')
  const nextCursor = ref<string | null>(null)
  const projectListLoadingMore = ref(false)
  let listController: AbortController | null = null
  let listLoadingQuery: string | null = null
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
  const messageConflict = ref(false)
  const messageConflictText = ref('')
  const messageLocalCopy = ref(false)
  const creationUnknown = ref(false)
  const organizing = computed(() => currentProject.value?.brief_status === 'organizing')

  const latestGenerationView = computed<GenerationView | null>(() => {
    let latest: GenerationView | null = null
    for (const item of projectGenerations.value) {
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

  const referencePending = shallowRef<PendingReference | null>(null)
  const referenceError = ref('')
  const referenceNotice = ref('')
  const referenceAction = ref<ReferenceAction | null>(null)
  const referenceChecking = ref(false)
  const referenceConflict = ref(false)
  let referenceToken = 0
  const imageCapabilities = computed(
    () => configStatus.value?.image_generation?.capabilities ?? configStatus.value?.visual.capabilities,
  )
  const referenceCapability = computed<boolean | null>(
    () => imageCapabilities.value?.reference_image ?? null,
  )
  const referenceBusy = computed(
    () => referenceChecking.value || referenceAction.value !== null,
  )
  const referenceLimitBytes = computed(() => {
    const caps = imageCapabilities.value
    let limit = typeof caps?.local_reference_max_bytes === 'number'
      ? caps.local_reference_max_bytes
      : 10_000_000
    for (const value of [caps?.provider_max_reference_bytes, caps?.effective_reference_max_bytes]) {
      if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
        limit = Math.min(limit, value)
      }
    }
    return Math.max(0, limit)
  })
  const referenceMimeTypes = computed<string[]>(() => {
    const configured =
      imageCapabilities.value?.effective_reference_mime_types ??
      imageCapabilities.value?.reference_mime_types
    if (!configured?.length) return ['image/png', 'image/jpeg']
    const normalized = configured
      .map((value) => value.trim().toLowerCase())
      .map((value) => (value === 'image/jpg' ? 'image/jpeg' : value))
      .filter((value) => value === 'image/png' || value === 'image/jpeg')
    return [...new Set(normalized)]
  })
  const referenceUploadDisabledReason = computed(() => {
    if (activeGeneration.value) return '生成任务进行中，完成前不能修改参考图。'
    if (referenceBusy.value) return '正在处理参考图，请稍候。'
    if (creationUnknown.value) return '项目创建结果尚未确认，请先从已有项目核实。'
    if (referenceConflict.value) return '参考图已在别处更新，请先加载最新项目。'
    if (!configStatus.value) return configUnavailableReason.value
    if (referenceCapability.value === false) return '当前模型明确不支持参考图。'
    if (!referenceMimeTypes.value.length) return '当前模型未声明支持 PNG 或 JPEG 参考图。'
    return ''
  })
  const referenceRemoveDisabledReason = computed(() => {
    if (activeGeneration.value) return '生成任务进行中，完成前不能修改参考图。'
    if (referenceBusy.value) return '正在处理参考图，请稍候。'
    if (!currentProject.value) return '尚未选择项目。'
    if (!currentProject.value.reference_asset) return '当前没有参考图。'
    if (referenceConflict.value) return '参考图已在别处更新，请先加载最新项目。'
    return ''
  })
  const draftDirty = computed(() => draft.value.trim().length > 0)
  const attachmentPending = computed(() => referencePending.value !== null)
  const hasUnsavedChanges = computed(() => draftDirty.value || attachmentPending.value)
  const sendDisabledReason = computed(() => {
    if (sendBusy.value) return '正在发送消息，请稍候。'
    if (activeGeneration.value) return '生成任务进行中，完成前不能发送消息。'
    if (referenceBusy.value) return '正在处理参考图，完成前不能发送消息。'
    if (organizing.value) return '正在整理需求，完成前不能发送新消息。'
    if (messageConflict.value) return '消息发送遇到版本冲突，请先加载最新需求或查看本地副本。'
    if (creationUnknown.value) return '项目创建结果尚未确认，请先从已有项目核实。'
    const llm = configStatus.value?.llm
    if (!llm) return configUnavailableReason.value
    if (!llm.configured) return 'LLM 尚未配置，当前无法整理需求；输入内容会保留。'
    if (!llm.ready) return 'LLM 当前不可用，当前无法整理需求；输入内容会保留。'
    if (!draft.value.trim()) return '请输入商品与图片需求。'
    return ''
  })

  function announce(message: string): void {
    if (!message || message === lastAnnouncement) return
    lastAnnouncement = message
    announcement.value = ''
    void nextTick(() => {
      announcement.value = message
    })
  }

  function routeQueryWithoutProject(): Record<string, string | string[]> {
    const query: Record<string, string | string[]> = {}
    for (const [key, value] of Object.entries(route.query)) {
      if (key === 'project' || value === undefined || value === null) continue
      query[key] = Array.isArray(value) ? value.map((item) => String(item)) : String(value)
    }
    return query
  }

  function updateProjectUrl(id: string | null, mode: 'push' | 'replace'): Promise<unknown> {
    if (id) projectParam.value = id
    if (id) {
      return router[mode]({
        name: 'project-chat',
        params: { projectId: id },
        query: routeQueryWithoutProject(),
        hash: route.hash,
      }).catch(() => undefined)
    }
    return router[mode]({ name: 'describe', query: routeQueryWithoutProject(), hash: route.hash }).catch(() => undefined)
  }

  function adoptDraftForProject(projectId: string): void {
    const pending = drafts.get('__new__')
    if (pending !== undefined) drafts.set(projectId, pending)
    drafts.delete('__new__')
  }

  function adoptCreatedProject(project: ProjectView): void {
    detailController?.abort()
    detailController = null
    detailToken += 1
    projectContextToken += 1
    creationUnknown.value = false
    sendError.value = ''
    projectLoading.value = false
    projectError.value = ''
    projectErrorKind.value = null
    currentProject.value = project
    projectMessages.value = []
    projectGenerations.value = []
    projectParam.value = project.id
    adoptDraftForProject(project.id)
    void updateProjectUrl(project.id, 'push')
  }

  function openNavigationConfirm(intent: NavigationIntent): void {
    closeProjectDrawer()
    closeSideDrawer()
    navigationIntent.value = intent
    navigationConfirmOpen.value = true
    announce('当前有未保存的修改，请确认是否放弃。')
  }

  function discardCurrentEditingState(): void {
    drafts.delete(draftKey.value)
    clearReferenceState()
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
    discardCurrentEditingState()
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
    resetMessageConflict()
    detailController?.abort()
    detailController = null
    detailToken += 1
    projectContextToken += 1
    currentProject.value = null
    projectMessages.value = []
    projectGenerations.value = []
    clearReferenceState()
    projectLoading.value = false
    projectError.value = ''
    projectErrorKind.value = null
  }

  function clearPrivateWorkspace(): void {
    navigationConfirmOpen.value = false
    navigationIntent.value = null
    sideDrawerOpen.value = false
    projectDrawerOpen.value = false
    modelDrawerOpen.value = false
    creationUnknown.value = false
    sendBusy.value = false
    sendError.value = ''
    listController?.abort()
    listController = null
    moreController?.abort()
    moreController = null
    clearProjectDetail()
    projectList.value = []
    projectListLoading.value = false
    projectListError.value = ''
    projectListMoreError.value = ''
    nextCursor.value = null
    projectListLoadingMore.value = false
    drafts.clear()
    projectQuery.value = ''
    projectSearchBusy.value = false
    projectSearchError.value = ''
    projectRenameBusy.value = false
    projectRenameError.value = ''
    const hadProject =
      projectParam.value !== null ||
      typeof route.query.project === 'string' ||
      typeof route.params.projectId === 'string'
    projectParam.value = null
    clearPrivateBrowserState()
    if (route.name !== 'describe' || hadProject) {
      void router.replace({ name: 'describe' })
    }
  }

  // flush: sync 保证在旧请求的回调恢复到微任务前先递增全部请求序号。
  watch(
    sessionRevision,
    () => {
      workspaceSessionRevision = sessionRevision.value
      clearPrivateWorkspace()
    },
    { flush: 'sync' },
  )

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
    clearProjectDetail()
    projectParam.value = null
    drafts.delete('__new__')
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
    const sessionAtStart = sessionRevision.value
    const queryAtStart = projectQuery.value.trim()
    if (projectListLoading.value && listLoadingQuery === queryAtStart) return
    listController?.abort()
    moreController?.abort()
    const controller = new AbortController()
    listController = controller
    listLoadingQuery = queryAtStart
    projectListLoading.value = true
    projectSearchBusy.value = Boolean(queryAtStart)
    projectListLoadingMore.value = false
    projectListError.value = ''
    projectListMoreError.value = ''
    try {
      const response = await api.listProjects(
        { limit: 20, q: queryAtStart || undefined },
        controller.signal,
      )
      if (
        sessionAtStart !== sessionRevision.value ||
        queryAtStart !== projectQuery.value.trim() ||
        controller.signal.aborted
      ) return
      projectList.value = dedupeProjects(response.items)
      nextCursor.value = response.next_cursor
      projectSearchError.value = ''
    } catch (error) {
      if (sessionAtStart !== sessionRevision.value || isAbortError(error) || controller.signal.aborted) return
      if (queryAtStart !== projectQuery.value.trim()) return
      projectListError.value =
        error instanceof ApiError ? error.message : '项目列表读取失败，请稍后重试。'
    } finally {
      if (listController === controller) {
        listLoadingQuery = null
        listController = null
        projectListLoading.value = false
        projectSearchBusy.value = false
      }
    }
  }

  async function appendPage(cursor: string): Promise<void> {
    if (projectListLoadingMore.value || !cursor) return
    const sessionAtStart = sessionRevision.value
    const queryAtStart = projectQuery.value.trim()
    moreController?.abort()
    const controller = new AbortController()
    moreController = controller
    projectListLoadingMore.value = true
    projectListMoreError.value = ''
    try {
      const response = await api.listProjects(
        { limit: 20, cursor, q: queryAtStart || undefined },
        controller.signal,
      )
      if (
        sessionAtStart !== sessionRevision.value ||
        queryAtStart !== projectQuery.value.trim() ||
        controller.signal.aborted
      ) return
      projectList.value = dedupeProjects([...projectList.value, ...response.items])
      nextCursor.value = response.next_cursor
    } catch (error) {
      if (sessionAtStart !== sessionRevision.value || isAbortError(error) || controller.signal.aborted) return
      if (queryAtStart !== projectQuery.value.trim()) return
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

  function setProjectQuery(query: string): void {
    const normalized = query
    if (normalized.trim() === projectQuery.value.trim()) return
    projectQuery.value = normalized
    projectList.value = []
    nextCursor.value = null
    projectListError.value = ''
    projectListMoreError.value = ''
    projectSearchError.value = ''
    void loadProjects()
  }

  async function renameProject(id: string, title: string): Promise<boolean> {
    const normalized = title.trim()
    if (!normalized || normalized.length > 120) {
      projectRenameError.value = '项目标题长度须为 1-120 个字符。'
      return false
    }
    if (projectRenameBusy.value) return false
    const sessionAtStart = sessionRevision.value
    const queryAtStart = projectQuery.value.trim()
    projectRenameBusy.value = true
    projectRenameError.value = ''
    try {
      const updated = await api.renameProject(id, normalized)
      // 账号或登录生命周期变化后，旧账号的晚到回执不得再写入当前工作台。
      if (sessionAtStart !== sessionRevision.value) return false
      if (currentProject.value?.id === id) currentProject.value = updated
      if (queryAtStart === projectQuery.value.trim()) {
        const index = projectList.value.findIndex((item) => item.id === id)
        if (index >= 0) {
          projectList.value[index] = {
            ...projectList.value[index],
            title: updated.title,
            updated_at: updated.updated_at,
          }
        }
      }
      announce('项目标题已更新。')
      return true
    } catch (error) {
      if (sessionAtStart !== sessionRevision.value) return false
      projectRenameError.value =
        error instanceof ApiError ? error.message : '项目标题保存失败，请稍后重试。'
      return false
    } finally {
      if (sessionAtStart === sessionRevision.value) projectRenameBusy.value = false
    }
  }

  async function loadProject(id: string): Promise<void> {
    if (!isUuid(id)) {
      clearProjectDetail()
      projectErrorKind.value = 'invalid'
      projectError.value = '项目链接无效，请检查地址或从项目列表重新选择。'
      return
    }
    const sessionAtStart = sessionRevision.value
    detailController?.abort()
    const controller = new AbortController()
    detailController = controller
    const token = ++detailToken
    projectContextToken += 1
    resetMessageConflict()
    projectLoading.value = true
    projectError.value = ''
    projectErrorKind.value = null
    currentProject.value = null
    projectMessages.value = []
    projectGenerations.value = []
    clearReferenceState()
    try {
      const response = await api.getProject(id, controller.signal)
      if (
        sessionAtStart !== sessionRevision.value ||
        controller.signal.aborted ||
        token !== detailToken
      ) return
      currentProject.value = response.project
      projectMessages.value = response.messages
      projectGenerations.value = response.generations
      creationUnknown.value = false
    } catch (error) {
      if (
        sessionAtStart !== sessionRevision.value ||
        isAbortError(error) ||
        controller.signal.aborted ||
        token !== detailToken
      ) return
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

  function modelConfigReason(): string {
    const llm = configStatus.value?.llm
    if (!llm) return configUnavailableReason.value
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

  function resetMessageConflict(): void {
    messageConflict.value = false
    messageConflictText.value = ''
    messageLocalCopy.value = false
  }

  function isUnconfirmedWrite(error: unknown): boolean {
    return (
      error instanceof ApiError &&
      (error.status === 0 || error.status >= 500 || error.code === 'INVALID_RESPONSE')
    )
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

  let pngCrcTable: Uint32Array | null = null

  function pngCrc32(bytes: Uint8Array, start: number, end: number): number {
    if (!pngCrcTable) {
      const table = new Uint32Array(256)
      for (let n = 0; n < 256; n += 1) {
        let value = n
        for (let bit = 0; bit < 8; bit += 1) {
          value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1
        }
        table[n] = value >>> 0
      }
      pngCrcTable = table
    }
    let crc = 0xffffffff
    for (let index = start; index < end; index += 1) {
      crc = pngCrcTable[(crc ^ bytes[index]) & 0xff] ^ (crc >>> 8)
    }
    return (crc ^ 0xffffffff) >>> 0
  }

  function parsePng(bytes: Uint8Array): { width: number; height: number } | null {
    if (bytes.length < 8) return null
    const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
    if (!signature.every((value, index) => bytes[index] === value)) return null
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
    let position = 8
    let width = 0
    let height = 0
    let sawHeader = false
    let sawData = false
    let sawEnd = false
    while (position + 12 <= bytes.length) {
      const length = view.getUint32(position)
      const dataStart = position + 8
      const dataEnd = dataStart + length
      const chunkEnd = dataEnd + 4
      if (dataEnd < dataStart || chunkEnd > bytes.length) return null
      const type = String.fromCharCode(
        bytes[position + 4],
        bytes[position + 5],
        bytes[position + 6],
        bytes[position + 7],
      )
      if (!sawHeader) {
        if (type !== 'IHDR' || length !== 13) return null
        width = view.getUint32(dataStart)
        height = view.getUint32(dataStart + 4)
        const bitDepth = bytes[dataStart + 8]
        const colorType = bytes[dataStart + 9]
        const validDepth = [1, 2, 4, 8, 16].includes(bitDepth)
        const validColor = [0, 2, 3, 4, 6].includes(colorType)
        if (!width || !height || !validDepth || !validColor) return null
        if (bytes[dataStart + 10] !== 0 || bytes[dataStart + 11] !== 0) return null
        if (bytes[dataStart + 12] !== 0 && bytes[dataStart + 12] !== 1) return null
        sawHeader = true
      } else if (type === 'IHDR') {
        return null
      }
      if (type === 'IDAT') sawData = true
      const expectedCrc = view.getUint32(dataEnd)
      if (pngCrc32(bytes, position + 4, dataEnd) !== expectedCrc) return null
      position = chunkEnd
      if (type === 'IEND') {
        if (length !== 0) return null
        sawEnd = true
        break
      }
    }
    return sawHeader && sawData && sawEnd ? { width, height } : null
  }

  function parseJpeg(bytes: Uint8Array): { width: number; height: number } | null {
    if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return null
    let position = 2
    let width = 0
    let height = 0
    let sawFrame = false
    let sawScan = false
    while (position + 1 < bytes.length) {
      if (bytes[position] !== 0xff) {
        position += 1
        continue
      }
      let marker = bytes[position + 1]
      while (marker === 0xff && position + 2 < bytes.length) {
        position += 1
        marker = bytes[position + 1]
      }
      if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
        position += 2
        continue
      }
      if (marker === 0xd9) {
        return sawFrame && sawScan && width > 0 && height > 0 ? { width, height } : null
      }
      if (position + 3 >= bytes.length) return null
      const segmentLength = (bytes[position + 2] << 8) | bytes[position + 3]
      if (segmentLength < 2 || position + 2 + segmentLength > bytes.length) return null
      const isStartOfFrame =
        marker >= 0xc0 &&
        marker <= 0xcf &&
        marker !== 0xc4 &&
        marker !== 0xc8 &&
        marker !== 0xcc
      if (isStartOfFrame) {
        if (segmentLength < 7 || position + 8 >= bytes.length) return null
        height = (bytes[position + 5] << 8) | bytes[position + 6]
        width = (bytes[position + 7] << 8) | bytes[position + 8]
        if (!width || !height) return null
        sawFrame = true
      }
      position += 2 + segmentLength
      if (marker === 0xda) {
        sawScan = true
        while (position + 1 < bytes.length) {
          if (bytes[position] !== 0xff) {
            position += 1
            continue
          }
          const next = bytes[position + 1]
          if (next === 0x00 || (next >= 0xd0 && next <= 0xd7)) {
            position += 2
            continue
          }
          if (next === 0xd9) {
            return sawFrame && width > 0 && height > 0 ? { width, height } : null
          }
          break
        }
      }
    }
    return null
  }

  async function inspectImage(file: File): Promise<ImageInfo | null> {
    let bytes: Uint8Array
    try {
      bytes = new Uint8Array(await readBlob(file))
    } catch {
      return null
    }
    const png = parsePng(bytes)
    const parsed = png ? { mimeType: 'image/png' as const, ...png } : null
    const jpeg = parsed ? null : parseJpeg(bytes)
    const info = parsed ?? (jpeg ? { mimeType: 'image/jpeg' as const, ...jpeg } : null)
    if (!info) return null
    if (typeof createImageBitmap === 'function') {
      let bitmap: ImageBitmap | null = null
      try {
        bitmap = await createImageBitmap(file)
        if (!bitmap.width || !bitmap.height) return null
      } catch {
        return null
      } finally {
        bitmap?.close()
      }
    }
    return info
  }

  function referenceFormatLabel(): string {
    return referenceMimeTypes.value
      .map((mime) => (mime === 'image/png' ? 'PNG' : mime === 'image/jpeg' ? 'JPEG' : mime))
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

  function adoptProjectDetail(response: ProjectDetailResponse): void {
    currentProject.value = response.project
    projectMessages.value = response.messages
    projectGenerations.value = response.generations
  }

  function applyReferenceProject(project: ProjectView, notice: string): void {
    currentProject.value = project
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
      const response = await api.getProject(projectId, undefined, true)
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
      adoptDraftForProject(project.id)
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
    resetMessageConflict()
    currentProject.value = response.project
    projectMessages.value = [
      ...projectMessages.value,
      response.user_message,
      response.assistant_message,
    ]
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
        adoptDraftForProject(project.id)
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
      if (error instanceof ApiError && error.status === 409) {
        messageConflict.value = true
        messageConflictText.value = content
        messageLocalCopy.value = false
        sendError.value = ''
        announce('消息发送遇到版本冲突，请加载最新需求或查看本地副本。')
      } else {
        sendError.value = writeErrorMessage(error, '消息发送失败，输入内容已保留，请稍后重试。')
      }
    } finally {
      sendBusy.value = false
    }
  }

  async function retryOrganize(): Promise<void> {
    if (sendBusy.value || referenceBusy.value || activeGeneration.value) return
    const project = currentProject.value
    if (!project) return
    const reason = modelConfigReason()
    if (reason) {
      sendError.value = reason
      return
    }
    const contextToken = projectContextToken
    sendError.value = ''
    try {
      const response = await api.sendMessage(project.id, {
        content: '请按当前已保存的需求重新整理。',
        expected_revision: project.revision,
      })
      if (projectContextToken !== contextToken || projectParam.value !== project.id) return
      applyMessageResponse(response)
      announce('已重新整理当前需求。')
    } catch (error) {
      if (projectContextToken !== contextToken) return
      if (error instanceof ApiError && error.status === 409) {
        messageConflict.value = true
        messageConflictText.value = '请按当前已保存的需求重新整理。'
        messageLocalCopy.value = false
        sendError.value = ''
        announce('重新整理遇到版本冲突，请加载最新需求或查看本地副本。')
      } else {
        sendError.value = writeErrorMessage(error, '重新整理失败，已保存的卡片内容仍会保留。')
      }
    }
  }

  function showMessageLocalCopy(): void {
    if (!messageConflict.value) return
    messageLocalCopy.value = true
    void nextTick(() => {
      document.getElementById('message-local-copy')?.focus()
    })
    announce('正在查看未发送文字的只读本地副本。')
  }

  async function refreshProjectDetail(): Promise<boolean> {
    const id = projectParam.value ?? currentProject.value?.id
    if (!id) return false
    const contextToken = projectContextToken
    const sessionAtStart = sessionRevision.value
    try {
      const response = await api.getProject(id)
      if (
        sessionAtStart !== sessionRevision.value ||
        projectContextToken !== contextToken ||
        projectParam.value !== id
      ) {
        return false
      }
      adoptProjectDetail(response)
      return true
    } catch {
      return false
    }
  }

  async function loadLatestAfterMessageConflict(): Promise<void> {
    if (sendBusy.value) return
    const id = projectParam.value ?? currentProject.value?.id
    if (!id) return
    const contextToken = projectContextToken
    sendError.value = ''
    try {
      const response = await api.getProject(id)
      if (projectContextToken !== contextToken || projectParam.value !== id) return
      adoptProjectDetail(response)
      resetMessageConflict()
      announce('已加载最新项目；未发送的文字仍保留。')
    } catch (error) {
      sendError.value = errorMessage(error, '加载最新需求失败，请稍后重试。')
    }
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
    const id = routeProjectId(route.params, route.query)
    if (id === projectParam.value) return
    if (hasUnsavedChanges.value) {
      const target = router.resolve({
        name: 'describe',
        query: routeQueryWithoutProject(),
        hash: route.hash,
      }).fullPath
      window.history.replaceState(window.history.state, '', target)
      void updateProjectUrl(projectParam.value, 'replace')
      openNavigationConfirm({ kind: 'history', projectId: id })
      return
    }
    projectParam.value = id
    if (id === null) {
      clearProjectDetail()
      return
    }
    void loadProject(id)
  }

  function performHistoryNavigation(id: string | null): void {
    creationUnknown.value = false
    sendError.value = ''
    if (id === null) {
      clearProjectDetail()
      projectParam.value = null
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

  watch(
    () => [route.params.projectId, route.query.project],
    () => {
      const nextProject = routeProjectId(route.params, route.query)
      if (nextProject !== projectParam.value) syncProjectFromUrl()
    },
    { flush: 'sync' },
  )

  async function checkHealth(): Promise<void> {
    try {
      const status = await api.getHealth()
      healthStatus.value = status.status === 'ok' ? 'ok' : 'error'
    } catch {
      healthStatus.value = 'error'
    }
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
          ? error.status === 502 || error.status === 503 || error.status === 504
            ? '后端服务不可用，请确认后端已启动后重试。'
            : error.message
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
      sidebarExpanded.value = next !== 'narrow'
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
    if (document.visibilityState === 'hidden') return
    if (!configStatus.value) {
      void checkHealth()
      void refreshConfig()
    }
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
    void checkHealth()
    void refreshConfig()
    if (hasUnsavedChanges.value) window.addEventListener('beforeunload', handleBeforeUnload)
    if (projectParam.value !== null) void loadProject(projectParam.value)
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
    clearReferenceState()
    window.removeEventListener('popstate', syncProjectFromUrl)
    window.removeEventListener('beforeunload', handleBeforeUnload)
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  })

  return {
    draft,
    draftDirty,
    attachmentPending,
    hasUnsavedChanges,
    navigationConfirmOpen,
    navigationIntentLabel,
    confirmNavigation,
    cancelNavigation,
    projectParam,
    configStatus,
    configError,
    configLoading,
    healthStatus,
    sidebarExpanded,
    sideDrawerOpen,
    modelDrawerOpen,
    projectDrawerOpen,
    announcement,
    breakpoint,
    isMobile,
    isCompact,
    projectList,
    projectListLoading,
    projectListError,
    projectListMoreError,
    nextCursor,
    projectListLoadingMore,
    projectQuery,
    projectSearchBusy,
    projectSearchError,
    projectRenameBusy,
    projectRenameError,
    setProjectQuery,
    renameProject,
    currentProject,
    projectMessages,
    projectGenerations,
    projectLoading,
    projectError,
    projectErrorKind,
    sendBusy,
    sendError,
    messageConflict,
    messageConflictText,
    messageLocalCopy,
    showMessageLocalCopy,
    loadLatestAfterMessageConflict,
    refreshProjectDetail,
    adoptCreatedProject,
    creationUnknown,
    organizing,
    sendDisabledReason,
    referencePending,
    referenceError,
    referenceNotice,
    referenceAction,
    referenceBusy,
    referenceConflict,
    referenceCapability,
    referenceLimitBytes,
    referenceMimeTypes,
    referenceUploadDisabledReason,
    referenceRemoveDisabledReason,
    latestGenerationView,
    activeGeneration,
    latestGenerationError,
    chooseReferenceFiles,
    retryReferenceUpload,
    removeReference,
    reloadLatestReference,
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
