<script setup lang="ts">
import { computed, ref } from 'vue'
import { assetUrl } from '@/api/client'
import type { WorkflowAction, WorkflowArtifact } from '@/api/workflow'
import { useWorkflow } from '@/composables/useWorkflow'
import { useWorkspace } from '@/composables/useWorkspace'
import { LEGACY_UNREVIEWED_NOTICE } from '@/utils/format'

const { currentProject } = useWorkspace()
const projectId = computed(() => currentProject.value?.id ?? null)
const { view, loading, error, actionBusy, refresh, runAction } = useWorkflow(projectId)

const actionText = ref<Record<string, string>>({})
const actionConfirmed = ref<Record<string, boolean>>({})
const actionError = ref('')

const STAGE_LABELS: Record<string, string> = {
  S01: '判断请求范围',
  S02: '分析参考图',
  S03: '整理需求',
  S04: '策划创意',
  S05: '制定视觉方案',
  S06: '审查需求卡',
  S07: '编制生图提示词',
  S08: '生成图片',
  S09: '检查图片',
  S10: '审查输出',
  S11: '准备交付',
  S12: '恢复流程',
}

const STATUS_LABELS: Record<string, string> = {
  none: '尚未开始',
  active: '处理中',
  needs_input: '等待补充',
  blocked: '等待处理',
  completed: '已完成',
  failed: '已暂停',
}

const LEGACY_KIND_LABELS: Record<string, string> = {
  message: '历史消息',
  generation: '历史生成记录',
  asset: '历史图片',
}

const ARTIFACT_LABELS: Record<string, string> = {
  reference_report: '参考图分析',
  brief_draft: '需求草稿',
  creative_direction: '创意方向',
  visual_plan: '视觉方案',
  final_brief: '最终需求卡',
  prompt_plan: '生图提示词',
  quality_report: '图片检查',
  delivery_manifest: '交付清单',
}

const visible = computed(() => Boolean(view.value))
const stageLabel = computed(() =>
  view.value?.stage ? STAGE_LABELS[view.value.stage] ?? view.value.stage : '',
)
const statusLabel = computed(() =>
  view.value ? STATUS_LABELS[view.value.status] ?? view.value.status : '',
)
const approvedArtifacts = computed<WorkflowArtifact[]>(() => {
  if (!view.value) return []
  const latest = new Map<string, WorkflowArtifact>()
  for (const artifact of view.value.artifacts) {
    if (artifact.status !== 'approved' || !artifact.payload) continue
    const current = latest.get(artifact.kind)
    if (!current || current.revision_no < artifact.revision_no) latest.set(artifact.kind, artifact)
  }
  return [...latest.values()]
})

function artifactLabel(kind: string): string {
  return ARTIFACT_LABELS[kind] ?? kind
}

function legacyKindLabel(kind: string): string {
  return LEGACY_KIND_LABELS[kind] ?? '历史内容'
}

function payloadLines(payload: Record<string, unknown>): string[] {
  return Object.entries(payload).map(([key, value]) => {
    const text = typeof value === 'string' ? value : JSON.stringify(value)
    return `${key}: ${text}`
  })
}

function canSubmit(action: WorkflowAction): boolean {
  if (actionBusy.value) return false
  if (action.requires_text && !(actionText.value[action.id] ?? '').trim()) return false
  if (action.requires_confirmation && !actionConfirmed.value[action.id]) return false
  return true
}

async function submit(action: WorkflowAction): Promise<void> {
  actionError.value = ''
  if (!canSubmit(action)) {
    actionError.value = action.requires_text ? '请先填写说明。' : '请先勾选确认。'
    return
  }
  const text = action.requires_text ? (actionText.value[action.id] ?? '').trim() : null
  const payload: Record<string, unknown> = {}
  const ok = await runAction(action, text, payload)
  if (ok) {
    delete actionText.value[action.id]
    delete actionConfirmed.value[action.id]
  }
}
</script>

<template>
  <section v-if="visible" class="workflow-panel" aria-labelledby="workflow-panel-title">
    <header class="workflow-panel-header">
      <div>
        <p class="eyebrow">流程状态</p>
        <h3 id="workflow-panel-title">{{ stageLabel || '当前流程' }}</h3>
      </div>
      <div class="workflow-panel-status">
        <span class="status-badge">{{ statusLabel }}</span>
        <button type="button" class="text-button" :disabled="loading" @click="refresh">
          刷新
        </button>
      </div>
    </header>

    <p v-if="loading" class="workflow-loading" role="status">正在读取流程状态…</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>

    <div v-if="view?.issue" class="workflow-issue" role="status">
      <strong>{{ view.issue.message }}</strong>
    </div>

    <div v-if="view?.available_actions.length" class="workflow-actions">
      <div v-for="action in view.available_actions" :key="action.id" class="workflow-action">
        <label v-if="action.requires_text" :for="`workflow-${action.id}`">
          {{ action.text_label || action.label }}
        </label>
        <textarea
          v-if="action.requires_text"
          :id="`workflow-${action.id}`"
          v-model="actionText[action.id]"
          rows="2"
          :disabled="actionBusy"
        />
        <label v-if="action.requires_confirmation" class="workflow-confirm">
          <input v-model="actionConfirmed[action.id]" type="checkbox" :disabled="actionBusy" />
          我已核对当前内容并确认继续
        </label>
        <button
          type="button"
          class="primary-button"
          :disabled="!canSubmit(action)"
          @click="submit(action)"
        >
          {{ actionBusy ? '处理中…' : action.label }}
        </button>
      </div>
      <p v-if="actionError" class="form-error" role="alert">{{ actionError }}</p>
    </div>

    <div v-if="approvedArtifacts.length" class="workflow-artifacts">
      <article v-for="artifact in approvedArtifacts" :key="artifact.id" class="workflow-card">
        <header>
          <h4>{{ artifactLabel(artifact.kind) }}</h4>
          <span>第 {{ artifact.revision_no }} 次 · 已审查</span>
        </header>
        <ul>
          <li v-for="line in payloadLines(artifact.payload ?? {})" :key="line">{{ line }}</li>
        </ul>
      </article>
    </div>

    <div v-if="view?.generation_job" class="workflow-job">
      <strong>生成任务</strong>
      <span>{{ view.generation_job.status }}</span>
      <p v-if="view.generation_job.error_message" class="form-error" role="alert">
        {{ view.generation_job.error_message }}
      </p>
    </div>

    <div v-if="view?.delivery" class="workflow-delivery">
      <strong>交付：{{ view.delivery.filename }}</strong>
      <p v-if="view.delivery.caption">{{ view.delivery.caption }}</p>
      <p v-if="view.delivery.status !== 'published'" class="muted">
        交付尚未发布，暂不提供查看或下载。
      </p>
      <img
        v-else
        class="workflow-delivery-image"
        :src="assetUrl(view.delivery.asset_id)"
        :alt="view.delivery.caption || '已发布交付图片'"
      />
    </div>

    <div v-if="view?.legacy.length" class="workflow-legacy">
      <h4>历史内容</h4>
      <ul>
        <li v-for="item in view.legacy" :key="item.id">
          <span>{{ legacyKindLabel(item.kind) }}</span>
          <span>{{ item.status === 'legacy_unreviewed' ? '尚未通过 v2 审查' : '已补审' }}</span>
          <span v-if="item.status === 'legacy_unreviewed'" class="muted">
            {{ LEGACY_UNREVIEWED_NOTICE }}
          </span>
        </li>
      </ul>
    </div>
  </section>
</template>
