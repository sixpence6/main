import { vi } from 'vitest'

export interface RecordedRequest {
  method: string
  url: string
  path: string
  search: string
  headers: Headers
  body: unknown
}

export interface MockResponse {
  status?: number
  body?: unknown
  headers?: Record<string, string>
  delayMs?: number
}

export interface MockNetworkError {
  networkError: string
}

export type MockStep = MockResponse | MockNetworkError
export type RouteHandler = (request: RecordedRequest) => MockStep | Response | Promise<MockStep | Response>

interface Route {
  method: string
  matcher: string | RegExp
  handler: RouteHandler
  sequence?: { steps: MockStep[]; index: number }
}

function isNetworkError(step: MockStep): step is MockNetworkError {
  return typeof step === 'object' && step !== null && 'networkError' in step
}

function parseBody(body: BodyInit | null | undefined): unknown {
  if (body === null || body === undefined) return null
  if (typeof body === 'string') {
    try {
      return JSON.parse(body) as unknown
    } catch {
      return body
    }
  }
  if (body instanceof FormData) {
    const entries: Record<string, unknown> = {}
    for (const [key, value] of body.entries()) {
      entries[key] = value
    }
    return entries
  }
  return body
}

function wait(ms: number, signal: AbortSignal | null | undefined): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = (): void => {
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function responseFrom(step: MockResponse): Response {
  if (step.status === 204) {
    return new Response(null, { status: 204, headers: step.headers })
  }
  const body = typeof step.body === 'string' ? step.body : JSON.stringify(step.body ?? null)
  return new Response(body, {
    status: step.status ?? 200,
    headers: {
      'content-type': 'application/json',
      ...step.headers,
    },
  })
}

/**
 * 测试用的 HTTP 边界。所有测试都通过它观察请求并控制响应，
 * 不替换业务层的提交函数。
 */
export class FetchController {
  readonly requests: RecordedRequest[] = []
  private readonly routes: Route[] = []
  private originalFetch: typeof globalThis.fetch | null = null

  install(): this {
    this.originalFetch = globalThis.fetch
    globalThis.fetch = this.handle.bind(this) as typeof globalThis.fetch
    return this
  }

  route(method: string, matcher: string | RegExp, handler: RouteHandler): this {
    this.routes.push({ method: method.toUpperCase(), matcher, handler })
    return this
  }

  json(method: string, matcher: string | RegExp, body: unknown, status = 200): this {
    return this.route(method, matcher, () => ({ status, body }))
  }

  sequence(method: string, matcher: string | RegExp, steps: MockStep[]): this {
    const route: Route = {
      method: method.toUpperCase(),
      matcher,
      handler: () => {
        const step = route.sequence?.steps[Math.min(route.sequence.index, route.sequence.steps.length - 1)]
        if (!step) throw new Error('No mock response configured')
        route.sequence!.index += 1
        return step
      },
      sequence: { steps, index: 0 },
    }
    this.routes.push(route)
    return this
  }

  networkError(method: string, matcher: string | RegExp, message = 'Failed to fetch'): this {
    return this.route(method, matcher, () => ({ networkError: message }))
  }

  get writes(): RecordedRequest[] {
    return this.requests.filter((request) => request.method !== 'GET' && request.method !== 'HEAD')
  }

  count(method: string, matcher?: string | RegExp): number {
    return this.requests.filter(
      (request) =>
        request.method === method.toUpperCase() &&
        (matcher === undefined || this.matches(request, matcher)),
    ).length
  }

  last(method: string, matcher?: string | RegExp): RecordedRequest | undefined {
    return [...this.requests]
      .reverse()
      .find(
        (request) =>
          request.method === method.toUpperCase() &&
          (matcher === undefined || this.matches(request, matcher)),
      )
  }

  restore(): void {
    if (this.originalFetch) {
      globalThis.fetch = this.originalFetch
      this.originalFetch = null
    }
  }

  private matches(request: RecordedRequest, matcher: string | RegExp): boolean {
    return typeof matcher === 'string' ? request.path === matcher : matcher.test(request.path)
  }

  private async handle(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const url = input instanceof Request ? input.url : String(input)
    const parsed = new URL(url, 'http://localhost')
    const request: RecordedRequest = {
      method: (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase(),
      url,
      path: parsed.pathname,
      search: parsed.search,
      headers: new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined)),
      body: parseBody(init?.body),
    }
    this.requests.push(request)

    const route = [...this.routes]
      .reverse()
      .find((candidate) => candidate.method === request.method && this.matches(request, candidate.matcher))

    if (!route) {
      throw new Error(`Unhandled test request: ${request.method} ${request.path}`)
    }

    const result = await route.handler(request)
    if (result instanceof Response) return result
    if (isNetworkError(result)) throw new TypeError(result.networkError)
    if (result.delayMs !== undefined) {
      await wait(result.delayMs, init?.signal)
    }
    return responseFrom(result)
  }
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

export { vi }
