<script setup lang="ts">
import { computed } from 'vue'
import { assetUrl } from '@/api/client'
import { useWorkspace } from '@/composables/useWorkspace'

const { projectGenerations } = useWorkspace()

const previews = computed(() =>
  projectGenerations.value
    .filter(
      (generation) =>
        generation.status === 'succeeded' &&
        generation.result_asset?.availability === 'available',
    )
    .slice()
    .sort((left, right) => right.version_no - left.version_no)
    .slice(0, 6),
)
</script>

<template>
  <section v-if="previews.length" class="history-preview" aria-labelledby="history-preview-title">
    <h3 id="history-preview-title">历史图片预览</h3>
    <p class="muted">
      以下旧图仅作历史参考，不代表当前需求状态。查看历史不会修改当前需求，也不会调用模型。
    </p>
    <ul class="history-preview-list">
      <li v-for="generation in previews" :key="generation.id">
        <figure>
          <img
            :src="assetUrl(generation.result_asset!.id)"
            :alt="'第 ' + generation.version_no + ' 次生成的历史预览'"
            loading="lazy"
          />
          <figcaption>
            第 {{ generation.version_no }} 次生成 · 历史预览，不代表当前需求状态
          </figcaption>
        </figure>
      </li>
    </ul>
  </section>
</template>
