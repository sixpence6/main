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

const accountLabel = computed(() => currentAccount.value?.email ?? '登录后查看用量')
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
      <RouterLink class="side-meta-link" :to="{ name: 'account-security' }">
        <span class="meta-icon" aria-hidden="true">◯</span>
        <span class="meta-copy">
          <strong>账号与用量</strong>
          <small>{{ accountLabel }}</small>
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
  flex-direction: column;
  width: var(--sidebar-width);
  min-width: var(--sidebar-width);
  height: 100%;
  padding: 24px 16px 18px;
  overflow: hidden;
  border-radius: var(--radius-shell);
  background: var(--color-panel);
  box-shadow: var(--shadow-panel);
}
.app-sidebar.is-mobile { width: 100%; min-width: 0; height: auto; min-height: 100%; border-radius: 0; box-shadow: none; padding: 8px 0 0; }
.side-brand { display: flex; align-items: center; gap: 10px; padding: 2px 10px 20px; }
.brand-mark { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 10px; background: var(--color-primary); color: #fff; font-size: 17px; font-weight: 700; }
.brand-name { font-size: 16px; font-weight: 700; letter-spacing: -.01em; }
.new-chat-button { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; height: 44px; border: 0; border-radius: 14px; background: var(--color-primary); color: #fff; font-weight: 600; }
.new-chat-button:hover { background: var(--color-primary-hover); }
.side-search { display: flex; align-items: center; gap: 6px; margin: 12px 2px 8px; padding: 0 10px; border: 1px solid var(--color-border); border-radius: 11px; background: var(--color-panel-soft); }
.side-search:focus-within { border-color: var(--color-primary); background: var(--color-panel); }
.search-icon { color: var(--color-text-muted); font-size: 15px; }
.search-input { min-width: 0; flex: 1; height: 36px; border: 0; outline: 0; background: transparent; color: var(--color-text); font: inherit; font-size: 13px; }
.search-input::placeholder { color: var(--color-text-muted); }
.search-input::-webkit-search-cancel-button { display: none; }
.search-clear { display: grid; place-items: center; width: 22px; height: 22px; border: 0; border-radius: 7px; background: transparent; color: var(--color-text-muted); font-size: 17px; line-height: 1; }
.search-clear:hover { background: var(--color-primary-soft); color: var(--color-primary); }
.side-conversations { min-height: 0; flex: 1 1 auto; overflow-y: auto; padding: 2px; }
.side-hint { margin: 18px 8px; color: var(--color-text-muted); font-size: 12px; line-height: 1.6; text-align: center; }
.side-error { margin: 10px 4px; padding: 10px; border-radius: 10px; background: var(--color-panel-soft); color: var(--color-danger, #a33a3a); font-size: 12px; }
.side-error p { margin: 0; }
.side-retry { margin-top: 8px; padding: 0; border: 0; background: transparent; color: var(--color-primary); font-size: 12px; }
.conversation-list { display: flex; flex-direction: column; gap: 3px; margin: 0; padding: 0; list-style: none; }
.conversation-item { display: flex; align-items: center; gap: 2px; border-radius: 11px; }
.conversation-item:hover { background: var(--color-panel-soft); }
.conversation-select { display: grid; min-width: 0; flex: 1; gap: 3px; padding: 9px 10px; border: 0; border-radius: 11px; background: transparent; color: var(--color-text); text-align: left; }
.conversation-select[aria-current='page'] { background: var(--color-primary-soft); color: var(--color-primary); }
.conversation-title { overflow: hidden; font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.conversation-meta { display: flex; gap: 7px; overflow: hidden; color: var(--color-text-muted); font-size: 10px; white-space: nowrap; }
.conversation-meta time { overflow: hidden; text-overflow: ellipsis; }
.conversation-rename {
  display: grid;
  flex: none;
  place-items: center;
  width: 26px;
  height: 26px;
  margin-right: 4px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-muted);
  opacity: 0;
  visibility: hidden;
  transform: scale(.9);
  transition: opacity .16s ease, transform .22s var(--ease-spring), background-color .16s ease, color .16s ease;
}
.conversation-item:hover .conversation-rename,
.conversation-item:focus-within .conversation-rename,
.conversation-rename:focus-visible {
  opacity: 1;
  visibility: visible;
  transform: scale(1);
}
.conversation-rename:hover { background: var(--color-primary-soft); color: var(--color-primary); }
@media (hover: none) {
  .conversation-rename { opacity: 1; visibility: visible; transform: none; }
}
.rename-form { width: 100%; padding: 6px; border-radius: 11px; background: var(--color-panel-soft); }
.rename-input { width: 100%; height: 30px; padding: 0 8px; border: 1px solid var(--color-primary); border-radius: 8px; outline: 0; background: var(--color-panel); color: var(--color-text); font: inherit; font-size: 12px; }
.rename-actions { display: flex; gap: 4px; margin-top: 5px; }
.rename-save, .rename-cancel { height: 26px; padding: 0 9px; border: 0; border-radius: 7px; font-size: 11px; }
.rename-save { background: var(--color-primary); color: #fff; }
.rename-cancel { background: transparent; color: var(--color-text-muted); }
.rename-error { margin: 5px 2px 0; color: var(--color-danger, #a33a3a); font-size: 11px; }
.load-more { display: block; width: calc(100% - 12px); margin: 7px 6px; padding: 7px; border: 0; border-radius: 9px; background: transparent; color: var(--color-primary); font-size: 12px; }
.load-more:hover { background: var(--color-primary-soft); }
.side-bottom { flex: none; display: flex; flex-direction: column; gap: 4px; padding-top: 12px; border-top: 1px solid var(--color-border-soft); }
.side-meta-link { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 10px; color: var(--color-text); text-decoration: none; }
.side-meta-link:hover { background: var(--color-panel-soft); }
.meta-icon { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 9px; background: var(--color-primary-soft); color: var(--color-primary); font-size: 15px; }
.meta-copy { display: grid; min-width: 0; }
.meta-copy strong { font-size: 13px; font-weight: 500; }
.meta-copy small { overflow: hidden; color: var(--color-text-muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.data-note { margin: 8px 10px 0; color: var(--color-text-muted); font-size: 12px; }
.data-note summary { cursor: pointer; }
.data-note p { margin: 8px 0 0; line-height: 1.6; }
.side-signout { align-self: flex-start; margin: 6px 10px 0; padding: 2px 0; border: 0; background: transparent; color: var(--color-text-muted); font-size: 12px; }
.side-signout:hover { color: var(--color-text); }
@media (max-width: 767px) {
  .side-brand { padding-bottom: 14px; }
  .side-bottom { margin-top: 12px; }
}
</style>
