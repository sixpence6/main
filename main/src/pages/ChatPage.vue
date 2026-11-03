<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { assetUrl } from '@/api/client'
import { useChatFlow } from '@/composables/useChatFlow'
import { useWorkspace } from '@/composables/useWorkspace'
import { displayProjectTitle } from '@/utils/format'

const workspace = useWorkspace()
const flow = useChatFlow()
const {
  projectMessages,
  projectLoading,
  projectError,
  currentProject,
  draft,
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
const attachmentInput = ref<HTMLInputElement | null>(null)
const composing = ref(false)
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

function scrollToLatest(): void {
  const element = scrollRegion.value
  if (!element) return
  element.scrollTop = element.scrollHeight
}

watch(
  () => [
    projectMessages.value.length,
    taskStatusText.value,
    canConfirmRequirements.value,
    Boolean(promptPreview.value),
    publishedAssetId.value,
  ],
  async () => {
    await nextTick()
    scrollToLatest()
  },
  { flush: 'post' },
)

async function submit(): Promise<void> {
  const value = draft.value
  if (!value.trim() || busy.value) return
  await send(value)
}

function onEnter(event: KeyboardEvent): void {
  if (composing.value || event.isComposing) return
  event.preventDefault()
  void submit()
}

function roleLabel(role: string): string {
  return role === 'user' ? '你' : '智能生图助手'
}
</script>

<template>
  <section class="pure-chat" aria-label="生图对话">
    <header class="chat-topbar">
      <div class="chat-heading">
        <p class="chat-eyebrow">智能生图助手</p>
        <h2 class="chat-title">{{ title }}</h2>
      </div>
      <span v-if="busy" class="chat-working">处理中…</span>
    </header>

    <div ref="scrollRegion" class="chat-scroll" aria-label="对话内容">
      <div v-if="!hasConversation" class="welcome">
        <p class="welcome-mark" aria-hidden="true">✦</p>
        <h1>发来商品图片，或告诉我你想做什么样的电商图片</h1>
        <p class="welcome-note">用一句话描述商品、用途和画面感觉即可。需要确认时，我会把要点整理给你。</p>
      </div>

      <template v-else>
        <div v-if="projectLoading" class="chat-loading" role="status">正在读取对话…</div>
        <div v-else-if="projectError" class="chat-error" role="alert">
          <p>{{ projectError }}</p>
          <button type="button" class="text-button" @click="retry">重试</button>
        </div>

        <template v-else>
          <ol v-if="projectMessages.length" class="message-list" aria-label="对话记录">
            <li
              v-for="message in projectMessages"
              :key="message.id"
              class="message-row"
              :class="message.role === 'user' ? 'is-user' : 'is-assistant'"
            >
              <div class="message-bubble">
                <p class="message-role">{{ roleLabel(message.role) }}</p>
                <img
                  v-if="message.attachment"
                  class="message-attachment"
                  :src="message.attachment.preview_url"
                  :alt="'随消息发送的图片，用途：' + message.attachment.usage"
                >
                <p class="message-content">{{ message.content }}</p>
              </div>
            </li>
          </ol>

          <section
            v-if="(taskStatusText || taskActions.length) && !publishedAssetId"
            class="assistant-turn task-turn"
            aria-label="任务状态"
          >
            <div class="message-bubble status-bubble">
              <p class="message-role">智能生图助手</p>
              <p v-if="taskReasonLabel" class="task-reason-label" :data-category="taskReason?.category">
                {{ taskReasonLabel }}
              </p>
              <p v-if="taskStatusText" class="message-content">{{ taskStatusText }}</p>
              <span v-if="isWorkingStatus" class="typing-dots" aria-hidden="true"><i /><i /><i /></span>
              <div v-if="taskActions.length" class="task-actions">
                <p class="task-actions-note">安全恢复：以下操作只使用当前服务端允许的动作，不会自动提交新的生成请求，也不会自动新增受理次数。</p>
                <div v-for="action in taskActions" :key="action.id" class="task-action">
                  <div class="task-action-head">
                    <button
                      type="button"
                      class="task-action-button"
                      :disabled="taskActionDisabled(action)"
                      @click="runTaskAction(action)"
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
                    <input v-model="taskConfirmations[action.id]" type="checkbox">
                    <span>{{ taskConfirmationText(action) }}</span>
                  </label>
                </div>
              </div>
            </div>
          </section>

          <section
            v-if="canConfirmRequirements"
            class="assistant-turn requirement-turn"
            aria-label="需求确认"
          >
            <div class="message-bubble">
              <p class="message-role">智能生图助手</p>
              <p class="assistant-lead">我先把你的描述整理成了这份需求，确认后我会准备提示词。</p>
              <dl class="requirement-list">
                <template v-for="row in requirementRows" :key="row.label">
                  <dt>{{ row.label }}</dt>
                  <dd>{{ row.value }}</dd>
                </template>
              </dl>
              <p class="usage-note">{{ usageText }}</p>
              <button type="button" class="primary-action" :disabled="busy" @click="confirmRequirements">
                确认需求并生成提示词
              </button>
            </div>
          </section>

          <section
            v-if="promptPreview"
            class="assistant-turn prompt-turn"
            aria-label="提示词确认"
          >
            <div class="message-bubble">
              <p class="message-role">智能生图助手</p>
              <p class="assistant-lead">提示词已经准备好。下面就是实际会交给图片模型的内容，请确认后开始生成。</p>
              <div class="prompt-block">
                <div class="prompt-heading">
                  <h3>实际提示词</h3>
                  <button type="button" class="text-button" @click="copyPrompt">
                    {{ copied ? '已复制' : '复制提示词' }}
                  </button>
                </div>
                <pre class="prompt-text">{{ positivePrompt }}</pre>
                <template v-if="negativePrompt">
                  <h3>需要避免</h3>
                  <pre class="prompt-text prompt-negative">{{ negativePrompt }}</pre>
                </template>
                <dl class="prompt-meta">
                  <template v-if="promptOutput">
                    <dt>输出</dt><dd>{{ promptOutput }}</dd>
                  </template>
                  <template v-if="referenceInfo">
                    <dt>参考图</dt><dd>{{ referenceInfo }}</dd>
                  </template>
                </dl>
              </div>
              <p v-if="promptStateLabel" class="usage-note">{{ promptStateLabel }}</p>
              <button
                type="button"
                class="primary-action"
                :disabled="busy || !promptPreview?.can_confirm"
                @click="confirmPrompt"
              >
                {{ promptPreview?.can_confirm ? '确认使用以上提示词生成' : '提示词检查中…' }}
              </button>
            </div>
          </section>

          <figure v-if="publishedAssetId" class="assistant-turn result-turn">
            <div class="message-bubble">
              <p class="message-role">智能生图助手</p>
              <p class="message-content">图片已经生成，可以保存使用了。</p>
              <img
                class="result-image"
                :src="assetUrl(publishedAssetId)"
                :alt="publishedCaption"
              >
              <figcaption>{{ publishedCaption }}</figcaption>
            </div>
          </figure>
        </template>
      </template>
    </div>

    <footer class="composer-area">
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
        <img :src="pendingAttachment.previewUrl" alt="待发送的图片预览">
        <div class="attachment-preview-copy">
          <strong>{{ attachmentFileName }}</strong>
          <span v-if="attachmentBusy">正在上传…</span>
          <span v-else>将随这条消息一起发送</span>
        </div>
        <button type="button" class="text-button" :disabled="attachmentBusy" @click="openAttachmentPicker">替换图片</button>
        <button type="button" class="text-button attachment-remove" :disabled="attachmentBusy" @click="removeAttachment">移除图片</button>
      </div>
      <div
        class="composer-shell"
        @dragover.prevent
        @drop.prevent="onDrop"
      >
        <input
          ref="attachmentInput"
          class="file-input"
          type="file"
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
          v-model="draft"
          rows="1"
          class="composer-input"
          aria-label="描述商品与图片需求"
          :placeholder="pendingTaskAction ? '说明要修改的地方，然后发送…' : '描述商品、用途和画面感觉…'"
          :disabled="attachmentBusy"
          @keydown.enter.exact="onEnter"
          @paste="onPaste"
          @compositionstart="composing = true"
          @compositionend="composing = false"
        />
        <button
          type="button"
          class="send-button"
          aria-label="发送"
          :disabled="sendDisabled"
          @click="submit"
        >
          发送
        </button>
      </div>
      <p class="composer-hint">Enter 发送，Shift + Enter 换行</p>
    </footer>
  </section>
</template>

<style scoped>
.pure-chat {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  background: var(--color-panel);
  border-radius: var(--radius-shell);
  overflow: hidden;
}
.chat-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex: none;
  min-height: var(--topbar-height);
  padding: 0 clamp(20px, 4vw, 44px);
  border-bottom: 1px solid var(--color-border-soft);
}
.chat-heading { min-width: 0; }
.chat-eyebrow { margin: 0 0 2px; color: var(--color-text-muted); font-size: 12px; letter-spacing: .08em; }
.chat-title { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 17px; line-height: 1.3; }
.chat-working { flex: none; color: var(--color-primary); font-size: 13px; }
.chat-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 34px clamp(20px, 5vw, 56px) 24px;
  scroll-behavior: smooth;
}
.welcome {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  min-height: 100%;
  padding-bottom: 8vh;
  text-align: center;
}
.welcome-mark {
  display: grid;
  place-items: center;
  width: 54px;
  height: 54px;
  margin: 0 0 22px;
  border-radius: 18px;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 28px;
}
.welcome h1 {
  max-width: 720px;
  margin: 0;
  font-size: clamp(26px, 3.2vw, 42px);
  line-height: 1.25;
  letter-spacing: -.025em;
}
.welcome-note { max-width: 560px; margin: 18px 0 0; color: var(--color-text-muted); font-size: 15px; line-height: 1.7; }
.chat-loading { padding: 48px 0; color: var(--color-text-muted); text-align: center; }
.message-list { display: flex; flex-direction: column; gap: 24px; width: min(var(--conversation-width), 100%); margin: 0 auto; padding: 0; list-style: none; }
.message-row { display: flex; }
.message-row.is-user { justify-content: flex-end; }
.message-row.is-assistant { justify-content: flex-start; }
.message-bubble {
  max-width: min(760px, 100%);
  padding: 17px 20px;
  border-radius: 18px;
  background: var(--color-panel-soft);
}
.is-user .message-bubble { background: var(--color-primary); color: #fff; border-bottom-right-radius: 5px; }
.is-assistant .message-bubble { border-bottom-left-radius: 5px; }
.message-role { margin: 0 0 6px; color: var(--color-text-muted); font-size: 12px; }
.is-user .message-role { color: rgba(255,255,255,.72); }
.message-content { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
.message-attachment { display: block; max-width: min(360px, 100%); max-height: 260px; margin: 0 0 12px; border-radius: 12px; object-fit: cover; }
.assistant-turn { width: min(var(--conversation-width), 100%); margin: 24px auto 0; }
.assistant-turn .message-bubble { max-width: 100%; }
.assistant-lead { margin: 0 0 16px; line-height: 1.7; }
.requirement-list, .prompt-meta { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 8px 18px; margin: 0 0 18px; }
.requirement-list dt, .prompt-meta dt { color: var(--color-text-muted); font-size: 13px; }
.requirement-list dd, .prompt-meta dd { margin: 0; overflow-wrap: anywhere; }
.usage-note { margin: 0 0 14px; color: var(--color-text-muted); font-size: 13px; line-height: 1.6; }
.primary-action {
  min-height: 44px;
  padding: 0 22px;
  border: 0;
  border-radius: 999px;
  background: var(--color-primary);
  color: #fff;
  font-weight: 600;
}
.primary-action:hover:not(:disabled) { background: var(--color-primary-hover); }
.primary-action:disabled { cursor: not-allowed; opacity: .55; }
.prompt-block { margin: 0 0 18px; padding: 18px; border: 1px solid var(--color-border); border-radius: 14px; background: var(--color-panel); }
.prompt-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.prompt-heading h3, .prompt-block h3 { margin: 0; font-size: 14px; }
.prompt-block h3 + .prompt-text { margin-top: 8px; }
.prompt-text { max-height: none; margin: 0 0 16px; padding: 14px; border-radius: 10px; background: var(--color-code); white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.65 var(--font-mono); }
.prompt-negative { background: #fbf1f1; }
.prompt-meta { margin-bottom: 0; }
.text-button { padding: 2px 0; border: 0; background: transparent; color: var(--color-primary); font-size: 13px; }
.text-button:hover { text-decoration: underline; }
.status-bubble { display: inline-flex; align-items: center; gap: 12px; color: var(--color-text-muted); }
.status-bubble .message-content { color: var(--color-text-muted); }
.task-reason-label { margin: 0 0 6px; color: var(--color-primary); font-size: 13px; font-weight: 600; }
.task-actions { display: flex; flex-direction: column; gap: 14px; margin-top: 16px; }
.task-actions-note { margin: 0; color: var(--color-text-muted); font-size: 13px; line-height: 1.6; }
.task-action { display: flex; flex-direction: column; gap: 6px; padding-top: 12px; border-top: 1px solid var(--color-border-soft); }
.task-action-head { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.task-action-button { min-height: 34px; padding: 0 14px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-panel); color: var(--color-text); font-size: 13px; }
.task-action-button:hover:not(:disabled) { border-color: var(--color-primary); color: var(--color-primary); }
.task-action-button:disabled { cursor: not-allowed; opacity: .5; }
.task-effect { padding: 2px 8px; border-radius: 999px; background: var(--color-primary-soft); color: var(--color-primary); font-size: 12px; }
.task-help, .task-count, .task-text-note { margin: 0; color: var(--color-text-muted); font-size: 12px; line-height: 1.55; }
.task-count { color: var(--color-text); }
.task-confirm { display: flex; align-items: flex-start; gap: 8px; color: var(--color-text); font-size: 13px; line-height: 1.5; }
.task-confirm input { margin-top: 2px; }
.pending-task-note { width: min(var(--conversation-width), 100%); margin: 0 auto 8px; color: var(--color-primary); font-size: 13px; }
.typing-dots { display: inline-flex; gap: 4px; }
.typing-dots i { width: 5px; height: 5px; border-radius: 50%; background: var(--color-primary); animation: typing 1s infinite ease-in-out; }
.typing-dots i:nth-child(2) { animation-delay: .15s; }
.typing-dots i:nth-child(3) { animation-delay: .3s; }
@keyframes typing { 0%, 80%, 100% { opacity: .25; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }
.result-image { display: block; width: min(640px, 100%); max-height: 620px; margin: 4px 0 10px; border-radius: 16px; object-fit: contain; background: var(--color-panel); }
figcaption { color: var(--color-text-muted); font-size: 13px; }
.chat-error { width: min(var(--conversation-width), 100%); margin: 0 auto 20px; padding: 12px 14px; border: 1px solid #efcaca; border-radius: 12px; background: #fff7f7; color: var(--color-danger); }
.chat-error p { margin: 0 0 4px; }
.attachment-preview {
  display: flex;
  align-items: center;
  gap: 10px;
  width: min(var(--conversation-width), 100%);
  margin: 0 auto 8px;
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-panel-soft);
}
.attachment-preview img { width: 46px; height: 46px; flex: none; border-radius: 9px; object-fit: cover; }
.attachment-preview-copy { display: flex; flex: 1 1 auto; min-width: 0; flex-direction: column; gap: 2px; }
.attachment-preview-copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.attachment-preview-copy span { color: var(--color-text-muted); font-size: 12px; }
.attachment-remove { color: var(--color-danger); }
.file-input { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.attachment-button {
  display: grid;
  flex: none;
  place-items: center;
  width: 34px;
  height: 44px;
  border: 0;
  background: transparent;
  color: var(--color-text-muted);
  font-size: 26px;
  line-height: 1;
}
.attachment-button:hover:not(:disabled) { color: var(--color-primary); }
.attachment-button:disabled { cursor: not-allowed; opacity: .45; }
.composer-area { flex: none; padding: 12px clamp(20px, 5vw, 56px) 20px; background: linear-gradient(to top, var(--color-panel) 78%, rgba(255,255,255,0)); }
.composer-error { margin-bottom: 10px; }
.composer-shell {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  width: min(var(--conversation-width), 100%);
  margin: 0 auto;
  padding: 8px 8px 8px 18px;
  border: 1px solid var(--color-border);
  border-radius: 22px;
  background: var(--color-panel);
  box-shadow: 0 10px 30px rgba(40, 44, 72, .08);
}
.composer-input {
  flex: 1;
  min-height: 44px;
  max-height: 180px;
  padding: 10px 0;
  border: 0;
  outline: 0;
  resize: none;
  background: transparent;
  color: var(--color-text);
  font: 16px/1.5 var(--font-family);
}
.composer-input:disabled { opacity: .6; }
.send-button { flex: none; min-width: 68px; height: 44px; border: 0; border-radius: 16px; background: var(--color-primary); color: #fff; font-weight: 600; }
.send-button:hover:not(:disabled) { background: var(--color-primary-hover); }
.send-button:disabled { cursor: not-allowed; opacity: .45; }
.composer-hint { width: min(var(--conversation-width), 100%); margin: 8px auto 0; color: var(--color-text-muted); font-size: 12px; text-align: center; }
@media (max-width: 767px) {
  .chat-topbar { min-height: 58px; padding: 0 18px; }
  .chat-scroll { padding: 24px 16px 16px; }
  .welcome h1 { font-size: 28px; }
  .composer-area { padding: 10px 14px 14px; }
  .composer-shell { border-radius: 18px; }
  .message-bubble { padding: 15px 16px; }
}
</style>
