<script setup lang="ts">
import AccessibleDrawer from './AccessibleDrawer.vue'
import { useWorkspace } from '@/composables/useWorkspace'
import {
  briefStatusLabel,
  displayProjectTitle,
  formatDateTime,
  generationStatusLabel,
  legacyNoticeText,
} from '@/utils/format'

const workspace = useWorkspace()
const {
  projectDrawerOpen,
  closeProjectDrawer,
  projectList,
  projectListLoading,
  projectListError,
  projectListMoreError,
  nextCursor,
  projectListLoadingMore,
  projectParam,
  createNewProject,
  loadProjects,
  loadMoreProjects,
  retryLoadMore,
  selectProject,
} = workspace

function startNewProject(): void {
  createNewProject()
}
</script>

<template>
  <AccessibleDrawer
    :open="projectDrawerOpen"
    title="已有项目"
    close-label="关闭项目列表"
    description="选择项目以只读恢复对话、需求和生成状态"
    width="360px"
    @close="closeProjectDrawer"
  >
    <div class="project-drawer-actions">
      <button type="button" class="primary-button" @click="startNewProject">新建项目</button>
    </div>

    <div v-if="projectListLoading" class="project-list-status" role="status">正在读取项目…</div>

    <div v-if="projectListError" class="status-error" role="alert">
      <strong>项目列表读取失败</strong>
      <p>{{ projectListError }}</p>
      <div class="project-drawer-actions">
        <button type="button" class="secondary-button" @click="loadProjects">重试</button>
        <button type="button" class="primary-button" @click="startNewProject">新建项目</button>
      </div>
    </div>

    <ul v-if="projectList.length" class="project-list" aria-label="项目列表">
      <li v-for="project in projectList" :key="project.id">
        <button
          type="button"
          class="project-list-item"
          :class="{ 'is-current': projectParam === project.id }"
          :aria-current="projectParam === project.id ? 'true' : undefined"
          @click="selectProject(project.id)"
        >
          <span class="project-list-title">{{ displayProjectTitle(project.title) }}</span>
          <span class="project-list-meta">
            <time :datetime="project.updated_at">{{ formatDateTime(project.updated_at) }}</time>
          </span>
          <span v-if="project.legacy_unreviewed" class="project-list-badges">
            <span class="status-badge legacy-badge">历史内容尚未审查</span>
            <span class="project-list-legacy">{{ legacyNoticeText(project.legacy_notice) }}</span>
          </span>
          <span v-else class="project-list-badges">
            <span class="status-badge">{{ briefStatusLabel(project.brief_status) }}</span>
            <span v-if="project.latest_generation_status" class="status-badge">
              生成：{{ generationStatusLabel(project.latest_generation_status) }}
            </span>
          </span>
        </button>
      </li>
    </ul>

    <div v-if="!projectListLoading && !projectListError && !projectList.length" class="project-list-empty">
      <p>暂无项目。新建项目后会显示在这里。</p>
      <button type="button" class="secondary-button" @click="startNewProject">新建项目</button>
    </div>

    <div v-if="nextCursor" class="project-list-more">
      <div v-if="projectListMoreError" class="status-error" role="alert">
        <strong>加载更多失败</strong>
        <p>{{ projectListMoreError }}</p>
        <button type="button" class="secondary-button" @click="retryLoadMore">重试</button>
      </div>
      <button
        v-else
        type="button"
        class="secondary-button"
        :disabled="projectListLoadingMore"
        @click="loadMoreProjects"
      >
        {{ projectListLoadingMore ? '正在加载更多…' : '加载更多' }}
      </button>
    </div>
  </AccessibleDrawer>
</template>
