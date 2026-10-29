<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import type { AssetView, Brief, BriefFields } from '@/api/types'
import { assetUrl } from '@/api/client'
import {
  briefFieldValue,
  editableBriefFields,
  formatByteSize,
  type EditableBriefKey,
} from '@/utils/format'

const props = defineProps<{
  brief: Brief
  heading: string
  snapshot?: boolean
  referenceAsset?: AssetView | null
  editable?: boolean
  editing?: boolean
  draft?: BriefFields | null
  dirty?: boolean
  fieldErrors?: Partial<Record<EditableBriefKey, string>>
  promptStale?: boolean
  busy?: boolean
  saveDisabledReason?: string
  confirmDisabledReason?: string
  saveNotice?: string
  saveError?: string
  confirmError?: string
  conflict?: boolean
}>()

const emit = defineEmits<{
  (e: 'start-edit'): void
  (e: 'update-field', key: EditableBriefKey, value: string): void
  (e: 'save'): void
  (e: 'cancel'): void
  (e: 'confirm'): void
  (e: 'reorganize'): void
  (e: 'reload-latest'): void
}>()

const headingId = useId()
const promptId = useId()
const rootEl = ref<HTMLElement | null>(null)
const promptExpanded = ref(true)
const fields = computed(() => {
  if (props.snapshot) {
    return editableBriefFields
      .filter((field) => Boolean(props.brief[field.key]?.trim()))
      .map((field) => ({ ...field, value: props.brief[field.key] }))
  }
  if (props.editing) {
    return editableBriefFields.map((field) => ({
      ...field,
      value: props.draft?.[field.key] ?? props.brief[field.key],
    }))
  }
  return editableBriefFields.map((field) => ({
    ...field,
    value: briefFieldValue(props.brief, field.key),
  }))
})
const prompt = computed(() => props.brief.prompt?.trim() ?? '')
const promptLong = computed(() => prompt.value.length > 240)
const reference = computed(() => props.referenceAsset ?? null)
const referenceId = computed(() => props.referenceAsset?.id ?? props.brief.reference_asset_id)

watch(prompt, () => {
  promptExpanded.value = true
})

watch(
  () => props.editing,
  async (editing) => {
    if (!editing) return
    await nextTick()
    rootEl.value?.querySelector<HTMLTextAreaElement>('.brief-input')?.focus()
  },
)

function fieldId(key: EditableBriefKey): string {
  return `${headingId}-${key}`
}

function fieldErrorId(key: EditableBriefKey): string {
  return `${fieldId(key)}-error`
}

function onFieldInput(key: EditableBriefKey, event: Event): void {
  emit('update-field', key, (event.target as HTMLTextAreaElement).value)
}
</script>

<template>
  <section ref="rootEl" class="brief-card" :aria-labelledby="headingId">
    <h3 :id="headingId">{{ heading }}</h3>
    <dl class="brief-fields">
      <div v-for="field in fields" :key="field.key" class="brief-field">
        <dt>
          <label v-if="editable && editing" :for="fieldId(field.key)">{{ field.label }}</label>
          <template v-else>{{ field.label }}</template>
        </dt>
        <dd>
          <textarea
            v-if="editable && editing"
            :id="fieldId(field.key)"
            :value="field.value"
            class="brief-input"
            rows="3"
            :aria-invalid="Boolean(fieldErrors?.[field.key])"
            :aria-describedby="fieldErrors?.[field.key] ? fieldErrorId(field.key) : undefined"
            :disabled="busy"
            @input="onFieldInput(field.key, $event)"
          />
          <template v-else>{{ field.value }}</template>
          <p
            v-if="editable && editing && fieldErrors?.[field.key]"
            :id="fieldErrorId(field.key)"
            class="field-error"
            role="alert"
          >
            {{ fieldErrors[field.key] }}
          </p>
        </dd>
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
      <p v-if="promptStale" class="status-warning" role="status">
        该提示词已过期，请重新整理需求后再确认。
      </p>
      <p v-show="!promptLong || promptExpanded" :id="promptId" class="prompt-text">
        {{ prompt || '尚未生成完整提示词' }}
      </p>
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

    <div v-if="editable && !snapshot" class="brief-actions">
      <button
        v-if="!editing"
        type="button"
        class="secondary-button"
        :disabled="busy"
        @click="emit('start-edit')"
      >
        编辑需求
      </button>
      <template v-else>
        <button
          type="button"
          class="primary-button"
          :disabled="busy || Boolean(saveDisabledReason)"
          @click="emit('save')"
        >
          保存需求
        </button>
        <button
          type="button"
          class="secondary-button"
          :disabled="busy"
          @click="emit('cancel')"
        >
          取消修改
        </button>
      </template>
      <button
        type="button"
        class="secondary-button"
        :disabled="busy"
        @click="emit('reorganize')"
      >
        重新整理需求
      </button>
      <button
        type="button"
        class="primary-button"
        :disabled="busy || Boolean(confirmDisabledReason)"
        @click="emit('confirm')"
      >
        确认当前需求
      </button>
      <p v-if="dirty" class="dirty-note">有未保存的修改</p>
      <p v-if="dirty && saveDisabledReason" class="control-reason">{{ saveDisabledReason }}</p>
      <p v-if="confirmDisabledReason && (!dirty || !saveDisabledReason)" class="control-reason">{{ confirmDisabledReason }}</p>
      <p v-if="saveNotice" class="status-notice" role="status">{{ saveNotice }}</p>
      <p v-if="saveError" class="brief-error" role="alert">{{ saveError }}</p>
      <p v-if="confirmError" class="brief-error" role="alert">{{ confirmError }}</p>
      <p v-if="conflict" class="brief-error" role="alert">
        <span>当前操作基于已过期的需求版本，请加载最新需求后再操作。</span>
        <button type="button" class="secondary-button" @click="emit('reload-latest')">
          加载最新需求
        </button>
      </p>
    </div>
  </section>
</template>
