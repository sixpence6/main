<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import { login } from '@/auth/session'
import AuthFrame from '@/components/AuthFrame.vue'
import PasswordField from '@/components/PasswordField.vue'

const router = useRouter()
const route = useRoute()
const email = ref('')
const password = ref('')
const submitting = ref(false)
const errorText = ref<string | null>(null)
const emailError = ref<string | null>(null)
const passwordError = ref<string | null>(null)
const credentialInvalid = ref(false)

const canSubmit = computed(() => !submitting.value)

function redirectTarget(): string {
  const value = Array.isArray(route.query.redirect) ? route.query.redirect[0] : route.query.redirect
  if (typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.startsWith('/login')) {
    return value
  }
  return '/'
}

function validate(): boolean {
  emailError.value = null
  passwordError.value = null
  const address = email.value.trim()
  if (!address) {
    emailError.value = '请输入邮箱地址。'
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(address)) {
    emailError.value = '请输入有效的邮箱地址。'
  }
  if (!password.value) {
    passwordError.value = '请输入密码。'
  }
  return !emailError.value && !passwordError.value
}

function clearErrors(): void {
  errorText.value = null
  emailError.value = null
  passwordError.value = null
  credentialInvalid.value = false
}

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  if (!validate()) return
  submitting.value = true
  errorText.value = null
  credentialInvalid.value = false
  try {
    await login(email.value.trim(), password.value)
    await router.push(redirectTarget())
  } catch (error) {
    credentialInvalid.value = error instanceof ApiError && error.status === 401
    if (error instanceof ApiError && error.status === 401) {
      errorText.value = '邮箱或密码不正确。'
    } else if (error instanceof ApiError && error.status === 429) {
      errorText.value = '尝试次数过多，请稍后再试。为避免连续撞库，请稍后再试或联系管理员。'
    } else if (error instanceof ApiError && error.status === 503) {
      errorText.value = '认证服务暂时不可用，请稍后再试。'
    } else {
      errorText.value = '暂时无法登录，请稍后再试。'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AuthFrame
    eyebrow="ACCOUNT ACCESS"
    title="登录工作台"
    description="使用管理员创建的账号和本人设置的安全密码登录。账号只属于本人，请勿共用。"
  >
    <form class="login-form" novalidate @submit.prevent="submit">
      <div class="login-field">
        <label for="login-email">邮箱</label>
        <input
          id="login-email"
          v-model="email"
          type="email"
          name="email"
          inputmode="email"
          autocomplete="username"
          autocapitalize="off"
          spellcheck="false"
          required
          :disabled="submitting"
          :aria-invalid="emailError || credentialInvalid ? 'true' : undefined"
          :aria-describedby="['login-email-hint', emailError ? 'login-email-error' : '', errorText ? 'login-form-error' : ''].filter(Boolean).join(' ') || undefined"
          @input="clearErrors"
        />
        <p id="login-email-hint" class="login-hint">使用管理员为你创建的邮箱地址。</p>
        <p v-if="emailError" id="login-email-error" class="login-field-error" role="alert">{{ emailError }}</p>
      </div>

      <PasswordField
        v-model="password"
        label="密码"
        name="password"
        autocomplete="current-password"
        :disabled="submitting"
        :required="true"
        :error="passwordError"
        :described-by="errorText ? 'login-form-error' : undefined"
        hint="密码区分大小写；可使用浏览器或密码管理器自动填充。"
        @update:model-value="clearErrors"
      />

      <p v-if="errorText" id="login-form-error" class="login-error" role="alert">{{ errorText }}</p>

      <button class="login-submit" type="submit" :disabled="submitting">
        {{ submitting ? '正在登录…' : '登录' }}
      </button>
    </form>

    <aside class="login-help" aria-label="无法登录时的帮助">
      <strong>无法登录？</strong>
      <p>请联系管理员生成一次性重置链接。系统不发送邮件，也不会通过聊天、电话或工单索要密码。</p>
    </aside>

    <template #footer>
      <p>登录会话只保存在受保护的 Cookie 中；本页面不会把密码写入浏览器存储。</p>
    </template>
  </AuthFrame>
</template>

<style scoped>
.login-form {
  display: grid;
  gap: 18px;
}
.login-field {
  display: grid;
  gap: 8px;
}
.login-field > label {
  color: var(--color-text-muted);
  font-size: 13px;
}
.login-field input {
  width: 100%;
  min-height: 46px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  background: #fff;
  color: var(--color-text);
  font-size: var(--text-input);
  transition: border-color 160ms ease, box-shadow 160ms ease;
}
.login-field input:focus {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(112, 82, 57, 0.12);
  outline: 0;
}
.login-field input[aria-invalid='true'] {
  border-color: var(--color-danger);
}
.login-hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 11px;
  line-height: 1.6;
}
.login-field-error {
  margin: 0;
  color: var(--color-danger);
  font-size: 12px;
  line-height: 1.55;
}
.login-error {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid rgba(164, 61, 53, 0.24);
  border-radius: var(--radius-control);
  background: rgba(164, 61, 53, 0.06);
  color: var(--color-danger);
  font-size: 12px;
  line-height: 1.6;
}
.login-submit {
  min-height: 48px;
  border: 0;
  border-radius: var(--radius-control);
  background: var(--color-primary);
  color: #fff;
  font-size: 15px;
  letter-spacing: 0.04em;
  transition: background 160ms ease, opacity 160ms ease;
}
.login-submit:hover:not(:disabled) {
  background: var(--color-primary-hover);
}
.login-submit:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.login-help {
  margin-top: 24px;
  padding: 14px 16px;
  border-left: 2px solid var(--color-accent);
  border-radius: 0 var(--radius-control) var(--radius-control) 0;
  background: rgba(184, 154, 104, 0.08);
}
.login-help strong {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  font-weight: 600;
}
.login-help p {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 11px;
  line-height: 1.7;
}
</style>
