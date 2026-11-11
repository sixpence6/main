const LOCAL_EXACT_KEYS = ['v3-generation-request']
const LOCAL_PREFIXES = ['v3-generation-request:']
const SESSION_EXACT_KEYS = ['ecommerce-generation-pending']
const SESSION_PREFIXES = ['ecommerce-generation-pending:']

function removeMatching(
  storage: Storage,
  exactKeys: readonly string[],
  prefixes: readonly string[],
): void {
  for (const key of exactKeys) storage.removeItem(key)
  const matched: string[] = []
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index)
    if (key && prefixes.some((prefix) => key.startsWith(prefix))) matched.push(key)
  }
  for (const key of matched) storage.removeItem(key)
}

/**
 * 清除本应用保存在浏览器里的私有工作台恢复信息。
 * 认证秘密只存在于 HttpOnly Cookie；这里清的是稳定请求标识和未确认提交凭据。
 */
export function clearPrivateBrowserState(): void {
  if (typeof window === 'undefined') return
  try {
    removeMatching(window.localStorage, LOCAL_EXACT_KEYS, LOCAL_PREFIXES)
  } catch {
    // 存储不可用时不能阻塞退出或会话失效处理。
  }
  try {
    removeMatching(window.sessionStorage, SESSION_EXACT_KEYS, SESSION_PREFIXES)
  } catch {
    // 存储不可用时不能阻塞退出或会话失效处理。
  }
}
