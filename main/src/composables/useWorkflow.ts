import { onBeforeUnmount, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ApiError } from '@/api/client'
import {
  getWorkflow,
  runWorkflowAction,
  type WorkflowAction,
  type WorkflowView,
} from '@/api/workflow'

export type WorkflowProjectId = Ref<string | null | undefined> | ComputedRef<string | null | undefined>

export function useWorkflow(projectId: WorkflowProjectId) {
  const view = ref<WorkflowView | null>(null)
  const loading = ref(false)
  const error = ref('')
  const errorCode = ref('')
  const actionBusy = ref(false)
  let controller: AbortController | null = null
  let loadSequence = 0
  let actionSequence = 0

  async function load(): Promise<void> {
    const id = projectId.value
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
    loading.value = true
    error.value = ''
    errorCode.value = ''
    try {
      const next = await getWorkflow(id, currentController.signal)
      if (sequence !== loadSequence || projectId.value !== id) return
      view.value = next
    } catch (caught) {
      if (sequence !== loadSequence || projectId.value !== id) return
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      error.value = caught instanceof ApiError ? caught.message : '流程状态读取失败，请稍后重试。'
      errorCode.value = caught instanceof ApiError ? caught.code : ''
    } finally {
      if (sequence === loadSequence) loading.value = false
    }
  }

  async function runAction(
    action: WorkflowAction,
    text: string | null = null,
    payload: Record<string, unknown> | null = null,
  ): Promise<boolean> {
    const id = projectId.value
    if (!id || !view.value || actionBusy.value) return false
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
      if (sequence === actionSequence && projectId.value === id) view.value = next
      return true
    } catch (caught) {
      if (sequence !== actionSequence || projectId.value !== id) return false
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
      if (sequence === actionSequence) actionBusy.value = false
    }
  }

  watch(projectId, () => {
    actionSequence += 1
    actionBusy.value = false
    void load()
  }, { immediate: true })
  onBeforeUnmount(() => controller?.abort())

  return { view, loading, error, errorCode, actionBusy, load, refresh: load, runAction }
}
