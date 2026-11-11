/** Markdown structure alone is not a prompt; short plain text remains valid. */
export function hasPromptContent(value: string | null | undefined): boolean {
  const lines = (value ?? '').split(/\r?\n/)
  return lines.some((line, index) => {
    const text = line.trim()
    if (!text || /^#{1,6}(?:\s|$)/.test(text)) return false
    if (/^(?:`{3,}|~{3,})/.test(text)) return false
    if (/^(?:=+|-+)$/.test(text)) return false
    // Setext headings consist of a title followed by an underline.
    if (/^\s*(?:=+|-+)\s*$/.test(lines[index + 1] ?? '')) return false
    return true
  })
}

export function hasValidPromptEdits(positive: string, negative: string): boolean {
  return hasPromptContent(positive) && (!negative.trim() || hasPromptContent(negative))
    && positive.length <= 12000 && negative.length <= 6000
}
