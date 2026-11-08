<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import ImageLightbox from '@/components/ImageLightbox.vue'
import { assetUrl } from '@/api/client'
import type { WorkflowAction } from '@/api/workflow'
import { useChatFlow } from '@/composables/useChatFlow'
import { publishLiveStatus, type LiveStatusInput } from '@/composables/useLiveStatus'
import { useWorkspace } from '@/composables/useWorkspace'
import { displayProjectTitle, formatDateTime } from '@/utils/format'

const workspace = useWorkspace()
const flow = useChatFlow()
const route = useRoute()
const {
  projectMessages,
  projectLoading,
  projectError,
  currentProject,
  projectParam,
  draft,
  isMobile,
} = workspace
const {
  busy,
  flowError,
  copied,
  pendingAttachment,
  attachmentBusy,
  attachmentError,
  attachmentFileName,
  chooseAttachment,
  removeAttachment,
  hasConversation,
  requirementRows,
  canConfirmRequirements,
  promptPreview,
  positivePrompt,
  negativePrompt,
  promptOutput,
  referenceInfo,
  publishedAssetId,
  publishedCaption,
  imageHistory,
  resultUnavailableText,
  taskReason,
  taskReasonLabel,
  taskStatusText,
  taskActions,
  taskConfirmations,
  pendingTaskAction,
  pendingTaskConfirmationMissing,
  isWorkingStatus,
  send,
  confirmRequirements,
  confirmPrompt,
  copyPrompt,
  retry,
  taskActionEffectLabel,
  taskActionHelp,
  taskConfirmationText,
  taskCountNote,
  taskActionDisabled,
  runTaskAction,
} = flow

const scrollRegion = ref<HTMLElement | null>(null)
const chatContent = ref<HTMLElement | null>(null)
const composerArea = ref<HTMLElement | null>(null)
const composerInput = ref<HTMLTextAreaElement | null>(null)
const attachmentInput = ref<HTMLInputElement | null>(null)
const composing = ref(false)
const stickToBottom = ref(true)
const newContentAvailable = ref(false)
const footerHeight = ref(132)
const lightboxOpen = ref(false)
const lightboxItem = ref<{ assetId: string; caption: string } | null>(null)
const pendingSend = ref<{ text: string; sentAt: string } | null>(null)
const retryingProject = ref(false)
const requirementSubmitting = ref(false)
const requirementConfirmed = ref(false)
const suggestions = [
  '把这张产品图换成秋日户外场景，保留包装质感',
  '为这组新品生成一张极简台面主图，冷白背景',
  '做一张 1:1 促销主视觉，石墨黑底，留出价格位置',
]
const ATTACHMENT_CAPTION = '参考图'
let initialComposerFocused = false
let composerFrame = 0
let bottomFrame = 0
let ignoreScrollUntil = 0
let contentObserver: ResizeObserver | null = null
let footerObserver: ResizeObserver | null = null
const title = computed(() => currentProject.value ? displayProjectTitle(currentProject.value.title) : '新对话')
const usageText = computed(() => {
  const usage = flow.workflow.view.value?.usage
  if (!usage) return '本次生成会占用 1 次额度。'
  return '本次生成会占用 1 次额度；今日已受理 ' + usage.used + ' / ' + usage.daily_limit + ' 次。'
})
const sendDisabled = computed(() =>
  busy.value ||
  attachmentBusy.value ||
  pendingTaskConfirmationMissing.value ||
  !draft.value.trim()
)
const promptStateLabel = computed(() => {
  if (!promptPreview.value) return ''
  return promptPreview.value.can_confirm ? '' : '提示词正在检查，确认按钮暂不可用。'
})

interface PromptCardSnapshot {
  positive: string
  negative: string
  output: string
  reference: string
  version: string
}

const promptSnapshot = ref<PromptCardSnapshot | null>(null)
const promptConfirmed = ref(false)
const promptConfirming = ref(false)
const promptConfirmedId = ref('')
let promptHoldTimer: ReturnType<typeof setTimeout> | null = null

function promptVersionFromRequest(request: Record<string, unknown> | undefined): string {
  const raw = request?.contract_version
  if (typeof raw !== 'string') return ''
  const match = raw.match(/(\d+)\.(\d+)/)
  return match ? 'v' + match[1] + '.' + match[2] : ''
}

const promptVersionLabel = computed(() =>
  promptSnapshot.value?.version || promptVersionFromRequest(promptPreview.value?.request) || 'v1'
)
const promptCardPositive = computed(() => promptSnapshot.value?.positive ?? positivePrompt.value)
const promptCardNegative = computed(() => promptSnapshot.value?.negative ?? negativePrompt.value)
const promptCardOutput = computed(() => promptSnapshot.value?.output ?? promptOutput.value)
const promptCardReference = computed(() => promptSnapshot.value?.reference ?? referenceInfo.value)
const promptCardVisible = computed(() => Boolean(promptPreview.value || promptSnapshot.value))
const promptStatus = computed<'pending' | 'checking' | 'confirmed'>(() => {
  if (promptConfirmed.value) return 'confirmed'
  return promptPreview.value?.can_confirm === false ? 'checking' : 'pending'
})
const promptStatusLabel = computed(() => {
  if (promptStatus.value === 'confirmed') return '已确认'
  return promptStatus.value === 'checking' ? '检查中' : '待确认'
})
const promptCanConfirm = computed(() =>
  Boolean(promptPreview.value?.can_confirm) &&
  !promptConfirmed.value &&
  !promptConfirming.value &&
  !busy.value
)
const promptCtaLabel = computed(() =>
  promptPreview.value && promptPreview.value.can_confirm === false
    ? '提示词检查中…'
    : '确认使用以上提示词生成'
)
const promptExecutionStatus = computed(() => taskStatusText.value || '已确认，正在执行…')

type TaskVisualState = 'executing' | 'failed' | 'unknown' | 'recovered' | 'waiting'
const RECOVERY_ACTION_IDS = new Set([
  'verify_generation',
  'recover_generation',
  'retrieve_original_result',
  'recover_submission',
  'resume_after_restart',
  'continue_workflow',
])
const taskActionPending = ref(false)
const taskPendingLabel = ref('')
const taskRecovered = ref(false)
let taskRecoveredRunId: string | null = null

const taskUnknown = computed(() => taskReason.value?.category === 'remote_unknown')
const taskFailed = computed(() => taskReason.value?.severity === 'failed')
const taskVisualState = computed<TaskVisualState>(() => {
  if (taskActionPending.value || isWorkingStatus.value) return 'executing'
  if (taskRecovered.value) return 'recovered'
  if (taskUnknown.value) return 'unknown'
  if (taskFailed.value) return 'failed'
  return 'waiting'
})
const taskTurnVisible = computed(() =>
  Boolean(
    (taskStatusText.value || taskActions.value.length || taskRecovered.value) &&
      (!publishedAssetId.value || taskRecovered.value),
  ),
)
const taskDisplayText = computed(() =>
  taskActionPending.value ? taskPendingLabel.value : taskStatusText.value,
)

watch(
  () => flow.workflow.view.value?.run_id,
  (runId) => {
    if (taskRecovered.value && taskRecoveredRunId && runId !== taskRecoveredRunId) {
      taskRecovered.value = false
      taskRecoveredRunId = null
    }
  },
)

function markTaskRecovered(): void {
  taskRecovered.value = true
  taskRecoveredRunId = flow.workflow.view.value?.run_id ?? null
}

async function onTaskAction(action: WorkflowAction): Promise<void> {
  if (taskActionDisabled(action)) return
  const isRecovery = RECOVERY_ACTION_IDS.has(action.id) && !action.requires_text
  if (isRecovery) {
    taskRecovered.value = false
    taskActionPending.value = true
    taskPendingLabel.value =
      action.id === 'verify_generation' || action.id === 'retrieve_original_result'
        ? '正在核实…'
        : '正在恢复…'
  }
  try {
    await runTaskAction(action)
  } finally {
    if (isRecovery) {
      taskActionPending.value = false
      taskPendingLabel.value = ''
      if (flow.workflow.view.value?.status === 'completed') markTaskRecovered()
    }
  }
}

function clearPromptHoldTimer(): void {
  if (promptHoldTimer !== null) {
    clearTimeout(promptHoldTimer)
    promptHoldTimer = null
  }
}

function resetPromptCard(): void {
  clearPromptHoldTimer()
  promptSnapshot.value = null
  promptConfirmed.value = false
  promptConfirming.value = false
  promptConfirmedId.value = ''
}

