<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { assetUrl } from '@/api/client'
import type { GenerationView } from '@/api/types'
import type { WorkflowVersion } from '@/api/workflow'
import { publishLiveStatus, type LiveStatusInput } from '@/composables/useLiveStatus'
import { useWorkflow } from '@/composables/useWorkflow'
import { useWorkspace } from '@/composables/useWorkspace'
import { currentPublishedVersion } from '@/workflow/projection'
import { displayProjectTitle, formatDateTime } from '@/utils/format'

const workspace = useWorkspace()
const { currentProject, projectParam, projectGenerations, projectLoading } = workspace
const workflow = useWorkflow(projectParam)
const route = useRoute()
const router = useRouter()
const resultsFigure = ref<HTMLElement | null>(null)

const requestedRunId = computed(() =>
  typeof route.query.version === 'string' && route.query.version.trim()
    ? route.query.version.trim()
    : null,
)
const versions = computed<WorkflowVersion[]>(() => workflow.view.value?.versions ?? [])
const currentVersion = computed(() =>
  currentPublishedVersion(currentProject.value, workflow.view.value),
)
const displayedVersion = computed<WorkflowVersion | null>(() => {
  if (requestedRunId.value) {
    return versions.value.find(
      (item) =>
        item.run_id === requestedRunId.value &&
        item.status === 'published' &&
        item.can_download &&
        Boolean(item.asset_id),
    ) ?? null
  }
  return currentVersion.value
})
const isHistory = computed(
  () =>
    Boolean(displayedVersion.value) &&
    Boolean(requestedRunId.value) &&
    displayedVersion.value?.run_id !== currentVersion.value?.run_id,
)
const generation = computed<GenerationView | null>(() => {
  const assetId = displayedVersion.value?.asset_id
  if (!assetId) return null
  return projectGenerations.value.find((item) => item.result_asset?.id === assetId) ?? null
})
const historyVersions = computed(() =>
  versions.value.filter((item) => item.status === 'published' && item.can_download && item.asset_id),
)
const downloadHref = computed(() => {
  const assetId = displayedVersion.value?.asset_id
  return assetId ? assetUrl(assetId) + '?download=true' : ''
})
const promptText = computed(() => {
  const prompt = displayedVersion.value?.prompt
  if (!prompt) return ''
  const positive = typeof prompt.positive_prompt === 'string' ? prompt.positive_prompt : ''
  const negative = typeof prompt.negative_prompt === 'string' ? prompt.negative_prompt : ''
  return [positive, negative ? '负向提示词：' + negative : ''].filter(Boolean).join('\n\n')
})
const unavailableText = computed(() => {
  if (requestedRunId.value && workflow.loading.value) return ''
  if (requestedRunId.value) {
    return '该版本不存在、尚未发布、已撤销或无权访问。请返回对话选择当前结果，系统不会用其他版本替代。'
  }
  return '当前没有可访问的已发布结果。请返回对话继续生成；系统不会展示未发布或旧图片作为当前结果。'
})
const heading = computed(() =>
  currentProject.value ? displayProjectTitle(currentProject.value.title) : '生成结果',
)

function backToChat(): void {
  const id = projectParam.value
  if (id) void router.push({ name: 'project-chat', params: { projectId: id } })
  else void router.push({ name: 'describe' })
}

function backToModify(): void {
  const id = projectParam.value
  if (id) void router.push({ name: 'project-chat', params: { projectId: id }, hash: '#composer' })
  else void router.push({ name: 'describe', hash: '#composer' })
}

function selectVersion(runId: string): void {
  const id = projectParam.value
  if (!id) return
  void router.push({
    name: 'project-results',
    params: { projectId: id },
    query: { version: runId },
  })
}

function versionLabel(version: WorkflowVersion): string {
  return '版本 ' + version.sequence_no + ' · 需求 v' + version.requirement_revision
}

function formatVersionSize(version: WorkflowVersion): string {
  if (version.width && version.height) return version.width + ' × ' + version.height
  const item = projectGenerations.value.find(
    (generation) => generation.result_asset?.id === version.asset_id,
  )
  if (item?.result_asset) return item.result_asset.width + ' × ' + item.result_asset.height
  return '尺寸信息不可用'
}

