<script setup lang="ts">
import axios from 'axios'
import { Pencil, Save, Trash2, X } from 'lucide-vue-next'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import type {
  ReferenceExactMatch,
  ReferenceFuzzyMatch,
  ReferenceMatchResult,
  ReferenceTermMatch,
  Segment,
  TermEntryRecord,
  TermMatch,
  TMEntryRecord,
  TMMatchCandidate,
  WorkbenchQAResultItem,
} from '../types/api'
import { http } from '../api/http'
import { useConfirm } from '../composables/useConfirm'
import { pushToast } from '../composables/useToast'
import { useAuthStore } from '../stores/auth'
import { hasTermTextMatch } from '../utils/termMatching'
import DiffText from './DiffText.vue'

type EditableMatchKind = 'tm' | 'term'

type MatchedTermDisplay = {
  id: string
  term_base_id: string | null
  term_base_name: string | null
  source_text: string
  target_text: string
  creator_id: string | null
  creator_name: string | null
  last_modified_by_id: string | null
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
  editableKind: EditableMatchKind | null
  entryId: string | null
  creatorId: string | null
  canEdit: boolean
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
  (e: 'resourceEntryChanged', kind: EditableMatchKind): void
}>()

const { t } = useI18n()
const authStore = useAuthStore()
const confirm = useConfirm()

const tmCandidates = ref<TMMatchCandidate[]>([])
const loadingCandidates = ref(false)
const selectedRowId = ref('')
const editingRowId = ref('')
const editSourceText = ref('')
const editTargetText = ref('')
const editMessage = ref('')
const savingEntry = ref(false)
const deletingEntry = ref(false)
const hiddenTermEntryIds = ref<Set<string>>(new Set())
const termEntryOverrides = ref<Record<string, TermEntryRecord>>({})
let candidateRequestId = 0
const CANDIDATE_DEBOUNCE_MS = 250
const CANDIDATE_CACHE_TTL_MS = 30_000
const FUZZY_DISPLAY_SCORE_CEILING = 0.99
type CandidateCacheEntry = {
  candidates: TMMatchCandidate[]
  expiresAt: number
}
const tmCandidateCache = new Map<string, CandidateCacheEntry>()
let candidateLoadTimer: number | null = null

const termEntryById = computed(() => {
  return new Map(props.termEntries.map((entry) => [entry.id, entry]))
})

const currentUserId = computed(() => authStore.user?.id || '')

