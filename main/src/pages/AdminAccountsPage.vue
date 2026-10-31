<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ApiError, api } from '@/api/client'
import type { AccountView, LinkIssueResponse, LinkPurpose } from '@/api/types'

const accounts = ref<AccountView[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)
const email = ref('')
const creating = ref(false)
const createMessage = ref<string | null>(null)
const createError = ref<string | null>(null)
const issuingId = ref<string | null>(null)
const linkResult = ref<LinkIssueResponse | null>(null)
const linkError = ref<string | null>(null)
const copied = ref(false)
let listController: AbortController | null = null

const statusLabels: Record<AccountView['status'], string> = {
  pending_setup: '待设置密码',
  active: '正常',
  disabled: '已停用',
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 409) return error.message
    if (error.status === 429) return '操作过于频繁，请稍后再试。'
    if (error.status === 503) return '服务暂时不可用，请稍后再试。'
    if (error.status === 404) return '账号不存在或已被移除。'
    if (error.status === 403) return '只有管理员可以管理账号。'
    if (error.status === 422) return error.message
  }
  return fallback
}

async function loadAccounts(): Promise<void> {
  listController?.abort()
  listController = new AbortController()
  loading.value = true
  loadError.value = null
  try {
    const response = await api.listAccounts(listController.signal)
    accounts.value = response.items
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return
    loadError.value = errorText(error, '暂时无法读取账号列表。')
  } finally {
    loading.value = false
  }
}

async function createAccount(): Promise<void> {
  const value = email.value.trim()
  if (!value) {
    createError.value = '请输入邮箱地址。'
    return
  }
  creating.value = true
  createError.value = null
  createMessage.value = null
  try {
    const response = await api.createAccount(value)
    email.value = ''
    createMessage.value =
      response.account.status === 'pending_setup'
        ? '账号已创建，请为其生成一次性设置链接并交给本人。'
        : '账号已创建。'
    await loadAccounts()
  } catch (error) {
    createError.value = errorText(error, '暂时无法创建账号。')
  } finally {
    creating.value = false
  }
}

async function issueLink(account: AccountView): Promise<void> {
  if (account.is_admin || account.status === 'disabled') return
  const purpose: LinkPurpose = account.status === 'pending_setup' ? 'setup' : 'reset'
  issuingId.value = account.id
  linkError.value = null
  copied.value = false
  try {
    linkResult.value = await api.issueAccountLink(account.id, purpose)
  } catch (error) {
    linkError.value = errorText(error, '暂时无法生成链接。')
  } finally {
    issuingId.value = null
  }
}

async function copyLink(): Promise<void> {
  if (!linkResult.value) return
  try {
    await navigator.clipboard.writeText(linkResult.value.link)
    copied.value = true
  } catch {
    copied.value = false
    linkError.value = '无法自动复制，请手动选中链接复制。'
  }
}

function selectLink(event: FocusEvent): void {
  const target = event.target
  if (target instanceof HTMLInputElement) target.select()
}

function closeLink(): void {
  linkResult.value = null
  linkError.value = null
  copied.value = false
}

onMounted(loadAccounts)
onBeforeUnmount(() => {
  listController?.abort()
  linkResult.value = null
})
</script>

