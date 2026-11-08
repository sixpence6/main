<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { currentAccount, sessionEstablished, sessionInvalidated, sessionRevalidating, sessionRevision } from '@/auth/session'
import { clearLiveStatus } from '@/composables/useLiveStatus'
import AppSidebar from '@/components/AppSidebar.vue'
import CurrentStatusCapsule from '@/components/CurrentStatusCapsule.vue'
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
  isCompact,
  sideDrawerOpen,
  openSideDrawer,
  navigationConfirmOpen,
  navigationIntentLabel,
  draftDirty,
  attachmentPending,
  confirmNavigation,
  cancelNavigation,
  currentProject,
} = workspace

// Status text can include a project title. Never let it survive an account or
// login-lifecycle change, even for the first frame of the next workspace.
watch(sessionRevision, () => clearLiveStatus(), { flush: 'sync' })
onBeforeUnmount(() => clearLiveStatus())
</script>

<template>
  <div class="pure-shell">
    <template v-if="businessVisible">
      <AppSidebar v-if="!isCompact" />

      <main class="pure-main">
        <div v-if="isCompact" class="mobile-bar">
          <button type="button" class="mobile-menu" aria-label="打开侧栏" :aria-expanded="sideDrawerOpen" aria-controls="workspace-side-drawer" @click="openSideDrawer">
            <svg aria-hidden="true" viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M4 7h16M4 12h16M4 17h16" />
            </svg>
            <span>对话</span>
          </button>
        </div>

        <div class="main-panel" :class="{ 'is-chat': chatView }">
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
        <CurrentStatusCapsule />
      </main>

      <SideDrawer />
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
    <div v-else class="session-lost" role="status">正在重新验证登录状态…</div>
  </div>
</template>

<style scoped>
.pure-shell {
  display: flex;
  gap: 12px;
  width: 100%;
  height: 100vh;
  height: 100dvh;
  max-width: 100vw;
  padding: 12px;
  padding-bottom: calc(12px + env(safe-area-inset-bottom, 0px));
  background: var(--color-page);
  overflow: hidden;
}
.pure-main { position: relative; display: flex; flex: 1 1 auto; flex-direction: column; min-width: 0; min-height: 0; }
.mobile-bar { display: flex; align-items: center; flex: none; height: 52px; padding: 0 4px; }
.mobile-menu { display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 12px; border: 1px solid var(--color-border); border-radius: 11px; background: var(--color-panel); color: var(--color-text); }
.main-panel { position: relative; display: flex; flex: 1 1 auto; flex-direction: column; min-width: 0; min-height: 0; border-radius: var(--radius-shell); }
.main-panel:not(.is-chat) { padding: 24px; background: var(--color-panel); overflow-y: auto; }
.legacy-isolation-banner { flex: none; margin: 0 0 12px; padding: 10px 14px; border: 1px solid #ecd8b8; border-radius: 12px; background: #fff9ed; color: #6d5125; }
.legacy-isolation-banner strong { font-size: 13px; }
.legacy-isolation-banner p { margin: 2px 0 0; font-size: 12px; }
.session-lost { display: grid; place-items: center; width: 100%; color: var(--color-text-muted); }
@media (max-width: 767px) {
  .pure-shell { padding: 6px; padding-bottom: calc(6px + env(safe-area-inset-bottom, 0px)); }
  .pure-main { gap: 0; }
}
</style>
