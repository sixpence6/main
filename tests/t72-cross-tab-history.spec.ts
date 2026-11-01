import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/vue'
import type { AccountView, ProjectDetailResponse } from '@/api/types'
import {
  clearSession,
  currentAccount,
  ensureSession,
  installSessionLifecycle,
  sessionEstablished,
  sessionInvalidated,
} from '@/auth/session'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { PROJECT_A, configuredConfig, emptyConfig, sampleGeneration, sampleProject } from './support/fixtures'

let harness: WorkspaceHarness | undefined
let cleanupLifecycle: (() => void) | undefined
let originalBroadcastChannel: typeof window.BroadcastChannel | undefined

const ACCOUNT_A: AccountView = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  email: 'a@example.com',
  is_admin: false,
  status: 'active',
  created_at: '2026-10-01T00:00:00.000Z',
  last_login_at: null,
}
const ACCOUNT_B: AccountView = {
  ...ACCOUNT_A,
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  email: 'b@example.com',
}

function authBody(account: AccountView, sessionId: string) {
  return {
    account,
    session_id: sessionId,
    csrf_token: 'csrf-' + sessionId,
    expires_at: '2026-11-02T00:00:00.000Z',
  }
}

function detailBody(title = '项目甲'): ProjectDetailResponse {
  return { project: sampleProject({ id: PROJECT_A, title }), messages: [], generations: [] }
}

class FakeBroadcastChannel {
  static all: FakeBroadcastChannel[] = []
  static messages: unknown[] = []
  name: string
  onmessage: ((event: MessageEvent) => void) | null = null

  constructor(name: string) {
    this.name = name
    FakeBroadcastChannel.all.push(this)
  }

  postMessage(data: unknown): void {
    FakeBroadcastChannel.messages.push(data)
    for (const channel of FakeBroadcastChannel.all) {
      if (channel === this || channel.name !== this.name) continue
      channel.onmessage?.(new MessageEvent('message', { data }))
    }
  }

  close(): void {
    FakeBroadcastChannel.all = FakeBroadcastChannel.all.filter((item) => item !== this)
  }
}

function installFakeBroadcastChannel(): void {
  originalBroadcastChannel = window.BroadcastChannel
  FakeBroadcastChannel.all = []
  FakeBroadcastChannel.messages = []
  Object.defineProperty(window, 'BroadcastChannel', {
    configurable: true,
    writable: true,
    value: FakeBroadcastChannel,
  })
}

function pageShow(persisted: boolean): void {
  const event = new Event('pageshow')
  Object.defineProperty(event, 'persisted', { value: persisted })
  window.dispatchEvent(event)
}

async function goToGenerate(): Promise<void> {
  const nav = screen.getByRole('navigation', { name: '工作阶段' })
  await harness!.user.click(within(nav).getByRole('button', { name: '生成图片' }))
  await screen.findByRole('heading', { name: '生成图片' })
  await waitFor(() => expect(harness!.router.currentRoute.value.name).toBe('generate'))
}

beforeEach(() => {
  clearSession()
  sessionInvalidated.value = false
  sessionEstablished.value = false
  installFakeBroadcastChannel()
})

afterEach(() => {
  cleanupLifecycle?.()
  cleanupLifecycle = undefined
  clearSession()
  harness?.dispose()
  harness = undefined
  if (originalBroadcastChannel) {
    Object.defineProperty(window, 'BroadcastChannel', {
      configurable: true,
      writable: true,
      value: originalBroadcastChannel,
    })
  } else {
    delete (window as unknown as Record<string, unknown>).BroadcastChannel
  }
})

