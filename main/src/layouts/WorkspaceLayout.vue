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
import { displayProjectTitle, legacyNoticeText } from '@/utils/format'
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
  isMobile,
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

const ROUTE_TITLES: Record<string, string> = {
  'project-results': '生成结果',
  'account-security': '账号与用量',
  'admin-accounts': '账号管理',
}

const pageTitle = computed(() => {
  const routeName = typeof route.name === 'string' ? route.name : ''
  const fixedTitle = ROUTE_TITLES[routeName]
  if (fixedTitle) return fixedTitle
  if (currentProject.value) return displayProjectTitle(currentProject.value.title)
  return routeName === 'project-chat' ? '对话' : '新对话'
})

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
        <header class="workspace-topbar">
          <div class="topbar-left">
            <button
              v-if="isCompact"
              type="button"
              class="topbar-menu"
              aria-label="打开侧栏"
              :aria-expanded="sideDrawerOpen"
              aria-controls="workspace-side-drawer"
              @click="openSideDrawer"
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            </button>
            <span v-if="isCompact" class="topbar-brand">电商生图工作台</span>
            <span v-if="isCompact" class="topbar-divider" aria-hidden="true"></span>
            <component :is="chatView ? 'h1' : 'span'" class="topbar-title">{{ pageTitle }}</component>
          </div>
          <CurrentStatusCapsule v-if="!isMobile" variant="topbar" />
        </header>

        <div v-if="isMobile" class="capsule-inline">
          <CurrentStatusCapsule variant="inline" />
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
  width: 100%;
  height: 100vh;
  height: 100dvh;
  max-width: 100vw;
  overflow: hidden;
  background: var(--surface-canvas);
}
.pure-main {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--surface-canvas);
}
.workspace-topbar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  height: var(--topbar-height);
  padding: 0 var(--space-4) 0 var(--shell-pad-x);
  border-bottom: 1px solid var(--line-inverse);
  background: var(--surface-shell);
  color: var(--text-inverse-secondary);
}
.topbar-left { display: flex; align-items: center; gap: var(--space-3); min-width: 0; }
.topbar-menu {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  margin-left: calc(var(--space-2) * -1);
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-inverse-primary);
}
.topbar-menu:hover { background: var(--surface-shell-hover); }
.topbar-brand {
  color: var(--text-inverse-primary);
  font-size: var(--text-body);
  font-weight: 600;
  white-space: nowrap;
}
.topbar-divider { width: 1px; height: 16px; background: var(--line-inverse-control); }
.topbar-title {
  margin: 0;
  overflow: hidden;
  color: var(--text-inverse-muted);
  font-size: var(--text-body);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.capsule-inline { display: flex; flex: none; padding: var(--space-2) var(--shell-pad-x) 0; }
.main-panel {
  position: relative;
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.main-panel:not(.is-chat) {
  padding: var(--space-6) var(--shell-pad-x);
  overflow-y: auto;
}
.legacy-isolation-banner {
  flex: none;
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid color-mix(in srgb, var(--state-warning) 35%, var(--line-hairline));
  background: var(--surface-panel);
  color: var(--text-secondary);
}
.legacy-isolation-banner strong { font-size: var(--text-body); font-weight: 600; }
.legacy-isolation-banner p { margin: var(--space-1) 0 0; font-size: var(--text-caption); line-height: var(--text-caption-line); }
.session-lost { display: grid; place-items: center; width: 100%; color: var(--text-muted); }
</style>
