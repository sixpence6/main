import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const repoRoot = resolve(__dirname, '..')
const srcRoot = resolve(repoRoot, 'main/src')
const tokensPath = resolve(srcRoot, 'styles/tokens.css')
const tokensCss = readFileSync(tokensPath, 'utf8')
const baseCss = readFileSync(resolve(srcRoot, 'styles/base.css'), 'utf8')
const workspaceCss = readFileSync(resolve(srcRoot, 'styles/workspace.css'), 'utf8')

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

function declaredTokens(css: string): Set<string> {
  const names = new Set<string>()
  for (const match of css.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) names.add(match[1]!)
  return names
}

const definedTokens = declaredTokens(tokensCss)

describe('V1 tokens.css contract (Appendix A)', () => {
  const expected: Record<string, string> = {
    '--surface-canvas': '#f6f4ef',
    '--surface-panel': '#fcfbf7',
    '--surface-inset': '#efede6',
    '--surface-media': '#e9e6de',
    '--surface-shell': '#17191d',
    '--surface-shell-raised': '#1e2126',
    '--surface-shell-hover': '#202329',
    '--surface-shell-active': '#24272d',
    '--line-hairline': '#dfddd5',
    '--line-hover': '#c9c8c2',
    '--line-control': '#8f8c84',
    '--line-inverse': '#2c2f35',
    '--line-inverse-control': '#6b7077',
    '--text-primary': '#1b1d21',
    '--text-secondary': '#4a4f57',
    '--text-muted': '#636870',
    '--text-inverse-primary': '#f4f1e9',
    '--text-inverse-secondary': '#c7cbd1',
    '--text-inverse-muted': '#a9adb4',
    '--text-inverse-meta': '#8c9198',
    '--action-primary': '#1b1d21',
    '--action-primary-hover': '#2c3037',
    '--action-primary-text': '#f4f1e9',
    '--action-soft': '#eceae3',
    '--action-link': '#1e5a73',
    '--focus-ring-light': '#1e5a73',
    '--focus-ring-dark': '#cfe6f2',
    '--state-success': '#35745B',
    '--state-warning': '#8A611E',
    '--state-danger': '#B23B4B',
    '--state-info': '#4965A8',
    '--text-caption': '12px',
    '--text-caption-line': '18px',
    '--text-body': '14px',
    '--text-body-line': '22px',
    '--text-reading': '15px',
    '--text-reading-line': '25px',
    '--text-section': '17px',
    '--text-section-line': '24px',
    '--text-page': '20px',
    '--text-page-line': '28px',
    '--space-1': '4px',
    '--space-2': '8px',
    '--space-3': '12px',
    '--space-4': '16px',
    '--space-5': '20px',
    '--space-6': '24px',
    '--space-8': '32px',
    '--space-10': '40px',
    '--space-12': '48px',
    '--space-16': '64px',
    '--radius-sm': '8px',
    '--radius-md': '14px',
    '--radius-lg': '19px',
    '--radius-pill': '999px',
    '--dur-fast': '120ms',
    '--dur-base': '180ms',
    '--dur-slow': '240ms',
    '--icon-sm': '16px',
    '--icon-md': '20px',
    '--icon-lg': '24px',
    '--sidebar-width': '264px',
    '--topbar-height': '48px',
    '--conversation-max': '760px',
    '--results-max': '1120px',
    '--shell-pad-x': '16px',
  }

  for (const [name, value] of Object.entries(expected)) {
    it(`${name} 的值为 ${value}`, () => {
      expect(declaredTokens(tokensCss).has(name)).toBe(true)
      expect(tokensCss).toMatch(new RegExp(`${name}\\s*:\\s*${value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*;`))
    })
  }

  it('使用系统字体栈与完全一致的缓动/阴影令牌', () => {
    expect(tokensCss).toContain('--font-sans: system-ui, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;')
    expect(tokensCss).toContain('--font-mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;')
    expect(tokensCss).toContain('--ease-out: cubic-bezier(0.22, 0.8, 0.3, 1);')
    expect(tokensCss).toContain('--shadow-composer: 0 10px 30px rgba(23, 25, 29, 0.06);')
    expect(tokensCss).toContain('--shadow-overlay: 0 24px 64px rgba(12, 14, 17, 0.24);')
    expect(tokensCss).toMatch(/--text-display:\s*clamp\(26px, 3\.2vw, 40px\);/)
    expect(tokensCss).toMatch(/--text-display-line:\s*1\.15;/)
  })

  it('保留 V7 兼容别名', () => {
    const aliases: Record<string, string> = {
      '--font-family': 'var(--font-sans)',
      '--text-input': 'var(--text-reading)',
      '--text-input-line': 'var(--text-reading-line)',
      '--text-product': 'var(--text-page)',
      '--text-product-line': 'var(--text-page-line)',
      '--text-small': 'var(--text-caption)',
      '--text-heading': 'var(--text-section)',
      '--color-page': 'var(--surface-canvas)',
      '--color-panel': 'var(--surface-panel)',
      '--color-panel-soft': 'var(--surface-inset)',
      '--color-glass': 'var(--surface-glass)',
      '--color-text': 'var(--text-primary)',
      '--color-text-muted': 'var(--text-muted)',
      '--color-primary': 'var(--action-primary)',
      '--color-primary-hover': 'var(--action-primary-hover)',
      '--color-primary-soft': 'var(--action-soft)',
      '--color-border': 'var(--line-hairline)',
      '--color-border-soft': 'var(--line-hairline)',
      '--color-code': 'var(--surface-inset)',
      '--color-accent': 'var(--action-link)',
      '--color-success': 'var(--state-success)',
      '--color-warning': 'var(--state-warning)',
      '--color-danger': 'var(--state-danger)',
      '--color-info': 'var(--state-info)',
      '--color-error': 'var(--state-danger)',
      '--color-muted': 'var(--text-muted)',
      '--radius-control': 'var(--radius-sm)',
      '--radius-panel': 'var(--radius-md)',
      '--radius-card': 'var(--radius-md)',
      '--radius-image': 'var(--radius-sm)',
      '--shadow-panel': 'var(--shadow-composer)',
      '--conversation-width': 'var(--conversation-max)',
    }
    for (const [name, value] of Object.entries(aliases)) {
      expect(tokensCss, `${name} alias`).toMatch(
        new RegExp(`${name}\\s*:\\s*${value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*;`),
      )
    }
  })

  it('删除已淘汰令牌并保留响应式覆盖', () => {
    for (const retired of ['--ease-spring', '--radius-shell', '--body-padding', '--head-height']) {
      expect(tokensCss.includes(`${retired}:`), retired).toBe(false)
    }
    expect(tokensCss).toMatch(/@media \(min-width: 768px\)[\s\S]*--shell-pad-x:\s*24px/)
    expect(tokensCss).toMatch(/@media \(min-width: 1280px\)[\s\S]*--shell-pad-x:\s*32px/)
    expect(tokensCss).toMatch(/@media \(max-width: 767px\)[\s\S]*--topbar-height:\s*44px/)
  })
})

