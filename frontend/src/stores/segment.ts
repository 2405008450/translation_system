import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import axios from 'axios'

import { http } from '../api/http'
import { pushToast } from '../composables/useToast'
import { buildTranslatedTaskFilename } from '../constants/taskFiles'
import { translate } from '../i18n'
import type {
  FileRecordDetail,
  FileRecordPreview,
  LLMGuidelineOptions,
  LLMProvider,
  LLMTranslateScope,
  ProjectSegmentSyncSummary,
  ProjectSyncDisableResult,
  Segment,
  SegmentPageResponse,
  SegmentRevisionEntry,
  SegmentStatusStats,
  SegmentUpdatePayload,
  SaveToTMStats,
  TermMatch,
} from '../types/api'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'
import { consumeLLMStream } from '../utils/llmStream'

const DEFAULT_SEGMENT_PAGE_SIZE = 100
const MAX_SEGMENT_PAGE_SIZE = 500
const AUTO_SYNC_DELAY_MS = 1500
const CHANGE_POLL_INTERVAL_MS = 10000
const CHANGE_POLL_BURST_DELAYS_MS = [1200, 3000, 6000, 10000]
const EXPORT_POLL_INTERVAL_MS = 1200

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
    exact: 0,
    fuzzy: 0,
    none: 0,
    confirmed: 0,
    empty_target: 0,
  }
}

function isCountedSegmentStatus(status: string): status is 'exact' | 'fuzzy' | 'none' | 'confirmed' {
  return status === 'exact' || status === 'fuzzy' || status === 'none' || status === 'confirmed'
}

function hasEmptyTarget(value: string | null | undefined) {
  return !(value || '').trim()
}

function normalizeMatchText(value: string | null | undefined) {
  return (value || '').trim().replace(/\s+/g, ' ').toLowerCase()
}

