import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const repoRoot = resolve(__dirname, '..')
const srcRoot = resolve(repoRoot, 'main/src')
const tokensPath = resolve(srcRoot, 'styles/tokens.css')
const tokensCss = readFileSync(tokensPath, 'utf8')

const deadComponents = [
  'components/ConversationPanel.vue',
  'components/ComposerPanel.vue',
  'components/BriefCard.vue',
  'components/HistoryPreview.vue',
  'components/ModelStatusDrawer.vue',
  'components/ProjectDrawer.vue',
  'components/ReferenceUploader.vue',
  'components/workflow/WorkflowPanel.vue',
]

const legacyAliases = [
  '--font-family',
  '--text-input',
  '--text-input-line',
  '--text-product',
  '--text-product-line',
  '--text-small',
  '--text-heading',
  '--color-page',
  '--color-panel',
  '--color-panel-soft',
  '--color-glass',
  '--color-text',
  '--color-text-muted',
  '--color-primary',
  '--color-primary-hover',
  '--color-primary-soft',
  '--color-border',
  '--color-border-soft',
  '--color-code',
  '--color-accent',
  '--color-success',
  '--color-warning',
  '--color-danger',
  '--color-info',
  '--color-error',
  '--color-muted',
  '--radius-control',
  '--radius-panel',
  '--radius-card',
  '--radius-image',
  '--shadow-panel',
  '--conversation-width',
]

function readSourceFiles(dir: string): Array<{ path: string; text: string }> {
  const files: Array<{ path: string; text: string }> = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      files.push(...readSourceFiles(full))
      continue
    }
    if (!/\.(vue|css|ts)$/.test(entry)) continue
    files.push({ path: full, text: readFileSync(full, 'utf8') })
  }
  return files
}

const sourceFiles = readSourceFiles(srcRoot)
const nonTokenFiles = sourceFiles.filter((file) => file.path !== tokensPath)

describe('V7 cleanup regression (issue #152)', () => {
  it('dead component chain is absent', () => {
    for (const component of deadComponents) {
      expect(existsSync(resolve(srcRoot, component)), component).toBe(false)
    }
  })

  it('legacy alias declarations are deleted from tokens.css', () => {
    for (const alias of legacyAliases) {
      expect(tokensCss, alias).not.toMatch(new RegExp(`${alias}\\s*:`))
    }
  })

  it('no source file uses a legacy alias', () => {
    for (const file of nonTokenFiles) {
      for (const alias of legacyAliases) {
        expect(file.text, `${relative(repoRoot, file.path)} uses ${alias}`).not.toContain(`var(${alias})`)
      }
    }
  })

  it('useWorkspace no longer owns project drawer state', () => {
    const workspace = readFileSync(resolve(srcRoot, 'composables/useWorkspace.ts'), 'utf8')
    for (const token of ['projectDrawerOpen', 'openProjectDrawer', 'closeProjectDrawer']) {
      expect(workspace, token).not.toContain(token)
    }
  })

  it('keeps six-digit hex colors inside tokens.css only', () => {
    for (const file of nonTokenFiles) {
      expect(file.text, relative(repoRoot, file.path)).not.toMatch(/#[0-9a-fA-F]{6}\b/)
    }
  })

  it('does not remove focus outlines', () => {
    for (const file of sourceFiles) {
      expect(file.text, relative(repoRoot, file.path)).not.toMatch(/outline\s*:\s*(?:0|none)\b/)
    }
  })

  it('does not use font sizes below 12px', () => {
    for (const file of sourceFiles) {
      for (const match of file.text.matchAll(/font-size\s*:\s*(\d+(?:\.\d+)?)px/g)) {
        expect(Number(match[1]), `${relative(repoRoot, file.path)}: ${match[0]}`).toBeGreaterThanOrEqual(12)
      }
    }
  })

  it('defines every custom property it consumes', () => {
    const defined = new Set<string>()
    for (const file of sourceFiles) {
      for (const match of file.text.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) defined.add(match[1]!)
    }
    for (const file of sourceFiles) {
      for (const match of file.text.matchAll(/var\(\s*(--[a-zA-Z0-9-]+)\s*[,)]/g)) {
        expect(defined.has(match[1]!), `${relative(repoRoot, file.path)} uses undefined ${match[1]}`).toBe(true)
      }
    }
  })

  it('keeps legacy --color-* aliases out of component styles', () => {
    for (const file of nonTokenFiles) {
      expect(file.text, relative(repoRoot, file.path)).not.toContain('var(--color-')
    }
  })
})
