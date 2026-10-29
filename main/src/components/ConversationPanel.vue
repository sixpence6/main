<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { useWorkspace } from '@/composables/useWorkspace'

const workspace = useWorkspace()
const { draft, projectParam, fillExample, announce } = workspace
const input = ref<HTMLTextAreaElement | null>(null)

const examples = [
  '米色亚麻托特包，简洁主图',
  '陶瓷马克杯，暖色生活场景',
  '木质香薰蜡烛，节日礼盒',
]

async function useExample(text: string): Promise<void> {
  fillExample(text)
  await nextTick()
  input.value?.focus()
}

function onSendShortcut(): void {
  announce('发送能力将在后续版本接入，当前输入尚未发送。')
}
</script>

<template>
  <section class="conversation-panel" aria-labelledby="conversation-title">
    <div v-if="projectParam" class="project-placeholder" role="note">
      <span class="placeholder-icon" aria-hidden="true">i</span>
      <div>
        <strong>项目读取尚未接入</strong>
        <p>当前版本不会读取或伪造项目数据。你可以新建项目，或直接在下方描述需求。</p>
      </div>
    </div>

    <div class="conversation-heading">
      <p class="eyebrow">当前状态：未选择项目</p>
      <h2 id="conversation-title">从一个商品想法开始</h2>
      <p class="conversation-intro">
        用一段自然语言描述商品、受众、画面风格和交付用途。工作台会保留这次会话的草稿；生成与发送能力将在后续版本接入。
      </p>
    </div>

    <div class="example-block">
      <h3>输入示例</h3>
      <div class="example-list">
        <button v-for="example in examples" :key="example" type="button" class="example-button" @click="useExample(example)">
          {{ example }}
        </button>
      </div>
    </div>

    <form class="composer" @submit.prevent="onSendShortcut">
      <label class="sr-only" for="workspace-prompt">描述商品与图片需求</label>
      <textarea
        id="workspace-prompt"
        ref="input"
        v-model="draft"
        class="prompt-input"
        aria-label="描述商品与图片需求"
        rows="6"
        placeholder="例如：为米色亚麻托特包生成一张简洁主图，浅暖色背景，突出材质与轮廓……"
        @keydown.ctrl.enter.prevent="onSendShortcut"
        @keydown.meta.enter.prevent="onSendShortcut"
      />
      <div class="composer-actions">
        <p class="composer-hint">Enter 换行；发送能力将在后续版本接入。</p>
        <div class="composer-buttons">
          <button type="button" class="secondary-button" disabled aria-describedby="reference-reason" title="添加参考图将在后续版本接入">
            添加参考图
          </button>
          <button type="submit" class="primary-button" disabled aria-describedby="send-reason" title="发送将在后续版本接入">
            发送
          </button>
        </div>
      </div>
      <p id="reference-reason" class="control-reason">添加参考图将在后续版本接入，当前不可用。</p>
      <p id="send-reason" class="control-reason">发送将在后续版本接入，当前输入不会发出。</p>
    </form>
  </section>
</template>
