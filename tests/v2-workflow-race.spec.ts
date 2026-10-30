import { defineComponent, nextTick, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { getWorkflow, type WorkflowView } from '@/api/workflow'
import { useWorkflow } from '@/composables/useWorkflow'

vi.mock('@/api/workflow', () => ({
  getWorkflow: vi.fn(),
  runWorkflowAction: vi.fn(),
}))

const mockedGetWorkflow = vi.mocked(getWorkflow)

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function view(projectId: string): WorkflowView {
  return {
    project_id: projectId,
    run_id: `run-${projectId}`,
    stage: 'S01',
    status: 'active',
    project_revision: 1,
    requirement_revision: 1,
    intent_route: null,
    intent_action: null,
    issue: null,
    artifacts: [],
    approvals: [],
    available_actions: [],
    legacy: [],
    generation_job: null,
    delivery: null,
    updated_at: '2026-01-01T00:00:00.000Z',
  }
}

describe('useWorkflow project switching', () => {
  it('ignores a stale workflow response from the previous project', async () => {
    const first = deferred<ReturnType<typeof view>>()
    const second = deferred<ReturnType<typeof view>>()
    mockedGetWorkflow.mockImplementation((projectId) =>
      projectId === 'p1' ? first.promise : second.promise,
    )

    const Harness = defineComponent({
      setup() {
        const projectId = ref<string | null>('p1')
        const { view, loading } = useWorkflow(projectId)
        return { projectId, view, loading }
      },
      template: '<div>{{ view?.project_id ?? "none" }}</div>',
    })
    const wrapper = mount(Harness)
    await nextTick()
    expect(wrapper.text()).toBe('none')

    wrapper.vm.projectId = 'p2'
    await nextTick()
    second.resolve(view('p2'))
    await flushPromises()
    expect(wrapper.text()).toBe('p2')

    first.resolve(view('p1'))
    await flushPromises()
    expect(wrapper.text()).toBe('p2')
    expect(wrapper.vm.loading).toBe(false)
  })
})
