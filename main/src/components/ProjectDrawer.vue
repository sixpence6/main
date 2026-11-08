<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
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
  projectQuery,
  projectSearchBusy,
  projectRenameBusy,
  projectRenameError,
  createNewProject,
  loadProjects,
  loadMoreProjects,
  retryLoadMore,
  selectProject,
  setProjectQuery,
  renameProject,
} = workspace

const searchText = ref(projectQuery.value)
const renamingId = ref<string | null>(null)
const renameText = ref('')
let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(projectQuery, (value) => {
  if (value !== searchText.value) searchText.value = value
})

function clearSearchTimer(): void {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
}

function onSearchInput(): void {
  clearSearchTimer()
  searchTimer = setTimeout(() => setProjectQuery(searchText.value), 300)
}

function submitSearch(): void {
  clearSearchTimer()
  setProjectQuery(searchText.value)
}

function clearSearch(): void {
  clearSearchTimer()
  searchText.value = ''
  setProjectQuery('')
}

function startNewProject(): void {
  createNewProject()
}

function startRename(projectId: string, title: string | null | undefined): void {
  renamingId.value = projectId
  renameText.value = displayProjectTitle(title)
  void nextTick(() => document.getElementById('project-rename-' + projectId)?.focus())
}

function cancelRename(): void {
  renamingId.value = null
  renameText.value = ''
}

async function submitRename(projectId: string): Promise<void> {
  const saved = await renameProject(projectId, renameText.value)
  if (saved) cancelRename()
}
</script>

<template>
  <AccessibleDrawer
    :open="projectDrawerOpen"
    title="已有项目"
    close-label="关闭项目列表"
    description="选择项目以恢复对话、需求和生成状态"
    width="360px"
    @close="closeProjectDrawer"
  >
    <div class="project-drawer-actions">
      <button type="button" class="primary-button" @click="startNewProject">新建项目</button>
    </div>

    <form class="project-search" role="search" @submit.prevent="submitSearch">
      <label class="sr-only" for="project-search-input">按项目标题搜索</label>
      <input
        id="project-search-input"
        v-model="searchText"
        type="search"
        class="project-search-input"
        placeholder="按项目标题搜索"
        aria-label="按项目标题搜索"
        @input="onSearchInput"
        @keydown.enter.prevent="submitSearch"
      />
      <button v-if="searchText" type="button" class="secondary-button" @click="clearSearch">
        清空
      </button>
      <button type="submit" class="secondary-button" :disabled="projectSearchBusy">
        {{ projectSearchBusy ? '搜索中…' : '搜索' }}
      </button>
    </form>
    <p v-if="projectSearchBusy" class="project-list-status" role="status">正在搜索项目…</p>
    <p v-if="projectListLoading && !projectSearchBusy" class="project-list-status" role="status">正在读取项目</p>

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
        <div class="project-list-entry" :class="{ 'is-current': projectParam === project.id }">
          <button
            type="button"
            class="project-list-item"
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
          <button
            type="button"
            class="project-rename-button"
            :aria-label="'重命名项目'"
            :title="'重命名 ' + displayProjectTitle(project.title)"
            :disabled="projectRenameBusy"
            @click="startRename(project.id, project.title)"
          >
            重命名
          </button>
        </div>
        <form
          v-if="renamingId === project.id"
          class="project-rename-form"
          @submit.prevent="submitRename(project.id)"
        >
          <label class="sr-only" :for="'project-rename-' + project.id">项目标题</label>
          <input
            :id="'project-rename-' + project.id"
            v-model="renameText"
            type="text"
            maxlength="120"
            aria-label="项目标题"
            :disabled="projectRenameBusy"
          />
          <button type="submit" class="primary-button" :disabled="projectRenameBusy">
            {{ projectRenameBusy ? '保存中…' : '保存' }}
          </button>
          <button type="button" class="secondary-button" :disabled="projectRenameBusy" @click="cancelRename">
            取消
          </button>
        </form>
        <p v-if="renamingId === project.id && projectRenameError" class="status-error" role="alert">
          {{ projectRenameError }}
        </p>
      </li>
    </ul>

    <div v-if="!projectListLoading && !projectListError && !projectList.length" class="project-list-empty">
      <p v-if="projectQuery.trim()">没有标题匹配“{{ projectQuery }}”的项目。</p>
      <p v-else>暂无项目。新建项目后会显示在这里。</p>
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

<style scoped>
.project-search {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  margin: var(--space-3) 0;
}
.project-search-input {
  flex: 1;
  min-width: 0;
  height: 36px;
  padding: 0 var(--space-3);
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-sm);
  background: var(--surface-panel);
  color: var(--text-primary);
}
.project-list-entry {
  display: flex;
  align-items: stretch;
  gap: var(--space-2);
  border-radius: var(--radius-sm);
}
.project-list-entry.is-current {
  background: var(--action-soft, var(--action-soft));
}
.project-list-entry .project-list-item {
  flex: 1;
  min-width: 0;
}
.project-rename-button {
  align-self: center;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-size: var(--text-caption);
  padding: var(--space-2);
}
.project-rename-button:hover {
  color: var(--text-primary);
}
.project-rename-form {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  padding: var(--space-2) 0;
}
.project-rename-form input {
  flex: 1;
  min-width: 0;
  height: 34px;
  padding: 0 var(--space-2);
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-sm);
}
</style>
