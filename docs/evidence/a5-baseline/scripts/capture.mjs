import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright-core'

const APP = 'http://127.0.0.1:5173'
const PID = 'fd12830f-9858-4dd3-a1d9-e53935f5c138'
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const OUT = path.resolve('.tmp/wayfinder/evidence/a5')
const PNG = fs.readFileSync(path.resolve('.tmp/a5/fixtures/result-placeholder.png'))
const CRED = { email: 'baseline@example.test', password: 'Wayfinder-2026-baseline' }
const VIEWPORTS = [
  { name: '320', width: 320, height: 800 },
  { name: '768', width: 768, height: 1024 },
  { name: '1440', width: 1440, height: 1000 },
]
fs.mkdirSync(OUT, { recursive: true })

const report = { startedAt: new Date().toISOString(), states: {}, metrics: {}, errors: [] }

function log(...args) {
  console.log('[capture]', ...args)
}

async function settle(page, ms = 700) {
  await page.waitForLoadState('networkidle').catch(() => {})
  await page.waitForTimeout(ms)
}

async function shot(page, name) {
  const file = path.join(OUT, `${name}.png`)
  await page.screenshot({ path: file, fullPage: false })
  log('screenshot', path.relative(process.cwd(), file))
  return file
}

const STYLE_PROPS = ['fontFamily', 'fontSize', 'fontWeight', 'color', 'backgroundColor', 'borderRadius', 'paddingTop', 'paddingLeft', 'lineHeight', 'letterSpacing']

async function collectMetrics(page, viewport) {
  return page.evaluate((props) => {
    const agg = (key) => {
      const counts = new Map()
      for (const el of document.querySelectorAll('*')) {
        const v = getComputedStyle(el)[key]
        if (!v || v === 'none' || v === 'auto') continue
        counts.set(v, (counts.get(v) ?? 0) + 1)
      }
      return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(([value, count]) => ({ value, count }))
    }
    const probe = (selector) => {
      const el = document.querySelector(selector)
      if (!el) return null
      const cs = getComputedStyle(el)
      const out = { selector, tag: el.tagName.toLowerCase(), class: el.className?.toString().slice(0, 80) }
      for (const p of props) out[p] = cs[p]
      const rect = el.getBoundingClientRect()
      out.box = { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) }
      return out
    }
    return {
      viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
      overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      mediaQueries: {
        max767: matchMedia('(max-width: 767px)').matches,
        min768: matchMedia('(min-width: 768px)').matches,
        min1024: matchMedia('(min-width: 1024px)').matches,
      },
      fontFaces: [...document.fonts].map((f) => `${f.family} ${f.weight} ${f.style} ${f.status}`),
      distinct: {
        fontFamily: agg('fontFamily'),
        fontSize: agg('fontSize'),
        fontWeight: agg('fontWeight'),
        borderRadius: agg('borderRadius'),
        color: agg('color'),
        backgroundColor: agg('backgroundColor'),
      },
      probes: {
        body: probe('body'),
        sidebar: probe('aside, .app-sidebar, .sidebar, nav'),
        header: probe('header, .topbar, .app-header'),
        composer: probe('textarea, .composer, form.composer'),
        message: probe('.message, .bubble, [class*=message]'),
        button: probe('button'),
      },
    }
  }, STYLE_PROPS).then((m) => ({ ...m, viewportName: viewport.name }))
}

async function buildFixtures(page) {
  const detail = await page.evaluate(async (pid) => {
    const r = await fetch(`/api/projects/${pid}`, { credentials: 'same-origin' })
    return r.ok ? r.json() : null
  }, PID)
  return detail
}

function generationFixture(detail) {
  const now = new Date().toISOString()
  return {
    id: 'baseline-generation-1',
    project_id: PID,
    version_no: 1,
    request_id: 'baseline-request-1',
    confirmed_revision: 1,
    status: 'succeeded',
    stage: 'completed',
    brief_snapshot: detail?.project?.brief ?? null,
    prompt: detail?.project?.brief?.prompt ?? '高端电商产品主图基线样本',
    reference_asset_id: null,
    adapter_id: 'openai-images',
    model: 'gpt-image-2-4k',
    remote_task_id: null,
    result_asset_id: 'baseline-asset',
    result_asset: {
      id: 'baseline-asset',
      kind: 'result',
      mime_type: 'image/png',
      width: 1600,
      height: 900,
      byte_size: PNG.length,
      created_at: now,
      availability: 'available',
      review_status: 'pending',
    },
    error: null,
    can_retrieve_result: true,
    retrieval_unavailable_reason: null,
    retention_status: 'active',
    retention_expires_at: null,
    legacy_unreviewed: false,
    legacy_notice: null,
    created_at: now,
    started_at: now,
    completed_at: now,
  }
}

