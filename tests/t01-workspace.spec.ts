import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { configuredConfig, emptyConfig, unknownCapabilitiesConfig } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

describe('T01 空配置工作台', () => {
  it('打开无项目工作台时显示用途与示例，点击示例只填入输入框且没有写请求', async () => {
    harness = await mountWorkspace({ config: emptyConfig() })

    const header = screen.getByRole('banner')
    expect(header).toHaveTextContent('电商生图工作台')
    expect(within(header).queryAllByRole('button')).toHaveLength(0)

    expect(screen.getByText('从一个商品想法开始')).toBeVisible()
    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    const example = screen.getByRole('button', { name: '米色亚麻托特包，简洁主图' })

    await harness.user.click(example)

    expect(textarea).toHaveValue('米色亚麻托特包，简洁主图')
    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('GET', '/api/config/status')).toBe(1)
  })

  it('新建项目清空草稿并移除项目参数，不读取或创建项目', async () => {
    harness = await mountWorkspace({ config: emptyConfig() })

    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '一段尚未发送的草稿')
    await harness.user.click(screen.getByRole('button', { name: '新建项目' }))
    const dialog = await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })
    expect(within(dialog).getByText('输入框中尚未发送的文字')).toBeVisible()
    await harness.user.click(within(dialog).getByRole('button', { name: '放弃修改' }))

    expect(textarea).toHaveValue('')
    expect(window.location.search).toBe('')
    expect(harness.fetch.count('GET', /^\/api\/projects/)).toBe(0)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('Head 仅显示产品名，旧阶段导航已移除且操作不触发生成请求', async () => {
    harness = await mountWorkspace({ config: emptyConfig() })

    const header = screen.getByRole('banner')
    expect(header.textContent?.trim()).toBe('电商生图工作台')
    expect(within(header).queryByRole('navigation')).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: '工作阶段' })).toBeNull()

    const textarea = screen.getByRole('textbox', { name: '描述商品与图片需求' })
    await harness.user.type(textarea, '仍留在当前工作台')
    await harness.user.keyboard('{Enter}')

    expect(harness.fetch.writes).toHaveLength(0)
    expect(harness.fetch.count('POST', /\/generations$/)).toBe(0)
  })
})

async function openModelDrawer(h: WorkspaceHarness) {
  await h.user.click(screen.getByRole('button', { name: '模型状态' }))
  return screen.findByRole('dialog', { name: '模型状态' })
}

