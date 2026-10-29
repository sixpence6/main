import type {
  ApiErrorBody,
  ConfigStatus,
  HealthStatus,
  ProjectDetailResponse,
  ProjectListResponse,
} from './types'

const JSON_HEADERS = { Accept: 'application/json' }

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

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(JSON_HEADERS)
  if (init.headers) {
    new Headers(init.headers).forEach((value, key) => headers.set(key, value))
  }
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
  } catch (error) {
    if (isAbortError(error)) throw error
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接本机服务，请确认服务已启动。')
  }

  if (!response.ok) {
    const body = await readErrorBody(response)
    const message = safeServerMessage(body?.error?.message) ?? defaultMessage(response.status)
    throw new ApiError(response.status, body?.error?.code ?? 'HTTP_ERROR', message)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export interface ListProjectsParams {
  limit?: number
  cursor?: string | null
}

export function assetUrl(id: string): string {
  return `/api/assets/${encodeURIComponent(id)}`
}

export const api = {
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
    return requestJson<ProjectListResponse>(`/api/projects?${query.toString()}`, { signal })
  },
  getProject(id: string, signal?: AbortSignal): Promise<ProjectDetailResponse> {
    return requestJson<ProjectDetailResponse>(`/api/projects/${encodeURIComponent(id)}`, { signal })
  },
}
