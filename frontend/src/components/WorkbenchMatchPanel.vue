<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { Segment, TermEntryRecord } from '../types/api'
import DiffText from './DiffText.vue'

const props = defineProps<{
  segment: Segment | null
  collectionName: string | null
  termBaseName: string | null
  termEntries: TermEntryRecord[]
  activeSourceText: string
}>()

const { t } = useI18n()

const matchPercent = computed(() => {
  if (!props.segment || props.segment.score <= 0) return null
  return Math.round(props.segment.score * 100)
})

const matchedCollectionName = computed(() => {
  return props.segment?.matched_collection_name || props.collectionName
})

const matchedTerms = computed(() => {
  if (!props.activeSourceText || props.termEntries.length === 0) return []
  const sourceText = props.activeSourceText.toLowerCase()
  return props.termEntries.filter((entry) => sourceText.includes(entry.source_text.toLowerCase()))
})

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
</script>

<template>
  <div class="match-panel">
    <div class="match-panel__header">
      <h3 class="match-panel__title">{{ t('matchPanel.title') }}</h3>
    </div>

    <div class="match-panel__body">
      <!-- TM 匹配区 -->
      <section class="match-section">
        <h4 class="match-section__title">{{ t('matchPanel.tmMatch') }}</h4>

        <template v-if="segment && segment.score > 0">
          <div class="match-info">
            <div class="match-info__row">
              <span class="match-info__label">{{ t('matchPanel.matchRate') }}</span>
              <span class="match-info__value match-rate" :class="`match-rate--${matchPercent && matchPercent >= 100 ? 'exact' : 'fuzzy'}`">
                {{ matchPercent }}%
              </span>
            </div>

            <div v-if="matchedCollectionName" class="match-info__row">
              <span class="match-info__label">{{ t('matchPanel.source') }}</span>
              <span class="match-info__value">{{ matchedCollectionName }}</span>
            </div>

            <div v-if="segment.matched_creator_name" class="match-info__row">
              <span class="match-info__label">{{ t('matchPanel.creator') }}</span>
              <span class="match-info__value">{{ segment.matched_creator_name }}</span>
            </div>

            <div v-if="segment.matched_updated_at" class="match-info__row">
              <span class="match-info__label">{{ t('matchPanel.updateTime') }}</span>
              <span class="match-info__value">{{ formatDateTime(segment.matched_updated_at) }}</span>
            </div>
          </div>

          <div v-if="segment.matched_source_text && segment.status === 'fuzzy'" class="match-diff">
            <div class="match-diff__label">{{ t('matchPanel.tmDiff') }}</div>
            <div class="match-diff__content">
              <DiffText :old-text="segment.matched_source_text" :new-text="segment.source_text" />
            </div>
          </div>

          <div v-if="segment.target_text" class="match-reference">
            <div class="match-reference__label">{{ t('matchPanel.tmReference') }}</div>
            <div class="match-reference__content">{{ segment.target_text }}</div>
          </div>
        </template>

        <div v-else class="match-empty">
          {{ t('matchPanel.noMatch') }}
        </div>
      </section>

      <!-- 术语匹配区 -->
      <section class="match-section">
        <h4 class="match-section__title">{{ t('matchPanel.termMatch') }}</h4>

        <template v-if="matchedTerms.length > 0">
          <div class="term-list">
            <div v-for="term in matchedTerms" :key="term.id" class="term-item">
              <div class="term-item__source">{{ term.source_text }}</div>
              <div class="term-item__target">{{ term.target_text }}</div>
              <div class="term-item__meta">
                <span v-if="termBaseName" class="term-item__source-name">
                  {{ t('matchPanel.source') }}: {{ termBaseName }}
                </span>
                <span v-if="term.creator_name" class="term-item__creator">
                  {{ t('matchPanel.creator') }}: {{ term.creator_name }}
                </span>
                <span v-if="term.updated_at" class="term-item__time">
                  {{ formatDateTime(term.updated_at) }}
                </span>
              </div>
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

.match-diff {
  padding: 10px;
  background: var(--surface-muted);
  border-radius: 8px;
}

.match-diff__label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.match-diff__content {
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-primary);
}

.match-reference {
  padding: 10px;
  background: var(--surface-muted);
  border-radius: 8px;
}

.match-reference__label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.match-reference__content {
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-primary);
}

.match-empty {
  padding: 16px;
  text-align: center;
  font-size: 13px;
  color: var(--text-tertiary);
  background: var(--surface-muted);
  border-radius: 8px;
}

.term-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.term-item {
  padding: 10px;
  background: var(--surface-muted);
  border-radius: 8px;
}

.term-item__source {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.term-item__target {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.term-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.term-item__source-name,
.term-item__creator,
.term-item__time {
  white-space: nowrap;
}
</style>
