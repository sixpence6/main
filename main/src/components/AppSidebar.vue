<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import type { ProjectSummary } from '@/api/types'
import { currentAccount, logout, sessionRevision } from '@/auth/session'
import { useWorkspace } from '@/composables/useWorkspace'
import {
  briefStatusLabel,
  displayProjectTitle,
  formatDateTime,
  generationStatusLabel,
} from '@/utils/format'

const props = withDefaults(defineProps<{ mobile?: boolean }>(), { mobile: false })
const emit = defineEmits<{ navigate: [] }>()
const workspace = useWorkspace()
const router = useRouter()
const {
  createNewProject,
  loadProjects,
  loadMoreProjects,
  retryLoadMore,
  renameProject,
  selectProject,
  setProjectQuery,
} = workspace

const accountEmail = computed(() => currentAccount.value?.email ?? '未登录')
const accountInitial = computed(() => accountEmail.value.slice(0, 1).toUpperCase())
const accountMeta = computed(() => {
  if (!currentAccount.value) return '登录后查看'
  return currentAccount.value.is_admin ? '管理员 · 本地部署' : '本地账号'
})
const activeProjectId = computed(() => workspace.currentProject.value?.id ?? workspace.projectParam.value)
const searchInput = ref(workspace.projectQuery.value)
const renamingId = ref<string | null>(null)
const renameInput = ref('')

watch(
  () => workspace.projectQuery.value,
  (query) => {
    if (searchInput.value !== query) searchInput.value = query
  },
)

watch(
  () => workspace.currentProject.value,
  (project) => {
    if (!project) return
    if (!workspace.projectList.value.some((item) => item.id === project.id)) {
      void loadProjects()
      return
    }
    // Workflow polling already gives the current project fresh status. Reflect
    // that single item instead of refetching the entire conversation list.
    const index = workspace.projectList.value.findIndex((item) => item.id === project.id)
    if (index < 0) return
    const current = workspace.projectList.value[index]
    if (
      current.title === project.title &&
      current.brief_status === project.brief_status &&
      current.updated_at === project.updated_at
    ) return
    workspace.projectList.value[index] = {
      ...current,
      title: project.title,
      brief_status: project.brief_status,
      updated_at: project.updated_at,
    }
  },
)

watch(
  sessionRevision,
  () => {
    renamingId.value = null
    renameInput.value = ''
    workspace.projectRenameError.value = ''
    if (currentAccount.value) void loadProjects()
  },
  { flush: 'sync' },
)

function newConversation(): void {
  createNewProject()
  emit('navigate')
}

function submitSearch(): void {
  setProjectQuery(searchInput.value.trim())
}

function clearSearch(): void {
  searchInput.value = ''
  setProjectQuery('')
}

function itemStatus(item: ProjectSummary): string {
  const parts = [briefStatusLabel(item.brief_status)]
  if (item.latest_generation_status) parts.push(generationStatusLabel(item.latest_generation_status))
  return parts.join(' · ')
}

function openProject(id: string): void {
  if (renamingId.value) return
  selectProject(id)
  emit('navigate')
}

async function startRename(item: ProjectSummary): Promise<void> {
  workspace.projectRenameError.value = ''
  renamingId.value = item.id
  renameInput.value = displayProjectTitle(item.title)
  await nextTick()
  const field = document.querySelector<HTMLInputElement>('.rename-input')
  field?.focus()
  field?.select()
}

function cancelRename(): void {
  renamingId.value = null
  renameInput.value = ''
  workspace.projectRenameError.value = ''
}

async function saveRename(): Promise<void> {
  if (!renamingId.value) return
  const ok = await renameProject(renamingId.value, renameInput.value)
  if (ok) cancelRename()
}

async function signOut(): Promise<void> {
  try {
    await logout()
  } finally {
    emit('navigate')
    await router.push({ name: 'login' })
  }
}

onMounted(() => {
  void loadProjects()
})
</script>

