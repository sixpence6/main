import type { Router } from 'vue-router'
import { ApiError, api, setUnauthorizedHandler } from '@/api/client'
import { setCsrfToken } from '@/api/csrf'
import type { AccountView, LoginResponse } from '@/api/types'
import { ref } from 'vue'

export const currentAccount = ref<AccountView | null>(null)

let bootstrapped: Promise<boolean> | null = null

setUnauthorizedHandler(() => {
  currentAccount.value = null
  setCsrfToken(null)
  bootstrapped = null
})

function clearLocalSession(): void {
  currentAccount.value = null
  setCsrfToken(null)
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
      currentAccount.value = session.account
      return true
    } catch (error) {
      if (isUnauthenticated(error)) {
        clearLocalSession()
        return false
      }
      // 认证服务或网络暂时不可用时不得把页面误当作已登录，也不缓存失败结果。
      clearLocalSession()
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
  currentAccount.value = session.account
  bootstrapped = Promise.resolve(true)
  return session
}

export async function logout(): Promise<void> {
  try {
    await api.logout()
  } catch (error) {
    if (!isUnauthenticated(error)) throw error
  } finally {
    clearLocalSession()
    bootstrapped = Promise.resolve(false)
  }
}

export function installAuthGuard(router: Router): void {
  router.beforeEach(async (to) => {
    const authenticated = await ensureSession(true)
    if (to.name === 'login') {
      return authenticated ? { name: 'describe' } : true
    }
    if (authenticated) return true
    return { name: 'login', query: { redirect: to.fullPath } }
  })
}
