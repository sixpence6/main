<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ApiError, api } from '@/api/client'
import BrandMark from '@/components/BrandMark.vue'
import type {
  AccountGenerationLimitsResponse,
  AccountView,
  AdminSecurityEventView,
  InstanceGenerationLimitsResponse,
  LinkIssueResponse,
  LinkPurpose,
} from '@/api/types'

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
const statusActionId = ref<string | null>(null)
const statusMessage = ref<string | null>(null)
const statusError = ref<string | null>(null)
const events = ref<AdminSecurityEventView[]>([])
const eventsLoading = ref(true)
const eventsError = ref<string | null>(null)
const instanceLimits = ref<InstanceGenerationLimitsResponse | null>(null)
const instanceDaily = ref('')
const instancePersonal = ref('')
const instanceSlots = ref('')
const instanceLoading = ref(true)
const instanceSaving = ref(false)
const instanceError = ref<string | null>(null)
const instanceMessage = ref<string | null>(null)
const quotaAccount = ref<AccountView | null>(null)
const quotaDetail = ref<AccountGenerationLimitsResponse | null>(null)
const quotaDaily = ref('')
const quotaPersonal = ref('')
const quotaLoading = ref(false)
const quotaSaving = ref(false)
const quotaError = ref<string | null>(null)
const quotaMessage = ref<string | null>(null)
let listController: AbortController | null = null
let eventsController: AbortController | null = null

const accountSummary = computed(() => ({
  total: accounts.value.length,
  active: accounts.value.filter((account) => account.status === 'active').length,
  pending: accounts.value.filter((account) => account.status === 'pending_setup').length,
  disabled: accounts.value.filter((account) => account.status === 'disabled').length,
}))

const linkTargetEmail = computed(() => {
  if (!linkResult.value) return ''
  return accounts.value.find((account) => account.id === linkResult.value?.account_id)?.email ?? linkResult.value.account_id
})

const statusLabels: Record<AccountView['status'], string> = {
  pending_setup: '待设置密码',
  active: '正常',
  disabled: '已停用',
}

const eventLabels: Record<string, string> = {
  bootstrap_admin_created: '初始化管理员',
  login_success: '登录成功',
  login_failure: '登录失败',
  login_limited: '登录受限',
  logout: '退出登录',
  password_changed: '修改密码',
  account_created: '创建账号',
  account_link_issued: '生成一次性链接',
  account_link_issue_limited: '生成链接受限',
  account_link_used: '使用一次性链接',
  account_link_verify_limited: '验证链接受限',
  session_revoked: '撤销会话',
  sessions_revoked: '撤销多个会话',
  admin_recovered: '本机恢复首管',
  account_disabled: '停用账号',
  account_restored: '恢复账号',
  permission_denied: '权限拒绝',
  generation_limits_updated: '调整生成额度',
  generation_tracking_closed: '结束本地跟踪',
}

const resultLabels: Record<AdminSecurityEventView['result'], string> = {
  success: '成功',
  failure: '失败',
  denied: '拒绝',
  limited: '受限',
}

function eventLabel(type: string): string {
  return eventLabels[type] ?? type
}

function formatEventTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

function statusTransition(event: AdminSecurityEventView): string {
  const before = typeof event.before?.status === 'string' ? event.before.status : null
  const after = typeof event.after?.status === 'string' ? event.after.status : null
  if (before || after) {
    const label = (value: string | null): string =>
      value === 'active' ? '正常' : value === 'pending_setup' ? '待设置密码' : value === 'disabled' ? '已停用' : value === 'closed' ? '本地跟踪已结束' : value ?? '—'
    return label(before) + ' → ' + label(after)
  }
  const parts: string[] = []
  const dailyBefore = event.before?.daily_limit
  const dailyAfter = event.after?.daily_limit
  const personalBefore = event.before?.personal_slots
  const personalAfter = event.after?.personal_slots
  const instanceBefore = event.before?.instance_slots
  const instanceAfter = event.after?.instance_slots
  if (dailyBefore !== undefined || dailyAfter !== undefined) {
    parts.push('每日 ' + (dailyBefore ?? '默认') + ' → ' + (dailyAfter ?? '默认'))
  }
  if (personalBefore !== undefined || personalAfter !== undefined) {
    parts.push('个人 ' + (personalBefore ?? '默认') + ' → ' + (personalAfter ?? '默认'))
  }
  if (instanceBefore !== undefined || instanceAfter !== undefined) {
    parts.push('实例 ' + (instanceBefore ?? '默认') + ' → ' + (instanceAfter ?? '默认'))
  }
  return parts.join(' · ') || '—'
}

