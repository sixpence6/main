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
  Brief,
  BriefFields,
  ConfigStatus,
  GenerationView,
  MessageCreateResponse,
  MessageView,
  PatchBriefRequest,
  ProjectSummary,
  ProjectView,
} from '@/api/types'
import { editableBriefFields, isUuid, type EditableBriefKey } from '@/utils/format'
import { useViewport } from './useViewport'

export type StageId = 'describe' | 'confirm' | 'generate' | 'results'
export type BriefAction = 'save' | 'confirm' | 'organize' | 'reload'
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
  promptStale: ComputedRef<boolean>
  saveDisabledReason: ComputedRef<string>
  confirmDisabledReason: ComputedRef<string>
  startBriefEdit: () => void
  cancelBriefEdit: () => void
  setBriefField: (key: EditableBriefKey, value: string) => void
  saveBrief: () => Promise<void>
  confirmBrief: () => Promise<void>
  reloadLatestBrief: () => Promise<void>
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

  const sendBusy = ref(false)
  const sendError = ref('')
  const creationUnknown = ref(false)
  const organizing = computed(() => currentProject.value?.brief_status === 'organizing')

  const briefDraft = ref<BriefFields | null>(null)
  const briefEditing = ref(false)
  const briefFieldErrors = ref<Partial<Record<EditableBriefKey, string>>>({})
  const briefAction = ref<BriefAction | null>(null)
  const briefSaveError = ref('')
  const briefConfirmError = ref('')
  const briefSaveNotice = ref('')
  const briefConflict = ref(false)
  const briefBusy = computed(() => briefAction.value !== null)
  const briefDirty = computed(() => {
    const project = currentProject.value
    const fields = briefDraft.value
    if (!project || !fields) return false
    return editableBriefFields.some((field) => fields[field.key] !== project.brief[field.key])
  })
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
  }

  const sendDisabledReason = computed(() => {
    if (sendBusy.value) return '正在发送消息，请稍候。'
    if (briefBusy.value) return '正在处理需求卡片，完成前不能发送新消息。'
    if (organizing.value) return '正在整理需求，完成前不能发送新消息。'
    if (briefDirty.value) return '有未保存的需求修改，请先保存或取消修改。'
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
    if (sendBusy.value) return '正在发送消息，完成前不能保存需求。'
    if (!currentProject.value) return '尚未选择项目。'
    if (briefConflict.value) return '需求已在别处更新，请先加载最新需求。'
    if (!briefEditing.value) return '请先进入编辑状态。'
    if (!briefDirty.value) return '尚未修改需求。'
    return ''
  })

  const confirmDisabledReason = computed(() => {
    if (sendBusy.value) return '正在发送消息，完成前不能确认需求。'
    if (briefBusy.value) return '正在处理需求操作，请稍候。'
    const project = currentProject.value
    if (!project) return '尚未选择项目。'
    if (briefConflict.value) return '需求已在别处更新，请先加载最新需求。'
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
    resetBriefDraft(null)
    briefEditing.value = false
    briefSaveError.value = ''
    briefConfirmError.value = ''
    briefSaveNotice.value = ''
    briefConflict.value = false
    projectLoading.value = false
    projectError.value = ''
    projectErrorKind.value = null
  }

  function createNewProject(): void {
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
    resetBriefDraft(null)
    briefEditing.value = false
    try {
      const response = await api.getProject(id, controller.signal)
      if (controller.signal.aborted || token !== detailToken) return
      currentProject.value = response.project
      projectMessages.value = response.messages
      projectGenerations.value = response.generations
      resetBriefDraft(response.project)
      briefEditing.value = false
      briefSaveError.value = ''
      briefConfirmError.value = ''
      briefSaveNotice.value = ''
      creationUnknown.value = false
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
        currentProject.value = project
        projectParam.value = project.id
        updateProjectUrl(project.id, 'push')
      }
      if (!project) return

      const response = await api.sendMessage(project.id, {
        content,
        expected_revision: project.revision,
      })
      if (projectParam.value !== project.id) return
      applyMessageResponse(response)
      creationUnknown.value = false
      draft.value = ''
      sendError.value = ''
      announce('需求整理完成，已显示最新回复。')
    } catch (error) {
      sendError.value = writeErrorMessage(error, '消息发送失败，输入内容已保留，请稍后重试。')
    } finally {
      sendBusy.value = false
    }
  }

  async function retryOrganize(): Promise<void> {
    if (sendBusy.value || briefBusy.value) return
    const project = currentProject.value
    if (!project) return
    if (briefDirty.value) {
      briefSaveError.value = '请先保存或取消当前修改，再重新整理需求。'
      return
    }
    const reason = modelConfigReason()
    if (reason) {
      sendError.value = reason
      return
    }
    briefAction.value = 'organize'
    sendError.value = ''
    try {
      const response = await api.sendMessage(project.id, {
        content: '请按当前已保存的需求重新整理。',
        expected_revision: project.revision,
      })
      if (projectParam.value !== project.id) return
      applyMessageResponse(response)
      announce('已重新请求整理当前需求。')
    } catch (error) {
      sendError.value = writeErrorMessage(error, '重新整理失败，已保存的卡片内容仍会保留。')
    } finally {
      briefAction.value = null
    }
  }

  function startBriefEdit(): void {
    const project = currentProject.value
    if (!project || sendBusy.value || briefBusy.value || project.brief_status === 'organizing') return
    if (briefConflict.value) {
      briefSaveError.value = '需求已在别处更新，请先加载最新需求后再编辑。'
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
    if (sendBusy.value || briefBusy.value) return
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
    briefAction.value = 'save'
    briefSaveError.value = ''
    briefConfirmError.value = ''
    briefSaveNotice.value = ''
    try {
      const updated = await api.updateBrief(project.id, patch)
      if (projectParam.value !== project.id) return
      currentProject.value = updated
      resetBriefDraft(updated)
      briefEditing.value = false
      const promptIsCurrent = Boolean(updated.brief.prompt.trim()) && updated.prompt_revision === updated.revision
      briefSaveNotice.value = promptIsCurrent
        ? '需求已保存。'
        : '需求已保存。旧提示词已过期，请重新整理需求后再确认。'
      announce('需求已保存。')
    } catch (error) {
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
    if (sendBusy.value || briefBusy.value) return
    const reason = confirmDisabledReason.value
    if (reason) {
      briefConfirmError.value = reason
      return
    }
    const project = currentProject.value
    if (!project) return
    briefAction.value = 'confirm'
    briefConfirmError.value = ''
    briefSaveError.value = ''
    briefSaveNotice.value = ''
    try {
      const updated = await api.confirmBrief(project.id, project.revision)
      if (projectParam.value !== project.id) return
      currentProject.value = updated
      resetBriefDraft(updated)
      briefEditing.value = false
      briefSaveNotice.value = '需求已确认。'
      announce('需求已确认。')
    } catch (error) {
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
    if (sendBusy.value || briefBusy.value) return
    const id = projectParam.value ?? currentProject.value?.id
    if (!id) return
    briefAction.value = 'reload'
    briefSaveError.value = ''
    briefConfirmError.value = ''
    try {
      const response = await api.getProject(id)
      if (projectParam.value !== id) return
      currentProject.value = response.project
      projectMessages.value = response.messages
      projectGenerations.value = response.generations
      resetBriefDraft(response.project)
      briefEditing.value = false
      briefConflict.value = false
      briefSaveNotice.value = '已加载最新需求。'
      announce('已加载最新需求。')
    } catch (error) {
      briefSaveError.value = writeErrorMessage(error, '最新需求加载失败，请稍后重试。')
    } finally {
      briefAction.value = null
    }
  }

  function selectProject(id: string): void {
    creationUnknown.value = false
    sendError.value = ''
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
    promptStale,
    saveDisabledReason,
    confirmDisabledReason,
    startBriefEdit,
    cancelBriefEdit,
    setBriefField,
    saveBrief,
    confirmBrief,
    reloadLatestBrief,
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
