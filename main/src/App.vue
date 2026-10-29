<script setup lang="ts">
import AppHeader from './components/AppHeader.vue'
import AppSidebar from './components/AppSidebar.vue'
import ConversationPanel from './components/ConversationPanel.vue'
import ModelStatusDrawer from './components/ModelStatusDrawer.vue'
import ProjectDrawer from './components/ProjectDrawer.vue'
import ResultPlaceholder from './components/ResultPlaceholder.vue'
import SideDrawer from './components/SideDrawer.vue'
import StageNav from './components/StageNav.vue'
import StatusAnnouncer from './components/StatusAnnouncer.vue'
import { provideWorkspace } from './composables/useWorkspace'
import './styles/workspace.css'

const workspace = provideWorkspace()
const { isMobile, openSideDrawer } = workspace
</script>

<template>
  <div class="workspace-shell">
    <AppHeader />

    <div class="workspace-body">
      <AppSidebar v-if="!isMobile" />

      <main class="workspace-main">
        <div class="workspace-content">
          <div v-if="isMobile" class="mobile-topbar">
            <button type="button" class="secondary-button mobile-side-trigger" aria-label="打开侧栏" title="打开侧栏" @click="openSideDrawer">
              <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
              菜单
            </button>
          </div>

          <StageNav />
          <ConversationPanel />
          <ResultPlaceholder />
        </div>
      </main>
    </div>

    <SideDrawer />
    <ProjectDrawer />
    <ModelStatusDrawer />
    <StatusAnnouncer />
  </div>
</template>
