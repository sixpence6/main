<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
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
  fillExample,
  announce,
  retryProject,
  openProjectDrawer,
  createNewProject,
} = workspace
const input = ref<HTMLTextAreaElement | null>(null)

const examples = [
  '米色亚麻托特包，简洁主图',
  '陶瓷马克杯，暖色生活场景',
  '木质香薰蜡烛，节日礼盒',
]

const hasProjectParam = computed(() => projectParam.value !== null)
const latest = computed(() => latestGeneration(projectGenerations.value))
const showComposer = computed(() => !hasProjectParam.value || Boolean(currentProject.value))

function useExample(text: string): void {
  fillExample(text)
  void nextTick(() => input.value?.focus())
}

function onSendShortcut(): void {
  announce('发送能力将在后续版本接入，当前输入尚未发送。')
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

        <BriefCard
          :brief="currentProject.brief"
          heading="当前需求"
          :reference-asset="currentProject.reference_asset"
        />

        <section class="conversation-history" aria-labelledby="history-title">
          <h3 id="history-title">对话记录</h3>
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
        </section>

        <section class="generation-summary" aria-labelledby="generation-title">
          <h3 id="generation-title">生成概览</h3>
          <p>生成记录 {{ projectGenerations.length }} 条</p>
          <p v-if="latest">最近版本：第 {{ latest.version_no }} 版，状态：{{ generationStatusLabel(latest.status) }}</p>
          <p v-else class="muted">尚未生成图片。</p>
          <p class="muted">T02 仅只读恢复生成记录，不轮询状态、不显示结果图，也不提供下载。</p>
        </section>
      </template>
    </template>

    <template v-else>
      <div class="conversation-heading">
        <p class="eyebrow">当前状态：未选择项目</p>
        <h2 id="conversation-title">从一个商品想法开始</h2>
        <p class="conversation-intro">
          用一段自然语言描述商品、受众、画面风格和交付用途。工作台会保留这次会话的草稿；生成与发送能力将在后续版本接入。
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

    <form v-if="showComposer" class="composer" @submit.prevent="onSendShortcut">
      <label class="sr-only" for="workspace-prompt">描述商品与图片需求</label>
      <textarea
        id="workspace-prompt"
        ref="input"
        v-model="draft"
        class="prompt-input"
        aria-label="描述商品与图片需求"
        rows="6"
        placeholder="例如：为米色亚麻托特包生成一张简洁主图，浅暖色背景，突出材质与轮廓……"
        @keydown.ctrl.enter.prevent="onSendShortcut"
        @keydown.meta.enter.prevent="onSendShortcut"
      />
      <div class="composer-actions">
        <p class="composer-hint">Enter 换行；当前为只读恢复，发送能力将在后续版本接入。</p>
        <div class="composer-buttons">
          <button type="button" class="secondary-button" disabled aria-describedby="reference-reason" title="添加参考图将在后续版本接入">
            添加参考图
          </button>
          <button type="submit" class="primary-button" disabled aria-describedby="send-reason" title="发送将在后续版本接入">
            发送
          </button>
        </div>
      </div>
      <p id="reference-reason" class="control-reason">添加参考图将在后续版本接入，当前不可用。</p>
      <p id="send-reason" class="control-reason">发送将在后续版本接入，当前输入不会发出。</p>
    </form>
  </section>
</template>