function eventDetail(event: AdminSecurityEventView): string {
  const parts: string[] = []
  const sessions = event.detail?.sessions_revoked
  const links = event.detail?.links_revoked
  const purpose = event.detail?.purpose
  const scope = event.detail?.scope
  if (typeof sessions === 'number') parts.push('撤销会话 ' + sessions)
  if (typeof links === 'number') parts.push('撤销链接 ' + links)
  if (typeof purpose === 'string') parts.push('用途 ' + (purpose === 'reset' ? '重置密码' : '首次设置密码'))
  if (scope === 'instance') parts.push('范围 实例')
  if (scope === 'account') parts.push('范围 本账号')
  if (event.detail?.remote_cancelled === false) parts.push('未取消远端任务')
  return parts.join(' · ')
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


function parseLimit(raw: string | number, label: string): number | null {
  const value = String(raw).trim()
  if (!value) return null
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed < 1) throw new Error(label + '必须是正整数。')
  return parsed
}

async function loadInstanceLimits(): Promise<void> {
  instanceLoading.value = true
  instanceError.value = null
  try {
    const response = await api.getInstanceGenerationLimits()
    instanceLimits.value = response
    instanceDaily.value = response.policy.daily_limit?.toString() ?? ''
    instancePersonal.value = response.policy.personal_slots?.toString() ?? ''
    instanceSlots.value = response.policy.instance_slots?.toString() ?? ''
  } catch (error) {
    instanceError.value = errorText(error, '暂时无法读取实例额度。')
  } finally {
    instanceLoading.value = false
  }
}

async function saveInstanceLimits(): Promise<void> {
  instanceSaving.value = true
  instanceError.value = null
  instanceMessage.value = null
  try {
    const response = await api.updateInstanceGenerationLimits({
      daily_limit: parseLimit(instanceDaily.value, '每日受理次数'),
      personal_slots: parseLimit(instancePersonal.value, '个人活跃槽位'),
      instance_slots: parseLimit(instanceSlots.value, '实例并发槽位'),
    })
    instanceLimits.value = response
    instanceMessage.value = '实例生成额度已保存，变更已写入审计。'
    await loadSecurityEvents()
  } catch (error) {
    instanceError.value = error instanceof Error && !(error instanceof ApiError)
      ? error.message
      : errorText(error, '暂时无法保存实例额度。')
  } finally {
    instanceSaving.value = false
  }
}

async function openQuota(account: AccountView): Promise<void> {
  if (account.is_admin) return
  closeLink()
  quotaAccount.value = account
  quotaDetail.value = null
  quotaDaily.value = ''
  quotaPersonal.value = ''
  quotaError.value = null
  quotaMessage.value = null
  quotaLoading.value = true
  try {
    const response = await api.getAccountGenerationLimits(account.id)
    quotaDetail.value = response
    quotaDaily.value = response.policy.daily_limit?.toString() ?? ''
    quotaPersonal.value = response.policy.personal_slots?.toString() ?? ''
  } catch (error) {
    quotaError.value = errorText(error, '暂时无法读取账号额度。')
  } finally {
    quotaLoading.value = false
  }
}

async function saveQuota(): Promise<void> {
  if (!quotaAccount.value) return
  quotaSaving.value = true
  quotaError.value = null
  quotaMessage.value = null
  try {
    const response = await api.updateAccountGenerationLimits(quotaAccount.value.id, {
      daily_limit: parseLimit(quotaDaily.value, '每日受理次数'),
      personal_slots: parseLimit(quotaPersonal.value, '个人活跃槽位'),
    })
    quotaDetail.value = response
    quotaMessage.value = '账号生成额度已保存，变更已写入审计。'
    await loadSecurityEvents()
  } catch (error) {
    quotaError.value = error instanceof Error && !(error instanceof ApiError)
      ? error.message
      : errorText(error, '暂时无法保存账号额度。')
  } finally {
    quotaSaving.value = false
  }
}

function closeQuota(): void {
  quotaAccount.value = null
  quotaDetail.value = null
  quotaError.value = null
  quotaMessage.value = null
}

