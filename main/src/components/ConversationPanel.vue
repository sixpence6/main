<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import BriefCard from './BriefCard.vue'
import ReferenceUploader from './ReferenceUploader.vue'
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
  fillExample,
  retryProject,
  openProjectDrawer,
  createNewProject,
  sendBusy,
  sendError,
  organizing,
  sendDisabledReason,
  briefDraft,
  briefEditing,
  briefFieldErrors,
  briefDirty,
  briefAction,
  briefBusy,
  briefSaveError,
  briefConfirmError,
  briefSaveNotice,
  briefConflict,
  briefLocalCopy,
  viewLocalBriefCopy,
  promptStale,
  saveDisabledReason,
  confirmDisabledReason,
  startBriefEdit,
  cancelBriefEdit,
  setBriefField,
  saveBrief,
  confirmBrief,
  reloadLatestBrief,
  send,
  retryOrganize,
  generationSubmitBusy,
  generationSubmitError,
  generationSubmitNotice,
  generationUncertain,
  generationQueryError,
  generationDisabledReason,
  activeGeneration,
  latestGenerationError,
  submitGeneration,
  verifyGenerationSubmission,
  retryGenerationQuery,
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
const latest = computed(() => latestGeneration(projectGenerations.value))
const showComposer = computed(() => !hasProjectParam.value || Boolean(currentProject.value))
const showOrganizing = computed(
  () => Boolean(currentProject.value) && (sendBusy.value || organizing.value || briefAction.value === 'organize'),
)
const composerReason = computed(() => {
  if (!sendDisabledReason.value) return ''
  if (sendDisabledReason.value === '请输入商品与图片需求。' && !draft.value.trim()) return ''
  return sendDisabledReason.value
})

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

