<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import type { Brief } from '@/api/types'
import { briefFieldValue, editableBriefFields } from '@/utils/format'

const props = defineProps<{
  brief: Brief
  heading: string
}>()

const headingId = useId()
const promptId = useId()
const promptExpanded = ref(true)
const fields = computed(() =>
  editableBriefFields
    .filter((field) => Boolean(props.brief[field.key]?.trim()))
    .map((field) => ({ ...field, value: briefFieldValue(props.brief, field.key) })),
)
const prompt = computed(() => props.brief.prompt?.trim() ?? '')
const promptLong = computed(() => prompt.value.length > 240)
const referenceId = computed(() => props.brief.reference_asset_id)

watch(prompt, () => {
  promptExpanded.value = true
})
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

    <div class="brief-field prompt-block">
      <div class="prompt-heading">
        <h4>完整提示词</h4>
        <button
          v-if="promptLong"
          type="button"
          class="link-button"
          :aria-expanded="promptExpanded"
          :aria-controls="promptId"
          @click="promptExpanded = !promptExpanded"
        >
          {{ promptExpanded ? '收起提示词' : '展开提示词' }}
        </button>
      </div>
      <p v-show="!promptLong || promptExpanded" :id="promptId" class="prompt-text">
        {{ prompt || '尚未生成完整提示词' }}
      </p>
    </div>

    <div v-if="referenceId" class="brief-field reference-block">
      <h4>参考图</h4>
      <p class="muted">参考资源记录不可用（{{ referenceId }}）。</p>
    </div>
  </section>
</template>
