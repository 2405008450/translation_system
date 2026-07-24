import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import axios from 'axios'

import { http } from '../api/http'
import { fetchMergeViewSegmentPage, getMergeViewDetail } from '../api/mergeViews'
import { pushToast } from '../composables/useToast'
import { buildTranslatedTaskFilename } from '../constants/taskFiles'
import { translate } from '../i18n'
import { useAuthStore } from './auth'
import type {
  FileRecordDetail,
  FileRecordPreview,
  LLMGuidelineOptions,
  LLMMergeTarget,
  LLMProvider,
  LLMTranslateScope,
  MergeViewDetail,
  MergeViewSegmentGroup,
  ProjectSegmentSyncSummary,
  ProjectSyncDisableResult,
  RevisionDisplaySettings,
  Segment,
  SegmentPageResponse,
  SegmentRevisionEntry,
  SegmentStatusStats,
  SegmentUpdatePayload,
  SaveToTMStats,
  TermMatch,
  WorkflowProgress,
} from '../types/api'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'
import { consumeLLMStream } from '../utils/llmStream'

const DEFAULT_SEGMENT_PAGE_SIZE = 100
const MAX_SEGMENT_PAGE_SIZE = 500
const AUTO_SYNC_DELAY_MS = 1500
const CHANGE_POLL_INTERVAL_MS = 10000
const CHANGE_POLL_BURST_DELAYS_MS = [1200, 3000, 6000, 10000]
const SEGMENT_EVENT_RECONNECT_DELAY_MS = 15000
const EXPORT_POLL_INTERVAL_MS = 1200
const REVISION_TRACKING_STORAGE_KEY = 'workbench.revisionTrackingEnabled'
const DEFAULT_REVISION_INSERT_COLOR = '#2563eb'
const DEFAULT_REVISION_DELETE_COLOR = '#dc2626'
// 行内样式标记 ⟦n⟧ / ⟦/n⟧：非全局用于 test，全局用于 replace（避免 lastIndex 状态问题）
const FORMAT_MARK_RE = /⟦\s*\/?\s*\d+\s*⟧/
const FORMAT_MARK_RE_GLOBAL = /⟦\s*\/?\s*\d+\s*⟧/g
type SegmentBatchTarget = LLMMergeTarget
interface SegmentConfirmationOptions {
  rangeStart?: number | null
  rangeEnd?: number | null
}

interface FileExportTask {
  task_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  message?: string
  error?: string | null
}

function createEmptySegmentStatusStats(): SegmentStatusStats {
  return {
    total: 0,
    project_sync: 0,
    exact: 0,
    fuzzy: 0,
    none: 0,
    confirmed: 0,
    empty_target: 0,
  }
}

function buildMergeViewStatusStats(detail: MergeViewDetail | null): SegmentStatusStats {
  const totals = createEmptySegmentStatusStats()
  for (const file of detail?.files ?? []) {
    const stats = { ...createEmptySegmentStatusStats(), ...(file.status_stats || {}) }
    totals.total += Number(stats.total || file.total_segments || 0)
    totals.project_sync += Number(stats.project_sync || 0)
    totals.exact += Number(stats.exact || 0)
    totals.fuzzy += Number(stats.fuzzy || 0)
    totals.none += Number(stats.none || 0)
    totals.confirmed += Number(stats.confirmed || 0)
    totals.empty_target += Number(stats.empty_target || 0)
  }
  return totals
}

function hasEmptyTarget(value: string | null | undefined) {
  return value === null || value === undefined || value === ''
}

function normalizeMatchText(value: string | null | undefined) {
  return (value || '').trim().replace(/\s+/g, ' ').replace(/[\u3002\uff01\uff1f!?.]+$/u, '')
}

function compactMatchCore(value: string | null | undefined) {
  return normalizeMatchText(value).replace(/[^\w\u4e00-\u9fff]+/gu, '')
}

function isShortStructuralFragment(value: string | null | undefined) {
  const core = compactMatchCore(value)
  return Boolean(core && core.length <= 4 && /^(?:\d+[A-Za-z]?|[A-Za-z]|[ivxlcdmIVXLCDM]{1,4})$/.test(core))
}

function resolveUnconfirmedSegmentStatus(segment: Segment, _targetText = segment.target_text) {
  const sourceText = normalizeMatchText(segment.source_text)
  const displayText = normalizeMatchText(segment.display_text)
  const matchedSourceText = normalizeMatchText(segment.matched_source_text)
  const score = Number(segment.score || 0)
  if (sourceText && matchedSourceText && matchedSourceText === sourceText) {
    return 'exact'
  }
  if (
    displayText
    && matchedSourceText
    && matchedSourceText === displayText
    && !isShortStructuralFragment(segment.source_text)
  ) {
    return 'exact'
  }
  if (score > 0 || matchedSourceText) {
    return 'fuzzy'
  }
  return 'none'
}

function resolveSegmentMatchStatusForStats(segment: Segment) {
  if (segment.source === 'project_sync') {
    return 'project_sync'
  }
  return resolveUnconfirmedSegmentStatus(segment)
}

function resolveSegmentStatusAfterTargetUpdate(segment: Segment, targetText: string, confirm: boolean) {
  if (confirm) {
    return 'confirmed'
  }
  if (segment.status === 'confirmed' && (segment.target_text || '') === (targetText || '')) {
    return 'confirmed'
  }
  return resolveUnconfirmedSegmentStatus(segment, targetText)
}

function normalizePositiveInt(value: unknown, fallback: number) {
  const numberValue = Number(value)
  if (!Number.isFinite(numberValue)) {
    return fallback
  }
  return Math.max(1, Math.floor(numberValue))
}

function normalizePageSize(value: unknown, fallback = DEFAULT_SEGMENT_PAGE_SIZE) {
  return Math.min(MAX_SEGMENT_PAGE_SIZE, normalizePositiveInt(value, fallback))
}

function normalizeStringArray(value: unknown) {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .map((item) => String(item).trim())
    .filter(Boolean)
}

function serializeFilterArray(value: string[]) {
  return value.length > 0 ? value.join(',') : undefined
}

function isEditLockedError(error: unknown) {
  return axios.isAxiosError(error) && error.response?.status === 409
}

function getEditLockedMessage(error: unknown) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || translate('stores.segment.syncLocked'))
  }
  return translate('stores.segment.syncLocked')
}

function getInitialRevisionTrackingEnabled() {
  if (typeof window === 'undefined') {
    return false
  }
  return window.localStorage.getItem(REVISION_TRACKING_STORAGE_KEY) === '1'
}

function persistRevisionTrackingEnabled(enabled: boolean) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(REVISION_TRACKING_STORAGE_KEY, enabled ? '1' : '0')
  } catch {
    // 忽略浏览器隐私模式下的存储失败，当前会话仍可使用。
  }
}

function createDefaultRevisionSettings(fileRecordId = ''): RevisionDisplaySettings {
  return {
    id: null,
    file_record_id: fileRecordId,
    show_author_time: true,
    show_others_revisions: true,
    default_insert_color: DEFAULT_REVISION_INSERT_COLOR,
    default_delete_color: DEFAULT_REVISION_DELETE_COLOR,
    author_colors: {},
    updated_by: null,
    updated_at: null,
  }
}

export interface SegmentPageQuery {
  page?: number
  pageSize?: number
  scope?: string
  sourceQuery?: string
  targetQuery?: string
  sourceExclude?: string
  targetExclude?: string
  searchFuzzy?: boolean
  caseSensitive?: boolean
  statusFilters?: string[]
  matchFilters?: string[]
  sourceFilters?: string[]
  sourceContentFilters?: string[]
  workflowStepIds?: string[]
  includeStats?: boolean
}

interface SegmentChangeResponse {
  file_record_id: string
  server_time: string
  next_cursor?: string
  has_more?: boolean
  status_stats?: SegmentStatusStats | null
  workflow_progress?: WorkflowProgress[] | null
  segments: Segment[]
}

interface SegmentConflictResponse {
  sentence_id: string
  current_version: number
  attempted_version: number | null
  current_target_text: string
  conflict_source?: string | null
  conflict_last_modified_by_id?: string | null
  resolution?: string | null
}

