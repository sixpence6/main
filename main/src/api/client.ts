import type {
  ApiErrorBody,
  AuthSessionResponse,
  CsrfResponse,
  LoginResponse,
  ConfigStatus,
  HealthStatus,
  ProjectDetailResponse,
  ProjectListResponse,
  ProjectView,
  MessageCreateResponse,
  ConfirmBriefRequest,
  GenerationSubmitResponse,
  GenerationView,
  PatchBriefRequest,
  SubmitGenerationRequest,
  AccountListResponse,
  AccountResponse,
  CreateAccountResponse,
  LinkIssueResponse,
  LinkPurpose,
  LinkStatusResponse,
  LinkPasswordResponse,
  SessionListResponse,
  SecurityEventListResponse,
  AdminSecurityEventListResponse,
  InstanceGenerationLimitsResponse,
  AccountGenerationLimitsResponse,
} from './types'
import { getCsrfToken, setCsrfToken } from './csrf'

const JSON_HEADERS = { Accept: 'application/json' }

export interface RequestJsonInit extends RequestInit {
  /** 自动轮询不得续期登录会话；用户主动操作才续期。 */
  authActivity?: 'user' | 'polling'
}

let unauthorizedHandler: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === 'AbortError') ||
    (error instanceof Error && error.name === 'AbortError')
  )
}

function defaultMessage(status: number): string {
  switch (status) {
    case 400:
      return '请求内容无法处理，请检查后重试。'
    case 404:
      return '请求的记录或文件不存在。'
    case 409:
      return '记录已更新，请读取最新内容后再操作。'
    case 413:
      return '文件超过允许大小，请选择更小的文件。'
    case 422:
      return '提交内容未通过校验，请检查后重试。'
    case 502:
      return '模型调用失败，请稍后重试。'
    case 503:
      return '所需模型尚未配置或当前不可用。'
    case 504:
      return '模型响应超时，请稍后重试。'
    default:
      return '请求失败，请稍后重试。'
  }
}

