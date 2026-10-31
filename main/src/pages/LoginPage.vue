<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import { login } from '@/auth/session'

const router = useRouter()
const route = useRoute()
const email = ref('')
const password = ref('')
const submitting = ref(false)
const errorText = ref<string | null>(null)

const canSubmit = computed(() => email.value.trim().length > 0 && password.value.length > 0 && !submitting.value)

function redirectTarget(): string {
  const value = Array.isArray(route.query.redirect) ? route.query.redirect[0] : route.query.redirect
  if (typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.startsWith('/login')) {
    return value
  }
  return '/'
}

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  submitting.value = true
  errorText.value = null
  try {
    await login(email.value, password.value)
    await router.push(redirectTarget())
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      errorText.value = '邮箱或密码不正确。'
    } else if (error instanceof ApiError && error.status === 429) {
      errorText.value = '尝试次数过多，请稍后再试。'
    } else {
      errorText.value = '暂时无法登录，请稍后再试。'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-panel" aria-labelledby="login-title">
      <p class="login-brand">电商生图智能体</p>
      <h1 id="login-title">登录工作台</h1>
      <p class="login-hint">使用管理员创建的账号和本人设置的安全密码登录。</p>
      <form class="login-form" novalidate @submit.prevent="submit">
        <label class="login-field">
          <span>邮箱</span>
          <input
            v-model="email"
            type="email"
            name="email"
            inputmode="email"
            autocomplete="username"
            required
            :disabled="submitting"
          />
        </label>
        <label class="login-field">
          <span>密码</span>
          <input
            v-model="password"
            type="password"
            name="password"
            autocomplete="current-password"
            required
            :disabled="submitting"
          />
        </label>
        <p v-if="errorText" class="login-error" role="alert">{{ errorText }}</p>
        <button class="login-submit" type="submit" :disabled="!canSubmit">
          {{ submitting ? '正在登录…' : '登录' }}
        </button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 100%;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(circle at 18% 12%, rgba(184, 154, 104, 0.18), transparent 34%),
    radial-gradient(circle at 82% 86%, rgba(112, 82, 57, 0.12), transparent 30%),
    var(--color-page);
}

.login-panel {
  width: min(420px, 100%);
  padding: 36px 32px 32px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-panel);
  background: var(--color-panel);
  box-shadow: var(--shadow-panel);
}

.login-brand {
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

.login-hint {
  margin: 10px 0 26px;
  color: var(--color-text-muted);
  line-height: 1.7;
}

.login-form {
  display: grid;
  gap: 18px;
}

.login-field {
  display: grid;
  gap: 8px;
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
}

.login-field input:focus {
  border-color: var(--color-primary);
}

.login-error {
  margin: 0;
  color: var(--color-danger);
}

.login-submit {
  min-height: 46px;
  border: 0;
  border-radius: var(--radius-control);
  background: var(--color-primary);
  color: #fff;
  font-size: 15px;
}

.login-submit:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.login-submit:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
</style>
