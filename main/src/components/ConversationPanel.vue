<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import BriefCard from './BriefCard.vue'
import { useWorkspace } from '@/composables/useWorkspace'
import {
  briefStatusLabel,
  displayProjectTitle,
  generationStatusLabel,
  hasSnapshotContent,
  latestGeneration,
} from '@/utils/format'

const workspace = useWorkspace()
const {
  draft,
  projectParam,
  projectLoading,
  projectError,
  projectErrorKind,
  currentProject,
  projectMessages,
  projectGenerations,
  sendBusy,
  creatingProject,
  organizing,
  sendError,
  creationUncertain,
  send,
  retryProject,
  retryOrganize,
  openProjectDrawer,
  createNewProject,
  fillExample,
} = workspace
const input = ref<HTMLTextAreaElement | null>(null)
const messageScroller = ref<HTMLElement | null>(null)
const hasNewContent = ref(false)
const atBottom = ref(true)
let composing = false

const examples = [
  '米色亚麻托特包，简洁主图',
  '陶瓷马克杯，暖色生活场景',
  '木质香薰蜡烛，节日礼盒',
]

const hasProjectParam = computed(() => projectParam.value !== null)
const latest = computed(() => latestGeneration(projectGenerations.value))
const showComposer = computed(() => !hasProjectParam.value || Boolean(currentProject.value))
const onlyWhitespace = computed(() => draft.value.length > 0 && !draft.value.trim())
const sendDisabled = computed(
  () =>
    sendBusy.value ||
    creatingProject.value ||
    organizing.value ||
    onlyWhitespace.value ||
    (!hasProjectParam.value && !draft.value.trim()) ||
    (hasProjectParam.value && !currentProject.value),
)
const composerHint = computed(() => {
  if (creatingProject.value) return '正在创建项目，请稍候…'
  if (organizing.value) return '正在整理需求，请稍候；刷新后只读等待，不会自动重发。'
  if (sendBusy.value) return '正在发送，请稍候；发送失败会保留当前输入。'
  return 'Enter 换行，Ctrl／⌘ + Enter 发送。'
})
const sendReason = computed(() => {
  if (organizing.value) return '需求正在整理，当前只读等待。'
  if (creatingProject.value) return '正在创建项目，请勿重复发送。'
  if (sendBusy.value) return '正在发送，请勿重复点击。'
  if (onlyWhitespace.value) return '空白内容不能发送，请输入商品与图片需求。'
  return composerHint.value
})

function useExample(text: string): void {
  fillExample(text)
  void nextTick(() => input.value?.focus())
}

function scrollToBottom(): void {
  const element = messageScroller.value
  if (!element) return
  element.scrollTop = Math.max(0, element.scrollHeight - element.clientHeight)
  atBottom.value = true
  hasNewContent.value = false
}

function onMessageScroll(): void {
  const element = messageScroller.value
  if (!element) return
  const nearBottom =
    element.scrollHeight - element.scrollTop - element.clientHeight <= 24
  atBottom.value = nearBottom
  if (nearBottom) hasNewContent.value = false
}

function showNewContent(): void {
  scrollToBottom()
}

watch(
  () => projectParam.value,
  () => {
    atBottom.value = true
    hasNewContent.value = false
  },
)

watch(
  () => [projectParam.value, currentProject.value?.id, projectMessages.value.length] as const,
  async () => {
    await nextTick()
    if (atBottom.value) scrollToBottom()
    else if (projectMessages.value.length > 0) hasNewContent.value = true
  },
  { flush: 'post' },
)

async function onSend(): Promise<void> {
  await send()
}

function onCompositionStart(): void {
  composing = true
}

function onCompositionEnd(): void {
  composing = false
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || (!event.ctrlKey && !event.metaKey)) return
  if (composing || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  void onSend()
}

function messageRoleLabel(role: string): string {
  if (role === 'user') return '用户'
  if (role === 'assistant') return '助手'
  return '系统'
}
</script>