<template>
  <main class="accounts-page">
    <div class="accounts-shell">
      <div class="accounts-topbar">
        <div>
          <p class="accounts-brand">电商生图智能体</p>
          <h1>账号管理</h1>
        </div>
        <RouterLink class="accounts-back" :to="{ name: 'describe' }">返回工作台</RouterLink>
      </div>

      <section class="accounts-panel" aria-labelledby="create-account-title">
        <h2 id="create-account-title">创建普通账号</h2>
        <p class="accounts-note">系统不开放自助注册，也不发送邮件。创建后生成一次性链接，由管理员当面或通过可信渠道交给本人。</p>
        <form class="accounts-create" novalidate @submit.prevent="createAccount">
          <label class="accounts-field">
            <span>邮箱</span>
            <input
              v-model="email"
              type="email"
              inputmode="email"
              autocomplete="off"
              placeholder="user@example.com"
              required
              :disabled="creating"
            />
          </label>
          <button class="accounts-primary" type="submit" :disabled="creating || !email.trim()">
            {{ creating ? '正在创建…' : '创建账号' }}
          </button>
        </form>
        <p v-if="createMessage" class="accounts-success" role="status">{{ createMessage }}</p>
        <p v-if="createError" class="accounts-error" role="alert">{{ createError }}</p>
      </section>

      <section class="accounts-panel" aria-labelledby="account-list-title">
        <h2 id="account-list-title">账号列表</h2>
        <p v-if="loading" class="accounts-note">正在读取账号…</p>
        <p v-else-if="loadError" class="accounts-error" role="alert">{{ loadError }}</p>
        <p v-else-if="accounts.length === 0" class="accounts-note">还没有普通账号。</p>
        <div v-else class="accounts-table-wrap">
          <table class="accounts-table">
            <thead>
              <tr>
                <th scope="col">邮箱</th>
                <th scope="col">状态</th>
                <th scope="col">创建时间</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="account in accounts" :key="account.id">
                <td>
                  <span class="accounts-email">{{ account.email }}</span>
                  <span v-if="account.is_admin" class="accounts-admin-tag">管理员</span>
                </td>
                <td>{{ statusLabels[account.status] }}</td>
                <td>{{ account.created_at }}</td>
                <td>
                  <button
                    v-if="!account.is_admin && account.status !== 'disabled'"
                    class="accounts-action"
                    type="button"
                    :disabled="issuingId === account.id"
                    @click="issueLink(account)"
                  >
                    {{ issuingId === account.id ? '正在生成…' : account.status === 'pending_setup' ? '生成设置链接' : '生成重置链接' }}
                  </button>
                  <span v-else-if="account.status === 'disabled'" class="accounts-note">已停用</span>
                  <span v-else class="accounts-note">由本机 CLI 管理</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="linkError && !linkResult" class="accounts-error" role="alert">{{ linkError }}</p>
      </section>

      <section v-if="linkResult" class="accounts-panel link-panel" aria-labelledby="link-title" role="dialog" aria-modal="true">
        <h2 id="link-title">一次性链接</h2>
        <p class="accounts-note">
          此链接只显示这一次，关闭后无法再次查看。请立即复制并通过可信方式交给账号本人；不要粘贴到公开聊天、工单或日志中。
        </p>
        <p class="accounts-note">有效期至 {{ linkResult.expires_at }}，使用一次后立即失效。</p>
        <label class="accounts-field">
          <span>链接</span>
          <input :value="linkResult.link" type="text" readonly @focus="selectLink" />
        </label>
        <div class="link-actions">
          <button class="accounts-primary" type="button" @click="copyLink">
            {{ copied ? '已复制' : '复制链接' }}
          </button>
          <button class="accounts-action" type="button" @click="closeLink">关闭并丢弃</button>
        </div>
        <p class="accounts-note">目标账号：{{ linkResult.account_id }} · 用途：{{ linkResult.purpose === 'reset' ? '重置密码' : '首次设置密码' }}</p>
      </section>
    </div>
  </main>
</template>

<style scoped>
.accounts-page {
  min-height: 100%;
  padding: 32px 20px 56px;
  background: var(--color-page);
}

.accounts-shell {
  width: min(920px, 100%);
  margin: 0 auto;
  display: grid;
  gap: 20px;
}

.accounts-topbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.accounts-brand {
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

.accounts-back {
  color: var(--color-primary);
}

.accounts-panel {
  padding: 22px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-panel);
  background: var(--color-panel);
  box-shadow: var(--shadow-panel);
}

h2 {
  margin: 0 0 10px;
  font-size: 18px;
}

.accounts-note {
  margin: 8px 0;
  color: var(--color-text-muted);
  line-height: 1.7;
  font-size: 13px;
}

.accounts-create {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  margin-top: 16px;
}

.accounts-field {
  flex: 1;
  display: grid;
  gap: 8px;
  color: var(--color-text-muted);
  font-size: 13px;
}

.accounts-field input {
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control);
  background: #fff;
  color: var(--color-text);
  font-size: var(--text-input);
}

.accounts-primary,
.accounts-action {
  min-height: 42px;
  padding: 8px 16px;
  border-radius: var(--radius-control);
  font-size: 14px;
}

.accounts-primary {
  border: 0;
  background: var(--color-primary);
  color: #fff;
}

.accounts-primary:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.accounts-primary:disabled,
.accounts-action:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.accounts-action {
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-primary);
}

.accounts-success {
  margin: 12px 0 0;
  color: var(--color-primary);
}

.accounts-error {
  margin: 12px 0 0;
  color: var(--color-danger);
  line-height: 1.6;
}

.accounts-table-wrap {
  margin-top: 12px;
  overflow-x: auto;
}

.accounts-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.accounts-table th,
.accounts-table td {
  padding: 12px 10px;
  border-bottom: 1px solid var(--color-border);
  text-align: left;
  vertical-align: middle;
}

.accounts-table th {
  color: var(--color-text-muted);
  font-size: 12px;
  font-weight: 500;
}

.accounts-email {
  word-break: break-all;
}

.accounts-admin-tag {
  margin-left: 8px;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--color-page);
  color: var(--color-text-muted);
  font-size: 11px;
}

.link-panel {
  border-color: var(--color-primary);
}

.link-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}

@media (max-width: 640px) {
  .accounts-page {
    padding: 20px 12px 40px;
  }

  .accounts-topbar,
  .accounts-create {
    align-items: stretch;
    flex-direction: column;
  }

  .accounts-panel {
    padding: 16px;
  }
}
</style>