<template>
  <aside class="app-sidebar" :class="{ 'is-mobile': props.mobile }" aria-label="对话侧栏">
    <div class="side-brand">
      <span class="brand-mark" aria-hidden="true">↗</span>
      <span class="brand-name">电商生图工作台</span>
    </div>

    <button type="button" class="new-chat-button" @click="newConversation">
      <span aria-hidden="true">＋</span>
      新对话
    </button>

    <form class="side-search" role="search" @submit.prevent="submitSearch">
      <span class="search-icon" aria-hidden="true">⌕</span>
      <input
        v-model="searchInput"
        class="search-input"
        type="search"
        aria-label="按对话标题搜索"
        placeholder="搜索对话"
        autocomplete="off"
      />
      <button
        v-if="searchInput"
        type="button"
        class="search-clear"
        aria-label="清空搜索"
        @click="clearSearch"
      >×</button>
    </form>

    <div class="side-conversations">
      <p v-if="workspace.projectListLoading.value && !workspace.projectList.value.length" class="side-hint">
        正在读取对话…
      </p>
      <div v-else-if="workspace.projectListError.value" class="side-error" role="alert">
        <p>{{ workspace.projectListError.value }}</p>
        <button type="button" class="side-retry" @click="loadProjects">重试</button>
      </div>
      <p
        v-else-if="!workspace.projectList.value.length && workspace.projectQuery.value.trim()"
        class="side-hint"
      >没有标题匹配“{{ workspace.projectQuery.value.trim() }}”的对话</p>
      <p v-else-if="!workspace.projectList.value.length" class="side-hint">
        暂无对话。新建对话后会显示在这里。
      </p>
      <ul v-else class="conversation-list" aria-label="对话列表">
        <li v-for="item in workspace.projectList.value" :key="item.id" class="conversation-item">
          <form
            v-if="renamingId === item.id"
            class="rename-form"
            @submit.prevent="saveRename"
          >
            <input
              v-model="renameInput"
              class="rename-input"
              type="text"
              aria-label="对话标题"
              maxlength="120"
            />
            <div class="rename-actions">
              <button type="submit" class="rename-save" :disabled="workspace.projectRenameBusy.value">
                保存
              </button>
              <button type="button" class="rename-cancel" @click="cancelRename">取消</button>
            </div>
            <p v-if="workspace.projectRenameError.value" class="rename-error" role="alert">
              {{ workspace.projectRenameError.value }}
            </p>
          </form>
          <template v-else>
            <button
              type="button"
              class="conversation-select"
              :aria-current="activeProjectId === item.id ? 'page' : undefined"
              @click="openProject(item.id)"
            >
              <span class="conversation-title">{{ displayProjectTitle(item.title) }}</span>
              <span class="conversation-meta">
                <span>{{ itemStatus(item) }}</span>
                <time :datetime="item.updated_at">{{ formatDateTime(item.updated_at) }}</time>
              </span>
            </button>
            <button
              type="button"
              class="conversation-rename"
              aria-label="重命名对话"
              @click="startRename(item)"
            >✎</button>
          </template>
        </li>
      </ul>
      <template v-if="workspace.nextCursor.value && !workspace.projectListLoading.value">
        <button
          type="button"
          class="load-more"
          :disabled="workspace.projectListLoadingMore.value"
          @click="loadMoreProjects"
        >{{ workspace.projectListLoadingMore.value ? '正在加载…' : '加载更多' }}</button>
      </template>
      <div v-if="workspace.projectListMoreError.value" class="side-error" role="alert">
        <p>{{ workspace.projectListMoreError.value }}</p>
        <button type="button" class="side-retry" @click="retryLoadMore">重试加载</button>
      </div>
    </div>

    <div class="side-bottom">
      <RouterLink class="side-user" :to="{ name: 'account-security' }">
        <span class="side-avatar" aria-hidden="true">{{ accountInitial }}</span>
        <span class="side-user-copy">
          <strong>{{ accountEmail }}</strong>
          <small>{{ accountMeta }}</small>
        </span>
      </RouterLink>
      <RouterLink class="side-meta-link" :to="{ name: 'account-security' }">
        <span class="meta-icon" aria-hidden="true">◔</span>
        <span class="meta-copy"><strong>用量</strong><small>查看生成额度与记录</small></span>
      </RouterLink>
      <details class="data-note">
        <summary>数据使用说明</summary>
        <p>描述和图片会发送给已配置的外部模型服务用于整理与生成。请勿提交密码、凭证或恢复链接。</p>
      </details>
      <button v-if="currentAccount" type="button" class="side-signout" @click="signOut">退出登录</button>
    </div>
  </aside>
</template>

