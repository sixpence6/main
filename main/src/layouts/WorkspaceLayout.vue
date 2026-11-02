<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { currentAccount, sessionEstablished, sessionInvalidated, sessionRevalidating } from '@/auth/session'
import AppHeader from '@/components/AppHeader.vue'
import AppSidebar from '@/components/AppSidebar.vue'
import ModelStatusDrawer from '@/components/ModelStatusDrawer.vue'
import ProjectDrawer from '@/components/ProjectDrawer.vue'
import SideDrawer from '@/components/SideDrawer.vue'
import StatusAnnouncer from '@/components/StatusAnnouncer.vue'
import UnsavedChangesDialog from '@/components/UnsavedChangesDialog.vue'
import { provideWorkspace } from '@/composables/useWorkspace'
import { legacyNoticeText } from '@/utils/format'
import '@/styles/workspace.css'

const route = useRoute()
const chatView = computed(() => route.name === 'describe' || route.name === 'project-chat')
const businessVisible = computed(
  () =>
    !sessionRevalidating.value &&
    (currentAccount.value !== null ||
      (!sessionEstablished.value && !sessionInvalidated.value)),
)
const workspace = provideWorkspace()
const {
  isMobile,
  isCompact,
  openSideDrawer,
  navigationConfirmOpen,
  navigationIntentLabel,
  draftDirty,
  attachmentPending,
  confirmNavigation,
  cancelNavigation,
  currentProject,
} = workspace
</script>

<template>
  <div class="workspace-shell">
    <AppHeader />

    <template v-if="businessVisible">
    <div class="workspace-body">
      <AppSidebar v-if="!isCompact" />

      <main class="workspace-main" :class="{ 'is-chat': chatView }">
        <div class="workspace-content">
          <div v-if="isCompact" class="mobile-topbar">
            <button type="button" class="secondary-button mobile-side-trigger" aria-label="打开侧栏" title="打开侧栏" @click="openSideDrawer">
              <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
              菜单
            </button>
          </div>

          

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
    <UnsavedChangesDialog
      :open="navigationConfirmOpen"
      :destination="navigationIntentLabel"
      :draft-dirty="draftDirty"
      :attachment-pending="attachmentPending"
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
