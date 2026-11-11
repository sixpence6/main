import { onBeforeUnmount, ref, shallowRef, watch, type ComputedRef, type InjectionKey, type Ref } from 'vue'
import { ApiError } from '@/api/client'
import { sessionRevision } from '@/auth/session'
import {
  getWorkflow,
  runWorkflowAction,
  type WorkflowAction,
  type WorkflowView,
} from '@/api/workflow'

export type WorkflowProjectId = Ref<string | null | undefined> | ComputedRef<string | null | undefined>

const POLL_INTERVAL_MS = 2500
const GENERATION_POLL_INTERVAL_MS = 1000
export type WorkflowController = ReturnType<typeof useWorkflow>
export const WORKFLOW_KEY: InjectionKey<WorkflowController> = Symbol('workflow')

function canObserveBackground(next: WorkflowView | null): boolean {
  if (!next || !next.run_id || next.status !== 'active') return false
  if (!next.stage || !['S07', 'S08', 'S09', 'S10'].includes(next.stage)) return false
  if (next.delivery) return false
  if (next.available_actions.some((action) => (
    action.id === 'approve_delivery' || action.id === 'publish_delivery'
  ))) return false
  if (next.intent && next.intent.status !== 'accepted' && next.intent.status !== 'executing') {
    return false
  }
  return true
}

function pageVisible(): boolean {
  return typeof document === 'undefined' || document.visibilityState === 'visible'
}

function browserOnline(): boolean {
  return typeof navigator === 'undefined' || navigator.onLine !== false
}

function sameWorkflow(left: WorkflowView | null, right: WorkflowView): boolean {
  if (!left) return false
  const leftVersions = left.versions ?? []
  const rightVersions = right.versions ?? []
  const leftLast = leftVersions[leftVersions.length - 1]
  const rightLast = rightVersions[rightVersions.length - 1]
  return (
    left.interaction_mode === right.interaction_mode &&
    left.project_id === right.project_id &&
    left.project_revision === right.project_revision &&
    left.requirement_revision === right.requirement_revision &&
    left.run_id === right.run_id &&
    left.status === right.status &&
    left.stage === right.stage &&
    left.updated_at === right.updated_at &&
    left.issue?.code === right.issue?.code &&
    left.generation_job?.status === right.generation_job?.status &&
    left.generation_job?.updated_at === right.generation_job?.updated_at &&
    left.delivery?.status === right.delivery?.status &&
    left.delivery?.asset_id === right.delivery?.asset_id &&
    left.prompt_preview?.id === right.prompt_preview?.id &&
    left.prompt_preview?.can_confirm === right.prompt_preview?.can_confirm &&
    left.intent?.status === right.intent?.status &&
    left.usage?.used === right.usage?.used &&
    leftVersions.length === rightVersions.length &&
    leftLast?.status === rightLast?.status &&
    leftLast?.asset_id === rightLast?.asset_id &&
    left.task_state?.reason?.code === right.task_state?.reason?.code &&
    left.task_state?.progress.awaiting_user === right.task_state?.progress.awaiting_user &&
    left.available_actions.map((action) => action.id).join('|') ===
      right.available_actions.map((action) => action.id).join('|')
  )
}

