<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import type { Segment, TermEntryRecord, TMMatchCandidate } from '../types/api'
import { http } from '../api/http'
import DiffText from './DiffText.vue'

const props = defineProps<{
  segment: Segment | null
  collectionId: string | null
  collectionName: string | null
  termBaseId: string | null
  termBaseName: string | null
  termEntries: TermEntryRecord[]
  activeSourceText: string
  fileRecordId: string | null
}>()

const emit = defineEmits<{
  (e: 'replaceText', text: string): void
  (e: 'appendText', text: string): void
}>()

const { t } = useI18n()

const tmCandidates = ref<TMMatchCandidate[]>([])
const loadingCandidates = ref(false)
let candidateRequestId = 0

const matchPercent = computed(() => {
  const bestScore = tmCandidates.value[0]?.score ?? props.segment?.score ?? 0
  if (bestScore <= 0) return null
  return Math.round(bestScore * 100)
})

const matchedTerms = computed(() => {
  if (!props.activeSourceText || props.termEntries.length === 0) return []
  const sourceText = props.activeSourceText.toLowerCase()
  return props.termEntries
    .filter((entry) => sourceText.includes(entry.source_text.toLowerCase()))
    .slice()
    .sort((left, right) => right.source_text.length - left.source_text.length)
})

watch(
  () => [props.segment?.id ?? '', props.fileRecordId ?? ''],
  async ([segmentId, fileRecordId]) => {
    const requestId = ++candidateRequestId

    if (!segmentId || !fileRecordId) {
      tmCandidates.value = []
      loadingCandidates.value = false
      return
    }

    loadingCandidates.value = true
    try {
      const { data } = await http.get<{
        segment_id: string
        source_text: string
        candidates: TMMatchCandidate[]
      }>(`/file-records/${fileRecordId}/segments/${segmentId}/tm-candidates`)

      if (requestId === candidateRequestId) {
        tmCandidates.value = data.candidates || []
      }
    } catch (error) {
      console.error('Failed to load TM candidates:', error)
      if (requestId === candidateRequestId) {
        tmCandidates.value = []
      }
    } finally {
      if (requestId === candidateRequestId) {
        loadingCandidates.value = false
      }
    }
  },
  { immediate: true },
)