function resolveUnconfirmedSegmentStatus(segment: Segment, targetText = segment.target_text) {
  if (hasEmptyTarget(targetText)) {
    return 'none'
  }

  const sourceText = normalizeMatchText(segment.source_text)
  const matchedSourceText = normalizeMatchText(segment.matched_source_text)
  const score = Number(segment.score || 0)
  if (score >= 0.999 || (matchedSourceText && matchedSourceText === sourceText)) {
    return 'exact'
  }
  if (score > 0 || matchedSourceText) {
    return 'fuzzy'
  }
  return 'none'
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

export interface SegmentPageQuery {
  page?: number
  pageSize?: number
  scope?: string
  sourceQuery?: string
  targetQuery?: string
  sourceExclude?: string
  targetExclude?: string
  searchFuzzy?: boolean
  statusFilters?: string[]
  matchFilters?: string[]
  sourceFilters?: string[]
  workflowStepIds?: string[]
}

interface SegmentChangeResponse {
  file_record_id: string
  server_time: string
  next_cursor?: string
  has_more?: boolean
  segments: Segment[]
}

export const useSegmentStore = defineStore('segment', () => {
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
    statusFilters: [] as string[],
    matchFilters: [] as string[],
    sourceFilters: [] as string[],
    workflowStepIds: [] as string[],
  })
  const saveToTMStats = ref<SaveToTMStats | null>(null)
  const revisionHistory = ref<Record<string, SegmentRevisionEntry[]>>({})
  const localRevisionDrafts = ref<Record<string, SegmentRevisionEntry>>({})
  const localRevisionBaselines = ref<Record<string, string>>({})
  const revisionTrackingEnabled = ref(false)

  const segmentIndexMap = new Map<string, number>()
  let syncTimer: number | null = null
  let changePollTimer: number | null = null
  let changePollBurstTimers: number[] = []
  let changeCursor: string | null = null
  let pollingChanges = false
  let loadMorePromise: Promise<boolean> | null = null
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
        if (isPendingManualRevision(entry)) {
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
    return revisionHistory.value[sentenceId]?.find(isPendingManualRevision) || null
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
    ensureRevisionTrackingBaselines()
  }

  function stopRevisionTracking() {
    revisionTrackingEnabled.value = false
    // 开关只控制修订痕迹是否显示；基准必须保留，避免再次开启时刷新快照。
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
      segmentIndexMap.set(segment.sentence_id, index)
    })
    segments.value = nextSegments
  }

  function applyServerSegment(nextSegment: Segment, markPreview = true) {
    const index = getSegmentIndex(nextSegment.sentence_id)
    if (index === -1) {
      return
    }

    const previousSegment = segments.value[index]
    segments.value[index] = nextSegment
    adjustSegmentStatusStats(previousSegment, nextSegment)
    if (activeSentenceId.value === nextSegment.sentence_id) {
      activeSourceText.value = nextSegment.source_text || ''
    }
    if (markPreview && previousSegment.target_text !== nextSegment.target_text) {
      markPreviewUpdate(nextSegment.sentence_id, nextSegment.target_text || '')
    }
  }

  function applyServerSegments(nextSegments: Segment[], markPreview = true) {
    for (const segment of nextSegments) {
      applyServerSegment(segment, markPreview)
    }
  }

  function setSegmentStatusStats(stats?: SegmentStatusStats | null) {
    segmentStatusStats.value = stats ? { ...createEmptySegmentStatusStats(), ...stats } : createEmptySegmentStatusStats()
  }

  function adjustSegmentStatusStats(previousSegment: Segment, nextSegment: Segment) {
    const nextStats = { ...segmentStatusStats.value }
    if (isCountedSegmentStatus(previousSegment.status)) {
      nextStats[previousSegment.status] = Math.max(0, nextStats[previousSegment.status] - 1)
    }
    if (isCountedSegmentStatus(nextSegment.status)) {
      nextStats[nextSegment.status] += 1
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
      statusFilters: normalizeStringArray(query.statusFilters ?? segmentFilters.value.statusFilters),
      matchFilters: normalizeStringArray(query.matchFilters ?? segmentFilters.value.matchFilters),
      sourceFilters: normalizeStringArray(query.sourceFilters ?? segmentFilters.value.sourceFilters),
      workflowStepIds: normalizeStringArray(query.workflowStepIds ?? segmentFilters.value.workflowStepIds),
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
      status_filters: serializeFilterArray(resolved.statusFilters),
      match_filters: serializeFilterArray(resolved.matchFilters),
      source_filters: serializeFilterArray(resolved.sourceFilters),
      workflow_step_ids: serializeFilterArray(resolved.workflowStepIds),
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
      statusFilters: [...segmentFilters.value.statusFilters],
      matchFilters: [...segmentFilters.value.matchFilters],
      sourceFilters: [...segmentFilters.value.sourceFilters],
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
      statusFilters: string[]
      matchFilters: string[]
      sourceFilters: string[]
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
      statusFilters: [...resolved.statusFilters],
      matchFilters: [...resolved.matchFilters],
      sourceFilters: [...resolved.sourceFilters],
      workflowStepIds: [...resolved.workflowStepIds],
    }
    totalSegmentCount.value = data.total_segments
    matchedSegmentCount.value = data.matched_segments
    setSegmentStatusStats(data.status_stats)
    resetSegments(data.segments)
    if (data.server_time) {
      changeCursor = data.server_time
    }
    resetPreviewState()
  }

  function startChangePolling() {
    stopChangePolling()
    if (!fileRecord.value) {
      return
    }
    changePollTimer = window.setInterval(() => {
      void pollSegmentChanges()
    }, CHANGE_POLL_INTERVAL_MS)
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
    if (changePollTimer !== null) {
      window.clearInterval(changePollTimer)
      changePollTimer = null
    }
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
      const nextConflicts = { ...conflictEntries.value }
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
              nextConflicts[remoteSegment.sentence_id] = remoteSegment.target_text || ''
              conflictAdded = true
              const index = getSegmentIndex(remoteSegment.sentence_id)
              if (index !== -1) {
                segments.value[index] = {
                  ...segments.value[index],
                  version: remoteVersion,
                  updated_at: remoteSegment.updated_at,
                }
              }
              continue
            }
          }
          applyServerSegment(remoteSegment)
        }
        changeCursor = data.next_cursor || data.server_time || new Date().toISOString()
        hasMore = Boolean(data.has_more)
      }

      conflictEntries.value = nextConflicts
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
      statusFilters: [],
      matchFilters: [],
      sourceFilters: [],
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
    revisionTrackingEnabled.value = false
    loadMorePromise = null
    llmAbortController = null
    llmReader = null
    llmAbortRequested = false
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
        statusFilters: query.statusFilters ?? [],
        matchFilters: query.matchFilters ?? [],
        sourceFilters: query.sourceFilters ?? [],
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
        statusFilters: [...resolved.statusFilters],
        matchFilters: [...resolved.matchFilters],
        sourceFilters: [...resolved.sourceFilters],
        workflowStepIds: [...resolved.workflowStepIds],
      }
      const detail = await fetchFileRecordDetail(fileRecordId, resolved.pageSize)
      fileRecord.value = {
        ...detail,
        segments: [],
      }
      changeCursor = detail.server_time || new Date().toISOString()
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
        && resolved.workflowStepIds.length === 0
      ) {
        matchedSegmentCount.value = detail.total_segments
        resetSegments(detail.segments)
      } else {
        const page = await fetchSegmentPage(fileRecordId, resolved)
        applySegmentPageData(page.data, page.resolved)
      }
      await loadRevisions(fileRecordId)
      startChangePolling()
    } finally {
      loading.value = false
    }
  }

  async function loadSegmentPage(query: SegmentPageQuery = {}) {
    if (!fileRecord.value) {
      return false
    }

    if (loadMorePromise) {
      return loadMorePromise
    }

    loadingMoreSegments.value = true
    loadMorePromise = (async () => {
      const { data, resolved } = await fetchSegmentPage(fileRecord.value!.id, query)
      applySegmentPageData(data, resolved)
      await loadRevisions(fileRecord.value!.id, resolved)
      if (segments.value[0] && !segments.value.some((segment) => segment.sentence_id === activeSentenceId.value)) {
        setActiveSentence(segments.value[0].sentence_id)
      } else if (!segments.value.length) {
        setActiveSentence(null)
      }
      return data.segments.length > 0
    })()

    try {
      return await loadMorePromise
    } finally {
      loadMorePromise = null
      loadingMoreSegments.value = false
    }
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
      segmentIndexMap.set(segment.sentence_id, index)
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
    const index = getSegmentIndex(sentenceId)
    if (index === -1) {
      return
    }

    const segment = segments.value[index]
    const confirm = options.confirm === true
    if (revisionTrackingEnabled.value) {
      upsertLocalRevisionDraft(segment, targetText)
    }
    const nextSegment = {
      ...segment,
      target_text: targetText,
      target_html: targetHtml || null,
      source: 'manual',
      status: confirm ? 'confirmed' : resolveUnconfirmedSegmentStatus(segment, targetText),
      llm_provider: null,
      llm_model: null,
    }
    segments.value[index] = nextSegment
    adjustSegmentStatusStats(segment, nextSegment)
    markPreviewUpdate(sentenceId, targetText)

    dirtyEntries.value = {
      ...dirtyEntries.value,
      [sentenceId]: {
        sentence_id: sentenceId,
        target_text: targetText,
        target_html: targetHtml || null,
        source: 'manual',
        track_revision: revisionTrackingEnabled.value,
        base_version: segment.version ?? 1,
        confirm,
      },
    }
    const nextConflicts = { ...conflictEntries.value }
    delete nextConflicts[sentenceId]
    conflictEntries.value = nextConflicts
    syncMessage.value = translate('stores.segment.syncPending', { count: dirtyCount.value })
    scheduleSync()
  }

  async function updateSource(sentenceId: string, sourceText: string) {
    const index = getSegmentIndex(sentenceId)
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

    // 直接同步到后端
    try {
      await http.put(`/file-records/${fileRecord.value?.id}/segments/${sentenceId}/source`, {
        source_text: sourceText,
      })
    } catch (error) {
      // 恢复原值
      segments.value[index] = segment
      throw error
    }
  }

  async function setProjectSyncDisabled(sentenceId: string, disabled: boolean) {
    if (!fileRecord.value) {
      return
    }
    const index = getSegmentIndex(sentenceId)
    const previousSegment = index === -1 ? null : segments.value[index]
    const willClearProjectSyncedTarget = Boolean(
      disabled
      && previousSegment?.source === 'project_sync'
      && (previousSegment.target_text || '').trim(),
    )
    try {
      const { data } = await http.patch<Segment>(
        `/file-records/${fileRecord.value.id}/segments/${sentenceId}/project-sync`,
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
    activeSentenceId.value = sentenceId
    if (sentenceId) {
      const segment = segments.value.find((s) => s.sentence_id === sentenceId)
      activeSourceText.value = segment?.source_text || ''
      void loadTermMatches(sentenceId, activeSourceText.value)
    } else {
      activeSourceText.value = ''
    }
  }

  async function loadTermMatches(sentenceId: string, sourceText: string) {
    if (!sourceText) {
      return
    }
    try {
      const params = new URLSearchParams({ text: sourceText })
      const boundTermBaseIds = fileRecord.value?.term_base_ids?.length
        ? fileRecord.value.term_base_ids
        : (fileRecord.value?.term_base_id ? [fileRecord.value.term_base_id] : [])
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

  async function syncToBackend() {
    if (!fileRecord.value || dirtyCount.value === 0) {
      return true
    }

    if (saving.value) {
      scheduleSync()
      return false
    }

    if (syncTimer !== null) {
      window.clearTimeout(syncTimer)
      syncTimer = null
    }

    const updates = Object.values(dirtyEntries.value)
    saving.value = true
    syncMessage.value = translate('stores.segment.syncing', { count: updates.length })
    try {
      const { data } = await http.put<{
        updated_count: number
        conflicts?: Array<{
          sentence_id: string
          current_version: number
          attempted_version: number | null
          current_target_text: string
        }>
        auto_tm?: {
          queued_count: number
          skipped_no_collection_count: number
          skipped_invalid_count?: number
        }
        project_sync?: ProjectSegmentSyncSummary
        segments?: Segment[]
      }>(`/file-records/${fileRecord.value.id}/segments`, {
        updates,
      })
      applyServerSegments(data.segments || [], false)
      await loadRevisions(fileRecord.value.id)
      const nextDirtyEntries = { ...dirtyEntries.value }
      const syncedSentenceIds: string[] = []
      const updatedSentenceIds = new Set((data.segments || []).map((segment) => segment.sentence_id))
      const conflictSentenceIds = new Set((data.conflicts || []).map((conflict) => conflict.sentence_id))
      for (const update of updates) {
        const currentEntry = nextDirtyEntries[update.sentence_id]
        if (
          currentEntry
          && currentEntry.target_text === update.target_text
          && (currentEntry.target_html || null) === (update.target_html || null)
          && currentEntry.source === update.source
          && Boolean(currentEntry.confirm) === Boolean(update.confirm)
          && (updatedSentenceIds.size === 0 || updatedSentenceIds.has(update.sentence_id))
          && !conflictSentenceIds.has(update.sentence_id)
        ) {
          delete nextDirtyEntries[update.sentence_id]
          syncedSentenceIds.push(update.sentence_id)
        }
      }
      dirtyEntries.value = nextDirtyEntries
      clearLocalRevisionDrafts(syncedSentenceIds)
      if ((data.conflicts || []).length > 0) {
        const nextConflicts = { ...conflictEntries.value }
        for (const conflict of data.conflicts || []) {
          nextConflicts[conflict.sentence_id] = conflict.current_target_text
          const index = getSegmentIndex(conflict.sentence_id)
          if (index !== -1) {
            segments.value[index] = {
              ...segments.value[index],
              version: conflict.current_version,
            }
          }
        }
        conflictEntries.value = nextConflicts
        syncMessage.value = '检测到协同冲突，本地修改已保留。'
        pushToast({
          tone: 'warn',
          title: '保存遇到协同冲突',
          message: '其他用户已更新同一句段，请确认后再保存。',
        })
        return false
      }
      if (data.auto_tm?.skipped_no_collection_count) {
        pushToast({
          tone: 'info',
          title: '未自动写入记忆库',
          message: '当前文件未绑定记忆库，确认译文已保存。',
        })
      }
      if (data.project_sync?.filled_count) {
        pushToast({
          tone: 'success',
          title: '项目同步完成',
          message: `已自动填充 ${data.project_sync.filled_count} 条相同原文句段。`,
        })
      }
      if (data.project_sync?.updated_count) {
        pushToast({
          tone: 'success',
          title: '项目同步已更新',
          message: `已自动更新 ${data.project_sync.updated_count} 条重复句段译文。`,
        })
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
      if (syncedSentenceIds.length > 0 || (data.segments || []).length > 0) {
        scheduleChangePollBurst()
      }
      return true
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

    const { data } = await http.post<{ updated_count: number }>(
      `/file-records/${fileRecord.value.id}/revisions/batch-reject`,
    )
    await refreshCurrentSegmentPage()
    return data.updated_count
  }

  async function updateAllSegmentConfirmations(action: 'confirm' | 'cancel') {
    if (!fileRecord.value) {
      return 0
    }

    const synced = await syncToBackend()
    if (!synced) {
      return 0
    }

    const { data } = await http.post<{ updated_count: number }>(
      `/file-records/${fileRecord.value.id}/segments/confirmation`,
      { action },
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
  ) {
    const index = getSegmentIndex(sentenceId)
    if (index === -1) {
      return
    }

    const currentSegment = segments.value[index]
    const isLLMSource = source === 'llm'
    const nextStatus = status || resolveUnconfirmedSegmentStatus(currentSegment, targetText)
    const nextSegment = {
      ...currentSegment,
      target_text: targetText,
      source,
      status: nextStatus,
      llm_provider: isLLMSource ? (llmInfo.provider ?? currentSegment.llm_provider ?? null) : null,
      llm_model: isLLMSource ? (llmInfo.model ?? currentSegment.llm_model ?? null) : null,
    }
    segments.value[index] = nextSegment
    adjustSegmentStatusStats(currentSegment, nextSegment)
    markPreviewUpdate(sentenceId, targetText)
    updateRevisionBaselineAfterPrefill(sentenceId, targetText)

    const nextDirtyEntries = { ...dirtyEntries.value }
    delete nextDirtyEntries[sentenceId]
    dirtyEntries.value = nextDirtyEntries
    clearLocalRevisionDrafts([sentenceId])
  }

  async function startLLMTranslation(
    scope: LLMTranslateScope,
    provider: LLMProvider,
    guidelineOptions: LLMGuidelineOptions = {},
  ) {
    if (!fileRecord.value || llmRunning.value) {
      return
    }

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
      const response = await fetch(`/api/file-records/${fileRecord.value.id}/llm-translate`, {
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
          sentence_id: guidelineOptions.sentenceId || null,
          guideline_template_id: guidelineOptions.guidelineTemplateId || null,
          temporary_prompt: guidelineOptions.temporaryPrompt || '',
        }),
        signal: llmAbortController.signal,
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
            handleLLMEvent(event, data)
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

      if (!llmAbortRequested && fileRecord.value) {
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

  function handleLLMEvent(event: string, data: Record<string, unknown>) {
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
      applyLLMUpdate(
        String(data.sentence_id || ''),
        String(data.target_text || ''),
        String(data.source || 'llm'),
        typeof data.status === 'string' ? data.status : undefined,
        {
          provider: typeof data.provider === 'string' ? data.provider : null,
          model: typeof data.model === 'string' ? data.model : null,
        },
      )
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
      llmPlannedCount.value = total
      llmProcessedCount.value = Math.max(total, updatedCount + errorCount)
      llmErrorCount.value = errorCount
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
    if (!fileRecord.value) return null
    // 先同步未保存的修改
    if (dirtyCount.value > 0) {
      await syncToBackend()
    }
    const { data } = await http.post<{ first: Segment; second: Segment }>(
      `/file-records/${fileRecord.value.id}/segments/${sentenceId}/split`,
      { split_offset: splitOffset },
    )
    // 更新本地 segments 列表
    const index = getSegmentIndex(sentenceId)
    if (index !== -1) {
      segments.value.splice(index, 1, data.first, data.second)
      rebuildSegmentIndexMap()
      totalSegmentCount.value += 1
      matchedSegmentCount.value += 1
    }
    // 使预览缓存失效，强制下次重新加载
    invalidatePreviewCache()
    return data
  }

  async function mergeSegment(sentenceId: string, targetSentenceId: string) {
    if (!fileRecord.value) return null
    // 先同步未保存的修改
    if (dirtyCount.value > 0) {
      await syncToBackend()
    }
    const { data } = await http.post<{ merged: Segment; deleted_sentence_id: string }>(
      `/file-records/${fileRecord.value.id}/segments/${sentenceId}/merge`,
      { target_sentence_id: targetSentenceId },
    )
    // 更新本地 segments 列表
    const firstIndex = getSegmentIndex(sentenceId)
    const secondIndex = getSegmentIndex(targetSentenceId)
    if (firstIndex !== -1) {
      segments.value[firstIndex] = data.merged
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
    revisionHistory,
    conflictEntries,
    getPendingRevision,
    getRevisionTrace,
    startRevisionTracking,
    stopRevisionTracking,
    ensureRevisionTrackingBaselines,
    loadTask,
    loadSegmentPage,
    refreshCurrentSegmentPage,
    loadMoreSegments,
    ensureAllSegmentsLoaded,
    ensurePreviewLoaded,
    ensureSentenceLoaded,
    loadRevisions,
    loadSaveToTMStats,
    updateTarget,
    updateSource,
    setProjectSyncDisabled,
    disableProjectSyncForCurrentFile,
    setActiveSentence,
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
