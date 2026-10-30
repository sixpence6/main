<script setup lang="ts">
import { computed } from 'vue'
import AccessibleDrawer from './AccessibleDrawer.vue'
import { useWorkspace } from '@/composables/useWorkspace'
import type {
  ApiIssue,
  CapabilityName,
  CapabilityStatus,
  ConfigCapabilities,
  GenerationGate,
  ImageGenerationConfigStatus,
  ModelConfigStatus,
  VisionConfigStatus,
  VisualConfigStatus,
} from '@/api/types'

const workspace = useWorkspace()
const { modelDrawerOpen, closeModelDrawer, configStatus, configError, configLoading, healthStatus, refreshConfig } = workspace

const UNSAFE_ISSUE = /(sk-[a-z0-9]|bearer\s|api[_-]?key\s*[:=]|https?:\/\/|traceback|\.py\b|\bat\s+\S+\s*\(|<script)/i
const CAPABILITY_LABELS: Record<CapabilityName, string> = {
  TEXT: '文字处理（TEXT）',
  VISION: '视觉理解（VISION）',
  IMAGE_GEN: '图片生成（IMAGE_GEN）',
}
const DECLARED_LABELS: Record<string, string> = {
  text: '文本',
  image_input: '图片输入',
  text_to_image: '文生图',
  reference_image: '参考图',
  asynchronous: '异步生成',
}

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
function listText(items: string[] | null | undefined): string {
  return items?.length ? items.join('、') : '待配置'
}
function valueText(value: string | null | undefined): string {
  return value?.trim() || '待配置'
}
function fingerprintText(value: string | null | undefined): string {
  return value?.trim() || '待核实'
}
function capabilityText(value: boolean | null): string {
  if (value === true) return '支持'
  if (value === false) return '不支持'
  return '待核实'
}
function bytesText(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0) return '待配置'
  return `${value.toLocaleString('zh-CN')} 字节`
}
function sourceText(source: VisionConfigStatus['endpoint_source'], kind: '地址' | '凭据'): string {
  if (source === 'shared_text') return `共享 TEXT ${kind}`
  if (source === 'independent') return `独立配置${kind}`
  return '待配置'
}
function declaredText(items: string[]): string {
  if (!items.length) return '待声明'
  return items.map((item) => DECLARED_LABELS[item] ?? item).join('、')
}
function emptyCapabilities(): ConfigCapabilities {
  return {
    text_to_image: null,
    reference_image: null,
    asynchronous: null,
    supported_image_sizes: null,
    selected_image_size: null,
    reference_mime_types: null,
    provider_max_reference_bytes: null,
    effective_reference_max_bytes: 10_000_000,
    local_reference_max_bytes: 10_000_000,
    effective_reference_mime_types: null,
    usable_image_size: null,
  }
}
function normalizeCapabilities(value: ConfigCapabilities | null | undefined): ConfigCapabilities {
  return { ...emptyCapabilities(), ...(value ?? {}) }
}
function actualReferenceFormats(caps: ConfigCapabilities): string[] | null {
  return caps.effective_reference_mime_types ?? caps.reference_mime_types
}
function declaredFormatsDiffer(caps: ConfigCapabilities): boolean {
  if (!caps.reference_mime_types?.length || !caps.effective_reference_mime_types?.length) return false
  return caps.reference_mime_types.join('|') !== caps.effective_reference_mime_types.join('|')
}
function currentSizeLabel(caps: ConfigCapabilities): string {
  if (caps.usable_image_size && caps.usable_image_size === caps.selected_image_size) {
    return '当前尺寸（已核验方形）'
  }
  return '当前尺寸'
}
function blankCapability(capability: CapabilityName, timeoutSeconds: number): CapabilityStatus {
  return {
    capability,
    configured: false,
    ready: false,
    adapter_id: null,
    model: null,
    missing_fields: [],
    issues: [],
    timeout_seconds: timeoutSeconds,
    verification: 'not_checked',
    model_fingerprint: null,
    declared_capabilities: [],
  }
}