<style scoped>
.app-sidebar {
  display: flex;
  flex: none;
  flex-direction: column;
  width: var(--sidebar-width);
  min-width: var(--sidebar-width);
  height: 100%;
  padding: 0;
  overflow: hidden;
  border-right: 1px solid var(--line-inverse);
  border-radius: 0;
  background: var(--surface-shell);
  box-shadow: none;
}
.app-sidebar.is-mobile {
  width: 100%;
  min-width: 0;
  height: 100%;
  border-right: 0;
}
.side-brand {
  display: flex;
  flex: none;
  align-items: center;
  gap: var(--space-3);
  height: var(--topbar-height);
  padding: 0 var(--space-3);
  border-bottom: 1px solid var(--line-inverse);
}
.brand-mark {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  background: var(--surface-shell-active);
  color: var(--text-inverse-secondary);
  font-size: var(--text-body);
  font-weight: 600;
}
.brand-name {
  overflow: hidden;
  color: var(--text-inverse-primary);
  font-size: var(--text-body);
  font-weight: 600;
  letter-spacing: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.new-chat-button {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  width: calc(100% - 24px);
  height: 36px;
  margin: var(--space-3) var(--space-3) var(--space-2);
  padding: 0 var(--space-3);
  border: 1px solid var(--line-inverse-control);
  border-radius: var(--radius-sm);
  background: var(--surface-shell-raised);
  color: var(--text-inverse-primary);
  font-size: var(--text-body);
  font-weight: 500;
}
.new-chat-button:hover { background: var(--surface-shell-hover); }
.side-search {
  display: flex;
  flex: none;
  align-items: center;
  gap: var(--space-2);
  height: 36px;
  margin: 0 var(--space-3) var(--space-2);
  padding: 0 var(--space-3);
  border: 1px solid var(--line-inverse-control);
  border-radius: var(--radius-sm);
  background: var(--surface-shell-raised);
}
.side-search:focus-within {
  outline: 2px solid var(--focus-ring-dark);
  outline-offset: 2px;
}
.search-icon { flex: none; color: var(--text-inverse-meta); font-size: var(--icon-sm); line-height: 1; }
.search-input {
  min-width: 0;
  flex: 1;
  height: 34px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-inverse-primary);
  font: inherit;
  font-size: var(--text-body);
}
.search-input::placeholder { color: var(--text-inverse-muted); }
.search-input::-webkit-search-cancel-button { display: none; }
.search-clear {
  display: grid;
  flex: none;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-inverse-meta);
  font-size: 16px;
  line-height: 1;
}
.search-clear:hover { background: var(--surface-shell-hover); color: var(--text-inverse-primary); }
.side-conversations {
  min-height: 0;
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 2px var(--space-2) var(--space-2);
  scrollbar-color: var(--line-inverse) transparent;
  scrollbar-width: thin;
}
.side-conversations::-webkit-scrollbar { width: 8px; }
.side-conversations::-webkit-scrollbar-track { background: transparent; }
.side-conversations::-webkit-scrollbar-thumb {
  border: 2px solid transparent;
  border-radius: var(--radius-pill);
  background: var(--line-inverse);
  background-clip: padding-box;
}
.side-conversations::-webkit-scrollbar-thumb:hover { background: var(--line-inverse-control); background-clip: padding-box; }
.side-hint {
  margin: var(--space-4) var(--space-2);
  color: var(--text-inverse-meta);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
  text-align: center;
}
.side-error {
  margin: var(--space-2) var(--space-1);
  padding: 10px;
  border: 1px solid var(--line-inverse-control);
  border-radius: var(--radius-sm);
  background: var(--surface-shell-raised);
  color: var(--text-inverse-secondary);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
}
.side-error p { margin: 0; }
.side-retry {
  margin-top: var(--space-2);
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-inverse-primary);
  font-size: var(--text-caption);
  text-decoration: underline;
}
.conversation-list { display: flex; flex-direction: column; gap: 2px; margin: 0; padding: 0; list-style: none; }
.conversation-item {
  position: relative;
  display: flex;
  align-items: center;
  height: 36px;
  border-radius: var(--radius-sm);
}
.conversation-item:hover { background: var(--surface-shell-hover); }
.conversation-item:has(.rename-form) { height: auto; }
.conversation-select {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  height: 36px;
  min-width: 0;
  padding: 0 var(--space-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-inverse-secondary);
  text-align: left;
}
.conversation-select[aria-current='page'] {
  background: var(--surface-shell-active);
  color: var(--text-inverse-primary);
}
.conversation-select[aria-current='page']::before {
  content: '';
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 4px;
  width: 2px;
  border-radius: 1px;
  background: var(--text-inverse-secondary);
}
.conversation-title {
  min-width: 0;
  flex: 1 1 auto;
  overflow: hidden;
  font-size: var(--text-body);
  font-weight: 500;
  line-height: var(--text-body-line);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conversation-meta {
  display: flex;
  flex: none;
  gap: var(--space-1);
  max-width: 48%;
  overflow: hidden;
  color: var(--text-inverse-meta);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
  white-space: nowrap;
}
.conversation-meta span,
.conversation-meta time { overflow: hidden; text-overflow: ellipsis; }
.conversation-rename {
  display: grid;
  flex: none;
  place-items: center;
  width: 24px;
  height: 24px;
  margin-right: var(--space-1);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-inverse-meta);
  opacity: 0;
  visibility: hidden;
  transform: scale(.94);
  transition: opacity var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out), background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
