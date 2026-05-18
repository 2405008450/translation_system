<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import StateView from './base/StateView.vue'
import DiffText from './DiffText.vue'

import { getSegmentSourceMeta } from '../constants/status'
import type { SegmentComment, SegmentRevisionEntry, User } from '../types/api'

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

function formatDateTime(value: string | null) {
  if (!value) {
    return ''
  }
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function getUserDisplayName(user: User | null) {
  if (!user) {
    return '系统'
  }
  return user.nickname || user.username
}

function getRevisionStatusLabel(status: SegmentRevisionEntry['status']) {
  return {
    pending: '待审核',
    accepted: '已接受',
    rejected: '已拒绝',
  }[status]
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
            <strong>{{ getUserDisplayName(comment.author) }}</strong>
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
            <div class="workbench-history-panel__meta-primary">
              <strong>{{ getUserDisplayName(entry.author) }}</strong>
              <span class="workbench-history-panel__tag">
                {{ getSegmentSourceMeta(entry.source).label }}
              </span>
              <span
                class="workbench-history-panel__tag"
                :class="`is-${entry.status}`"
              >
                {{ getRevisionStatusLabel(entry.status) }}
              </span>
            </div>
            <span>{{ formatDateTime(entry.created_at) }}</span>
          </div>
          <div v-if="entry.resolved_by || entry.resolved_at" class="workbench-history-panel__resolved">
            <span>处理人：{{ getUserDisplayName(entry.resolved_by) }}</span>
            <span v-if="entry.resolved_at">{{ formatDateTime(entry.resolved_at) }}</span>
          </div>
          <div class="workbench-history-panel__diff">
            <div class="workbench-history-panel__label">变更内容</div>
            <DiffText
              :old-text="entry.before_text"
              :new-text="entry.after_text"
              :empty-text="t('historyPanel.emptyText')"
            />
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

.workbench-history-panel__meta,
.workbench-history-panel__meta-primary,
.workbench-history-panel__resolved {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--text-muted);
  font-size: 12px;
}

.workbench-history-panel__meta-primary {
  justify-content: flex-start;
}

.workbench-history-panel__comment p {
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.6;
  white-space: pre-wrap;
}

.workbench-history-panel__diff {
  display: grid;
  gap: 6px;
}

.workbench-history-panel__label {
  color: var(--text-muted);
  font-size: 12px;
}

.workbench-history-panel__tag {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(229, 236, 238, 0.9);
  color: #556d72;
}

.workbench-history-panel__tag.is-pending {
  background: rgba(218, 183, 61, 0.18);
  color: #8f6900;
}

.workbench-history-panel__tag.is-accepted {
  background: rgba(83, 176, 116, 0.18);
  color: #267246;
}

.workbench-history-panel__tag.is-rejected {
  background: rgba(208, 88, 88, 0.16);
  color: #9d3a3a;
}
</style>
