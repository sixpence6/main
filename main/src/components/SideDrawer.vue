<script setup lang="ts">
import AccessibleDrawer from './AccessibleDrawer.vue'
import AppSidebar from './AppSidebar.vue'
import { useWorkspace } from '@/composables/useWorkspace'

const { sideDrawerOpen, closeSideDrawer } = useWorkspace()
</script>

<template>
  <AccessibleDrawer
    :open="sideDrawerOpen"
    title="工作区菜单"
    close-label="关闭侧栏"
    panel-id="workspace-side-drawer"
    width="min(272px, calc(100vw - 32px))"
    @close="closeSideDrawer"
  >
    <AppSidebar mobile @navigate="closeSideDrawer" />
  </AccessibleDrawer>
</template>
