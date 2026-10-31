<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { assetUrl } from '@/api/client'
import type {
  WorkflowAction,
  WorkflowArtifact,
  WorkflowLegacyItem,
  WorkflowVersion,
} from '@/api/workflow'
import { currentSessionId } from '@/auth/session'
import { useWorkflow } from '@/composables/useWorkflow'
import { useWorkspace } from '@/composables/useWorkspace'
import { LEGACY_UNREVIEWED_NOTICE } from '@/utils/format'
import {
  confirmationStateOf,
  latestFinalBrief,
  nextStepForWorkflow,
} from '@/workflow/projection'

const { currentProject } = useWorkspace()
const projectId = computed(() => currentProject.value?.id ?? null)
const { view, loading, error, errorCode, actionBusy, refresh, runAction } = useWorkflow(projectId)

const actionText = ref<Record<string, string>>({})
const actionConfirmed = ref<Record<string, boolean>>({})
const actionUsage = ref<Record<string, string>>({})
const actionError = ref('')

const conflictGuidance = computed(() => {
  if (errorCode.value === 'INTENT_REPLAY_CONFLICT') {
    return '本次确认参数与该请求标识已受理的内容不一致，未创建新任务。请刷新并核对当前需求卡；如需改变尺寸或参考用途，请先修改需求并重新确认。'
  }
  if (
    errorCode.value === 'PROMPT_REQUIRES_RECONFIRMATION' ||
    view.value?.issue?.code === 'PROMPT_REQUIRES_RECONFIRMATION'
  ) {
    return '提示词引入了尚未确认的卖点、文字或弱化了已确认要求，本次未提交生图。请修改需求、重新核对并确认当前需求卡后再生成。'
  }
  if (errorCode.value === 'REQUIREMENT_REVISION_CONFLICT' || errorCode.value === 'REQUIREMENTS_CHANGED') {
    return '需求版本已经变化，原确认不能继续使用。请核对最新需求卡并重新确认。'
  }
  return ''
})

const STAGE_LABELS: Record<string, string> = {
  S01: '判断请求范围',
  S02: '分析参考图',
  S03: '整理需求',
  S04: '策划创意',
  S05: '制定视觉方案',
  S06: '审查需求卡',
  S07: '编制生图提示词',
  S08: '生成图片',
  S09: '检查图片',
  S10: '准备交付',
  S11: '反馈新版本',
  S12: '恢复流程',
}

const STATUS_LABELS: Record<string, string> = {
  none: '尚未开始',
  active: '处理中',
  needs_input: '等待补充',
  blocked: '等待处理',
  completed: '已完成',
  failed: '已暂停',
}

const LEGACY_KIND_LABELS: Record<string, string> = {
  message: '历史消息',
  generation: '历史生成记录',
  asset: '历史图片',
}

const GENERATION_JOB_LABELS: Record<string, string> = {
  created: '已创建',
  submit_pending: '提交中',
  submitted: '已提交',
  remote_pending: '生成中',
  result_fetched: '已获取结果',
  quarantined: '待人工审核',
  failed: '已暂停',
  unknown: '结果未确认',
  approved: '已通过审核',
  rejected: '未通过审核',
  published: '已发布',
}

const ARTIFACT_LABELS: Record<string, string> = {
  reference_report: '参考图分析',
  brief_draft: '需求草稿',
  creative_direction: '创意方向',
  visual_plan: '视觉方案',
  final_brief: '最终需求卡',
  prompt_plan: '生图提示词',
  quality_report: '图片检查',
  delivery_manifest: '交付清单',
}

const BRIEF_FIELDS: Array<{ key: string; label: string }> = [
  { key: 'product_description', label: '商品描述' },
  { key: 'purpose', label: '用途' },
  { key: 'facts', label: '已核实事实' },
  { key: 'creative_direction', label: '创意方向' },
  { key: 'style_keywords', label: '风格关键词' },
  { key: 'composition', label: '构图' },
  { key: 'lighting', label: '光线' },
  { key: 'texts', label: '图片文字' },
  { key: 'must_include', label: '必须包含' },
  { key: 'must_preserve', label: '必须保留' },
  { key: 'forbidden', label: '禁止项' },
  { key: 'size', label: '输出尺寸' },
  { key: 'reference_asset_id', label: '参考图 ID' },
  { key: 'reference_usage', label: '参考图用途' },
  { key: 'assumptions', label: '已作假设' },
  { key: 'unresolved', label: '未解决问题' },
]