const textConfig = computed<CapabilityStatus>(() => {
  const status = configStatus.value
  if (status?.text) return status.text
  if (status?.llm) {
    return {
      ...status.llm,
      capability: 'TEXT',
      model_fingerprint: null,
      declared_capabilities: [],
    }
  }
  return blankCapability('TEXT', 60)
})

const visionConfig = computed<VisionConfigStatus>(() => {
  const status = configStatus.value
  if (status?.vision) return status.vision
  return {
    ...blankCapability('VISION', status?.llm?.timeout_seconds ?? 60),
    missing_fields: ['vision_model'],
    endpoint_source: 'missing',
    credential_source: 'missing',
  }
})

const imageConfig = computed<ImageGenerationConfigStatus>(() => {
  const status = configStatus.value
  if (status?.image_generation) return status.image_generation
  const visual = status?.visual
  return {
    ...(visual ?? blankCapability('IMAGE_GEN', 300)),
    capability: 'IMAGE_GEN',
    timeout_seconds: visual?.timeout_seconds ?? 300,
    model_fingerprint: null,
    declared_capabilities: [],
    capabilities: normalizeCapabilities(visual?.capabilities),
  }
})

const imageCapabilities = computed(() => normalizeCapabilities(imageConfig.value.capabilities))

const generation = computed<GenerationGate>(() => {
  return configStatus.value?.generation ?? {
    allowed: false,
    missing_capabilities: ['TEXT', 'VISION', 'IMAGE_GEN'],
    issues: [],
    verification: 'not_checked',
  }
})
const gateMissingText = computed(() => {
  const labels = generation.value.missing_capabilities.map(
    (capability) => CAPABILITY_LABELS[capability] ?? capability,
  )
  return labels.length ? labels.join('、') : '待核实'
})
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
      <span class="health-note" :class="healthStatus === 'error' ? 'is-warning' : ''">
        服务健康检查：{{ healthStatus === 'ok' ? '正常' : healthStatus === 'error' ? '不可用' : '尚未完成' }}
      </span>
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
        <div
          class="generation-gate"
          :class="generation.allowed ? 'is-ok' : 'is-blocked'"
          role="status"
          aria-live="polite"
          aria-label="付费生成门禁"
        >
          <strong>付费生成门禁：{{ generation.allowed ? '允许' : '禁止' }}</strong>
          <p v-if="!generation.allowed">缺少能力：{{ gateMissingText }}</p>
          <p v-if="!generation.allowed">修复配置并完成能力声明前，不会发起任何模型调用。</p>
          <p v-else>三种能力均已配置并通过声明检查；仍不代表真实调用成功。</p>
        </div>

        <section class="config-card" aria-labelledby="llm-heading">
          <div class="config-card-head">
            <h3 id="llm-heading">需求整理模型（LLM）</h3>
            <span class="status-chip" :class="textConfig.configured ? 'is-ok' : 'is-warning'">
              <span aria-hidden="true">{{ textConfig.configured ? '✓' : '!' }}</span>
              {{ textConfig.configured ? '配置完整' : '配置不完整' }}
            </span>
          </div>
          <p class="capability-kicker">能力：TEXT（文字处理）</p>

          <p class="ready-line">
            <span class="status-chip" :class="textConfig.ready ? 'is-ok' : 'is-warning'">
              <span aria-hidden="true">{{ textConfig.ready ? '✓' : '!' }}</span>
              {{ textConfig.ready ? '已就绪' : '未就绪' }}
            </span>
          </p>

          <p class="verification-note">
            <span aria-hidden="true">○</span>
            调用验证：尚未验证实际调用
          </p>

          <dl class="config-facts">
            <div><dt>适配器</dt><dd>{{ adapterText(textConfig) }}</dd></div>
            <div><dt>模型</dt><dd>{{ modelName(textConfig) }}</dd></div>
            <div><dt>单次调用上限</dt><dd>{{ textConfig.timeout_seconds }} 秒</dd></div>
            <div><dt>模型指纹</dt><dd>{{ fingerprintText(textConfig.model_fingerprint) }}</dd></div>
            <div><dt>声明能力</dt><dd>{{ declaredText(textConfig.declared_capabilities) }}</dd></div>
          </dl>

          <div class="config-section">
            <h4>缺失字段</h4>
            <ul v-if="textConfig.missing_fields.length" class="field-list">
              <li v-for="field in textConfig.missing_fields" :key="field"><code>{{ field }}</code></li>
            </ul>
            <p v-else class="muted">无</p>
          </div>

          <div class="config-section">
            <h4>配置问题</h4>
            <ul v-if="textConfig.issues.length" class="issue-list">
              <li v-for="(issue, index) in textConfig.issues" :key="`${issue.code}-${index}`">{{ issueText(issue) }}</li>
            </ul>
            <p v-else class="muted">暂无问题说明</p>
          </div>
        </section>

        <section class="config-card" aria-labelledby="vision-heading">
          <div class="config-card-head">
            <h3 id="vision-heading">视觉理解模型（VISION）</h3>
            <span class="status-chip" :class="visionConfig.configured ? 'is-ok' : 'is-warning'">
              <span aria-hidden="true">{{ visionConfig.configured ? '✓' : '!' }}</span>
              {{ visionConfig.configured ? '配置完整' : '配置不完整' }}
            </span>
          </div>
          <p class="capability-kicker">能力：VISION（视觉理解）</p>

          <p class="ready-line">
            <span class="status-chip" :class="visionConfig.ready ? 'is-ok' : 'is-warning'">
              <span aria-hidden="true">{{ visionConfig.ready ? '✓' : '!' }}</span>
              {{ visionConfig.ready ? '已就绪' : '未就绪' }}
            </span>
          </p>

          <p class="verification-note">
            <span aria-hidden="true">○</span>
            调用验证：尚未验证实际调用
          </p>

          <dl class="config-facts">
            <div><dt>适配器</dt><dd>{{ adapterText(visionConfig) }}</dd></div>
            <div><dt>模型</dt><dd>{{ modelName(visionConfig) }}</dd></div>
            <div><dt>单次调用上限</dt><dd>{{ visionConfig.timeout_seconds }} 秒</dd></div>
            <div><dt>模型指纹</dt><dd>{{ fingerprintText(visionConfig.model_fingerprint) }}</dd></div>
            <div><dt>声明能力</dt><dd>{{ declaredText(visionConfig.declared_capabilities) }}</dd></div>
          </dl>

          <div class="config-section">
            <h4>配置来源</h4>
            <dl class="capability-list">
              <div><dt>地址</dt><dd>{{ sourceText(visionConfig.endpoint_source, '地址') }}</dd></div>
              <div><dt>凭据</dt><dd>{{ sourceText(visionConfig.credential_source, '凭据') }}</dd></div>
            </dl>
          </div>

          <div class="config-section">
            <h4>缺失字段</h4>
            <ul v-if="visionConfig.missing_fields.length" class="field-list">
              <li v-for="field in visionConfig.missing_fields" :key="field"><code>{{ field }}</code></li>
            </ul>
            <p v-else class="muted">无</p>
          </div>

          <div class="config-section">
            <h4>配置问题</h4>
            <ul v-if="visionConfig.issues.length" class="issue-list">
              <li v-for="(issue, index) in visionConfig.issues" :key="`${issue.code}-${index}`">{{ issueText(issue) }}</li>
            </ul>
            <p v-else class="muted">暂无问题说明</p>
          </div>
        </section>

        <section class="config-card" aria-labelledby="visual-heading">
          <div class="config-card-head">
            <h3 id="visual-heading">图片生成模型（VISUAL）</h3>
            <span class="status-chip" :class="imageConfig.configured ? 'is-ok' : 'is-warning'">
              <span aria-hidden="true">{{ imageConfig.configured ? '✓' : '!' }}</span>
              {{ imageConfig.configured ? '配置完整' : '配置不完整' }}
            </span>
          </div>
          <p class="capability-kicker">能力：IMAGE_GEN（图片生成）</p>

          <p class="ready-line">
            <span class="status-chip" :class="imageConfig.ready ? 'is-ok' : 'is-warning'">
              <span aria-hidden="true">{{ imageConfig.ready ? '✓' : '!' }}</span>
              {{ imageConfig.ready ? '已就绪' : '未就绪' }}
            </span>
          </p>

          <p class="verification-note">
            <span aria-hidden="true">○</span>
            调用验证：尚未验证实际调用
          </p>

          <dl class="config-facts">
            <div><dt>适配器</dt><dd>{{ adapterText(imageConfig) }}</dd></div>
            <div><dt>模型</dt><dd>{{ modelName(imageConfig) }}</dd></div>
            <div><dt>生成等待上限</dt><dd>{{ imageConfig.timeout_seconds }} 秒</dd></div>
            <div><dt>模型指纹</dt><dd>{{ fingerprintText(imageConfig.model_fingerprint) }}</dd></div>
            <div><dt>声明能力</dt><dd>{{ declaredText(imageConfig.declared_capabilities) }}</dd></div>
          </dl>

          <div class="config-section">
            <h4>能力</h4>
            <dl class="capability-list">
              <div><dt>文生图</dt><dd>{{ capabilityText(imageCapabilities.text_to_image) }}</dd></div>
              <div><dt>参考图</dt><dd>{{ capabilityText(imageCapabilities.reference_image) }}</dd></div>
              <div><dt>异步生成</dt><dd>{{ capabilityText(imageCapabilities.asynchronous) }}</dd></div>
            </dl>
          </div>

          <div class="config-section">
            <h4>图片规格与参考图限制</h4>
            <dl class="capability-list">
              <div><dt>支持尺寸</dt><dd>{{ listText(imageCapabilities.supported_image_sizes) }}</dd></div>
              <div><dt>{{ currentSizeLabel(imageCapabilities) }}</dt><dd>{{ valueText(imageCapabilities.selected_image_size) }}</dd></div>
              <div v-if="imageCapabilities.usable_image_size && imageCapabilities.usable_image_size !== imageCapabilities.selected_image_size">
                <dt>可用方形尺寸</dt><dd>{{ valueText(imageCapabilities.usable_image_size) }}</dd>
              </div>
              <div><dt>实际参考图格式</dt><dd>{{ listText(actualReferenceFormats(imageCapabilities)) }}</dd></div>
              <div v-if="declaredFormatsDiffer(imageCapabilities)">
                <dt>适配器声明格式</dt><dd>{{ listText(imageCapabilities.reference_mime_types) }}</dd>
              </div>
              <div><dt>供应商参考图上限</dt><dd>{{ bytesText(imageCapabilities.provider_max_reference_bytes) }}</dd></div>
              <div><dt>本地参考图上限</dt><dd>{{ bytesText(imageCapabilities.local_reference_max_bytes) }}</dd></div>
              <div><dt>实际参考图上限</dt><dd>{{ bytesText(imageCapabilities.effective_reference_max_bytes) }}</dd></div>
            </dl>
          </div>

          <div class="config-section">
            <h4>缺失字段</h4>
            <ul v-if="imageConfig.missing_fields.length" class="field-list">
              <li v-for="field in imageConfig.missing_fields" :key="field"><code>{{ field }}</code></li>
            </ul>
            <p v-else class="muted">无</p>
          </div>

          <div class="config-section">
            <h4>配置问题</h4>
            <ul v-if="imageConfig.issues.length" class="issue-list">
              <li v-for="(issue, index) in imageConfig.issues" :key="`${issue.code}-${index}`">{{ issueText(issue) }}</li>
            </ul>
            <p v-else class="muted">暂无问题说明</p>
          </div>
        </section>
      </template>
    </div>
  </AccessibleDrawer>
</template>
