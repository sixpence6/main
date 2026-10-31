<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ApiError, api } from '@/api/client'
import { clearSession, currentAccount } from '@/auth/session'
import type { SessionView } from '@/api/types'
import AppHeader from '@/components/AppHeader.vue'

const router = useRouter()
const sessions = ref<SessionView[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)
const currentPassword = ref('')
const newPassword = ref('')
const confirmation = ref('')
const changing = ref(false)
const changeError = ref<string | null>(null)
const revokingId = ref<string | null>(null)
const revokingOthers = ref(false)
const sessionMessage = ref<string | null>(null)
const sessionError = ref<string | null>(null)
let listController: AbortController | null = null

const canChange = computed(
  () =>
    currentPassword.value.length > 0 &&
    newPassword.value.length >= 15 &&
    newPassword.value.length <= 128 &&
    confirmation.value.length > 0 &&
    !changing.value,
)
const hasOtherSessions = computed(() => sessions.value.some((session) => !session.current))

function friendlyError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 400 || error.status === 409 || error.status === 422) return error.message
    if (error.status === 429) return '操作过于频繁，请稍后再试。'
    if (error.status === 503) return '认证服务暂时不可用，请稍后再试。'
    if (error.status === 404) return '该会话已失效，列表将刷新。'
  }
  return fallback
}

function formatTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

async function loadSessions(): Promise<void> {
  listController?.abort()
  listController = new AbortController()
  loading.value = true
  loadError.value = null
  try {
    const response = await api.listSessions(listController.signal)
    sessions.value = response.items
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return
    loadError.value = friendlyError(error, '暂时无法读取登录会话。')
  } finally {
    loading.value = false
  }
}

function passwordValidation(): string | null {
  if (newPassword.value.length < 15) return '新密码至少需要 15 个 Unicode 码点。'
  if (newPassword.value.length > 128) return '新密码不能超过 128 个 Unicode 码点。'
  if (newPassword.value !== confirmation.value) return '两次输入的新密码不一致。'
  if (currentPassword.value === newPassword.value) return '新密码不能与当前密码相同。'
  return null
}

async function changePassword(): Promise<void> {
  if (!canChange.value) return
  const validation = passwordValidation()
  if (validation) {
    changeError.value = validation
    return
  }
  changing.value = true
  changeError.value = null
  try {
    await api.changePassword(currentPassword.value, newPassword.value)
    clearSession()
    await router.push({ name: 'login' })
  } catch (error) {
    changeError.value = friendlyError(error, '暂时无法修改密码，请稍后再试。')
  } finally {
    changing.value = false
  }
}

async function revokeOne(session: SessionView): Promise<void> {
  revokingId.value = session.id
  sessionError.value = null
  sessionMessage.value = null
  try {
    await api.revokeSession(session.id)
    if (session.current) {
      clearSession()
      await router.push({ name: 'login' })
      return
    }
    sessionMessage.value = '所选会话已撤销。'
    await loadSessions()
  } catch (error) {
    sessionError.value = friendlyError(error, '暂时无法撤销会话。')
  } finally {
    revokingId.value = null
  }
}

async function revokeOthers(): Promise<void> {
  if (!hasOtherSessions.value || revokingOthers.value) return
  revokingOthers.value = true
  sessionError.value = null
  sessionMessage.value = null
  try {
    await api.revokeOtherSessions()
    sessionMessage.value = '其他会话已全部撤销。'
    await loadSessions()
  } catch (error) {
    sessionError.value = friendlyError(error, '暂时无法撤销其他会话。')
  } finally {
    revokingOthers.value = false
  }
}

onMounted(loadSessions)
onBeforeUnmount(() => listController?.abort())
</script>

