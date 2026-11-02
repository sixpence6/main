<script setup lang="ts">
import { computed, inject, nextTick, ref, watch } from 'vue'
import BriefCard from './BriefCard.vue'
import ReferenceUploader from './ReferenceUploader.vue'
import { useWorkspace } from '@/composables/useWorkspace'
import { WORKFLOW_KEY, type WorkflowController } from '@/composables/useWorkflow'
import type { WorkflowAction } from '@/api/workflow'
import { currentPublishedVersion } from '@/workflow/projection'
import {
  briefStatusLabel,
  displayProjectTitle,
  hasSnapshotContent,
  legacyNoticeText,
} from '@/utils/format'

const workspace = useWorkspace()
const workflow = inject<WorkflowController | null>(WORKFLOW_KEY, null)
const {
  draft,
  projectParam,
  projectLoading,
  projectError,
  projectErrorKind,
  currentProject,
  projectMessages,
  projectGenerations,
  fillExample,
  retryProject,
  openProjectDrawer,
  createNewProject,
  sendBusy,
  sendError,
  messageConflict,
  messageConflictText,
  messageLocalCopy,
  showMessageLocalCopy,
  loadLatestAfterMessageConflict,
  chooseReferenceFiles,
  organizing,
  sendDisabledReason,
  send,
  retryOrganize,
  latestGenerationError,
  activeGeneration,
  announce,
  refreshProjectDetail,
} = workspace
const input = ref<HTMLTextAreaElement | null>(null)
const historyScroll = ref<HTMLElement | null>(null)
const composing = ref(false)
const hasNewContent = ref(false)
const nearBottom = ref(true)

const examples = [
  '米色亚麻托特包，简洁主图',
  '陶瓷马克杯，暖色生活场景',
  '木质香薰蜡烛，节日礼盒',
]

const hasProjectParam = computed(() => projectParam.value !== null)
const showComposer = computed(() => !hasProjectParam.value || Boolean(currentProject.value))
const showOrganizing = computed(
  () => Boolean(currentProject.value) && (sendBusy.value || organizing.value),
)
const composerReason = computed(() => {
  if (!sendDisabledReason.value) return ''
  if (sendDisabledReason.value === '请输入商品与图片需求。' && !draft.value.trim()) return ''
  return sendDisabledReason.value
})

interface RevisionBaseline {
  versionNo: number
  filename: string
  size: string
}

function versionSize(version: { brief?: Record<string, unknown> | null }): string {
  const size = version.brief?.size
  return typeof size === 'string' && size ? size.replace(/x/i, '×') : '尺寸未记录'
}

function revisionBaseline(): RevisionBaseline | null {
  const view = workflow?.view.value
  if (!currentProject.value || !view || view.status !== 'completed') return null
  if (view.project_revision < currentProject.value.revision) return null
  const published = currentPublishedVersion(currentProject.value, view)
  if (published) {
    return {
      versionNo: published.requirement_revision,
      filename: published.filename ?? '未命名',
      size: versionSize(published),
    }
  }
  const delivery = view.delivery
  if (!delivery || delivery.status !== 'approved') return null
  return {
    versionNo: view.requirement_revision,
    filename: delivery.filename || '未命名',
    size: '尺寸未记录',
  }
}

const revisionAction = computed<WorkflowAction | null>(() => {
  if (!workflow || !revisionBaseline()) return null
  return workflow.view.value?.available_actions.find((action) => action.id === 'request_new_version') ?? null
})

const revisionContext = computed(() => {
  const baseline = revisionBaseline()
  if (!baseline) return ''
  return '当前交付为第 ' + baseline.versionNo + ' 版（' + baseline.filename + '），尺寸 ' + baseline.size + '。发送后会基于当前版本创建新的需求版本，重新整理并独立复审；生成仍需重新确认。不会自动使用当前图片作为参考，也不承诺局部像素不变或只修改指定区域。'
})

const composerBusy = computed(() => sendBusy.value || Boolean(workflow?.actionBusy.value))

function useExample(text: string): void {
  fillExample(text)
  void nextTick(() => input.value?.focus())
}

function isNearBottom(): boolean {
  const element = historyScroll.value
  if (!element) return true
  return element.scrollHeight - element.scrollTop - element.clientHeight < 24
}

