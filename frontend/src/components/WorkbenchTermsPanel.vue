<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import StateView from './base/StateView.vue'

import type { TermBase, TermEntryRecord } from '../types/api'

const props = withDefaults(defineProps<{
  termBases: TermBase[]
  selectedTermBaseId: string
  entries: TermEntryRecord[]
  activeSourceText: string
  loadingBases?: boolean
  loadingEntries?: boolean
  message?: string
}>(), {
  loadingBases: false,
  loadingEntries: false,
  message: '',
})

const emit = defineEmits<{
  'update:selectedTermBaseId': [value: string]
}>()
const { t } = useI18n()

const normalizedSourceText = computed(() => props.activeSourceText.trim().toLowerCase())

const matchedEntries = computed(() => {
  if (!normalizedSourceText.value) {
    return []
  }

  return props.entries.filter((entry) => normalizedSourceText.value.includes(entry.source_text.trim().toLowerCase()))
})

const fallbackEntries = computed(() => props.entries.slice(0, 10))
</script>

<template>
  <section class="panel workbench-tool-panel">
    <div class="panel-header panel-header--compact">
      <div>
        <div class="section-title section-title--tight">{{ t('termsPanel.title') }}</div>
        <p class="panel-subtitle">{{ message || t('termsPanel.description') }}</p>
      </div>
    </div>

    <div class="workbench-terms-panel__controls">
      <label class="field">
        <span class="field__label">{{ t('termsPanel.termBase') }}</span>
        <select
          class="field__control"
          :value="selectedTermBaseId"
          :disabled="loadingBases || termBases.length === 0"
          @change="emit('update:selectedTermBaseId', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">{{ t('termsPanel.selectTermBase') }}</option>
          <option v-for="termBase in termBases" :key="termBase.id" :value="termBase.id">
            {{ termBase.name }}（{{ termBase.entry_count }} 条）
          </option>
        </select>
      </label>
    </div>

    <div class="workbench-terms-panel__summary">
      <span class="tag">{{ t('termsPanel.currentSegment', { status: activeSourceText ? t('termsPanel.segmentReady') : t('termsPanel.segmentMissing') }) }}</span>
      <span class="tag">{{ t('termsPanel.matchCount', { count: matchedEntries.length }) }}</span>
    </div>

    <StateView
      v-if="loadingEntries"
      kind="loading"
      :title="t('termsPanel.loadingTitle')"
      :message="t('termsPanel.loadingMessage')"
    />
    <StateView
      v-else-if="termBases.length === 0"
      kind="empty"
      :title="t('termsPanel.emptyBaseTitle')"
      :message="t('termsPanel.emptyBaseMessage')"
    />
    <StateView
      v-else-if="!selectedTermBaseId"
      kind="empty"
      :title="t('termsPanel.noSelectionTitle')"
      :message="t('termsPanel.noSelectionMessage')"
    />
    <div v-else class="workbench-terms-panel__list">
      <div v-if="matchedEntries.length > 0" class="workbench-terms-panel__group">
        <div class="workbench-terms-panel__group-title">{{ t('termsPanel.matchedTitle') }}</div>
        <article v-for="entry in matchedEntries" :key="entry.id" class="workbench-terms-panel__item is-hit">
          <strong>{{ entry.source_text }}</strong>
          <span>{{ entry.target_text }}</span>
        </article>
      </div>

      <div v-if="matchedEntries.length === 0 && fallbackEntries.length > 0" class="workbench-terms-panel__group">
        <div class="workbench-terms-panel__group-title">{{ t('termsPanel.recentTitle') }}</div>
        <article v-for="entry in fallbackEntries" :key="entry.id" class="workbench-terms-panel__item">
          <strong>{{ entry.source_text }}</strong>
          <span>{{ entry.target_text }}</span>
        </article>
      </div>

      <StateView
        v-if="matchedEntries.length === 0 && fallbackEntries.length === 0"
        kind="empty"
        :title="t('termsPanel.noEntriesTitle')"
        :message="t('termsPanel.noEntriesMessage')"
      />
    </div>
  </section>
</template>

<style scoped>
.workbench-terms-panel__controls,
.workbench-terms-panel__summary,
.workbench-terms-panel__list,
.workbench-terms-panel__group {
  display: grid;
  gap: 12px;
}

.workbench-terms-panel__summary {
  grid-template-columns: repeat(auto-fit, minmax(120px, max-content));
}

.workbench-terms-panel__group-title {
  color: var(--text-muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.workbench-terms-panel__item {
  display: grid;
  gap: 6px;
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--surface-panel);
}

.workbench-terms-panel__item.is-hit {
  border-color: rgba(13, 122, 104, 0.22);
  background: var(--brand-050);
}

.workbench-terms-panel__item strong {
  color: var(--text-primary);
  font-size: 14px;
}

.workbench-terms-panel__item span {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
</style>
