import { describe, expect, it } from 'vitest'
import { hasPromptContent, hasValidPromptEdits } from '@/utils/promptContent'

describe('提示词正文判定与后端约定一致', () => {
  it.each([
    '猫', '32', '**猫**', '- 猫', '```text\n猫\n```',
    '# 正向提示词：猫', '# Positive Prompt: cat',
    '# 正向提示词：猫\n# 反向提示词：模糊',
    '**Positive Prompt:** **cat**', '正向提示词：猫\n====',
  ])('保留合法短正文或行内标题正文：%s', (text) => {
    expect(hasPromptContent(text)).toBe(true)
    expect(hasValidPromptEdits(text, '')).toBe(true)
  })

  it.each([
    '', ' \n\t', '#', '## 正向提示词', '```markdown\n\n```',
    '# 正向提示词\n## 反向提示词', '标题\n====',
    '**正向提示词**\n**反向提示词**', '正向提示词：\n反向提示词：',
    '---\n***\n- [ ]', '# 正向提示词\n```text\n## 只有标题\n```',
  ])('拒绝没有正文的结构：%s', (text) => {
    expect(hasPromptContent(text)).toBe(false)
    expect(hasValidPromptEdits(text, '')).toBe(false)
  })
})
