import { inject, onBeforeUnmount, ref, watch, type ComputedRef, type InjectionKey, type Ref } from 'vue'
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

export function useWorkflow(projectId: WorkflowProjectId) {
  const view = ref<WorkflowView | null>(null)
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

  function armPoll(): void {
    clearPoll()
    if (disposed || actionBusy.value || !pageVisible() || !browserOnline()) return
    if (!canObserveBackground(view.value)) return
    pollTimer = setTimeout(() => {
      pollTimer = null
      void poll()
    }, POLL_INTERVAL_MS)
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
    armPoll()
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
      view.value = next
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
        sessionAtStart === sessionRevision.value &&
        sequence === actionSequence &&
        projectId.value === id
      ) view.value = next
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
