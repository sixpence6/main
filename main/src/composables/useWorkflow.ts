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
  const actionBusy = ref(false)
  let controller: AbortController | null = null

  async function load(): Promise<void> {
    const id = projectId.value
    controller?.abort()
    if (!id) {
      view.value = null
      error.value = ''
      loading.value = false
      return
    }
    controller = new AbortController()
    loading.value = true
    error.value = ''
    try {
      view.value = await getWorkflow(id, controller.signal)
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      error.value = caught instanceof ApiError ? caught.message : '流程状态读取失败，请稍后重试。'
    } finally {
      loading.value = false
    }
  }

  async function runAction(
    action: WorkflowAction,
    text: string | null = null,
    payload: Record<string, unknown> | null = null,
  ): Promise<boolean> {
    const id = projectId.value
    if (!id || !view.value || actionBusy.value) return false
    actionBusy.value = true
    error.value = ''
    try {
      view.value = await runWorkflowAction(id, action.id, {
        expected_revision: view.value.project_revision,
        text,
        payload,
      })
      return true
    } catch (caught) {
      error.value = caught instanceof ApiError ? caught.message : '操作未能完成，请稍后重试。'
      if (caught instanceof ApiError && caught.status === 409) {
        const message = caught.message
        await load()
        error.value = message
      }
      return false
    } finally {
      actionBusy.value = false
    }
  }

  watch(projectId, () => void load(), { immediate: true })
  onBeforeUnmount(() => controller?.abort())

  return { view, loading, error, actionBusy, load, refresh: load, runAction }
}