const GENERATION_STATUS_LABELS: Record<string, string> = {
  pending: '尚未提交生成',
  queued: '等待生成',
  running: '生成中',
  succeeded: '图片待审查',
  failed: '生成失败',
  uncertain: '提交待核实',
  cancelled: '已取消',
}

const visible = computed(() => Boolean(view.value))
const referenceAsset = computed(() => currentProject.value?.reference_asset ?? null)
const stageLabel = computed(() =>
  view.value?.stage ? STAGE_LABELS[view.value.stage] ?? view.value.stage : '',
)
const statusLabel = computed(() =>
  view.value ? STATUS_LABELS[view.value.status] ?? view.value.status : '',
)

const DELIVERY_STATUS_LABELS: Record<string, string> = {
  approved: '待发布',
  published: '已发布',
  revoked: '不可用',
}

function deliveryStatusLabel(status: string): string {
  return DELIVERY_STATUS_LABELS[status] ?? '不可用'
}
const approvedArtifacts = computed<WorkflowArtifact[]>(() => {
  if (!view.value) return []
  const latest = new Map<string, WorkflowArtifact>()
  for (const artifact of view.value.artifacts) {
    if (
      artifact.kind === 'final_brief' ||
      artifact.status !== 'approved' ||
      !artifact.payload
    ) {
      continue
    }
    const current = latest.get(artifact.kind)
    if (!current || current.revision_no < artifact.revision_no) latest.set(artifact.kind, artifact)
  }
  return [...latest.values()]
})

const approvedBriefDraft = computed(() =>
  approvedArtifacts.value.some((artifact) => artifact.kind === 'brief_draft'),
)
const publishedVersions = computed<WorkflowVersion[]>(() =>
  (view.value?.versions ?? []).filter(
    (version) => version.status === 'published' && version.can_download,
  ),
)

const issueHeading = computed(() => {
  if (!view.value?.issue) return ''
  if (view.value.status === 'needs_input') return '需要补充的信息'
  if (view.value.status === 'failed') return '处理暂停'
  return '流程提示'
})

const briefFields = computed(() => {
  const payload = finalBrief.value?.payload
  if (!payload || finalBrief.value?.status !== 'approved') return []
  const known = new Set(BRIEF_FIELDS.map((field) => field.key))
  const rows = BRIEF_FIELDS.filter((field) => field.key in payload).map((field) => ({
    ...field,
    value: formatBriefValue(field.key, payload[field.key]),
  }))
  for (const [key, value] of Object.entries(payload)) {
    if (!known.has(key)) rows.push({ key, label: key, value: formatBriefValue(key, value) })
  }
  return rows
})

const confirmationLabel = computed(() => {
  switch (confirmationState.value) {
    case 'confirmed':
      return '已确认'
    case 'stale':
      return '需重新确认'
    case 'reviewing':
      return '需求卡待审查'
    case 'pending':
      return '需求待确认'
    default:
      return '尚未生成需求卡'
  }
})

const shortDigest = computed(() => {
  const digest = finalBrief.value?.digest ?? ''
  return digest ? digest.slice(0, 12) : ''
})

const imageStatus = computed(() => {
  if (!view.value) return ''
  const delivery = view.value.delivery
  if (delivery) {
    if (delivery.status === 'published') return '已发布'
    if (delivery.status === 'approved') return '交付待发布'
    return '交付已撤销'
  }
  const job = view.value.generation_job
  if (!job) return '尚未提交生成'
  if (job.error_code === 'IMAGE_NOT_APPROVED') return '未通过检查'
  return GENERATION_STATUS_LABELS[job.status] ?? job.status
})

const finalBrief = computed(() => latestFinalBrief(view.value))
const confirmationState = computed(() => confirmationStateOf(view.value))
const nextStep = computed(() => nextStepForWorkflow(currentProject.value, view.value))
const nextStepActionLabel = computed(() => {
  const actionId = nextStep.value.actionId
  if (!actionId) return ''
  const action = view.value?.available_actions.find((item) => item.id === actionId)
  return action ? actionLabel(action) : ''
})
const hasRun = computed(() => Boolean(view.value?.run_id))
const usage = computed(() => view.value?.usage ?? null)
const generationIntent = computed(() => view.value?.intent ?? null)

const visibleActions = computed(() => {
  const actions = view.value?.available_actions ?? []
  return actions.filter((action) => {
    if (action.id === 'review_legacy_item') return false
    return action.id === 'confirm_requirements'
      ? confirmationState.value === 'pending'
      : true
  })
})