const liveStatusInput = computed<LiveStatusInput>(() => {
  const projectTitle = currentProject.value ? displayProjectTitle(currentProject.value.title) : '生成结果'
  const projectId = projectParam.value
  const base = { projectId, projectTitle, detail: '', busy: false }
  if (projectLoading.value || workflow.loading.value) {
    return { ...base, label: '正在读取结果', detail: '正在恢复当前版本和历史版本…', tone: 'working', busy: true }
  }
  if (!displayedVersion.value) {
    return { ...base, label: '结果暂不可用', detail: unavailableText.value, tone: 'waiting' }
  }
  if (isHistory.value) {
    return {
      ...base,
      label: '正在查看历史版本',
      detail: `${versionLabel(displayedVersion.value)}，修改仍基于当前交付。`,
      tone: 'idle',
    }
  }
  return {
    ...base,
    label: '结果已就绪',
    detail: `${versionLabel(displayedVersion.value)} · 可以下载原图或返回对话修改。`,
    tone: 'success',
  }
})

watch(
  liveStatusInput,
  (next) => publishLiveStatus(next),
  { immediate: true, flush: 'post' },
)

watch(
  () => route.query.version,
  async (version) => {
    if (!version) return
    await nextTick()
    resultsFigure.value?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  },
  { flush: 'post' },
)
</script>

<template>
  <section class="results-page" aria-labelledby="results-page-title">
    <div class="workspace-page-topbar">
      <div class="workspace-page-heading">
        <p class="eyebrow">{{ isHistory ? '历史结果' : '当前结果' }}</p>
        <h1 id="results-page-title">{{ heading }}</h1>
      </div>
      <div class="workspace-page-status">
        <button type="button" class="secondary-button" @click="backToChat">返回对话</button>
        <button type="button" class="primary-button" @click="backToModify">返回对话修改</button>
      </div>
    </div>

    <div v-if="projectLoading || workflow.loading.value" class="results-status" role="status">
      正在读取结果…
    </div>
    <div v-else-if="!displayedVersion" class="results-unavailable" role="status">
      <strong>结果不可用</strong>
      <p>{{ unavailableText }}</p>
      <button type="button" class="secondary-button" @click="backToChat">返回对话</button>
    </div>
    <template v-else>
      <article class="results-current">
        <figure ref="resultsFigure" class="results-figure">
          <img
            :src="assetUrl(displayedVersion.asset_id!)"
            :alt="displayedVersion.caption || '生成结果图片'"
            :width="displayedVersion.width ?? undefined"
            :height="displayedVersion.height ?? undefined"
            :style="displayedVersion.width && displayedVersion.height
              ? { aspectRatio: displayedVersion.width + ' / ' + displayedVersion.height }
              : undefined"
            class="results-image"
            loading="eager"
            decoding="async"
          />
          <figcaption>
            {{ isHistory ? '历史版本' : '当前版本' }} · {{ versionLabel(displayedVersion) }}
          </figcaption>
        </figure>
        <p v-if="isHistory" class="results-history-note" role="note">
          第 {{ displayedVersion.sequence_no }} 版历史成果；
          <template v-if="currentVersion">修改仍基于当前第 {{ currentVersion.sequence_no }} 版交付。</template>
          <template v-else>当前没有可调整的当前交付，修改请在原对话继续完善当前需求。</template>
        </p>
        <dl class="results-meta">
          <div>
            <dt>需求版本</dt>
            <dd>v{{ displayedVersion.requirement_revision }}</dd>
          </div>
          <div>
            <dt>实际尺寸</dt>
            <dd>{{ formatVersionSize(displayedVersion) }}</dd>
          </div>
          <div>
            <dt>生成时间</dt>
            <dd>{{ formatDateTime(displayedVersion.published_at || displayedVersion.created_at) }}</dd>
          </div>
        </dl>
        <div class="results-actions">
          <a
            v-if="displayedVersion.can_download && downloadHref"
            class="primary-button"
            :href="downloadHref"
            download
          >
            下载原图
          </a>
          <button type="button" class="secondary-button" @click="backToModify">返回对话修改</button>
        </div>
      </article>

      <details v-if="promptText" class="results-prompt">
        <summary>该版本实际使用的已批准提示词</summary>
        <pre>{{ promptText }}</pre>
      </details>
      <details v-else class="results-prompt">
        <summary>该版本提示词不可用</summary>
        <p>该版本未保存可展示的已批准提示词；系统不会用当前提示词替代。</p>
      </details>

    </template>

    <details
      v-if="historyVersions.length"
      class="results-history"
      role="group"
      aria-labelledby="results-history-title"
    >
      <summary>
        <span id="results-history-title">历史结果</span>
        <span class="results-history-count">（{{ historyVersions.length }}）</span>
      </summary>
      <ul>
        <li v-for="version in historyVersions" :key="version.run_id">
          <button
            type="button"
            class="history-version-button"
            :class="{ 'is-current': version.run_id === displayedVersion?.run_id }"
            :aria-current="version.run_id === displayedVersion?.run_id ? 'true' : undefined"
            @click="selectVersion(version.run_id)"
          >
            <img
              :src="assetUrl(version.asset_id!)"
              :width="version.width ?? undefined"
              :height="version.height ?? undefined"
              alt=""
              loading="lazy"
              decoding="async"
            />
            <span>{{ versionLabel(version) }}</span>
            <time :datetime="version.published_at || version.created_at">
              {{ formatDateTime(version.published_at || version.created_at) }}
            </time>
            <span class="history-version-meta">
              需求 v{{ version.requirement_revision }} · {{ formatVersionSize(version) }}
            </span>
          </button>
        </li>
      </ul>
    </details>
  </section>
