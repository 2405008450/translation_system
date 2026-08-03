<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import StateView from './base/StateView.vue'

import type { OnlineTermResult, TermBase, TermEntryRecord } from '../types/api'
import { hasTermTextMatch } from '../utils/termMatching'

interface OnlineTermQueryRequest {
  query: string
  sources: Array<'wikipedia' | 'iate' | 'linguee'>
}

const props = withDefaults(defineProps<{
  termBases: TermBase[]
  selectedTermBaseId: string
  entries: TermEntryRecord[]
  activeSourceText: string
  localTotal?: number
  loadingMoreLocal?: boolean
  hasMoreLocal?: boolean
  addedOnlineTermKeys?: string[]
  onlineEntries?: OnlineTermResult[]
  onlineLoading?: boolean
  onlineError?: string
  canWriteSelectedTermBase?: boolean
  loadingBases?: boolean
  loadingEntries?: boolean
  message?: string
}>(), {
  localTotal: 0,
  loadingMoreLocal: false,
  hasMoreLocal: false,
  addedOnlineTermKeys: () => [],
  onlineEntries: () => [],
  onlineLoading: false,
  onlineError: '',
  canWriteSelectedTermBase: false,
  loadingBases: false,
  loadingEntries: false,
  message: '',
})

const emit = defineEmits<{
  'update:selectedTermBaseId': [value: string]
  'query-online': [request: OnlineTermQueryRequest]
  'add-online-term': [entry: OnlineTermResult]
  'revoke-online-term': [entryId: string]
  'load-more-local': []
}>()
const { t } = useI18n()
const viewMode = ref<'matched' | 'local' | 'online'>('matched')
const onlineQuery = ref('')
const selectedSources = ref<OnlineTermQueryRequest['sources']>(['wikipedia', 'iate', 'linguee'])

watch(() => props.activeSourceText, (value) => {
  if (value.trim()) onlineQuery.value = value.trim()
}, { immediate: true })

const normalizedSourceText = computed(() => props.activeSourceText.trim())
const matchedEntries = computed(() => !normalizedSourceText.value
  ? []
  : props.entries.filter((entry) => hasTermTextMatch(normalizedSourceText.value, entry.source_text)))
const localEntries = computed(() => props.entries)
const onlineMatchCount = computed(() => props.onlineEntries.length)

function termEntryKey(sourceText: string, targetText: string) {
  return `${sourceText}\u0000${targetText}`.toLocaleLowerCase()
}

function isOnlineEntry(entry: TermEntryRecord) {
  return entry.metadata?.origin === 'online'
}

function onlineLocalEntry(entry: OnlineTermResult) {
  const key = termEntryKey(entry.source_text, entry.target_text)
  return props.entries.find((item) => isOnlineEntry(item) && termEntryKey(item.source_text, item.target_text) === key)
}

function isOnlineResultAdded(entry: OnlineTermResult) {
  const key = termEntryKey(entry.source_text, entry.target_text)
  return props.addedOnlineTermKeys.includes(key) || Boolean(onlineLocalEntry(entry))
}

function revokeOnlineTerm(entry: OnlineTermResult) {
  const localEntry = onlineLocalEntry(entry)
  if (localEntry) emit('revoke-online-term', localEntry.id)
}

function formatEntryTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function queryOnline() {
  const query = onlineQuery.value.trim()
  if (!query || props.onlineLoading || !props.selectedTermBaseId) return
  emit('query-online', { query, sources: selectedSources.value })
}

function toggleSource(source: OnlineTermQueryRequest['sources'][number]) {
  selectedSources.value = selectedSources.value.includes(source)
    ? selectedSources.value.filter((item) => item !== source)
    : [...selectedSources.value, source]
}

function ratingStars(confidence: number) {
  const count = Math.max(0, Math.min(5, Math.round(confidence * 5)))
  return '★'.repeat(count) + '☆'.repeat(5 - count)
}

function handleLocalScroll(event: Event) {
  if (!props.hasMoreLocal || props.loadingMoreLocal) return
  const element = event.currentTarget as HTMLElement | null
  if (!element) return
  const distanceToBottom = element.scrollHeight - element.scrollTop - element.clientHeight
  if (distanceToBottom <= 80) emit('load-more-local')
}
</script>