<template>
  <div class="security-shell-page">
    <AppHeader />
    <main class="security-page">
      <div class="security-shell">
        <div class="security-topbar">
          <div>
            <p class="security-brand">电商生图智能体</p>
            <h1>账号安全</h1>
          </div>
          <RouterLink class="security-back" :to="{ name: 'describe' }">返回工作台</RouterLink>
        </div>

        <section class="security-panel" aria-labelledby="account-title">
          <h2 id="account-title">当前账号</h2>
          <p class="security-note">{{ currentAccount?.email }}</p>
        </section>

        <section class="security-panel" aria-labelledby="password-title">
          <h2 id="password-title">修改登录密码</h2>
          <p class="security-note">修改成功后，所有设备上的登录会话都会失效，需要使用新密码重新登录。</p>
          <form class="security-form" novalidate @submit.prevent="changePassword">
            <label class="security-field">
              <span>当前密码</span>
              <input
                v-model="currentPassword"
                type="password"
                name="current-password"
                autocomplete="current-password"
                required
                :disabled="changing"
              />
            </label>
            <label class="security-field">
              <span>新密码</span>
              <input
                v-model="newPassword"
                type="password"
                name="new-password"
                autocomplete="new-password"
                minlength="15"
                maxlength="128"
                required
                :disabled="changing"
              />
            </label>
            <label class="security-field">
              <span>再次输入新密码</span>
              <input
                v-model="confirmation"
                type="password"
                name="confirm-password"
                autocomplete="new-password"
                minlength="15"
                maxlength="128"
                required
                :disabled="changing"
              />
            </label>
            <p v-if="changeError" class="security-error" role="alert">{{ changeError }}</p>
            <button class="security-primary" type="submit" :disabled="!canChange">
              {{ changing ? '正在修改…' : '修改密码' }}
            </button>
          </form>
        </section>

        <section class="security-panel" aria-labelledby="sessions-title">
          <div class="security-section-head">
            <div>
              <h2 id="sessions-title">登录设备与会话</h2>
              <p class="security-note">这里只显示客户端信息和活动时间，不显示任何会话凭证。</p>
            </div>
            <button
              class="security-secondary"
              type="button"
              :disabled="!hasOtherSessions || revokingOthers"
              @click="revokeOthers"
            >
              {{ revokingOthers ? '正在撤销…' : '撤销其他会话' }}
            </button>
          </div>
          <p v-if="sessionMessage" class="security-success" role="status">{{ sessionMessage }}</p>
          <p v-if="sessionError" class="security-error" role="alert">{{ sessionError }}</p>
          <p v-if="loading" class="security-note">正在读取登录会话…</p>
          <p v-else-if="loadError" class="security-error" role="alert">{{ loadError }}</p>
          <ul v-else class="session-list">
            <li v-for="session in sessions" :key="session.id" class="session-item">
              <div class="session-main">
                <p class="session-title">
                  <strong>{{ session.client_info ?? '未知客户端' }}</strong>
                  <span v-if="session.current" class="session-current">当前会话</span>
                </p>
                <p class="session-meta">最近活动：{{ formatTime(session.last_activity_at) }}</p>
                <p class="session-meta">会话到期：{{ formatTime(session.absolute_expires_at) }}</p>
              </div>
              <button
                class="security-secondary session-revoke"
                type="button"
                :aria-label="session.current ? '撤销当前会话' : '撤销会话 ' + (session.client_info ?? session.id)"
                :disabled="revokingId === session.id"
                @click="revokeOne(session)"
              >
                {{ revokingId === session.id ? '正在撤销…' : '撤销' }}
              </button>
            </li>
          </ul>
        </section>
      </div>
    </main>
  </div>
</template>

<style scoped>
.security-shell-page {
  min-height: 100%;
  background: var(--color-page);
}
.security-page {
  padding: 32px 20px 56px;
}
.security-shell {
  width: min(760px, 100%);
  margin: 0 auto;
  display: grid;
  gap: 20px;
}
.security-topbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}
.security-brand {
  margin: 0 0 8px;
  color: var(--color-accent);
  font-size: 13px;
  letter-spacing: 0.12em;
}
h1 {
  margin: 0;
  font-size: 28px;
  line-height: 1.2;
}
h2 {
  margin: 0 0 10px;
  font-size: 18px;
}
.security-back {
  color: var(--color-primary);
}
.security-panel {
  padding: 22px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-panel);
  background: var(--color-panel);
  box-shadow: var(--shadow-panel);
}
.security-note {
  margin: 8px 0;
  color: var(--color-text-muted);
  line-height: 1.7;
  font-size: 13px;
}
.security-form {
  display: grid;
  gap: 16px;
  margin-top: 16px;
}
.security-field {
  display: grid;
  gap: 8px;
  color: var(--color-text-muted);
  font-size: 13px;
}
.security-field input {
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  background: #fff;
  color: var(--color-text);
  font-size: var(--text-input);
}
.security-field input:focus {
  border-color: var(--color-primary);
}
.security-section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.security-primary,
.security-secondary {
  min-height: 42px;
  padding: 8px 16px;
  border-radius: var(--radius-control);
  font-size: 14px;
}
.security-primary {
  justify-self: start;
  border: 0;
  background: var(--color-primary);
  color: #fff;
}
.security-primary:hover:not(:disabled) {
  background: var(--color-primary-hover);
}
.security-secondary {
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-primary);
}
.security-primary:disabled,
.security-secondary:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.security-error {
  margin: 8px 0 0;
  color: var(--color-danger);
  line-height: 1.6;
}
.security-success {
  margin: 8px 0 0;
  color: var(--color-primary);
}
.session-list {
  margin: 14px 0 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
}
.session-main {
  min-width: 0;
}
.session-title {
  margin: 0 0 6px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.session-title strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-current {
  flex: none;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--color-page);
  color: var(--color-text-muted);
  font-size: 11px;
}
.session-meta {
  margin: 3px 0 0;
  color: var(--color-text-muted);
  font-size: 12px;
}
.session-revoke {
  flex: none;
}
@media (max-width: 640px) {
  .security-page {
    padding: 20px 12px 40px;
  }
  .security-topbar,
  .security-section-head,
  .session-item {
    align-items: stretch;
    flex-direction: column;
  }
  .security-panel {
    padding: 16px;
  }
}
</style>