const matchedTerms = computed<MatchedTermDisplay[]>(() => {
  if (!props.activeSourceText) return []
  const hiddenIds = hiddenTermEntryIds.value
  const overrides = termEntryOverrides.value

  if (props.termMatches.length > 0) {
    return props.termMatches.map((match): MatchedTermDisplay | null => {
      if (hiddenIds.has(match.term_id)) return null
      const entry = termEntryById.value.get(match.term_id)
      const override = overrides[match.term_id]
      const sourceText = override?.source_text ?? entry?.source_text ?? match.source_text
      const targetText = override?.target_text ?? entry?.target_text ?? match.target_text
      if (!hasTermTextMatch(props.activeSourceText, sourceText)) return null
      return {
        id: match.term_id,
        term_base_id: override?.term_base_id ?? match.term_base_id ?? entry?.term_base_id ?? null,
        term_base_name: match.term_base_name ?? null,
        source_text: sourceText,
        target_text: targetText,
        creator_id: override?.creator_id ?? entry?.creator_id ?? match.creator_id ?? null,
        creator_name: override?.creator_name ?? entry?.creator_name ?? match.creator_name ?? null,
        last_modified_by_id: override?.last_modified_by_id ?? entry?.last_modified_by_id ?? match.last_modified_by_id ?? null,
        last_modified_by_name: override?.last_modified_by_name ?? entry?.last_modified_by_name ?? match.last_modified_by_name ?? null,
        created_at: override?.created_at ?? entry?.created_at ?? match.created_at ?? null,
        updated_at: override?.updated_at ?? entry?.updated_at ?? match.updated_at ?? null,
      }
    }).filter((term): term is MatchedTermDisplay => term !== null)
  }

  return props.termEntries
    .filter((entry) => !hiddenIds.has(entry.id))
    .slice()
    .map((entry) => termEntryOverrides.value[entry.id] || entry)
    .filter((entry) => hasTermTextMatch(props.activeSourceText, entry.source_text))
    .sort((left, right) => right.source_text.length - left.source_text.length)
    .map((entry) => {
      return {
        id: entry.id,
        term_base_id: entry.term_base_id,
        term_base_name: props.termBaseId === entry.term_base_id ? props.termBaseName : null,
        source_text: entry.source_text,
        target_text: entry.target_text,
        creator_id: entry.creator_id ?? null,
        creator_name: entry.creator_name,
        last_modified_by_id: entry.last_modified_by_id ?? null,
        last_modified_by_name: entry.last_modified_by_name ?? null,
        created_at: entry.created_at,
        updated_at: entry.updated_at,
      }
    })
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
      ...buildReadonlyRowMeta(),
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

function buildTMCandidateCacheKey(segmentId: string, fileRecordId: string, sourceText: string) {
  return [fileRecordId, segmentId, sourceText].join(':')
}

function clearCandidateLoadTimer() {
  if (candidateLoadTimer !== null) {
    window.clearTimeout(candidateLoadTimer)
    candidateLoadTimer = null
  }
}

function scheduleTMCandidatesLoad(segmentId: string, fileRecordId: string, sourceText: string) {
  const requestId = ++candidateRequestId
  clearCandidateLoadTimer()
  candidateLoadTimer = window.setTimeout(() => {
    candidateLoadTimer = null
    void loadTMCandidates(segmentId, fileRecordId, sourceText, requestId)
  }, CANDIDATE_DEBOUNCE_MS)
}

async function loadTMCandidates(
  segmentId: string,
  fileRecordId: string,
  sourceText: string,
  requestId: number,
) {
  if (!segmentId || !fileRecordId) {
    tmCandidates.value = []
    loadingCandidates.value = false
    return
  }

  const cacheKey = buildTMCandidateCacheKey(segmentId, fileRecordId, sourceText)
  const cached = tmCandidateCache.get(cacheKey)
  if (cached && cached.expiresAt > Date.now()) {
    if (requestId !== candidateRequestId) return
    tmCandidates.value = cached.candidates
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
      const candidates = data.candidates || []
      tmCandidates.value = candidates
      tmCandidateCache.set(cacheKey, {
        candidates,
        expiresAt: Date.now() + CANDIDATE_CACHE_TTL_MS,
      })
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
}

watch(
  () => [props.segment?.id ?? '', props.fileRecordId ?? '', props.segment?.source_text ?? ''],
  ([segmentId, fileRecordId, sourceText]) => {
    if (!segmentId || !fileRecordId) {
      candidateRequestId += 1
      clearCandidateLoadTimer()
      tmCandidates.value = []
      loadingCandidates.value = false
      return
    }
    scheduleTMCandidatesLoad(segmentId, fileRecordId, sourceText)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  candidateRequestId += 1
  clearCandidateLoadTimer()
})

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

watch(selectedRowId, () => {
  resetEntryEdit()
})

function buildReadonlyRowMeta() {
  return {
    editableKind: null,
    entryId: null,
    creatorId: null,
    canEdit: false,
  }
}

function buildEditableRowMeta(
  kind: EditableMatchKind,
  entryId: string | null | undefined,
  creatorId: string | null | undefined,
) {
  const normalizedEntryId = entryId || null
  const normalizedCreatorId = creatorId || null
  return {
    editableKind: normalizedEntryId ? kind : null,
    entryId: normalizedEntryId,
    creatorId: normalizedCreatorId,
    canEdit: Boolean(normalizedEntryId && normalizedCreatorId && normalizedCreatorId === currentUserId.value),
  }
}

function buildReferenceExactRow(match: ReferenceExactMatch): MatchDisplayRow {
  const score = Number.isFinite(match.similarity) ? match.similarity : 1
  return {
    id: `ref-exact-${match.segment_id}`,
    ...buildReadonlyRowMeta(),
    sourceLabel: '参阅材料',
    badge: formatScore(score),
    tone: getMatchScoreTone(score, true),
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
    ...buildReadonlyRowMeta(),
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
  const exactTextMatch = isExactTextMatch(props.activeSourceText, candidate.source_text)
  const displayScore = normalizeDisplayScore(
    candidate.score,
    exactTextMatch,
    props.activeSourceText,
    candidate.source_text,
  )
  return {
    id: `tm-${candidate.entry_id || index}`,
    ...buildEditableRowMeta('tm', candidate.entry_id ?? null, candidate.creator_id ?? null),
    sourceLabel: collectionName,
    badge: formatScore(displayScore),
    tone: getMatchScoreTone(displayScore ?? 0, exactTextMatch),
    sourceText: candidate.source_text,
    compareText: props.activeSourceText || candidate.source_text,
    targetText: candidate.target_text,
    detailItems: buildDetailItems([
      ['记忆库名称', collectionName],
      ['匹配率', formatScore(displayScore)],
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
    ...buildEditableRowMeta('term', term.id, term.creator_id),
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
    ...buildReadonlyRowMeta(),
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

function normalizeMatchText(value: string | null | undefined): string {
  return (value || '').trim().replace(/\s+/g, ' ').replace(/[\u3002\uff01\uff1f!?.]+$/u, '')
}

function compactMatchCore(value: string | null | undefined): string {
  return normalizeMatchText(value).replace(/[^\w\u4e00-\u9fff]+/gu, '')
}

function isShortStructuralFragment(value: string | null | undefined): boolean {
  const core = compactMatchCore(value)
  return Boolean(core && core.length <= 4 && /^(?:\d+[A-Za-z]?|[A-Za-z]|[ivxlcdmIVXLCDM]{1,4})$/.test(core))
}

function isExactTextMatch(sourceText: string | null | undefined, matchedSourceText: string | null | undefined): boolean {
  const normalizedSource = normalizeMatchText(sourceText)
  const normalizedMatchedSource = normalizeMatchText(matchedSourceText)
  return Boolean(normalizedSource && normalizedSource === normalizedMatchedSource)
}

function normalizedSequenceRatio(left: string, right: string): number {
  if (left === right) return 1
  const rows = left.length + 1
  const cols = right.length + 1
  const lengths = Array.from({ length: rows }, () => Array<number>(cols).fill(0))
  for (let row = 1; row < rows; row += 1) {
    for (let col = 1; col < cols; col += 1) {
      lengths[row][col] = left[row - 1] === right[col - 1]
        ? lengths[row - 1][col - 1] + 1
        : Math.max(lengths[row - 1][col], lengths[row][col - 1])
    }
  }
  return (2 * lengths[left.length][right.length]) / Math.max(left.length + right.length, 1)
}

function capShortStructuralDisplayScore(
  score: number,
  sourceText: string | null | undefined,
  matchedSourceText: string | null | undefined,
) {
  const normalizedSource = normalizeMatchText(sourceText)
  const normalizedMatchedSource = normalizeMatchText(matchedSourceText)
  if (!normalizedSource || !normalizedMatchedSource || normalizedSource === normalizedMatchedSource) {
    return score
  }
  const sourceCore = compactMatchCore(normalizedSource)
  const matchedCore = compactMatchCore(normalizedMatchedSource)
  if (
    sourceCore
    && sourceCore === matchedCore
    && (isShortStructuralFragment(normalizedSource) || isShortStructuralFragment(normalizedMatchedSource))
  ) {
    return Math.min(score, normalizedSequenceRatio(normalizedSource, normalizedMatchedSource), 0.79)
  }
  return score
}

function normalizeDisplayScore(
  score: number | null | undefined,
  exactTextMatch = false,
  sourceText?: string | null,
  matchedSourceText?: string | null,
): number | null {
  if (score === null || score === undefined || !Number.isFinite(score)) return null
  const safeScore = Math.min(Math.max(score, 0), 1)
  const cappedScore = capShortStructuralDisplayScore(safeScore, sourceText, matchedSourceText)
  return exactTextMatch ? cappedScore : Math.min(cappedScore, FUZZY_DISPLAY_SCORE_CEILING)
}

function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return ''
  return `${Math.round(score * 100)}%`
}

function getMatchScoreTone(score: number, exactTextMatch = false): MatchRowTone {
  if (exactTextMatch && score >= 1.0) return 'exact'
  if (score >= 0.8) return 'high'
  if (score >= 0.6) return 'medium'
  return 'low'
}

function selectMatchRow(row: MatchDisplayRow) {
  selectedRowId.value = row.id
}

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

function resetEntryEdit() {
  editingRowId.value = ''
  editSourceText.value = ''
  editTargetText.value = ''
  editMessage.value = ''
}

function startEditEntry(row: MatchDisplayRow) {
  if (!row.canEdit || !row.entryId || !row.editableKind) return
  editingRowId.value = row.id
  editSourceText.value = row.sourceText
  editTargetText.value = row.targetText
  editMessage.value = ''
}

function setHiddenTermEntry(entryId: string) {
  hiddenTermEntryIds.value = new Set([...hiddenTermEntryIds.value, entryId])
}

function setTermEntryOverride(entry: TermEntryRecord) {
  termEntryOverrides.value = {
    ...termEntryOverrides.value,
    [entry.id]: entry,
  }
}

function removeTMCandidate(entryId: string) {
  tmCandidates.value = tmCandidates.value.filter((candidate) => candidate.entry_id !== entryId)
  tmCandidateCache.clear()
}

async function refreshCurrentTMCandidates() {
  const segmentId = props.segment?.id || ''
  const fileRecordId = props.fileRecordId || ''
  const sourceText = props.segment?.source_text || ''
  if (!segmentId || !fileRecordId) return
  tmCandidateCache.clear()
  const requestId = ++candidateRequestId
  await loadTMCandidates(segmentId, fileRecordId, sourceText, requestId)
}

async function saveEntryEdit(row: MatchDisplayRow) {
  if (!row.entryId || !row.editableKind || !row.canEdit || savingEntry.value) return
  const sourceText = editSourceText.value.trim()
  const targetText = editTargetText.value.trim()
  if (!sourceText || !targetText) {
    editMessage.value = '原文和译文不能为空。'
    return
  }

  savingEntry.value = true
  editMessage.value = ''
  try {
    if (row.editableKind === 'tm') {
      await http.put<TMEntryRecord>(`/translation-memory/entries/${row.entryId}`, {
        source_text: sourceText,
        target_text: targetText,
      })
      await refreshCurrentTMCandidates()
    } else {
      const { data } = await http.put<TermEntryRecord>(`/term-entries/${row.entryId}`, {
        source_text: sourceText,
        target_text: targetText,
      })
      setTermEntryOverride(data)
      emit('resourceEntryChanged', 'term')
    }
    resetEntryEdit()
    pushToast({ tone: 'success', message: '条目已更新。' })
  } catch (error) {
    editMessage.value = getErrorMessage(error, '条目更新失败。')
  } finally {
    savingEntry.value = false
  }
}

async function deleteEntry(row: MatchDisplayRow) {
  if (!row.entryId || !row.editableKind || !row.canEdit || deletingEntry.value) return
  const label = row.editableKind === 'tm' ? 'TM 条目' : '术语条目'
  const confirmed = await confirm({
    title: `删除${label}`,
    message: `确定删除这条${label}吗？此操作无法撤销。`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) return

  deletingEntry.value = true
  editMessage.value = ''
  try {
    if (row.editableKind === 'tm') {
      await http.delete(`/translation-memory/entries/${row.entryId}`)
      removeTMCandidate(row.entryId)
    } else {
      await http.delete(`/term-entries/${row.entryId}`)
      setHiddenTermEntry(row.entryId)
      emit('resourceEntryChanged', 'term')
    }
    resetEntryEdit()
    pushToast({ tone: 'success', message: '条目已删除。' })
  } catch (error) {
    editMessage.value = getErrorMessage(error, '条目删除失败。')
  } finally {
    deletingEntry.value = false
  }
}

function applyMatchRow(row: MatchDisplayRow | null) {
  if (!row || !row.applyText) return

  if (row.applyMode === 'replace') {
    emit('replaceText', row.applyText)
    return
  }

  emit('appendText', row.applyText)
}

function applyMatchAtIndex(index: number) {
  const row = matchRows.value[index] ?? null
  if (!row?.applyText) {
    return false
  }

  selectedRowId.value = row.id
  applyMatchRow(row)
  return true
}

defineExpose({
  applyMatchAtIndex,
})
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

        <div v-if="editingRowId === selectedMatchRow.id" class="match-detail__edit-form">
          <label class="match-detail__edit-field">
            <span>原文</span>
            <textarea
              v-model="editSourceText"
              class="match-detail__edit-control"
              rows="3"
              :disabled="savingEntry || deletingEntry"
            />
          </label>
          <label class="match-detail__edit-field">
            <span>译文</span>
            <textarea
              v-model="editTargetText"
              class="match-detail__edit-control"
              rows="3"
              :disabled="savingEntry || deletingEntry"
            />
          </label>
          <p v-if="editMessage" class="match-detail__message">{{ editMessage }}</p>
        </div>

        <div v-else class="match-detail__texts">
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
          <template v-if="editingRowId === selectedMatchRow.id">
            <button
              class="button match-detail__action"
              type="button"
              :disabled="savingEntry || deletingEntry"
              title="保存"
              @click="saveEntryEdit(selectedMatchRow)"
            >
              <Save :size="14" />
              保存
            </button>
            <button
              class="button match-detail__action"
              type="button"
              :disabled="savingEntry || deletingEntry"
              title="取消"
              @click="resetEntryEdit"
            >
              <X :size="14" />
              取消
            </button>
          </template>
          <template v-else>
            <button
              v-if="selectedMatchRow.canEdit"
              class="button match-detail__action"
              type="button"
              :disabled="savingEntry || deletingEntry"
              title="编辑条目"
              @click="startEditEntry(selectedMatchRow)"
            >
              <Pencil :size="14" />
              编辑
            </button>
            <button
              v-if="selectedMatchRow.canEdit"
              class="button match-detail__action match-detail__action--danger"
              type="button"
              :disabled="savingEntry || deletingEntry"
              title="删除条目"
              @click="deleteEntry(selectedMatchRow)"
            >
              <Trash2 :size="14" />
              删除
            </button>
          <button
            class="button match-detail__apply"
            type="button"
            :disabled="!selectedMatchRow.applyText"
            @click="applyMatchRow(selectedMatchRow)"
          >
            应用
          </button>
          </template>
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

.match-detail__edit-form {
  display: grid;
  gap: 8px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #e2e8ee;
}

.match-detail__edit-field {
  display: grid;
  gap: 4px;
  color: #223843;
  font-size: 12px;
  font-weight: 700;
}

.match-detail__edit-control {
  width: 100%;
  min-height: 66px;
  resize: vertical;
  padding: 6px 8px;
  border: 1px solid #b8cfd3;
  border-radius: 4px;
  background: #ffffff;
  color: #23343f;
  font: inherit;
  font-weight: 400;
  line-height: 1.5;
}

.match-detail__edit-control:focus {
  border-color: #40a391;
  outline: none;
  box-shadow: 0 0 0 2px rgba(64, 163, 145, 0.16);
}

.match-detail__message {
  margin: 0;
  color: #a43a3d;
  font-size: 12px;
  line-height: 1.45;
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
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
  padding-top: 8px;
}

.match-detail__action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 28px;
  padding: 4px 9px;
  border-color: #a9c7c9;
  border-radius: 4px;
  background: #ffffff;
  color: #255661;
  font-size: 12px;
  font-weight: 700;
  box-shadow: none;
}

.match-detail__action:hover:not(:disabled),
.match-detail__action:focus-visible {
  border-color: #40a391;
  color: #0b6658;
  outline: none;
}

.match-detail__action--danger {
  border-color: #e1b7ba;
  color: #a43a3d;
}

.match-detail__action--danger:hover:not(:disabled),
.match-detail__action--danger:focus-visible {
  border-color: #c95c62;
  color: #8e2f32;
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
