import type { Router } from 'vue-router'
import { ApiError, api, setUnauthorizedHandler } from '@/api/client'
import { setCsrfToken } from '@/api/csrf'
import type { AccountView, LoginResponse } from '@/api/types'
import { ref } from 'vue'
import { clearPrivateBrowserState } from './privateData'

export const currentAccount = ref<AccountView | null>(null)
export const currentSessionId = ref<string | null>(null)
export const sessionEstablished = ref(false)
export const sessionInvalidated = ref(false)
/** 每次账号或登录生命周期变化都会递增；同一会话的普通导航不会变化。 */
export const sessionRevision = ref(0)

let bootstrapped: Promise<boolean> | null = null
let activeSessionKey: string | null = null
let authRouter: Router | null = null

function applySession(account: AccountView, sessionId: string | null): void {
  currentAccount.value = account
  currentSessionId.value = sessionId
  sessionEstablished.value = true
  sessionInvalidated.value = false
  const key = account.id + ':' + (sessionId ?? 'session')
  if (key !== activeSessionKey) {
    activeSessionKey = key
    sessionRevision.value += 1
  }
}

function dropSession(): void {
  const hadSession = activeSessionKey !== null
  currentAccount.value = null
  currentSessionId.value = null
  setCsrfToken(null)
  if (hadSession || sessionEstablished.value) sessionInvalidated.value = true
  sessionEstablished.value = false
  if (activeSessionKey !== null) {
    activeSessionKey = null
    sessionRevision.value += 1
  }
  clearPrivateBrowserState()
}

function redirectToLogin(): void {
  if (!authRouter) return
  const current = authRouter.currentRoute.value
  if (current.meta.public || current.name === 'login') return
  void authRouter.replace({ name: 'login', query: { redirect: current.fullPath } })
}

setUnauthorizedHandler(() => {
  // 引导阶段收到 401 时只清理状态，由路由守卫决定跳转；否则会和守卫的本轮导航互相竞争。
  const hadActiveSession = activeSessionKey !== null || sessionEstablished.value
  dropSession()
  bootstrapped = null
  if (hadActiveSession) redirectToLogin()
})

export function clearSession(): void {
  dropSession()
  bootstrapped = Promise.resolve(false)
}

function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}

export async function ensureSession(force = false): Promise<boolean> {
  if (!force && bootstrapped) return bootstrapped
  bootstrapped = (async () => {
    try {
      const session = await api.getSession()
      setCsrfToken(session.csrf_token)
      applySession(session.account, session.session_id ?? session.expires_at ?? null)
      return true
    } catch (error) {
      if (isUnauthenticated(error)) {
        clearSession()
        return false
      }
      // 认证服务或网络暂时不可用时不得把页面误当作已登录，也不缓存失败结果。
      clearSession()
      bootstrapped = null
      return false
    }
  })()
  return bootstrapped
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const preauth = await api.getLoginCsrf()
  setCsrfToken(preauth.csrf_token)
  const session = await api.login(email, password)
  setCsrfToken(session.csrf_token)
  applySession(session.account, session.session_id ?? session.expires_at ?? null)
  bootstrapped = Promise.resolve(true)
  return session
}

export async function logout(): Promise<void> {
  try {
    await api.logout()
  } catch (error) {
    if (!isUnauthenticated(error)) throw error
  } finally {
    clearSession()
    bootstrapped = Promise.resolve(false)
  }
}

export function installAuthGuard(router: Router): void {
  authRouter = router
  router.beforeEach(async (to) => {
    // 设置密码页是一次性链接入口，不能因为匿名访问被登录守卫拦截。
    if (to.meta.public) return true
    const authenticated = await ensureSession(true)
    if (to.name === 'login') {
      return authenticated ? { name: 'describe' } : true
    }
    if (!authenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    if (to.meta.requiresAdmin && !currentAccount.value?.is_admin) {
      return { name: 'describe' }
    }
    return true
  })
}
