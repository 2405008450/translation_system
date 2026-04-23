<script setup lang="ts">
import { computed } from 'vue'

import DiffText from './DiffText.vue'

import { getSegmentSourceMeta, getSegmentStatusMeta } from '../constants/status'
import type { Segment, SegmentRevisionEntry } from '../types/api'

const props = withDefaults(defineProps<{
  segment: Segment
  index: number
  active: boolean
  disabled?: boolean
  pendingRevision?: SegmentRevisionEntry | null
  revisionBusy?: boolean
}>(), {
  disabled: false,
  pendingRevision: null,
  revisionBusy: false,
})

const emit = defineEmits<{
  update: [sentenceId: string, value: string]
  focus: [sentenceId: string]
  acceptRevision: [revisionId: string]
  rejectRevision: [revisionId: string]
}>()

const statusClass = computed(() => `segment-row--${props.segment.status || 'none'}`)
const sourceClass = computed(() => `segment-row__tag--source-${props.segment.source || 'none'}`)
const statusMeta = computed(() => getSegmentStatusMeta(props.segment.status))
const sourceMeta = computed(() => getSegmentSourceMeta(props.segment.source))
const revisionSourceMeta = computed(() => getSegmentSourceMeta(props.pendingRevision?.source || 'manual'))
const hasSourceDiff = computed(() => (
  props.segment.status === 'fuzzy'
  && Boolean(props.segment.matched_source_text)
))
const hasPendingRevision = computed(() => Boolean(props.pendingRevision))
</script>

<template>
  <article
    class="segment-row"
    :class="[statusClass, { 'is-active': active, 'has-pending-revision': hasPendingRevision }]"
    :id="`segment-${segment.sentence_id}`"
    :data-sentence-id="segment.sentence_id"
    role="group"
    :aria-label="`segment ${index + 1}`"
  >
    <div class="segment-row__head">
      <span class="segment-row__index">#{{ index + 1 }}</span>
      <span class="segment-row__tag segment-row__tag--status">{{ statusMeta.label }}</span>
      <span class="segment-row__tag is-muted" :class="sourceClass">{{ sourceMeta.label }}</span>
      <span v-if="segment.score" class="segment-row__tag is-muted">
        {{ segment.score.toFixed(2) }}
      </span>
    </div>

    <div class="segment-row__grid">
      <div class="segment-row__cell segment-row__cell--source">
        <div class="segment-row__label-row">
          <div class="segment-row__label">原文</div>
          <span v-if="hasSourceDiff" class="segment-row__badge">TM 差异</span>
        </div>
        <div class="segment-row__text">
          <DiffText
            v-if="hasSourceDiff"
            :old-text="segment.matched_source_text || ''"
            :new-text="segment.source_text"
          />
          <template v-else>{{ segment.display_text || segment.source_text }}</template>
        </div>
      </div>

      <div class="segment-row__cell segment-row__cell--target" :class="{ 'is-pending': hasPendingRevision }">
        <div class="segment-row__label-row">
          <span class="segment-row__label">译文</span>
          <span v-if="hasPendingRevision" class="segment-row__badge segment-row__badge--pending">待审核</span>
        </div>
        <div v-if="pendingRevision" class="segment-row__revision-panel">
          <div class="segment-row__revision-head">
            <strong>修订对比</strong>
            <span>{{ revisionSourceMeta.label }}</span>
          </div>
          <DiffText
            :old-text="pendingRevision.before_text"
            :new-text="pendingRevision.after_text"
            empty-text="空"
          />
          <div class="segment-row__revision-actions">
            <button
              class="button segment-row__revision-button"
              type="button"
              :disabled="disabled || revisionBusy"
              @click.stop="emit('acceptRevision', pendingRevision.id)"
            >
              接受
            </button>
            <button
              class="button segment-row__revision-button segment-row__revision-button--danger"
              type="button"
              :disabled="disabled || revisionBusy"
              @click.stop="emit('rejectRevision', pendingRevision.id)"
            >
              拒绝
            </button>
          </div>
        </div>
        <textarea
          class="segment-row__textarea"
          :value="segment.target_text"
          :disabled="disabled"
          data-segment-target="true"
          :data-sentence-id="segment.sentence_id"
          :aria-label="`translation for segment ${index + 1}`"
          spellcheck="false"
          @focus="emit('focus', segment.sentence_id)"
          @input="emit('update', segment.sentence_id, ($event.target as HTMLTextAreaElement).value)"
        />
      </div>
    </div>
  </article>
</template>

<style scoped>
.segment-row__label-row,
.segment-row__revision-head,
.segment-row__revision-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.segment-row__badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(216, 183, 78, 0.18);
  color: #8a6700;
  font-size: 12px;
}

.segment-row__badge--pending {
  background: rgba(13, 122, 104, 0.14);
  color: #0b6b5b;
}

.segment-row__cell--target.is-pending {
  border-color: rgba(13, 122, 104, 0.35);
  box-shadow: inset 3px 0 0 rgba(13, 122, 104, 0.48);
}

.segment-row__revision-panel {
  display: grid;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid rgba(13, 122, 104, 0.16);
  border-radius: 8px;
  background: rgba(243, 250, 247, 0.96);
}

.segment-row__revision-head {
  color: var(--text-muted);
  font-size: 12px;
}

.segment-row__revision-head strong {
  color: var(--text-primary);
  font-size: 13px;
}

.segment-row__revision-actions {
  justify-content: flex-end;
}

.segment-row__revision-button {
  min-height: 30px;
  padding: 6px 12px;
  font-size: 13px;
  box-shadow: none;
}

.segment-row__revision-button--danger {
  border-color: rgba(194, 59, 63, 0.28);
  color: #a43a3d;
}
</style>
