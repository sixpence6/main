<script setup lang="ts">
import AccessibleDrawer from './AccessibleDrawer.vue'
import { useWorkspace } from '@/composables/useWorkspace'
import type { ApiIssue, ModelConfigStatus, VisualConfigStatus } from '@/api/types'

const workspace = useWorkspace()
const { modelDrawerOpen, closeModelDrawer, configStatus, configError, configLoading, refreshConfig } = workspace

const UNSAFE_ISSUE = /(sk-[a-z0-9]|bearer\s|api[_-]?key\s*[:=]|https?:\/\/|traceback|\.py\b|\bat\s+\S+\s*\(|<script)/i

function issueText(issue: ApiIssue): string {
  const message = typeof issue?.message === 'string' ? issue.message : ''
  if (!message || message.length > 240 || UNSAFE_ISSUE.test(message)) {
    return '问题详情已省略（出于安全原因）'
  }
  return message
}

function adapterText(model: ModelConfigStatus): string {
  return model.adapter_id?.trim() || '未配置'
}
function modelName(model: ModelConfigStatus): string {
  return model.model?.trim() || '未配置'
}
function listText(items: string[] | null): string {
  return items?.length ? items.join('、') : '待配置'
}
function valueText(value: string | null): string {
  return value?.trim() || '待配置'
}
function capabilityText(value: boolean | null): string {
  if (value === true) return '支持'
  if (value === false) return '不支持'
  return '待核实'
}
function bytesText(value: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0) return '待配置'
  return `${value.toLocaleString('zh-CN')} 字节`
}
function visualCapabilities(visual: VisualConfigStatus) {
  return visual.capabilities
}
</script>

