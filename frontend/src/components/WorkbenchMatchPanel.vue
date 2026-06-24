<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import type {
  ReferenceExactMatch,
  ReferenceFuzzyMatch,
  ReferenceMatchResult,
  ReferenceTermMatch,
  Segment,
  TermEntryRecord,
  TermMatch,
  TMMatchCandidate,
  WorkbenchQAResultItem,
} from '../types/api'
import { http } from '../api/http'
import { hasTermTextMatch } from '../utils/termMatching'
import DiffText from './DiffText.vue'

type MatchedTermDisplay = {
  id: string
  term_base_id: string | null
  term_base_name: string | null
  source_text: string
  target_text: string
  creator_name: string | null
  last_modified_by_name: string | null
  created_at: string | null
  updated_at: string | null
}

type MatchRowTone = 'exact' | 'high' | 'medium' | 'low' | 'term'
type MatchApplyMode = 'replace' | 'append'

type MatchDetailItem = {
  label: string
  value: string
}

type MatchDisplayRow = {
  id: string
  sourceLabel: string
  badge: string
  tone: MatchRowTone
  sourceText: string
  compareText: string | null
  targetText: string
  detailItems: MatchDetailItem[]
  sourceTitle: string
  targetTitle: string
  applyText: string
  applyMode: MatchApplyMode
}

const props = defineProps<{
  segment: Segment | null
  collectionId: string | null
  collectionName: string | null
  termBaseId: string | null
  termBaseName: string | null
  termEntries: TermEntryRecord[]
  termMatches: TermMatch[]
  qaTermItems: WorkbenchQAResultItem[]
  activeSourceText: string
  fileRecordId: string | null
  referenceMatchResult: ReferenceMatchResult | null
}>()

const emit = defineEmits<{
  (e: 'replaceText', text: string): void
  (e: 'appendText', text: string): void
}>()

const { t } = useI18n()

const tmCandidates = ref<TMMatchCandidate[]>([])
const loadingCandidates = ref(false)
const selectedRowId = ref('')
let candidateRequestId = 0

const termEntryById = computed(() => {
  return new Map(props.termEntries.map((entry) => [entry.id, entry]))
})

const matchedTerms = computed<MatchedTermDisplay[]>(() => {
  if (!props.activeSourceText) return []

  if (props.termMatches.length > 0) {
    return props.termMatches.map((match) => {
      const entry = termEntryById.value.get(match.term_id)
      return {
        id: match.term_id,
        term_base_id: match.term_base_id ?? entry?.term_base_id ?? null,
        term_base_name: match.term_base_name ?? null,
        source_text: match.source_text,
        target_text: match.target_text,
        creator_name: entry?.creator_name ?? null,
        last_modified_by_name: entry?.last_modified_by_name ?? null,
        created_at: entry?.created_at ?? null,
        updated_at: entry?.updated_at ?? null,
      }
    })
  }

  return props.termEntries
    .filter((entry) => hasTermTextMatch(props.activeSourceText, entry.source_text))
    .slice()
    .sort((left, right) => right.source_text.length - left.source_text.length)
    .map((entry) => ({
      id: entry.id,
      term_base_id: entry.term_base_id,
      term_base_name: props.termBaseId === entry.term_base_id ? props.termBaseName : null,
      source_text: entry.source_text,
      target_text: entry.target_text,
      creator_name: entry.creator_name,
      last_modified_by_name: entry.last_modified_by_name ?? null,
      created_at: entry.created_at,
      updated_at: entry.updated_at,
    }))
})

const currentSegmentRefExactMatch = computed<ReferenceExactMatch | null>(() => {
  if (!props.referenceMatchResult || !props.segment) return null
  const sentenceId = props.segment.sentence_id
  return props.referenceMatchResult.exact_matches.find((match) => match.segment_id === sentenceId) || null
})

const currentSegmentRefFuzzyMatches = computed<ReferenceFuzzyMatch[]>(() => {
  if (!props.referenceMatchResult || !props.segment) return []
  const sentenceId = props.segment.sentence_id
  return props.referenceMatchResult.fuzzy_matches.filter((match) => match.segment_id === sentenceId)
})

