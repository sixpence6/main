<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { ApiError, api } from '@/api/client'
import { setCsrfToken } from '@/api/csrf'
import type { LinkStatusResponse } from '@/api/types'
import AuthFrame from '@/components/AuthFrame.vue'
import PasswordField from '@/components/PasswordField.vue'

type PageState = 'loading' | 'ready' | 'invalid' | 'done'

const route = useRoute()
const router = useRouter()
const state = ref<PageState>('loading')
const linkInfo = ref<LinkStatusResponse | null>(null)
const password = ref('')
const confirmation = ref('')
const submitting = ref(false)
const errorText = ref<string | null>(null)
const showValidation = ref(false)

const token = computed<string | null>(() => {
  const hash = route.hash
  if (!hash.startsWith('#token=')) return null
  const raw = hash.slice('#token='.length)
  if (!raw) return null
  try {
    return decodeURIComponent(raw)
  } catch {
    return null
  }
})

const canSubmit = computed(() => state.value === 'ready' && !submitting.value)
const purposeLabel = computed(() => (linkInfo.value?.purpose === 'reset' ? '重置登录密码' : '设置登录密码'))
const pageDescription = computed(() =>
  linkInfo.value?.purpose === 'reset'
    ? '这是一次性重置链接。设置新密码后，旧密码和所有旧会话都会失效。'
    : '这是一次性设置链接。请由账号本人设置密码；设置成功后即可使用新密码登录。',
)

const passwordLengthError = computed(() => {
  if (password.value.length < 15) return '密码至少需要 15 个 Unicode 码点。'
  if (password.value.length > 128) return '密码不能超过 128 个 Unicode 码点。'
  return null
})
const confirmationError = computed(() => {
  if (password.value !== confirmation.value) return '两次输入的密码不一致。'
  return null
})
const newPasswordError = computed(() => (showValidation.value ? passwordLengthError.value : null))
const confirmFieldError = computed(() => (showValidation.value ? confirmationError.value : null))
const policyItems = computed(() => [
  { key: 'minimum', label: '至少 15 个 Unicode 码点', met: password.value.length >= 15 },
  { key: 'maximum', label: '不超过 128 个 Unicode 码点', met: password.value.length <= 128 },
  { key: 'match', label: '两次输入一致', met: confirmation.value.length > 0 && password.value === confirmation.value },
])

function passwordError(): string | null {
  return passwordLengthError.value ?? confirmationError.value
}

function friendlyError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return '验证尝试次数过多，请稍后再试。'
    if (error.status === 503) return '认证服务暂时不可用，请稍后再试。'
    if (error.status === 422) return error.message
    if (error.status === 400) return '链接无效、已过期或已被使用，请联系管理员重新获取。'
  }
  return '暂时无法完成设置，请稍后再试。'
}

function clearError(): void {
  errorText.value = null
}

async function loadLink(): Promise<void> {
  if (!token.value) {
    state.value = 'invalid'
    return
  }
  try {
    linkInfo.value = await api.getLinkStatus(token.value)
    state.value = 'ready'
  } catch (error) {
    state.value = 'invalid'
    errorText.value = friendlyError(error)
  }
}

async function submit(): Promise<void> {
  if (!token.value || !linkInfo.value || !canSubmit.value) return
  showValidation.value = true
  const validation = passwordError()
  if (validation) {
    errorText.value = null
    return
  }
  submitting.value = true
  errorText.value = null
  try {
    const preauth = await api.getLoginCsrf()
    setCsrfToken(preauth.csrf_token)
    await api.setPasswordWithLink(token.value, password.value, linkInfo.value.purpose)
    password.value = ''
    confirmation.value = ''
    state.value = 'done'
    // 成功后立即从地址栏移除一次性凭证，避免被历史记录或截图长期保留。
    await router.replace({ name: 'set-password' })
  } catch (error) {
    errorText.value = friendlyError(error)
    if (error instanceof ApiError && error.status === 400) {
      state.value = 'invalid'
    }
  } finally {
    submitting.value = false
  }
}

onMounted(loadLink)
</script>

