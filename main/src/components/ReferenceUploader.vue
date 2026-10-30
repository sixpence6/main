<script setup lang="ts">
import { computed, ref } from 'vue'
import { assetUrl } from '@/api/client'
import { useWorkspace } from '@/composables/useWorkspace'
import { formatByteSize } from '@/utils/format'

const workspace = useWorkspace()
const {
  currentProject,
  referencePending,
  referenceError,
  referenceNotice,
  referenceBusy,
  referenceConflict,
  referenceCapability,
  referenceLimitBytes,
  referenceMimeTypes,
  referenceUploadDisabledReason,
  referenceRemoveDisabledReason,
  chooseReferenceFiles,
  retryReferenceUpload,
  removeReference,
  reloadLatestReference,
} = workspace

const fileInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)

const currentReference = computed(() => currentProject.value?.reference_asset ?? null)
const hasCurrent = computed(() => Boolean(currentReference.value))
const pickingDisabled = computed(() => Boolean(referenceUploadDisabledReason.value))
const pickLabel = computed(() => (hasCurrent.value ? '替换参考图' : '选择参考图'))
const capabilityText = computed(() => {
  if (referenceCapability.value === true) {
    return '参考图能力已核实：当前模型支持参考图。'
  }
  if (referenceCapability.value === false) {
    return '当前模型明确不支持参考图；已有参考图时可移除后继续纯文字流程。'
  }
  return '参考图能力待核实；带参考图时不能生成图片。'
})
const formatText = computed(() =>
  referenceMimeTypes.value.map((mime) => (mime === 'image/png' ? 'PNG' : 'JPEG')).join(' 或 '),
)
const limitText = computed(
  () => '单张 ' + formatText.value + '，最多 ' + formatByteSize(referenceLimitBytes.value) + '。',
)
const uploading = computed(
  () => referencePending.value?.status === 'uploading' &&
    (workspace.referenceAction.value === 'upload' || referenceBusy.value),
)
const retryReason = computed(() => referenceUploadDisabledReason.value)

function openPicker(): void {
  if (pickingDisabled.value) return
  fileInput.value?.click()
}

function acceptFiles(files: File[]): void {
  if (pickingDisabled.value) return
  void chooseReferenceFiles(files)
}

function onChange(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = input.files ? Array.from(input.files) : []
  input.value = ''
  acceptFiles(files)
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  const files = event.dataTransfer?.files ? Array.from(event.dataTransfer.files) : []
  acceptFiles(files)
}

function onDragOver(): void {
  if (!pickingDisabled.value) dragging.value = true
}

function onDragLeave(): void {
  dragging.value = false
}
</script>

<template>
  <section class="reference-uploader" aria-labelledby="reference-uploader-title">
    <div class="reference-uploader-heading">
      <h3 id="reference-uploader-title">参考图</h3>
      <span class="reference-capability">{{ capabilityText }}</span>
    </div>
    <p class="reference-help">{{ limitText }} 不接受粘贴上传。</p>

    <div
      class="reference-dropzone"
      :class="{ 'is-dragging': dragging, 'is-disabled': pickingDisabled }"
      aria-label="拖放参考图到此处"
      @dragover.prevent="onDragOver"
      @dragenter.prevent="onDragOver"
      @dragleave.prevent="onDragLeave"
      @drop.prevent="onDrop"
    >
      <p>拖放一张参考图到这里，或使用按钮选择。</p>
      <label class="sr-only" for="reference-file-input">选择参考图文件</label>
      <input
        id="reference-file-input"
        ref="fileInput"
        class="sr-only"
        type="file"
        accept="image/png,image/jpeg"
        aria-label="选择参考图文件"
        :disabled="pickingDisabled"
        @change="onChange"
      />
      <button
        type="button"
        class="secondary-button"
        :disabled="pickingDisabled"
        @click="openPicker"
      >
        {{ pickLabel }}
      </button>
      <p v-if="referenceUploadDisabledReason" class="control-reason">{{ referenceUploadDisabledReason }}</p>
    </div>

    <div v-if="referencePending" class="reference-pending">
      <p class="reference-pending-badge">待上传</p>
      <p>
        {{ referencePending.name }} · {{ formatByteSize(referencePending.byteSize) }} ·
        {{ referencePending.width }} × {{ referencePending.height }}
      </p>
      <img
        v-if="referencePending.previewUrl"
        :src="referencePending.previewUrl"
        alt="待上传的参考图预览"
        class="reference-pending-preview"
      />
      <p v-if="uploading" class="status-notice" role="status">正在上传参考图…</p>
      <template v-else>
        <p class="status-warning">上传失败，尚未成为当前参考图。</p>
        <button
          type="button"
          class="secondary-button"
          :disabled="Boolean(retryReason)"
          @click="retryReferenceUpload"
        >
          重试上传
        </button>
        <p v-if="retryReason" class="control-reason">{{ retryReason }}</p>
      </template>
    </div>

    <div v-if="hasCurrent" class="reference-current-actions">
      <p>当前参考图已关联。替换或移除会使用当前项目修订。</p>
      <span v-if="currentReference?.availability === 'missing'" class="status-warning">参考图文件缺失</span>
      <img
        v-else-if="currentReference?.availability === 'available'"
        :src="assetUrl(currentReference.id)"
        alt="参考图"
        class="reference-thumbnail"
      />
      <button
        type="button"
        class="secondary-button"
        :disabled="Boolean(referenceRemoveDisabledReason)"
        @click="removeReference"
      >
        移除参考图
      </button>
      <p v-if="referenceRemoveDisabledReason" class="control-reason">{{ referenceRemoveDisabledReason }}</p>
    </div>

    <p v-if="referenceError" class="reference-error" role="alert">{{ referenceError }}</p>
    <p v-if="referenceNotice" class="status-notice" role="status">{{ referenceNotice }}</p>
    <p v-if="referenceConflict" class="reference-error" role="alert">
      <span>参考图已在别处更新，请加载最新项目后再操作。</span>
      <button
        type="button"
        class="secondary-button"
        :disabled="referenceBusy"
        @click="reloadLatestReference"
      >
        加载最新项目
      </button>
    </p>
  </section>
</template>
