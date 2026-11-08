<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { ApiError, api } from '@/api/client'
import { clearSession, currentAccount } from '@/auth/session'
import type { SecurityEventView, SessionView } from '@/api/types'
import AppHeader from '@/components/AppHeader.vue'
import BrandMark from '@/components/BrandMark.vue'
import PasswordField from '@/components/PasswordField.vue'

const router = useRouter()
const sessions = ref<SessionView[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)
const currentPassword = ref('')
const newPassword = ref('')
const confirmation = ref('')
const changing = ref(false)
const changeError = ref<string | null>(null)
const showValidation = ref(false)
const revokingId = ref<string | null>(null)
const revokingOthers = ref(false)
const sessionMessage = ref<string | null>(null)
const sessionError = ref<string | null>(null)
const events = ref<SecurityEventView[]>([])
const eventsLoading = ref(true)
const eventsError = ref<string | null>(null)
let listController: AbortController | null = null
let eventsController: AbortController | null = null

const canChange = computed(() => !changing.value)
const hasOtherSessions = computed(() => sessions.value.some((session) => !session.current))
const currentPasswordError = computed(() =>
  showValidation.value && currentPassword.value.length === 0 ? '请输入当前密码。' : null,
)
const newPasswordError = computed(() => {
  if (!showValidation.value) return null
  if (newPassword.value.length < 15) return '新密码至少需要 15 个 Unicode 码点。'
  if (newPassword.value.length > 128) return '新密码不能超过 128 个 Unicode 码点。'
  if (currentPassword.value.length > 0 && currentPassword.value === newPassword.value) {
    return '新密码不能与当前密码相同。'
  }
  return null
})
const confirmationError = computed(() =>
  showValidation.value && newPassword.value !== confirmation.value ? '两次输入的新密码不一致。' : null,
)
const hasFieldError = computed(
  () => Boolean(currentPasswordError.value || newPasswordError.value || confirmationError.value),
)

const eventLabels: Record<string, string> = {
  bootstrap_admin_created: '初始化管理员',
  login_success: '登录成功',
  login_failure: '登录失败',
  login_limited: '登录受限',
  logout: '退出登录',
  password_changed: '修改密码',
  account_created: '创建账号',
  account_link_issued: '生成一次性链接',
  account_link_used: '使用一次性链接',
  session_revoked: '撤销会话',
  sessions_revoked: '撤销多个会话',
  admin_recovered: '本机恢复首管',
  account_disabled: '账号停用',
  account_restored: '账号恢复',
  permission_denied: '权限拒绝',
}

const eventResultLabels: Record<SecurityEventView['result'], string> = {
  success: '成功',
  failure: '失败',
  denied: '拒绝',
  limited: '受限',
}

function eventLabel(type: string): string {
  return eventLabels[type] ?? type
}

function actorLabel(kind: string): string {
  if (kind === 'anonymous') return '匿名'
  if (kind === 'system') return '系统'
  if (kind === 'local_maintainer') return '本机维护'
  if (kind === 'admin') return '管理员'
  return '本人'
}

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

function clearChangeError(): void {
  changeError.value = null
}