async function createAccount(): Promise<void> {
  const value = email.value.trim()
  if (!value) {
    createError.value = '请输入邮箱地址。'
    return
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
    createError.value = '请输入有效的邮箱地址。'
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
  closeQuota()
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

async function loadSecurityEvents(): Promise<void> {
  eventsController?.abort()
  eventsController = new AbortController()
  eventsLoading.value = true
  eventsError.value = null
  try {
    const response = await api.listAdminSecurityEvents(eventsController.signal)
    events.value = response.items
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return
    eventsError.value = errorText(error, '暂时无法读取安全事件。')
  } finally {
    eventsLoading.value = false
  }
}

async function setAccountStatus(account: AccountView, disabled: boolean): Promise<void> {
  if (account.is_admin || statusActionId.value) return
  statusActionId.value = account.id
  statusError.value = null
  statusMessage.value = null
  try {
    if (disabled) await api.disableAccount(account.id)
    else await api.restoreAccount(account.id)
    statusMessage.value = disabled
      ? '账号已停用，其登录会话和未使用的一次性链接已撤销。'
      : '账号已恢复；旧会话和旧链接不会恢复，本人需重新登录。'
    await Promise.all([loadAccounts(), loadSecurityEvents()])
  } catch (error) {
    statusError.value = errorText(error, '暂时无法更新账号状态。')
  } finally {
    statusActionId.value = null
  }
}

function handleDialogKey(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  if (linkResult.value) {
    event.preventDefault()
    closeLink()
  } else if (quotaAccount.value) {
    event.preventDefault()
    closeQuota()
  }
}

onMounted(() => {
  void loadAccounts()
  void loadSecurityEvents()
  void loadInstanceLimits()
  window.addEventListener('keydown', handleDialogKey)
})
onBeforeUnmount(() => {
  listController?.abort()
  eventsController?.abort()
  linkResult.value = null
  window.removeEventListener('keydown', handleDialogKey)
})
</script>

<template>
  <main class="accounts-page">
    <div class="accounts-shell">
      <header class="accounts-topbar">
        <div class="accounts-heading">
          <p class="accounts-brand"><BrandMark :size="18" /> 电商生图工作台 · ADMIN</p>
          <h1>账号管理</h1>
          <p class="accounts-lede">创建账号、签发一次性链接、调整生成额度并审计管理操作。管理员不能读取他人的项目内容。</p>
        </div>
        <RouterLink class="accounts-back" :to="{ name: 'describe' }">返回工作台</RouterLink>
      </header>

      <section class="accounts-summary" aria-label="账号概览">
        <div><strong>{{ accountSummary.total }}</strong><span>账号总数</span></div>
        <div><strong>{{ accountSummary.active }}</strong><span>正常</span></div>
        <div><strong>{{ accountSummary.pending }}</strong><span>待设置密码</span></div>
        <div><strong>{{ accountSummary.disabled }}</strong><span>已停用</span></div>
      </section>

      <section class="accounts-panel" aria-labelledby="create-account-title">
        <h2 id="create-account-title">创建普通账号</h2>
        <p class="accounts-note">系统不开放自助注册，也不发送邮件。创建后生成一次性链接，由管理员当面或通过可信渠道交给本人。</p>
        <form class="accounts-create" novalidate @submit.prevent="createAccount">
          <label class="accounts-field">
            <span>邮箱</span>
            <input
              id="create-account-email"
              v-model="email"
              type="email"
              name="email"
              inputmode="email"
              autocomplete="off"
              placeholder="user@example.com"
              required
              :disabled="creating"
              :aria-invalid="createError ? 'true' : undefined"
              aria-describedby="create-account-note create-account-error"
            />
          </label>
          <button class="accounts-primary" type="submit" :disabled="creating">
            {{ creating ? '正在创建…' : '创建账号' }}
          </button>
        </form>
        <p id="create-account-note" class="accounts-note accounts-field-note">账号创建后处于“待设置密码”状态，需生成一次性设置链接。管理员无法设置或查看他人密码。</p>
        <p v-if="createMessage" class="accounts-success" role="status">{{ createMessage }}</p>
        <p v-if="createError" id="create-account-error" class="accounts-error" role="alert">{{ createError }}</p>
      </section>

      <section class="accounts-panel" aria-labelledby="instance-limits-title">
        <div class="accounts-section-head">
          <div>
            <h2 id="instance-limits-title">实例生成额度</h2>
            <p class="accounts-note">限制所有账号共用的每日受理次数和并发槽位。留空表示继承配置默认值；管理员自己的创作任务同样受限。</p>
          </div>
        </div>
        <p v-if="instanceLoading" class="accounts-note">正在读取实例额度…</p>
        <form v-else class="accounts-create" novalidate @submit.prevent="saveInstanceLimits">
          <label class="accounts-field">
            <span>每日受理次数</span>
            <input v-model="instanceDaily" type="number" min="1" step="1" :placeholder="'默认 ' + (instanceLimits?.defaults.daily_limit ?? 20)" />
          </label>
          <label class="accounts-field">
            <span>个人活跃槽位</span>
            <input v-model="instancePersonal" type="number" min="1" step="1" :placeholder="'默认 ' + (instanceLimits?.defaults.personal_slots ?? 1)" />
          </label>
          <label class="accounts-field">
            <span>实例并发槽位</span>
            <input v-model="instanceSlots" type="number" min="1" step="1" :placeholder="'默认 ' + (instanceLimits?.defaults.instance_slots ?? 2)" />
          </label>
          <button class="accounts-primary" type="submit" :disabled="instanceSaving">
            {{ instanceSaving ? '正在保存…' : '保存实例额度' }}
          </button>
        </form>
        <p v-if="instanceLimits" class="accounts-note">
          当前生效：每日 {{ instanceLimits.effective.daily_limit }} 次 · 个人 {{ instanceLimits.effective.personal_slots }} · 实例 {{ instanceLimits.effective.instance_slots }} · 单项目 {{ instanceLimits.effective.project_slots }}
        </p>
        <p v-if="instanceMessage" class="accounts-success" role="status">{{ instanceMessage }}</p>
        <p v-if="instanceError" class="accounts-error" role="alert">{{ instanceError }}</p>
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
                <td data-label="邮箱">
                  <span class="accounts-email">{{ account.email }}</span>
                  <span v-if="account.is_admin" class="accounts-admin-tag">管理员</span>
                </td>
                <td data-label="状态"><span class="accounts-status" :class="'accounts-status-' + account.status">{{ statusLabels[account.status] }}</span></td>
                <td data-label="创建时间">{{ account.created_at }}</td>
                <td data-label="操作">
                  <div class="account-actions">
                    <button
                      v-if="!account.is_admin && account.status !== 'disabled'"
                      class="accounts-action"
                      type="button"
                      :disabled="issuingId === account.id || statusActionId === account.id"
                      @click="issueLink(account)"
                    >
                      {{ issuingId === account.id ? '正在生成…' : account.status === 'pending_setup' ? '生成设置链接' : '生成重置链接' }}
                    </button>
                    <button
                      v-if="!account.is_admin"
                      class="accounts-action"
                      type="button"
                      @click="openQuota(account)"
                    >
                      调整额度
                    </button>
                    <button
                      v-if="!account.is_admin"
                      class="accounts-action"
                      :class="{ 'accounts-danger': account.status !== 'disabled' }"
                      type="button"
                      :disabled="statusActionId === account.id || issuingId === account.id"
                      @click="setAccountStatus(account, account.status !== 'disabled')"
                    >
                      {{ statusActionId === account.id ? '正在处理…' : account.status === 'disabled' ? '恢复账号' : '停用账号' }}
                    </button>
                    <span v-if="account.is_admin" class="accounts-note">由本机 CLI 管理</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="statusMessage" class="accounts-success" role="status">{{ statusMessage }}</p>
        <p v-if="statusError" class="accounts-error" role="alert">{{ statusError }}</p>
        <p v-if="linkError && !linkResult" class="accounts-error" role="alert">{{ linkError }}</p>
      </section>

      <section class="accounts-panel" aria-labelledby="admin-events-title">
        <div class="accounts-section-head">
          <div>
            <h2 id="admin-events-title">安全事件</h2>
            <p class="accounts-note">只展示必要的管理审计字段；不包含密码、令牌、会话凭证或他人的创作内容。</p>
          </div>
          <button class="accounts-action" type="button" :disabled="eventsLoading" @click="loadSecurityEvents">
            {{ eventsLoading ? '正在读取…' : '刷新' }}
          </button>
        </div>
        <p v-if="eventsError" class="accounts-error" role="alert">{{ eventsError }}</p>
        <p v-else-if="eventsLoading" class="accounts-note">正在读取安全事件…</p>
        <p v-else-if="events.length === 0" class="accounts-note">还没有可显示的安全事件。</p>
        <div v-else class="accounts-table-wrap">
          <table class="accounts-table events-table">
            <thead>
              <tr>
                <th scope="col">时间</th>
                <th scope="col">事件</th>
                <th scope="col">结果</th>
                <th scope="col">操作者 / 对象</th>
                <th scope="col">状态变化</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="event in events" :key="event.id">
                <td data-label="时间">{{ formatEventTime(event.occurred_at) }}</td>
                <td data-label="事件">
                  <span>{{ eventLabel(event.event_type) }}</span>
                  <span v-if="eventDetail(event)" class="accounts-event-detail">{{ eventDetail(event) }}</span>
                </td>
                <td data-label="结果"><span class="accounts-status" :class="'accounts-status-result-' + event.result">{{ resultLabels[event.result] }}</span></td>
                <td data-label="操作者 / 对象">
                  <span class="accounts-email">{{ event.actor_label ?? event.actor_kind }}</span>
                  <span class="accounts-event-target">对象：{{ event.target_label ?? '—' }}</span>
                </td>
                <td data-label="状态变化">{{ statusTransition(event) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="quotaAccount" class="accounts-panel link-panel" aria-labelledby="account-quota-title" role="dialog" aria-modal="true">
        <div class="accounts-section-head">
          <div>
            <h2 id="account-quota-title">账号生成额度 · {{ quotaAccount.email }}</h2>
            <p class="accounts-note">只调整该账号的每日受理次数和个人活跃槽位；留空表示继承实例设置。不会读取或展示该账号的项目内容。</p>
          </div>
          <button class="accounts-action" type="button" @click="closeQuota">关闭</button>
        </div>
        <p v-if="quotaLoading" class="accounts-note">正在读取账号额度…</p>
        <form v-else class="accounts-create" novalidate @submit.prevent="saveQuota">
          <label class="accounts-field">
            <span>每日受理次数</span>
            <input v-model="quotaDaily" type="number" min="1" step="1" :placeholder="quotaDetail ? '继承 ' + quotaDetail.effective.daily_limit : '继承实例设置'" />
          </label>
          <label class="accounts-field">
            <span>个人活跃槽位</span>
            <input v-model="quotaPersonal" type="number" min="1" step="1" :placeholder="quotaDetail ? '继承 ' + quotaDetail.effective.personal_slots : '继承实例设置'" />
          </label>
          <button class="accounts-primary" type="submit" :disabled="quotaSaving">
            {{ quotaSaving ? '正在保存…' : '保存账号额度' }}
          </button>
        </form>
        <template v-if="quotaDetail">
          <p class="accounts-note">
            当前生效：每日 {{ quotaDetail.effective.daily_limit }} 次 · 个人 {{ quotaDetail.effective.personal_slots }} · 已用 {{ quotaDetail.usage.used }} · 剩余 {{ quotaDetail.usage.remaining }}
          </p>
          <p class="accounts-note">
            活跃：个人 {{ quotaDetail.usage.personal_active }} / {{ quotaDetail.usage.personal_limit }} · 实例 {{ quotaDetail.usage.instance_active }} / {{ quotaDetail.usage.instance_limit }}
          </p>
          <p v-if="quotaDetail.usage.window_started_at" class="accounts-note">
            滚动窗口：{{ quotaDetail.usage.window_started_at }} 至 {{ quotaDetail.usage.window_expires_at }}
          </p>
        </template>
        <p class="accounts-note">额度只限制受理次数和并发槽位，不承诺模型费用封顶；受限请求会在调用前被拒绝，不会排队或自动重交。</p>
        <p v-if="quotaMessage" class="accounts-success" role="status">{{ quotaMessage }}</p>
        <p v-if="quotaError" class="accounts-error" role="alert">{{ quotaError }}</p>
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
        <p class="accounts-note">目标账号：{{ linkTargetEmail }} · 用途：{{ linkResult.purpose === 'reset' ? '重置密码' : '首次设置密码' }}</p>
      </section>
    </div>
  </main>
</template>

<style scoped>
.accounts-page {
  min-height: 100vh;
  min-height: 100dvh;
  padding: 32px 20px 56px;
  background: var(--surface-canvas);
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
  color: var(--action-link);
  font-size: 13px;
  letter-spacing: 0.12em;
}

h1 {
  margin: 0;
  font-size: 28px;
  line-height: 1.2;
}

.accounts-back {
  color: var(--action-primary);
}

.accounts-panel {
  padding: 22px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-md);
  background: var(--surface-panel);
  box-shadow: var(--shadow-composer);
}

h2 {
  margin: 0 0 10px;
  font-size: 18px;
}

.accounts-note {
  margin: 8px 0;
  color: var(--text-muted);
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
  color: var(--text-muted);
  font-size: 13px;
}

.accounts-field input {
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-sm);
  background: var(--surface-panel);
  color: var(--text-primary);
  font-size: var(--text-reading);
}

.accounts-primary,
.accounts-action {
  min-height: 42px;
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  font-size: 14px;
}

.accounts-primary {
  border: 0;
  background: var(--action-primary);
  color: var(--action-primary-text);
}

.accounts-primary:hover:not(:disabled) {
  background: var(--action-primary-hover);
}

.accounts-primary:disabled,
.accounts-action:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.accounts-action {
  border: 1px solid var(--line-hairline);
  background: transparent;
  color: var(--action-primary);
}

.accounts-success {
  margin: 12px 0 0;
  color: var(--action-primary);
}

.accounts-error {
  margin: 12px 0 0;
  color: var(--state-danger);
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
  border-bottom: 1px solid var(--line-hairline);
  text-align: left;
  vertical-align: middle;
}

.accounts-table th {
  color: var(--text-muted);
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
  background: var(--surface-canvas);
  color: var(--text-muted);
  font-size: 12px;
}

.link-panel {
  border-color: var(--action-primary);
}

.link-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}

.accounts-section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.account-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.accounts-danger {
  border-color: var(--state-danger);
  color: var(--state-danger);
}

.events-table td {
  font-size: 13px;
}

.accounts-event-detail,
.accounts-event-target {
  display: block;
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 12px;
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

.accounts-heading {
  min-width: 0;
}

.accounts-brand {
  display: flex;
  align-items: center;
  gap: 7px;
}

.accounts-lede {
  max-width: 620px;
  margin: 10px 0 0;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.75;
}

.accounts-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.accounts-summary div {
  display: grid;
  gap: 3px;
  padding: 14px 16px;
  border: 1px solid var(--line-hairline);
  border-radius: var(--radius-sm);
  background: rgba(255, 252, 247, 0.62);
}

.accounts-summary strong {
  font-size: 20px;
  font-weight: 600;
}

.accounts-summary span {
  color: var(--text-muted);
  font-size: 12px;
}

.accounts-field-note {
  margin-top: 10px;
}

.accounts-status {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--surface-canvas);
  color: var(--text-muted);
  font-size: 12px;
  white-space: nowrap;
}

.accounts-status-active,
.accounts-status-result-success {
  background: rgba(66, 102, 76, 0.1);
  color: var(--state-success);
}

.accounts-status-pending_setup {
  background: rgba(124, 90, 34, 0.1);
  color: var(--state-warning);
}

.accounts-status-disabled,
.accounts-status-result-failure,
.accounts-status-result-denied {
  background: rgba(164, 61, 53, 0.08);
  color: var(--state-danger);
}

.accounts-status-result-limited {
  background: rgba(124, 90, 34, 0.1);
  color: var(--state-warning);
}

@media (max-width: 720px) {
  .accounts-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .accounts-table-wrap {
    overflow-x: visible;
  }

  .accounts-table,
  .accounts-table tbody,
  .accounts-table tr,
  .accounts-table td {
    display: block;
    width: 100%;
  }

  .accounts-table thead {
    display: none;
  }

  .accounts-table tbody {
    display: grid;
    gap: 10px;
  }

  .accounts-table tr {
    padding: 12px 14px;
    border: 1px solid var(--line-hairline);
    border-radius: var(--radius-sm);
    background: rgba(255, 252, 247, 0.54);
  }

  .accounts-table td {
    display: grid;
    grid-template-columns: 86px minmax(0, 1fr);
    gap: 8px;
    padding: 5px 0;
    border: 0;
    font-size: 13px;
  }

  .accounts-table td::before {
    content: attr(data-label);
    color: var(--text-muted);
    font-size: 12px;
  }

  .account-actions {
    display: grid;
    grid-template-columns: 1fr;
  }

  .account-actions .accounts-action {
    width: 100%;
  }
}

</style>