const currentSegmentRefTermMatch = computed<ReferenceTermMatch | null>(() => {
  if (!props.referenceMatchResult || !props.segment) return null
  const sentenceId = props.segment.sentence_id
  return props.referenceMatchResult.term_matches.find((match) => match.segment_id === sentenceId) || null
})

const matchRows = computed<MatchDisplayRow[]>(() => {
  const rows: MatchDisplayRow[] = []

  const exactMatch = currentSegmentRefExactMatch.value
  if (exactMatch) {
    rows.push(buildReferenceExactRow(exactMatch))
  }

  currentSegmentRefFuzzyMatches.value.forEach((match, index) => {
    rows.push(buildReferenceFuzzyRow(match, index))
  })

  currentSegmentRefTermMatch.value?.terms.forEach((term, index) => {
    rows.push({
      id: `ref-term-${index}-${term.source}`,
      sourceLabel: '参阅材料',
      badge: 'TB',
      tone: 'term',
      sourceText: term.source,
      compareText: null,
      targetText: term.target,
      detailItems: buildDetailItems([
        ['匹配类型', '参考术语匹配'],
        ['来源文件', term.source_file || currentSegmentRefTermMatch.value?.source_file],
        ['分类', term.category],
      ]),
      sourceTitle: '术语原文',
      targetTitle: '术语译文',
      applyText: term.target,
      applyMode: 'append',
    })
  })

  tmCandidates.value.forEach((candidate, index) => {
    rows.push(buildTMRow(candidate, index))
  })

  const termRowKeys = new Set<string>()
  matchedTerms.value.forEach((term, index) => {
    termRowKeys.add(buildTermRowKey(term.source_text, term.target_text))
    rows.push(buildTermRow(term, index))
  })

  props.qaTermItems.forEach((item, index) => {
    const rowKey = buildTermRowKey(item.source_term, item.expected_target_term)
    if (!rowKey || termRowKeys.has(rowKey)) return
    termRowKeys.add(rowKey)
    rows.push(buildQATermRow(item, index))
  })

  return rows
})