function onSubmit(): void {
  void send()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter') return
  if (event.isComposing || composing.value || event.keyCode === 229) return
  if (event.ctrlKey || event.metaKey) {
    event.preventDefault()
    void send()
  }
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
          <p class="eyebrow">当前项目</p>
          <h2 id="conversation-title">{{ displayProjectTitle(currentProject.title) }}</h2>
          <p class="project-status-line">
            <span class="status-badge">{{ briefStatusLabel(currentProject.brief_status) }}</span>
          </p>
          <p v-if="latestGenerationError" class="generation-latest-error" role="alert">
            {{ latestGenerationError }}
          </p>
        </header>

        <div v-if="showOrganizing" class="organizing-status" role="status">
          正在整理需求…
        </div>

        <div v-if="currentProject.brief_error" class="brief-error" role="alert">
          <strong>需求整理失败</strong>
          <p>{{ currentProject.brief_error.message || '已保存的原文和卡片仍会保留。' }}</p>
          <button
            type="button"
            class="secondary-button"
            :disabled="sendBusy || organizing || briefBusy || briefConflict || briefLocalCopy"
            @click="retryOrganize"
          >
            重新整理
          </button>
        </div>

        <BriefCard
          :brief="currentProject.brief"
          heading="当前需求"
          :reference-asset="currentProject.reference_asset"
          editable
          :editing="briefEditing"
          :draft="briefDraft"
          :dirty="briefDirty"
          :field-errors="briefFieldErrors"
          :prompt-stale="promptStale"
          :busy="briefBusy"
          :save-disabled-reason="saveDisabledReason"
          :confirm-disabled-reason="confirmDisabledReason"
          :save-notice="briefSaveNotice"
          :save-error="briefSaveError"
          :confirm-error="briefConfirmError"
          :conflict="briefConflict"
          :local-copy="briefLocalCopy"
          @start-edit="startBriefEdit"
          @update-field="setBriefField"
          @save="saveBrief"
          @cancel="cancelBriefEdit"
          @confirm="confirmBrief"
          @reorganize="retryOrganize"
          @reload-latest="reloadLatestBrief"
          @view-local-copy="viewLocalBriefCopy"
        />

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
          <button v-if="hasNewContent" type="button" class="new-content-button" @click="onNewContent">
            有新内容
          </button>
        </section>

        <section class="generation-summary" aria-labelledby="generation-title">
          <div class="generation-summary-header">
            <div>
              <h3 id="generation-title">生成概览</h3>
              <p>生成记录 {{ projectGenerations.length }} 条</p>
            </div>
            <span class="generation-quantity">生成数量：1 张</span>
          </div>

          <dl class="generation-facts">
            <div>
              <dt>用途</dt>
              <dd>{{ currentProject.brief.purpose || '未填写' }}</dd>
            </div>
            <div>
              <dt>图片尺寸</dt>
              <dd>{{ currentProject.brief.image_size || '尚未确定' }}</dd>
            </div>
            <div>
              <dt>参考图</dt>
              <dd>{{ currentProject.reference_asset ? '有（1 张）' : '无' }}</dd>
            </div>
            <div>
              <dt>生成数量</dt>
              <dd>1 张</dd>
            </div>
          </dl>

          <details class="generation-prompt">
            <summary>查看完整提示词</summary>
            <textarea class="generation-prompt-text" readonly rows="5" aria-label="完整提示词" :value="currentProject.brief.prompt || '尚未生成完整提示词'"></textarea>
          </details>

          <p v-if="latest">最近版本：第 {{ latest.version_no }} 版，状态：{{ generationStatusLabel(latest.status) }}</p>
          <p v-else class="muted">尚未生成图片。</p>

          <p v-if="activeGeneration" class="generation-live" role="status">
            当前生成：第 {{ activeGeneration.version_no }} 版，{{ generationStatusLabel(activeGeneration.status) }}。
          </p>
          <p v-if="generationSubmitNotice" class="status-notice" role="status">{{ generationSubmitNotice }}</p>

          <div v-if="generationUncertain" class="generation-uncertain">
            <p class="generation-error" role="alert">
              提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。
            </p>
            <button
              type="button"
              class="secondary-button"
              :disabled="generationSubmitBusy"
              @click="verifyGenerationSubmission"
            >
              {{ generationSubmitBusy ? '正在核实…' : '核实提交结果' }}
            </button>
          </div>

          <div class="generation-action-row">
            <button
              type="button"
              class="primary-button"
              :disabled="Boolean(generationDisabledReason) || generationSubmitBusy"
              @click="submitGeneration"
            >
              {{ generationSubmitBusy ? '正在提交生成请求…' : '生成首图' }}
            </button>
            <p v-if="generationDisabledReason" class="control-reason">{{ generationDisabledReason }}</p>
          </div>

          <p v-if="generationSubmitError" class="generation-error" role="alert">{{ generationSubmitError }}</p>
          <p v-if="generationQueryError" class="generation-error" role="alert">{{ generationQueryError }}</p>
          <button
            v-if="generationQueryError"
            type="button"
            class="secondary-button"
            @click="retryGenerationQuery"
          >
            重新查询状态
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

    <form v-if="showComposer" class="composer" @submit.prevent="onSubmit">
      <p v-if="sendError" class="composer-error" role="alert">{{ sendError }}</p>
      <label class="sr-only" for="workspace-prompt">描述商品与图片需求</label>
      <textarea
        id="workspace-prompt"
        ref="input"
        v-model="draft"
        class="prompt-input"
        aria-label="描述商品与图片需求"
        rows="6"
        placeholder="例如：为米色亚麻托特包生成一张简洁主图，浅暖色背景，突出材质与轮廓……"
        :disabled="sendBusy || organizing || generationSubmitBusy || Boolean(activeGeneration)"
        @keydown="onKeydown"
        @compositionstart="onCompositionStart"
        @compositionend="onCompositionEnd"
      />
      <div class="composer-actions">
        <p class="composer-hint">Enter 换行；Ctrl/⌘ + Enter 发送。</p>
        <div class="composer-buttons">
          <button type="submit" class="primary-button" :disabled="Boolean(sendDisabledReason)">
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
