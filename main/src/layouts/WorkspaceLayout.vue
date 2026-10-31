<script setup lang="ts">
import { computed } from 'vue'
import { RouterView } from 'vue-router'
import { currentAccount, sessionEstablished, sessionInvalidated, sessionRevalidating } from '@/auth/session'
import AppHeader from '@/components/AppHeader.vue'
import AppSidebar from '@/components/AppSidebar.vue'
import GenerationRiskDialog from '@/components/GenerationRiskDialog.vue'
import ModelStatusDrawer from '@/components/ModelStatusDrawer.vue'
import ProjectDrawer from '@/components/ProjectDrawer.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import StageNav from '@/components/StageNav.vue'
import StatusAnnouncer from '@/components/StatusAnnouncer.vue'
import UnsavedChangesDialog from '@/components/UnsavedChangesDialog.vue'
import { provideWorkspace } from '@/composables/useWorkspace'
import { legacyNoticeText } from '@/utils/format'
import '@/styles/workspace.css'

const businessVisible = computed(
  () =>
    !sessionRevalidating.value &&
    (currentAccount.value !== null ||
      (!sessionEstablished.value && !sessionInvalidated.value)),
)
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
  generationRiskDialogOpen,
  latestGenerationView,
  confirmGenerationRisk,
  cancelGenerationRisk,
  currentProject,
} = workspace
</script>

<template>
  <div class="workspace-shell">
    <AppHeader />

    <template v-if="businessVisible">
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
          <section
            v-if="currentProject?.legacy_unreviewed"
            class="legacy-isolation-banner"
            role="status"
            aria-label="历史内容隔离提示"
          >
            <strong>历史内容尚未审查</strong>
            <p>{{ legacyNoticeText(currentProject.legacy_notice) }}</p>
          </section>
          <RouterView />
        </div>
      </main>
    </div>

    <SideDrawer />
    <ProjectDrawer />
    <ModelStatusDrawer />
    <GenerationRiskDialog
      :open="generationRiskDialogOpen"
      :version-no="latestGenerationView?.version_no ?? null"
      @cancel="cancelGenerationRisk"
      @confirm="confirmGenerationRisk"
    />
    <UnsavedChangesDialog
      :open="navigationConfirmOpen"
      :destination="navigationIntentLabel"
      :draft-dirty="draftDirty"
      :brief-dirty="briefDirty"
      @cancel="cancelNavigation"
      @confirm="confirmNavigation"
    />
    <StatusAnnouncer />
    </template>
    <div v-else class="workspace-session-lost" role="status">
      正在重新验证登录状态…
    </div>
  </div>
</template>