<template>
  <AuthFrame
    eyebrow="ONE-TIME CREDENTIAL"
    :title="purposeLabel"
    :description="pageDescription"
  >
    <div v-if="state === 'loading'" class="password-state" role="status">
      <span class="password-spinner" aria-hidden="true"></span>
      <p>正在核实链接…</p>
      <small>只读取链接状态，不会设置密码。</small>
    </div>

    <div v-else-if="state === 'invalid'" class="password-state password-state-invalid">
      <p class="password-state-kicker">链接不可用</p>
      <p class="password-error" role="alert">
        {{ errorText ?? '链接无效、已过期或已被使用，请联系管理员重新获取。' }}
      </p>
      <p class="password-state-help">一次性链接通常只有 30 分钟有效期，使用一次后立即失效。管理员可以重新生成，但无法查看旧链接或密码。</p>
      <RouterLink class="password-link" :to="{ name: 'login' }">返回登录</RouterLink>
    </div>

    <div v-else-if="state === 'done'" class="password-state password-state-done">
      <span class="password-done-mark" aria-hidden="true">✓</span>
      <p class="password-success" role="status">密码已设置成功，请使用新密码登录。</p>
      <p class="password-state-help">旧密码和此前签发的会话已失效。请使用新密码重新登录。</p>
      <RouterLink class="password-link" :to="{ name: 'login' }">前往登录</RouterLink>
    </div>

    <template v-else>
      <p class="password-account">
        <span>正在为</span>
        <strong>{{ linkInfo?.account_email }}</strong>
        <span>设置密码</span>
      </p>
      <form class="password-form" novalidate @submit.prevent="submit">
        <PasswordField
          v-model="password"
          label="新密码"
          name="new-password"
          autocomplete="new-password"
          :minlength="15"
          :maxlength="128"
          :required="true"
          :disabled="submitting"
          :error="newPasswordError"
          hint="至少 15 个 Unicode 码点，可使用空格；不会自动截断。"
          @update:model-value="clearError"
        />
        <PasswordField
          v-model="confirmation"
          label="再次输入新密码"
          name="confirm-password"
          autocomplete="new-password"
          :minlength="15"
          :maxlength="128"
          :required="true"
          :disabled="submitting"
          :error="confirmFieldError"
          @update:model-value="clearError"
        />

        <ul class="password-policy" aria-label="密码要求">
          <li v-for="item in policyItems" :key="item.key" :class="{ 'password-policy-met': item.met }">
            <span aria-hidden="true">{{ item.met ? '✓' : '·' }}</span>
            {{ item.label }}
          </li>
        </ul>

        <p class="password-manager-hint">可以使用浏览器或密码管理器生成并保存密码。页面不会把密码写入本地存储。</p>
        <p v-if="errorText" class="password-error" role="alert">{{ errorText }}</p>
        <button class="password-submit" type="submit" :disabled="submitting">
          {{ submitting ? '正在设置…' : '设置密码' }}
        </button>
      </form>
    </template>

    <template #footer>
      <p>链接无效、已过期或已被使用？请联系管理员重新生成。系统不会通过邮件发送链接。</p>
    </template>
  </AuthFrame>
</template>

<style scoped>
.password-form {
  display: grid;
  gap: 18px;
}
.password-account {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0 0 20px;
  padding: 11px 13px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-sm);
  background: rgba(244, 240, 232, 0.58);
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.6;
}
.password-account strong {
  color: var(--text-primary);
  font-weight: 600;
  word-break: break-all;
}
.password-policy {
  display: grid;
  gap: 4px;
  margin: -2px 0 0;
  padding: 0;
  list-style: none;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.6;
}
.password-policy li {
  display: flex;
  align-items: center;
  gap: 7px;
}
.password-policy li span {
  width: 12px;
  color: var(--action-link);
}
.password-policy-met {
  color: var(--state-success);
}
.password-policy-met span {
  color: var(--state-success);
}
.password-manager-hint {
  margin: -4px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.6;
}
.password-error {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid rgba(164, 61, 53, 0.24);
  border-radius: var(--radius-sm);
  background: rgba(164, 61, 53, 0.06);
  color: var(--state-danger);
  font-size: 12px;
  line-height: 1.6;
}
.password-submit {
  min-height: 48px;
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--action-primary);
  color: var(--action-primary-text);
  font-size: 15px;
  letter-spacing: 0.04em;
  transition: background 160ms ease, opacity 160ms ease;
}
.password-submit:hover:not(:disabled) {
  background: var(--action-primary-hover);
}
.password-submit:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.password-state {
  display: grid;
  justify-items: start;
  gap: 12px;
  padding: 8px 0;
}
.password-state p {
  margin: 0;
  color: var(--text-muted);
  line-height: 1.7;
}
.password-state small {
  color: var(--text-muted);
  font-size: 12px;
}
.password-state-kicker {
  color: var(--action-link) !important;
  font-size: 12px;
  letter-spacing: 0.12em;
}
.password-state-help {
  font-size: 12px;
}
.password-state-invalid .password-error {
  width: 100%;
}
.password-spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--line-hairline);
  border-top-color: var(--action-primary);
  border-radius: 50%;
  animation: password-spin 0.9s linear infinite;
}
@keyframes password-spin {
  to { transform: rotate(360deg); }
}
.password-done-mark {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: 50%;
  background: rgba(66, 102, 76, 0.12);
  color: var(--state-success);
  font-size: 22px;
}
.password-success {
  color: var(--text-primary) !important;
  font-size: 16px;
  font-weight: 500;
}
.password-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  margin-top: 6px;
  padding: 8px 18px;
  border-radius: var(--radius-sm);
  background: var(--action-primary);
  color: var(--action-primary-text);
  text-decoration: none;
  font-size: 14px;
}
.password-link:hover {
  background: var(--action-primary-hover);
}
</style>
