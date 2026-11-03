<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import { currentAccount, sessionEstablished, sessionInvalidated, sessionRevalidating } from '@/auth/session'
import AppSidebar from '@/components/AppSidebar.vue'
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
  <div class="pure-shell">
    <template v-if="businessVisible">
      <AppSidebar v-if="!isCompact" />

      <main class="pure-main">
        <div v-if="isCompact" class="mobile-bar">
          <button type="button" class="mobile-menu" aria-label="打开侧栏" @click="openSideDrawer">
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
  height: 100%;
  padding: 12px;
  background: var(--color-page);
  overflow: hidden;
}
.pure-main { display: flex; flex: 1 1 auto; flex-direction: column; min-width: 0; min-height: 0; }
.mobile-bar { display: flex; align-items: center; flex: none; height: 52px; padding: 0 4px; }
.mobile-menu { display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 12px; border: 1px solid var(--color-border); border-radius: 11px; background: var(--color-panel); color: var(--color-text); }
.main-panel { position: relative; display: flex; flex: 1 1 auto; flex-direction: column; min-height: 0; border-radius: var(--radius-shell); }
.main-panel:not(.is-chat) { padding: 24px; background: var(--color-panel); overflow-y: auto; }
.legacy-isolation-banner { flex: none; margin: 0 0 12px; padding: 10px 14px; border: 1px solid #ecd8b8; border-radius: 12px; background: #fff9ed; color: #6d5125; }
.legacy-isolation-banner strong { font-size: 13px; }
.legacy-isolation-banner p { margin: 2px 0 0; font-size: 12px; }
.session-lost { display: grid; place-items: center; width: 100%; color: var(--color-text-muted); }
@media (max-width: 767px) {
  .pure-shell { padding: 6px; }
  .pure-main { gap: 0; }
}
</style>
