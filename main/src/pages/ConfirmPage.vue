<script setup lang="ts">
import PageTemplate from '@/components/PageTemplate.vue'
import BriefCard from '@/components/BriefCard.vue'
import { useWorkspace } from '@/composables/useWorkspace'
import { briefStatusLabel, displayProjectTitle } from '@/utils/format'

const {
  currentProject,
  projectLoading,
  projectError,
  projectErrorKind,
  retryProject,
  openProjectDrawer,
  createNewProject,
  briefDraft,
  briefEditing,
  briefFieldErrors,
  briefDirty,
  briefBusy,
  briefSaveError,
  briefConfirmError,
  briefSaveNotice,
  briefConflict,
  briefLocalCopy,
  briefAction,
  promptStale,
  saveDisabledReason,
  confirmDisabledReason,
  startBriefEdit,
  cancelBriefEdit,
  setBriefField,
  saveBrief,
  confirmBrief,
  reloadLatestBrief,
  viewLocalBriefCopy,
  retryOrganize,
} = useWorkspace()
</script>

<template>
  <PageTemplate
    eyebrow="工作台"
    title="确认需求"
    title-id="confirm-title"
    intro="编辑并确认当前需求；确认后才能进入生成图片阶段。"
  >
    <div v-if="projectLoading" class="project-loading" role="status">正在读取项目详情…</div>

    <div v-else-if="projectError" class="project-error" role="alert">
      <strong>项目读取失败</strong>
      <p>{{ projectError }}</p>
      <div class="project-error-actions">
        <button
          v-if="projectErrorKind === 'network' || projectErrorKind === 'generic'"
          type="button"
          class="secondary-button"
          @click="retryProject"
        >
          重试
        </button>
        <button type="button" class="secondary-button" @click="openProjectDrawer">打开已有项目</button>
        <button type="button" class="primary-button" @click="createNewProject">新建项目</button>
      </div>
    </div>

    <template v-else-if="currentProject">
      <header class="page-project-heading">
        <div>
          <p class="eyebrow">当前项目</p>
          <h3>{{ displayProjectTitle(currentProject.title) }}</h3>
        </div>
        <span class="status-badge">{{ briefStatusLabel(currentProject.brief_status) }}</span>
      </header>

      <div v-if="briefAction === 'organize'" class="organizing-status" role="status">正在整理需求…</div>

      <BriefCard
        :brief="currentProject.brief"
        heading="当前需求"
        :reference-asset="currentProject.reference_asset"
        editable
        :editing="briefEditing"
        :draft="briefDraft"
        :dirty="briefDirty"
        :field-errors="briefFieldErrors"
        :prompt-stale="promptStale"
        :busy="briefBusy"
        :save-disabled-reason="saveDisabledReason"
        :confirm-disabled-reason="confirmDisabledReason"
        :save-notice="briefSaveNotice"
        :save-error="briefSaveError"
        :confirm-error="briefConfirmError"
        :conflict="briefConflict"
        :local-copy="briefLocalCopy"
        @start-edit="startBriefEdit"
        @update-field="setBriefField"
        @save="saveBrief"
        @cancel="cancelBriefEdit"
        @confirm="confirmBrief"
        @reorganize="retryOrganize"
        @reload-latest="reloadLatestBrief"
        @view-local-copy="viewLocalBriefCopy"
      />
    </template>

    <div v-else class="result-empty">
      <p>尚未选择项目，请先在描述需求页创建或选择项目。</p>
    </div>
  </PageTemplate>
</template>