const selectedMatchRow = computed(() => {
  return matchRows.value.find((row) => row.id === selectedRowId.value) ?? matchRows.value[0] ?? null
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

watch(
  matchRows,
  (rows) => {
    if (rows.length === 0) {
      selectedRowId.value = ''
      return
    }

    if (!rows.some((row) => row.id === selectedRowId.value)) {
      selectedRowId.value = rows[0].id
    }
  },
  { immediate: true },
)

function buildReferenceExactRow(match: ReferenceExactMatch): MatchDisplayRow {
  const score = Number.isFinite(match.similarity) ? match.similarity : 1
  return {
    id: `ref-exact-${match.segment_id}`,
    sourceLabel: '参阅材料',
    badge: formatScore(score),
    tone: getMatchScoreTone(score),
    sourceText: match.source,
    compareText: null,
    targetText: match.target,
    detailItems: buildDetailItems([
      ['匹配类型', '参考精确匹配'],
      ['来源文件', match.source_file],
      ['匹配率', formatScore(score)],
    ]),
    sourceTitle: '匹配原文',
    targetTitle: '参考译文',
    applyText: match.target,
    applyMode: 'replace',
  }
}

function buildReferenceFuzzyRow(match: ReferenceFuzzyMatch, index: number): MatchDisplayRow {
  return {
    id: `ref-fuzzy-${index}-${match.segment_id}`,
    sourceLabel: '参阅材料',
    badge: formatScore(match.similarity),
    tone: getMatchScoreTone(match.similarity),
    sourceText: match.matched_source,
    compareText: props.activeSourceText || match.source,
    targetText: match.target,
    detailItems: buildDetailItems([
      ['匹配类型', '参考模糊匹配'],
      ['来源文件', match.source_file],
      ['匹配率', formatScore(match.similarity)],
    ]),
    sourceTitle: '差异对比',
    targetTitle: '参考译文',
    applyText: match.target,
    applyMode: 'replace',
  }
}

function buildTMRow(candidate: TMMatchCandidate, index: number): MatchDisplayRow {
  const collectionName = candidate.collection_name || props.collectionName || '记忆库'
  return {
    id: `tm-${index}`,
    sourceLabel: collectionName,
    badge: formatScore(candidate.score),
    tone: getMatchScoreTone(candidate.score),
    sourceText: candidate.source_text,
    compareText: props.activeSourceText || candidate.source_text,
    targetText: candidate.target_text,
    detailItems: buildDetailItems([
      ['记忆库名称', collectionName],
      ['匹配率', formatScore(candidate.score)],
      ['创建人', candidate.creator_name],
      ['创建时间', formatDateTime(candidate.created_at)],
      ['最近更新时间', formatDateTime(candidate.updated_at)],
    ]),
    sourceTitle: 'TM 差异',
    targetTitle: 'TM 译文参考',
    applyText: candidate.target_text,
    applyMode: 'replace',
  }
}

function buildTermRow(term: MatchedTermDisplay, index: number): MatchDisplayRow {
  const termBaseName = term.term_base_name || props.termBaseName || '术语库'
  return {
    id: `term-${index}-${term.id}`,
    sourceLabel: termBaseName,
    badge: 'TB',
    tone: 'term',
    sourceText: term.source_text,
    compareText: null,
    targetText: term.target_text,
    detailItems: buildDetailItems([
      ['匹配类型', '术语库命中'],
      ['术语库名称', termBaseName],
      ['创建人', term.creator_name],
      ['创建时间', formatDateTime(term.created_at)],
      ['最近更新人', term.last_modified_by_name],
      ['最近更新时间', formatDateTime(term.updated_at)],
    ]),
    sourceTitle: '术语原文',
    targetTitle: '术语译文',
    applyText: term.target_text,
    applyMode: 'append',
  }
}

function buildQATermRow(item: WorkbenchQAResultItem, index: number): MatchDisplayRow {
  const sourceTerm = item.source_term || item.source_text
  const targetTerm = item.expected_target_term || item.suggestion || item.target_text
  const termBaseName = item.term_base_name || props.termBaseName || '术语库'
  return {
    id: `qa-term-${index}-${item.id}`,
    sourceLabel: termBaseName,
    badge: 'TB',
    tone: 'term',
    sourceText: sourceTerm,
    compareText: null,
    targetText: targetTerm,
    detailItems: buildDetailItems([
      ['匹配类型', 'QA 术语命中'],
      ['术语库名称', termBaseName],
      ['句段编号', item.sentence_id],
      ['状态', item.ignored ? '已忽略' : '待处理'],
      ['创建时间', formatDateTime(item.created_at)],
    ]),
    sourceTitle: '术语原文',
    targetTitle: '术语译文',
    applyText: targetTerm,
    applyMode: 'append',
  }
}

function buildTermRowKey(sourceText: string | null | undefined, targetText: string | null | undefined) {
  const source = `${sourceText ?? ''}`.trim().toLocaleLowerCase()
  const target = `${targetText ?? ''}`.trim().toLocaleLowerCase()
  return source && target ? `${source}\u0000${target}` : ''
}

function buildDetailItems(items: Array<[string, string | null | undefined]>): MatchDetailItem[] {
  return items
    .map(([label, value]) => ({ label, value: `${value ?? ''}`.trim() }))
    .filter((item) => item.value.length > 0)
}

function formatDateTime(isoString: string | null | undefined): string {
  if (!isoString) return ''
  const date = new Date(isoString)
  if (Number.isNaN(date.getTime())) return isoString

  const pad = (value: number) => `${value}`.padStart(2, '0')
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
  ].join(' ')
}

function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return ''
  return `${Math.round(score * 100)}%`
}

function getMatchScoreTone(score: number): MatchRowTone {
  if (score >= 1.0) return 'exact'
  if (score >= 0.8) return 'high'
  if (score >= 0.6) return 'medium'
  return 'low'
}

function selectMatchRow(row: MatchDisplayRow) {
  selectedRowId.value = row.id
}

function applyMatchRow(row: MatchDisplayRow | null) {
  if (!row || !row.applyText) return

  if (row.applyMode === 'replace') {
    emit('replaceText', row.applyText)
    return
  }

  emit('appendText', row.applyText)
}
</script>