function scrollToBottom(): void {
  const element = historyScroll.value
  if (!element) return
  element.scrollTop = element.scrollHeight
  nearBottom.value = true
  hasNewContent.value = false
}

function onHistoryScroll(): void {
  nearBottom.value = isNearBottom()
  if (nearBottom.value) hasNewContent.value = false
}

function onNewContent(): void {
  scrollToBottom()
  void nextTick(() => input.value?.focus())
}

async function submit(): Promise<void> {
  if (sendBusy.value || workflow?.actionBusy.value) return
  const action = revisionAction.value
  if (!action || !workflow) {
    await send()
    return
  }
  const content = draft.value.trim()
  if (!content) {
    sendError.value = '请输入本次修改要求。'
    return
  }
  const reason = sendDisabledReason.value
  if (reason && reason !== '请输入商品与图片需求。') {
    sendError.value = reason
    return
  }
  sendError.value = ''
  const ok = await workflow.runAction(action, content, {})
  if (!ok) {
    sendError.value = workflow.error.value || '修改未能提交，输入内容已保留，请稍后重试。'
    return
  }
  draft.value = ''
  announce('已提交修改，正在创建新的需求版本并重新整理。')
  await refreshProjectDetail()
}

function onSubmit(): void {
  void submit()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter') return
  if (event.isComposing || composing.value || event.keyCode === 229) return
  if (event.shiftKey) return
  event.preventDefault()
  void submit()
}

function filesFrom(list: FileList | null | undefined): File[] {
  return list ? Array.from(list).filter((file) => file.type.startsWith('image/')) : []
}

function onComposerPaste(event: ClipboardEvent): void {
  const files = filesFrom(event.clipboardData?.files)
  if (!files.length) return
  event.preventDefault()
  void chooseReferenceFiles(files)
}

function onComposerDrop(event: DragEvent): void {
  const files = filesFrom(event.dataTransfer?.files)
  if (!files.length) return
  event.preventDefault()
  void chooseReferenceFiles(files)
}

function onCompositionStart(): void {
  composing.value = true
}

function onCompositionEnd(): void {
  composing.value = false
}

function messageRoleLabel(role: string): string {
  if (role === 'user') return '用户'
  if (role === 'assistant') return '助手'
  return '系统'
}

watch(
  () => projectMessages.value.length,
  async () => {
    const follow = nearBottom.value
    await nextTick()
    if (follow) scrollToBottom()
    else hasNewContent.value = true
  },
  { flush: 'post' },
)
</script>

