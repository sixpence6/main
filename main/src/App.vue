<script setup lang="ts">
import { computed } from 'vue'
import AppHeader from './components/AppHeader.vue'
import AppSidebar from './components/AppSidebar.vue'
import ConversationPanel from './components/ConversationPanel.vue'
import ModelStatusDrawer from './components/ModelStatusDrawer.vue'
import ProjectDrawer from './components/ProjectDrawer.vue'
import ResultPanel from './components/ResultPanel.vue'
import ResultPlaceholder from './components/ResultPlaceholder.vue'
import SideDrawer from './components/SideDrawer.vue'
import StageNav from './components/StageNav.vue'
import StatusAnnouncer from './components/StatusAnnouncer.vue'
import UnsavedChangesDialog from './components/UnsavedChangesDialog.vue'
import { provideWorkspace } from './composables/useWorkspace'
import './styles/workspace.css'

const workspace = provideWorkspace()
const {
  isMobile,
  openSideDrawer,
  navigationConfirmOpen,
  navigationIntentLabel,
  draftDirty,
  briefDirty,
  confirmNavigation,
  cancelNavigation,
  generationQueryError,
  statusQueryInterruptionTitle,
  generationQuerying,
  retryGenerationQuery,
} = workspace
const showResults = computed(
  () => workspace.stage.value === 'results' && Boolean(workspace.currentProject.value),
)
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

          <section v-if="generationQueryError" class="status-query-alert" role="alert" aria-labelledby="status-query-title">
            <strong id="status-query-title">{{ statusQueryInterruptionTitle }}</strong>
            <p>{{ generationQueryError }}</p>
            <button
              type="button"
              class="secondary-button"
              :disabled="generationQuerying"
              @click="retryGenerationQuery"
            >
              {{ generationQuerying ? '正在查询…' : '重新查询状态' }}
            </button>
          </section>

          <StageNav />
          <ResultPanel v-if="showResults" />
          <ConversationPanel v-else />
          <ResultPlaceholder v-if="!workspace.currentProject.value" />
        </div>
      </main>
    </div>

    <SideDrawer />
    <ProjectDrawer />
    <ModelStatusDrawer />
    <UnsavedChangesDialog
      :open="navigationConfirmOpen"
      :destination="navigationIntentLabel"
      :draft-dirty="draftDirty"
      :brief-dirty="briefDirty"
      @cancel="cancelNavigation"
      @confirm="confirmNavigation"
    />
    <StatusAnnouncer />
  </div>
</template>