<template>
  <div class="match-panel">
    <div class="match-panel__header">
      <h3 class="match-panel__title">{{ t('matchPanel.title') }}</h3>
      <span v-if="matchRows.length > 0" class="match-panel__count">{{ matchRows.length }}</span>
    </div>

    <div class="match-panel__body">
      <div class="match-summary">
        <div v-if="loadingCandidates && matchRows.length === 0" class="match-summary-message">
          {{ t('matchPanel.loading') }}
        </div>

        <table v-else-if="matchRows.length > 0" class="match-summary-table" aria-label="匹配信息列表">
          <colgroup>
            <col class="match-summary-col--index" />
            <col class="match-summary-col--source" />
            <col class="match-summary-col--score" />
            <col class="match-summary-col--target" />
          </colgroup>
          <tbody>
            <tr
              v-for="(row, index) in matchRows"
              :key="row.id"
              class="match-summary-row"
              :class="{ 'is-selected': selectedMatchRow?.id === row.id }"
              tabindex="0"
              @click="selectMatchRow(row)"
              @dblclick="applyMatchRow(row)"
              @keydown.enter.prevent="selectMatchRow(row)"
              @keydown.space.prevent="selectMatchRow(row)"
            >
              <td class="match-summary-cell match-summary-cell--index">{{ index + 1 }}</td>
              <td class="match-summary-cell match-summary-cell--source" :title="row.sourceText">
                {{ row.sourceText }}
              </td>
              <td class="match-summary-cell match-summary-cell--score">
                <span class="match-summary-badge" :class="`match-summary-badge--${row.tone}`">
                  {{ row.badge }}
                </span>
              </td>
              <td class="match-summary-cell match-summary-cell--target" :title="row.targetText">
                {{ row.targetText }}
              </td>
            </tr>
          </tbody>
        </table>

        <div v-else class="match-summary-message">
          {{ t('matchPanel.noMatch') }}
        </div>
      </div>

      <div v-if="selectedMatchRow" class="match-detail">
        <div class="match-detail__fields">
          <div
            v-for="item in selectedMatchRow.detailItems"
            :key="item.label"
            class="match-detail__field"
          >
            <span class="match-detail__label">{{ item.label }}：</span>
            <span class="match-detail__value" :title="item.value">{{ item.value }}</span>
          </div>
        </div>

        <div class="match-detail__texts">
          <div class="match-detail__text-block">
            <div class="match-detail__text-label">{{ selectedMatchRow.sourceTitle }}</div>
            <div class="match-detail__text">
              <DiffText
                v-if="selectedMatchRow.compareText"
                :old-text="selectedMatchRow.sourceText"
                :new-text="selectedMatchRow.compareText"
              />
              <span v-else>{{ selectedMatchRow.sourceText || '—' }}</span>
            </div>
          </div>

          <div class="match-detail__text-block">
            <div class="match-detail__text-label">{{ selectedMatchRow.targetTitle }}</div>
            <div class="match-detail__text">{{ selectedMatchRow.targetText || '—' }}</div>
          </div>
        </div>

        <div class="match-detail__actions">
          <button
            class="button match-detail__apply"
            type="button"
            :disabled="!selectedMatchRow.applyText"
            @click="applyMatchRow(selectedMatchRow)"
          >
            应用
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.match-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  border: 0;
  background: linear-gradient(180deg, #ffffff 0%, #f7fbfb 100%);
  color: #1f2933;
}

.match-panel__header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 44px;
  padding: 10px 14px;
  border-bottom: 1px solid #d8e4e7;
  background: rgba(247, 251, 251, 0.92);
}

.match-panel__title {
  margin: 0;
  color: #223843;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.3;
}

.match-panel__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  min-height: 20px;
  padding: 0 6px;
  border: 1px solid #bfd5d8;
  border-radius: 4px;
  background: #ffffff;
  color: #21515b;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
}

.match-panel__body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.match-summary {
  flex: 1 1 auto;
  min-height: 180px;
  overflow: auto;
  background: #ffffff;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: #9fb8bd transparent;
}

.match-summary::-webkit-scrollbar,
.match-detail::-webkit-scrollbar,
.match-detail__text::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.match-summary::-webkit-scrollbar-track,
.match-detail::-webkit-scrollbar-track,
.match-detail__text::-webkit-scrollbar-track {
  background: transparent;
}