<template>
  <section class="conversation-panel" aria-labelledby="conversation-title">
    <template v-if="hasProjectParam">
      <div v-if="projectLoading" class="project-loading" role="status">正在读取项目详情…</div>

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
          <p class="eyebrow">已读取项目 · 只读</p>
          <h2 id="conversation-title">{{ displayProjectTitle(currentProject.title) }}</h2>
          <p class="project-status-line">
            <span class="status-badge">{{ briefStatusLabel(currentProject.brief_status) }}</span>
          </p>
        </header>

        <p v-if="currentProject.brief_status === 'review'" class="review-hint">
          待确认：请核对当前需求卡与同一版本的完整提示词。
        </p>
        <p v-else-if="currentProject.brief_status === 'needs_input'" class="needs-input-hint">
          需要补充信息：助手问题已显示在对话中，请用下方普通消息集中回答。
        </p>

        <div v-if="organizing" class="organize-status" role="status" aria-label="需求整理状态">
          正在整理需求…
        </div>

        <div v-if="currentProject.brief_error" class="brief-error" role="alert">
          <strong>需求整理失败</strong>
          <p>{{ currentProject.brief_error.message }}</p>
          <p class="muted">原文和已有需求卡已保留；重新整理只会由你的点击触发。</p>
          <button type="button" class="secondary-button" :disabled="sendBusy || organizing" @click="retryOrganize">
            重新整理
          </button>
        </div>

        <BriefCard
          :brief="currentProject.brief"
          heading="当前需求"
          :reference-asset="currentProject.reference_asset"
        />

        <section class="conversation-history" aria-labelledby="history-title">
          <h3 id="history-title">对话记录</h3>
          <p v-if="!projectMessages.length" class="muted">暂无对话记录。</p>
          <div
            v-else
            ref="messageScroller"
            class="message-list-scroll"
            role="log"
            aria-label="对话消息"
            @scroll="onMessageScroll"
          >
            <ol class="message-list">
              <li v-for="message in projectMessages" :key="message.id" class="message-item">
                <article :aria-label="messageRoleLabel(message.role) + '的消息'">
                  <p class="message-role">{{ messageRoleLabel(message.role) }}</p>
                  <p class="message-content">{{ message.content }}</p>
                  <BriefCard
                    v-if="message.role === 'assistant' && hasSnapshotContent(message.brief_snapshot)"
                    :brief="message.brief_snapshot!"
                    heading="当时的需求快照"
                    snapshot
                  />
                </article>
              </li>
            </ol>
          </div>
          <button
            v-if="hasNewContent"
            type="button"
            class="new-content-button"
            @click="showNewContent"
          >
            有新内容
          </button>
        </section>

        <section class="generation-summary" aria-labelledby="generation-title">
          <h3 id="generation-title">生成概览</h3>
          <p>生成记录 {{ projectGenerations.length }} 条</p>
          <p v-if="latest">最近版本：第 {{ latest.version_no }} 版，状态：{{ generationStatusLabel(latest.status) }}</p>
          <p v-else class="muted">尚未生成图片。</p>
          <p class="muted">当前仅只读恢复生成记录，不轮询状态、不显示结果图，也不提供下载。</p>
        </section>
      </template>
    </template>

    <template v-else>
      <div class="conversation-heading">
        <p class="eyebrow">当前状态：未选择项目</p>
        <h2 id="conversation-title">从一个商品想法开始</h2>
        <p class="conversation-intro">
          用一段自然语言描述商品、受众、画面风格和交付用途。点击示例只会填入输入框；首次有效发送会先创建项目，再请求需求整理。
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

    <div v-if="sendError" class="send-error" role="alert">
      <p>{{ sendError }}</p>
      <button
        v-if="creationUncertain"
        type="button"
        class="secondary-button"
        @click="openProjectDrawer"
      >
        打开已有项目
      </button>
    </div>

    <form v-if="showComposer" class="composer" @submit.prevent="onSend">
      <label class="sr-only" for="workspace-prompt">描述商品与图片需求</label>
      <textarea
        id="workspace-prompt"
        ref="input"
        v-model="draft"
        class="prompt-input"
        aria-label="描述商品与图片需求"
        rows="6"
        placeholder="例如：为米色亚麻托特包生成一张简洁主图，浅暖色背景，突出材质与轮廓……"
        :disabled="organizing"
        @keydown="onKeydown"
        @compositionstart="onCompositionStart"
        @compositionend="onCompositionEnd"
      />
      <div class="composer-actions">
        <p class="composer-hint">{{ composerHint }}</p>
        <div class="composer-buttons">
          <button type="button" class="secondary-button" disabled aria-describedby="reference-reason" title="添加参考图将在后续版本接入">
            添加参考图
          </button>
          <button
            type="submit"
            class="primary-button"
            :disabled="sendDisabled"
            :aria-busy="sendBusy || creatingProject || organizing ? 'true' : undefined"
            aria-describedby="send-reason"
          >
            发送
          </button>
        </div>
      </div>
      <p id="reference-reason" class="control-reason">添加参考图将在后续版本接入，当前不可用。</p>
      <p id="send-reason" class="control-reason">{{ sendReason }}</p>
    </form>
  </section>
</template>
