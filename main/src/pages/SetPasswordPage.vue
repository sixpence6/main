<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { ApiError, api } from '@/api/client'
import { setCsrfToken } from '@/api/csrf'
import type { LinkStatusResponse } from '@/api/types'

type PageState = 'loading' | 'ready' | 'invalid' | 'done'

const route = useRoute()
const router = useRouter()
const state = ref<PageState>('loading')
const linkInfo = ref<LinkStatusResponse | null>(null)
const password = ref('')
const confirmation = ref('')
const submitting = ref(false)
const errorText = ref<string | null>(null)

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

const canSubmit = computed(
  () =>
    state.value === 'ready' &&
    password.value.length >= 15 &&
    password.value.length <= 128 &&
    confirmation.value.length > 0 &&
    !submitting.value,
)

const purposeLabel = computed(() => (linkInfo.value?.purpose === 'reset' ? '重置登录密码' : '设置登录密码'))

function passwordError(): string | null {
  if (password.value.length < 15) return '密码至少需要 15 个 Unicode 码点。'
  if (password.value.length > 128) return '密码不能超过 128 个 Unicode 码点。'
  if (password.value !== confirmation.value) return '两次输入的密码不一致。'
  return null
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
  const validation = passwordError()
  if (validation) {
    errorText.value = validation
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
  <main class="password-page">
    <section class="password-panel" aria-labelledby="password-title">
      <p class="password-brand">电商生图智能体</p>
      <h1 id="password-title">{{ purposeLabel }}</h1>

      <p v-if="state === 'loading'" class="password-hint">正在核实链接…</p>

      <template v-else-if="state === 'invalid'">
        <p class="password-error" role="alert">
          {{ errorText ?? '链接无效、已过期或已被使用，请联系管理员重新获取。' }}
        </p>
        <RouterLink class="password-link" :to="{ name: 'login' }">返回登录</RouterLink>
      </template>

      <template v-else-if="state === 'done'">
        <p class="password-success" role="status">密码已设置成功，请使用新密码登录。</p>
        <RouterLink class="password-link" :to="{ name: 'login' }">前往登录</RouterLink>
      </template>

      <template v-else>
        <p class="password-hint">
          {{ linkInfo?.account_email }}，请设置一个至少 15 个字符、只有你本人知道的安全密码。
        </p>
        <form class="password-form" novalidate @submit.prevent="submit">
          <label class="password-field">
            <span>新密码</span>
            <input
              v-model="password"
              type="password"
              name="new-password"
              autocomplete="new-password"
              minlength="15"
              maxlength="128"
              required
              :disabled="submitting"
            />
          </label>
          <label class="password-field">
            <span>再次输入新密码</span>
            <input
              v-model="confirmation"
              type="password"
              name="confirm-password"
              autocomplete="new-password"
              minlength="15"
              maxlength="128"
              required
              :disabled="submitting"
            />
          </label>
          <p v-if="errorText" class="password-error" role="alert">{{ errorText }}</p>
          <button class="password-submit" type="submit" :disabled="!canSubmit">
            {{ submitting ? '正在设置…' : '设置密码' }}
          </button>
        </form>
      </template>
    </section>
  </main>
</template>

<style scoped>
.password-page {
  min-height: 100%;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(circle at 18% 12%, rgba(184, 154, 104, 0.18), transparent 34%),
    radial-gradient(circle at 82% 86%, rgba(112, 82, 57, 0.12), transparent 30%),
    var(--color-page);
}

.password-panel {
  width: min(440px, 100%);
  padding: 36px 32px 32px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-panel);
  background: var(--color-panel);
  box-shadow: var(--shadow-panel);
}

.password-brand {
  margin: 0 0 22px;
  color: var(--color-accent);
  font-size: 13px;
  letter-spacing: 0.12em;
}

h1 {
  margin: 0;
  font-size: 26px;
  line-height: 1.25;
  font-weight: 600;
}

.password-hint {
  margin: 12px 0 24px;
  color: var(--color-text-muted);
  line-height: 1.7;
}

.password-form {
  display: grid;
  gap: 18px;
}

.password-field {
  display: grid;
  gap: 8px;
  color: var(--color-text-muted);
  font-size: 13px;
}

.password-field input {
  width: 100%;
  min-height: 46px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  background: #fff;
  color: var(--color-text);
  font-size: var(--text-input);
}

.password-field input:focus {
  border-color: var(--color-primary);
}

.password-error {
  margin: 0;
  color: var(--color-danger);
  line-height: 1.6;
}

.password-success {
  margin: 16px 0 22px;
  color: var(--color-primary);
  line-height: 1.7;
}

.password-submit {
  min-height: 46px;
  border: 0;
  border-radius: var(--radius-control);
  background: var(--color-primary);
  color: #fff;
  font-size: 15px;
}

.password-submit:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.password-submit:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.password-link {
  display: inline-block;
  color: var(--color-primary);
}
</style>
