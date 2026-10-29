import { render, screen, waitFor, within } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import ElementPlus from 'element-plus'
import { nextTick } from 'vue'
import { vi } from 'vitest'
import App from '@/App.vue'
import type { ConfigStatus } from '@/api/types'
import { ClockController } from './clock'
import { emptyConfig } from './fixtures'
import { FetchController, type MockStep } from './fetch'
import { StorageController } from './storage'
import { VisibilityController } from './visibility'

export interface MountWorkspaceOptions {
  config?: ConfigStatus
  configResponses?: MockStep[]
  /** 挂载前注册的 HTTP 路由；首屏即读取详情时使用。 */
  routes?: Array<{ method: string; url: string | RegExp; responses: MockStep[] }>
  /** 挂载前对 HTTP 边界做任意设置；首屏请求必须先注册路由时使用。 */
  setup?: (fetch: FetchController) => void
  fakeClock?: boolean
  /** 视口宽度（px），在挂载前设置，用于断点相关行为。 */
  viewportWidth?: number
  /** 初始地址（可含 ?project=...），测试结束恢复原地址。 */
  url?: string
}

/**
 * 唯一的工作台测试入口：挂载完整 App，保留真实子组件，只在 HTTP、
 * 时钟、页面可见性和存储边界上做替身。
 */
export async function mountWorkspace(options: MountWorkspaceOptions = {}) {
  const fetchController = new FetchController().install()
  const clock = new ClockController()
  if (options.fakeClock) clock.install()
  const visibility = new VisibilityController().install()
  const storage = new StorageController().install()

  const originalUrl = window.location.href
  const viewportDescriptor = Object.getOwnPropertyDescriptor(window, 'innerWidth')
  const setViewportWidth = async (width: number): Promise<void> => {
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      writable: true,
      value: width,
    })
    window.dispatchEvent(new Event('resize'))
    await nextTick()
  }
  if (options.url) window.history.replaceState({}, '', options.url)
  if (options.viewportWidth !== undefined) await setViewportWidth(options.viewportWidth)

  const steps = options.configResponses ?? [{ body: options.config ?? emptyConfig() }]
  fetchController.sequence('GET', '/api/config/status', steps)
  for (const route of options.routes ?? []) {
    fetchController.sequence(route.method, route.url, route.responses)
  }
  options.setup?.(fetchController)

  const user = userEvent.setup(
    options.fakeClock ? { advanceTimers: vi.advanceTimersByTime } : undefined,
  )
  const rendered = render(App, {
    global: { plugins: [ElementPlus] },
  })

  return {
    ...rendered,
    user,
    fetch: fetchController,
    clock,
    visibility,
    storage,
    screen,
    within,
    waitFor,
    setViewportWidth,
    dispose(): void {
      rendered.unmount()
      fetchController.restore()
      visibility.restore()
      storage.restore()
      clock.restore()
      window.history.replaceState({}, '', originalUrl)
      if (viewportDescriptor) {
        Object.defineProperty(window, 'innerWidth', viewportDescriptor)
      }
    },
  }
}

export type WorkspaceHarness = Awaited<ReturnType<typeof mountWorkspace>>
