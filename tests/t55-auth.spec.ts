import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createMemoryHistory } from 'vue-router'
import LoginPage from '@/pages/LoginPage.vue'
import { createAppRouter } from '@/router'
import { installAuthGuard } from '@/auth/session'
import { FetchController } from './support/fetch'

let fetchController: FetchController | undefined

afterEach(() => {
  fetchController?.restore()
  fetchController = undefined
})

const account = {
  id: 'account-admin',
  email: 'admin@example.com',
  is_admin: true,
  status: 'active' as const,
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
}

describe('T55 登录与认证守卫', () => {
  it('未登录访问工作台时跳转登录页并保留原地址', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/auth/session', {
      error: { code: 'AUTH_REQUIRED', message: '请先登录' },
    }, 401)

    const router = createAppRouter(createMemoryHistory())
    installAuthGuard(router)
    await router.push('/generate?project=abc')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/generate?project=abc')
    expect(fetchController.count('GET', '/api/auth/session')).toBeGreaterThanOrEqual(1)
  })

  it('登录页先取预认证 CSRF，再提交同一 token 且不在浏览器存储密码', async () => {
    fetchController = new FetchController().install()
    fetchController.json('GET', '/api/auth/csrf', { csrf_token: 'preauth-csrf' })
    fetchController.json('POST', '/api/auth/login', {
      account,
      csrf_token: 'session-csrf',
      expires_at: '2026-01-01T12:00:00Z',
    })

    const router = createAppRouter(createMemoryHistory())
    await router.push('/login')
    await router.isReady()
    render(LoginPage, { global: { plugins: [router] } })

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('邮箱'), 'admin@example.com')
    await user.type(screen.getByLabelText('密码'), 'correct horse battery staple')
    await user.click(screen.getByRole('button', { name: '登录' }))

    await waitFor(() => {
      expect(fetchController!.count('POST', '/api/auth/login')).toBe(1)
    })
    const loginRequest = fetchController.last('POST', '/api/auth/login')
    expect(loginRequest?.headers.get('x-csrf-token')).toBe('preauth-csrf')
    expect(loginRequest?.body).toEqual({
      email: 'admin@example.com',
      password: 'correct horse battery staple',
    })
    await waitFor(() => {
      expect(router.currentRoute.value.path).toBe('/')
    })
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
  })
})
