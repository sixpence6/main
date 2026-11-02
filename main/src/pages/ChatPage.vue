<script setup lang="ts">
import { computed, nextTick, onMounted, provide, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ConversationPanel from '@/components/ConversationPanel.vue'
import HistoryPreview from '@/components/HistoryPreview.vue'
import WorkflowPanel from '@/components/workflow/WorkflowPanel.vue'
import { useWorkflow, WORKFLOW_KEY } from '@/composables/useWorkflow'
import { useWorkspace } from '@/composables/useWorkspace'
import { displayProjectTitle } from '@/utils/format'
import { currentPublishedVersion } from '@/workflow/projection'

const workspace = useWorkspace()
const { currentProject, projectParam } = workspace
const workflow = useWorkflow(projectParam)
provide(WORKFLOW_KEY, workflow)
const route = useRoute()
const router = useRouter()

const progressLabel = computed(() => {
  const state = workflow.view.value?.task_state
  return state?.progress.stage_label ?? ''
})
const publishedVersion = computed(() =>
  currentPublishedVersion(currentProject.value, workflow.view.value),
)
const heading = computed(() =>
  currentProject.value ? displayProjectTitle(currentProject.value.title) : '新建生图需求',
)

let baselineReady = false
let baselineProject: string | null = null
let previousPublishedDeliveryId: string | null = null

watch(
  projectParam,
  () => {
    baselineReady = false
    baselineProject = null
    previousPublishedDeliveryId = null
  },
  { flush: 'sync' },
)

watch(
  () => workflow.view.value,
  (view) => {
    const projectId = projectParam.value
    if (!projectId || !view) return
    if (!baselineReady || baselineProject !== projectId) {
      baselineReady = true
      baselineProject = projectId
      previousPublishedDeliveryId = currentPublishedVersion(currentProject.value, view)
        ? (view.delivery?.id ?? null)
        : null
      return
    }
    const version = currentPublishedVersion(currentProject.value, view)
    const publishedDeliveryId = version ? (view.delivery?.id ?? null) : null
    const previous = previousPublishedDeliveryId
    previousPublishedDeliveryId = publishedDeliveryId
    if (route.name !== 'project-chat') return
    if (!version || !version.asset_id || !version.can_download) return
    if (!publishedDeliveryId || previous !== null) return
    void router.push({ name: 'project-results', params: { projectId } })
  },
  { flush: 'post' },
)

function openResults(): void {
  const id = projectParam.value
  if (id) void router.push({ name: 'project-results', params: { projectId: id } })
}

function focusComposer(): void {
  if (route.hash === '#composer') {
    void nextTick(() => document.getElementById('workspace-prompt')?.focus())
  }
}

onMounted(focusComposer)
watch(currentProject, focusComposer, { flush: 'post' })
</script>

<template>
  <section class="chat-page" aria-label="需求工作台">
    <div class="workspace-page-topbar">
      <div class="workspace-page-heading">
        <p class="eyebrow">需求对话</p>
        <p id="chat-page-title" class="workspace-page-title">{{ heading }}</p>
      </div>
      <div class="workspace-page-status">
        <span v-if="progressLabel" class="status-badge">{{ progressLabel }}</span>
        <button
          v-if="publishedVersion"
          type="button"
          class="secondary-button"
          @click="openResults"
        >
          查看当前结果
        </button>
      </div>
    </div>

    <section class="model-data-notice" aria-labelledby="model-data-notice-title">
      <h2 id="model-data-notice-title">外部模型数据说明</h2>
      <p>
        你在工作台提交的文字描述、需求卡信息和参考图会发送给已配置的外部模型服务，用于需求整理、图片生成和结果核验。请勿在描述或图片中填写密码、会话凭证、恢复链接或其他秘密信息。模型服务可能按其自身政策处理数据，本应用不承诺供应商零保留。
      </p>
    </section>

    <div class="chat-stream">
      <ConversationPanel />
      <WorkflowPanel />
      <HistoryPreview />
    </div>
  </section>
</template>

<style scoped>
.chat-page { display: flex; flex-direction: column; gap: var(--space-4); }
.workspace-page-topbar { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--space-4); flex-wrap: wrap; }
.workspace-page-heading { display: grid; gap: 2px; }
.workspace-page-title { margin: 0; font-size: clamp(20px, 2.2vw, 26px); line-height: 1.25; font-weight: 700; }
.workspace-page-heading .eyebrow { margin: 0; color: var(--color-text-muted); font-size: var(--text-small); letter-spacing: 0.08em; text-transform: uppercase; }
.workspace-page-status { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.chat-stream { display: flex; flex-direction: column; gap: var(--space-4); width: 100%; max-width: 880px; margin: 0 auto; min-width: 0; }
.chat-stream :deep(.conversation-panel), .chat-stream :deep(.workflow-panel), .chat-stream :deep(.history-preview) { max-width: none; margin: 0; width: 100%; }
.model-data-notice { display: grid; gap: var(--space-1); padding: var(--space-3) var(--space-4); border: 1px solid var(--color-border); border-left: 3px solid var(--color-warning); border-radius: var(--radius-control); background: var(--color-panel); }
.model-data-notice h2 { margin: 0; font-size: var(--text-body); line-height: var(--text-body-line); }
.model-data-notice p { margin: 0; color: var(--color-text-muted); font-size: var(--text-body); line-height: var(--text-body-line); }
@media (max-width: 767px) { .chat-stream { gap: var(--space-3); } }
</style>
