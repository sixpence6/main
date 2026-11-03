<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { currentAccount, logout } from '@/auth/session'
import { useWorkspace } from '@/composables/useWorkspace'

const props = withDefaults(defineProps<{ mobile?: boolean }>(), { mobile: false })
const emit = defineEmits<{ navigate: [] }>()
const workspace = useWorkspace()
const router = useRouter()
const { createNewProject } = workspace

const accountLabel = computed(() => currentAccount.value?.email ?? '登录后查看用量')

function newConversation(): void {
  createNewProject()
  emit('navigate')
}

async function signOut(): Promise<void> {
  try {
    await logout()
  } finally {
    emit('navigate')
    await router.push({ name: 'login' })
  }
}
</script>

<template>
  <aside class="app-sidebar" :class="{ 'is-mobile': props.mobile }" aria-label="对话侧栏">
    <div class="side-brand">
      <span class="brand-mark" aria-hidden="true">↗</span>
      <span class="brand-name">电商生图工作台</span>
    </div>

    <button type="button" class="new-chat-button" @click="newConversation">
      <span aria-hidden="true">＋</span>
      新对话
    </button>

    <div class="side-spacer" aria-hidden="true" />

    <div class="side-bottom">
      <RouterLink class="side-meta-link" :to="{ name: 'account-security' }">
        <span class="meta-icon" aria-hidden="true">◯</span>
        <span class="meta-copy">
          <strong>{{ currentAccount ? '账号' : '账号与用量' }}</strong>
          <small>{{ accountLabel }}</small>
        </span>
      </RouterLink>
      <RouterLink class="side-meta-link" :to="{ name: 'account-security' }">
        <span class="meta-icon" aria-hidden="true">◔</span>
        <span class="meta-copy"><strong>用量</strong><small>查看生成额度与记录</small></span>
      </RouterLink>
      <details class="data-note">
        <summary>数据使用说明</summary>
        <p>描述和图片会发送给已配置的外部模型服务用于整理与生成。请勿提交密码、凭证或恢复链接。</p>
      </details>
      <button v-if="currentAccount" type="button" class="side-signout" @click="signOut">退出登录</button>
    </div>
  </aside>
</template>

<style scoped>
.app-sidebar {
  display: flex;
  flex-direction: column;
  width: var(--sidebar-width);
  min-width: var(--sidebar-width);
  height: 100%;
  padding: 24px 16px 18px;
  overflow: hidden;
  border-radius: var(--radius-shell);
  background: var(--color-panel);
  box-shadow: var(--shadow-panel);
}
.app-sidebar.is-mobile { width: 100%; min-width: 0; height: auto; min-height: 100%; border-radius: 0; box-shadow: none; padding: 8px 0 0; }
.side-brand { display: flex; align-items: center; gap: 10px; padding: 2px 10px 24px; }
.brand-mark { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 10px; background: var(--color-primary); color: #fff; font-size: 17px; font-weight: 700; }
.brand-name { font-size: 16px; font-weight: 700; letter-spacing: -.01em; }
.new-chat-button { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; height: 44px; border: 0; border-radius: 14px; background: var(--color-primary); color: #fff; font-weight: 600; }
.new-chat-button:hover { background: var(--color-primary-hover); }
.side-spacer { flex: 1 1 auto; min-height: 24px; }
.side-bottom { flex: none; display: flex; flex-direction: column; gap: 4px; padding-top: 12px; border-top: 1px solid var(--color-border-soft); }
.side-meta-link { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 10px; color: var(--color-text); text-decoration: none; }
.side-meta-link:hover { background: var(--color-panel-soft); }
.meta-icon { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 9px; background: var(--color-primary-soft); color: var(--color-primary); font-size: 15px; }
.meta-copy { display: grid; min-width: 0; }
.meta-copy strong { font-size: 13px; font-weight: 500; }
.meta-copy small { overflow: hidden; color: var(--color-text-muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.data-note { margin: 8px 10px 0; color: var(--color-text-muted); font-size: 12px; }
.data-note summary { cursor: pointer; }
.data-note p { margin: 8px 0 0; line-height: 1.6; }
.side-signout { align-self: flex-start; margin: 6px 10px 0; padding: 2px 0; border: 0; background: transparent; color: var(--color-text-muted); font-size: 12px; }
.side-signout:hover { color: var(--color-text); }
@media (max-width: 767px) {
  .side-brand { padding-bottom: 14px; }
  .side-bottom { margin-top: 12px; }
}
</style>
