import { readonly, shallowRef } from 'vue'

export type LiveStatusTone = 'idle' | 'working' | 'waiting' | 'success' | 'error'

export interface LiveStatusInput {
  projectId: string | null
  projectTitle: string
  label: string
  detail: string
  tone: LiveStatusTone
  busy: boolean
}

export interface LiveStatusSnapshot extends LiveStatusInput {
  updatedAt: number
}

const status = shallowRef<LiveStatusSnapshot | null>(null)

function sameStatus(
  left: LiveStatusInput | LiveStatusSnapshot | null,
  right: LiveStatusInput,
): boolean {
  return Boolean(
    left &&
      left.projectId === right.projectId &&
      left.projectTitle === right.projectTitle &&
      left.label === right.label &&
      left.detail === right.detail &&
      left.tone === right.tone &&
      left.busy === right.busy,
  )
}

/**
 * The active page publishes its already-authoritative workflow projection here.
 * The capsule is a pure consumer, so it never starts a second poll or request.
 */
export function publishLiveStatus(next: LiveStatusInput): void {
  if (sameStatus(status.value, next)) return
  status.value = {
    ...next,
    updatedAt: Date.now(),
  }
}

export function clearLiveStatus(projectId?: string | null): void {
  if (projectId && status.value?.projectId !== projectId) return
  status.value = null
}

export function useLiveStatus() {
  return {
    status: readonly(status),
  }
}
