import { afterEach, describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/vue'
import { mountWorkspace, type WorkspaceHarness } from './support/workspace'
import { configuredConfig } from './support/fixtures'

let harness: WorkspaceHarness | undefined

afterEach(() => {
  harness?.dispose()
  harness = undefined
})

describe('T60 外部模型数据流说明', () => {
  it('首次业务输入前显示数据流与秘密信息提示，且不声称供应商零保留', async () => {
    harness = await mountWorkspace({ config: configuredConfig(), viewportWidth: 1440 })
        const notice = await screen.findByRole('region', { name: '外部模型数据说明' })
    expect(notice).toBeVisible()
    expect(notice).toHaveTextContent('文字描述、需求卡信息和参考图会发送给已配置的外部模型服务')
    expect(notice).toHaveTextContent('请勿在描述或图片中填写密码、会话凭证、恢复链接或其他秘密信息')
    expect(notice).toHaveTextContent('不承诺供应商零保留')
  })
})