const generationJobPending = computed(() => view.value?.generation_job?.status === 'quarantined')

function artifactLabel(kind: string): string {
  return ARTIFACT_LABELS[kind] ?? kind
}

function legacyKindLabel(kind: string): string {
  return LEGACY_KIND_LABELS[kind] ?? '历史内容'
}

function versionDownloadUrl(version: WorkflowVersion): string {
  return version.asset_id ? assetUrl(version.asset_id) + '?download=true' : ''
}

function versionBrief(version: WorkflowVersion): string {
  const product = version.brief?.product_description
  const purpose = version.brief?.purpose
  const parts = [product, purpose].filter(
    (item): item is string => typeof item === 'string' && item.length > 0,
  )
  return parts.join(' · ')
}

function versionPrompt(version: WorkflowVersion): string {
  const prompt = version.prompt?.positive_prompt
  return typeof prompt === 'string' ? prompt : ''
}

function formatBriefValue(key: string, value: unknown): string {
  if (key === 'reference_usage') {
    const labels: Record<string, string> = {
      none: '不使用参考图',
      product: '参考商品外观',
      style: '参考风格',
      composition: '参考构图',
    }
    return typeof value === 'string' ? labels[value] ?? value : '—'
  }
  if (Array.isArray(value)) {
    if (!value.length) return '无'
    return value
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') {
          const record = item as Record<string, unknown>
          if (typeof record.text === 'string') return record.text
          return JSON.stringify(item)
        }
        return String(item)
      })
      .join('；')
  }
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function payloadLines(payload: Record<string, unknown>): string[] {
  return Object.entries(payload).map(([key, value]) => {
    const text = typeof value === 'string' ? value : JSON.stringify(value)
    return `${key}: ${text}`
  })
}

function usageValue(action: WorkflowAction): string {
  return actionUsage.value[action.id] ?? 'product'
}

function onUsageChange(action: WorkflowAction, event: Event): void {
  actionUsage.value[action.id] = (event.target as HTMLSelectElement).value
}

function canSubmit(action: WorkflowAction): boolean {
  if (loading.value || actionBusy.value) return false
  if (action.requires_text && !(actionText.value[action.id] ?? '').trim()) return false
  if (action.requires_confirmation && !actionConfirmed.value[action.id]) return false
  return true
}

function actionClass(action: WorkflowAction): string {
  return action.kind === 'confirm' ? 'primary-button' : 'secondary-button'
}

function confirmationText(action: WorkflowAction): string {
  if (action.id === 'close_generation_tracking') {
    return '我确认结束本地跟踪；原受理计数保留，且这不代表取消远端任务'
  }
  if (action.id === 'start_new_generation') {
    return '我确认这是新任务，将重新计数并占用槽位；旧任务仍可能在远端执行或计费'
  }
  return '我已核对当前内容并确认继续'
}

function actionLabel(action: WorkflowAction): string {
  if (action.id === 'confirm_requirements' && usage.value) {
    return '确认以上需求并生成图片'
  }
  return action.label
}

function randomRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (token) => {
    const value = Math.floor(Math.random() * 16)
    const digit = token === 'x' ? value : (value & 0x3) | 0x8
    return digit.toString(16)
  })
}

function confirmationStorageKey(): string | null {
  const current = view.value
  const brief = finalBrief.value
  if (!current || !brief) return null
  const parts = [
    'v3-generation-request',
    current.project_id,
    current.run_id ?? '',
    current.requirement_revision,
    brief.digest,
  ]
  // 生产环境把稳定请求标识绑定到登录生命周期；旧测试模式没有会话时保持旧键。
  if (currentSessionId.value) parts.push('session', currentSessionId.value)
  return parts.join(':')
}

function confirmationRequestId(): string {
  const key = confirmationStorageKey()
  if (!key) return randomRequestId()
  try {
    const stored = window.localStorage.getItem(key)
    if (stored) return stored
  } catch {
    // Storage can be unavailable in hardened browser profiles; fall back to
    // an in-memory id rather than blocking confirmation.
  }
  const generated = randomRequestId()
  try {
    window.localStorage.setItem(key, generated)
  } catch {
    // The request id is still stable for this component instance.
  }
  return generated
}

