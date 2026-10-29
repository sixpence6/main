<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { assetUrl } from '@/api/client'
import { useWorkspace } from '@/composables/useWorkspace'
import { extensionForMime, formatDateTime, generationStatusLabel } from '@/utils/format'

const workspace = useWorkspace()
const {
  latestSucceededGeneration,
  latestGenerationError,
  generationSubmitNotice,
  setStage,
} = workspace

const selectedGeneration = computed(() => latestSucceededGeneration.value)
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
        第 {{ selectedGeneration.version_no }} 版 · {{ generationStatusLabel(selectedGeneration.status) }} · {{ resultTime }}
      </p>
      <p v-if="latestGenerationError" class="generation-latest-error" role="alert">
        {{ latestGenerationError }}
      </p>
      <p v-if="generationSubmitNotice" class="status-notice" role="status">{{ generationSubmitNotice }}</p>
    </header>

    <div v-if="!selectedGeneration" class="result-empty">
      <span class="result-empty-icon" aria-hidden="true">◇</span>
      <p>尚未生成图片。</p>
    </div>

    <template v-else>
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

      <div class="result-footer-actions">
        <button type="button" class="primary-button" @click="returnToConversation">继续编辑</button>
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