export function useWorkflow(projectId: WorkflowProjectId) {
  // WorkflowView is replaced byte-for-byte as one immutable server snapshot.
  // A shallow ref avoids walking every artifact, version and approval on each poll.
  const view = shallowRef<WorkflowView | null>(null)
  const loading = ref(false)
  const error = ref('')
  const errorCode = ref('')
  const actionBusy = ref(false)
  let controller: AbortController | null = null
  let loadSequence = 0
  let actionSequence = 0
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let disposed = false

  function clearPoll(): void {
    if (pollTimer !== null) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  }

  function pollDelay(): number {
    return view.value && ['S08', 'S09', 'S10'].includes(view.value.stage ?? '')
      ? GENERATION_POLL_INTERVAL_MS
      : POLL_INTERVAL_MS
  }

  function armPoll(): void {
    clearPoll()
    if (disposed || actionBusy.value || !pageVisible() || !browserOnline()) return
    if (!canObserveBackground(view.value)) return
    pollTimer = setTimeout(() => {
      pollTimer = null
      void poll()
    }, pollDelay())
  }

  async function poll(): Promise<void> {
    if (disposed || actionBusy.value || !pageVisible() || !browserOnline()) return
    if (!canObserveBackground(view.value)) return
    await load(true)
  }

  function handleVisibilityChange(): void {
    if (!pageVisible()) {
      clearPoll()
      return
    }
    // Returning to the tab should confirm an active task immediately instead of
    // waiting through another full polling interval.
    if (canObserveBackground(view.value)) void load(true)
    else armPoll()
  }

  function handleOnline(): void {
    armPoll()
  }

  function handleOffline(): void {
    clearPoll()
  }

  async function load(silent = false): Promise<void> {
    clearPoll()
    const id = projectId.value
    const sessionAtStart = sessionRevision.value
    const sequence = ++loadSequence
    controller?.abort()
    if (!id) {
      view.value = null
      error.value = ''
      errorCode.value = ''
      loading.value = false
      return
    }
    const currentController = new AbortController()
    controller = currentController
    // Never let a previous project's actions, prompt or delivery leak into the
    // newly selected conversation while the next snapshot is in flight.
    if (view.value && view.value.project_id !== id) view.value = null
    if (!silent) {
      loading.value = true
      error.value = ''
      errorCode.value = ''
    }
    try {
      const next = await getWorkflow(id, currentController.signal)
      if (
        sessionAtStart !== sessionRevision.value ||
        sequence !== loadSequence ||
        projectId.value !== id
      ) return
      if (!sameWorkflow(view.value, next)) view.value = next
    } catch (caught) {
      if (
        sessionAtStart !== sessionRevision.value ||
        sequence !== loadSequence ||
        projectId.value !== id
      ) return
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      if (silent && view.value) return
      error.value = caught instanceof ApiError ? caught.message : '流程状态读取失败，请稍后重试。'
      errorCode.value = caught instanceof ApiError ? caught.code : ''
    } finally {
      if (sequence === loadSequence) {
        if (!silent) loading.value = false
        armPoll()
      }
    }
  }

  async function runAction(
    action: WorkflowAction,
    text: string | null = null,
    payload: Record<string, unknown> | null = null,
  ): Promise<boolean> {
    const id = projectId.value
    if (!id || !view.value || actionBusy.value) return false
    const sessionAtStart = sessionRevision.value
    const sequence = ++actionSequence
    actionBusy.value = true
    error.value = ''
    errorCode.value = ''
    try {
      const next = await runWorkflowAction(id, action.id, {
        expected_revision: view.value.project_revision,
        text,
        payload,
      })
      if (
        sessionAtStart !== sessionRevision.value ||
        sequence !== actionSequence ||
        projectId.value !== id
      ) return false
      view.value = next
      return true
    } catch (caught) {
      if (
        sessionAtStart !== sessionRevision.value ||
        sequence !== actionSequence ||
        projectId.value !== id
      ) return false
      error.value = caught instanceof ApiError ? caught.message : '操作未能完成，请稍后重试。'
      errorCode.value = caught instanceof ApiError ? caught.code : ''
      if (caught instanceof ApiError && caught.status === 409) {
        const message = caught.message
        const code = caught.code
        await load()
        error.value = message
        errorCode.value = code
      }
      return false
    } finally {
      if (sequence === actionSequence) {
        actionBusy.value = false
        armPoll()
      }
    }
  }

  watch(projectId, () => {
    actionSequence += 1
    actionBusy.value = false
    clearPoll()
    void load()
  }, { immediate: true })
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handleVisibilityChange)
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)
  }
  onBeforeUnmount(() => {
    disposed = true
    clearPoll()
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
    if (typeof window !== 'undefined') {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
    controller?.abort()
  })

  return {
    view,
    loading,
    error,
    errorCode,
    actionBusy,
    load,
    adopt: (next: WorkflowView | null) => {
      loadSequence += 1
      controller?.abort()
      controller = null
      loading.value = false
      error.value = ''
      errorCode.value = ''
      view.value = next
      armPoll()
    },
    refresh: () => load(),
    runAction,
  }
}