</template>

<style scoped>
.results-page { display: flex; flex-direction: column; gap: var(--space-4); }
.workspace-page-topbar { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--space-4); flex-wrap: wrap; }
.workspace-page-heading { display: grid; gap: 2px; }
.workspace-page-heading h1 { margin: 0; font-size: clamp(20px, 2.2vw, 26px); line-height: 1.25; }
.workspace-page-heading .eyebrow { margin: 0; color: var(--color-text-muted); font-size: var(--text-small); letter-spacing: 0.08em; text-transform: uppercase; }
.workspace-page-status { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.results-status, .results-unavailable { padding: var(--space-5); border: 1px solid var(--color-border); border-radius: var(--radius-panel); background: var(--color-panel); color: var(--color-text-muted); display: grid; gap: var(--space-2); justify-items: start; }
.results-unavailable strong { color: var(--color-text); font-size: var(--text-heading); }
.results-unavailable p { margin: 0; max-width: 60ch; }
.results-current { display: grid; gap: var(--space-4); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-panel); background: var(--color-panel); }
.results-figure { margin: 0; display: grid; gap: var(--space-2); justify-items: center; }
.results-image { display: block; width: 100%; height: auto; max-height: 72vh; object-fit: contain; border-radius: var(--radius-control); background: var(--color-page); }
.results-figure figcaption { color: var(--color-text-muted); font-size: var(--text-small); }
.results-meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-3); margin: 0; }
.results-meta div { display: grid; gap: 2px; }
.results-meta dt { color: var(--color-text-muted); font-size: var(--text-small); }
.results-meta dd { margin: 0; font-weight: 600; }
.results-actions { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.results-prompt { border: 1px solid var(--color-border); border-radius: var(--radius-control); background: var(--color-panel); padding: var(--space-3) var(--space-4); }
.results-prompt summary { cursor: pointer; font-weight: 600; }
.results-prompt pre { margin: var(--space-3) 0 0; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--color-text-muted); font-family: inherit; }
.results-prompt p { margin: var(--space-2) 0 0; color: var(--color-text-muted); }
.results-history-note { margin: 0; padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-control); background: var(--color-page); color: var(--color-text-muted); }
.results-history { border: 1px solid var(--color-border); border-radius: var(--radius-control); background: var(--color-panel); padding: var(--space-3) var(--space-4); }
.results-history summary { cursor: pointer; font-weight: 600; }
.results-history-count { color: var(--color-text-muted); font-weight: 400; }
.results-history ul { list-style: none; margin: var(--space-3) 0 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: var(--space-3); }
.history-version-button { width: 100%; display: grid; gap: 4px; padding: var(--space-2); border: 1px solid var(--color-border); border-radius: var(--radius-control); background: var(--color-panel); color: inherit; text-align: left; cursor: pointer; }
.history-version-button.is-current { border-color: var(--color-primary); box-shadow: 0 0 0 1px var(--color-primary); }
.history-version-button img { width: 100%; aspect-ratio: 4 / 3; object-fit: cover; border-radius: var(--radius-control); background: var(--color-page); }
.history-version-button span { font-size: var(--text-small); font-weight: 600; }
.history-version-button time, .history-version-meta { color: var(--color-text-muted); font-size: var(--text-small); font-weight: 400; }
@media (max-width: 767px) { .results-meta { grid-template-columns: minmax(0, 1fr); } .results-current { padding: var(--space-3); } }
</style>
