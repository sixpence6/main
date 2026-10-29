<script setup lang="ts">
import { useWorkspace } from '@/composables/useWorkspace'

const {
  resultRetrievalGeneration,
  resultRetrievalAvailable,
  resultRetrievalUnavailableReason,
  generationRetrieveBusy,
  generationRetrieveError,
  generationRetrieveNotice,
  generationRetrieveDisabledReason,
  retrieveGenerationResult,
} = useWorkspace()
</script>

<template>
  <section
    v-if="resultRetrievalGeneration || generationRetrieveNotice || generationRetrieveError"
    class="result-retrieval"
    aria-labelledby="result-retrieval-title"
  >
    <div class="result-retrieval-copy">
      <h3 id="result-retrieval-title">重取现有结果</h3>
      <p v-if="resultRetrievalGeneration">
        第 {{ resultRetrievalGeneration.version_no }} 版的结果文件获取失败。重取只重新下载这条记录已保存的结果，不会发起新的生成，也不会创建新版本。
      </p>
      <p v-if="resultRetrievalGeneration?.error" class="result-retrieval-original">
        原记录错误：{{ resultRetrievalGeneration.error.message }}（{{ resultRetrievalGeneration.error.code }}）
      </p>
    </div>
    <div v-if="resultRetrievalGeneration" class="result-retrieval-actions">
      <button
        v-if="resultRetrievalAvailable"
        type="button"
        class="secondary-button"
        :disabled="generationRetrieveBusy || Boolean(generationRetrieveDisabledReason)"
        @click="retrieveGenerationResult"
      >
        {{ generationRetrieveBusy ? '正在重取结果…' : '重取现有结果' }}
      </button>
      <p v-else class="control-reason">{{ resultRetrievalUnavailableReason }}</p>
      <p
        v-if="resultRetrievalAvailable && generationRetrieveDisabledReason"
        class="control-reason"
      >
        {{ generationRetrieveDisabledReason }}
      </p>
    </div>
    <p v-if="generationRetrieveNotice" class="status-notice" role="status">
      {{ generationRetrieveNotice }}
    </p>
    <p v-if="generationRetrieveError" class="result-retrieval-error" role="alert">
      {{ generationRetrieveError }}
    </p>
  </section>
</template>
