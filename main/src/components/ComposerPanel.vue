<script setup lang="ts">
import { computed, inject, nextTick, onMounted, ref, watch } from 'vue'
import ReferenceUploader from './ReferenceUploader.vue'
import { useWorkspace } from '@/composables/useWorkspace'
import { WORKFLOW_KEY, type WorkflowController } from '@/composables/useWorkflow'
import type { WorkflowAction } from '@/api/workflow'
import { currentPublishedVersion } from '@/workflow/projection'

const workspace = useWorkspace()
const workflow = inject<WorkflowController | null>(WORKFLOW_KEY, null)
const {
  draft,
  projectParam,
  currentProject,
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
  activeGeneration,
  announce,
  refreshProjectDetail,
} = workspace
const composing = ref(false)
const promptInput = ref<HTMLTextAreaElement | null>(null)
const PROMPT_MAX_HEIGHT = 240

function resizePromptInput(): void {
  const field = promptInput.value
  if (!field) return
  field.style.height = 'auto'
  field.style.height = Math.min(field.scrollHeight, PROMPT_MAX_HEIGHT) + 'px'
  field.style.overflowY = field.scrollHeight > PROMPT_MAX_HEIGHT ? 'auto' : 'hidden'
}

watch(draft, () => {
  void nextTick(resizePromptInput)
})

onMounted(() => {
  void nextTick(resizePromptInput)
})

const hasProjectParam = computed(() => projectParam.value !== null)
const showComposer = computed(() => !hasProjectParam.value || Boolean(currentProject.value))
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
</script>

<template>
  <section v-if="showComposer" class="composer-panel" aria-label="消息输入区">
    <ReferenceUploader />

    <form class="composer" @submit.prevent="onSubmit" @dragover.prevent @drop.prevent="onComposerDrop">
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
      <div class="composer-label-row">
        <label class="composer-label" for="workspace-prompt">描述商品与图片需求</label>
        <span class="composer-label-hint">支持文字描述与图片参考</span>
      </div>
      <textarea
        id="workspace-prompt"
        ref="promptInput"
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

<style scoped>
.composer-panel {
  display: grid;
  flex: none;
  gap: var(--space-2);
  width: 100%;
  max-width: var(--conversation-max);
  margin: 0 auto;
  padding: 0 0 max(var(--space-2), env(safe-area-inset-bottom));
}

.composer {
  margin-top: 0;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: 20px;
  background: var(--color-panel);
  box-shadow: 0 10px 30px rgba(31, 36, 56, 0.08);
}

.composer-label-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-2);
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: var(--space-2);
}

.composer-label {
  color: var(--color-text);
  font-size: var(--text-body);
  font-weight: 650;
}

.composer-label-hint {
  color: var(--color-text-muted);
  font-size: 12px;
}

.prompt-input {
  max-height: 240px;
  overflow-y: hidden;
  border-radius: 20px;
  background: var(--color-panel);
  resize: none;
}

@media (max-width: 767px) {
  .composer-panel {
    padding-bottom: max(var(--space-1), env(safe-area-inset-bottom));
  }

  .composer {
    padding: var(--space-2);
  }

  .prompt-input {
    max-height: 160px;
  }

  .composer-panel :deep(.reference-uploader) {
    gap: var(--space-1);
    margin: 0;
    padding: var(--space-2);
  }

  .composer-panel :deep(.reference-uploader-heading .reference-capability),
  .composer-panel :deep(.reference-help),
  .composer-panel :deep(.reference-dropzone p) {
    display: none;
  }

  .composer-panel :deep(.reference-dropzone) {
    padding: var(--space-2);
  }
}
</style>
