<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import PageTemplate from '@/components/PageTemplate.vue'
import ResultRetrievalPanel from '@/components/ResultRetrievalPanel.vue'
import { assetUrl } from '@/api/client'
import type { GenerationView } from '@/api/types'
import { useWorkspace } from '@/composables/useWorkspace'
import {
  displayProjectTitle,
  extensionForMime,
  formatDateTime,
  generationStatusLabel,
} from '@/utils/format'

const {
  currentProject,
  projectLoading,
  projectError,
  projectErrorKind,
  retryProject,
  openProjectDrawer,
  createNewProject,
  latestGenerationView,
  latestGenerationError,
  activeGeneration,
  generationSubmitNotice,
  succeededGenerations,
  selectedResultGeneration,
  selectResultGeneration,
} = useWorkspace()

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
</script>

<template>
  <PageTemplate
    eyebrow="工作台"
    title="查看结果"
    title-id="result-title"
    intro="这里只展示当前生成和历史生成的图片。"
  >
    <div v-if="projectLoading" class="project-loading" role="status">正在读取项目详情…</div>

    <div v-else-if="projectError" class="project-error" role="alert">
      <strong>项目读取失败</strong>
      <p>{{ projectError }}</p>
      <div class="project-error-actions">
        <button
          v-if="projectErrorKind === 'network' || projectErrorKind === 'generic'"
          type="button"
          class="secondary-button"
          @click="retryProject"
        >
          重试
        </button>
        <button type="button" class="secondary-button" @click="openProjectDrawer">打开已有项目</button>
        <button type="button" class="primary-button" @click="createNewProject">新建项目</button>
      </div>
    </div>

    <template v-else-if="currentProject">
      <header class="page-project-heading">
        <div>
          <p class="eyebrow">当前项目</p>
          <h3>{{ displayProjectTitle(currentProject.title) }}</h3>
        </div>
        <span v-if="selectedGeneration" class="status-badge">
          第 {{ selectedGeneration.version_no }} 版 · 状态：{{ generationStatusLabel(selectedGeneration.status) }}
        </span>
      </header>

      <p v-if="latestGenerationError" class="generation-latest-error" role="alert">{{ latestGenerationError }}</p>
      <p v-if="generationSubmitNotice" class="status-notice" role="status">{{ generationSubmitNotice }}</p>
      <p v-if="activeGeneration" class="generation-live" role="status">
        当前生成：第 {{ activeGeneration.version_no }} 版，{{ generationStatusLabel(activeGeneration.status) }}。
      </p>

      <ResultRetrievalPanel />

      <div v-if="!selectedGeneration" class="result-empty">
        <span class="result-empty-icon" aria-hidden="true">◇</span>
        <p v-if="activeGeneration">第 {{ activeGeneration.version_no }} 版正在生成，完成后会显示在这里。</p>
        <p v-else-if="latestGenerationView">最近一次生成状态：{{ generationStatusLabel(latestGenerationView.status) }}。</p>
        <p v-else>尚未生成图片。</p>
      </div>

      <template v-else>
        <section class="result-versions" aria-labelledby="result-versions-title">
          <div class="result-versions-head">
            <h3 id="result-versions-title">生成历史</h3>
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

        <p class="result-meta">
          正在查看第 {{ selectedGeneration.version_no }} 版 · {{ resultTime }}
        </p>

        <div v-if="!hasResult" class="result-delivery-error" role="alert">
          <strong>结果文件缺失</strong>
          <p>生成记录存在，但结果文件不可用，无法预览或下载。</p>
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
    </template>

    <div v-else class="result-empty">
      <p>尚未选择项目，请先在描述需求页创建或选择项目。</p>
    </div>
  </PageTemplate>
</template>
