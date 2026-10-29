<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { assetUrl } from '@/api/client'
import type { GenerationView } from '@/api/types'
import { useWorkspace } from '@/composables/useWorkspace'
import {
  briefFieldValue,
  briefStatusLabel,
  extensionForMime,
  formatDateTime,
  generationStatusLabel,
  hasSnapshotContent,
  snapshotFields,
} from '@/utils/format'

const workspace = useWorkspace()
const {
  currentProject,
  latestGenerationView,
  latestGenerationError,
  generationSubmitNotice,
  succeededGenerations,
  selectedResultGeneration,
  selectResultGeneration,
  setStage,
} = workspace

const selectedGeneration = computed(() => selectedResultGeneration.value)
const resultAsset = computed(() => selectedGeneration.value?.result_asset ?? null)
const hasResult = computed(
  () => Boolean(resultAsset.value && resultAsset.value.availability === 'available'),
)
const downloadName = computed(() => {
  const generation = selectedGeneration.value
  const asset = resultAsset.value
  if (!generation || !asset) return ''
  return `project-${generation.project_id}-v${generation.version_no}.${extensionForMime(asset.mime_type)}`
})
const resultTime = computed(() => {
  const generation = selectedGeneration.value
  if (!generation) return ''
  return formatDateTime(generation.completed_at ?? generation.created_at)
})
const currentBrief = computed(() => currentProject.value?.brief ?? null)
const selectedSnapshotFields = computed(() => snapshotFields(selectedGeneration.value?.brief_snapshot ?? null))
const selectedHasSnapshot = computed(() => hasSnapshotContent(selectedGeneration.value?.brief_snapshot ?? null))

function versionTime(generation: GenerationView): string {
  return formatDateTime(generation.completed_at ?? generation.created_at)
}

function versionLabel(generation: GenerationView): string {
  return `第 ${generation.version_no} 版，${versionTime(generation)}`
}

function selectVersion(id: string): void {
  selectResultGeneration(id)
}

function onVersionKeydown(event: KeyboardEvent, index: number): void {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  const buttons = Array.from(
    (event.currentTarget as HTMLElement).parentElement?.querySelectorAll<HTMLButtonElement>('.result-version-button') ?? [],
  )
  if (!buttons.length) return
  event.preventDefault()
  let nextIndex = index
  if (event.key === 'ArrowLeft') nextIndex = Math.max(0, index - 1)
  if (event.key === 'ArrowRight') nextIndex = Math.min(buttons.length - 1, index + 1)
  if (event.key === 'Home') nextIndex = 0
  if (event.key === 'End') nextIndex = buttons.length - 1
  buttons[nextIndex]?.focus()
}

const viewMode = ref<'fit' | 'original'>('fit')
const viewerOpen = ref(false)
const viewerTrigger = ref<HTMLButtonElement | null>(null)
const viewerLayer = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)

watch(
  () => selectedGeneration.value?.id,
  () => {
    viewMode.value = 'fit'
    viewerOpen.value = false
  },
)

async function openViewer(event: MouseEvent): Promise<void> {
  viewerTrigger.value = event.currentTarget as HTMLButtonElement | null
  viewerOpen.value = true
  await nextTick()
  closeButton.value?.focus()
}

async function closeViewer(): Promise<void> {
  if (!viewerOpen.value) return
  viewerOpen.value = false
  await nextTick()
  viewerTrigger.value?.focus()
}