watch(generationIntent, (intent) => {
  if (!intent || !['delivered', 'failed', 'abandoned'].includes(intent.status)) return
  const key = confirmationStorageKey()
  if (!key) return
  try {
    window.localStorage.removeItem(key)
  } catch {
    // Ignore storage failures; the next confirmation will use a fresh id.
  }
})

function actionPayload(action: WorkflowAction): Record<string, unknown> {
  if (action.id === 'close_generation_tracking') {
    return { acknowledge_local_close: true }
  }
  if (action.id === 'start_new_generation') {
    return { acknowledge_risk: true }
  }
  if (action.id === 'confirm_requirements' && finalBrief.value && view.value) {
    const payload: Record<string, unknown> = {
      artifact_id: finalBrief.value.id,
      artifact_digest: finalBrief.value.digest,
      requirement_revision: view.value.requirement_revision,
    }
    if (usage.value) payload.request_id = confirmationRequestId()
    return payload
  }
  return {}
}

async function reviewLegacy(item: WorkflowLegacyItem): Promise<void> {
  const action: WorkflowAction = {
    id: 'review_legacy_item',
    label: '补审历史内容',
    kind: 'button',
    requires_text: false,
    text_label: null,
    requires_confirmation: false,
  }
  await runAction(action, null, { item_id: item.id })
}

async function submit(action: WorkflowAction): Promise<void> {
  actionError.value = ''
  if (!canSubmit(action)) {
    actionError.value = action.requires_text
      ? '请先填写说明。'
      : action.requires_confirmation
        ? '请先勾选确认。'
        : '当前操作暂不可用。'
    return
  }
  const text = action.requires_text ? (actionText.value[action.id] ?? '').trim() : null
  const payload: Record<string, unknown> = actionPayload(action)
  if (action.id === 'confirm_reference_usage') {
    payload.usage = usageValue(action)
  }
  const ok = await runAction(action, text, payload)
  if (ok) {
    delete actionText.value[action.id]
    delete actionConfirmed.value[action.id]
  }
}

// After an upload the workflow view is refreshed once, then the v2 UI asks the
// server for the independent S02 analysis immediately. A missing or failing
// adapter leaves the original image and the fixed stage status visible; there
// is no text-only fallback and no automatic retry loop.
watch(
  () => currentProject.value?.reference_asset?.id ?? null,
  async (id, previous) => {
    if (!id || id === previous || !view.value) return
    await refresh()
    if (view.value?.status !== 'none') return
    const action = view.value?.available_actions.find((item) => item.id === 'analyze_reference')
    if (action) await runAction(action, null, {})
  },
)
</script>