const UNSAFE_MESSAGE = /sk-[a-z0-9]|bearer\s|api[_-]?key|https?:\/\/|traceback|\bat\s+\S+\s*\(|stack\s*:|<script/i

function safeServerMessage(message: unknown): string | null {
  if (typeof message !== 'string') return null
  const trimmed = message.trim()
  if (!trimmed || trimmed.length > 200 || UNSAFE_MESSAGE.test(trimmed)) return null
  return trimmed
}

async function readErrorBody(response: Response): Promise<ApiErrorBody | null> {
  try {
    const body: unknown = await response.json()
    if (
      typeof body === 'object' &&
      body !== null &&
      'error' in body &&
      typeof (body as ApiErrorBody).error === 'object'
    ) {
      return body as ApiErrorBody
    }
  } catch {
    // 非 JSON 错误体不进入界面，只使用通用安全说明。
  }
  return null
}

export async function requestJson<T>(path: string, init: RequestJsonInit = {}): Promise<T> {
  const { authActivity = 'user', ...fetchInit } = init
  const method = (fetchInit.method ?? 'GET').toUpperCase()
  const headers = new Headers(JSON_HEADERS)
  if (fetchInit.headers) {
    new Headers(fetchInit.headers).forEach((value, key) => headers.set(key, value))
  }
  if (fetchInit.body !== undefined && !(fetchInit.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (method === 'GET' || method === 'HEAD') {
    if (authActivity === 'polling') headers.set('X-Polling', '1')
    else headers.set('X-Auth-Activity', 'user')
  }
  const csrfToken = getCsrfToken()
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }

  let response: Response
  try {
    response = await fetch(path, { ...fetchInit, headers, credentials: 'same-origin' })
  } catch (error) {
    if (isAbortError(error)) throw error
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接本机服务，请确认服务已启动。')
  }

  if (!response.ok) {
    if (response.status === 401) {
      setCsrfToken(null)
      unauthorizedHandler?.()
    }
    const body = await readErrorBody(response)
    const message = safeServerMessage(body?.error?.message) ?? defaultMessage(response.status)
    throw new ApiError(response.status, body?.error?.code ?? 'HTTP_ERROR', message)
  }

  if (response.status === 204) {
    return undefined as T
  }

  try {
    return (await response.json()) as T
  } catch (error) {
    if (isAbortError(error)) throw error
    throw new ApiError(
      response.status,
      'INVALID_RESPONSE',
      '服务返回的数据无法识别，请核实操作结果。',
    )
  }
}

export interface ListProjectsParams {
  limit?: number
  cursor?: string | null
  q?: string
}

export function assetUrl(id: string): string {
  return `/api/assets/${encodeURIComponent(id)}`
}

export const api = {
  getSession(signal?: AbortSignal): Promise<AuthSessionResponse> {
    return requestJson<AuthSessionResponse>('/api/auth/session', { signal })
  },
  getLoginCsrf(signal?: AbortSignal): Promise<CsrfResponse> {
    return requestJson<CsrfResponse>('/api/auth/csrf', { signal })
  },
  login(email: string, password: string): Promise<LoginResponse> {
    return requestJson<LoginResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
  },
  logout(): Promise<void> {
    return requestJson<void>('/api/auth/logout', { method: 'POST', body: '{}' })
  },
  listAccounts(signal?: AbortSignal): Promise<AccountListResponse> {
    return requestJson<AccountListResponse>('/api/admin/accounts', { signal })
  },
  createAccount(email: string): Promise<CreateAccountResponse> {
    return requestJson<CreateAccountResponse>('/api/admin/accounts', {
      method: 'POST',
      body: JSON.stringify({ email }),
    })
  },
  disableAccount(accountId: string): Promise<AccountResponse> {
    return requestJson<AccountResponse>(
      `/api/admin/accounts/${encodeURIComponent(accountId)}/disable`,
      { method: 'POST', body: '{}' },
    )
  },
  restoreAccount(accountId: string): Promise<AccountResponse> {
    return requestJson<AccountResponse>(
      `/api/admin/accounts/${encodeURIComponent(accountId)}/restore`,
      { method: 'POST', body: '{}' },
    )
  },
  getInstanceGenerationLimits(signal?: AbortSignal): Promise<InstanceGenerationLimitsResponse> {
    return requestJson<InstanceGenerationLimitsResponse>('/api/admin/generation-limits', { signal })
  },
  updateInstanceGenerationLimits(input: {
    daily_limit: number | null
    personal_slots: number | null
    instance_slots: number | null
  }): Promise<InstanceGenerationLimitsResponse> {
    return requestJson<InstanceGenerationLimitsResponse>('/api/admin/generation-limits', {
      method: 'PUT',
      body: JSON.stringify(input),
    })
  },
  getAccountGenerationLimits(
    accountId: string,
    signal?: AbortSignal,
  ): Promise<AccountGenerationLimitsResponse> {
    return requestJson<AccountGenerationLimitsResponse>(
      `/api/admin/accounts/${encodeURIComponent(accountId)}/generation-limits`,
      { signal },
    )
  },
  updateAccountGenerationLimits(
    accountId: string,
    input: { daily_limit: number | null; personal_slots: number | null },
  ): Promise<AccountGenerationLimitsResponse> {
    return requestJson<AccountGenerationLimitsResponse>(
      `/api/admin/accounts/${encodeURIComponent(accountId)}/generation-limits`,
      { method: 'PUT', body: JSON.stringify(input) },
    )
  },
  listAdminSecurityEvents(signal?: AbortSignal): Promise<AdminSecurityEventListResponse> {
    return requestJson<AdminSecurityEventListResponse>('/api/admin/security-events', { signal })
  },
  issueAccountLink(accountId: string, purpose: LinkPurpose): Promise<LinkIssueResponse> {
    return requestJson<LinkIssueResponse>(
      `/api/admin/accounts/${encodeURIComponent(accountId)}/links`,
      { method: 'POST', body: JSON.stringify({ purpose }) },
    )
  },
  getLinkStatus(token: string, signal?: AbortSignal): Promise<LinkStatusResponse> {
    return requestJson<LinkStatusResponse>('/api/auth/link', {
      signal,
      headers: { 'X-Link-Token': token },
    })
  },
  setPasswordWithLink(
    token: string,
    password: string,
    purpose: LinkPurpose,
  ): Promise<LinkPasswordResponse> {
    return requestJson<LinkPasswordResponse>('/api/auth/link', {
      method: 'POST',
      body: JSON.stringify({ token, password, purpose }),
    })
  },
  changePassword(currentPassword: string, newPassword: string): Promise<void> {
    return requestJson<void>('/api/auth/password/change', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    })
  },
  listSessions(signal?: AbortSignal): Promise<SessionListResponse> {
    return requestJson<SessionListResponse>('/api/auth/sessions', { signal })
  },
  revokeSession(sessionId: string): Promise<void> {
    return requestJson<void>('/api/auth/sessions/' + encodeURIComponent(sessionId), {
      method: 'DELETE',
    })
  },
  revokeOtherSessions(): Promise<void> {
    return requestJson<void>('/api/auth/sessions/revoke-others', {
      method: 'POST',
      body: '{}',
    })
  },
  listSecurityEvents(signal?: AbortSignal): Promise<SecurityEventListResponse> {
    return requestJson<SecurityEventListResponse>('/api/auth/security-events', { signal })
  },
  getHealth(signal?: AbortSignal): Promise<HealthStatus> {
    return requestJson<HealthStatus>('/api/health', { signal })
  },
  getConfigStatus(signal?: AbortSignal): Promise<ConfigStatus> {
    return requestJson<ConfigStatus>('/api/config/status', { signal })
  },
  listProjects(
    params: ListProjectsParams = {},
    signal?: AbortSignal,
  ): Promise<ProjectListResponse> {
    const query = new URLSearchParams()
    query.set('limit', String(params.limit ?? 20))
    if (params.cursor) query.set('cursor', params.cursor)
    if (params.q) query.set('q', params.q)
    return requestJson<ProjectListResponse>(`/api/projects?${query.toString()}`, { signal })
  },
  getProject(
    id: string,
    signal?: AbortSignal,
    polling = false,
  ): Promise<ProjectDetailResponse> {
    return requestJson<ProjectDetailResponse>(`/api/projects/${encodeURIComponent(id)}`, {
      signal,
      authActivity: polling ? 'polling' : 'user',
    })
  },
  createProject(): Promise<ProjectView> {
    return requestJson<ProjectView>('/api/projects', { method: 'POST', body: '{}' })
  },
  renameProject(projectId: string, title: string): Promise<ProjectView> {
    return requestJson<ProjectView>(
      `/api/projects/${encodeURIComponent(projectId)}`,
      { method: 'PATCH', body: JSON.stringify({ title }) },
    )
  },
  sendMessage(
    projectId: string,
    input: { content: string; expected_revision: number; request_id?: string },
  ): Promise<MessageCreateResponse> {
    return requestJson<MessageCreateResponse>(
      `/api/projects/${encodeURIComponent(projectId)}/messages`,
      { method: 'POST', body: JSON.stringify(input) },
    )
  },
  updateBrief(projectId: string, input: PatchBriefRequest): Promise<ProjectView> {
    return requestJson<ProjectView>(
      `/api/projects/${encodeURIComponent(projectId)}/brief`,
      { method: 'PATCH', body: JSON.stringify(input) },
    )
  },
  confirmBrief(projectId: string, revision: number): Promise<ProjectView> {
    const body: ConfirmBriefRequest = { revision }
    return requestJson<ProjectView>(
      `/api/projects/${encodeURIComponent(projectId)}/confirm`,
      { method: 'POST', body: JSON.stringify(body) },
    )
  },
  uploadReference(projectId: string, file: File, expectedRevision: number): Promise<ProjectView> {
    const body = new FormData()
    body.append('file', file)
    body.append('expected_revision', String(expectedRevision))
    return requestJson<ProjectView>(
      `/api/projects/${encodeURIComponent(projectId)}/reference`,
      { method: 'POST', body },
    )
  },
  removeReference(projectId: string, expectedRevision: number): Promise<ProjectView> {
    const query = new URLSearchParams({ expected_revision: String(expectedRevision) })
    return requestJson<ProjectView>(
      `/api/projects/${encodeURIComponent(projectId)}/reference?${query.toString()}`,
      { method: 'DELETE' },
    )
  },
  submitGeneration(
    projectId: string,
    input: SubmitGenerationRequest,
  ): Promise<GenerationSubmitResponse> {
    return requestJson<GenerationSubmitResponse>(
      `/api/projects/${encodeURIComponent(projectId)}/generations`,
      { method: 'POST', body: JSON.stringify(input) },
    )
  },
  getGeneration(
    id: string,
    signal?: AbortSignal,
    polling = false,
  ): Promise<GenerationView> {
    return requestJson<GenerationView>(`/api/generations/${encodeURIComponent(id)}`, {
      signal,
      authActivity: polling ? 'polling' : 'user',
    })
  },
  retrieveGenerationResult(id: string, signal?: AbortSignal): Promise<GenerationView> {
    return requestJson<GenerationView>(`/api/generations/${encodeURIComponent(id)}/retrieve-result`, {
      method: 'POST',
      body: '{}',
      signal,
    })
  },
}