<template>
  <section class="panel workbench-tool-panel">
    <div class="panel-header panel-header--compact">
      <div>
        <div class="section-title section-title--tight">{{ t('termsPanel.title') }}</div>
        <p class="panel-subtitle">{{ message || t('termsPanel.description') }}</p>
      </div>
    </div>

    <div class="workbench-terms-panel__tabs" role="tablist" :aria-label="t('termsPanel.title')">
      <button type="button" :class="['tag', { 'is-active': viewMode === 'matched' }]" @click="viewMode = 'matched'">
        {{ t('termsPanel.matchedTab') }}
      </button>
      <button type="button" :class="['tag', { 'is-active': viewMode === 'local' }]" @click="viewMode = 'local'">
        {{ t('termsPanel.localTab') }}
      </button>
      <button type="button" :class="['tag', { 'is-active': viewMode === 'online' }]" @click="viewMode = 'online'">
        🌐 {{ t('termsPanel.onlineTab') }}
      </button>
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
      <span class="tag">{{ t(
        viewMode === 'online'
          ? 'termsPanel.onlineCount'
          : viewMode === 'local'
            ? 'termsPanel.localCount'
            : 'termsPanel.matchCount',
        { count: viewMode === 'online' ? onlineMatchCount : viewMode === 'local' ? (props.localTotal || localEntries.length) : matchedEntries.length },
      ) }}</span>
    </div>

    <StateView
      v-if="loadingEntries && viewMode === 'local'"
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
    <div v-else-if="viewMode === 'online'" class="workbench-terms-panel__list">
      <div class="workbench-terms-panel__online-bar">
        <input v-model="onlineQuery" class="field__control" type="search" :placeholder="t('termsPanel.onlinePlaceholder')" @keyup.enter="queryOnline">
        <button class="button" type="button" :disabled="onlineLoading || !onlineQuery.trim()" @click="queryOnline">
          {{ onlineLoading ? t('termsPanel.onlineQuerying') : t('termsPanel.onlineQueryButton') }}
        </button>
      </div>
      <div class="workbench-terms-panel__sources">
        <label v-for="source in (['wikipedia', 'iate', 'linguee'] as const)" :key="source">
          <input type="checkbox" :checked="selectedSources.includes(source)" @change="toggleSource(source)">
          {{ t(`termsPanel.sources.${source}`) }}
        </label>
      </div>
      <p v-if="onlineError" class="form-message is-error">{{ onlineError }}</p>
      <StateView v-if="onlineLoading" kind="loading" :title="t('termsPanel.onlineQuerying')" :message="t('termsPanel.onlineLoadingMessage')" />
      <div v-else-if="onlineEntries.length > 0" class="workbench-terms-panel__group">
        <div class="workbench-terms-panel__group-title">{{ t('termsPanel.onlineResultsTitle') }}</div>
        <article v-for="entry in onlineEntries" :key="`${entry.source_text}-${entry.target_text}-${entry.source_url}`" class="workbench-terms-panel__item is-online">
          <strong>{{ entry.source_text }}</strong>
          <span class="workbench-terms-panel__target">{{ entry.target_text }}</span>
          <small>{{ t('termsPanel.sourceLabel') }}：{{ entry.source_name }} · {{ ratingStars(entry.confidence) }}</small>
          <p v-if="entry.note">{{ entry.note }}</p>
          <div class="workbench-terms-panel__item-actions">
            <a class="button button--ghost" :href="entry.source_url" target="_blank" rel="noopener noreferrer">{{ t('termsPanel.cite') }}</a>
            <button
              :class="['button', 'workbench-terms-panel__online-add', { 'is-added': isOnlineResultAdded(entry) }]"
              type="button"
              :disabled="!canWriteSelectedTermBase || isOnlineResultAdded(entry)"
              :title="canWriteSelectedTermBase ? '' : t('termsPanel.noWritePermission')"
              @click="emit('add-online-term', entry)"
            >
              {{ isOnlineResultAdded(entry) ? t('termsPanel.alreadyAdded') : t('termsPanel.addToTermBase') }}
            </button>
            <button
              v-if="onlineLocalEntry(entry)"
              class="button button--ghost workbench-terms-panel__online-revoke"
              type="button"
              :disabled="!canWriteSelectedTermBase"
              :title="canWriteSelectedTermBase ? '' : t('termsPanel.noWritePermission')"
              @click="revokeOnlineTerm(entry)"
            >
              {{ t('termsPanel.revokeOnlineTerm') }}
            </button>
          </div>
        </article>
      </div>
      <StateView v-else kind="empty" :title="t('termsPanel.onlineEmptyTitle')" :message="t('termsPanel.onlineEmptyMessage')" />
    </div>
    <div v-else-if="viewMode === 'matched'" class="workbench-terms-panel__list">
      <div v-if="matchedEntries.length > 0" class="workbench-terms-panel__group">
        <div class="workbench-terms-panel__group-title">{{ t('termsPanel.matchedTitle') }}</div>
        <article v-for="entry in matchedEntries" :key="entry.id" class="workbench-terms-panel__item is-hit">
          <strong>{{ entry.source_text }}</strong>
          <span class="workbench-terms-panel__target">{{ entry.target_text }}</span>
          <div class="workbench-terms-panel__entry-meta">
            <span v-if="isOnlineEntry(entry)" class="tag workbench-terms-panel__online-tag">{{ t('termsPanel.onlineTag') }}</span>
            <span v-if="entry.creator_name">{{ t('termsPanel.addedMeta', { name: entry.creator_name, time: formatEntryTime(entry.created_at) }) }}</span>
          </div>
        </article>
      </div>
      <StateView v-else kind="empty" :title="t('termsPanel.noMatchedTitle')" :message="t('termsPanel.noMatchedMessage')" />
    </div>
    <div v-else-if="viewMode === 'local'" class="workbench-terms-panel__list" @scroll.passive="handleLocalScroll">
      <div v-if="localEntries.length > 0" class="workbench-terms-panel__group">
        <div class="workbench-terms-panel__group-title">{{ t('termsPanel.localTitle') }}</div>
        <article v-for="entry in localEntries" :key="entry.id" class="workbench-terms-panel__item">
          <strong>{{ entry.source_text }}</strong>
          <span class="workbench-terms-panel__target">{{ entry.target_text }}</span>
          <div class="workbench-terms-panel__entry-meta">
            <span v-if="isOnlineEntry(entry)" class="tag workbench-terms-panel__online-tag">{{ t('termsPanel.onlineTag') }}</span>
            <span v-if="entry.creator_name">{{ t('termsPanel.addedMeta', { name: entry.creator_name, time: formatEntryTime(entry.created_at) }) }}</span>
          </div>
        </article>
        <div v-if="loadingMoreLocal" class="workbench-terms-panel__load-more">{{ t('termsPanel.loadingMore') }}</div>
        <div v-else-if="!hasMoreLocal" class="workbench-terms-panel__load-more">{{ t('termsPanel.allLoaded') }}</div>
      </div>
      <StateView v-else kind="empty" :title="t('termsPanel.noEntriesTitle')" :message="t('termsPanel.noEntriesMessage')" />
    </div>
  </section>
