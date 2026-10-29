<script setup lang="ts">
import { useWorkspace } from '@/composables/useWorkspace'
import type { StageId } from '@/composables/useWorkspace'

const workspace = useWorkspace()
const { stages, stage, setStage } = workspace

function activate(id: StageId): void {
  setStage(id)
}
</script>

<template>
  <nav class="stage-nav" aria-label="工作阶段">
    <ol class="stage-list">
      <li v-for="(item, index) in stages" :key="item.id" class="stage-item">
        <button
          type="button"
          class="stage-button"
          :class="{ 'is-current': stage === item.id, 'is-locked': item.locked }"
          :aria-current="stage === item.id ? 'step' : undefined"
          :aria-disabled="item.locked ? 'true' : undefined"
          :aria-describedby="item.locked ? `stage-reason-${item.id}` : undefined"
          @click="activate(item.id)"
        >
          <span class="stage-index" aria-hidden="true">{{ index + 1 }}</span>
          <span class="stage-label">{{ item.label }}</span>
          <svg v-if="item.locked" class="stage-lock" aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="5" y="10" width="14" height="10" rx="2" />
            <path d="M8 10V7a4 4 0 0 1 8 0v3" />
          </svg>
          <span v-else class="stage-open" aria-hidden="true">●</span>
        </button>
        <span v-if="item.locked" :id="`stage-reason-${item.id}`" class="sr-only">{{ item.reason }}</span>
      </li>
    </ol>
  </nav>
</template>
