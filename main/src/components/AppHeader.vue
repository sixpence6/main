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
    <h1 class="app-title">电商生图工作台</h1>
    <div v-if="currentAccount" class="account-area">
      <RouterLink class="account-security" :to="{ name: 'account-security' }">账号安全</RouterLink>
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
  gap: var(--space-3);
  margin-left: auto;
  color: var(--text-muted);
  font-size: var(--text-caption);
}

.account-admin,
.account-security {
  color: var(--action-primary);
  text-decoration: none;
}

.account-admin:hover,
.account-security:hover {
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
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--text-muted);
}

.account-logout:hover {
  border-color: var(--action-primary);
  color: var(--action-primary);
}
</style>
