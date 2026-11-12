/** Client-side feedback for the backend's prompt-body rule, not authorization. */
const promptLabels = new Set([
  '正向提示词', '正面提示词', '正向', 'positive prompt', 'positive',
  '反向提示词', '负向提示词', '负面提示词', '反向', '负向', 'negative prompt', 'negative',
  '生成提示词', '提示词', 'prompt', 'generation prompt',
])
const hasLettersOrNumbers = (text: string) => /[\p{L}\p{N}]/u.test(text)

function inlinePromptBody(text: string): string | null {
  const label = text.replace(/^\s*(?:[-+*]|\d+[.)、])\s+/, '').trim()
  const colon = label.search(/[:：]/)
  const name = (colon < 0 ? label : label.slice(0, colon))
    .trim().replace(/^[*_:：\s]+|[*_:：\s]+$/g, '').replace(/\s+/g, ' ').toLowerCase()
  if (!promptLabels.has(name)) return null
  return colon < 0 ? '' : label.slice(colon + 1)
}

export function hasPromptContent(value: string | null | undefined): boolean {
  const lines = (value ?? '').split(/\r?\n/)
  return lines.some((line, index) => {
    const text = line.trim()
    if (!text || /^(?:`{3,}|~{3,})/.test(text)) return false
    if (/^(?:=+|-+)$/.test(text)) return false
    const heading = /^#{1,6}(?:\s+(.*?)|\s*)$/.exec(text)
    const inline = inlinePromptBody(heading ? (heading[1] ?? '').replace(/\s+#+\s*$/, '') : text)
    // A heading can contain real prompt text: "# 正向提示词：猫".
    if (inline !== null) return hasLettersOrNumbers(inline)
    if (heading || /^\s*(?:=+|-+)\s*$/.test(lines[index + 1] ?? '')) return false
    return hasLettersOrNumbers(text)
  })
}

export function hasValidPromptEdits(positive: string, negative: string): boolean {
  return hasPromptContent(positive) && (!negative.trim() || hasPromptContent(negative))
    && positive.length <= 12000 && negative.length <= 6000
}
