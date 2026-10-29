<script setup lang="ts">
import { computed, useId } from 'vue'
import type { AssetView, Brief } from '@/api/types'
import { assetUrl } from '@/api/client'
import { briefFieldValue, editableBriefFields, formatByteSize } from '@/utils/format'

const props = defineProps<{
  brief: Brief
  heading: string
  snapshot?: boolean
  referenceAsset?: AssetView | null
}>()

const headingId = useId()
const fields = computed(() => {
  if (!props.snapshot) {
    return editableBriefFields.map((field) => ({ ...field, value: briefFieldValue(props.brief, field.key) }))
  }
  return editableBriefFields
    .filter((field) => Boolean(props.brief[field.key]?.trim()))
    .map((field) => ({ ...field, value: props.brief[field.key] }))
})
const prompt = computed(() => props.brief.prompt?.trim() ?? '')
const reference = computed(() => props.referenceAsset ?? null)
const referenceId = computed(() => props.referenceAsset?.id ?? props.brief.reference_asset_id)
</script>

<template>
  <section class="brief-card" :aria-labelledby="headingId">
    <h3 :id="headingId">{{ heading }}</h3>
    <dl class="brief-fields">
      <div v-for="field in fields" :key="field.key" class="brief-field">
        <dt>{{ field.label }}</dt>
        <dd>{{ field.value }}</dd>
      </div>
      <div class="brief-field">
        <dt>图片尺寸</dt>
        <dd>{{ brief.image_size || '待配置' }}</dd>
      </div>
    </dl>
    <div class="brief-field">
      <h4>完整提示词</h4>
      <p class="prompt-text">{{ prompt || '尚未生成完整提示词' }}</p>
    </div>
    <div v-if="referenceId" class="brief-field reference-block">
      <h4>参考图</h4>
      <template v-if="reference">
        <dl class="reference-facts">
          <div><dt>类型</dt><dd>{{ reference.mime_type }}</dd></div>
          <div><dt>尺寸</dt><dd>{{ reference.width }} × {{ reference.height }}</dd></div>
          <div><dt>大小</dt><dd>{{ formatByteSize(reference.byte_size) }}</dd></div>
          <div>
            <dt>可用性</dt>
            <dd>
              <span v-if="reference.availability === 'missing'" class="status-warning">参考图文件缺失</span>
              <span v-else>文件可用</span>
            </dd>
          </div>
        </dl>
        <img
          v-if="reference.availability === 'available'"
          :src="assetUrl(reference.id)"
          alt="参考图"
          class="reference-thumbnail"
        />
      </template>
      <p v-else class="muted">参考资源记录不可用（{{ referenceId }}）。</p>
    </div>
    <div v-else-if="!snapshot" class="brief-field reference-block">
      <h4>参考图</h4>
      <p class="muted">暂无参考图。</p>
    </div>
  </section>
</template>
