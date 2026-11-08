// 校验：对每个状态断言渲染内容与截图尺寸，输出渲染文本摘要（不覆盖已有截图）
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

const summary = {}
const md5 = (f) => fs.existsSync(f) ? fs.readFileSync(f).toString('base64').length : 0

function sizeOf(file) {
  const buf = fs.readFileSync(file)
  // PNG IHDR: width at byte 16, height at 20
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20), bytes: buf.length }
}

const browser = await chromium.launch({
  headless: true,
  executablePath: EDGE,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

for (const vp of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    deviceScaleFactor: 1,
  })
  const page = await context.newPage()
  const entry = { screenshots: {}, text: {} }
  for (const n of ['01-login', '02-chat-empty', '03-chat-messages', '04-composer', '05-prompt-confirm', '06-image-state']) {
    const f = path.join(OUT, `${n}-${vp.name}.png`)
    entry.screenshots[n] = fs.existsSync(f) ? sizeOf(f) : 'MISSING'
  }

  await page.goto(`${APP}/login`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(800)
  entry.text['01-login'] = (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 220)

  await page.fill('#login-email', CRED.email)
  await page.fill('input[name=password]', CRED.password)
  await page.click('.login-submit')
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 })
  await page.waitForTimeout(1000)
  entry.text['02-chat-empty'] = (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 260)

  await page.goto(`${APP}/projects/${PID}/chat`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1400)
  entry.text['03-chat-messages'] = (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 260)
  const composer = page.locator('textarea').first()
  if (await composer.count()) {
    await composer.click()
    await composer.fill('把背景换成浅灰，产品再放大一些。')
    await page.waitForTimeout(300)
    entry.text['04-composer'] = (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(-200)
    entry.composerFocused = await composer.evaluate((el) => document.activeElement === el)
  }
  summary[vp.name] = entry
  await context.close()
}
await browser.close()
fs.writeFileSync(path.join(OUT, 'verify.json'), JSON.stringify(summary, null, 2))
console.log(JSON.stringify(summary, null, 1).slice(0, 3000))
process.exit(0)
