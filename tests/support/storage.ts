export class MemoryStorage implements Storage {
  private readonly values = new Map<string, string>()
  failReads = false
  failWrites = false

  get length(): number {
    return this.values.size
  }

  clear(): void {
    this.guardWrite()
    this.values.clear()
  }

  getItem(key: string): string | null {
    this.guardRead()
    return this.values.get(key) ?? null
  }

  key(index: number): string | null {
    this.guardRead()
    return [...this.values.keys()][index] ?? null
  }

  removeItem(key: string): void {
    this.guardWrite()
    this.values.delete(key)
  }

  setItem(key: string, value: string): void {
    this.guardWrite()
    this.values.set(key, String(value))
  }

  entries(): Record<string, string> {
    return Object.fromEntries(this.values)
  }

  private guardRead(): void {
    if (this.failReads) {
      throw new DOMException('Storage read failed', 'SecurityError')
    }
  }

  private guardWrite(): void {
    if (this.failWrites) {
      throw new DOMException('Storage write failed', 'QuotaExceededError')
    }
  }
}

/** 替换同源 sessionStorage/localStorage，并可注入读写失败。 */
export class StorageController {
  readonly session = new MemoryStorage()
  readonly local = new MemoryStorage()
  private sessionDescriptor?: PropertyDescriptor
  private localDescriptor?: PropertyDescriptor

  install(): this {
    this.sessionDescriptor = Object.getOwnPropertyDescriptor(window, 'sessionStorage')
    this.localDescriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      value: this.session,
    })
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: this.local,
    })
    return this
  }

  restore(): void {
    if (this.sessionDescriptor) {
      Object.defineProperty(window, 'sessionStorage', this.sessionDescriptor)
    }
    if (this.localDescriptor) {
      Object.defineProperty(window, 'localStorage', this.localDescriptor)
    }
  }
}
