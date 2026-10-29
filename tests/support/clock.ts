import { vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'

/** 控制业务定时器与系统时间；不要用真实等待验证查询节奏。 */
export class ClockController {
  private installed = false

  install(now = new Date('2026-01-01T00:00:00.000Z')): this {
    vi.useFakeTimers()
    vi.setSystemTime(now)
    this.installed = true
    return this
  }

  async advance(ms: number): Promise<void> {
    await vi.advanceTimersByTimeAsync(ms)
    await flushPromises()
  }

  async runPending(): Promise<void> {
    await vi.runOnlyPendingTimersAsync()
    await flushPromises()
  }

  restore(): void {
    if (this.installed) {
      vi.useRealTimers()
      this.installed = false
    }
  }
}
