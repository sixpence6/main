<script setup lang="ts">
import AccessibleDrawer from './AccessibleDrawer.vue'
import { useWorkspace } from '@/composables/useWorkspace'

const { projectDrawerOpen, closeProjectDrawer } = useWorkspace()
</script>

<template>
  <AccessibleDrawer
    :open="projectDrawerOpen"
    title="已有项目"
    close-label="关闭项目列表"
    description="项目列表将在后续接入"
    width="360px"
    @close="closeProjectDrawer"
  >
    <div class="project-list-placeholder">
      <span class="placeholder-icon" aria-hidden="true">□</span>
      <p>项目列表将在后续接入</p>
      <p class="muted">当前版本不会显示虚构项目，也不会自动读取历史项目。</p>
    </div>
  </AccessibleDrawer>
</template>
