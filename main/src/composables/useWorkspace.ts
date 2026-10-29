import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  provide,
  ref,
  watch,
  type ComputedRef,
  type InjectionKey,
  type Ref,
} from 'vue'
import { ApiError, api, isAbortError } from '@/api/client'
import type {
  ConfigStatus,
  GenerationView,
  MessageView,
  ProjectSummary,
  ProjectView,
} from '@/api/types'
import { isUuid } from '@/utils/format'
import { useViewport } from './useViewport'

export type StageId = 'describe' | 'confirm' | 'generate' | 'results'
export type ProjectErrorKind = 'invalid' | 'not-found' | 'network' | 'generic'

export interface StageView {
  id: StageId
  label: string
  locked: boolean
  reason: string
}

export interface WorkspaceStore {
  draft: Ref<string>
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

  const stages = computed<StageView[]>(() => {
    const project = currentProject.value
    const hasProject = Boolean(projectParam.value)
    const hasReviewableBrief = Boolean(
      project && (project.brief_status === 'review' || project.brief_status === 'confirmed'),
    )
    const hasConfirmedBrief = project?.brief_status === 'confirmed'
    const hasGeneration = projectGenerations.value.some((item) => item.status === 'succeeded')
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
        locked: !hasConfirmedBrief,
        reason: hasProject
          ? '需求尚未确认，确认后才能生成图片。'
          : '尚未选择项目，生成图片将在需求确认后解锁。',
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

  function clearProjectDetail(): void {
    detailController?.abort()
    detailController = null
    detailToken += 1
    currentProject.value = null
    projectMessages.value = []
    projectGenerations.value = []
    projectLoading.value = false
    projectError.value = ''
    projectErrorKind.value = null
  }

  function createNewProject(): void {
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
    detailController?.abort()
    const controller = new AbortController()
    detailController = controller
    const token = ++detailToken
    projectLoading.value = true
    projectError.value = ''
    projectErrorKind.value = null
    currentProject.value = null
    projectMessages.value = []
    projectGenerations.value = []
    try {
      const response = await api.getProject(id, controller.signal)
      if (controller.signal.aborted || token !== detailToken) return
      currentProject.value = response.project
      projectMessages.value = response.messages
      projectGenerations.value = response.generations
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

  function selectProject(id: string): void {
    closeProjectDrawer()
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
    projectParam.value = id
    if (id === null) {
      clearProjectDetail()
      stage.value = 'describe'
      return
    }
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

  onMounted(() => {
    void refreshConfig()
    if (projectParam.value !== null) {
      void loadProject(projectParam.value)
    }
    window.addEventListener('popstate', syncProjectFromUrl)
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
    window.removeEventListener('popstate', syncProjectFromUrl)
  })

  return {
    draft,
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
