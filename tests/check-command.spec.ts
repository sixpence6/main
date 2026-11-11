import { spawnSync } from 'node:child_process'
import { describe, expect, it } from 'vitest'

const npm = process.platform === 'win32' ? (process.env.ComSpec ?? 'cmd.exe') : 'npm'
const npmArgs =
  process.platform === 'win32'
    ? ['/d', '/s', '/c', 'npm run check -- --dry-run']
    : ['run', 'check', '--', '--dry-run']

describe('npm run check', () => {
  it('prints the check plan without running the stages', () => {
    const result = spawnSync(npm, npmArgs, {
      cwd: process.cwd(),
      encoding: 'utf8',
    })

    expect(result.status).toBe(0)
    expect(result.stdout).toContain('dependency graph')
    expect(result.stdout).toContain('typecheck')
    expect(result.stdout).toContain('frontend tests')
    expect(result.stdout).toContain('backend lint')
    expect(result.stdout).toContain('backend tests')
  })
})