export const useSegmentStore = defineStore('segment', () => {
  const authStore = useAuthStore()
  const fileRecord = ref<FileRecordDetail | null>(null)
  const segments = ref<Segment[]>([])
  const previewHtml = ref('')
  const previewSupported = ref(false)
  const activeSentenceId = ref<string | null>(null)
  const activeSourceText = ref('')
  const termMatchesMap = ref<Record<string, TermMatch[]>>({})
  const loading = ref(false)
  const loadingMoreSegments = ref(false)
  const loadingAllSegments = ref(false)
  const previewLoading = ref(false)
  const saving = ref(false)
  const llmRunning = ref(false)
  const syncMessage = ref(translate('stores.segment.syncIdle'))
  const llmMessage = ref(translate('stores.segment.llmReady'))
  const llmPlannedCount = ref(0)
  const llmProcessedCount = ref(0)
  const llmErrorCount = ref(0)
  const lastSyncedAt = ref<string | null>(null)
  const lastModifiedAt = ref<string | null>(null)
  const dirtyEntries = ref<Record<string, SegmentUpdatePayload>>({})
  const conflictEntries = ref<Record<string, string>>({})
  const previewUpdateToken = ref(0)
  const lastPreviewUpdatedSentenceId = ref<string | null>(null)
  const lastPreviewUpdatedText = ref('')
  const totalSegmentCount = ref(0)
  const matchedSegmentCount = ref(0)
  const segmentStatusStats = ref<SegmentStatusStats>(createEmptySegmentStatusStats())
  const currentPage = ref(1)
  const pageSize = ref(DEFAULT_SEGMENT_PAGE_SIZE)
  const segmentFilters = ref({
    scope: 'all',
    sourceQuery: '',
    targetQuery: '',
    sourceExclude: '',
    targetExclude: '',
    searchFuzzy: false,
    caseSensitive: false,
    statusFilters: [] as string[],
    matchFilters: [] as string[],
    sourceFilters: [] as string[],
    sourceContentFilters: [] as string[],
    workflowStepIds: [] as string[],
  })
  const saveToTMStats = ref<SaveToTMStats | null>(null)
  const revisionHistory = ref<Record<string, SegmentRevisionEntry[]>>({})
  const localRevisionDrafts = ref<Record<string, SegmentRevisionEntry>>({})
  const localRevisionBaselines = ref<Record<string, string>>({})
  const revisionTrackingEnabled = ref(getInitialRevisionTrackingEnabled())
  const revisionSettings = ref<RevisionDisplaySettings>(createDefaultRevisionSettings())

  // ---- 合并视图(merge-view)模式状态 ----
  // mergeViewId 非空即处于合并模式：segments 中每段带 file_record_id，
  // 句段对外 key 改用复合键 `${file_record_id}:${sentence_id}`，避免多文件
  // sentence_id 冲突。mergeViewId 为空时走单文件旧逻辑，回归风险为零。
  const mergeViewId = ref<string | null>(null)
  const mergeViewDetail = ref<MergeViewDetail | null>(null)
  const mergeViewGroups = ref<MergeViewSegmentGroup[]>([])
  /** 当前激活句段所属文件 id（侧栏/LLM/导出按此取上下文） */
  const activeFileRecordId = ref<string | null>(null)

  /** 复合键：合并模式下 `${file_record_id}:${sentence_id}`，单文件模式即 sentence_id */
  function buildSegmentKey(fileRecordId: string | null | undefined, sentenceId: string) {
    return mergeViewId.value && fileRecordId ? `${fileRecordId}:${sentenceId}` : sentenceId
  }
  /** 从复合键还原 sentence_id（去掉前缀） */
  function sentenceIdFromKey(key: string) {
    const idx = key.indexOf(':')
    return idx === -1 ? key : key.slice(idx + 1)
  }
  /** 从复合键还原 file_record_id */
  function fileRecordIdFromKey(key: string): string | null {
    const idx = key.indexOf(':')
    return idx === -1 ? null : key.slice(0, idx)
  }
  function fileRecordIdForSegment(segment: Segment): string | null {
    return segment.file_record_id ?? fileRecord.value?.id ?? null
  }
  function fileNameForFileId(fileId: string | null) {
    if (!fileId) {
      return null
    }
    return mergeViewDetail.value?.files.find((file) => file.id === fileId)?.filename ?? null
  }
  function withSegmentFileContext(segment: Segment, fileId: string | null): Segment {
    if (!mergeViewId.value || !fileId) {
      return segment
    }
    return {
      ...segment,
      file_record_id: segment.file_record_id ?? fileId,
      filename: segment.filename ?? fileNameForFileId(fileId) ?? undefined,
    }
  }
  function segmentKeyOf(segment: Segment) {
    return buildSegmentKey(fileRecordIdForSegment(segment), segment.sentence_id)
  }

  function isMachineSegmentSource(source: string | null | undefined) {
    return source === 'llm' || source === 'tm' || source === 'project_sync' || source === 'none' || !source
  }

  function currentUserId() {
    return authStore.user?.id ?? null
  }

  function isSensitiveRemoteConflict(
    source: string | null | undefined,
    lastModifiedById: string | null | undefined,
    incomingSource = 'manual',
  ) {
    const userId = currentUserId()
    if (userId && lastModifiedById && lastModifiedById === userId) {
      return false
    }
    if (incomingSource === 'manual' && isMachineSegmentSource(source)) {
      return false
    }
    return source === 'manual'
  }

  function isSameDirtyPayload(
    dirtyEntry: SegmentUpdatePayload | undefined,
    payload: SegmentUpdatePayload,
  ) {
    return Boolean(
      dirtyEntry
      && dirtyEntry.target_text === payload.target_text
      && (dirtyEntry.target_html || null) === (payload.target_html || null)
      && dirtyEntry.source === payload.source
      && Boolean(dirtyEntry.confirm) === Boolean(payload.confirm),
    )
  }

  function bumpDirtyBaseVersion(
    nextDirtyEntries: Record<string, SegmentUpdatePayload>,
    key: string,
    version: number | null | undefined,
  ) {
    const currentEntry = nextDirtyEntries[key]
    const nextVersion = Number(version || 0)
    if (!currentEntry || !nextVersion) {
      return
    }
    nextDirtyEntries[key] = {
      ...currentEntry,
      base_version: Math.max(Number(currentEntry.base_version || 0), nextVersion),
    }
  }

  function applyServerSegmentMetadata(segmentKey: string, remoteSegment: Segment) {
    const index = getSegmentIndex(segmentKey)
    if (index === -1) {
      return
    }
    segments.value[index] = {
      ...segments.value[index],
      version: remoteSegment.version,
      updated_at: remoteSegment.updated_at,
      last_modified_by_id: remoteSegment.last_modified_by_id,
      last_modified_by: remoteSegment.last_modified_by,
    }
  }

  const segmentIndexMap = new Map<string, number>()
  let syncTimer: number | null = null
  let syncPromise: Promise<boolean> | null = null
  let changePollTimer: number | null = null
  let changePollBurstTimers: number[] = []
  let segmentEventAbortController: AbortController | null = null
  let segmentEventReconnectTimer: number | null = null
  let segmentEventGeneration = 0
  let changeCursor: string | null = null
  let pollingChanges = false
  let loadMorePromise: Promise<boolean> | null = null
  let pendingSegmentPageQuery: SegmentPageQuery | null = null
  let previewPromise: Promise<void> | null = null
  let previewLoaded = false
  let previewCacheKey = ''
  let llmAbortController: AbortController | null = null
  let llmReader: ReadableStreamDefaultReader<Uint8Array> | null = null
  let llmAbortRequested = false

  const dirtyCount = computed(() => Object.keys(dirtyEntries.value).length)
  const conflictCount = computed(() => Object.keys(conflictEntries.value).length)
  const canExport = computed(() => Boolean(fileRecord.value?.can_export))
  const loadedSegmentCount = computed(() => segments.value.length)
  const currentPageStart = computed(() => (
    matchedSegmentCount.value === 0 ? 0 : (currentPage.value - 1) * pageSize.value + 1
  ))
  const currentPageEnd = computed(() => Math.min(currentPage.value * pageSize.value, matchedSegmentCount.value))
  const hasMoreSegments = computed(() => currentPageEnd.value < matchedSegmentCount.value)
  const allSegmentsLoaded = computed(() => (
    matchedSegmentCount.value === 0
      ? !loading.value && loadedSegmentCount.value === 0
      : currentPageEnd.value >= matchedSegmentCount.value
  ))
  const llmProgressPercent = computed(() => {
    if (llmPlannedCount.value <= 0) {
      return 0
    }
    return Math.min(100, Math.round((llmProcessedCount.value / llmPlannedCount.value) * 100))
  })
  const pendingRevisionCount = computed(() => {
    const sentenceIds = new Set<string>()
    for (const entries of Object.values(revisionHistory.value)) {
      for (const entry of entries) {
        if (isCurrentVisiblePendingManualRevision(entry)) {
          sentenceIds.add(entry.sentence_id)
        }
      }
    }
    for (const entry of Object.values(localRevisionDrafts.value)) {
      if (isPendingManualRevision(entry)) {
        sentenceIds.add(entry.sentence_id)
      }
    }
    return sentenceIds.size
  })

  function isPendingManualRevision(entry: SegmentRevisionEntry) {
    return entry.status === 'pending' && entry.source === 'manual'
  }

  function isVisibleRevision(entry: SegmentRevisionEntry) {
    if (revisionSettings.value.show_others_revisions) {
      return true
    }
    const userId = currentUserId()
    const authorId = entry.author?.id || null
    return !userId || !authorId || authorId === userId
  }

  function isVisiblePendingManualRevision(entry: SegmentRevisionEntry) {
    return isPendingManualRevision(entry) && isVisibleRevision(entry)
  }

  function isRevisionCurrentForSegment(entry: SegmentRevisionEntry) {
    const segmentKey = mergeViewId.value
      ? buildSegmentKey(entry.file_record_id, entry.sentence_id)
      : entry.sentence_id
    const index = getSegmentIndex(segmentKey)
    if (index === -1) {
      return true
    }
    return (segments.value[index].target_text || '') === (entry.after_text || '')
  }

  function isCurrentVisiblePendingManualRevision(entry: SegmentRevisionEntry) {
    return isVisiblePendingManualRevision(entry) && isRevisionCurrentForSegment(entry)
  }

  function isLocalRevisionId(id: string) {
    return id.startsWith('local-')
  }

  function compareRevisionEntries(a: SegmentRevisionEntry, b: SegmentRevisionEntry) {
    const timeDelta = new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    if (timeDelta !== 0) {
      return timeDelta
    }
    return b.id.localeCompare(a.id)
  }

  function setRevisionEntries(entries: SegmentRevisionEntry[]) {
    const nextHistory: Record<string, SegmentRevisionEntry[]> = {}
    for (const entry of entries) {
      const sentenceEntries = nextHistory[entry.sentence_id] || []
      sentenceEntries.push(entry)
      nextHistory[entry.sentence_id] = sentenceEntries
    }

    for (const sentenceId of Object.keys(nextHistory)) {
      nextHistory[sentenceId] = nextHistory[sentenceId].slice().sort(compareRevisionEntries)
    }

    revisionHistory.value = nextHistory
    syncRevisionBaselinesFromHistory(nextHistory)
  }

  function upsertRevisionEntry(entry: SegmentRevisionEntry) {
    const nextHistory = { ...revisionHistory.value }
    const sentenceEntries = [...(nextHistory[entry.sentence_id] || [])]
    const index = sentenceEntries.findIndex((item) => item.id === entry.id)

    if (index === -1) {
      sentenceEntries.unshift(entry)
    } else {
      sentenceEntries[index] = entry
    }

    nextHistory[entry.sentence_id] = sentenceEntries.sort(compareRevisionEntries)
    revisionHistory.value = nextHistory
  }

  function getPendingRevision(sentenceId: string) {
    return revisionHistory.value[sentenceId]?.find(isCurrentVisiblePendingManualRevision) || null
  }

  function listVisiblePendingRevisions() {
    const revisions: SegmentRevisionEntry[] = []
    const seenRevisionIds = new Set<string>()
    for (const entries of Object.values(revisionHistory.value)) {
      for (const entry of entries) {
        if (isCurrentVisiblePendingManualRevision(entry) && !seenRevisionIds.has(entry.id)) {
          revisions.push(entry)
          seenRevisionIds.add(entry.id)
        }
      }
    }
    for (const entry of Object.values(localRevisionDrafts.value)) {
      if (isPendingManualRevision(entry) && !seenRevisionIds.has(entry.id)) {
        revisions.push(entry)
        seenRevisionIds.add(entry.id)
      }
    }
    return revisions.sort(compareRevisionEntries)
  }

  function getRevisionTrace(sentenceId: string) {
    return localRevisionDrafts.value[sentenceId] || getPendingRevision(sentenceId)
  }

  function getLocalRevisionDraftById(id: string) {
    return Object.values(localRevisionDrafts.value).find((entry) => entry.id === id) || null
  }

  function syncRevisionBaselinesFromHistory(history: Record<string, SegmentRevisionEntry[]>) {
    const nextBaselines = { ...localRevisionBaselines.value }
    let changed = false
    for (const [sentenceId, entries] of Object.entries(history)) {
      const pendingRevision = entries.find(isPendingManualRevision)
      if (!pendingRevision) {
        continue
      }
      if (nextBaselines[sentenceId] !== pendingRevision.before_text) {
        nextBaselines[sentenceId] = pendingRevision.before_text || ''
        changed = true
      }
    }
    if (changed) {
      localRevisionBaselines.value = nextBaselines
    }
  }

  function hasLocalRevisionBaseline(sentenceId: string) {
    return Object.prototype.hasOwnProperty.call(localRevisionBaselines.value, sentenceId)
  }

  function setLocalRevisionBaseline(sentenceId: string, targetText: string) {
    localRevisionBaselines.value = {
      ...localRevisionBaselines.value,
      [sentenceId]: targetText,
    }

    const nextDrafts = { ...localRevisionDrafts.value }
    delete nextDrafts[sentenceId]
    localRevisionDrafts.value = nextDrafts
  }

  function upsertLocalRevisionDraft(segment: Segment, targetText: string) {
    const sentenceId = segment.sentence_id
    if (!hasLocalRevisionBaseline(sentenceId)) {
      setLocalRevisionBaseline(sentenceId, segment.target_text || '')
    }
    const baselineText = localRevisionBaselines.value[sentenceId] ?? ''

    const nextDrafts = { ...localRevisionDrafts.value }
    if (targetText === baselineText) {
      delete nextDrafts[sentenceId]
      localRevisionDrafts.value = nextDrafts
      return
    }

    nextDrafts[sentenceId] = {
      id: `local-${sentenceId}`,
      file_record_id: fileRecord.value?.id || '',
      segment_id: segment.id,
      sentence_id: sentenceId,
      source: 'manual',
      status: 'pending',
      before_text: baselineText,
      after_text: targetText,
      author: null,
      resolved_by: null,
      created_at: new Date().toISOString(),
      resolved_at: null,
    }
    localRevisionDrafts.value = nextDrafts
  }

  function startRevisionTracking() {
    revisionTrackingEnabled.value = true
    persistRevisionTrackingEnabled(true)
    ensureRevisionTrackingBaselines()
  }

  function stopRevisionTracking() {
    revisionTrackingEnabled.value = false
    persistRevisionTrackingEnabled(false)
    // 停止记录时保留基准，避免再次开启后丢失当前会话里已有的待处理修订上下文。
  }

  function ensureRevisionTrackingBaselines() {
    const nextBaselines = { ...localRevisionBaselines.value }
    let changed = false
    for (const segment of segments.value) {
      if (!Object.prototype.hasOwnProperty.call(nextBaselines, segment.sentence_id)) {
        nextBaselines[segment.sentence_id] = segment.target_text || ''
        changed = true
      }
    }
    if (changed) {
      localRevisionBaselines.value = nextBaselines
    }
  }

  function updateRevisionBaselineAfterPrefill(sentenceId: string, targetText: string) {
    setLocalRevisionBaseline(sentenceId, targetText)
  }

  function clearLocalRevisionDrafts(sentenceIds: string[]) {
    if (!sentenceIds.length) {
      return
    }
    const nextDrafts = { ...localRevisionDrafts.value }
    for (const sentenceId of sentenceIds) {
      delete nextDrafts[sentenceId]
    }
    localRevisionDrafts.value = nextDrafts
  }

  function resetPreviewState() {
    previewHtml.value = ''
    previewSupported.value = false
    previewLoading.value = false
    previewPromise = null
    previewLoaded = false
    previewCacheKey = ''
  }

  function resetSegments(nextSegments: Segment[] = []) {
    segmentIndexMap.clear()
    nextSegments.forEach((segment, index) => {
      segmentIndexMap.set(segmentKeyOf(segment), index)
    })
    segments.value = nextSegments
  }

  function applyServerSegment(nextSegment: Segment, markPreview = true) {
    const index = getSegmentIndex(segmentKeyOf(nextSegment))
    if (index === -1) {
      return
    }

    const previousSegment = segments.value[index]
    segments.value[index] = nextSegment
    adjustSegmentStatusStats(previousSegment, nextSegment)
    if (activeSentenceId.value === segmentKeyOf(nextSegment)) {
      activeSourceText.value = nextSegment.source_text || ''
    }
    if (markPreview && previousSegment.target_text !== nextSegment.target_text) {
      markPreviewUpdate(segmentKeyOf(nextSegment), nextSegment.target_text || '')
    }
  }

  function applyServerSegments(nextSegments: Segment[], markPreview = true) {
    for (const segment of nextSegments) {
      applyServerSegment(segment, markPreview)
    }
  }

  function applyServerSegmentPatches(
    patches: Array<Pick<Segment, 'sentence_id'> & Partial<Segment>>,
    statusStats?: SegmentStatusStats | null,
  ) {
    for (const patch of patches) {
      const index = segments.value.findIndex((segment) => segment.sentence_id === patch.sentence_id)
      if (index === -1) {
        continue
      }
      applyServerSegment({ ...segments.value[index], ...patch })
    }
    if (statusStats) {
      setSegmentStatusStats(statusStats)
    }
  }

  function setSegmentStatusStats(stats?: SegmentStatusStats | null) {
    segmentStatusStats.value = stats ? { ...createEmptySegmentStatusStats(), ...stats } : createEmptySegmentStatusStats()
  }

  async function refreshMergeViewDetail() {
    if (!mergeViewId.value) {
      return null
    }
    const detail = await getMergeViewDetail(mergeViewId.value)
    mergeViewDetail.value = detail
    totalSegmentCount.value = detail.total_segments
    setSegmentStatusStats(buildMergeViewStatusStats(detail))
    return detail
  }

  function adjustSegmentStatusStats(previousSegment: Segment, nextSegment: Segment) {
    const nextStats = { ...segmentStatusStats.value }
    const previousMatchStatus = resolveSegmentMatchStatusForStats(previousSegment)
    const nextMatchStatus = resolveSegmentMatchStatusForStats(nextSegment)
    if (previousMatchStatus !== nextMatchStatus) {
      nextStats[previousMatchStatus] = Math.max(0, nextStats[previousMatchStatus] - 1)
      nextStats[nextMatchStatus] += 1
    }

    const wasConfirmed = previousSegment.status === 'confirmed'
    const isConfirmed = nextSegment.status === 'confirmed'
    if (wasConfirmed !== isConfirmed) {
      nextStats.confirmed = Math.max(0, nextStats.confirmed + (isConfirmed ? 1 : -1))
    }

    const wasEmptyTarget = hasEmptyTarget(previousSegment.target_text)
    const isEmptyTarget = hasEmptyTarget(nextSegment.target_text)
    if (wasEmptyTarget !== isEmptyTarget) {
      nextStats.empty_target = Math.max(0, nextStats.empty_target + (isEmptyTarget ? 1 : -1))
    }
    segmentStatusStats.value = nextStats
  }

  function appendSegments(nextSegments: Segment[]) {
    if (!nextSegments.length) {
      return
    }

    const startIndex = segments.value.length
    segments.value = segments.value.concat(nextSegments)
    nextSegments.forEach((segment, offset) => {
      segmentIndexMap.set(segment.sentence_id, startIndex + offset)
    })
  }

  function resolvePageQuery(query: SegmentPageQuery = {}) {
    return {
      page: normalizePositiveInt(query.page, currentPage.value || 1),
      pageSize: normalizePageSize(query.pageSize, pageSize.value || DEFAULT_SEGMENT_PAGE_SIZE),
      scope: query.scope ?? segmentFilters.value.scope,
      sourceQuery: query.sourceQuery ?? segmentFilters.value.sourceQuery,
      targetQuery: query.targetQuery ?? segmentFilters.value.targetQuery,
      sourceExclude: query.sourceExclude ?? segmentFilters.value.sourceExclude,
      targetExclude: query.targetExclude ?? segmentFilters.value.targetExclude,
      searchFuzzy: query.searchFuzzy ?? segmentFilters.value.searchFuzzy,
      caseSensitive: query.caseSensitive ?? segmentFilters.value.caseSensitive,
      statusFilters: normalizeStringArray(query.statusFilters ?? segmentFilters.value.statusFilters),
      matchFilters: normalizeStringArray(query.matchFilters ?? segmentFilters.value.matchFilters),
      sourceFilters: normalizeStringArray(query.sourceFilters ?? segmentFilters.value.sourceFilters),
      sourceContentFilters: normalizeStringArray(
        query.sourceContentFilters ?? segmentFilters.value.sourceContentFilters,
      ),
      workflowStepIds: normalizeStringArray(query.workflowStepIds ?? segmentFilters.value.workflowStepIds),
      includeStats: query.includeStats ?? true,
    }
  }

  function buildSegmentWindowParams(query: SegmentPageQuery = {}) {
    const resolved = resolvePageQuery(query)
    return {
      skip: (resolved.page - 1) * resolved.pageSize,
      limit: resolved.pageSize,
      scope: resolved.scope,
      source_query: resolved.sourceQuery,
      target_query: resolved.targetQuery,
      source_exclude: resolved.sourceExclude,
      target_exclude: resolved.targetExclude,
      search_fuzzy: resolved.searchFuzzy,
      case_sensitive: resolved.caseSensitive,
      status_filters: serializeFilterArray(resolved.statusFilters),
      match_filters: serializeFilterArray(resolved.matchFilters),
      source_filters: serializeFilterArray(resolved.sourceFilters),
      source_content_filters: serializeFilterArray(resolved.sourceContentFilters),
      workflow_step_ids: serializeFilterArray(resolved.workflowStepIds),
      include_stats: resolved.includeStats,
    }
  }

  function getCurrentPageQuery(): SegmentPageQuery {
    return {
      page: currentPage.value,
      pageSize: pageSize.value,
      scope: segmentFilters.value.scope,
      sourceQuery: segmentFilters.value.sourceQuery,
      targetQuery: segmentFilters.value.targetQuery,
      sourceExclude: segmentFilters.value.sourceExclude,
      targetExclude: segmentFilters.value.targetExclude,
      searchFuzzy: segmentFilters.value.searchFuzzy,
      caseSensitive: segmentFilters.value.caseSensitive,
      statusFilters: [...segmentFilters.value.statusFilters],
      matchFilters: [...segmentFilters.value.matchFilters],
      sourceFilters: [...segmentFilters.value.sourceFilters],
      sourceContentFilters: [...segmentFilters.value.sourceContentFilters],
      workflowStepIds: [...segmentFilters.value.workflowStepIds],
    }
  }

  async function fetchFileRecordDetail(fileRecordId: string, limit: number) {
    const { data } = await http.get<FileRecordDetail>(`/file-records/${fileRecordId}`, {
      params: {
        skip: 0,
        limit,
      },
    })
    return data
  }

  async function fetchSegmentPage(fileRecordId: string, query: SegmentPageQuery = {}) {
    const resolved = resolvePageQuery(query)
    const requestPage = async (pageQuery: typeof resolved) => {
      const { data } = await http.get<SegmentPageResponse>(`/file-records/${fileRecordId}/segments`, {
        params: buildSegmentWindowParams(pageQuery),
      })
      return data
    }

    let data = await requestPage(resolved)
    const maxPage = Math.max(1, Math.ceil(data.matched_segments / resolved.pageSize))
    if (resolved.page > maxPage) {
      const clamped = { ...resolved, page: maxPage }
      data = await requestPage(clamped)
      return { data, resolved: clamped }
    }
    return { data, resolved }
  }

  function applySegmentPageData(
    data: SegmentPageResponse,
    resolved: {
      page: number
      pageSize: number
      scope: string
      sourceQuery: string
      targetQuery: string
      sourceExclude: string
      targetExclude: string
      searchFuzzy: boolean
      caseSensitive: boolean
      statusFilters: string[]
      matchFilters: string[]
      sourceFilters: string[]
      sourceContentFilters: string[]
      workflowStepIds: string[]
    },
  ) {
    currentPage.value = resolved.page
    pageSize.value = resolved.pageSize
    segmentFilters.value = {
      scope: resolved.scope,
      sourceQuery: resolved.sourceQuery,
      targetQuery: resolved.targetQuery,
      sourceExclude: resolved.sourceExclude,
      targetExclude: resolved.targetExclude,
      searchFuzzy: resolved.searchFuzzy,
      caseSensitive: resolved.caseSensitive,
      statusFilters: [...resolved.statusFilters],
      matchFilters: [...resolved.matchFilters],
      sourceFilters: [...resolved.sourceFilters],
      sourceContentFilters: [...resolved.sourceContentFilters],
      workflowStepIds: [...resolved.workflowStepIds],
    }
    totalSegmentCount.value = data.total_segments ?? totalSegmentCount.value
    matchedSegmentCount.value = data.matched_segments
    if (data.status_stats) {
      setSegmentStatusStats(data.status_stats)
    }
    if (fileRecord.value && data.workflow_progress) {
      fileRecord.value = {
        ...fileRecord.value,
        workflow_progress: data.workflow_progress,
      }
    }
    resetSegments(data.segments)
    if (data.change_cursor || data.server_time) {
      changeCursor = data.change_cursor || data.server_time || null
    }
    resetPreviewState()
  }

  function startChangePolling() {
    stopChangePolling()
    if (!fileRecord.value) {
      return
    }
    const generation = ++segmentEventGeneration
    void connectSegmentEventStream(fileRecord.value.id, generation)
  }

  function startChangePollingFallback() {
    if (changePollTimer !== null || !fileRecord.value) {
      return
    }
    void pollSegmentChanges()
    changePollTimer = window.setInterval(() => {
      void pollSegmentChanges()
    }, CHANGE_POLL_INTERVAL_MS)
  }

  function stopChangePollingFallback() {
    if (changePollTimer !== null) {
      window.clearInterval(changePollTimer)
      changePollTimer = null
    }
  }

  function scheduleSegmentEventReconnect(fileRecordId: string, generation: number) {
    if (generation !== segmentEventGeneration || !fileRecord.value) {
      return
    }
    if (segmentEventReconnectTimer !== null) {
      window.clearTimeout(segmentEventReconnectTimer)
    }
    segmentEventReconnectTimer = window.setTimeout(() => {
      segmentEventReconnectTimer = null
      void connectSegmentEventStream(fileRecordId, generation)
    }, SEGMENT_EVENT_RECONNECT_DELAY_MS)
  }

  async function connectSegmentEventStream(fileRecordId: string, generation: number) {
    if (generation !== segmentEventGeneration || fileRecord.value?.id !== fileRecordId) {
      return
    }
    segmentEventAbortController?.abort()
    const controller = new AbortController()
    segmentEventAbortController = controller
    const token = window.localStorage.getItem('token')
    const baseUrl = http.defaults.baseURL || '/api'

    try {
      const response = await fetch(`${baseUrl}/file-records/${fileRecordId}/segments/events`, {
        headers: {
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        signal: controller.signal,
      })
      if (!response.ok || !response.body) {
        throw new Error(`segment event stream unavailable: ${response.status}`)
      }
      stopChangePollingFallback()
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (generation === segmentEventGeneration) {
        const { done, value } = await reader.read()
        if (done) {
          break
        }
        buffer += decoder.decode(value, { stream: true })
        let boundary = buffer.indexOf('\n\n')
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          if (block.split('\n').some((line) => line.trim() === 'event: segments')) {
            void pollSegmentChanges()
          }
          boundary = buffer.indexOf('\n\n')
        }
      }
    } catch (error) {
      if (!controller.signal.aborted && generation === segmentEventGeneration) {
        startChangePollingFallback()
      }
    } finally {
      if (segmentEventAbortController === controller) {
        segmentEventAbortController = null
      }
      if (!controller.signal.aborted && generation === segmentEventGeneration) {
        startChangePollingFallback()
        scheduleSegmentEventReconnect(fileRecordId, generation)
      }
    }
  }

  function clearChangePollBurstTimers() {
    for (const timer of changePollBurstTimers) {
      window.clearTimeout(timer)
    }
    changePollBurstTimers = []
  }

  function scheduleChangePollBurst() {
    clearChangePollBurstTimers()
    if (!fileRecord.value) {
      return
    }
    changePollBurstTimers = CHANGE_POLL_BURST_DELAYS_MS.map((delay) => window.setTimeout(() => {
      void pollSegmentChanges()
    }, delay))
  }

  function stopChangePolling() {
    segmentEventGeneration += 1
    segmentEventAbortController?.abort()
    segmentEventAbortController = null
    if (segmentEventReconnectTimer !== null) {
      window.clearTimeout(segmentEventReconnectTimer)
      segmentEventReconnectTimer = null
    }
    stopChangePollingFallback()
    clearChangePollBurstTimers()
    pollingChanges = false
  }

  async function pollSegmentChanges() {
    if (!fileRecord.value || !changeCursor || pollingChanges) {
      return
    }

    pollingChanges = true
    try {
      const currentFileRecordId = fileRecord.value.id
      // 轮询期间自动保存可能会完成并清空 dirtyEntries。这里只记录轮询发现的
      // 版本推进和冲突，结束时再合并到最新状态，避免旧快照把已保存条目“复活”。
      const dirtyBaseVersionBumps = new Map<string, number>()
      const addedConflicts: Record<string, string> = {}
      let conflictAdded = false
      let hasMore = true
      let pageGuard = 0

      while (fileRecord.value && changeCursor && hasMore && pageGuard < 20) {
        pageGuard += 1
        const response: { data: SegmentChangeResponse } = await http.get<SegmentChangeResponse>(`/file-records/${currentFileRecordId}/segments/changes`, {
          params: {
            since: changeCursor,
            limit: MAX_SEGMENT_PAGE_SIZE,
          },
        })
        const data: SegmentChangeResponse = response.data

        for (const remoteSegment of data.segments || []) {
          const dirtyEntry = dirtyEntries.value[remoteSegment.sentence_id]
          if (dirtyEntry) {
            const baseVersion = Number(dirtyEntry.base_version || 0)
            const remoteVersion = Number(remoteSegment.version || 1)
            if (remoteVersion > baseVersion) {
              if ((remoteSegment.target_text || '') === (dirtyEntry.target_text || '')) {
                dirtyBaseVersionBumps.set(
                  remoteSegment.sentence_id,
                  Math.max(dirtyBaseVersionBumps.get(remoteSegment.sentence_id) || 0, remoteVersion),
                )
              } else if (isSensitiveRemoteConflict(
                remoteSegment.source,
                remoteSegment.last_modified_by_id,
                dirtyEntry.source,
              )) {
                addedConflicts[remoteSegment.sentence_id] = remoteSegment.target_text || ''
                conflictAdded = true
              } else {
                dirtyBaseVersionBumps.set(
                  remoteSegment.sentence_id,
                  Math.max(dirtyBaseVersionBumps.get(remoteSegment.sentence_id) || 0, remoteVersion),
                )
              }
              applyServerSegmentMetadata(remoteSegment.sentence_id, remoteSegment)
              continue
            }
          }
          applyServerSegment(remoteSegment)
        }
        if (data.status_stats) {
          setSegmentStatusStats(data.status_stats)
        }
        if (fileRecord.value?.id === currentFileRecordId && data.workflow_progress) {
          fileRecord.value = {
            ...fileRecord.value,
            workflow_progress: data.workflow_progress,
          }
        }
        changeCursor = data.next_cursor || data.server_time || new Date().toISOString()
        hasMore = Boolean(data.has_more)
      }

      const latestDirtyEntries = { ...dirtyEntries.value }
      for (const [sentenceId, version] of dirtyBaseVersionBumps) {
        bumpDirtyBaseVersion(latestDirtyEntries, sentenceId, version)
      }
      dirtyEntries.value = latestDirtyEntries
      conflictEntries.value = {
        ...conflictEntries.value,
        ...addedConflicts,
      }
      if (conflictAdded) {
        syncMessage.value = '检测到其他用户已更新同一句段，请确认后再保存本地修改。'
        pushToast({
          tone: 'warn',
          title: '发现协同冲突',
          message: '有句段已被其他用户更新，本地修改已保留。',
        })
      }
    } catch {
      // 增量同步失败不打断当前编辑，下一轮继续尝试。
    } finally {
      pollingChanges = false
    }
  }

  async function refreshCurrentSegmentPage() {
    if (!fileRecord.value) {
      return false
    }
    const { data, resolved } = await fetchSegmentPage(fileRecord.value.id, getCurrentPageQuery())
    applySegmentPageData(data, resolved)
    await loadRevisions(fileRecord.value.id, resolved)
    if (segments.value[0] && !segments.value.some((segment) => segment.sentence_id === activeSentenceId.value)) {
      setActiveSentence(segments.value[0].sentence_id)
    } else if (!segments.value.length) {
      setActiveSentence(null)
    }
    return true
  }

  async function loadRevisions(fileRecordId: string, query: SegmentPageQuery = {}) {
    if (!segments.value.length) {
      setRevisionEntries([])
      return []
    }
    const { data } = await http.get<SegmentRevisionEntry[]>(`/file-records/${fileRecordId}/revisions`, {
      params: buildSegmentWindowParams(query),
    })
    setRevisionEntries(data)
    return data
  }

  async function fetchRevisionSettings(fileRecordId = fileRecord.value?.id || '') {
    if (!fileRecordId) {
      revisionSettings.value = createDefaultRevisionSettings()
      return revisionSettings.value
    }
    const { data } = await http.get<RevisionDisplaySettings>(`/file-records/${fileRecordId}/revision-settings`)
    revisionSettings.value = {
      ...createDefaultRevisionSettings(fileRecordId),
      ...data,
      author_colors: data.author_colors || {},
    }
    return revisionSettings.value
  }

  async function saveRevisionSettings(payload: RevisionDisplaySettings) {
    const fileRecordId = fileRecord.value?.id || payload.file_record_id
    if (!fileRecordId) {
      throw new Error('当前任务尚未加载，无法保存修订设置。')
    }
    const { data } = await http.put<RevisionDisplaySettings>(
      `/file-records/${fileRecordId}/revision-settings`,
      {
        show_author_time: payload.show_author_time,
        show_others_revisions: payload.show_others_revisions,
        default_insert_color: payload.default_insert_color,
        default_delete_color: payload.default_delete_color,
        author_colors: payload.author_colors || {},
      },
    )
    revisionSettings.value = {
      ...createDefaultRevisionSettings(fileRecordId),
      ...data,
      author_colors: data.author_colors || {},
    }
    return revisionSettings.value
  }

  function resetState() {
    if (syncTimer !== null) {
      window.clearTimeout(syncTimer)
      syncTimer = null
    }
    stopChangePolling()

    fileRecord.value = null
    resetSegments()
    resetPreviewState()
    totalSegmentCount.value = 0
    matchedSegmentCount.value = 0
    setSegmentStatusStats()
    currentPage.value = 1
    pageSize.value = DEFAULT_SEGMENT_PAGE_SIZE
    segmentFilters.value = {
      scope: 'all',
      sourceQuery: '',
      targetQuery: '',
      sourceExclude: '',
      targetExclude: '',
      searchFuzzy: false,
      caseSensitive: false,
      statusFilters: [],
      matchFilters: [],
      sourceFilters: [],
      sourceContentFilters: [],
      workflowStepIds: [],
    }
    saveToTMStats.value = null
    activeSentenceId.value = null
    loading.value = false
    loadingMoreSegments.value = false
    loadingAllSegments.value = false
    saving.value = false
    llmRunning.value = false
    syncMessage.value = translate('stores.segment.syncIdle')
    llmMessage.value = translate('stores.segment.llmReady')
    llmPlannedCount.value = 0
    llmProcessedCount.value = 0
    llmErrorCount.value = 0
    lastSyncedAt.value = null
    lastModifiedAt.value = null
    dirtyEntries.value = {}
    conflictEntries.value = {}
    changeCursor = null
    previewUpdateToken.value = 0
    lastPreviewUpdatedSentenceId.value = null
    lastPreviewUpdatedText.value = ''
    revisionHistory.value = {}
    localRevisionDrafts.value = {}
    localRevisionBaselines.value = {}
    revisionTrackingEnabled.value = getInitialRevisionTrackingEnabled()
    revisionSettings.value = createDefaultRevisionSettings()
    syncPromise = null
    loadMorePromise = null
    pendingSegmentPageQuery = null
    llmAbortController = null
    llmReader = null
    llmAbortRequested = false
    // 合并视图状态也一并清空
    mergeViewId.value = null
    mergeViewDetail.value = null
    mergeViewGroups.value = []
    activeFileRecordId.value = null
  }

  /** 合并视图模式：加载视图聚合段（每段带 file_record_id），填充 segments。 */
  async function loadMergeView(viewId: string, query: SegmentPageQuery = {}) {
    resetState()
    mergeViewId.value = viewId
    loading.value = true
    try {
      const resolved = resolvePageQuery({
        page: query.page ?? 1,
        pageSize: query.pageSize ?? DEFAULT_SEGMENT_PAGE_SIZE,
        scope: query.scope ?? 'all',
        sourceQuery: query.sourceQuery ?? '',
        targetQuery: query.targetQuery ?? '',
        sourceExclude: query.sourceExclude ?? '',
        targetExclude: query.targetExclude ?? '',
        searchFuzzy: query.searchFuzzy ?? false,
        caseSensitive: query.caseSensitive ?? false,
        statusFilters: query.statusFilters ?? [],
        matchFilters: query.matchFilters ?? [],
        sourceFilters: query.sourceFilters ?? [],
        sourceContentFilters: query.sourceContentFilters ?? [],
        workflowStepIds: query.workflowStepIds ?? [],
      })
      pageSize.value = resolved.pageSize
      currentPage.value = resolved.page
      segmentFilters.value = {
        scope: resolved.scope,
        sourceQuery: resolved.sourceQuery,
        targetQuery: resolved.targetQuery,
        sourceExclude: resolved.sourceExclude,
        targetExclude: resolved.targetExclude,
        searchFuzzy: resolved.searchFuzzy,
        caseSensitive: resolved.caseSensitive,
        statusFilters: [...resolved.statusFilters],
        matchFilters: [...resolved.matchFilters],
        sourceFilters: [...resolved.sourceFilters],
        sourceContentFilters: [...resolved.sourceContentFilters],
        workflowStepIds: [...resolved.workflowStepIds],
      }
      const detail = await getMergeViewDetail(viewId)
      mergeViewDetail.value = detail
      totalSegmentCount.value = detail.total_segments
      setSegmentStatusStats(buildMergeViewStatusStats(detail))

      const page = await fetchMergeViewSegmentPage(viewId, resolved)
      currentPage.value = Math.floor((page.skip || 0) / Math.max(page.limit || resolved.pageSize, 1)) + 1
      pageSize.value = page.limit || resolved.pageSize
      matchedSegmentCount.value = page.matched_segments
      mergeViewGroups.value = page.groups
      changeCursor = page.change_cursor || page.server_time || new Date().toISOString()
      resetSegments(page.segments)
      // 自动激活首个句段
      if (segments.value[0]) {
        setActiveSentence(segmentKeyOf(segments.value[0]))
      }
    } finally {
      loading.value = false
    }
  }

  /** 合并模式刷新当前页（筛选/分页变化后）。 */
  async function refreshMergeViewPage(query: SegmentPageQuery) {
    if (!mergeViewId.value) {
      return
    }
    loading.value = true
    try {
      const resolved = resolvePageQuery(query)
      currentPage.value = resolved.page
      pageSize.value = resolved.pageSize
      segmentFilters.value = {
        scope: resolved.scope,
        sourceQuery: resolved.sourceQuery,
        targetQuery: resolved.targetQuery,
        sourceExclude: resolved.sourceExclude,
        targetExclude: resolved.targetExclude,
        searchFuzzy: resolved.searchFuzzy,
        caseSensitive: resolved.caseSensitive,
        statusFilters: [...resolved.statusFilters],
        matchFilters: [...resolved.matchFilters],
        sourceFilters: [...resolved.sourceFilters],
        sourceContentFilters: [...resolved.sourceContentFilters],
        workflowStepIds: [...resolved.workflowStepIds],
      }
      const page = await fetchMergeViewSegmentPage(mergeViewId.value, resolved)
      currentPage.value = Math.floor((page.skip || 0) / Math.max(page.limit || resolved.pageSize, 1)) + 1
      pageSize.value = page.limit || resolved.pageSize
      matchedSegmentCount.value = page.matched_segments
      mergeViewGroups.value = page.groups
      changeCursor = page.change_cursor || page.server_time || changeCursor
      resetSegments(page.segments)
      if (segments.value[0] && !activeSentenceId.value) {
        setActiveSentence(segmentKeyOf(segments.value[0]))
      }
    } finally {
      loading.value = false
    }
  }

  /** 由当前页/筛选状态构造合并视图查询参数。 */
  function resolveCurrentMergeQuery(): SegmentPageQuery {
    return {
      page: currentPage.value,
      pageSize: pageSize.value,
      scope: segmentFilters.value.scope,
      sourceQuery: segmentFilters.value.sourceQuery,
      targetQuery: segmentFilters.value.targetQuery,
      sourceExclude: segmentFilters.value.sourceExclude,
      targetExclude: segmentFilters.value.targetExclude,
      searchFuzzy: segmentFilters.value.searchFuzzy,
      caseSensitive: segmentFilters.value.caseSensitive,
      statusFilters: segmentFilters.value.statusFilters,
      matchFilters: segmentFilters.value.matchFilters,
      sourceFilters: segmentFilters.value.sourceFilters,
      sourceContentFilters: segmentFilters.value.sourceContentFilters,
      workflowStepIds: segmentFilters.value.workflowStepIds,
    }
  }

  async function loadTask(fileRecordId: string, query: SegmentPageQuery = {}) {
    resetState()
    loading.value = true
    try {
      const resolved = resolvePageQuery({
        page: query.page ?? 1,
        pageSize: query.pageSize ?? DEFAULT_SEGMENT_PAGE_SIZE,
        scope: query.scope ?? 'all',
        sourceQuery: query.sourceQuery ?? '',
        targetQuery: query.targetQuery ?? '',
        sourceExclude: query.sourceExclude ?? '',
        targetExclude: query.targetExclude ?? '',
        searchFuzzy: query.searchFuzzy ?? false,
        caseSensitive: query.caseSensitive ?? false,
        statusFilters: query.statusFilters ?? [],
        matchFilters: query.matchFilters ?? [],
        sourceFilters: query.sourceFilters ?? [],
        sourceContentFilters: query.sourceContentFilters ?? [],
        workflowStepIds: query.workflowStepIds ?? [],
      })
      pageSize.value = resolved.pageSize
      currentPage.value = resolved.page
      segmentFilters.value = {
        scope: resolved.scope,
        sourceQuery: resolved.sourceQuery,
        targetQuery: resolved.targetQuery,
        sourceExclude: resolved.sourceExclude,
        targetExclude: resolved.targetExclude,
        searchFuzzy: resolved.searchFuzzy,
        caseSensitive: resolved.caseSensitive,
        statusFilters: [...resolved.statusFilters],
        matchFilters: [...resolved.matchFilters],
        sourceFilters: [...resolved.sourceFilters],
        sourceContentFilters: [...resolved.sourceContentFilters],
        workflowStepIds: [...resolved.workflowStepIds],
      }
      const detail = await fetchFileRecordDetail(fileRecordId, resolved.pageSize)
      fileRecord.value = {
        ...detail,
        segments: [],
      }
      changeCursor = detail.change_cursor || detail.server_time || new Date().toISOString()
      totalSegmentCount.value = detail.total_segments
      setSegmentStatusStats(detail.status_stats)
      if (
        resolved.page === 1
        && resolved.scope === 'all'
        && !resolved.sourceQuery
        && !resolved.targetQuery
        && !resolved.sourceExclude
        && !resolved.targetExclude
        && resolved.statusFilters.length === 0
        && resolved.matchFilters.length === 0
        && resolved.sourceFilters.length === 0
        && resolved.sourceContentFilters.length === 0
        && resolved.workflowStepIds.length === 0
      ) {
        matchedSegmentCount.value = detail.total_segments
        resetSegments(detail.segments)
      } else {
        const page = await fetchSegmentPage(fileRecordId, resolved)
        applySegmentPageData(page.data, page.resolved)
      }
      await loadRevisions(fileRecordId)
      await fetchRevisionSettings(fileRecordId)
      if (revisionTrackingEnabled.value) {
        ensureRevisionTrackingBaselines()
      }
      startChangePolling()
    } finally {
      loading.value = false
    }
  }

  async function loadSegmentPage(query: SegmentPageQuery = {}): Promise<boolean> {
    if (!fileRecord.value) {
      return false
    }

    // 已有进行中的加载：记录最新目标 query，由当前的加载循环在结束后继续处理（last-wins），
    // 避免连续翻页时后点击的页码被静默丢弃而停在旧页。
    if (loadMorePromise) {
      pendingSegmentPageQuery = query
      return loadMorePromise
    }

    loadingMoreSegments.value = true
    loadMorePromise = (async () => {
      let currentQuery: SegmentPageQuery | null = query
      let hasMore = false
      try {
        while (currentQuery) {
          const activeQuery = currentQuery
          pendingSegmentPageQuery = null
          const { data, resolved } = await fetchSegmentPage(fileRecord.value!.id, activeQuery)
          applySegmentPageData(data, resolved)
          await loadRevisions(fileRecord.value!.id, resolved)
          if (segments.value[0] && !segments.value.some((segment) => segment.sentence_id === activeSentenceId.value)) {
            setActiveSentence(segments.value[0].sentence_id)
          } else if (!segments.value.length) {
            setActiveSentence(null)
          }
          hasMore = data.segments.length > 0
          // 加载期间若有更新的目标页码，循环加载最新的那一页。
          currentQuery = pendingSegmentPageQuery
        }
        return hasMore
      } finally {
        pendingSegmentPageQuery = null
        loadMorePromise = null
        loadingMoreSegments.value = false
      }
    })()

    return loadMorePromise
  }

  async function loadMoreSegments() {
    if (!fileRecord.value || !hasMoreSegments.value) {
      return false
    }
    return loadSegmentPage({ page: currentPage.value + 1 })
  }

  async function ensureAllSegmentsLoaded() {
    // 大文档模式不再把全文加载到浏览器；保留方法名兼容旧调用。
    return
  }

  async function fetchPreview(fileRecordId: string, mode: 'source' | 'target' = 'source') {
    const { data } = await http.get<FileRecordPreview>(`/file-records/${fileRecordId}/preview`, {
      params: {
        skip: (currentPage.value - 1) * pageSize.value,
        limit: pageSize.value,
        mode,
      },
    })
    return data
  }

  async function ensurePreviewLoaded(mode: 'source' | 'target' = 'source') {
    if (!fileRecord.value) {
      return
    }
    const nextPreviewKey = `${fileRecord.value.id}:${currentPage.value}:${pageSize.value}:${mode}`
    if (previewLoaded && previewCacheKey === nextPreviewKey) {
      return
    }

    if (previewPromise) {
      return previewPromise
    }

    previewLoading.value = true
    previewPromise = (async () => {
      const preview = await fetchPreview(fileRecord.value!.id, mode)
      previewHtml.value = preview.preview_html
      previewSupported.value = preview.supports_preview
      previewLoaded = true
      previewCacheKey = nextPreviewKey
    })()

    try {
      await previewPromise
    } finally {
      previewPromise = null
      previewLoading.value = false
    }
  }

  function getSegmentIndex(sentenceId: string) {
    const index = segmentIndexMap.get(sentenceId)
    return typeof index === 'number' ? index : -1
  }

  function rebuildSegmentIndexMap() {
    segmentIndexMap.clear()
    segments.value.forEach((segment, index) => {
      segmentIndexMap.set(segmentKeyOf(segment), index)
    })
  }

  async function ensureSentenceLoaded(sentenceId: string) {
    return getSegmentIndex(sentenceId)
  }

  function markPreviewUpdate(sentenceId: string, targetText: string) {
    lastPreviewUpdatedSentenceId.value = sentenceId
    lastPreviewUpdatedText.value = targetText
    lastModifiedAt.value = new Date().toISOString()
    previewUpdateToken.value += 1
  }

  function updateTarget(
    sentenceId: string,
    targetText: string,
    targetHtml?: string,
    options: { confirm?: boolean } = {},
  ) {
    // sentenceId 在合并模式下是复合键；还原真实 sentence_id 用于后端匹配与回写。
    const segmentKey = sentenceId
    const index = getSegmentIndex(segmentKey)
    if (index === -1) {
      return
    }

    const segment = segments.value[index]
    const confirm = options.confirm === true
    const nextTargetHtml = targetHtml || null
    // 内联标签编辑：编辑值可能带 ⟦n⟧ 标签。存储/展示用纯译文（剥标签），
    // 带标签版式译文单独留存；发给后端的仍是带标签文本，由后端拆分落库。
    const hasMarks = FORMAT_MARK_RE.test(targetText || '')
    const cleanTargetText = hasMarks ? (targetText || '').replace(FORMAT_MARK_RE_GLOBAL, '') : (targetText || '')
    const layoutTargetText = hasMarks ? (targetText || '') : ''
    const currentTargetText = segment.target_text || ''
    const currentTargetHtml = segment.target_html || null
    if (
      currentTargetText === cleanTargetText
      && (segment.target_layout_text || '') === layoutTargetText
      && currentTargetHtml === nextTargetHtml
      && (!confirm || segment.status === 'confirmed')
    ) {
      return
    }
    if (revisionTrackingEnabled.value) {
      upsertLocalRevisionDraft(segment, cleanTargetText)
    }
    const nextSegment = {
      ...segment,
      target_text: cleanTargetText,
      target_layout_text: layoutTargetText || null,
      target_html: nextTargetHtml,
      source: 'manual',
      status: resolveSegmentStatusAfterTargetUpdate(segment, cleanTargetText, confirm),
      llm_provider: null,
      llm_model: null,
    }
    segments.value[index] = nextSegment
    adjustSegmentStatusStats(segment, nextSegment)
    markPreviewUpdate(segmentKey, cleanTargetText)

    dirtyEntries.value = {
      ...dirtyEntries.value,
      [segmentKey]: {
        sentence_id: segment.sentence_id,
        target_text: targetText,
        target_html: nextTargetHtml,
        source: 'manual',
        track_revision: revisionTrackingEnabled.value,
        base_version: segment.version ?? 1,
        confirm,
        // 合并模式保存分组用：记录该 dirty 条目归属的文件
        file_record_id: fileRecordIdForSegment(segment) ?? undefined,
      },
    }
    const nextConflicts = { ...conflictEntries.value }
    delete nextConflicts[segmentKey]
    conflictEntries.value = nextConflicts
    syncMessage.value = translate('stores.segment.syncPending', { count: dirtyCount.value })
    if (confirm) {
      void syncToBackend()
    } else {
      scheduleSync()
    }
  }

  async function updateSource(sentenceId: string, sourceText: string) {
    const segmentKey = sentenceId
    const index = getSegmentIndex(segmentKey)
    if (index === -1) {
      return
    }

    const segment = segments.value[index]
    const nextSegment = {
      ...segment,
      source_text: sourceText,
      display_text: sourceText,
      source_body_text: sourceText,
      automatic_numbering_text: null,
      target_automatic_numbering_text: null,
      source_html: null,
    }
    segments.value[index] = nextSegment

    // 合并模式按激活句段所属文件路由；单文件走 fileRecord.id
    const fileId = mergeViewId.value
      ? (fileRecordIdForSegment(segment) ?? null)
      : (fileRecord.value?.id ?? null)
    if (!fileId) {
      return
    }
    // 直接同步到后端
    try {
      await http.put(`/file-records/${fileId}/segments/${segment.sentence_id}/source`, {
        source_text: sourceText,
      })
    } catch (error) {
      // 恢复原值
      segments.value[index] = segment
      throw error
    }
  }

  async function setProjectSyncDisabled(sentenceId: string, disabled: boolean) {
    const segmentKey = sentenceId
    const index = getSegmentIndex(segmentKey)
    if (index === -1) {
      return
    }
    const segment = segments.value[index]
    // 合并模式按该句段所属文件路由；单文件走 fileRecord.id
    const fileId = mergeViewId.value
      ? (fileRecordIdForSegment(segment) ?? null)
      : (fileRecord.value?.id ?? null)
    if (!fileId) {
      return
    }
    const previousSegment = segment
    const willClearProjectSyncedTarget = Boolean(
      disabled
      && previousSegment?.source === 'project_sync'
      && (previousSegment.target_text || '').trim(),
    )
    try {
      const { data } = await http.patch<Segment>(
        `/file-records/${fileId}/segments/${segment.sentence_id}/project-sync`,
        { disabled },
      )
      applyServerSegments([data])
      pushToast({
        tone: 'success',
        title: disabled ? '已关闭项目同步' : '已开启项目同步',
        message: disabled
          ? (willClearProjectSyncedTarget
              ? '已清空该句段由项目同步生成的译文，后续不再被相同原文自动同步覆盖。'
              : '该句段后续不再被相同原文自动同步覆盖。')
          : '该句段已恢复项目内相同原文自动同步。',
      })
    } catch (error) {
      console.error('Failed to update project sync:', error)
      pushToast({
        tone: 'error',
        title: '同步开关更新失败',
        message: String((error as any)?.response?.data?.detail || '请稍后重试。'),
      })
    }
  }

  async function disableProjectSyncForCurrentFile(): Promise<ProjectSyncDisableResult> {
    if (!fileRecord.value) {
      return { updated_count: 0, disabled_count: 0, cleared_count: 0 }
    }

    const synced = await syncToBackend()
    if (!synced) {
      return { updated_count: 0, disabled_count: 0, cleared_count: 0 }
    }

    const { data } = await http.post<ProjectSyncDisableResult>(
      `/file-records/${fileRecord.value.id}/segments/project-sync/disable`,
    )
    await refreshCurrentSegmentPage()
    const syncedAt = new Date()
    lastSyncedAt.value = syncedAt.toISOString()
    syncMessage.value = translate('stores.segment.syncedAt', {
      time: syncedAt.toLocaleString('zh-CN', { hour12: false }),
    })
    return data
  }

  function setActiveSentence(sentenceId: string | null) {
    // sentenceId 在合并模式下是复合键（${file_record_id}:${sentence_id}）
    activeSentenceId.value = sentenceId
    if (sentenceId) {
      const segment = mergeViewId.value
        ? segments.value.find((s) => segmentKeyOf(s) === sentenceId)
        : segments.value.find((s) => s.sentence_id === sentenceId)
      activeSourceText.value = segment?.source_text || ''
      activeFileRecordId.value = segment ? fileRecordIdForSegment(segment) : null
      void loadTermMatches(sentenceId, activeSourceText.value)
    } else {
      activeSourceText.value = ''
      activeFileRecordId.value = null
    }
  }

  async function loadTermMatches(sentenceId: string, sourceText: string) {
    if (!sourceText) {
      termMatchesMap.value = {
        ...termMatchesMap.value,
        [sentenceId]: [],
      }
      return
    }
    try {
      const params = new URLSearchParams({ text: sourceText })
      const termContext = mergeViewId.value
        ? mergeViewDetail.value?.files.find((file) => (
            file.id === (fileRecordIdFromKey(sentenceId) || activeFileRecordId.value)
          ))
        : fileRecord.value
      const boundTermBaseIds = Array.from(new Set([
        ...(termContext?.term_base_ids || []),
        ...(termContext?.term_base_id ? [termContext.term_base_id] : []),
        ...(termContext?.qa_term_base_ids || []),
      ].filter(Boolean)))
      if (boundTermBaseIds.length === 0) {
        termMatchesMap.value = {
          ...termMatchesMap.value,
          [sentenceId]: [],
        }
        return
      }
      for (const termBaseId of boundTermBaseIds) {
        params.append('collection_ids', termBaseId)
      }
      const { data } = await http.get<{ matches: TermMatch[] }>('/termbase/match', {
        params,
      })
      termMatchesMap.value = {
        ...termMatchesMap.value,
        [sentenceId]: data.matches,
      }
    } catch {
      // 静默失败
    }
  }

  function refreshActiveTermMatches() {
    if (!activeSentenceId.value) {
      return Promise.resolve()
    }
    return loadTermMatches(activeSentenceId.value, activeSourceText.value)
  }

  function getTermMatches(sentenceId: string): TermMatch[] {
    return termMatchesMap.value[sentenceId] || []
  }

  function scheduleSync() {
    if (syncTimer !== null) {
      window.clearTimeout(syncTimer)
    }
    syncTimer = window.setTimeout(() => {
      void syncToBackend()
    }, AUTO_SYNC_DELAY_MS)
  }

  async function runSyncToBackend(): Promise<boolean> {
    saving.value = true
    const allUpdates = Object.entries(dirtyEntries.value) // [segmentKey, payload]
    syncMessage.value = translate('stores.segment.syncing', { count: allUpdates.length })
    try {
      if (mergeViewId.value) {
        // 合并模式：按 file_record_id 分组，对每个文件并行 PUT
        const grouped = new Map<string, Array<{ key: string; payload: SegmentUpdatePayload }>>()
        for (const [key, payload] of allUpdates) {
          const fileId = payload.file_record_id
          if (!fileId) {
            continue
          }
          const list = grouped.get(fileId) || []
          list.push({ key, payload })
          grouped.set(fileId, list)
        }
        const results = await Promise.all(
          Array.from(grouped.entries()).map(([fileId, items]) =>
            syncOneFile(fileId, items).then((res) => ({ fileId, items, data: res.data })),
          ),
        )
        return reconcileMergeSync(results)
      }

      // 单文件模式
      const result = await syncOneFile(fileRecord.value!.id, allUpdates.map(([key, payload]) => ({ key, payload })))
      const ok = reconcileSingleSync(fileRecord.value!.id, result)
      return ok
    } catch (error) {
      if (isEditLockedError(error)) {
        const message = getEditLockedMessage(error)
        syncMessage.value = message
        pushToast({
          tone: 'warn',
          title: '文件暂时不可编辑',
          message,
        })
        return false
      }
      console.error('Failed to sync segments:', error)
      const message = (error as any)?.response?.data?.detail || '保存失败，请重试'
      syncMessage.value = translate('stores.segment.syncFailed')
      pushToast({
        tone: 'error',
        title: '保存失败',
        message: String(message),
      })
      // 稍后重试
      scheduleSync()
      return false
    } finally {
      saving.value = false
    }
  }

  async function syncToBackend(): Promise<boolean> {
    if (dirtyCount.value === 0) {
      return true
    }
    // 单文件模式需要 fileRecord；合并模式按 dirty 条目自带 file_record_id 分组。
    if (!mergeViewId.value && !fileRecord.value) {
      return true
    }

    if (syncPromise) {
      const synced = await syncPromise
      if (!synced) {
        return false
      }
      // 当前保存过程中可能又产生了新编辑；翻页、切页等显式动作要等待这些修改落库。
      return dirtyCount.value === 0 ? true : syncToBackend()
    }

    if (syncTimer !== null) {
      window.clearTimeout(syncTimer)
      syncTimer = null
    }

    syncPromise = runSyncToBackend()
    try {
      return await syncPromise
    } finally {
      syncPromise = null
    }
  }

  /** 对单个文件执行批量 PUT；返回该文件的服务端响应（segments/conflicts/auto_tm/project_sync）。 */
  async function syncOneFile(
    fileId: string,
    items: Array<{ key: string; payload: SegmentUpdatePayload }>,
  ) {
    const updates = items.map((item) => item.payload)
    const { data } = await http.put<{
      updated_count: number
      conflicts?: SegmentConflictResponse[]
      auto_tm?: {
        queued_count: number
        skipped_no_collection_count: number
        skipped_invalid_count?: number
      }
      project_sync?: ProjectSegmentSyncSummary
      status_stats?: SegmentStatusStats | null
      workflow_progress?: WorkflowProgress[]
      segments?: Segment[]
    }>(`/file-records/${fileId}/segments`, {
      updates,
    })
    if (mergeViewId.value) {
      data.segments = (data.segments || []).map((segment) => withSegmentFileContext(segment, fileId))
    }
    return { data, items }
  }

  /** 单文件保存后：应用服务端段、清成功 dirty、处理冲突，返回是否成功。 */
  function reconcileSingleSync(fileId: string, result: ReturnType<typeof syncOneFile> extends Promise<infer R> ? R : never): boolean {
    const { data, items } = result
    void loadRevisions(fileId)
    const nextDirtyEntries = { ...dirtyEntries.value }
    const syncedSentenceIds: string[] = []
    const payloadByKey = new Map(items.map((item) => [item.key, item.payload]))
    for (const segment of data.segments || []) {
      const segmentKey = segment.sentence_id
      const payload = payloadByKey.get(segmentKey)
      const currentEntry = nextDirtyEntries[segmentKey]
      if (currentEntry && (!payload || !isSameDirtyPayload(currentEntry, payload))) {
        bumpDirtyBaseVersion(nextDirtyEntries, segmentKey, segment.version)
        applyServerSegmentMetadata(segmentKey, segment)
        continue
      }
      applyServerSegment(segment, false)
    }
    const updatedSentenceIds = new Set((data.segments || []).map((segment) => segment.sentence_id))
    const sensitiveConflicts = (data.conflicts || []).filter((conflict) => {
      const key = conflict.sentence_id
      const incomingSource = nextDirtyEntries[key]?.source || payloadByKey.get(key)?.source || 'manual'
      if ((conflict.current_target_text || '') === (nextDirtyEntries[key]?.target_text || '')) {
        bumpDirtyBaseVersion(nextDirtyEntries, key, conflict.current_version)
        return false
      }
      if (isSensitiveRemoteConflict(
        conflict.conflict_source,
        conflict.conflict_last_modified_by_id,
        incomingSource,
      )) {
        return true
      }
      bumpDirtyBaseVersion(nextDirtyEntries, key, conflict.current_version)
      return false
    })
    const conflictSentenceIds = new Set(sensitiveConflicts.map((conflict) => conflict.sentence_id))
    let hadConflict = false
    for (const { key, payload } of items) {
      const currentEntry = nextDirtyEntries[key]
      if (
        isSameDirtyPayload(currentEntry, payload)
        && (updatedSentenceIds.size === 0 || updatedSentenceIds.has(payload.sentence_id))
        && !conflictSentenceIds.has(payload.sentence_id)
      ) {
        delete nextDirtyEntries[key]
        syncedSentenceIds.push(key)
      } else if (conflictSentenceIds.has(payload.sentence_id)) {
        hadConflict = true
      }
    }
    dirtyEntries.value = nextDirtyEntries
    clearLocalRevisionDrafts(syncedSentenceIds)
    if (sensitiveConflicts.length > 0) {
      applySyncConflicts(sensitiveConflicts, fileId)
    }
    if (data.status_stats) {
      setSegmentStatusStats(data.status_stats)
    }
    if (fileRecord.value?.id === fileId && data.workflow_progress) {
      fileRecord.value = {
        ...fileRecord.value,
        workflow_progress: data.workflow_progress,
      }
    }
    return finishSyncState(hadConflict)
  }

  /** 合并模式保存后：汇总各文件结果，统一应用。 */
  function reconcileMergeSync(
    results: Array<{ fileId: string; items: Array<{ key: string; payload: SegmentUpdatePayload }>; data: Awaited<ReturnType<typeof syncOneFile>>['data'] }>,
  ): boolean {
    const nextDirtyEntries = { ...dirtyEntries.value }
    const syncedSentenceIds: string[] = []
    let hadConflict = false
    for (const result of results) {
      const payloadByKey = new Map(result.items.map((item) => [item.key, item.payload]))
      for (const segment of result.data.segments || []) {
        const segmentKey = segmentKeyOf(segment)
        const payload = payloadByKey.get(segmentKey)
        const currentEntry = nextDirtyEntries[segmentKey]
        if (currentEntry && (!payload || !isSameDirtyPayload(currentEntry, payload))) {
          bumpDirtyBaseVersion(nextDirtyEntries, segmentKey, segment.version)
          applyServerSegmentMetadata(segmentKey, segment)
          continue
        }
        applyServerSegment(segment, false)
      }
      // 合并模式下按文件加载修订意义不大（多文件），跳过 loadRevisions
      const updatedSentenceIds = new Set((result.data.segments || []).map((segment) => segment.sentence_id))
      const sensitiveConflicts = (result.data.conflicts || []).filter((conflict) => {
        const key = buildSegmentKey(result.fileId, conflict.sentence_id)
        const incomingSource = nextDirtyEntries[key]?.source || payloadByKey.get(key)?.source || 'manual'
        if ((conflict.current_target_text || '') === (nextDirtyEntries[key]?.target_text || '')) {
          bumpDirtyBaseVersion(nextDirtyEntries, key, conflict.current_version)
          return false
        }
        if (isSensitiveRemoteConflict(
          conflict.conflict_source,
          conflict.conflict_last_modified_by_id,
          incomingSource,
        )) {
          return true
        }
        bumpDirtyBaseVersion(nextDirtyEntries, key, conflict.current_version)
        return false
      })
      const conflictSentenceIds = new Set(sensitiveConflicts.map((conflict) => conflict.sentence_id))
      for (const { key, payload } of result.items) {
        const currentEntry = nextDirtyEntries[key]
        if (
          isSameDirtyPayload(currentEntry, payload)
          && (updatedSentenceIds.size === 0 || updatedSentenceIds.has(payload.sentence_id))
          && !conflictSentenceIds.has(payload.sentence_id)
        ) {
          delete nextDirtyEntries[key]
          syncedSentenceIds.push(key)
        } else if (conflictSentenceIds.has(payload.sentence_id)) {
          hadConflict = true
        }
      }
      if (sensitiveConflicts.length > 0) {
        applySyncConflicts(sensitiveConflicts, result.fileId)
      }
    }
    const statusStatsByFileId = new Map(
      results
        .filter((result) => result.data.status_stats)
        .map((result) => [result.fileId, result.data.status_stats as SegmentStatusStats]),
    )
    if (statusStatsByFileId.size > 0 && mergeViewDetail.value) {
      mergeViewDetail.value = {
        ...mergeViewDetail.value,
        files: mergeViewDetail.value.files.map((file) => (
          statusStatsByFileId.has(file.id)
            ? { ...file, status_stats: statusStatsByFileId.get(file.id)! }
            : file
        )),
      }
      setSegmentStatusStats(buildMergeViewStatusStats(mergeViewDetail.value))
    }
    const workflowProgressByFileId = new Map(
      results
        .filter((result) => result.data.workflow_progress)
        .map((result) => [result.fileId, result.data.workflow_progress as WorkflowProgress[]]),
    )
    if (workflowProgressByFileId.size > 0 && mergeViewDetail.value) {
      mergeViewDetail.value = {
        ...mergeViewDetail.value,
        files: mergeViewDetail.value.files.map((file) => (
          workflowProgressByFileId.has(file.id)
            ? { ...file, workflow_progress: workflowProgressByFileId.get(file.id)! }
            : file
        )),
      }
    }
    dirtyEntries.value = nextDirtyEntries
    clearLocalRevisionDrafts(syncedSentenceIds)
    return finishSyncState(hadConflict)
  }

  function applySyncConflicts(
    conflicts: Array<{ sentence_id: string; current_version: number; current_target_text: string }>,
    fileId: string,
  ) {
    const nextConflicts = { ...conflictEntries.value }
    for (const conflict of conflicts) {
      // 合并模式：冲突 key 用复合键（按文件定位）；单文件：即 sentence_id
      const conflictKey = mergeViewId.value ? `${fileId}:${conflict.sentence_id}` : conflict.sentence_id
      nextConflicts[conflictKey] = conflict.current_target_text
      const index = getSegmentIndex(conflictKey)
      if (index !== -1) {
        segments.value[index] = {
          ...segments.value[index],
          version: conflict.current_version,
        }
      }
    }
    conflictEntries.value = nextConflicts
  }

  function finishSyncState(hadConflict: boolean): boolean {
    if (hadConflict) {
      syncMessage.value = '检测到协同冲突，本地修改已保留。'
      pushToast({
        tone: 'warn',
        title: '保存遇到协同冲突',
        message: '其他用户已更新同一句段，请确认后再保存。',
      })
      if (dirtyCount.value > 0) {
        scheduleSync()
      }
      return false
    }
    const syncedAt = new Date()
    lastSyncedAt.value = syncedAt.toISOString()
    if (dirtyCount.value > 0) {
      syncMessage.value = translate('stores.segment.syncPending', { count: dirtyCount.value })
      scheduleSync()
    } else {
      syncMessage.value = translate('stores.segment.syncedAt', {
        time: syncedAt.toLocaleString('zh-CN', { hour12: false }),
      })
    }
    scheduleChangePollBurst()
    return true
  }

  async function resolvePersistedRevisionId(revisionId: string) {
    if (!isLocalRevisionId(revisionId)) {
      return revisionId
    }

    const localDraft = getLocalRevisionDraftById(revisionId)
    if (!localDraft) {
      throw new Error('当前本地修订已失效，请刷新页面后重试。')
    }

    const synced = await syncToBackend()
    if (!synced) {
      throw new Error('修订尚未保存，无法继续处理。')
    }

    const persistedRevision = getPendingRevision(localDraft.sentence_id)
    if (!persistedRevision) {
      throw new Error('修订保存后未返回可处理记录，请刷新页面后重试。')
    }
    return persistedRevision.id
  }

  async function acceptRevision(id: string) {
    const revisionId = await resolvePersistedRevisionId(id)
    const { data } = await http.patch<SegmentRevisionEntry>(`/revisions/${revisionId}`, {
      status: 'accepted',
    })
    upsertRevisionEntry(data)
    // 接受修订：将 after_text 应用到 segment
    applyLLMUpdate(data.sentence_id, data.after_text, data.source)
    return data
  }

  async function rejectRevision(id: string) {
    const revisionId = await resolvePersistedRevisionId(id)
    const { data } = await http.patch<SegmentRevisionEntry>(`/revisions/${revisionId}`, {
      status: 'rejected',
    })
    upsertRevisionEntry(data)
    // 拒绝修订：恢复 before_text 到 segment
    applyLLMUpdate(data.sentence_id, data.before_text, data.source)
    return data
  }

  async function batchAcceptRevisions() {
    if (!fileRecord.value) {
      return 0
    }

    const synced = await syncToBackend()
    if (!synced) {
      return 0
    }

    if (!revisionSettings.value.show_others_revisions) {
      await loadRevisions(fileRecord.value.id)
      const revisions = listVisiblePendingRevisions()
      let updatedCount = 0
      for (const revision of revisions) {
        await acceptRevision(revision.id)
        updatedCount += 1
      }
      await refreshCurrentSegmentPage()
      return updatedCount
    }

    const { data } = await http.post<{ updated_count: number }>(
      `/file-records/${fileRecord.value.id}/revisions/batch-accept`,
    )
    await refreshCurrentSegmentPage()
    return data.updated_count
  }

  async function batchRejectRevisions() {
    if (!fileRecord.value) {
      return 0
    }

    const synced = await syncToBackend()
    if (!synced) {
      return 0
    }

    if (!revisionSettings.value.show_others_revisions) {
      await loadRevisions(fileRecord.value.id)
      const revisions = listVisiblePendingRevisions()
      let updatedCount = 0
      for (const revision of revisions) {
        await rejectRevision(revision.id)
        updatedCount += 1
      }
      await refreshCurrentSegmentPage()
      return updatedCount
    }

    const { data } = await http.post<{ updated_count: number }>(
      `/file-records/${fileRecord.value.id}/revisions/batch-reject`,
    )
    await refreshCurrentSegmentPage()
    return data.updated_count
  }

  async function updateAllSegmentConfirmations(
    action: 'confirm' | 'cancel',
    target: SegmentBatchTarget = 'current_file',
    options: SegmentConfirmationOptions = {},
  ) {
    const payload = {
      action,
      range_start: options.rangeStart ?? undefined,
      range_end: options.rangeEnd ?? undefined,
    }
    if (mergeViewId.value) {
      const targetFileIds = target === 'merge_view'
        ? (mergeViewDetail.value?.files ?? [])
            .filter((file) => file.can_write !== false)
            .map((file) => file.id)
        : [activeFileRecordId.value].filter(Boolean) as string[]
      if (!targetFileIds.length) {
        return 0
      }
      const synced = await syncToBackend()
      if (!synced) {
        return 0
      }
      const results = await Promise.all(
        targetFileIds.map((fileId) =>
          http.post<{ updated_count: number }>(
            `/file-records/${fileId}/segments/confirmation`,
            target === 'merge_view' ? { action } : payload,
          ),
        ),
      )
      const updatedCount = results.reduce((sum, result) => sum + Number(result.data.updated_count || 0), 0)
      await refreshMergeViewDetail()
      await refreshMergeViewPage(resolveCurrentMergeQuery())
      return updatedCount
    }

    if (!fileRecord.value) {
      return 0
    }

    const synced = await syncToBackend()
    if (!synced) {
      return 0
    }

    const { data } = await http.post<{ updated_count: number }>(
      `/file-records/${fileRecord.value.id}/segments/confirmation`,
      payload,
    )
    await refreshCurrentSegmentPage()
    return data.updated_count
  }

  async function applyPartialRevision(revisionId: string, newText: string) {
    const persistedRevisionId = await resolvePersistedRevisionId(revisionId)
    // 部分接受修订：更新修订的 after_text，然后接受
    const { data } = await http.patch<SegmentRevisionEntry>(`/revisions/${persistedRevisionId}`, {
      status: 'accepted',
      after_text: newText,
    })
    upsertRevisionEntry(data)
    // 应用新的文本到 segment
    applyLLMUpdate(data.sentence_id, newText, data.source)
    return data
  }

  function applyLLMUpdate(
    sentenceId: string,
    targetText: string,
    source = 'llm',
    status?: string | null,
    llmInfo: { provider?: string | null, model?: string | null } = {},
    fileRecordId?: string | null,
  ) {
    // 合并模式：LLM 端点返回真实 sentence_id，需用文件 id 拼复合键定位。
    const segmentKey = mergeViewId.value
      ? buildSegmentKey(fileRecordId || activeFileRecordId.value, sentenceId)
      : sentenceId
    const index = getSegmentIndex(segmentKey)
    if (index === -1) {
      return
    }

    const currentSegment = segments.value[index]
    const isLLMSource = source === 'llm'
    // 兼容带标签版式译文：拆出纯译文用于展示/状态，带标签文本单独留存供内联标签编辑。
    const hasMarks = FORMAT_MARK_RE.test(targetText || '')
    const cleanTargetText = hasMarks ? (targetText || '').replace(FORMAT_MARK_RE_GLOBAL, '') : (targetText || '')
    const layoutTargetText = hasMarks ? (targetText || '') : ''
    const nextStatus = status || resolveUnconfirmedSegmentStatus(currentSegment, cleanTargetText)
    const nextSegment = {
      ...currentSegment,
      target_text: cleanTargetText,
      target_layout_text: layoutTargetText || null,
      source,
      status: nextStatus,
      llm_provider: isLLMSource ? (llmInfo.provider ?? currentSegment.llm_provider ?? null) : null,
      llm_model: isLLMSource ? (llmInfo.model ?? currentSegment.llm_model ?? null) : null,
    }
    segments.value[index] = nextSegment
    adjustSegmentStatusStats(currentSegment, nextSegment)
    markPreviewUpdate(segmentKey, cleanTargetText)
    updateRevisionBaselineAfterPrefill(segmentKey, cleanTargetText)

    const nextDirtyEntries = { ...dirtyEntries.value }
    delete nextDirtyEntries[segmentKey]
    dirtyEntries.value = nextDirtyEntries
    clearLocalRevisionDrafts([segmentKey])
  }

  function finishLLMCompletion(updatedCount: number, errorCount: number, total: number) {
    const hasNoSegments = total === 0 && updatedCount === 0 && errorCount === 0
    llmPlannedCount.value = total
    llmProcessedCount.value = Math.max(total, updatedCount + errorCount)
    llmErrorCount.value = errorCount

    if (hasNoSegments) {
      llmMessage.value = translate('stores.segment.llmNoSegments')
      pushToast({
        tone: 'info',
        title: translate('stores.segment.llmNoSegmentsToastTitle'),
        message: translate('stores.segment.llmNoSegmentsToastMessage'),
      })
      return
    }

    llmMessage.value = translate('stores.segment.llmCompleted', {
      updated: updatedCount,
      error: errorCount,
    })
    pushToast({
      tone: errorCount > 0 ? 'warn' : 'success',
      title: translate('stores.segment.llmCompletedToastTitle'),
      message: translate('stores.segment.llmCompletedToastMessage', {
        updated: updatedCount,
        error: errorCount,
      }),
    })
  }

  async function startLLMTranslation(
    scope: LLMTranslateScope,
    provider: LLMProvider,
    guidelineOptions: LLMGuidelineOptions = {},
  ) {
    if (llmRunning.value) {
      return
    }

    const isMergeViewBatch = Boolean(
      mergeViewId.value
      && guidelineOptions.mergeTarget === 'merge_view'
      && scope !== 'current_segment',
    )
    const mergeViewTargetFileIds = isMergeViewBatch
      ? (mergeViewDetail.value?.files ?? [])
          .filter((file) => file.can_write !== false)
          .map((file) => file.id)
      : []
    const llmFileId = mergeViewId.value ? activeFileRecordId.value : (fileRecord.value?.id ?? null)
    if (isMergeViewBatch && !mergeViewTargetFileIds.length) {
      pushToast({
        tone: 'warn',
        title: 'AI 修正未开始',
        message: '当前合并视图中没有可执行 AI 修正的可写文件。',
      })
      return
    }
    if (!isMergeViewBatch && !llmFileId) {
      return
    }

    // 合并模式下还原真实 sentence_id（前端持有的是复合键）
    const realSentenceId = mergeViewId.value && guidelineOptions.sentenceId
      ? sentenceIdFromKey(guidelineOptions.sentenceId)
      : (guidelineOptions.sentenceId || null)

    if (syncTimer !== null) {
      window.clearTimeout(syncTimer)
      syncTimer = null
    }
    if (dirtyCount.value > 0) {
      const synced = await syncToBackend()
      if (!synced) {
        return
      }
    }

    llmRunning.value = true
    llmAbortRequested = false
    llmPlannedCount.value = 0
    llmProcessedCount.value = 0
    llmErrorCount.value = 0
    llmMessage.value = translate('stores.segment.llmStarting')

    try {
      const token = window.localStorage.getItem('token')
      llmAbortController = new AbortController()

      const runFileLLMTranslation = async (
        targetFileId: string,
        eventHandler: (event: string, data: Record<string, unknown>) => void,
      ) => {
        const response = await fetch(`/api/file-records/${targetFileId}/llm-translate`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            scope,
            provider,
            model: guidelineOptions.model || null,
            sentence_id: realSentenceId,
            guideline_template_id: guidelineOptions.guidelineTemplateId || null,
            temporary_prompt: guidelineOptions.temporaryPrompt || '',
          }),
          signal: llmAbortController?.signal,
        })

        if (!response.ok) {
          let message = translate('stores.segment.llmRequestFailed')
          try {
            const payload = await response.json()
            message = String(payload.detail || message)
          } catch {
            // ignore parsing error
          }
          throw new Error(message)
        }

        try {
          await consumeLLMStream(
            response,
            ({ event, data }) => {
              if (llmAbortRequested) {
                return
              }
              eventHandler(event, data)
            },
            (reader) => {
              llmReader = reader
            },
          )
        } catch (error) {
          if (error instanceof Error && error.message === 'SSE 响应体为空。') {
            throw new Error(translate('stores.segment.llmNoStream'))
          }
          throw error
        }
      }

      if (isMergeViewBatch) {
        let aggregateTotal = 0
        let aggregateUpdatedCount = 0
        let aggregateErrorCount = 0

        for (const targetFileId of mergeViewTargetFileIds) {
          if (llmAbortRequested) {
            break
          }
          await runFileLLMTranslation(targetFileId, (event, data) => {
            if (event === 'start') {
              aggregateTotal += Number(data.total || 0)
              llmPlannedCount.value = aggregateTotal
              llmMessage.value = translate('stores.segment.llmStarted', { total: aggregateTotal })
              return
            }
            if (event === 'complete') {
              aggregateUpdatedCount += Number(data.updated_count || 0)
              aggregateErrorCount += Number(data.error_count || 0)
              return
            }
            handleLLMEvent(event, data, targetFileId)
          })
        }

        if (!llmAbortRequested) {
          finishLLMCompletion(aggregateUpdatedCount, aggregateErrorCount, aggregateTotal)
        }
      } else {
        await runFileLLMTranslation(llmFileId!, (event, data) => {
          handleLLMEvent(event, data, llmFileId)
        })
      }

      if (!llmAbortRequested && mergeViewId.value) {
        await refreshMergeViewDetail()
        await refreshMergeViewPage(resolveCurrentMergeQuery())
      } else if (!llmAbortRequested && fileRecord.value) {
        await loadRevisions(fileRecord.value.id)
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        llmMessage.value = translate('stores.segment.llmStopped')
        pushToast({
          tone: 'info',
          title: translate('stores.segment.llmStoppedToastTitle'),
          message: translate('stores.segment.llmStoppedToastMessage'),
        })
        return
      }
      throw error
    } finally {
      llmAbortController = null
      llmReader = null
      llmRunning.value = false
    }
  }

  function handleLLMEvent(event: string, data: Record<string, unknown>, fileRecordId?: string | null) {
    if (event === 'start') {
      const total = Number(data.total || 0)
      llmPlannedCount.value = total
      llmProcessedCount.value = 0
      llmErrorCount.value = 0
      llmMessage.value = translate('stores.segment.llmStarted', { total })
      return
    }

    if (event === 'segment') {
      llmProcessedCount.value += 1
      const eventFileRecordId = typeof data.file_record_id === 'string'
        ? data.file_record_id
        : fileRecordId
      applyLLMUpdate(
        String(data.sentence_id || ''),
        String(data.target_text || ''),
        String(data.source || 'llm'),
        typeof data.status === 'string' ? data.status : undefined,
        {
          provider: typeof data.provider === 'string' ? data.provider : null,
          model: typeof data.model === 'string' ? data.model : null,
        },
        eventFileRecordId,
      )
      llmMessage.value = translate('stores.segment.llmProgress', {
        processed: llmProcessedCount.value,
        planned: Math.max(llmPlannedCount.value, llmProcessedCount.value),
      })
      return
    }

    if (event === 'skipped') {
      llmProcessedCount.value += 1
      llmMessage.value = translate('stores.segment.llmProgress', {
        processed: llmProcessedCount.value,
        planned: Math.max(llmPlannedCount.value, llmProcessedCount.value),
      })
      return
    }

    if (event === 'error') {
      llmProcessedCount.value += 1
      llmErrorCount.value += 1
      llmMessage.value = translate('stores.segment.llmError', {
        message: String(data.message || '未知错误'),
      })
      return
    }

    if (event === 'complete') {
      const updatedCount = Number(data.updated_count || 0)
      const errorCount = Number(data.error_count || 0)
      const total = Number(data.total || llmPlannedCount.value || updatedCount + errorCount)
      finishLLMCompletion(updatedCount, errorCount, total)
    }
  }

  async function abortLLM() {
    if (!llmRunning.value) {
      return
    }

    llmAbortRequested = true
    llmAbortController?.abort()
    try {
      await llmReader?.cancel()
    } catch {
      // ignore cancel errors
    }
    llmMessage.value = translate('stores.segment.llmStopped')
  }

  async function downloadTranslatedFile() {
    if (!fileRecord.value || !canExport.value) {
      return
    }

    if (dirtyCount.value > 0) {
      const synced = await syncToBackend()
      if (!synced) {
        return
      }
    }

    const { data: task } = await http.post<FileExportTask>(
      `/file-records/${fileRecord.value.id}/exports`,
      null,
      { params: { type: 'original' } },
    )
    const completedTask = await waitForFileExportTask(task)
    const response = await http.get(`/file-records/export-tasks/${completedTask.task_id}/download`, {
      responseType: 'blob',
    })
    const filename = resolveDownloadFilename(
      response.headers['content-disposition'],
      buildTranslatedTaskFilename(fileRecord.value.filename),
    )
    downloadBlob(response.data, filename)
  }

  async function waitForFileExportTask(task: FileExportTask) {
    let currentTask = task
    while (true) {
      if (currentTask.status === 'completed') {
        return currentTask
      }
      if (currentTask.status === 'failed') {
        throw new Error(currentTask.error || currentTask.message || '导出失败。')
      }

      await new Promise((resolve) => window.setTimeout(resolve, EXPORT_POLL_INTERVAL_MS))
      const { data } = await http.get<FileExportTask>(`/file-records/export-tasks/${currentTask.task_id}`)
      currentTask = data
    }
  }

  async function loadSaveToTMStats(scope: 'translated' | 'confirmed' | 'all' = 'translated') {
    if (!fileRecord.value) {
      saveToTMStats.value = null
      return null
    }

    const { data } = await http.get<SaveToTMStats>(`/file-records/${fileRecord.value.id}/save-to-tm/stats`, {
      params: { scope },
    })
    saveToTMStats.value = data
    return data
  }

  async function splitSegment(sentenceId: string, splitOffset: number) {
    const segmentKey = sentenceId
    const index = getSegmentIndex(segmentKey)
    if (index === -1) {
      return null
    }
    const segment = segments.value[index]
    const fileId = mergeViewId.value
      ? (fileRecordIdForSegment(segment) ?? null)
      : (fileRecord.value?.id ?? null)
    if (!fileId) {
      return null
    }
    // 先同步未保存的修改
    if (dirtyCount.value > 0) {
      await syncToBackend()
    }
    const { data } = await http.post<{ first: Segment; second: Segment }>(
      `/file-records/${fileId}/segments/${segment.sentence_id}/split`,
      { split_offset: splitOffset },
    )
    const firstSegment = withSegmentFileContext(data.first, fileId)
    const secondSegment = withSegmentFileContext(data.second, fileId)
    // 更新本地 segments 列表
    if (index !== -1) {
      segments.value.splice(index, 1, firstSegment, secondSegment)
      rebuildSegmentIndexMap()
      totalSegmentCount.value += 1
      matchedSegmentCount.value += 1
    }
    // 使预览缓存失效，强制下次重新加载
    invalidatePreviewCache()
    return data
  }

  async function mergeSegment(sentenceId: string, targetSentenceId: string) {
    const baseKey = sentenceId
    const otherKey = targetSentenceId
    const baseIndex = getSegmentIndex(baseKey)
    if (baseIndex === -1) {
      return null
    }
    const segment = segments.value[baseIndex]
    const fileId = mergeViewId.value
      ? (fileRecordIdForSegment(segment) ?? null)
      : (fileRecord.value?.id ?? null)
    if (!fileId) {
      return null
    }
    // 合并模式下还原真实 sentence_id
    const realBaseId = mergeViewId.value ? sentenceIdFromKey(baseKey) : baseKey
    const realOtherId = mergeViewId.value ? sentenceIdFromKey(otherKey) : otherKey
    // 先同步未保存的修改
    if (dirtyCount.value > 0) {
      await syncToBackend()
    }
    const { data } = await http.post<{ merged: Segment; deleted_sentence_id: string }>(
      `/file-records/${fileId}/segments/${realBaseId}/merge`,
      { target_sentence_id: realOtherId },
    )
    const mergedSegment = withSegmentFileContext(data.merged, fileId)
    // 更新本地 segments 列表
    const firstIndex = getSegmentIndex(baseKey)
    const secondIndex = getSegmentIndex(otherKey)
    if (firstIndex !== -1) {
      segments.value[firstIndex] = mergedSegment
    }
    if (secondIndex !== -1) {
      segments.value.splice(secondIndex, 1)
    }
    rebuildSegmentIndexMap()
    totalSegmentCount.value -= 1
    matchedSegmentCount.value -= 1
    // 使预览缓存失效，强制下次重新加载
    invalidatePreviewCache()
    return data
  }

  function invalidatePreviewCache() {
    previewLoaded = false
    previewCacheKey = ''
  }

  return {
    fileRecord,
    segments,
    previewHtml,
    previewSupported,
    activeSentenceId,
    activeSourceText,
    activeFileRecordId,
    termMatchesMap,
    loading,
    loadingMoreSegments,
    loadingAllSegments,
    previewLoading,
    saving,
    llmRunning,
    syncMessage,
    llmMessage,
    lastSyncedAt,
    lastModifiedAt,
    llmPlannedCount,
    llmProcessedCount,
    llmErrorCount,
    llmProgressPercent,
    dirtyCount,
    conflictCount,
    canExport,
    pendingRevisionCount,
    loadedSegmentCount,
    totalSegmentCount,
    matchedSegmentCount,
    segmentStatusStats,
    currentPage,
    pageSize,
    currentPageStart,
    currentPageEnd,
    segmentFilters,
    saveToTMStats,
    hasMoreSegments,
    allSegmentsLoaded,
    previewUpdateToken,
    lastPreviewUpdatedSentenceId,
    lastPreviewUpdatedText,
    revisionTrackingEnabled,
    revisionHistory,
    revisionSettings,
    conflictEntries,
    // 合并视图状态
    mergeViewId,
    mergeViewDetail,
    mergeViewGroups,
    segmentKeyOf,
    getPendingRevision,
    getRevisionTrace,
    startRevisionTracking,
    stopRevisionTracking,
    ensureRevisionTrackingBaselines,
    fetchRevisionSettings,
    saveRevisionSettings,
    loadTask,
    loadMergeView,
    refreshMergeViewPage,
    refreshMergeViewDetail,
    loadSegmentPage,
    refreshCurrentSegmentPage,
    loadMoreSegments,
    ensureAllSegmentsLoaded,
    ensurePreviewLoaded,
    ensureSentenceLoaded,
    loadRevisions,
    loadSaveToTMStats,
    applyServerSegmentPatches,
    updateTarget,
    updateSource,
    setProjectSyncDisabled,
    disableProjectSyncForCurrentFile,
    setActiveSentence,
    refreshActiveTermMatches,
    getTermMatches,
    syncToBackend,
    acceptRevision,
    rejectRevision,
    applyPartialRevision,
    batchAcceptRevisions,
    batchRejectRevisions,
    updateAllSegmentConfirmations,
    startLLMTranslation,
    abortLLM,
    downloadTranslatedFile,
    splitSegment,
    mergeSegment,
    resetState,
  }
})