<template>
  <section
    v-if="visible"
    class="workflow-panel"
    aria-labelledby="workflow-panel-title"
    :aria-busy="loading || actionBusy"
  >
    <header class="workflow-panel-header">
      <div>
        <p class="eyebrow">流程状态</p>
        <h3 id="workflow-panel-title">{{ stageLabel || '当前流程' }}</h3>
      </div>
      <div class="workflow-panel-status">
        <span class="status-badge">{{ statusLabel }}</span>
        <button
          type="button"
          class="text-button"
          :disabled="loading || actionBusy"
          :aria-busy="loading"
          @click="refresh"
        >
          刷新
        </button>
      </div>
    </header>

    <section class="workflow-next-step" aria-labelledby="workflow-next-step-title" role="status">
      <p class="eyebrow">当前问题与下一步</p>
      <h4 id="workflow-next-step-title">{{ nextStep.title }}</h4>
      <p>{{ nextStep.detail }}</p>
      <p v-if="nextStepActionLabel" class="muted">下一步操作：{{ nextStepActionLabel }}</p>
    </section>

    <p v-if="loading" class="workflow-loading" role="status">正在读取流程状态…</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="conflictGuidance" class="muted workflow-conflict-guidance" role="note">
      {{ conflictGuidance }}
    </p>

    <div v-if="referenceAsset && view?.stage === 'S02'" class="workflow-reference">
      <h4>参考图预览</h4>
      <img
        v-if="referenceAsset.availability === 'available'"
        :src="assetUrl(referenceAsset.id)"
        alt="你的参考图"
        class="workflow-reference-image"
      />
      <p v-else class="form-error">参考图文件不可读，请重新上传或移除。</p>
      <p class="muted">报告完成审查前只显示原图预览和阶段状态。</p>
    </div>

    <div v-if="view?.issue" class="workflow-issue" role="alert">
      <p class="eyebrow">{{ issueHeading }}</p>
      <strong>{{ view.issue.message }}</strong>
    </div>

    <div v-if="view && !hasRun" class="workflow-empty" role="status">
      先描述商品与用途，提交后会开始范围判断。
    </div>

    <div v-if="hasRun && finalBrief" class="workflow-confirmation" role="status">
      <p>
        需求状态：<strong>{{ confirmationLabel }}</strong>
        <span class="status-badge">{{ finalBrief.status === 'approved' ? '已通过独立审查' : '独立审查未完成' }}</span>
      </p>
      <p class="workflow-version">
        第 {{ finalBrief.revision_no }} 次修订 · 摘要 {{ shortDigest || '—' }} ·
        项目版本 {{ view?.project_revision }} · 需求版本 {{ view?.requirement_revision }}
      </p>
      <p v-if="confirmationState === 'reviewing'" class="muted">
        最新需求卡正在独立审查，审查通过后才能确认，暂不提供确认入口。
      </p>
      <p v-if="confirmationState === 'stale'" class="muted">
        需求内容、参考图或需求版本已经变化，原确认已失效；请在审查通过后重新确认当前卡片。
      </p>
      <p class="muted">图片状态：{{ imageStatus }}</p>
      <div v-if="usage" class="workflow-usage" role="status">
        <p>
          今日已受理 <strong>{{ usage.used }}</strong> / {{ usage.daily_limit }} 次，剩余
          <strong>{{ usage.remaining }}</strong> 次（滚动 24 小时）。
        </p>
        <p>
          进行中：个人 {{ usage.personal_active }} / {{ usage.personal_limit }} · 实例
          {{ usage.instance_active }} / {{ usage.instance_limit }} · 本项目
          {{ usage.project_active }} / {{ usage.project_limit }}
        </p>
        <p v-if="generationIntent" class="muted">
          本次生成已受理，请求标识 {{ generationIntent.request_id.slice(0, 8) }}…，
          状态 {{ generationIntent.status }}。重复点击不会再次计数。
        </p>
      </div>
    </div>

    <section
      v-if="hasRun && finalBrief"
      class="workflow-brief"
      aria-labelledby="workflow-brief-title"
    >
      <header>
        <h4 id="workflow-brief-title">当前最终需求卡</h4>
        <span>
          第 {{ finalBrief.revision_no }} 次修订 · 摘要 {{ shortDigest || '—' }}
        </span>
      </header>
      <p v-if="!finalBrief.payload || finalBrief.status !== 'approved'" class="muted">
        需求卡内容正在审查，通过后会在此完整展示，不能提前确认。
      </p>
      <dl v-else class="workflow-brief-fields">
        <div v-for="field in briefFields" :key="field.key" class="workflow-brief-field">
          <dt>{{ field.label }}</dt>
          <dd>{{ field.value }}</dd>
        </div>
      </dl>
    </section>

    <div v-if="visibleActions.length" class="workflow-actions">
      <div
        v-for="action in visibleActions"
        :key="action.id"
        class="workflow-action"
      >
        <label v-if="action.requires_text" :for="`workflow-${action.id}`">
          {{ action.text_label || action.label }}
        </label>
        <textarea
          v-if="action.requires_text"
          :id="`workflow-${action.id}`"
          v-model="actionText[action.id]"
          rows="2"
          :disabled="loading || actionBusy"
        />
        <label v-if="action.id === 'confirm_reference_usage'" :for="`workflow-${action.id}-usage`">
          参考图用途
        </label>
        <select
          v-if="action.id === 'confirm_reference_usage'"
          :id="`workflow-${action.id}-usage`"
          :value="usageValue(action)"
          :disabled="actionBusy"
          @change="onUsageChange(action, $event)"
        >
          <option value="product">商品外观</option>
          <option value="style">风格参考</option>
          <option value="composition">构图参考</option>
        </select>
        <label v-if="action.requires_confirmation" class="workflow-confirm">
          <input v-model="actionConfirmed[action.id]" type="checkbox" :disabled="loading || actionBusy" />
          {{ confirmationText(action) }}
        </label>
        <button
          type="button"
          :class="actionClass(action)"
          :disabled="!canSubmit(action)"
          :aria-busy="actionBusy || loading"
          @click="submit(action)"
        >
          {{ actionBusy ? '处理中…' : actionLabel(action) }}
        </button>
      </div>
      <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
      <p v-if="view?.available_actions.some((item) => item.id === 'request_new_version')" class="muted workflow-version-note" role="note">
        反馈会重新确认需求并生成一个新版本，不承诺局部像素不变或只修改指定区域。每次生成仍仅一张图并使用固定尺寸。
      </p>
    </div>

    <div v-if="approvedArtifacts.length" class="workflow-artifacts">
      <article v-for="artifact in approvedArtifacts" :key="artifact.id" class="workflow-card">
        <header>
          <h4>{{ artifactLabel(artifact.kind) }}</h4>
          <span>第 {{ artifact.revision_no }} 次 · 已审查</span>
        </header>
        <ul>
          <li v-for="line in payloadLines(artifact.payload ?? {})" :key="line">{{ line }}</li>
        </ul>
      </article>
    </div>

    <p v-if="approvedBriefDraft" class="muted workflow-draft-note" role="note">
      需求草稿已通过独立审查，仅表示可进入后续审核；尚未成为最终需求卡，也未获得用户确认或生成授权。
    </p>
    <div v-if="view?.generation_job" class="workflow-job">
      <strong>生成任务</strong>
      <span>{{ GENERATION_STATUS_LABELS[view.generation_job.status] ?? GENERATION_JOB_LABELS[view.generation_job.status] ?? view.generation_job.status }}</span>
      <p v-if="generationJobPending" class="muted" role="note">
        候选图片已保存在后台，待人工审核；当前不展示预览、链接或下载。
      </p>
      <p v-if="view.generation_job.error_message" class="form-error" role="alert">
        {{ view.generation_job.error_message }}
      </p>
    </div>

    <div v-if="view?.delivery" class="workflow-delivery">
      <div class="workflow-delivery-heading">
        <strong>交付：{{ view.delivery.filename }}</strong>
        <span class="status-badge">{{ deliveryStatusLabel(view.delivery.status) }}</span>
      </div>
      <p v-if="view.delivery.caption">{{ view.delivery.caption }}</p>
      <template v-if="view.delivery.status === 'published'">
        <img
          class="workflow-delivery-image"
          :src="assetUrl(view.delivery.asset_id)"
          :alt="view.delivery.caption || '已发布交付图片'"
        />
        <a
          class="workflow-download"
          :href="assetUrl(view.delivery.asset_id) + '?download=true'"
          :download="view.delivery.filename"
        >
          下载已发布图片
        </a>
      </template>
      <p v-else-if="view.delivery.status === 'revoked'" class="muted">
        当前内容不可访问或批准已失效，请检查状态或重新审查。
      </p>
      <p v-else class="muted">交付尚未发布，暂不提供查看或下载。</p>
    </div>

    <section v-if="publishedVersions.length" class="workflow-versions" aria-labelledby="workflow-versions-title">
      <h4 id="workflow-versions-title">历史版本</h4>
      <article v-for="version in publishedVersions" :key="version.run_id" class="workflow-version">
        <header>
          <strong>第 {{ version.requirement_revision }} 版</strong>
          <span>已发布 · 已审查</span>
        </header>
        <p v-if="versionBrief(version)">需求：{{ versionBrief(version) }}</p>
        <p v-if="versionPrompt(version)">提示词：{{ versionPrompt(version) }}</p>
        <figure v-if="version.asset_id" class="workflow-version-preview">
          <img
            :src="assetUrl(version.asset_id)"
            :alt="'第 ' + version.requirement_revision + ' 版历史预览'"
            loading="lazy"
          />
          <figcaption>历史预览 · 不代表当前需求状态</figcaption>
        </figure>
        <a v-if="version.asset_id" :href="versionDownloadUrl(version)" class="text-button" download>
          下载第 {{ version.requirement_revision }} 版
        </a>
      </article>
    </section>

    <div v-if="view?.legacy.length" class="workflow-legacy">
      <h4>历史内容</h4>
      <ul>
        <li v-for="item in view.legacy" :key="item.id">
          <span>{{ legacyKindLabel(item.kind) }}</span>
          <span>{{ item.status === 'legacy_unreviewed' ? '尚未通过 v2 审查' : '已补审' }}</span>
          <span v-if="item.status === 'legacy_unreviewed'" class="muted">
            {{ LEGACY_UNREVIEWED_NOTICE }}
          </span>
          <button
            v-if="item.status === 'legacy_unreviewed'"
            type="button"
            class="text-button"
            :disabled="actionBusy"
            @click="reviewLegacy(item)"
          >
            补审此历史内容
          </button>
        </li>
      </ul>
    </div>
  </section>
</template>