function formatDateTime(isoString: string | null): string {
  if (!isoString) return ''
  const date = new Date(isoString)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function handleTMApply(candidate: TMMatchCandidate) {
  emit('replaceText', candidate.target_text)
}

function handleTermApply(term: TermEntryRecord) {
  emit('appendText', term.target_text)
}

function getMatchScoreClass(score: number): string {
  if (score >= 1.0) return 'exact'
  if (score >= 0.8) return 'high'
  if (score >= 0.6) return 'medium'
  return 'low'
}
</script>

<template>
  <div class="match-panel">
    <div class="match-panel__header">
      <h3 class="match-panel__title">{{ t('matchPanel.title') }}</h3>
    </div>

    <div class="match-panel__body">
      <section class="match-section">
        <h4 class="match-section__title">{{ t('matchPanel.tmMatch') }}</h4>

        <div v-if="loadingCandidates" class="match-loading">
          {{ t('matchPanel.loading') }}
        </div>

        <template v-else-if="tmCandidates.length > 0">
          <div v-if="matchPercent !== null" class="match-info">
            <div class="match-info__row">
              <span class="match-info__label">{{ t('matchPanel.bestMatch') }}</span>
              <span class="match-info__value match-rate" :class="`match-rate--${matchPercent >= 100 ? 'exact' : 'fuzzy'}`">
                {{ matchPercent }}%
              </span>
            </div>
          </div>

          <div class="candidate-list">
            <div
              v-for="(candidate, index) in tmCandidates"
              :key="index"
              class="candidate-item"
              @dblclick="handleTMApply(candidate)"
            >
              <div class="candidate-item__header">
                <div class="candidate-item__meta">
                  <span v-if="candidate.collection_name" class="candidate-item__meta-item">
                    {{ candidate.collection_name }}
                  </span>
                  <span v-if="candidate.creator_name" class="candidate-item__meta-item">
                    {{ t('matchPanel.creator') }}: {{ candidate.creator_name }}
                  </span>
                  <span v-if="candidate.updated_at" class="candidate-item__meta-item">
                    {{ formatDateTime(candidate.updated_at) }}
                  </span>
                </div>
                <button
                  class="button candidate-item__action"
                  type="button"
                  @click.stop="handleTMApply(candidate)"
                >
                  应用
                </button>
              </div>

              <div class="candidate-item__body">
                <div class="candidate-item__panel candidate-item__panel--source">
                  <div class="candidate-item__panel-label">{{ t('matchPanel.tmDiff') }}</div>
                  <div class="candidate-item__diff">
                    <DiffText
                      :old-text="candidate.source_text"
                      :new-text="activeSourceText || candidate.source_text"
                    />
                  </div>
                </div>

                <div class="candidate-item__score-column">
                  <span class="candidate-item__score" :class="getMatchScoreClass(candidate.score)">
                    {{ Math.round(candidate.score * 100) }}%
                  </span>
                </div>

                <div class="candidate-item__panel candidate-item__panel--target">
                  <div class="candidate-item__panel-label">{{ t('matchPanel.tmReference') }}</div>
                  <div class="candidate-item__target-text">{{ candidate.target_text }}</div>
                </div>
              </div>

              <div class="candidate-item__hint">双击替换当前译文</div>
            </div>
          </div>
        </template>

        <div v-else class="match-empty">
          {{ t('matchPanel.noMatch') }}
        </div>
      </section>

      <section class="match-section">
        <h4 class="match-section__title">{{ t('matchPanel.termMatch') }}</h4>

        <template v-if="matchedTerms.length > 0">
          <div class="term-list">
            <div
              v-for="term in matchedTerms"
              :key="term.id"
              class="term-item"
              @dblclick="handleTermApply(term)"
            >
              <div class="term-item__header">
                <div class="term-item__meta">
                  <span v-if="termBaseName" class="term-item__meta-item">
                    {{ t('matchPanel.source') }}: {{ termBaseName }}
                  </span>
                  <span v-if="term.creator_name" class="term-item__meta-item">
                    {{ t('matchPanel.creator') }}: {{ term.creator_name }}
                  </span>
                  <span v-if="term.updated_at" class="term-item__meta-item">
                    {{ formatDateTime(term.updated_at) }}
                  </span>
                </div>
                <button
                  class="button term-item__action"
                  type="button"
                  @click.stop="handleTermApply(term)"
                >
                  应用
                </button>
              </div>

              <div class="term-item__body">
                <div class="term-item__panel">
                  <div class="term-item__panel-label">{{ t('matchPanel.source') }}</div>
                  <div class="term-item__source">{{ term.source_text }}</div>
                </div>

                <div class="term-item__token">TB</div>

                <div class="term-item__panel">
                  <div class="term-item__panel-label">译文</div>
                  <div class="term-item__target">{{ term.target_text }}</div>
                </div>
              </div>

              <div class="term-item__hint">双击追加到当前译文</div>
            </div>
          </div>
        </template>

        <div v-else class="match-empty">
          {{ t('matchPanel.noTermMatch') }}
        </div>
      </section>

    </div>
  </div>
</template>

<style scoped>
.match-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface-panel);
  border-left: 1px solid var(--line-soft);
}

.match-panel__header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-soft);
}

.match-panel__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.match-panel__body {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.match-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.match-section__title {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.match-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  background: var(--surface-muted);
  border-radius: 8px;
}

.match-info__row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.match-info__label {
  font-size: 12px;
  color: var(--text-secondary);
  min-width: 60px;
}

.match-info__value {
  font-size: 12px;
  color: var(--text-primary);
}

.match-rate {
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
}

.match-rate--exact {
  background: var(--state-success-bg);
  color: var(--state-success);
}

.match-rate--fuzzy {
  background: var(--state-warning-bg);
  color: var(--state-warning);
}

.match-loading {
  padding: 16px;
  text-align: center;
  font-size: 13px;
  color: var(--text-tertiary);
}

.candidate-list,
.term-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.candidate-item,
.term-item {
  display: grid;
  gap: 10px;
  padding: 12px;
  border-radius: 12px;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease;
}

.candidate-item {
  background: linear-gradient(180deg, rgba(245, 249, 248, 0.96), rgba(255, 255, 255, 0.98));
  border: 1px solid rgba(13, 122, 104, 0.12);
}

.candidate-item:hover {
  background: rgba(247, 251, 250, 0.98);
  border-color: rgba(13, 122, 104, 0.24);
  box-shadow: 0 10px 24px rgba(15, 76, 68, 0.08);
}

.term-item {
  background: linear-gradient(180deg, rgba(250, 251, 247, 0.96), rgba(255, 255, 255, 0.98));
  border: 1px solid rgba(216, 183, 78, 0.18);
}

.term-item:hover {
  background: rgba(252, 252, 249, 0.98);
  border-color: rgba(216, 183, 78, 0.28);
  box-shadow: 0 10px 24px rgba(124, 95, 14, 0.08);
}

.candidate-item__header,
.term-item__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.candidate-item__meta,
.term-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
  font-size: 11px;
  color: var(--text-tertiary);
}