function workflowFixture(base, detail) {
  const now = new Date().toISOString()
  const prompt = detail?.project?.brief?.prompt ?? ''
  return {
    ...base,
    stage: 'reviewing',
    status: 'running',
    requirement_revision: 1,
    project_revision: 1,
    prompt_preview: {
      id: 'baseline-prompt-preview',
      status: 'reviewing',
      request_digest: 'baseline-digest',
      request: { prompt, image_size: '3840x2160', model: 'gpt-image-2-4k' },
      created_at: now,
      can_confirm: true,
      command_context: {},
    },
    available_actions: [
      {
        id: 'confirm_prompt',
        label: '确认提示词并生成',
        kind: 'confirm',
        requires_text: false,
        text_label: null,
        requires_confirmation: true,
        effect: 'model_call',
        read_only: false,
        requires_model_call: true,
        retry_mode: 'none',
        automatic_retry: false,
      },
    ],
    task_state: {
      ...(base?.task_state ?? {}),
      progress: {
        phase: 'reviewing',
        stage: 'prompt_review',
        stage_label: '提示词确认',
        status: 'running',
        awaiting_user: true,
        terminal: false,
      },
      resource_state: { current_run_id: 'baseline-run', current_state: 'reviewing', current_version: null, history: [] },
    },
    updated_at: now,
  }
}

const browser = await chromium.launch({
  headless: true,
  executablePath: EDGE,
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none'],
})
report.browserVersion = browser.version()
log('browser', report.browserVersion)

for (const vp of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    deviceScaleFactor: 1,
    reducedMotion: 'no-preference',
  })
  const page = await context.newPage()
  page.on('pageerror', (e) => report.errors.push(`${vp.name} pageerror: ${e.message}`))
  page.on('console', (m) => {
    if (m.type() === 'error') report.errors.push(`${vp.name} console: ${m.text().slice(0, 200)}`)
  })

  const states = {}
  try {
    // state 1: login
    await page.goto(`${APP}/login`, { waitUntil: 'domcontentloaded' })
    await settle(page, 900)
    states.login = await shot(page, `01-login-${vp.name}`)
    report.metrics[`login-${vp.name}`] = await collectMetrics(page, vp)

    // login through the real backend
    await page.fill('#login-email', CRED.email)
    await page.fill('input[name=password]', CRED.password)
    await page.click('.login-submit')
    await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 20000 })
    await settle(page, 1200)

    // state 2: chat empty (real, account has a single project; sidebar collapsed view)
    states.chatEmpty = await shot(page, `02-chat-empty-${vp.name}`)

    // state 3: real message stream
    await page.goto(`${APP}/projects/${PID}/chat`, { waitUntil: 'domcontentloaded' })
    await settle(page, 1500)
    const detail = await buildFixtures(page)
    report.metrics[`chat-${vp.name}`] = await collectMetrics(page, vp)
    states.chatMessages = await shot(page, `03-chat-messages-${vp.name}`)

    // state 4: composer focus with draft text
    const composer = page.locator('textarea').first()
    if (await composer.count()) {
      await composer.click()
      await composer.fill('把背景换成浅灰，产品再放大一些。')
      await page.waitForTimeout(400)
      states.composer = await shot(page, `04-composer-${vp.name}`)
    }

    // state 5: prompt confirmation (workflow payload fixture at the network edge)
    const baseWorkflow = await page.evaluate(async (pid) => {
      const r = await fetch(`/api/projects/${pid}/workflow`, { credentials: 'same-origin' })
      return r.ok ? r.json() : {}
    }, PID)
    await context.route(`**/api/projects/${PID}/workflow`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(workflowFixture(baseWorkflow, detail)) }),
    )
    await page.reload({ waitUntil: 'domcontentloaded' })
    await settle(page, 1500)
    states.promptConfirm = await shot(page, `05-prompt-confirm-${vp.name}`)
    states.promptConfirmText = (await page.locator('body').innerText()).slice(0, 400)

    // state 6: image state (detail payload fixture + local placeholder asset)
    const withGeneration = {
      project: { ...(detail?.project ?? {}), brief_status: 'confirmed', confirmed_revision: 1 },
      messages: detail?.messages ?? [],
      generations: [generationFixture(detail)],
    }
    await context.route(`**/api/projects/${PID}`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(withGeneration) }),
    )
    await context.route('**/api/assets/**', (route) =>
      route.fulfill({ status: 200, contentType: 'image/png', body: PNG }),
    )
    await page.reload({ waitUntil: 'domcontentloaded' })
    await settle(page, 1800)
    states.image = await shot(page, `06-image-state-${vp.name}`)
  } catch (err) {
    report.errors.push(`${vp.name} fatal: ${err.message}`)
    log('ERROR', vp.name, err.message)
  }
  report.states[`viewport-${vp.name}`] = states
  await context.close()
}

await browser.close()
report.finishedAt = new Date().toISOString()
fs.writeFileSync(path.join(OUT, 'capture-report.json'), JSON.stringify(report, null, 2))
log('done', report.errors.length, 'errors')
process.exit(0)