async function changePassword(): Promise<void> {
  if (!canChange.value) return
  showValidation.value = true
  changeError.value = null
  if (hasFieldError.value) return
  changing.value = true
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

async function loadSecurityEvents(): Promise<void> {
  eventsController?.abort()
  eventsController = new AbortController()
  eventsLoading.value = true
  eventsError.value = null
  try {
    const response = await api.listSecurityEvents(eventsController.signal)
    events.value = response.items
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return
    eventsError.value = friendlyError(error, '暂时无法读取安全事件。')
  } finally {
    eventsLoading.value = false
  }
}

onMounted(() => {
  void loadSessions()
  void loadSecurityEvents()
})
onBeforeUnmount(() => {
  listController?.abort()
  eventsController?.abort()
})
</script>

<template>
  <div class="security-shell-page">
    <AppHeader />
    <main class="security-page">
      <div class="security-shell">
        <header class="security-topbar">
          <div class="security-heading">
            <p class="security-brand"><BrandMark :size="18" /> 电商生图工作台 · ACCOUNT</p>
            <h1>账号安全</h1>
            <p class="security-lede">管理本人密码、登录会话与安全事件。页面只展示必要的账号信息，不显示任何凭证。</p>
          </div>
          <RouterLink class="security-back" :to="{ name: 'describe' }">返回工作台</RouterLink>
        </header>

        <section class="security-panel security-account-panel" aria-labelledby="account-title">
          <div class="security-panel-icon" aria-hidden="true">A</div>
          <div>
            <h2 id="account-title">当前账号</h2>
            <p class="security-account-email">{{ currentAccount?.email }}</p>
            <p class="security-note">此页面上的操作只影响当前登录账号，不会读取他人的项目或生成内容。</p>
          </div>
        </section>

        <section class="security-panel" aria-labelledby="password-title">
          <div class="security-section-head">
            <div>
              <p class="security-kicker">CREDENTIALS</p>
              <h2 id="password-title">修改登录密码</h2>
            </div>
            <span class="security-chip">建议定期更新</span>
          </div>
          <p class="security-note">修改成功后，所有设备上的登录会话都会失效，需要使用新密码重新登录。</p>
          <form class="security-form" novalidate @submit.prevent="changePassword">
            <PasswordField
              v-model="currentPassword"
              label="当前密码"
              name="current-password"
              autocomplete="current-password"
              :required="true"
              :disabled="changing"
              :error="currentPasswordError"
              @update:model-value="clearChangeError"
            />
            <PasswordField
              v-model="newPassword"
              label="新密码"
              name="new-password"
              autocomplete="new-password"
              :minlength="15"
              :maxlength="128"
              :required="true"
              :disabled="changing"
              :error="newPasswordError"
              hint="至少 15 个 Unicode 码点；不会自动截断，也不会写入浏览器存储。"
              @update:model-value="clearChangeError"
            />
            <PasswordField
              v-model="confirmation"
              label="再次输入新密码"
              name="confirm-password"
              autocomplete="new-password"
              :minlength="15"
              :maxlength="128"
              :required="true"
              :disabled="changing"
              :error="confirmationError"
              @update:model-value="clearChangeError"
            />
            <p v-if="changeError" class="security-error" role="alert">{{ changeError }}</p>
            <div class="security-form-foot">
              <button class="security-primary" type="submit" :disabled="changing">
                {{ changing ? '正在修改…' : '修改密码' }}
              </button>
              <p class="security-note security-note-inline">修改后会退出所有设备，包括当前设备。</p>
            </div>
          </form>
        </section>

        <section class="security-panel" aria-labelledby="sessions-title">
          <div class="security-section-head">
            <div>
              <p class="security-kicker">SESSIONS</p>
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
          <p v-else-if="sessions.length === 0" class="security-note">没有可显示的登录会话。</p>
          <ul v-else class="session-list" aria-label="登录会话列表">
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

        <section class="security-panel" aria-labelledby="security-events-title">
          <div class="security-section-head">
            <div>
              <p class="security-kicker">AUDIT</p>
              <h2 id="security-events-title">账号安全事件</h2>
              <p class="security-note">只显示与本账号直接相关的必要事件，不包含密码、令牌或创作内容。</p>
            </div>
            <button class="security-secondary" type="button" :disabled="eventsLoading" @click="loadSecurityEvents">
              {{ eventsLoading ? '正在读取…' : '刷新' }}
            </button>
          </div>
          <p v-if="eventsError" class="security-error" role="alert">{{ eventsError }}</p>
          <p v-else-if="eventsLoading" class="security-note">正在读取安全事件…</p>
          <p v-else-if="events.length === 0" class="security-note">还没有可显示的安全事件。</p>
          <ul v-else class="event-list" aria-label="账号安全事件列表">
            <li v-for="event in events" :key="event.id" class="event-item">
              <div class="event-main">
                <p class="event-title">
                  <strong>{{ eventLabel(event.event_type) }}</strong>
                  <span class="event-result" :class="'event-result-' + event.result">{{ eventResultLabels[event.result] }}</span>
                </p>
                <p class="event-meta">{{ formatTime(event.occurred_at) }} · 操作者：{{ actorLabel(event.actor_kind) }}</p>
                <p v-if="event.reason_code" class="event-meta">原因：{{ event.reason_code }}</p>
              </div>
            </li>
          </ul>
        </section>

        <aside class="security-help" aria-label="账号帮助">
          <strong>忘记当前密码或账号被停用？</strong>
          <p>请联系管理员生成一次性重置链接。管理员无法读取你的密码，也不会通过聊天或邮件索要密码。系统不提供自助找回。</p>
        </aside>
      </div>
    </main>
  </div>
</template>

<style scoped>
.security-shell-page {
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--surface-canvas);
}
.security-page {
  padding: 34px 20px 60px;
}
.security-shell {
  width: min(780px, 100%);
  margin: 0 auto;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 20px;
}
.security-shell > *,
.security-topbar > *,
.security-section-head > *,
.security-account-panel > *,
.session-item > * {
  min-width: 0;
}
.security-form,
.session-title,
.session-main {
  min-width: 0;
  overflow: hidden;
}
.security-topbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
}
.security-brand {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 0 9px;
  color: var(--action-link);
  font-size: 12px;
  letter-spacing: 0.16em;
}
h1 {
  margin: 0;
  font-size: clamp(28px, 4vw, 34px);
  line-height: 1.2;
  letter-spacing: -0.02em;
}
h2 {
  margin: 0 0 10px;
  font-size: 19px;
}
.security-lede {
  max-width: 560px;
  margin: 10px 0 0;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.75;
}
.security-back {
  flex: none;
  color: var(--action-primary);
  font-size: 13px;
}
.security-panel {
  padding: 24px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-md);
  background: var(--surface-panel);
  box-shadow: var(--shadow-panel);
}
.security-account-panel {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  background: var(--surface-panel);
}
.security-panel-icon {
  display: grid;
  flex: none;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: var(--action-primary);
  color: var(--action-primary-text);
  font-size: 14px;
  font-weight: 600;
}
.security-account-email {
  margin: 0 0 4px;
  font-size: 16px;
  font-weight: 600;
  word-break: break-all;
}
.security-kicker {
  margin: 0 0 7px;
  color: var(--action-link);
  font-size: 12px;
  letter-spacing: 0.18em;
}
.security-note {
  margin: 8px 0;
  color: var(--text-muted);
  line-height: 1.7;
  font-size: 13px;
}
.security-note-inline {
  margin: 0;
  font-size: 12px;
}
.security-form {
  display: grid;
  gap: 16px;
  margin-top: 18px;
}
.security-form-foot {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.security-section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.security-chip {
  flex: none;
  padding: 4px 9px;
  border-radius: var(--radius-pill);
  background: var(--action-soft);
  color: var(--action-link);
  font-size: 12px;
}
.security-primary,
.security-secondary {
  min-height: 42px;
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  font-size: 14px;
}
.security-primary {
  justify-self: start;
  border: 0;
  background: var(--action-primary);
  color: var(--action-primary-text);
}
.security-primary:hover:not(:disabled) {
  background: var(--action-primary-hover);
}
.security-secondary {
  border: 1px solid var(--line-hairline);
  background: transparent;
  color: var(--action-primary);
}
.security-primary:disabled,
.security-secondary:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.security-error {
  margin: 8px 0 0;
  color: var(--state-danger);
  line-height: 1.6;
}
.security-success {
  margin: 8px 0 0;
  color: var(--state-success);
}
.session-list,
.event-list {
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
}
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 15px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-sm);
  background: var(--surface-panel);
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
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, var(--state-success) 10%, var(--surface-panel));
  color: var(--state-success);
  font-size: 12px;
}
.session-meta,
.event-meta {
  margin: 3px 0 0;
  color: var(--text-muted);
  font-size: 12px;
}
.session-revoke {
  flex: none;
}
.event-item {
  padding: 15px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-sm);
  background: var(--surface-panel);
}
.event-title {
  margin: 0 0 6px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.event-result {
  padding: 2px 7px;
  border-radius: var(--radius-pill);
  background: var(--surface-canvas);
  color: var(--text-muted);
  font-size: 12px;
}
.event-result-success {
  background: color-mix(in srgb, var(--state-success) 10%, var(--surface-panel));
  color: var(--state-success);
}
.event-result-failure,
.event-result-denied {
  background: color-mix(in srgb, var(--state-danger) 8%, var(--surface-panel));
  color: var(--state-danger);
}
.event-result-limited {
  background: color-mix(in srgb, var(--state-warning) 10%, var(--surface-panel));
  color: var(--state-warning);
}
.security-help {
  padding: 16px 18px;
  border-left: 2px solid var(--action-link);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: var(--surface-inset);
}
.security-help strong {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
}
.security-help p {
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.7;
}
@media (max-width: 640px) {
  .security-page {
    padding: 22px 12px 42px;
  }
  .security-topbar,
  .security-section-head,
  .session-item {
    align-items: stretch;
    flex-direction: column;
  }
  .security-panel {
    padding: 18px;
  }
  .security-back {
    align-self: flex-start;
  }
  .security-primary,
  .security-secondary {
    width: 100%;
  }
  .security-form-foot {
    display: grid;
  }
}
</style>
