import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const repoRoot = resolve(__dirname, '..')
const workspaceSource = readFileSync(
  resolve(repoRoot, 'main/src/composables/useWorkspace.ts'),
  'utf8',
)

describe('T18 cleanup regression (issue #130)', () => {
  it('removes the dead legacy window.location.search project helper', () => {
    expect(workspaceSource).not.toContain('currentProjectParam')
    expect(workspaceSource).not.toContain('window.location.search')
  })

  it('keeps route/query project resolution for old read-only links', () => {
    expect(workspaceSource).toContain('function routeProjectId')
    expect(workspaceSource).toContain('query.project')
  })
})