<template>
  <section class="conversation-panel" aria-labelledby="conversation-title">
    <template v-if="hasProjectParam">
      <div v-if="projectLoading" class="project-loading" role="status">正在读取项目</div>

      <div v-else-if="projectError" class="project-error" role="alert">
        <strong>项目读取失败</strong>
        <p>{{ projectError }}</p>
        <div class="project-error-actions">
          <button
            v-if="projectErrorKind === 'network' || projectErrorKind === 'generic'"
            type="button"
            class="secondary-button"
            @click="retryProject"
          >
            重试
          </button>
          <button type="button" class="secondary-button" @click="openProjectDrawer">打开已有项目</button>
          <button type="button" class="primary-button" @click="createNewProject">新建项目</button>
        </div>
      </div>

      <template v-else-if="currentProject">
        <header class="project-heading">
          <p class="eyebrow">当前项目</p>
          <h2 id="conversation-title">{{ displayProjectTitle(currentProject.title) }}</h2>
          <p class="project-status-line">
            <span class="status-badge">{{ briefStatusLabel(currentProject.brief_status) }}</span>
          </p>
          <p v-if="latestGenerationError" class="generation-latest-error" role="alert">
            {{ latestGenerationError }}
          </p>
        </header>

        <div v-if="showOrganizing" class="organizing-status" role="status">正在整理需求…</div>

        <div v-if="currentProject.brief_error" class="brief-error" role="alert">
          <strong>需求整理失败</strong>
          <p>{{ currentProject.brief_error.message || '已保存的原文和卡片仍会保留。' }}</p>
          <button
            type="button"
            class="secondary-button"
            :disabled="sendBusy || organizing"
            @click="retryOrganize"
          >
            重新整理
          </button>
        </div>

        <section class="conversation-history" aria-labelledby="history-title">
          <h3 id="history-title">对话记录</h3>
          <div
            ref="historyScroll"
            class="history-scroll"
            tabindex="0"
            aria-label="对话记录滚动区域"
            @scroll="onHistoryScroll"
          >
            <p v-if="!projectMessages.length" class="muted">暂无对话记录。</p>
            <ol v-else class="message-list">
              <li v-for="message in projectMessages" :key="message.id" class="message-item">
                <article :aria-label="messageRoleLabel(message.role) + '的消息'">
                  <p class="message-role">{{ messageRoleLabel(message.role) }}</p>
                  <p v-if="message.legacy_unreviewed" class="message-content legacy-placeholder">
                    {{ legacyNoticeText(message.legacy_notice) }}
                  </p>
                  <p v-else class="message-content">{{ message.content }}</p>
                  <BriefCard
                    v-if="!message.legacy_unreviewed && message.role === 'assistant' && hasSnapshotContent(message.brief_snapshot)"
                    :brief="message.brief_snapshot!"
                    heading="当时的需求快照"
                    snapshot
                  />
                </article>
              </li>
            </ol>
          </div>
          <button v-if="hasNewContent" type="button" class="new-content-button" @click="onNewContent">
            有新内容
          </button>
        </section>
      </template>
    </template>

    <template v-else>
      <div class="conversation-heading">
        <p class="eyebrow">当前状态：未选择项目</p>
        <h2 id="conversation-title">从一个商品想法开始</h2>
        <p class="conversation-intro">
          用一段自然语言描述商品、受众、画面风格和交付用途。发送后会为你创建项目并整理需求；未发送的内容只保留在当前页面。
        </p>
      </div>

      <div class="example-block">
        <h3>输入示例</h3>
        <div class="example-list">
          <button v-for="example in examples" :key="example" type="button" class="example-button" @click="useExample(example)">
            {{ example }}
          </button>
        </div>
      </div>
    </template>

    <ReferenceUploader v-if="showComposer" />

    <form v-if="showComposer" class="composer" @submit.prevent="onSubmit" @dragover.prevent @drop.prevent="onComposerDrop">
      <div v-if="messageConflict" class="composer-conflict" role="alert">
        <p>
          <strong>需求已在别处更新，这条消息未发送。</strong>
          输入内容已保留；加载最新需求，或只读查看本地内容后再决定。
        </p>
        <div class="composer-conflict-actions">
          <button type="button" class="secondary-button" @click="loadLatestAfterMessageConflict">加载最新需求</button>
          <button type="button" class="secondary-button" @click="showMessageLocalCopy">只读查看本地内容</button>
        </div>
        <label class="sr-only" for="message-local-copy">未发送文字的只读本地副本</label>
        <textarea
          v-if="messageLocalCopy"
          id="message-local-copy"
          class="composer-local-copy"
          :value="messageConflictText"
          readonly
          rows="4"
          aria-label="未发送文字的只读本地副本"
        />
      </div>
      <p v-if="sendError && !messageConflict" class="composer-error" role="alert">{{ sendError }}</p>
      <p v-if="revisionAction" id="revision-edit-context" class="composer-revision-context" role="note">
        {{ revisionContext }}
      </p>
      <label class="sr-only" for="workspace-prompt">描述商品与图片需求</label>
      <textarea
        id="workspace-prompt"
        ref="input"
        v-model="draft"
        class="prompt-input"
        aria-label="描述商品与图片需求"
        rows="6"
        placeholder="例如：为米色亚麻托特包生成一张简洁主图，浅暖色背景，突出材质与轮廓……"
        :disabled="composerBusy || organizing || Boolean(activeGeneration)"
        @keydown="onKeydown"
        @paste="onComposerPaste"
        @compositionstart="onCompositionStart"
        @compositionend="onCompositionEnd"
      />
      <div class="composer-actions">
        <p class="composer-hint">
          {{ revisionAction ? '发送后会创建新的需求版本并重新审核；' : '' }}Enter 发送；Shift + Enter 换行；Ctrl/⌘ + Enter 也可发送。
        </p>
        <div class="composer-buttons">
          <button type="submit" class="primary-button" :disabled="composerBusy || Boolean(sendDisabledReason)">
            发送
          </button>
        </div>
      </div>
      <p v-if="composerReason" id="send-reason" class="control-reason">
        {{ composerReason }}
      </p>
    </form>
  </section>
</template>
