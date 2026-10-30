<script setup lang="ts">
import { nextTick, onMounted } from "vue";
import PageTemplate from "@/components/PageTemplate.vue";
import WorkflowPanel from "@/components/workflow/WorkflowPanel.vue";
import { useWorkspace } from "@/composables/useWorkspace";
import { displayProjectTitle, generationStatusLabel } from "@/utils/format";

const {
  currentProject,
  projectLoading,
  projectError,
  projectErrorKind,
  retryProject,
  openProjectDrawer,
  createNewProject,
  generationDisabledReason,
  generationSubmitBusy,
  generationSubmitError,
  generationSubmitNotice,
  generationUncertain,
  generationPendingRestored,
  generationRecoveryAvailable,
  generationCredentialWarning,
  generationRecoveryAction,
  latestGenerationView,
  latestGenerationError,
  activeGeneration,
  submitGeneration,
  verifyGenerationSubmission,
  resumeGenerationSubmission,
  setStage,
} = useWorkspace();

function editRequirement(): void {
  setStage("confirm");
}

function generate(): void {
  void submitGeneration();
}

onMounted(() => {
  void nextTick(() => {
    const heading = document.getElementById("generation-title");
    if (!heading) return;
    heading.setAttribute("tabindex", "-1");
    heading.focus();
  });
});
</script>

<template>
  <PageTemplate
    eyebrow="工作台"
    title="生成图片"
    title-id="generation-page-title"
    intro="确认后的完整提示词如下；可返回修改需求，或直接生成图片。"
  >
    <WorkflowPanel />
    <div v-if="projectLoading" class="project-loading" role="status">
      正在读取项目详情…
    </div>

    <div v-else-if="projectError" class="project-error" role="alert">
      <strong>项目读取失败</strong>
      <p>{{ projectError }}</p>
      <div class="project-error-actions">
        <button
          v-if="
            projectErrorKind === 'network' || projectErrorKind === 'generic'
          "
          type="button"
          class="secondary-button"
          @click="retryProject"
        >
          重试
        </button>
        <button
          type="button"
          class="secondary-button"
          @click="openProjectDrawer"
        >
          打开已有项目
        </button>
        <button type="button" class="primary-button" @click="createNewProject">
          新建项目
        </button>
      </div>
    </div>

    <template v-else-if="currentProject">
      <header class="page-project-heading">
        <div>
          <p class="eyebrow">当前项目</p>
          <h3>{{ displayProjectTitle(currentProject.title) }}</h3>
        </div>
        <span class="status-badge">需求已确认</span>
      </header>

      <section
        id="generation-summary"
        class="generation-summary"
        aria-labelledby="generation-title"
      >
        <div class="generation-summary-header">
          <div>
            <h3 id="generation-title">生成概览</h3>
            <p>第 {{ currentProject.revision }} 修订 · 确认后的提示词</p>
          </div>
          <span class="generation-quantity"
            >图片尺寸：{{ currentProject.brief.image_size || "尚未确定" }}</span
          >
        </div>

        <textarea
          class="generation-prompt-text"
          readonly
          rows="8"
          aria-label="确认后的提示词"
          :value="currentProject.brief.prompt || '尚未生成完整提示词'"
        ></textarea>

        <p
          v-if="latestGenerationError"
          class="generation-latest-error"
          role="alert"
        >
          {{ latestGenerationError }}
        </p>
        <p v-if="latestGenerationView" class="muted">
          最近版本：第 {{ latestGenerationView.version_no }} 版，状态：{{
            generationStatusLabel(latestGenerationView.status)
          }}
        </p>
        <p v-if="activeGeneration" class="generation-live" role="status">
          当前生成：第 {{ activeGeneration.version_no }} 版，{{
            generationStatusLabel(activeGeneration.status)
          }}。
        </p>
        <p v-if="generationSubmitNotice" class="status-notice" role="status">
          {{ generationSubmitNotice }}
        </p>

        <div v-if="generationUncertain" class="generation-uncertain">
          <p class="generation-error" role="alert">
            提交待核实：生成请求可能已发出，但尚未收到确认。确认前不会再次提交。
          </p>
          <p
            v-if="generationPendingRestored"
            class="generation-recovery-note"
            role="status"
          >
            已恢复上次未确认的原始提交参数。可先只读核实，或明确恢复本次提交。
          </p>
          <p
            v-if="generationCredentialWarning"
            class="generation-recovery-note"
            role="status"
          >
            {{ generationCredentialWarning }}
          </p>
          <div class="generation-recovery-actions">
            <button
              type="button"
              class="secondary-button"
              :disabled="generationSubmitBusy"
              @click="verifyGenerationSubmission"
            >
              {{
                generationRecoveryAction === "verify"
                  ? "正在核实…"
                  : "核实提交结果"
              }}
            </button>
            <button
              v-if="generationRecoveryAvailable"
              type="button"
              class="secondary-button"
              aria-describedby="generation-recovery-risk"
              :disabled="generationSubmitBusy"
              @click="resumeGenerationSubmission"
            >
              {{
                generationRecoveryAction === "resume"
                  ? "正在恢复提交…"
                  : "恢复本次提交"
              }}
            </button>
          </div>
          <p id="generation-recovery-risk" class="generation-recovery-risk">
            恢复本次提交仅重放原始请求参数；如果原请求未被受理，可能执行这次生成。
          </p>
        </div>

        <p v-if="generationSubmitError" class="generation-error" role="alert">
          {{ generationSubmitError }}
        </p>
      </section>

      <div class="page-actions generation-page-actions">
        <button type="button" class="secondary-button" @click="editRequirement">
          修改需求
        </button>
        <button
          type="button"
          class="primary-button"
          :disabled="Boolean(generationDisabledReason) || generationSubmitBusy"
          @click="generate"
        >
          {{ generationSubmitBusy ? "正在提交生成请求…" : "生成首图" }}
        </button>
      </div>
      <p v-if="generationDisabledReason" class="control-reason">
        {{ generationDisabledReason }}
      </p>
    </template>

    <div v-else class="result-empty">
      <p>尚未选择项目，请先在描述需求页创建或选择项目。</p>
    </div>
  </PageTemplate>
</template>
