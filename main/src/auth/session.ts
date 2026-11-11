import type { Router } from 'vue-router'
import { ApiError, api, setUnauthorizedHandler } from '@/api/client'
import { setCsrfToken } from '@/api/csrf'
import type { AccountView, LoginResponse } from '@/api/types'
import { ref } from 'vue'
import { clearPrivateBrowserState } from './privateData'
import { broadcastSessionInvalidated, installCrossTabSessionListener } from './crossTab'

export const currentAccount = ref<AccountView | null>(null)
export const currentSessionId = ref<string | null>(null)
export const sessionEstablished = ref(false)
export const sessionInvalidated = ref(false)
/** 历史返回或跨标签通知后，身份核验完成前隐藏旧业务。 */
export const sessionRevalidating = ref(false)
/** 每次账号或登录生命周期变化都会递增；同一会话的普通导航不会变化。 */
export const sessionRevision = ref(0)

let bootstrapped: Promise<boolean> | null = null
let sessionCheck: Promise<boolean> | null = null
let activeSessionKey: string | null = null
let authRouter: Router | null = null
let lifecycleCleanup: (() => void) | null = null
let lifecycleRevalidation: Promise<boolean> | null = null

function applySession(account: AccountView, sessionId: string | null): void {
  currentAccount.value = account
  currentSessionId.value = sessionId
  sessionEstablished.value = true
  sessionInvalidated.value = false
  const key = account.id + ':' + (sessionId ?? 'session')
  if (key !== activeSessionKey) {
    activeSessionKey = key
    sessionRevision.value += 1
    broadcastSessionInvalidated()
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
  if (hadSession) broadcastSessionInvalidated()
}

function safeAuthRedirect(fullPath: string): string {
  if (!fullPath.startsWith('/') || fullPath.startsWith('//') || fullPath.startsWith('/login')) return '/'
  try {
    const parsed = new URL(fullPath, 'http://local.invalid')
    // 项目地址属于旧账号的私有上下文，重新登录时不得自动回到该项目。
    if (parsed.searchParams.has('project')) return '/'
  } catch {
    return '/'
  }
  return fullPath
}

function redirectToLogin(): void {
  if (!authRouter) return
  const current = authRouter.currentRoute.value
  if (current.meta.public || current.name === 'login') return
  void authRouter.replace({ name: 'login', query: { redirect: safeAuthRedirect(current.fullPath) } })
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
  if (sessionCheck) return sessionCheck
  const check = (async () => {
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
  sessionCheck = check
  bootstrapped = check
  try {
    return await check
  } finally {
    if (sessionCheck === check) sessionCheck = null
  }
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

async function revalidateForLifecycleChange(): Promise<boolean> {
  if (activeSessionKey === null && !sessionEstablished.value) return false
  sessionRevalidating.value = true
  if (!lifecycleRevalidation) {
    lifecycleRevalidation = ensureSession(true).finally(() => {
      lifecycleRevalidation = null
      sessionRevalidating.value = false
    })
  }
  return lifecycleRevalidation
}

/**
 * 接收跨标签失效信号、从浏览器前进／后退返回以及 BFCache 恢复时，
 * 先向服务端重新核验当前身份；核验完成前工作台保持隐藏。
 */
export function installSessionLifecycle(): () => void {
  if (typeof window === 'undefined') return () => undefined
  if (lifecycleCleanup) return lifecycleCleanup
  const removeCrossTab = installCrossTabSessionListener(() => {
    void revalidateForLifecycleChange()
  })
  const onPageShow = (event: PageTransitionEvent): void => {
    if (event.persisted) void revalidateForLifecycleChange()
  }
  const onPopState = (): void => {
    void revalidateForLifecycleChange()
  }
  window.addEventListener('pageshow', onPageShow)
  window.addEventListener('popstate', onPopState)
  lifecycleCleanup = () => {
    removeCrossTab()
    window.removeEventListener('pageshow', onPageShow)
    window.removeEventListener('popstate', onPopState)
    lifecycleCleanup = null
  }
  return lifecycleCleanup
}

export function installAuthGuard(router: Router): void {
  authRouter = router
  router.beforeEach(async (to) => {
    // 设置密码页是一次性链接入口，不能因为匿名访问被登录守卫拦截。
    if (to.meta.public) return true
    // 普通路由切换复用当前会话快照；真正的失效由 401、跨标签信号和
    // 浏览器前进/后退的专项复验处理，避免每次点击都额外阻塞一次网络请求。
    const authenticated = await ensureSession(false)
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