describe('V1 全局令牌与样式纪律', () => {
  it('base.css 使用新表面/文字令牌与全局焦点环', () => {
    expect(baseCss).toMatch(/background:\s*var\(--surface-canvas\)/)
    expect(baseCss).toMatch(/color:\s*var\(--text-primary\)/)
    expect(baseCss).toMatch(/font-family:\s*var\(--font-sans\)/)
    expect(baseCss).toMatch(/font-size:\s*var\(--text-body\)/)
    expect(baseCss).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--focus-ring-light\)/)
    expect(baseCss).toMatch(/:focus-visible\s*\{[^}]*outline-offset:\s*2px/)
    expect(baseCss).toMatch(/prefers-reduced-motion/)
  })

  it('暗色壳层覆盖焦点环颜色', () => {
    const darkFocus = /\.app-sidebar[^{}]*:focus-visible|\.workspace-topbar[^{}]*:focus-visible/
    expect(darkFocus.test(baseCss) || darkFocus.test(workspaceCss)).toBe(true)
    expect(baseCss.includes('--focus-ring-dark') || workspaceCss.includes('--focus-ring-dark')).toBe(true)
  })

  it('tokens.css 之外没有 var(--color-*)、六位十六进制、color: rgba、outline:0/none、低于 12px 的字号', () => {
    const offenders: string[] = []
    for (const file of nonTokenFiles) {
      const label = relative(repoRoot, file.path).replace(/\\/g, '/')
      const lines = file.text.split('\n')
      lines.forEach((line, index) => {
        const where = `${label}:${index + 1}`
        if (/var\(--color-/.test(line)) offenders.push(`${where} var(--color-*): ${line.trim()}`)
        if (/#[0-9a-fA-F]{6}\b/.test(line)) offenders.push(`${where} hex: ${line.trim()}`)
        if (/\bcolor:\s*rgba\(/.test(line)) offenders.push(`${where} rgba text: ${line.trim()}`)
        if (/outline:\s*(0|none)\b/.test(line)) offenders.push(`${where} outline none: ${line.trim()}`)
        if (/font-size:\s*(8|9|10|11)px\b/.test(line)) offenders.push(`${where} small font: ${line.trim()}`)
      })
    }
    expect(offenders).toEqual([])
  })

  it('所有 var(--x) 均在 tokens.css 中定义（或属于已知动态例外）', () => {
    const dynamicExceptions = new Set<string>([])
    const undefinedUses: string[] = []
    for (const file of nonTokenFiles) {
      const label = relative(repoRoot, file.path).replace(/\\/g, '/')
      const lines = file.text.split('\n')
      lines.forEach((line, index) => {
        for (const match of line.matchAll(/var\(\s*(--[a-zA-Z0-9-]+)/g)) {
          const name = match[1]!
          if (!definedTokens.has(name) && !dynamicExceptions.has(name)) {
            undefinedUses.push(`${label}:${index + 1} ${name}`)
          }
        }
      })
    }
    expect(undefinedUses).toEqual([])
  })
})

describe('V1 壳体、侧栏、顶栏与状态胶囊结构', () => {
  const layout = readFileSync(resolve(srcRoot, 'layouts/WorkspaceLayout.vue'), 'utf8')
  const sidebar = readFileSync(resolve(srcRoot, 'components/AppSidebar.vue'), 'utf8')
  const capsule = readFileSync(resolve(srcRoot, 'components/CurrentStatusCapsule.vue'), 'utf8')
  const chatPage = readFileSync(resolve(srcRoot, 'pages/ChatPage.vue'), 'utf8')
  const resultsPage = readFileSync(resolve(srcRoot, 'pages/ResultsPage.vue'), 'utf8')

  it('WorkspaceLayout 只有一个壳层顶栏，删除移动双顶栏', () => {
    expect((layout.match(/class="workspace-topbar"/g) ?? []).length).toBe(1)
    expect(layout.includes('mobile-bar')).toBe(false)
    expect(layout.includes('chat-topbar')).toBe(false)
    expect(layout).not.toMatch(/padding:\s*12px/)
    expect(layout).toMatch(/<CurrentStatusCapsule[^>]*variant="topbar"/)
    expect(layout).toMatch(/<CurrentStatusCapsule[^>]*variant="inline"/)
    expect(layout).toMatch(/--topbar-height/)
    expect(layout).toMatch(/--surface-shell/)
    expect(layout).toMatch(/--shell-pad-x/)
  })

  it('移动端状态胶囊在非对话工作区路由也进入文档流', () => {
    expect(layout).toMatch(/<div v-if="isMobile" class="capsule-inline">\s*<CurrentStatusCapsule\s+variant="inline"\s*\/>/)
    const inlineIndex = layout.indexOf('<div v-if="isMobile" class="capsule-inline">')
    expect(inlineIndex).toBeGreaterThan(layout.indexOf('class="workspace-topbar"'))
    expect(inlineIndex).toBeLessThan(layout.indexOf('class="main-panel"'))
  })

  it('ChatPage 不再自带旧顶栏', () => {
    expect(chatPage.includes('chat-topbar')).toBe(false)
    expect(chatPage.includes('chat-heading')).toBe(false)
    expect(chatPage.includes('chat-eyebrow')).toBe(false)
  })

  it('AppSidebar 使用暗色壳层、36px 会话行和选中左侧条', () => {
    expect(sidebar).toMatch(/background:\s*var\(--surface-shell\)/)
    expect(sidebar).toMatch(/height:\s*36px/)
    expect(sidebar).toMatch(/var\(--sidebar-width\)/)
    expect(sidebar).toMatch(
      /\.conversation-select\[aria-current='page'\]::before\s*\{[^}]*width:\s*2px;[^}]*background:\s*var\(--text-inverse-secondary\)/,
    )
    expect(sidebar).toMatch(/left:\s*4px/)
    expect(sidebar).toMatch(/var\(--surface-shell-active\)/)
  })

  it('CurrentStatusCapsule 是无交互的单一 live region，24px 高且无动画装饰', () => {
    expect(capsule).toMatch(/role="status"/)
    expect(capsule).toMatch(/aria-live="polite"/)
    expect(capsule.includes('linear-gradient')).toBe(false)
    expect(capsule.includes('status-breathe')).toBe(false)
    expect(capsule.includes('status-spin')).toBe(false)
    expect(capsule).toMatch(/<div[^>]*class="status-capsule"/)
    expect(capsule).not.toMatch(/<button[^>]*class="status-capsule"/)
    expect(capsule).toMatch(/height:\s*24px/)
    expect(capsule).toMatch(/--text-inverse-secondary/)
    expect(capsule).toMatch(/--surface-shell-active/)
    expect(capsule).toMatch(/--line-inverse/)
  })

  it('ResultsPage 只使用已定义令牌', () => {
    expect(resultsPage.includes('--space-5')).toBe(true)
    expect(resultsPage.includes('--text-small')).toBe(false)
    expect(resultsPage.includes('--text-heading')).toBe(false)
  })
})