.match-summary::-webkit-scrollbar-thumb,
.match-detail::-webkit-scrollbar-thumb,
.match-detail__text::-webkit-scrollbar-thumb {
  border: 2px solid transparent;
  border-radius: 999px;
  background-color: #9fb8bd;
  background-clip: content-box;
}

.match-summary-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  color: #2d3b45;
  font-size: 12px;
  line-height: 1.3;
}

.match-summary-col--index {
  width: 28px;
}

.match-summary-col--source {
  width: 43%;
}

.match-summary-col--score {
  width: 58px;
}

.match-summary-col--target {
  width: auto;
}

.match-summary-row {
  cursor: pointer;
  outline: none;
}

.match-summary-row:focus-visible .match-summary-cell {
  box-shadow: inset 0 0 0 1px #40a391;
}

.match-summary-cell {
  height: 25px;
  min-height: 25px;
  padding: 0 8px;
  border-right: 1px solid #cfd8df;
  border-bottom: 1px solid #cfd8df;
  background: #fbfcfd;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}

.match-summary-row:nth-child(even) .match-summary-cell {
  background: #f7f9fa;
}

.match-summary-row:hover .match-summary-cell,
.match-summary-row.is-selected .match-summary-cell {
  background: #dcecff;
}

.match-summary-cell--index {
  padding: 0;
  background: #f4f8fb;
  color: #315366;
  text-align: center;
}

.match-summary-cell--source {
  color: #354650;
}

.match-summary-cell--score {
  padding: 0;
  text-align: center;
}

.match-summary-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 25px;
  color: #ffffff;
  font-size: 12px;
  font-weight: 800;
  line-height: 1;
}

.match-summary-badge--exact,
.match-summary-badge--high {
  background: #4fa873;
}

.match-summary-badge--medium {
  background: #d8b74e;
}

.match-summary-badge--low {
  background: #c95c62;
}

.match-summary-badge--term {
  background: #9b7ad8;
}

.match-summary-cell--target {
  color: #2f3d47;
}

.match-summary-message {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
  padding: 16px;
  color: var(--text-tertiary);
  font-size: 13px;
  text-align: center;
}

.match-detail {
  flex: 0 0 auto;
  max-height: 46%;
  padding: 10px 14px 14px;
  border-top: 1px solid #d8e4e7;
  background: #ffffff;
  overflow: auto;
  font-size: 12px;
}

.match-detail__fields {
  display: grid;
  gap: 3px;
}

.match-detail__field {
  display: flex;
  align-items: baseline;
  min-width: 0;
  min-height: 18px;
  line-height: 1.45;
}

.match-detail__label {
  flex: 0 0 auto;
  color: #223843;
  font-weight: 700;
}

.match-detail__value {
  min-width: 0;
  color: #2d3b45;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.match-detail__texts {
  display: grid;
  gap: 7px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #e2e8ee;
}

.match-detail__text-block {
  min-width: 0;
}

.match-detail__text-label {
  margin-bottom: 3px;
  color: #223843;
  font-weight: 700;
  line-height: 1.35;
}

.match-detail__text {
  max-height: 92px;
  color: #2d3b45;
  line-height: 1.55;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.match-detail__actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 8px;
}

.match-detail__apply {
  min-height: 28px;
  padding: 4px 12px;
  border-color: #a9c7c9;
  border-radius: 4px;
  background: linear-gradient(180deg, #ffffff, #edf8f6);
  color: #0b6658;
  font-size: 12px;
  font-weight: 700;
  box-shadow: none;
}

.match-detail__apply:hover:not(:disabled),
.match-detail__apply:focus-visible {
  border-color: #40a391;
  background: #ffffff;
  outline: none;
}

.match-detail__text :deep(.diff-text__segment--insert) {
  background: rgba(13, 122, 104, 0.14);
  color: #0b6b5b;
  box-shadow: inset 0 0 0 1px rgba(13, 122, 104, 0.18);
}

.match-detail__text :deep(.diff-text__segment--delete) {
  background: rgba(194, 59, 63, 0.14);
  color: #a43a3d;
  text-decoration-thickness: 1.5px;
  box-shadow: inset 0 0 0 1px rgba(194, 59, 63, 0.16);
}

@media (max-width: 1180px) {
  .match-panel {
    min-height: 500px;
  }

  .match-detail {
    max-height: none;
  }
}
</style>