.candidate-item__meta-item,
.term-item__meta-item {
  white-space: nowrap;
}

.candidate-item__action,
.term-item__action {
  min-height: 32px;
  padding: 6px 12px;
  border-color: rgba(13, 122, 104, 0.22);
  color: #0b6b5b;
  box-shadow: none;
}

.candidate-item__body,
.term-item__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 70px minmax(0, 1fr);
  gap: 10px;
  align-items: stretch;
}

.candidate-item__panel,
.term-item__panel {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.92);
}

.candidate-item__panel--source {
  border-color: rgba(194, 59, 63, 0.14);
  background: linear-gradient(180deg, rgba(255, 248, 248, 0.98), rgba(255, 255, 255, 0.94));
}

.candidate-item__panel--target {
  border-color: rgba(13, 122, 104, 0.14);
  background: linear-gradient(180deg, rgba(245, 251, 249, 0.98), rgba(255, 255, 255, 0.94));
}

.candidate-item__panel-label,
.term-item__panel-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
}

.candidate-item__diff,
.candidate-item__target-text,
.term-item__source,
.term-item__target {
  min-width: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.candidate-item__score-column,
.term-item__token {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 10px 6px;
  border-radius: 10px;
}

.candidate-item__score-column {
  background: linear-gradient(180deg, rgba(232, 243, 240, 0.96), rgba(243, 248, 247, 0.92));
  border: 1px solid rgba(13, 122, 104, 0.12);
}

.candidate-item__score {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 56px;
  min-height: 40px;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 700;
}

.candidate-item__score.exact {
  background: rgba(13, 122, 104, 0.16);
  color: #0b6b5b;
}

.candidate-item__score.high {
  background: rgba(13, 122, 104, 0.12);
  color: #0b6b5b;
}

.candidate-item__score.medium {
  background: rgba(216, 183, 78, 0.2);
  color: #8a6700;
}

.candidate-item__score.low {
  background: rgba(194, 59, 63, 0.16);
  color: #a43a3d;
}

.candidate-item__hint,
.term-item__hint {
  font-size: 11px;
  color: var(--text-tertiary);
  opacity: 0.88;
}

.term-item__token {
  background: linear-gradient(180deg, rgba(255, 244, 215, 0.96), rgba(255, 248, 230, 0.9));
  border: 1px solid rgba(216, 183, 78, 0.18);
  color: #9a7400;
  font-size: 13px;
  font-weight: 700;
}

.term-item__source {
  font-weight: 600;
}

.term-item__target {
  color: var(--text-secondary);
}

.match-empty {
  padding: 16px;
  text-align: center;
  font-size: 13px;
  color: var(--text-tertiary);
  background: var(--surface-muted);
  border-radius: 8px;
}

.candidate-item__diff :deep(.diff-text__segment--insert) {
  background: rgba(13, 122, 104, 0.14);
  color: #0b6b5b;
  box-shadow: inset 0 0 0 1px rgba(13, 122, 104, 0.18);
}

.candidate-item__diff :deep(.diff-text__segment--delete) {
  background: rgba(194, 59, 63, 0.14);
  color: #a43a3d;
  text-decoration-thickness: 1.5px;
  box-shadow: inset 0 0 0 1px rgba(194, 59, 63, 0.16);
}

@media (max-width: 1280px) {
  .candidate-item__body,
  .term-item__body {
    grid-template-columns: 1fr;
  }

  .candidate-item__score-column,
  .term-item__token {
    min-height: auto;
    padding: 8px 10px;
    justify-content: flex-start;
  }
}

@media (max-width: 720px) {
  .candidate-item__header,
  .term-item__header {
    flex-direction: column;
  }

  .candidate-item__action,
  .term-item__action {
    width: 100%;
  }
}
</style>