async function onViewerKeydown(event: KeyboardEvent): Promise<void> {
  if (event.key === 'Escape') {
    event.preventDefault()
    await closeViewer()
    return
  }
  if (event.key !== 'Tab' || !viewerLayer.value) return
  const focusables = Array.from(
    viewerLayer.value.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'),
  ).filter((element) => !element.hasAttribute('disabled'))
  if (!focusables.length) return
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

async function returnToConversation(): Promise<void> {
  setStage('describe')
  await nextTick()
  const prompt = document.getElementById('workspace-prompt')
  if (prompt instanceof HTMLTextAreaElement) prompt.focus()
}
</script>

<template>
  <section class="result-panel result-view-panel" aria-labelledby="result-title">
    <header class="result-header">
      <p class="eyebrow">当前项目</p>
      <h2 id="result-title">查看结果</h2>
      <p v-if="selectedGeneration" class="result-meta">
        正在查看第 {{ selectedGeneration.version_no }} 版 · {{ generationStatusLabel(selectedGeneration.status) }} · {{ resultTime }}
      </p>
      <p v-if="latestGenerationError" class="generation-latest-error" role="alert">
        {{ latestGenerationError }}
      </p>
      <p v-if="generationSubmitNotice" class="status-notice" role="status">{{ generationSubmitNotice }}</p>
    </header>

    <section v-if="currentProject" class="result-current" aria-labelledby="result-current-title">
      <div class="result-current-head">
        <h3 id="result-current-title">当前需求</h3>
        <span class="status-badge">{{ briefStatusLabel(currentProject.brief_status) }}</span>
      </div>
      <dl class="result-current-facts">
        <div>
          <dt>商品描述</dt>
          <dd>{{ briefFieldValue(currentBrief, 'product_description') }}</dd>
        </div>
        <div>
          <dt>图片用途</dt>
          <dd>{{ briefFieldValue(currentBrief, 'purpose') }}</dd>
        </div>
        <div>
          <dt>风格</dt>
          <dd>{{ briefFieldValue(currentBrief, 'style') }}</dd>
        </div>
        <div>
          <dt>构图与场景</dt>
          <dd>{{ briefFieldValue(currentBrief, 'composition') }}</dd>
        </div>
        <div>
          <dt>图片中的文字</dt>
          <dd>{{ briefFieldValue(currentBrief, 'text_content') }}</dd>
        </div>
        <div>
          <dt>图片尺寸</dt>
          <dd>{{ currentBrief?.image_size || '尚未确定' }}</dd>
        </div>
        <div>
          <dt>参考图</dt>
          <dd>{{ currentProject.reference_asset ? '有（1 张）' : '无' }}</dd>
        </div>
        <div>
          <dt>最近任务</dt>
          <dd v-if="latestGenerationView">
            第 {{ latestGenerationView.version_no }} 版 · 状态：{{ generationStatusLabel(latestGenerationView.status) }}
          </dd>
          <dd v-else>暂无生成记录</dd>
        </div>
      </dl>
      <p class="result-current-meta">当前修订：第 {{ currentProject.revision }} 修订</p>
      <p class="result-current-note">浏览历史版本只改变下方预览，不会改变这里的当前需求、参考图或确认状态。</p>
    </section>

    <div v-if="!selectedGeneration" class="result-empty">
      <span class="result-empty-icon" aria-hidden="true">◇</span>
      <p>尚未生成图片。</p>
    </div>

    <template v-else>
      <section class="result-versions" aria-labelledby="result-versions-title">
        <div class="result-versions-head">
          <h3 id="result-versions-title">成功版本</h3>
          <p>共 {{ succeededGenerations.length }} 个成功版本</p>
        </div>
        <div class="result-version-strip" role="group" aria-label="历史成功版本">
          <button
            v-for="(generation, index) in succeededGenerations"
            :key="generation.id"
            type="button"
            class="result-version-button"
            :class="{ 'is-selected': selectedGeneration.id === generation.id }"
            :aria-pressed="selectedGeneration.id === generation.id"
            :aria-label="versionLabel(generation)"
            @click="selectVersion(generation.id)"
            @keydown="onVersionKeydown($event, index)"
          >
            <span class="result-version-thumbnail" aria-hidden="true">
              <img
                v-if="generation.result_asset && generation.result_asset.availability === 'available'"
                :src="assetUrl(generation.result_asset.id)"
                :alt="`第 ${generation.version_no} 版缩略图`"
                loading="lazy"
              />
              <span v-else class="result-version-missing">文件缺失</span>
            </span>
            <span class="result-version-label">第 {{ generation.version_no }} 版</span>
            <time class="result-version-time" :datetime="generation.completed_at ?? generation.created_at">
              {{ versionTime(generation) }}
            </time>
          </button>
        </div>
      </section>

      <div v-if="!hasResult" class="result-delivery-error" role="alert">
        <strong>结果文件缺失</strong>
        <p>生成记录存在，但结果文件不可用，无法预览或下载。请保留错误信息并稍后重新查询。</p>
      </div>

      <template v-else>
        <div class="result-image-frame" :class="'view-' + viewMode">
          <img
            class="result-image"
            :class="'is-' + viewMode"
            :src="resultAsset?.id ? assetUrl(resultAsset.id) : ''"
            alt="生成结果图"
          />
        </div>
        <p class="result-dimensions">
          实际尺寸：{{ resultAsset?.width }} × {{ resultAsset?.height }}
        </p>
        <div class="result-actions">
          <button type="button" class="secondary-button" @click="openViewer">查看大图</button>
          <button
            type="button"
            class="secondary-button"
            :aria-pressed="viewMode === 'fit'"
            @click="viewMode = 'fit'"
          >
            适应窗口
          </button>
          <button
            type="button"
            class="secondary-button"
            :aria-pressed="viewMode === 'original'"
            @click="viewMode = 'original'"
          >
            原始大小
          </button>
          <a
            class="secondary-button result-download"
            :href="resultAsset?.id ? assetUrl(resultAsset.id) + '?download=true' : ''"
            :download="downloadName"
          >
            下载当前图片
          </a>
        </div>
      </template>

      <section class="result-snapshot" aria-labelledby="result-snapshot-title">
        <h3 id="result-snapshot-title">第 {{ selectedGeneration.version_no }} 版需求快照</h3>
        <dl v-if="selectedSnapshotFields.length" class="result-snapshot-facts">
          <div v-for="field in selectedSnapshotFields" :key="field.key">
            <dt>{{ field.label }}</dt>
            <dd>{{ field.value }}</dd>
          </div>
        </dl>
        <p v-else-if="!selectedHasSnapshot" class="muted">
          该版本未保存可还原的需求快照，无法还原当时的需求字段。
        </p>
        <p v-else class="muted">该版本没有可还原的需求字段。</p>
        <label class="result-prompt-label" :for="'result-prompt-' + selectedGeneration.id">该版本提示词</label>
        <textarea
          :id="'result-prompt-' + selectedGeneration.id"
          class="result-prompt-text"
          readonly
          rows="5"
          :value="selectedGeneration.prompt || '该版本未保存完整提示词。'"
        ></textarea>
        <p class="result-snapshot-note">以上内容来自第 {{ selectedGeneration.version_no }} 版的实际生成记录，不会用当前需求补写。</p>
      </section>

      <div class="result-footer-actions">
        <button type="button" class="primary-button" @click="returnToConversation">继续编辑</button>
        <p class="result-feedback-hint">
          继续修改只会回到对话并聚焦输入，不会自动发送消息。新的反馈将基于当前需求；浏览历史版本不会恢复旧需求，也不会改变当前参考图。生成结果不承诺像素级或商品细节完全一致。
        </p>
      </div>
    </template>

    <div
      v-if="viewerOpen && hasResult"
      ref="viewerLayer"
      class="result-viewer-layer"
      role="dialog"
      aria-modal="true"
      aria-label="生成结果大图"
      tabindex="-1"
      @keydown="onViewerKeydown"
    >
      <div class="result-viewer-toolbar">
        <p class="result-viewer-title">
          第 {{ selectedGeneration?.version_no }} 版 · 实际尺寸 {{ resultAsset?.width }} × {{ resultAsset?.height }}
        </p>
        <button ref="closeButton" type="button" class="secondary-button" @click="closeViewer">关闭大图</button>
      </div>
      <div class="result-viewer-scroll">
        <img
          class="result-viewer-image"
          :class="'is-' + viewMode"
          :src="resultAsset?.id ? assetUrl(resultAsset.id) : ''"
          alt="生成结果图大图"
        />
      </div>
    </div>
  </section>
</template>