function onConfirmPrompt(): void {
  if (promptConfirmed.value || promptConfirming.value) return
  const preview = promptPreview.value
  if (!preview?.can_confirm || busy.value) return
  const hasAction = flow.workflow.view.value?.available_actions.some((item) => item.id === 'confirm_prompt')
  if (!hasAction) return
  promptSnapshot.value = {
    positive: positivePrompt.value,
    negative: negativePrompt.value,
    output: promptOutput.value,
    reference: referenceInfo.value,
    version: promptVersionLabel.value,
  }
  promptConfirmedId.value = preview.id
  promptConfirmed.value = true
  promptConfirming.value = true
  clearPromptHoldTimer()
  promptHoldTimer = setTimeout(() => {
    promptHoldTimer = null
    promptSnapshot.value = null
  }, 3000)
  void confirmPrompt().then(
    () => {
      promptConfirming.value = false
      if (flowError.value && promptPreview.value?.can_confirm) resetPromptCard()
    },
    () => {
      promptConfirming.value = false
      resetPromptCard()
    },
  )
}

function openAttachmentPicker(): void {
  attachmentInput.value?.click()
}

function onAttachmentChange(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) void chooseAttachment(file)
  input.value = ''
}

function firstImageFile(data: DataTransfer | null): File | null {
  if (!data) return null
  const files = Array.from(data.files ?? [])
  const direct = files.find((file) => file.type.startsWith('image/'))
  if (direct) return direct
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) return file
    }
  }
  return null
}

function onPaste(event: ClipboardEvent): void {
  const file = firstImageFile(event.clipboardData)
  if (!file) return
  event.preventDefault()
  void chooseAttachment(file)
}

function onDrop(event: DragEvent): void {
  const file = firstImageFile(event.dataTransfer)
  if (!file) return
  event.preventDefault()
  void chooseAttachment(file)
}

function scrollToLatest(behavior: ScrollBehavior = 'auto'): void {
  const element = scrollRegion.value
  if (!element) return
  // Smooth scrolling emits a stream of intermediate scroll events; ignore them
  // until the animation settles. An instant scroll lands on the live edge and
  // its single event already reports distance 0, so it needs no suppression.
  ignoreScrollUntil = behavior === 'smooth' ? performance.now() + 700 : 0
  if (behavior === 'smooth' && typeof element.scrollTo === 'function') {
    element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
  } else {
    element.scrollTop = element.scrollHeight
  }
  stickToBottom.value = true
  newContentAvailable.value = false
}

function scheduleStickToBottom(): void {
  if (!stickToBottom.value || bottomFrame) return
  bottomFrame = requestAnimationFrame(() => {
    bottomFrame = 0
    if (!stickToBottom.value) return
    const element = scrollRegion.value
    if (!element) return
    ignoreScrollUntil = performance.now() + 80
    element.scrollTop = element.scrollHeight
  })
}

function onScroll(): void {
  if (performance.now() < ignoreScrollUntil) return
  const element = scrollRegion.value
  if (!element) return
  const distance = element.scrollHeight - element.scrollTop - element.clientHeight
  stickToBottom.value = distance <= 48
  if (stickToBottom.value) newContentAvailable.value = false
}

function resizeComposerNow(): void {
  const element = composerInput.value
  if (!element) return
  element.style.height = 'auto'
  const next = Math.min(element.scrollHeight || 44, 212) + 'px'
  if (element.style.height !== next) element.style.height = next
}

function scheduleComposerResize(): void {
  if (composerFrame) return
  composerFrame = requestAnimationFrame(() => {
    composerFrame = 0
    resizeComposerNow()
  })
}

function onComposerInput(): void {
  scheduleComposerResize()
}

function onMediaLoad(): void {
  if (stickToBottom.value) scheduleStickToBottom()
}

function openLightbox(item: { assetId: string | null; caption: string }, event: Event): void {
  if (!item.assetId) return
  const trigger = event.currentTarget as HTMLElement | null
  trigger?.focus()
  lightboxItem.value = { assetId: item.assetId, caption: item.caption }
  lightboxOpen.value = true
}

function onResultCardClick(
  item: { assetId: string | null; caption: string },
  event: MouseEvent,
): void {
  const target = event.target as HTMLElement | null
  // 卡片内真实的链接、按钮和提示词折叠区优先，不劫持它们的点击。
  if (target?.closest('a, button, summary, details, input, textarea, select, label')) return
  openLightbox(item, event)
}

function onResultCardKeydown(
  item: { assetId: string | null; caption: string },
  event: KeyboardEvent,
): void {
  // 只有焦点在卡片本身时才拦截，内部链接/按钮保留原生键盘行为。
  if (event.target !== event.currentTarget) return
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  openLightbox(item, event)
}

function closeLightbox(): void {
  lightboxOpen.value = false
}

watch(
  () => [
    projectMessages.value.length,
    taskStatusText.value,
    canConfirmRequirements.value,
    Boolean(promptPreview.value),
    publishedAssetId.value,
    imageHistory.value.length,
  ],
  async () => {
    await nextTick()
    if (stickToBottom.value) scrollToLatest()
    else newContentAvailable.value = true
  },
  { flush: 'post' },
)

watch(
  () => draft.value,
  () => scheduleComposerResize(),
  { flush: 'post' },
)

watch(
  () => projectParam.value,
  async () => {
    resetPromptCard()
    stickToBottom.value = true
    newContentAvailable.value = false
    await nextTick()
    scrollToLatest()
  },
  { flush: 'post' },
)

watch(
  () => promptPreview.value?.id,
  (id) => {
    if (id && promptConfirmed.value && id !== promptConfirmedId.value) resetPromptCard()
  },
)

watch(
  () => route.hash,
  async (hash) => {
    if (hash !== '#composer') return
    await nextTick()
    composerArea.value?.scrollIntoView({ block: 'nearest' })
    composerInput.value?.focus({ preventScroll: true })
  },
  { immediate: true, flush: 'post' },
)

onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') {
    if (chatContent.value) {
      contentObserver = new ResizeObserver(() => {
        if (stickToBottom.value) scheduleStickToBottom()
      })
      contentObserver.observe(chatContent.value)
    }
    if (composerArea.value) {
      footerObserver = new ResizeObserver(() => {
        footerHeight.value = composerArea.value?.offsetHeight ?? 0
      })
      footerObserver.observe(composerArea.value)
      footerHeight.value = composerArea.value.offsetHeight
    }
  }
  scheduleComposerResize()
  focusComposerOnFirstLoad()
})

onBeforeUnmount(() => {
  clearPromptHoldTimer()
  if (composerFrame) cancelAnimationFrame(composerFrame)
  if (bottomFrame) cancelAnimationFrame(bottomFrame)
  contentObserver?.disconnect()
  footerObserver?.disconnect()
})

async function submit(): Promise<void> {
  const value = draft.value
  if (sendDisabled.value) return
  pendingSend.value = { text: value.trim(), sentAt: new Date().toISOString() }
  try {
    await send(value)
  } finally {
    pendingSend.value = null
  }
}

function onComposerKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter') return
  if (composing.value || event.isComposing) return
  if (event.altKey) return
  if (event.shiftKey && !event.ctrlKey && !event.metaKey) return
  event.preventDefault()
  void submit()
}

function resultPageRoute(runId: string): { name: string; params?: { projectId: string }; query?: { version: string } } {
  const projectId = currentProject.value?.id
  if (!projectId) return { name: 'describe' }
  return { name: 'project-results', params: { projectId }, query: { version: runId } }
}

function roleLabel(role: string): string {
  return role === 'user' ? '你' : '智能生图助手'
}

function formatMessageTime(createdAt: string): string {
  const date = new Date(createdAt)
  if (Number.isNaN(date.getTime())) return ''
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return hours + ':' + minutes
}

interface MessageSegment {
  type: 'text' | 'code'
  content: string
}

