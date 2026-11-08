import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright-core'

const APP = 'http://127.0.0.1:4173'
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
const errors = []

const browser = await chromium.launch({
  headless: true,
  executablePath: EDGE,
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--font-render-hinting=none'],
})
console.log('[capture6] browser', browser.version())

for (const vp of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    deviceScaleFactor: 1,
  })
  const page = await context.newPage()
  page.on('pageerror', (e) => errors.push(`${vp.name} pageerror: ${e.message}`))
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`${vp.name} console: ${m.text().slice(0, 200)}`)
  })
  try {
    await page.goto(`${APP}/login`, { waitUntil: 'domcontentloaded' })
    await page.fill('#login-email', CRED.email)
    await page.fill('input[name=password]', CRED.password)
    await page.click('.login-submit')
    await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 20000 })
    await page.waitForTimeout(800)

    const detail = await page.evaluate(async (pid) => {
      const r = await fetch(`/api/projects/${pid}`, { credentials: 'same-origin' })
      return r.ok ? r.json() : null
    }, PID)
    const workflow = await page.evaluate(async (pid) => {
      const r = await fetch(`/api/projects/${pid}/workflow`, { credentials: 'same-origin' })
      return r.ok ? r.json() : {}
    }, PID)

    const now = new Date().toISOString()
    const asset = {
      id: 'baseline-asset',
      kind: 'result',
      mime_type: 'image/png',
      width: 1600,
      height: 900,
      byte_size: PNG.length,
      created_at: now,
      availability: 'available',
      review_status: 'pending',
    }
    const generation = {
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
      result_asset: asset,
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
    const version = {
      run_id: 'baseline-run',
      sequence_no: 1,
      requirement_revision: 1,
      status: 'published',
      generation_job_id: 'baseline-generation-1',
      asset_id: 'baseline-asset',
      filename: 'baseline-1600x900.png',
      width: 1600,
      height: 900,
      caption: '秋季新款保温杯主图 · 第 1 版',
      published_at: now,
      brief: detail?.project?.brief ?? null,
      prompt: { positive_prompt: detail?.project?.brief?.prompt ?? '' },
      quality: null,
      can_download: true,
      created_at: now,
    }

    await context.route(`**/api/projects/${PID}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          project: { ...(detail?.project ?? {}), brief_status: 'confirmed', confirmed_revision: 1 },
          messages: detail?.messages ?? [],
          generations: [generation],
        }),
      }),
    )
    await context.route(`**/api/projects/${PID}/workflow`, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...workflow,
          stage: 'published',
          status: 'completed',
          project_revision: 1,
          requirement_revision: 1,
          run_id: 'baseline-run',
          delivery: {
            id: 'baseline-delivery',
            status: 'published',
            asset_id: 'baseline-asset',
            filename: 'baseline-1600x900.png',
            caption: '秋季新款保温杯主图 · 第 1 版',
            published_at: now,
            created_at: now,
          },
          versions: [version],
          task_state: {
            ...(workflow?.task_state ?? {}),
            progress: {
              phase: 'published',
              stage: 'delivery',
              stage_label: '已交付',
              status: 'completed',
              awaiting_user: false,
              terminal: true,
            },
          },
          updated_at: now,
        }),
      }),
    )
    await context.route('**/api/assets/**', (route) =>
      route.fulfill({ status: 200, contentType: 'image/png', body: PNG }),
    )

    await page.goto(`${APP}/projects/${PID}/results`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(2200)
    const file = path.join(OUT, `06-image-state-${vp.name}.png`)
    await page.screenshot({ path: file, fullPage: false })
    const text = (await page.locator('body').innerText()).slice(0, 300)
    console.log('[capture6]', vp.name, 'saved', path.basename(file), '| text:', text.replace(/\s+/g, ' ').slice(0, 120))
  } catch (err) {
    errors.push(`${vp.name} fatal: ${err.message}`)
    console.log('[capture6] ERROR', vp.name, err.message)
  }
  await context.close()
}
await browser.close()
console.log('[capture6] errors:', errors.length)
for (const e of errors) console.log('  -', e.slice(0, 200))
process.exit(0)
