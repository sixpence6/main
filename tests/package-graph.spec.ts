import { existsSync, readFileSync, realpathSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const repoRoot = process.cwd()

function readJson(relativePath: string) {
  return JSON.parse(readFileSync(path.join(repoRoot, relativePath), 'utf8')) as Record<
    string,
    unknown
  >
}

function vueRouterInstallations(): string[] {
  return [
    path.join(repoRoot, 'node_modules', 'vue-router'),
    path.join(repoRoot, 'main', 'node_modules', 'vue-router'),
  ]
    .filter((candidate) => existsSync(candidate))
    .map((candidate) => realpathSync(candidate))
}

describe('frontend package graph', () => {
  it('resolves vue-router to a single physical installation', () => {
    const installations = [...new Set(vueRouterInstallations())]

    expect(installations).toHaveLength(1)
  })

  it('keeps dependency declarations in the main workspace only', () => {
    const rootManifest = readJson('package.json')
    const mainManifest = readJson('main/package.json')
    const rootLock = readJson('package-lock.json')

    expect(rootManifest.workspaces).toEqual(['main'])
    expect(rootManifest.dependencies ?? {}).toEqual({})
    expect(rootManifest.devDependencies ?? {}).toEqual({})
    expect(mainManifest.dependencies).toBeTruthy()
    expect(existsSync(path.join(repoRoot, 'main', 'package-lock.json'))).toBe(false)

    const lockPackages = rootLock.packages as Record<string, { name?: string }>
    expect(lockPackages.main?.name).toBe(mainManifest.name)
  })
})