<template>
  <AccessibleDrawer
    :open="modelDrawerOpen"
    title="模型状态"
    close-label="关闭模型状态"
    description="只读配置状态；配置完整不代表真实调用成功"
    width="400px"
    @close="closeModelDrawer"
  >
    <div class="config-toolbar">
      <p class="muted">本面板只读取 GET /api/config/status，不会修改配置或探测付费能力。</p>
      <button type="button" class="secondary-button" :disabled="configLoading" aria-label="刷新配置状态" title="刷新配置状态" @click="refreshConfig">
        刷新配置状态
      </button>
      <span v-if="configLoading" class="loading-note" aria-live="off">正在读取配置状态…</span>
    </div>

    <div v-if="configError" class="config-error" role="alert">
      <span class="config-error-icon" aria-hidden="true">!</span>
      <div>
        <strong>配置状态读取失败</strong>
        <p>{{ configError }}</p>
      </div>
    </div>

    <div class="config-body" :aria-busy="configLoading">
      <p v-if="!configStatus && configLoading" class="muted">正在读取配置状态…</p>
      <p v-else-if="!configStatus && !configError" class="muted">暂无配置状态。</p>

      <template v-if="configStatus">
        <section class="config-card" aria-labelledby="llm-heading">
          <div class="config-card-head">
            <h3 id="llm-heading">需求整理模型（LLM）</h3>
            <span class="status-chip" :class="configStatus.llm.configured ? 'is-ok' : 'is-warning'">
              <span aria-hidden="true">{{ configStatus.llm.configured ? '✓' : '!' }}</span>
              {{ configStatus.llm.configured ? '配置完整' : '配置不完整' }}
            </span>
          </div>

          <p class="ready-line">
            <span class="status-chip" :class="configStatus.llm.ready ? 'is-ok' : 'is-warning'">
              <span aria-hidden="true">{{ configStatus.llm.ready ? '✓' : '!' }}</span>
              {{ configStatus.llm.ready ? '已就绪' : '未就绪' }}
            </span>
          </p>

          <p class="verification-note">
            <span aria-hidden="true">○</span>
            调用验证：尚未验证实际调用
          </p>

          <dl class="config-facts">
            <div><dt>适配器</dt><dd>{{ adapterText(configStatus.llm) }}</dd></div>
            <div><dt>模型</dt><dd>{{ modelName(configStatus.llm) }}</dd></div>
            <div><dt>超时</dt><dd>{{ configStatus.llm.timeout_seconds }} 秒</dd></div>
          </dl>

          <div class="config-section">
            <h4>缺失字段</h4>
            <ul v-if="configStatus.llm.missing_fields.length" class="field-list">
              <li v-for="field in configStatus.llm.missing_fields" :key="field"><code>{{ field }}</code></li>
            </ul>
            <p v-else class="muted">无</p>
          </div>

          <div class="config-section">
            <h4>配置问题</h4>
            <ul v-if="configStatus.llm.issues.length" class="issue-list">
              <li v-for="(issue, index) in configStatus.llm.issues" :key="`${issue.code}-${index}`">{{ issueText(issue) }}</li>
            </ul>
            <p v-else class="muted">暂无问题说明</p>
          </div>
        </section>

        <section class="config-card" aria-labelledby="visual-heading">
          <div class="config-card-head">
            <h3 id="visual-heading">图片生成模型（VISUAL）</h3>
            <span class="status-chip" :class="configStatus.visual.configured ? 'is-ok' : 'is-warning'">
              <span aria-hidden="true">{{ configStatus.visual.configured ? '✓' : '!' }}</span>
              {{ configStatus.visual.configured ? '配置完整' : '配置不完整' }}
            </span>
          </div>

          <p class="ready-line">
            <span class="status-chip" :class="configStatus.visual.ready ? 'is-ok' : 'is-warning'">
              <span aria-hidden="true">{{ configStatus.visual.ready ? '✓' : '!' }}</span>
              {{ configStatus.visual.ready ? '已就绪' : '未就绪' }}
            </span>
          </p>

          <p class="verification-note">
            <span aria-hidden="true">○</span>
            调用验证：尚未验证实际调用
          </p>

          <dl class="config-facts">
            <div><dt>适配器</dt><dd>{{ adapterText(configStatus.visual) }}</dd></div>
            <div><dt>模型</dt><dd>{{ modelName(configStatus.visual) }}</dd></div>
            <div><dt>超时</dt><dd>{{ configStatus.visual.timeout_seconds }} 秒</dd></div>
          </dl>

          <div class="config-section">
            <h4>能力</h4>
            <dl class="capability-list">
              <div><dt>文生图</dt><dd>{{ capabilityText(visualCapabilities(configStatus.visual).text_to_image) }}</dd></div>
              <div><dt>参考图</dt><dd>{{ capabilityText(visualCapabilities(configStatus.visual).reference_image) }}</dd></div>
              <div><dt>异步生成</dt><dd>{{ capabilityText(visualCapabilities(configStatus.visual).asynchronous) }}</dd></div>
            </dl>
          </div>

          <div class="config-section">
            <h4>图片规格与参考图限制</h4>
            <dl class="capability-list">
              <div><dt>支持尺寸</dt><dd>{{ listText(visualCapabilities(configStatus.visual).supported_image_sizes) }}</dd></div>
              <div><dt>当前尺寸</dt><dd>{{ valueText(visualCapabilities(configStatus.visual).selected_image_size) }}</dd></div>
              <div><dt>参考图格式</dt><dd>{{ listText(visualCapabilities(configStatus.visual).reference_mime_types) }}</dd></div>
              <div><dt>供应商参考图上限</dt><dd>{{ bytesText(visualCapabilities(configStatus.visual).provider_max_reference_bytes) }}</dd></div>
              <div><dt>实际参考图上限</dt><dd>{{ bytesText(visualCapabilities(configStatus.visual).effective_reference_max_bytes) }}</dd></div>
            </dl>
          </div>

          <div class="config-section">
            <h4>缺失字段</h4>
            <ul v-if="configStatus.visual.missing_fields.length" class="field-list">
              <li v-for="field in configStatus.visual.missing_fields" :key="field"><code>{{ field }}</code></li>
            </ul>
            <p v-else class="muted">无</p>
          </div>

          <div class="config-section">
            <h4>配置问题</h4>
            <ul v-if="configStatus.visual.issues.length" class="issue-list">
              <li v-for="(issue, index) in configStatus.visual.issues" :key="`${issue.code}-${index}`">{{ issueText(issue) }}</li>
            </ul>
            <p v-else class="muted">暂无问题说明</p>
          </div>
        </section>
      </template>
    </div>
  </AccessibleDrawer>
</template>
