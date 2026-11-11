export interface SessionInvalidationSignal {
  type: 'session-invalidated'
  nonce: string
}

const CHANNEL_NAME = 'v3-session-lifecycle'
const SIGNAL_STORAGE_KEY = 'v3-session-lifecycle-signal'

let channel: BroadcastChannel | null = null
let active = false

function createNonce(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

function isSignal(value: unknown): value is SessionInvalidationSignal {
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>
  return record.type === 'session-invalidated' && typeof record.nonce === 'string'
}

/**
 * 跨标签只广播“当前身份需要重新核验”这一事实。消息不包含账号、项目、
 * 业务正文、Cookie、CSRF 或任何恢复凭据。
 */
export function broadcastSessionInvalidated(): void {
  if (!active || typeof window === 'undefined') return
  const signal: SessionInvalidationSignal = { type: 'session-invalidated', nonce: createNonce() }
  if (channel) {
    channel.postMessage(signal)
    return
  }
  try {
    window.localStorage.setItem(SIGNAL_STORAGE_KEY, JSON.stringify(signal))
  } catch {
    // 存储不可用时只是不能通知其他标签；本标签的清理仍会完成。
  }
}

export function installCrossTabSessionListener(onInvalidate: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined
  const BroadcastChannelCtor = window.BroadcastChannel
  if (typeof BroadcastChannelCtor === 'function') {
    channel = new BroadcastChannelCtor(CHANNEL_NAME)
    channel.onmessage = (event: MessageEvent) => {
      if (isSignal(event.data)) onInvalidate()
    }
  }
  const onStorage = (event: StorageEvent): void => {
    if (event.key !== SIGNAL_STORAGE_KEY || !event.newValue) return
    try {
      if (isSignal(JSON.parse(event.newValue) as unknown)) onInvalidate()
    } catch {
      // 非本应用的信号不处理。
    }
  }
  window.addEventListener('storage', onStorage)
  active = true
  return () => {
    active = false
    window.removeEventListener('storage', onStorage)
    channel?.close()
    channel = null
  }
}
