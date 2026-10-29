<script setup lang="ts">
import { computed } from 'vue'
import { useWorkspace } from '@/composables/useWorkspace'

const props = withDefaults(defineProps<{ mobile?: boolean }>(), { mobile: false })
const emit = defineEmits<{ navigate: [] }>()
const workspace = useWorkspace()
const { sidebarExpanded, configStatus, createNewProject, openProjectDrawer, openModelDrawer } = workspace

const expanded = computed(() => (props.mobile ? true : sidebarExpanded.value))
const toggleLabel = computed(() => (expanded.value ? '收起侧栏' : '展开侧栏'))
const toggleHint = computed(() =>
  expanded.value ? '收起侧栏后，工作区将获得更多空间。' : '展开侧栏以查看新建项目和已有项目入口。',
)
const configLabel = computed(() => {
  if (!configStatus.value) return '模型状态'
  return configStatus.value.llm.configured && configStatus.value.visual.configured
    ? '模型状态：配置完整'
    : '模型状态：配置不完整'
})

function toggle(): void {
  workspace.toggleSidebar()
  if (props.mobile) emit('navigate')
}
function newProject(): void {
  createNewProject()
  emit('navigate')
}
function projects(): void {
  openProjectDrawer()
}
function modelStatus(): void {
  openModelDrawer()
}
</script>

<template>
  <aside class="app-sidebar" :class="{ 'is-expanded': expanded, 'is-collapsed': !expanded, 'is-mobile': mobile }" aria-label="工作区侧栏">
    <div class="sidebar-top">
      <button
        type="button"
        class="icon-button sidebar-toggle"
        :aria-label="toggleLabel"
        :title="toggleLabel"
        aria-describedby="sidebar-toggle-hint"
        :aria-expanded="expanded"
        @click="toggle"
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8">
          <rect x="3.5" y="4" width="17" height="16" rx="2" />
          <path d="M9 4v16" />
          <path v-if="!expanded" d="M13 9l3 3-3 3" />
          <path v-else d="M16 9l-3 3 3 3" />
        </svg>
        <span id="sidebar-toggle-hint" class="side-tip">{{ toggleHint }}</span>
      </button>
    </div>

    <nav class="sidebar-actions" aria-label="工作区操作">
      <button type="button" class="side-action" aria-label="新建项目" title="新建项目" @click="newProject">
        <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M12 5v14M5 12h14" />
        </svg>
        <span class="side-label">新建项目</span>
        <span class="side-tip" aria-hidden="true">新建项目</span>
      </button>
      <button type="button" class="side-action" aria-label="已有项目" title="已有项目" @click="projects">
        <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M3.5 7.5h6l1.7 2h9.3v9.2a1.8 1.8 0 0 1-1.8 1.8H5.3a1.8 1.8 0 0 1-1.8-1.8z" />
          <path d="M3.5 9.5V6.3c0-1 .8-1.8 1.8-1.8h4l1.7 2h8.7" />
        </svg>
        <span class="side-label">已有项目</span>
        <span class="side-tip" aria-hidden="true">已有项目</span>
      </button>
    </nav>

    <div class="sidebar-bottom">
      <button type="button" class="side-action model-status-entry" aria-label="模型状态" :title="configLabel" @click="modelStatus">
        <span class="model-dot" :class="{ 'is-ready': configStatus?.llm.configured && configStatus?.visual.configured }" aria-hidden="true" />
        <span class="side-label">模型状态</span>
        <span class="side-tip" aria-hidden="true">{{ configLabel }}</span>
      </button>
    </div>
  </aside>
</template>
