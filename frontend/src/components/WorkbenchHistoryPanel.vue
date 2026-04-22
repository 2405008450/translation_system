<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import StateView from './base/StateView.vue'

import { getSegmentSourceMeta } from '../constants/status'
import type { SegmentComment, SegmentRevisionEntry } from '../types/api'

const props = withDefaults(defineProps<{
  activeSentenceId: string | null
  comments: SegmentComment[]
  history: SegmentRevisionEntry[]
}>(), {
  activeSentenceId: null,
})
const { t } = useI18n()

const timelineComments = computed(() => {
  if (!props.activeSentenceId) {
    return []
  }
  return props.comments.filter((comment) => comment.sentence_id === props.activeSentenceId && !comment.parent_id)
})

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function getAuthorDisplayName(comment: SegmentComment) {
  return comment.author.nickname || comment.author.username
}
</script>

<template>
  <section class="panel workbench-tool-panel">
    <div class="panel-header panel-header--compact">
      <div>
        <div class="section-title section-title--tight">{{ t('historyPanel.title') }}</div>
        <p class="panel-subtitle">{{ t('historyPanel.description') }}</p>
      </div>
    </div>

    <StateView
      v-if="!activeSentenceId"
      kind="empty"
      :title="t('historyPanel.noActiveTitle')"
      :message="t('historyPanel.noActiveMessage')"
    />
    <div v-else class="workbench-history-panel">
      <div class="workbench-history-panel__group">
        <div class="workbench-history-panel__title">{{ t('historyPanel.commentTitle') }}</div>
        <StateView
          v-if="timelineComments.length === 0"
          kind="empty"
          :title="t('historyPanel.noCommentTitle')"
          :message="t('historyPanel.noCommentMessage')"
        />
        <article v-for="comment in timelineComments" :key="comment.id" class="workbench-history-panel__comment">
          <div class="workbench-history-panel__meta">
            <strong>{{ getAuthorDisplayName(comment) }}</strong>
            <span>{{ formatDateTime(comment.created_at) }}</span>
          </div>
          <p>{{ comment.body }}</p>
        </article>
      </div>

      <div class="workbench-history-panel__group">
        <div class="workbench-history-panel__title">{{ t('historyPanel.revisionTitle') }}</div>
        <StateView
          v-if="history.length === 0"
          kind="empty"
          :title="t('historyPanel.noRevisionTitle')"
          :message="t('historyPanel.noRevisionMessage')"
        />
        <article v-for="entry in history" :key="entry.id" class="workbench-history-panel__revision">
          <div class="workbench-history-panel__meta">
            <strong>{{ getSegmentSourceMeta(entry.source).label }}</strong>
            <span>{{ formatDateTime(entry.created_at) }}</span>
          </div>
          <div class="workbench-history-panel__diff">
            <div>
              <div class="workbench-history-panel__label">{{ t('historyPanel.before') }}</div>
              <p>{{ entry.before_text || t('historyPanel.emptyText') }}</p>
            </div>
            <div>
              <div class="workbench-history-panel__label">{{ t('historyPanel.after') }}</div>
              <p>{{ entry.after_text || t('historyPanel.emptyText') }}</p>
            </div>
          </div>
        </article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.workbench-history-panel,
.workbench-history-panel__group {
  display: grid;
  gap: 12px;
}

.workbench-history-panel__title {
  color: var(--text-muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.workbench-history-panel__comment,
.workbench-history-panel__revision {
  display: grid;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--surface-panel);
}

.workbench-history-panel__meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--text-muted);
  font-size: 12px;
}

.workbench-history-panel__comment p,
.workbench-history-panel__revision p {
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.6;
  white-space: pre-wrap;
}

.workbench-history-panel__diff {
  display: grid;
  gap: 10px;
}

.workbench-history-panel__label {
  margin-bottom: 4px;
  color: var(--text-muted);
  font-size: 12px;
}
</style>
