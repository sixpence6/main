/** 控制 Page Visibility，用于验证隐藏页暂停与恢复。 */
export class VisibilityController {
  private state: DocumentVisibilityState = 'visible'

  install(): this {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => this.state,
    })
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => this.state === 'hidden',
    })
    return this
  }

  set(state: DocumentVisibilityState): void {
    this.state = state
    document.dispatchEvent(new Event('visibilitychange'))
  }

  hide(): void {
    this.set('hidden')
  }

  show(): void {
    this.set('visible')
  }

  restore(): void {
    delete (document as { visibilityState?: DocumentVisibilityState }).visibilityState
    delete (document as { hidden?: boolean }).hidden
  }
}
