import type { Brief, BriefStatus, GenerationStatus } from '@/api/types'

/** 历史模型产物未审查时的固定占位；不展示任何旧正文、提示词或图片。 */
export const LEGACY_UNREVIEWED_NOTICE = '此历史内容尚未通过 v2 审查，暂不可查看或下载。可以主动发起补审。'

export function legacyNoticeText(notice: string | null | undefined): string {
  return notice?.trim() || LEGACY_UNREVIEWED_NOTICE
}

export const editableBriefFields = [
  { key: 'product_description', label: '商品描述' },
  { key: 'purpose', label: '图片用途' },
  { key: 'style', label: '风格' },
  { key: 'composition', label: '构图与场景' },
  { key: 'text_content', label: '图片中的文字' },
] as const

export type EditableBriefKey = (typeof editableBriefFields)[number]['key']

export function displayProjectTitle(title: string | null | undefined): string {
  const value = (title ?? '').trim()
  return value || '未命名项目'
}

export function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return date.toLocaleString()
}

export function formatByteSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '大小未知'
  return `${bytes.toLocaleString()} 字节`
}

export function briefStatusLabel(status: BriefStatus): string {
  switch (status) {
    case 'draft': return '草稿'
    case 'organizing': return '正在整理'
    case 'needs_input': return '需要补充'
    case 'review': return '待确认'
    case 'confirmed': return '需求已确认'
  }
}

export function generationStatusLabel(status: GenerationStatus): string {
  switch (status) {
    case 'queued': return '等待生成'
    case 'running': return '正在生成'
    case 'succeeded': return '成功'
    case 'failed': return '生成失败'
    case 'unknown': return '结果未确认'
  }
}

export function briefFieldValue(brief: Brief | null | undefined, key: EditableBriefKey): string {
  const value = brief?.[key]
  if (typeof value === 'string' && value.trim()) return value
  return key === 'text_content' ? '不要求加字' : '未填写'
}

export function hasSnapshotContent(brief: Brief | null): boolean {
  if (!brief) return false
  return editableBriefFields.some((field) => Boolean(brief[field.key]?.trim())) || Boolean(brief.prompt?.trim())
}

export function extensionForMime(mime: string): string {
  const normalized = mime.toLowerCase().split(';')[0]?.trim() ?? ''
  switch (normalized) {
    case 'image/png': return 'png'
    case 'image/jpeg': return 'jpg'
    case 'image/webp': return 'webp'
    case 'image/gif': return 'gif'
    default: {
      const subtype = normalized.split('/')[1] ?? ''
      const safe = subtype.replace(/[^a-z0-9]/g, '')
      return safe || 'bin'
    }
  }
}

export function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
}