describe('T01 只读模型状态抽屉', () => {
  it('空配置分别展示 LLM 与 VISUAL 的缺失字段，并始终提示尚未验证实际调用', async () => {
    harness = await mountWorkspace({ config: emptyConfig() })
    const dialog = await openModelDrawer(harness)

    const llm = within(dialog).getByRole('region', { name: '需求整理模型（LLM）' })
    expect(within(llm).getByText('配置不完整')).toBeVisible()
    expect(within(llm).getByText('LLM_API_KEY')).toBeVisible()
    expect(within(llm).getByText('LLM_MODEL')).toBeVisible()
    expect(within(llm).getByText(/尚未验证实际调用/)).toBeVisible()

    const visual = within(dialog).getByRole('region', { name: '图片生成模型（VISUAL）' })
    expect(within(visual).getByText('VISUAL_API_KEY')).toBeVisible()
    expect(within(visual).getByText('VISUAL_MODEL')).toBeVisible()
    expect(within(visual).getByText(/尚未验证实际调用/)).toBeVisible()

    expect(dialog).not.toHaveTextContent('认证成功')
    expect(dialog).not.toHaveTextContent('连接成功')
    expect(dialog).not.toHaveTextContent('已验证')
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('未知能力显示待核实或待配置，配置完整仍只显示尚未验证实际调用', async () => {
    harness = await mountWorkspace({ config: unknownCapabilitiesConfig() })
    const dialog = await openModelDrawer(harness)

    const llm = within(dialog).getByRole('region', { name: '需求整理模型（LLM）' })
    const visual = within(dialog).getByRole('region', { name: '图片生成模型（VISUAL）' })
    expect(within(llm).getByText('配置完整')).toBeVisible()
    expect(within(visual).getByText('配置完整')).toBeVisible()
    expect(within(llm).getByText(/尚未验证实际调用/)).toBeVisible()
    expect(within(visual).getByText(/尚未验证实际调用/)).toBeVisible()

    expect(within(visual).getAllByText('待核实').length).toBeGreaterThanOrEqual(3)
    expect(within(visual).getAllByText('待配置').length).toBeGreaterThanOrEqual(4)
    expect(within(visual).queryByText('1024x1024')).not.toBeInTheDocument()
    expect(dialog).not.toHaveTextContent('认证成功')
    expect(dialog).not.toHaveTextContent('连接成功')
  })

  it('已配置能力展示真实字段与秒/字节单位，不编造缺失值', async () => {
    harness = await mountWorkspace({ config: configuredConfig() })
    const dialog = await openModelDrawer(harness)

    const llm = within(dialog).getByRole('region', { name: '需求整理模型（LLM）' })
    const visual = within(dialog).getByRole('region', { name: '图片生成模型（VISUAL）' })
    expect(within(llm).getByText('90 秒')).toBeVisible()
    expect(within(visual).getByText('180 秒')).toBeVisible()
    expect(within(visual).getByText('1024x1024')).toBeVisible()
    expect(within(visual).getByText('image/png、image/jpeg')).toBeVisible()
    expect(within(visual).getAllByText('5,000,000 字节').length).toBeGreaterThanOrEqual(2)
    expect(within(visual).queryByText('待核实')).not.toBeInTheDocument()
  })

  it('读取失败显示安全错误，关闭重开仍保留，刷新只发 GET 且成功后更新', async () => {
    harness = await mountWorkspace({
      configResponses: [
        {
          status: 503,
          body: {
            error: {
              code: 'CONFIG_READ_FAILED',
              message: 'sk-secret Bearer raw-vendor-response stack trace <script>alert(1)</script>',
            },
          },
        },
        { body: emptyConfig() },
      ],
    })

    await harness.waitFor(() => expect(harness!.fetch.count('GET', '/api/config/status')).toBe(1))
    await harness.waitFor(() => expect(screen.getByRole('button', { name: '模型状态' })).toHaveTextContent('模型状态：读取失败'))
    expect(screen.getAllByText('模型配置读取失败，请确认后端服务已启动后重试。').length).toBeGreaterThan(0)
    expect(screen.queryByText('正在读取模型配置，请稍候。')).not.toBeInTheDocument()
    const dialog = await openModelDrawer(harness)
    let alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent('配置状态读取失败')
    expect(alert).not.toHaveTextContent('sk-secret')
    expect(alert).not.toHaveTextContent('Bearer')
    expect(alert).not.toHaveTextContent('raw-vendor-response')
    expect(alert).not.toHaveTextContent('stack')

    await harness.user.click(within(dialog).getByRole('button', { name: '关闭模型状态' }))
    await harness.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const reopened = await openModelDrawer(harness)
    alert = await within(reopened).findByRole('alert')
    expect(alert).toHaveTextContent('配置状态读取失败')

    await harness.user.click(within(reopened).getByRole('button', { name: '刷新配置状态' }))
    await within(reopened).findByText('LLM_API_KEY')

    expect(within(reopened).queryByRole('alert')).not.toBeInTheDocument()
    expect(harness.fetch.count('GET', '/api/config/status')).toBe(2)
    expect(harness.fetch.writes).toHaveLength(0)
  })

  it('配置读取失败后返回页面会自动重试并恢复状态', async () => {
    harness = await mountWorkspace({
      configResponses: [
        { status: 502, body: { error: { code: 'BAD_GATEWAY', message: 'upstream unavailable' } } },
        { body: configuredConfig() },
      ],
    })

    await harness.waitFor(() => expect(screen.getByRole('button', { name: '模型状态' })).toHaveTextContent('模型状态：读取失败'))
    expect(harness.fetch.count('GET', '/api/config/status')).toBe(1)

    harness.visibility.hide()
    harness.visibility.show()

    await harness.waitFor(() => expect(harness!.fetch.count('GET', '/api/config/status')).toBe(2))
    await harness.waitFor(() => expect(screen.getByRole('button', { name: '模型状态' })).toHaveTextContent('模型状态：配置完整'))
  })

  it('刷新防重入，进行中只保留一个 GET，完成后更新内容', async () => {
    harness = await mountWorkspace({
      configResponses: [
        { body: emptyConfig() },
        { body: configuredConfig(), delayMs: 80 },
      ],
    })
    const dialog = await openModelDrawer(harness)
    await within(dialog).findByText('LLM_API_KEY')

    const refresh = within(dialog).getByRole('button', { name: '刷新配置状态' })
    await harness.user.click(refresh)
    await harness.user.click(refresh)

    expect(harness.fetch.count('GET', '/api/config/status')).toBe(2)
    await within(dialog).findByText('90 秒')
    expect(within(dialog).queryByText('LLM_API_KEY')).not.toBeInTheDocument()
    expect(harness.fetch.writes).toHaveLength(0)
  })
})

describe('T01 无障碍与断点', () => {
  it('配置抽屉可键盘打开，焦点进入并受限，Esc 关闭后回到触发按钮', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1280 })
    const trigger = screen.getByRole('button', { name: '模型状态' })
    trigger.focus()
    await harness.user.keyboard('{Enter}')

    const dialog = await screen.findByRole('dialog', { name: '模型状态' })
    const close = within(dialog).getByRole('button', { name: '关闭模型状态' })
    await harness.waitFor(() => expect(close).toHaveFocus())

    for (let i = 0; i < 4; i += 1) {
      await harness.user.tab()
      expect(dialog.contains(document.activeElement)).toBe(true)
    }

    await harness.user.keyboard('{Escape}')
    await harness.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  })

  it('侧栏收起/展开入口有可访问名称、title 与说明', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    const collapse = screen.getByRole('button', { name: '收起侧栏' })
    expect(collapse).toHaveAttribute('title', '收起侧栏')
    const describedBy = collapse.getAttribute('aria-describedby')
    expect(describedBy).toBeTruthy()
    expect(document.getElementById(describedBy!)).toHaveTextContent('收起侧栏后')

    await harness.user.click(collapse)
    const expand = screen.getByRole('button', { name: '展开侧栏' })
    expect(expand).toHaveAttribute('title', '展开侧栏')
  })

  it('断点默认值与手动选择按规则重置，窄屏侧栏使用可关闭抽屉', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), viewportWidth: 1440 })
    expect(screen.getByRole('button', { name: '收起侧栏' })).toBeVisible()

    await harness.setViewportWidth(1024)
    expect(screen.getByRole('button', { name: '收起侧栏' })).toBeVisible()
    await harness.user.click(screen.getByRole('button', { name: '收起侧栏' }))
    expect(screen.getByRole('button', { name: '展开侧栏' })).toBeVisible()
    await harness.user.click(screen.getByRole('button', { name: '展开侧栏' }))
    expect(screen.getByRole('button', { name: '收起侧栏' })).toBeVisible()

    await harness.setViewportWidth(1100)
    expect(screen.getByRole('button', { name: '收起侧栏' })).toBeVisible()

    await harness.setViewportWidth(1440)
    expect(screen.getByRole('button', { name: '收起侧栏' })).toBeVisible()
    await harness.setViewportWidth(1023)
    expect(screen.queryByRole('button', { name: '收起侧栏' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开侧栏' })).toBeVisible()

    await harness.setViewportWidth(500)
    expect(screen.queryByRole('button', { name: '新建项目' })).not.toBeInTheDocument()
    const open = screen.getByRole('button', { name: '打开侧栏' })
    await harness.user.click(open)
    const sideDialog = await screen.findByRole('dialog', { name: '工作区菜单' })
    expect(within(sideDialog).getByRole('button', { name: '新建项目' })).toBeVisible()
    await harness.user.keyboard('{Escape}')
    await harness.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(open).toHaveFocus()
  })

  it('状态播报只有一个 status 区域，相同示例状态不会产生重复区域', async () => {
    harness = await mountWorkspace({ config: emptyConfig() })
    expect(screen.getAllByRole('status')).toHaveLength(1)
    const example = screen.getByRole('button', { name: '米色亚麻托特包，简洁主图' })
    await harness.user.click(example)
    await harness.user.click(example)
    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(screen.getByRole('status')).toHaveTextContent('示例已填入输入框，尚未发送')
  })
})

describe('T01 测试控制入口 smoke', () => {
  it('fake clock、visibility 与 storage 读写失败注入均可用', async () => {
    harness = await mountWorkspace({ config: emptyConfig(), fakeClock: true })
    const before = Date.now()
    await harness.clock.advance(1000)
    expect(Date.now() - before).toBe(1000)

    harness.visibility.hide()
    expect(document.hidden).toBe(true)
    harness.visibility.show()
    expect(document.hidden).toBe(false)

    harness.storage.session.setItem('draft', 'hello')
    expect(harness.storage.session.getItem('draft')).toBe('hello')
    const storage = harness.storage.session
    storage.failReads = true
    expect(() => storage.getItem('draft')).toThrow()
    storage.failReads = false
    storage.failWrites = true
    expect(() => storage.setItem('other', 'x')).toThrow()
  })
})
