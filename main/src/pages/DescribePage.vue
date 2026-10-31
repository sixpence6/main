<script setup lang="ts">
import PageTemplate from '@/components/PageTemplate.vue'
import WorkflowPanel from '@/components/workflow/WorkflowPanel.vue'
import ConversationPanel from '@/components/ConversationPanel.vue'
</script>

<template>
  <PageTemplate
    eyebrow="工作台"
    title="描述需求"
    title-id="describe-title"
    intro="用自然语言描述商品与画面要求，并可上传一张参考图。"
  >
    <section class="model-data-notice" aria-labelledby="model-data-notice-title">
      <h3 id="model-data-notice-title">外部模型数据说明</h3>
      <p>
        你在工作台提交的文字描述、需求卡信息和参考图会发送给已配置的外部模型服务，用于需求整理、图片生成和结果核验。请勿在描述或图片中填写密码、会话凭证、恢复链接或其他秘密信息。模型服务可能按其自身政策处理数据，本应用不承诺供应商零保留。
      </p>
    </section>
    <WorkflowPanel />
    <ConversationPanel />
  </PageTemplate>
</template>

<style scoped>
.model-data-notice {
  display: grid;
  gap: var(--space-1);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-warning);
  border-radius: var(--radius-control);
  background: var(--color-panel);
}

.model-data-notice h3 {
  margin: 0;
  font-size: var(--text-body);
  line-height: var(--text-body-line);
}

.model-data-notice p {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-body);
  line-height: var(--text-body-line);
}
</style>
