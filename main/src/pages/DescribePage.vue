<script setup lang="ts">
import PageTemplate from '@/components/PageTemplate.vue'
import ConversationPanel from '@/components/ConversationPanel.vue'
</script>

<template>
  <PageTemplate
    eyebrow="工作台"
    title="描述需求"
    title-id="describe-title"
    intro="用自然语言描述商品与画面要求，并可上传一张参考图。"
  >
    <ConversationPanel />
  </PageTemplate>
</template>
