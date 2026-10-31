<script setup lang="ts">
import { RouterLink, useRouter } from 'vue-router'
import { currentAccount, logout } from '@/auth/session'

const router = useRouter()

async function signOut(): Promise<void> {
  try {
    await logout()
  } finally {
    await router.push({ name: 'login' })
  }
}
</script>

<template>
  <header class="app-header">
    <h1 class="app-title">电商生图智能体</h1>
    <div v-if="currentAccount" class="account-area">
      <RouterLink
        v-if="currentAccount.is_admin"
        class="account-admin"
        :to="{ name: 'admin-accounts' }"
      >
        账号管理
      </RouterLink>
      <span class="account-email">{{ currentAccount.email }}</span>
      <button type="button" class="account-logout" @click="signOut">退出登录</button>
    </div>
  </header>
</template>

<style scoped>
.account-area {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
  color: var(--color-text-muted);
  font-size: 13px;
}

.account-admin {
  color: var(--color-primary);
  text-decoration: none;
}

.account-admin:hover {
  text-decoration: underline;
}

.account-email {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-logout {
  padding: 5px 10px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: transparent;
  color: var(--color-text-muted);
}

.account-logout:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}
</style>
