import { render, screen, waitFor, within } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import ElementPlus from 'element-plus'
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
  fakeClock?: boolean
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

  const steps = options.configResponses ?? [{ body: options.config ?? emptyConfig() }]
  fetchController.sequence('GET', '/api/config/status', steps)

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
    dispose(): void {
      rendered.unmount()
      fetchController.restore()
      visibility.restore()
      storage.restore()
      clock.restore()
    },
  }
}

export type WorkspaceHarness = Awaited<ReturnType<typeof mountWorkspace>>