function messageSegments(content: string): MessageSegment[] {
  if (!content.includes('```')) return [{ type: 'text', content }]
  const segments: MessageSegment[] = []
  const fence = /```([^\n`]*)\n([\s\S]*?)```/g
  let cursor = 0
  let match: RegExpExecArray | null
  while ((match = fence.exec(content)) !== null) {
    if (match.index > cursor) {
      segments.push({ type: 'text', content: content.slice(cursor, match.index) })
    }
    segments.push({ type: 'code', content: match[2]!.replace(/\n$/, '') })
    cursor = match.index + match[0].length
  }
  if (cursor < content.length) {
    segments.push({ type: 'text', content: content.slice(cursor) })
  }
  return segments.length ? segments : [{ type: 'text', content }]
}

function attachmentAlt(attachment: { usage: string }): string {
  return `随消息发送的图片，用途：${attachment.usage}`
}

function applySuggestion(text: string): void {
  draft.value = text
  void nextTick(() => composerInput.value?.focus({ preventScroll: true }))
}

async function withBusyFlag(flag: Ref<boolean>, run: () => Promise<void>): Promise<void> {
  if (flag.value) return
  flag.value = true
  try {
    await run()
  } finally {
    flag.value = false
  }
}

async function retryProjectError(): Promise<void> {
  await withBusyFlag(retryingProject, () => retry())
}

async function confirmRequirementCard(): Promise<void> {
  if (busy.value || requirementSubmitting.value) return
  const projectAtStart = projectParam.value
  await withBusyFlag(requirementSubmitting, async () => {
    await confirmRequirements()
    if (projectParam.value === projectAtStart && !flowError.value && !canConfirmRequirements.value) {
      requirementConfirmed.value = true
    }
  })
}

function focusComposerOnFirstLoad(): void {
  if (initialComposerFocused) return
  if (projectParam.value || route.hash === '#composer') return
  initialComposerFocused = true
  void nextTick(() => composerInput.value?.focus({ preventScroll: true }))
}

watch(
  () => canConfirmRequirements.value,
  (available) => {
    if (available) requirementConfirmed.value = false
  },
  { flush: 'sync' },
)

watch(
  () => projectParam.value,
  () => {
    requirementConfirmed.value = false
    requirementSubmitting.value = false
  },
  { flush: 'sync' },
)

watch(
  () => projectMessages.value.map((message) => message.id),
  (_ids, previousIds) => {
    if (!pendingSend.value) return
    const known = new Set(previousIds ?? [])
    const arrived = projectMessages.value.some(
      (message) => message.role === 'user' && !known.has(message.id),
    )
    if (arrived) pendingSend.value = null
  },
)

const liveStatusInput = computed<LiveStatusInput>(() => {
  const projectId = projectParam.value
  const projectTitle = currentProject.value
    ? title.value
    : projectId
      ? '正在读取对话'
      : '新对话'
  const currentView = flow.workflow.view.value
  const progress = currentView?.task_state?.progress
  const base = { projectId, projectTitle, detail: '', busy: false }

  if (projectError.value) {
    return { ...base, label: '对话读取失败', detail: projectError.value, tone: 'error' }
  }
  if (projectLoading.value) {
    return { ...base, label: '正在读取对话', detail: '正在恢复消息和工作流状态…', tone: 'working' }
  }
  if (attachmentBusy.value) {
    return { ...base, label: '正在上传参考图', detail: '上传完成后才会发送这条消息。', tone: 'working', busy: true }
  }
  if (busy.value) {
    const label = progress?.stage_label ? `正在${progress.stage_label}` : '正在处理'
    return {
      ...base,
      label,
      detail: taskStatusText.value || '正在同步最新进度，无需重复发送。',
      tone: 'working',
      busy: true,
    }
  }
  if (canConfirmRequirements.value) {
    return { ...base, label: '等待确认需求', detail: '核对需求卡后，才会准备提示词。', tone: 'waiting' }
  }
  if (promptPreview.value) {
    return {
      ...base,
      label: '等待确认提示词',
      detail: promptPreview.value.can_confirm ? '确认真实提示词后才会提交生成。' : '提示词仍在检查，请稍候。',
      tone: 'waiting',
    }
  }
  if (isWorkingStatus.value) {
    return {
      ...base,
      label: progress?.stage_label ? `正在${progress.stage_label}` : '正在处理',
      detail: taskStatusText.value || '工作流仍在执行，状态会自动更新。',
      tone: 'working',
      busy: Boolean(progress?.status === 'active'),
    }
  }
  if (taskReasonLabel.value) {
    return {
      ...base,
      label: taskReasonLabel.value,
      detail: taskStatusText.value || '需要你确认后继续。',
      tone: taskReason.value?.severity === 'failed' ? 'error' : 'waiting',
    }
  }
  if (publishedAssetId.value) {
    return { ...base, label: '图片已就绪', detail: '可以下载原图，或在输入框继续描述修改。', tone: 'success' }
  }
  if (currentView?.status === 'completed') {
    return { ...base, label: '本轮已完成', detail: '在下方输入框可以继续下一轮调整。', tone: 'success' }
  }
  if (projectId) {
    return { ...base, label: '已就绪', detail: '在下方输入框继续描述商品和画面。', tone: 'idle' }
  }
  return { ...base, label: '准备开始', detail: '描述商品和画面，我会同步每一步进度。', tone: 'idle' }
})

watch(
  liveStatusInput,
  (next) => publishLiveStatus(next),
  { immediate: true, flush: 'post' },
)
</script>

<template>
  <section class="pure-chat" aria-label="生图对话">
    <div ref="scrollRegion" class="chat-scroll" aria-label="对话内容" @scroll.passive="onScroll">
      <div ref="chatContent" class="chat-content">
      <div v-if="!hasConversation" class="welcome">
        <div class="welcome-inner">
          <h1>发来商品图片，或告诉我你想做什么样的电商图片</h1>
          <p class="welcome-note">用一句话描述商品、用途和画面感觉即可。需要确认时，我会把要点整理给你。</p>
          <div v-if="!draft.trim()" class="suggestion-list" aria-label="推荐提示词">
            <button
              v-for="suggestion in suggestions"
              :key="suggestion"
              type="button"
              class="suggestion-button"
              @click="applySuggestion(suggestion)"
            >
              {{ suggestion }}
            </button>
          </div>
        </div>
      </div>

      <template v-else>
        <div v-if="projectLoading" class="chat-loading" role="status">正在读取对话…</div>
        <div v-else-if="projectError" class="chat-error chat-load-error" role="alert">
          <svg class="chat-error-icon" aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7.5v5.5M12 16.5v.5" />
          </svg>
          <p>{{ projectError }}</p>
          <button
            type="button"
            class="text-button"
            :disabled="retryingProject"
            :aria-busy="retryingProject"
            @click="retryProjectError"
          >
            重试
          </button>
        </div>

        <template v-else>
          <ol v-if="projectMessages.length || pendingSend" class="message-list" aria-label="对话记录">
            <li
              v-for="message in projectMessages"
              :key="message.id"
              v-memo="[message.id, message.content, message.attachment?.asset_id, message.attachment?.preview_url]"
              class="message-row"
              :class="message.role === 'user' ? 'is-user' : 'is-assistant'"
            >
              <div class="message-stack">
                <p class="message-meta">{{ roleLabel(message.role) }} · {{ formatMessageTime(message.created_at) }}</p>
                <div :class="message.role === 'user' ? 'message-bubble' : 'message-assistant'">
                  <template v-for="(segment, index) in messageSegments(message.content)" :key="index">
                    <p v-if="segment.type === 'text'" class="message-content">{{ segment.content }}</p>
                    <pre v-else class="message-code"><code>{{ segment.content }}</code></pre>
                  </template>
                  <div v-if="message.attachment" class="message-attachments">
                    <button
                      type="button"
                      class="message-attachment"
                      :aria-label="'查看' + ATTACHMENT_CAPTION"
                      @click="openLightbox({ assetId: message.attachment.asset_id, caption: ATTACHMENT_CAPTION }, $event)"
                    >
                      <img
                        class="message-attachment-thumb"
                        :src="message.attachment.preview_url"
                        :alt="attachmentAlt(message.attachment)"
                        width="34"
                        height="42"
                        loading="lazy"
                        decoding="async"
                        @load="onMediaLoad"
                      >
                      <span class="message-attachment-name">{{ ATTACHMENT_CAPTION }}</span>
                    </button>
                  </div>
                </div>
              </div>
            </li>
            <li v-if="pendingSend" class="message-row is-user is-pending">
              <div class="message-stack">
                <p class="message-meta">
                  你 · {{ formatMessageTime(pendingSend?.sentAt) }}<span class="pending-send-label"> · 发送中…</span>
                </p>
                <div class="message-bubble">
                  <p class="message-content">{{ pendingSend?.text }}</p>
                </div>
              </div>
            </li>
          </ol>

          <section
            v-if="taskTurnVisible"
            class="assistant-turn task-turn"
            :class="'is-' + taskVisualState"
            :data-state="taskVisualState"
            aria-label="任务状态"
          >
            <div class="task-status-card">
              <div class="task-status-line">
                <svg
                  class="task-status-icon"
                  :class="'is-' + taskVisualState"
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.5"
                >
                  <template v-if="taskVisualState === 'failed'">
                    <path d="M12 3 2.5 20h19L12 3Z" />
                    <path d="M12 9v5" />
                    <path d="M12 17h.01" />
                  </template>
                  <template v-else-if="taskVisualState === 'recovered'">
                    <circle cx="12" cy="12" r="9" />
                    <path d="m8.5 12 2.5 2.5 4.5-5" />
                  </template>
                  <template v-else>
                    <circle cx="12" cy="12" r="9" />
                    <path d="M12 11v5" />
                    <path d="M12 8h.01" />
                  </template>
                </svg>
                <div class="task-status-copy">
                  <p v-if="taskVisualState === 'recovered'" class="task-status-heading">已恢复</p>
                  <p
                    v-else-if="taskVisualState === 'unknown'"
                    class="task-reason-label"
                    :data-category="taskReason?.category"
                  ><span class="task-reason-label-text">{{ taskReasonLabel || '结果未知' }}</span><span>，正在核实</span></p>
                  <p
                    v-else-if="taskReasonLabel"
                    class="task-reason-label"
                    :data-category="taskReason?.category"
                  >{{ taskReasonLabel }}</p>
                  <p v-if="taskDisplayText" class="task-status-text">{{ taskDisplayText }}</p>
                </div>
              </div>
              <div v-if="taskActions.length" class="task-actions">
                <p v-if="!isWorkingStatus && !taskActionPending" class="task-actions-note">安全恢复：以下操作只使用当前服务端允许的动作，不会自动提交新的生成请求，也不会自动新增受理次数。</p>
                <div v-for="action in taskActions" :key="action.id" class="task-action">
                  <div class="task-action-head">
                    <button
                      type="button"
                      class="task-action-button"
                      :disabled="taskActionDisabled(action)"
                      @click="onTaskAction(action)"
                    >
                      {{ action.label }}
                    </button>
                    <span class="task-effect">{{ taskActionEffectLabel(action) }}</span>
                  </div>
                  <p class="task-help">{{ taskActionHelp(action) }}</p>
                  <p v-if="taskCountNote(action)" class="task-count">{{ taskCountNote(action) }}</p>
                  <p v-if="action.requires_text" class="task-text-note">
                    请在下方输入框说明{{ action.text_label || '补充内容' }}，然后点击发送。
                  </p>
                  <label v-if="action.requires_confirmation" class="task-confirm">
                    <input v-model="taskConfirmations[action.id]" class="task-confirm-box" type="checkbox">
                    <span>{{ taskConfirmationText(action) }}</span>
                  </label>
                </div>
              </div>
            </div>
          </section>

          <section
            v-if="canConfirmRequirements || requirementConfirmed || requirementSubmitting"
            class="assistant-turn requirement-turn"
            aria-label="需求确认"
          >
            <h2 class="requirement-title">需求确认</h2>
            <p class="assistant-lead">我先把你的描述整理成了这份需求，确认后我会准备提示词。</p>
            <dl class="requirement-list">
              <template v-for="row in requirementRows" :key="row.label">
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value }}</dd>
              </template>
            </dl>
            <p class="usage-note">{{ usageText }}</p>
            <button
              v-if="!requirementConfirmed"
              type="button"
              class="requirement-cta"
              :disabled="busy || requirementSubmitting"
              @click="confirmRequirementCard"
            >
              确认需求并生成提示词
            </button>
            <p v-else class="requirement-status" role="status">
              <svg
                class="requirement-status-icon"
                aria-hidden="true"
                viewBox="0 0 24 24"
                width="16"
                height="16"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
              >
                <circle cx="12" cy="12" r="9" />
                <path d="m8.5 12 2.5 2.5 4.5-5" />
              </svg>
              <span class="requirement-status-text">需求已确认</span>
              <span class="requirement-status-note">正在准备提示词…</span>
            </p>
          </section>

          <section
            v-if="promptCardVisible"
            class="assistant-turn prompt-turn"
            aria-label="提示词确认"
          >
            <p class="message-role">智能生图助手</p>
            <p class="assistant-lead">提示词已经准备好。下面就是实际会交给图片模型的内容，确认后才会生成图片。</p>
            <div
              class="prompt-card"
              :class="{ 'is-narrow': isMobile, 'is-confirmed': promptConfirmed }"
            >
              <header class="prompt-card-head">
                <h3 class="prompt-card-title">实际提示词</h3>
                <span class="prompt-card-version">{{ promptVersionLabel }}</span>
                <span class="prompt-card-pill" :class="'is-' + promptStatus">
                  <svg
                    v-if="promptStatus === 'confirmed'"
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                  >
                    <circle cx="12" cy="12" r="9" />
                    <path d="M12 7v5l3 2" />
                  </svg>
                  <svg
                    v-else-if="promptStatus === 'checking'"
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                  >
                    <circle cx="12" cy="12" r="9" />
                    <path d="M12 11v5" />
                    <path d="M12 8h.01" />
                  </svg>
                  <svg
                    v-else
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                  >
                    <path d="M12 3 2.5 20h19L12 3Z" />
                    <path d="M12 9v5" />
                    <path d="M12 17h.01" />
                  </svg>
                  <span>{{ promptStatusLabel }}</span>
                </span>
              </header>

              <div class="prompt-card-body">
                <blockquote class="prompt-card-quote">{{ promptCardPositive }}</blockquote>

                <div v-if="promptCardNegative" class="prompt-card-negative">
                  <svg
                    class="prompt-card-danger-icon"
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                  >
                    <path d="M12 3 2.5 20h19L12 3Z" />
                    <path d="M12 9v5" />
                    <path d="M12 17h.01" />
                  </svg>
                  <div class="prompt-card-negative-body">
                    <span class="prompt-card-negative-label">避免</span>
                    <p class="prompt-card-negative-text">{{ promptCardNegative }}</p>
                  </div>
                </div>

                <dl v-if="promptCardOutput || promptCardReference" class="prompt-card-facts">
                  <div v-if="promptCardOutput" class="prompt-card-fact">
                    <dt>输出</dt>
                    <dd>{{ promptCardOutput }}</dd>
                  </div>
                  <div v-if="promptCardReference" class="prompt-card-fact">
                    <dt>参考图</dt>
                    <dd>{{ promptCardReference }}</dd>
                  </div>
                </dl>

                <p v-if="promptStateLabel && !promptConfirmed" class="prompt-card-note">{{ promptStateLabel }}</p>

                <div v-if="!promptConfirmed" class="prompt-card-actions">
                  <button type="button" class="prompt-copy" @click="copyPrompt">
                    {{ copied ? '已复制' : '复制提示词' }}
                  </button>
                  <button
                    type="button"
                    class="prompt-cta"
                    :disabled="!promptCanConfirm"
                    @click="onConfirmPrompt"
                  >
                    {{ promptCtaLabel }}
                  </button>
                </div>
              </div>
              <p v-if="promptConfirmed" class="prompt-card-execution" role="status">
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.5"
                >
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 11v5" />
                  <path d="M12 8h.01" />
                </svg>
                <span>{{ promptExecutionStatus }}</span>
              </p>
            </div>
          </section>

          <section
            v-if="resultUnavailableText"
            class="assistant-turn result-turn"
            aria-label="当前结果不可用"
          >
            <div class="task-status-card result-unavailable-card" role="status">
              <svg
                class="task-status-icon is-failed"
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
              >
                <path d="M12 3 2.5 20h19L12 3Z" />
                <path d="M12 9v5" />
                <path d="M12 17h.01" />
              </svg>
              <p class="result-unavailable-card-text">{{ resultUnavailableText }}</p>
            </div>
          </section>

          <section v-if="imageHistory.length" class="assistant-turn image-history" aria-label="图片历史">
            <div class="message-bubble history-bubble">
              <p class="message-role">智能生图助手</p>
              <p class="history-lead">图片和当时实际使用的提示词都按版本保留。默认继续使用最新版本，查看旧版本不会改变修改基准。</p>
            </div>
            <div class="result-grid">
              <article
                v-for="item in imageHistory"
                :key="item.runId"
                class="message-bubble result-card"
                :class="{ 'is-clickable': item.assetId }"
                :aria-label="'版本 ' + item.sequenceNo + ' 结果'"
                :tabindex="item.assetId ? 0 : undefined"
                @click="onResultCardClick(item, $event)"
                @keydown="onResultCardKeydown(item, $event)"
              >
                <a
                  v-if="item.assetId"
                  class="result-media-link"
                  :href="assetUrl(item.assetId)"
                  target="_blank"
                  rel="noopener"
                  :aria-label="'查看大图：' + item.caption"
                  @click.prevent="openLightbox(item, $event)"
                  @keydown.space.prevent="openLightbox(item, $event)"
                >
                  <img
                    class="result-media"
                    :src="assetUrl(item.assetId)"
                    :alt="item.caption"
                    :width="item.width ?? undefined"
                    :height="item.height ?? undefined"
                    loading="lazy"
                    decoding="async"
                    @load="onMediaLoad"
                  >
                  <span class="result-media-hint">查看大图</span>
                </a>
                <div v-else class="result-unavailable" role="status">
                  <svg class="result-unavailable-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="3" y="4" width="18" height="16" rx="2" />
                    <path d="m4 16 5-5 4 4 2-2 5 5M4 6l16 12" />
                  </svg>
                  <p class="result-unavailable-text">该版本图片不可用</p>
                </div>
                <p class="result-heading">
                  <span class="result-badge" :class="{ 'is-current': item.isCurrent, 'is-latest': !item.isCurrent && item.isLatest }">
                    {{ item.isCurrent ? '当前版本' : item.isLatest ? '最新已发布' : '历史版本' }}
                  </span>
                  <span class="result-heading-text">版本 {{ item.sequenceNo }} · 需求 v{{ item.requirementRevision }}</span>
                </p>
                <p class="result-title">{{ item.caption }}</p>
                <p v-if="item.width && item.height" class="result-caption">{{ item.width }}×{{ item.height }}</p>
                <p v-if="item.isCurrent && item.assetId" class="message-content result-status">图片已经生成，可以保存使用了。</p>
                <details v-if="item.prompt" class="result-prompt">
                  <summary>该版本实际使用的已批准提示词</summary>
                  <pre v-if="item.prompt.positive">{{ item.prompt.positive }}</pre>
                  <template v-if="item.prompt.negative">
                    <h4>需要避免</h4>
                    <pre>{{ item.prompt.negative }}</pre>
                  </template>
                </details>
                <details v-else class="result-prompt">
                  <summary>该版本提示词不可用</summary>
                  <p>该版本未保存可展示的已批准提示词；系统不会用当前提示词替代。</p>
                </details>
                <div class="result-actions">
                  <a
                    v-if="item.canDownload && item.assetId"
                    class="primary-action result-download"
                    :href="assetUrl(item.assetId) + '?download=true'"
                    download
                  >
                    下载原图
                  </a>
                  <button
                    v-else
                    type="button"
                    class="result-download result-download-disabled"
                    disabled
                    aria-disabled="true"
                  >
                    下载原图
                  </button>
                  <RouterLink class="text-button" :to="resultPageRoute(item.runId)">
                    在结果页只读查看
                  </RouterLink>
                </div>
                <p class="result-time">
                  发布于 <time :datetime="item.publishedAt || item.createdAt">{{ formatDateTime(item.publishedAt || item.createdAt) }}</time>
                </p>
              </article>
            </div>
          </section>
        </template>
      </template>
      </div>
    </div>

    <footer ref="composerArea" class="composer-area">
      <div v-if="flowError" class="chat-error composer-error" role="alert">
        <p>{{ flowError }}</p>
        <button type="button" class="text-button" @click="retry">重试</button>
      </div>
      <div v-if="attachmentError" class="chat-error composer-error" role="alert">
        <p>{{ attachmentError }}</p>
      </div>
      <p v-if="pendingTaskAction" class="pending-task-note" role="status">
        正在修改：{{ pendingTaskAction.label }}。填写{{ pendingTaskAction.text_label || '补充内容' }}后点击发送。
      </p>
      <div v-if="pendingAttachment" class="attachment-preview" aria-label="待发送的图片">
        <img
          v-if="!attachmentBusy"
          class="attachment-thumb"
          :src="pendingAttachment.previewUrl"
          alt="待发送的图片预览"
        >
        <div class="attachment-preview-copy">
          <strong>{{ attachmentFileName }}</strong>
          <span v-if="attachmentBusy">正在上传…</span>
          <span v-else>将随这条消息一起发送</span>
        </div>
        <template v-if="!attachmentBusy">
          <button
            type="button"
            class="attachment-action"
            aria-label="替换图片"
            title="替换图片"
            @click="openAttachmentPicker"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
              <path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17v3ZM13.5 7.5l3 3" />
            </svg>
          </button>
          <button
            type="button"
            class="attachment-remove"
            aria-label="移除图片"
            title="移除图片"
            @click="removeAttachment"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </template>
      </div>
      <div
        id="composer"
        class="composer-shell"
        @dragover.prevent
        @drop.prevent="onDrop"
      >
        <input
          ref="attachmentInput"
          class="file-input"
          type="file"
          tabindex="-1"
          accept="image/png,image/jpeg"
          aria-label="选择参考图文件"
          :disabled="busy || attachmentBusy"
          @change="onAttachmentChange"
        >
        <button
          type="button"
          class="attachment-button"
          aria-label="添加参考图"
          :disabled="busy || attachmentBusy"
          @click="openAttachmentPicker"
        >
          <span aria-hidden="true">+</span>
        </button>
        <label class="sr-only" for="workspace-prompt">描述商品与图片需求</label>
        <textarea
          id="workspace-prompt"
          ref="composerInput"
          v-model="draft"
          rows="1"
          class="composer-input"
          aria-label="描述商品与图片需求"
          :placeholder="pendingTaskAction ? '说明要修改的地方，然后发送…' : '描述商品、用途和画面感觉…'"
          :disabled="attachmentBusy"
          @keydown="onComposerKeydown"
          @input="onComposerInput"
          @paste="onPaste"
          @compositionstart="composing = true"
          @compositionend="composing = false"
        />
        <button
          type="button"
          class="send-button"
          aria-label="发送"
          :aria-busy="busy"
          :title="busy ? '处理中…' : '发送'"
          :data-label="busy ? '处理中…' : '发送'"
          :disabled="sendDisabled"
          @click="submit"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 19V5M6 11l6-6 6 6" />
          </svg>
        </button>
      </div>
      <p class="composer-hint">{{ busy ? '正在生成，完成后可发送' : 'Enter 发送，Shift + Enter 换行' }}</p>
    </footer>

    <Transition name="float-pop">
      <button
        v-if="newContentAvailable"
        type="button"
        class="new-content-button"
        :style="{ bottom: footerHeight + 12 + 'px' }"
        @click="scrollToLatest('smooth')"
      >
        <span class="new-content-dot" aria-hidden="true" />
        有新内容，回到底部
      </button>
    </Transition>
    <ImageLightbox
      :open="lightboxOpen"
      :src="lightboxItem ? assetUrl(lightboxItem.assetId) : ''"
      :alt="lightboxItem?.caption ?? ''"
      @close="closeLightbox"
    />
  </section>
</template>

<style scoped>
.pure-chat {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  max-width: 100%;
  background: transparent;
  border-radius: 0;
  overflow: hidden;
}
.chat-scroll {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 34px clamp(20px, 5vw, 56px) 24px;
  overflow-anchor: none;
  overscroll-behavior-y: contain;
  scrollbar-color: var(--line-hairline) transparent;
  scrollbar-width: thin;
}
.chat-scroll::-webkit-scrollbar { width: 8px; }
.chat-scroll::-webkit-scrollbar-track { background: transparent; }
.chat-scroll::-webkit-scrollbar-thumb {
  border-radius: var(--radius-pill);
  background: var(--line-hairline);
}
.chat-scroll::-webkit-scrollbar-thumb:hover { background: var(--line-hover); }
.chat-content { display: flex; flex-direction: column; min-height: 100%; }
.welcome {
  display: flex;
  flex: 1 0 auto;
  flex-direction: column;
  align-items: stretch;
  min-height: 100%;
  padding-top: max(0px, calc(38vh - 82px));
}
.welcome-inner {
  width: min(var(--conversation-max), 100%);
  margin: 0 auto;
}
.welcome h1 {
  margin: 0;
  color: var(--text-primary);
  font-size: var(--text-display);
  font-weight: 600;
  line-height: var(--text-display-line);
  letter-spacing: -.025em;
}
.welcome-note {
  margin: var(--space-3) 0 0;
  color: var(--text-secondary);
  font-size: var(--text-reading);
  line-height: var(--text-reading-line);
}
.suggestion-list {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-8);
}
.suggestion-button {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: calc(var(--space-10) + var(--space-1));
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-sm);
  background: var(--surface-panel);
  color: var(--text-primary);
  font-size: var(--text-body);
  font-weight: 500;
  line-height: var(--text-body-line);
  text-align: left;
  transition: border-color var(--dur-fast) var(--ease-out);
}
.suggestion-button:hover { border-color: var(--line-hover); }
.suggestion-button:focus-visible {
  outline: 2px solid var(--focus-ring-light);
  outline-offset: 2px;
}
@media (min-width: 768px) {
  .suggestion-list {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: var(--space-3);
  }
}
.chat-loading {
  padding: var(--space-12) 0;
  color: var(--text-muted);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
  text-align: center;
}
.message-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  width: min(var(--conversation-width), 100%);
  margin: 0 auto;
  padding: 0;
  list-style: none;
}
.message-row { display: flex; min-width: 0; width: 100%; }
.message-row.is-user { justify-content: flex-end; }
.message-row.is-assistant { justify-content: flex-start; }
.message-row + .message-row { margin-top: var(--space-3); }
.message-row.is-user + .message-row.is-assistant,
.message-row.is-assistant + .message-row.is-user { margin-top: var(--space-6); }
.message-stack {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.is-user .message-stack {
  align-items: flex-end;
  width: auto;
  max-width: 78%;
}
.is-assistant .message-stack {
  align-items: flex-start;
  width: 92%;
  max-width: 92%;
}
.message-meta {
  margin: 0 0 calc(var(--space-3) / 2);
  color: var(--text-muted);
  font-size: var(--text-caption);
  font-variant-numeric: tabular-nums;
  line-height: var(--text-caption-line);
}
.is-user .message-meta { text-align: right; }
.message-bubble {
  box-sizing: border-box;
  max-width: 100%;
  min-width: 0;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-md);
  background: var(--surface-inset);
  color: var(--text-primary);
}
.message-assistant {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  padding: 0;
  background: transparent;
}
.message-content {
  margin: 0;
  font-size: var(--text-reading);
  line-height: var(--text-reading-line);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.message-code {
  box-sizing: border-box;
  max-width: 100%;
  margin: var(--space-2) 0;
  padding: var(--space-3) var(--space-4);
  overflow-x: auto;
  border-radius: var(--radius-sm);
  background: var(--surface-inset);
  font-family: var(--font-mono);
  font-size: calc(var(--text-caption) + 1px);
  line-height: calc(var(--text-caption-line) + 2px);
  white-space: pre;
}
.message-code code { font: inherit; }
.message-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: calc(var(--space-3) / 2);
  max-width: 100%;
  margin: var(--space-2) 0 0;
}
.message-attachment {
  display: flex;
  flex: 0 1 auto;
  align-items: center;
  gap: calc(var(--space-3) / 2);
  max-width: calc((100% - 3 * (var(--space-3) / 2)) / 4);
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  text-align: left;
}
.message-attachment-thumb {
  flex: none;
  width: calc(var(--space-8) + var(--space-1) / 2);
  height: calc(var(--space-10) + var(--space-1) / 2);
  border-radius: var(--radius-sm);
  background: var(--surface-media);
  object-fit: cover;
}
.message-attachment-name {
  min-width: 0;
  overflow: hidden;
  color: var(--text-secondary);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pending-send-label { color: var(--text-muted); }
.assistant-turn { width: min(var(--conversation-width), 100%); margin: 24px auto 0; }
.assistant-lead { margin: 0 0 16px; line-height: 1.7; }
.requirement-turn {
  box-sizing: border-box;
  margin: var(--space-6) auto;
  padding: var(--space-4);
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-md);
  background: var(--surface-panel);
}
.requirement-title {
  margin: 0 0 8px;
  color: var(--text-primary);
  font-size: var(--text-section);
  font-weight: 600;
  line-height: var(--text-section-line);
}
.requirement-turn .assistant-lead {
  color: var(--text-secondary);
  font-size: var(--text-reading);
  line-height: var(--text-reading-line);
}
.requirement-cta {
  height: calc(var(--space-8) + var(--space-1));
  padding: 0 var(--space-4);
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--action-primary);
  color: var(--action-primary-text);
  font-size: var(--text-body);
  font-weight: 500;
  line-height: var(--text-body-line);
}
.requirement-cta:hover:not(:disabled) { background: var(--action-primary-hover); }
.requirement-cta:disabled {
  cursor: not-allowed;
  background: var(--surface-inset);
  color: var(--text-muted);
}
.requirement-status {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  min-height: calc(var(--space-8) + var(--space-1));
  margin: 0;
  color: var(--text-secondary);
  font-size: var(--text-body);
  line-height: var(--text-body-line);
}
.requirement-status-icon { flex: none; color: var(--state-success); }
.requirement-status-text { color: var(--state-success); }
.requirement-status-note { color: var(--text-muted); }
.requirement-list, .prompt-meta { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 8px 18px; margin: 0 0 18px; }
.requirement-list dt, .prompt-meta dt { color: var(--text-muted); font-size: 13px; }
.requirement-list dd, .prompt-meta dd { margin: 0; overflow-wrap: anywhere; }
.usage-note { margin: 0 0 14px; color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.primary-action {
  min-height: 44px;
  padding: 0 22px;
  border: 0;
  border-radius: 999px;
  background: var(--action-primary);
  color: var(--action-primary-text);
  font-weight: 600;
}
.primary-action { transition: background-color .2s ease, transform .18s var(--ease-out), opacity .2s ease; }
.primary-action:hover:not(:disabled) { background: var(--action-primary-hover); }
.primary-action:active:not(:disabled) { transform: scale(.975); }
.primary-action:disabled { cursor: not-allowed; opacity: .55; }
.prompt-card {
  margin: 0 0 var(--space-4);
  padding: var(--space-4);
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-md);
  background: var(--surface-panel);
}
.prompt-card-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}
.prompt-card-title {
  margin: 0;
  color: var(--text-primary);
  font-size: var(--text-section);
  line-height: var(--text-section-line);
  font-weight: 600;
}
.prompt-card-version {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: calc(var(--text-caption) + 1px);
  line-height: calc(var(--text-caption-line) + 2px);
}
.prompt-card-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  margin-left: auto;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--state-warning) 9%, var(--surface-panel));
  color: var(--state-warning);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
  font-weight: 600;
  white-space: nowrap;
}
.prompt-card-pill svg { flex: none; width: var(--icon-sm); height: var(--icon-sm); }
.prompt-card-pill.is-confirmed {
  background: color-mix(in srgb, var(--state-success) 9%, var(--surface-panel));
  color: var(--state-success);
}
.prompt-card-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  max-height: 480px;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: thin;
  scrollbar-color: var(--line-hover) transparent;
}
.prompt-card-body::-webkit-scrollbar { width: var(--space-2); }
.prompt-card-body::-webkit-scrollbar-thumb { border-radius: var(--radius-pill); background: var(--line-hover); }
.prompt-card-quote {
  margin: 0;
  padding: calc(var(--space-3) + 2px) var(--space-4);
  border-left: 2px solid var(--line-hover);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: var(--surface-inset);
  color: var(--text-primary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: var(--font-mono);
  font-size: var(--text-reading);
  line-height: var(--text-reading-line);
}
.prompt-card-negative {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--surface-inset);
}
.prompt-card-danger-icon { flex: none; width: var(--icon-sm); height: var(--icon-sm); margin-top: var(--space-1); color: var(--state-danger); }
.prompt-card-negative-body { min-width: 0; }
.prompt-card-negative-label { color: var(--text-muted); font-size: var(--text-caption); line-height: var(--text-caption-line); font-weight: 600; }
.prompt-card-negative-text {
  margin: var(--space-1) 0 0;
  color: var(--text-secondary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: var(--font-mono);
  font-size: var(--text-reading);
  line-height: var(--text-reading-line);
}
.prompt-card-facts {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr) max-content minmax(0, 1fr);
  gap: var(--space-4);
  margin: 0;
}
.prompt-card.is-narrow .prompt-card-facts { grid-template-columns: max-content minmax(0, 1fr); }
.prompt-card-fact { display: contents; }
.prompt-card-facts dt { color: var(--text-muted); font-size: var(--text-caption); line-height: var(--text-caption-line); }
.prompt-card-facts dd { margin: 0; overflow-wrap: anywhere; color: var(--text-primary); font-size: var(--text-body); line-height: var(--text-body-line); }
.prompt-card-note { margin: 0; color: var(--text-muted); font-size: var(--text-caption); line-height: var(--text-caption-line); }
.prompt-card-actions {
  position: sticky;
  bottom: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  background: var(--surface-panel);
}
.prompt-card.is-narrow .prompt-card-actions { flex-direction: column; align-items: stretch; }
.prompt-card.is-narrow .prompt-copy { align-self: flex-start; }
.prompt-card.is-narrow .prompt-cta { width: 100%; }
.prompt-copy {
  min-height: calc(var(--space-8) + var(--space-1));
  padding: 0 var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--action-primary);
  font-size: var(--text-body);
  line-height: var(--text-body-line);
}
.prompt-copy:hover:not(:disabled) { background: var(--action-soft); }
.prompt-copy:disabled { cursor: not-allowed; color: var(--text-muted); }
.prompt-cta {
  min-height: calc(var(--space-8) + var(--space-1));
  padding: 0 var(--space-4);
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--action-primary);
  color: var(--action-primary-text);
  font-size: var(--text-body);
  font-weight: 600;
  transition: background-color var(--dur-base) var(--ease-out), transform var(--dur-fast) var(--ease-out);
}
.prompt-cta:hover:not(:disabled) { background: var(--action-primary-hover); }
.prompt-cta:active:not(:disabled) { transform: scale(.98); }
.prompt-cta:disabled { cursor: not-allowed; background: var(--surface-inset); color: var(--text-muted); opacity: 1; }
.prompt-copy:focus-visible, .prompt-cta:focus-visible { outline: 2px solid var(--focus-ring-light); outline-offset: 2px; }
.prompt-card-execution {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-4) 0 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--surface-inset);
  color: var(--text-secondary);
  font-size: var(--text-body);
  line-height: var(--text-body-line);
}
.prompt-card-execution svg { flex: none; width: var(--icon-sm); height: var(--icon-sm); color: var(--state-info); }
.text-button { padding: 2px 0; border: 0; background: transparent; color: var(--action-primary); font-size: 13px; }
.text-button:hover:not(:disabled) { text-decoration: underline; }
.text-button:disabled { cursor: not-allowed; color: var(--text-muted); }
.task-status-card {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  box-sizing: border-box;
  width: 100%;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--surface-inset);
  color: var(--text-secondary);
  font-size: var(--text-body);
  line-height: var(--text-body-line);
}
.task-status-line { display: flex; align-items: flex-start; gap: var(--space-2); min-width: 0; }
.task-status-icon { flex: none; width: var(--icon-sm); height: var(--icon-sm); margin-top: 3px; color: var(--state-info); }
.task-status-icon.is-executing { color: var(--state-info); }
.task-status-icon.is-unknown { color: var(--state-info); }
.task-status-icon.is-failed { color: var(--state-danger); }
.task-status-icon.is-recovered { color: var(--state-success); }
.task-status-copy { display: flex; flex-direction: column; gap: var(--space-1); min-width: 0; }
.task-status-heading { margin: 0; color: var(--state-success); font-size: var(--text-body); line-height: var(--text-body-line); font-weight: 600; }
.task-status-text { margin: 0; color: var(--text-secondary); font-size: var(--text-body); line-height: var(--text-body-line); overflow-wrap: anywhere; }
.task-reason-label { margin: 0; color: var(--text-primary); font-size: var(--text-body); line-height: var(--text-body-line); font-weight: 600; }
.task-actions { display: flex; align-self: stretch; flex-direction: column; gap: var(--space-3); width: 100%; margin-top: var(--space-3); }
.task-actions-note { margin: 0; color: var(--text-muted); font-size: var(--text-caption); line-height: var(--text-caption-line); }
.task-action { display: flex; flex-direction: column; gap: var(--space-2); padding-top: var(--space-3); border-top: 1px solid var(--line-hairline); }
.task-action-head { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); }
.task-action-button {
  min-height: calc(var(--space-8) + var(--space-1));
  padding: 0 var(--space-4);
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-pill);
  background: var(--surface-panel);
  color: var(--text-primary);
  font-size: var(--text-body);
  transition: border-color var(--dur-base) var(--ease-out), color var(--dur-base) var(--ease-out), background-color var(--dur-base) var(--ease-out), transform var(--dur-fast) var(--ease-out);
}
.task-action-button:hover:not(:disabled) { border-color: var(--action-primary); color: var(--action-primary); background: var(--action-soft); }
.task-action-button:active:not(:disabled) { transform: scale(.97); }
.task-action-button:disabled { cursor: not-allowed; opacity: .5; }
.task-effect { padding: 2px var(--space-2); border-radius: var(--radius-pill); background: var(--action-soft); color: var(--action-primary); font-size: var(--text-caption); }
.task-help, .task-count, .task-text-note { margin: 0; color: var(--text-muted); font-size: var(--text-caption); line-height: var(--text-caption-line); }
.task-count { color: var(--text-primary); }
.task-confirm { display: flex; align-items: flex-start; gap: var(--space-2); color: var(--text-primary); font-size: var(--text-body); line-height: var(--text-body-line); }
.task-confirm-box {
  appearance: none;
  -webkit-appearance: none;
  flex: none;
  box-sizing: border-box;
  width: 20px;
  height: 20px;
  margin: 1px 0 0;
  border: 1px solid var(--line-control);
  border-radius: var(--radius-sm);
  background: var(--surface-panel);
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out);
}
.task-confirm-box:checked {
  border-color: var(--action-primary);
  background: var(--action-primary);
}
.task-confirm-box:checked::after {
  content: '';
  display: block;
  width: 16px;
  height: 16px;
  background: var(--action-primary-text);
  -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M3 8.5 6.5 12 13 4.5' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center / 16px 16px no-repeat;
  mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M3 8.5 6.5 12 13 4.5' fill='none' stroke='black' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center / 16px 16px no-repeat;
}
.task-confirm-box:focus-visible { outline: 2px solid var(--focus-ring-light); outline-offset: 2px; }
.pending-task-note { width: min(var(--conversation-width), 100%); margin: 0 auto 8px; color: var(--action-primary); font-size: 13px; }
.result-unavailable-card { flex-direction: row; align-items: flex-start; gap: var(--space-2); }
.result-unavailable-card .task-status-icon { margin-top: 3px; color: var(--state-danger); }
.result-unavailable-card-text { margin: 0; color: var(--text-secondary); font-size: var(--text-body); line-height: var(--text-body-line); }
@media (prefers-reduced-motion: reduce) {
  .task-status-card,
  .task-status-icon,
  .task-action-button,
  .task-confirm-box { transition: none; animation: none; }
}
.image-history { display: flex; flex-direction: column; gap: var(--space-4); }
.history-bubble { width: 100%; }
.history-lead { margin: 0; color: var(--text-muted); font-size: 13px; line-height: 1.6; }
.result-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; width: 100%; }
.result-card {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  width: 100%;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-md);
  background: var(--surface-panel);
  color: var(--text-primary);
  transition: border-color var(--dur-fast) var(--ease-out);
}
.result-card.is-clickable { cursor: pointer; }
.result-card:hover { border-color: var(--line-hover); }
.result-card:focus-within { outline: 2px solid var(--focus-ring-light); outline-offset: 2px; }
.result-card:focus-visible { outline: 2px solid var(--focus-ring-light); outline-offset: 2px; }
.result-media-link { position: relative; display: block; overflow: hidden; border-radius: var(--radius-sm); background: var(--surface-media); }
.result-media { display: block; width: 100%; height: auto; aspect-ratio: 3 / 4; border-radius: var(--radius-sm); background: var(--surface-media); object-fit: cover; }
.result-media-hint {
  position: absolute;
  right: 10px;
  bottom: 10px;
  padding: 4px 10px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-pill);
  background: var(--surface-panel);
  color: var(--text-secondary);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
}
.result-heading { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); margin: var(--space-3) 0 0; color: var(--text-secondary); font-size: var(--text-caption); line-height: var(--text-caption-line); font-weight: 500; }
.result-heading-text { min-width: 0; }
.result-badge {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 0 8px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-pill);
  background: var(--surface-panel);
  color: var(--text-secondary);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
  font-weight: 500;
  white-space: nowrap;
}
.result-badge.is-current { border-color: var(--line-hover); background: var(--action-soft); color: var(--action-primary); }
.result-badge.is-latest { border-color: var(--line-hover); color: var(--text-primary); }
.result-title { margin: var(--space-2) 0 0; color: var(--text-primary); font-size: var(--text-body); line-height: var(--text-body-line); font-weight: 500; overflow-wrap: anywhere; }
.result-caption { margin: 2px 0 0; color: var(--text-muted); font-size: var(--text-caption); line-height: var(--text-caption-line); overflow-wrap: anywhere; }
.result-status { margin-top: 10px; }
.result-prompt { margin-top: 12px; padding: 10px 12px; border: 1px solid var(--line-hairline); border-radius: 10px; background: var(--surface-panel); }
.result-prompt summary { cursor: pointer; font-size: 13px; font-weight: 600; }
.result-prompt pre { margin: 10px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.6 var(--font-mono); }
.result-prompt h4 { margin: 12px 0 4px; font-size: 12px; color: var(--text-muted); }
.result-prompt p { margin: 8px 0 0; color: var(--text-muted); font-size: 13px; }
.result-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: var(--space-3); }
.result-download { display: inline-flex; align-items: center; justify-content: center; min-height: 44px; padding: 0 18px; font-size: var(--text-body); text-decoration: none; }
.result-download-disabled { border: 1px solid var(--line-hairline); border-radius: var(--radius-pill); background: var(--surface-inset); color: var(--text-muted); opacity: 1; cursor: not-allowed; }
.result-download:disabled { border: 1px solid var(--line-hairline); border-radius: var(--radius-pill); background: var(--surface-inset); color: var(--text-muted); opacity: 1; cursor: not-allowed; }
.result-download-disabled:disabled { opacity: 1; color: var(--text-muted); }
.result-time { margin: 10px 0 0; color: var(--text-muted); font-size: 12px; }
.result-unavailable {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  aspect-ratio: 3 / 4;
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--surface-inset);
  color: var(--text-secondary);
  text-align: center;
}
.result-unavailable-icon { width: 32px; height: 32px; color: var(--text-muted); }
.result-unavailable-text { margin: 0; color: var(--text-secondary); font-size: var(--text-caption); line-height: var(--text-caption-line); }
@media (min-width: 768px) {
  .result-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
}
figcaption { color: var(--text-muted); font-size: 13px; }
.chat-error { width: min(var(--conversation-width), 100%); margin: 0 auto 20px; padding: 12px 14px; border: 1px solid color-mix(in srgb, var(--state-danger) 35%, var(--line-hairline)); border-radius: 12px; background: color-mix(in srgb, var(--state-danger) 6%, var(--surface-panel)); color: var(--state-danger); }
.chat-error p { margin: 0 0 4px; }
.chat-load-error {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  width: 100%;
  max-width: 640px;
  margin: 0 auto;
  padding: var(--space-4) var(--space-5);
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-md);
  background: var(--surface-inset);
  color: var(--text-secondary);
}
.chat-load-error p {
  margin: 0;
  color: var(--text-secondary);
  font-size: var(--text-body);
  line-height: var(--text-body-line);
}
.chat-error-icon { flex: none; color: var(--state-danger); }
.chat-load-error .text-button {
  color: var(--action-link);
  font-size: var(--text-body);
  line-height: var(--text-body-line);
  white-space: nowrap;
}
.chat-load-error .text-button:disabled {
  cursor: not-allowed;
  color: var(--text-muted);
}
.attachment-preview {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  min-width: 0;
  min-height: 58px;
  width: min(var(--conversation-width), 100%);
  margin: 0 auto 8px;
  padding: 8px 12px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-md);
  background: var(--surface-inset);
}
.attachment-thumb {
  flex: none;
  width: 34px;
  height: 42px;
  border-radius: var(--radius-sm);
  background: var(--surface-media);
  object-fit: cover;
}
.attachment-preview-copy { display: flex; flex: 1 1 auto; min-width: 0; flex-direction: column; gap: 2px; }
.attachment-preview-copy strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
}
.attachment-preview-copy span { color: var(--text-muted); font-size: var(--text-caption); line-height: var(--text-caption-line); }
.attachment-action {
  display: grid;
  flex: none;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
}
.attachment-remove {
  display: grid;
  flex: none;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--state-danger);
}
.attachment-action:hover:not(:disabled) { background: var(--surface-panel); color: var(--text-primary); }
.attachment-remove:hover:not(:disabled) { background: var(--surface-panel); }
.file-input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.attachment-button {
  display: grid;
  flex: none;
  place-items: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
}
.attachment-button:hover:not(:disabled) { background: var(--surface-inset); color: var(--action-primary); }
.attachment-button:disabled { cursor: not-allowed; color: var(--text-muted); }
.composer-area {
  flex: none;
  min-width: 0;
  padding: 12px var(--shell-pad-x) calc(var(--space-5) + env(safe-area-inset-bottom, 0px));
  background: linear-gradient(to top, var(--surface-canvas) 78%, transparent);
}
.new-content-button {
  position: absolute;
  left: 50%;
  z-index: 6;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  width: max-content;
  max-width: calc(100% - 32px);
  margin: 0;
  padding: 8px 14px;
  border: 1px solid rgba(88, 70, 232, .2);
  border-radius: 999px;
  background: rgba(255, 255, 255, .94);
  box-shadow: 0 9px 24px rgba(42, 45, 78, .13), inset 0 1px 0 var(--surface-panel);
  color: var(--action-primary);
  font-size: 12.5px;
  font-weight: 600;
  -webkit-backdrop-filter: blur(14px);
  backdrop-filter: blur(14px);
  transform: translateX(-50%);
  transition: transform .22s var(--ease-out), box-shadow .22s ease, background-color .2s ease;
}
.new-content-button:hover { box-shadow: 0 12px 28px rgba(42, 45, 78, .18), inset 0 1px 0 var(--surface-panel); }
.new-content-button:active { transform: translateX(-50%) scale(.96); }
.new-content-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--action-primary);
  box-shadow: 0 0 0 4px var(--action-soft);
}
.float-pop-enter-active,
.float-pop-leave-active { transition: opacity .2s ease, transform .3s var(--ease-out); }
.float-pop-enter-from,
.float-pop-leave-to { opacity: 0; transform: translateX(-50%) translateY(9px) scale(.9); }

.composer-error { margin-bottom: 10px; }
.composer-shell {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  min-width: 0;
  width: min(var(--conversation-width), 100%);
  margin: 0 auto;
  padding: 12px 16px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-md);
  background: var(--surface-panel);
  box-shadow: var(--shadow-composer);
  transition: border-color var(--dur-base) ease, box-shadow var(--dur-base) var(--ease-out);
}
.composer-shell:focus-within {
  border-color: var(--line-control);
  outline: 2px solid var(--focus-ring-light);
  outline-offset: 2px;
}
.composer-input {
  flex: 1;
  min-width: 0;
  min-height: 44px;
  max-height: 212px;
  padding: 10px 0;
  overflow-y: auto;
  border: 0;
  resize: none;
  background: transparent;
  color: var(--text-primary);
  font-family: var(--font-family);
  font-size: var(--text-reading);
  line-height: 24px;
}
.composer-input:disabled { opacity: 1; color: var(--text-muted); }
.send-button {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--action-primary);
  color: var(--action-primary-text);
  transition: background-color var(--dur-fast) ease, transform var(--dur-fast) var(--ease-out);
}
.send-button:hover:not(:disabled) { background: var(--action-primary-hover); }
.send-button:active:not(:disabled) { transform: scale(.94); }
.send-button:disabled {
  cursor: not-allowed;
  background: var(--surface-inset);
  color: var(--text-muted);
  opacity: 1;
}
.composer-hint {
  width: min(var(--conversation-width), 100%);
  min-height: 18px;
  margin: 8px auto 0;
  color: var(--text-muted);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
  text-align: center;
}
@media (min-width: 768px) and (max-width: 1023px) {
  .welcome { padding-top: max(0px, calc(30vh - 82px)); }
}
@media (max-width: 767px) {
  .chat-scroll { padding: 24px 16px 16px; }
  .welcome { padding-top: max(0px, calc(30vh - 24px)); }
  .composer-area { padding: 10px var(--space-4) max(var(--space-3), env(safe-area-inset-bottom, 0px)); }
  .message-bubble:not(.result-card) { padding: var(--space-3) var(--space-4); }
}
</style>
