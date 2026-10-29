<script setup lang="ts">
import { computed } from 'vue'
import { useWorkspace } from '@/composables/useWorkspace'

const { projectGenerations } = useWorkspace()
const hasSucceeded = computed(() => projectGenerations.value.some((item) => item.status === 'succeeded'))
</script>

<template>
  <section class="result-panel" aria-labelledby="result-title">
    <h2 id="result-title">查看结果</h2>
    <div class="result-empty">
      <span class="result-empty-icon" aria-hidden="true">◇</span>
      <p v-if="hasSucceeded">已有成功生成记录；请选择项目查看结果。</p>
      <p v-else>尚未生成图片</p>
    </div>
  </section>
</template>