</template>

<style scoped>
.workbench-terms-panel__controls,
.workbench-terms-panel__summary,
.workbench-terms-panel__list,
.workbench-terms-panel__group,
.workbench-terms-panel__sources,
.workbench-terms-panel__online-bar {
  display: grid;
  gap: 10px;
}
.workbench-terms-panel__list {
  max-height: min(60vh, 520px);
  overflow-y: auto;
  padding-right: 4px;
}
.workbench-terms-panel__tabs,
.workbench-terms-panel__summary,
.workbench-terms-panel__sources,
.workbench-terms-panel__item-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.workbench-terms-panel__tabs .tag { border: 0; cursor: pointer; }
.workbench-terms-panel__tabs .tag.is-active { color: #087a68; background: #e6f7f2; }
.workbench-terms-panel__sources { grid-template-columns: repeat(3, max-content); color: var(--text-secondary); font-size: 13px; }
.workbench-terms-panel__group-title { color: var(--text-muted); font-size: 12px; text-transform: uppercase; }
.workbench-terms-panel__item { display: grid; gap: 6px; padding: 12px 14px; border: 1px solid #d7e4e5; border-radius: 8px; background: #fff; box-shadow: 0 4px 12px rgba(29, 59, 67, 0.04); }
.workbench-terms-panel__item.is-hit { border-color: rgba(13, 122, 104, 0.28); background: linear-gradient(180deg, #f2fbf7, #fff); }
.workbench-terms-panel__item strong { color: var(--text-primary); font-size: 14px; }
.workbench-terms-panel__item span { color: var(--text-secondary); font-size: 13px; line-height: 1.6; }
.workbench-terms-panel__item .workbench-terms-panel__target { color: #2f5f59; font-size: 14px; font-weight: 600; line-height: 1.5; }
.workbench-terms-panel__item small,
.workbench-terms-panel__item p { margin: 0; color: var(--text-muted); font-size: 12px; }
.workbench-terms-panel__item small { color: #0b86ae; }
.workbench-terms-panel__entry-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  font-size: 12px;
}
.workbench-terms-panel__entry-meta span:not(.tag) {
  color: #8a9a9c;
  font-size: 11px;
  line-height: 1.3;
}
.workbench-terms-panel__online-tag {
  color: #087a68;
  background: #e6f7f2;
}
.workbench-terms-panel__online-add{
  border-color: #0d9f83;
  background: #0d9f83;
  color: #fff;
}
.workbench-terms-panel__online-add:hover:not(:disabled) {
  background: #087a68;
  border-color: #087a68;
}
.workbench-terms-panel__online-add.is-added,
.workbench-terms-panel__online-add.is-added:disabled {
  border-color: #c8d5d6;
  background: #eef3f3;
  color: #718184;
}
.workbench-terms-panel__online-revoke {
  border-color: #e0b7b7;
  background: #fff8f8;
  color: #a74444;
}
.workbench-terms-panel__online-revoke:hover:not(:disabled) {
  border-color: #c96b6b;
  background: #fff0f0;
  color: #8f3030;
}
.workbench-terms-panel__item-actions { margin-top: 4px; }
.workbench-terms-panel__load-more {
  padding: 8px 0;
  color: var(--text-muted);
  font-size: 12px;
  text-align: center;
}
</style>