describe('T72 跨标签、历史返回与账号绑定恢复', () => {
  it('跨标签只传播失效信号，身份变化清理旧项目', async () => {
    let sessionSwitcher: (account: AccountView) => void = () => undefined
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      setup(fetch) {
        let sessionAccount = ACCOUNT_A
        let sessionCalls = 0
        fetch.route('GET', '/api/auth/session', () => {
          sessionCalls += 1
          return {
            body: authBody(sessionAccount, 'session-' + sessionAccount.id.slice(0, 1)),
            delayMs: sessionCalls > 1 ? 120 : 0,
          }
        })
        fetch.json('GET', '/api/projects/' + PROJECT_A, detailBody())
        sessionSwitcher = (account) => {
          sessionAccount = account
        }
      },
    })
    cleanupLifecycle = installSessionLifecycle()
    await ensureSession(true)
    window.history.pushState({}, '', '/?project=' + PROJECT_A)
    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()

    sessionSwitcher(ACCOUNT_B)
    const otherTab = new FakeBroadcastChannel('v3-session-lifecycle')
    otherTab.postMessage({ type: 'session-invalidated', nonce: 'signal-1' })

    expect(await screen.findByText('从一个商品想法开始')).toBeVisible()
    await waitFor(() => expect(harness!.router.currentRoute.value.fullPath).toBe('/'))
    expect(harness!.router.currentRoute.value.name).toBe('describe')
    expect(harness!.router.currentRoute.value.query.project).toBeUndefined()
    expect(screen.queryByRole('heading', { name: '项目甲' })).not.toBeInTheDocument()
    expect(currentAccount.value?.id).toBe(ACCOUNT_B.id)

    expect(FakeBroadcastChannel.messages.length).toBeGreaterThan(0)
    for (const message of FakeBroadcastChannel.messages) {
      expect(Object.keys(message as Record<string, unknown>).sort()).toEqual(['nonce', 'type'])
      expect(JSON.stringify(message)).not.toContain(ACCOUNT_A.email)
      expect(JSON.stringify(message)).not.toContain(ACCOUNT_B.email)
      expect(JSON.stringify(message)).not.toContain(PROJECT_A)
      expect(JSON.stringify(message)).not.toContain('csrf')
      expect(JSON.stringify(message)).not.toContain('session-a1')
    }
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
    expect(harness.fetch.count('POST', /\/workflow\/actions/)).toBe(0)
  })

  it('BFCache／历史返回先核验身份，核验期间隐藏旧业务', async () => {
    let sessionSwitcher: (account: AccountView) => void = () => undefined
    harness = await mountWorkspace({
      config: emptyConfig(),
      viewportWidth: 1440,
      setup(fetch) {
        let sessionAccount = ACCOUNT_A
        let sessionCalls = 0
        fetch.route('GET', '/api/auth/session', () => {
          sessionCalls += 1
          return {
            body: authBody(sessionAccount, 'session-' + sessionAccount.id.slice(0, 1)),
            delayMs: sessionCalls > 1 ? 120 : 0,
          }
        })
        fetch.json('GET', '/api/projects/' + PROJECT_A, detailBody())
        sessionSwitcher = (account) => {
          sessionAccount = account
        }
      },
    })
    cleanupLifecycle = installSessionLifecycle()
    await ensureSession(true)
    window.history.pushState({}, '', '/?project=' + PROJECT_A)
    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(await screen.findByRole('heading', { name: '项目甲' })).toBeVisible()

    sessionSwitcher(ACCOUNT_B)
    pageShow(true)
    expect(await screen.findByText('正在重新验证登录状态…')).toBeVisible()
    expect(await screen.findByText('从一个商品想法开始')).toBeVisible()
    expect(screen.queryByRole('heading', { name: '项目甲' })).not.toBeInTheDocument()
    expect(currentAccount.value?.id).toBe(ACCOUNT_B.id)
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)
  })

  it('跨账号迟到的项目响应不会覆盖新身份', async () => {
    let sessionSwitcher: (account: AccountView) => void = () => undefined
    let firstProjectRequest = false
    currentAccount.value = ACCOUNT_A
    sessionEstablished.value = true
    harness = await mountWorkspace({
      config: emptyConfig(),
      url: '/?project=' + PROJECT_A,
      setup(fetch) {
        let sessionAccount = ACCOUNT_A
        fetch.route('GET', '/api/auth/session', () => ({
          body: authBody(sessionAccount, 'session-' + sessionAccount.id.slice(0, 1)),
          delayMs: 30,
        }))
        fetch.route('GET', '/api/projects/' + PROJECT_A, () => {
          firstProjectRequest = true
          return { body: detailBody('旧账号项目'), delayMs: 400 }
        })
        sessionSwitcher = (account) => {
          sessionAccount = account
        }
      },
    })
    cleanupLifecycle = installSessionLifecycle()
    await ensureSession(true)
    await waitFor(() => expect(firstProjectRequest).toBe(true))
    expect(harness.fetch.count('GET', '/api/projects/' + PROJECT_A)).toBe(1)

    sessionSwitcher(ACCOUNT_B)
    const otherTab = new FakeBroadcastChannel('v3-session-lifecycle')
    otherTab.postMessage({ type: 'session-invalidated', nonce: 'late-response' })

    await waitFor(() => expect(currentAccount.value?.id).toBe(ACCOUNT_B.id))
    expect(await screen.findByText('b@example.com')).toBeVisible()
    await new Promise((resolve) => setTimeout(resolve, 450))
    expect(screen.queryByRole('heading', { name: '旧账号项目' })).not.toBeInTheDocument()
    expect(screen.queryByText('旧账号项目')).not.toBeInTheDocument()
    expect(harness.fetch.count('POST', /\/generations(?:\/|$)/)).toBe(0)

  })

})