.conversation-item:hover .conversation-rename,
.conversation-item:focus-within .conversation-rename,
.conversation-rename:focus-visible {
  opacity: 1;
  visibility: visible;
  transform: scale(1);
}
.conversation-rename:hover { background: var(--surface-shell-hover); color: var(--text-inverse-primary); }
@media (hover: none) {
  .conversation-rename { opacity: 1; visibility: visible; transform: none; }
}
.rename-form { width: 100%; padding: var(--space-1) var(--space-2); border-radius: var(--radius-sm); }
.rename-input {
  width: 100%;
  height: 28px;
  padding: 0 var(--space-2);
  border: 1px solid var(--line-inverse-control);
  border-radius: var(--radius-sm);
  background: var(--surface-shell-raised);
  color: var(--text-inverse-primary);
  font: inherit;
  font-size: var(--text-caption);
}
.rename-actions { display: flex; gap: var(--space-1); margin-top: var(--space-1); }
.rename-save,
.rename-cancel {
  height: 24px;
  padding: 0 var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  font-size: var(--text-caption);
}
.rename-save { background: var(--action-primary); color: var(--action-primary-text); }
.rename-cancel { background: transparent; color: var(--text-inverse-meta); }
.rename-error { margin: var(--space-1) var(--space-1) 0; color: var(--text-inverse-secondary); font-size: var(--text-caption); }
.load-more {
  display: block;
  width: calc(100% - var(--space-3));
  margin: var(--space-2) var(--space-1);
  padding: var(--space-1);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-inverse-secondary);
  font-size: var(--text-caption);
}
.load-more:hover { background: var(--surface-shell-hover); }
.side-bottom {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2) 0;
  border-top: 1px solid var(--line-inverse);
}
.side-user {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  color: var(--text-inverse-secondary);
  text-decoration: none;
}
.side-user:hover { background: var(--surface-shell-hover); }
.side-avatar {
  display: grid;
  flex: none;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-pill);
  background: var(--surface-shell-active);
  color: var(--text-inverse-primary);
  font-size: var(--text-caption);
  font-weight: 600;
  text-transform: uppercase;
}
.side-user-copy { display: grid; min-width: 0; }
.side-user-copy strong {
  overflow: hidden;
  color: var(--text-inverse-primary);
  font-size: var(--text-body);
  font-weight: 500;
  line-height: var(--text-body-line);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.side-user-copy small {
  overflow: hidden;
  color: var(--text-inverse-meta);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.side-meta-link {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  color: var(--text-inverse-secondary);
  text-decoration: none;
}
.side-meta-link:hover { background: var(--surface-shell-hover); }
.meta-icon {
  display: grid;
  flex: none;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  background: var(--surface-shell-active);
  color: var(--text-inverse-secondary);
  font-size: var(--icon-sm);
}
.meta-copy { display: grid; min-width: 0; }
.meta-copy strong { color: var(--text-inverse-secondary); font-size: var(--text-body); font-weight: 500; line-height: var(--text-body-line); }
.meta-copy small {
  overflow: hidden;
  color: var(--text-inverse-meta);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.data-note {
  margin: var(--space-1) var(--space-3) 0;
  color: var(--text-inverse-meta);
  font-size: var(--text-caption);
  line-height: var(--text-caption-line);
}
.data-note summary { cursor: pointer; }
.data-note p { margin: var(--space-2) 0 0; }
.side-signout {
  align-self: flex-start;
  margin: var(--space-1) var(--space-3) 0;
  padding: 2px 0;
  border: 0;
  background: transparent;
  color: var(--text-inverse-meta);
  font-size: var(--text-caption);
}
.side-signout:hover { color: var(--text-inverse-primary); }
</style>
