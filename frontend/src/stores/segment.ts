import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { http } from '../api/http'
import { pushToast } from '../composables/useToast'
import { buildTranslatedTaskFilename } from '../constants/taskFiles'
import { translate } from '../i18n'
import type {
  FileRecordDetail,
  FileRecordPreview,
  LLMProvider,
  LLMTranslateScope,
  Segment,
  SegmentRevisionEntry,
  SegmentUpdatePayload,
} from '../types/api'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'

const SEGMENT_PAGE_SIZE = 1000
const AUTO_SYNC_DELAY_MS = 1500

function parseSSEChunk(chunk: string) {
  const eventMatch = chunk.match(/^event:\s*(.+)$/m)
  const dataMatch = chunk.match(/^data:\s*(.+)$/m)
  if (!eventMatch || !dataMatch) {
    return null
  }

  try {
    return {
      event: eventMatch[1].trim(),
      data: JSON.parse(dataMatch[1]),
    }
  } catch {
    return null
  }
}

export const useSegmentStore = defineStore('segment', () => {
  const fileRecord = ref<FileRecordDetail | null>(null)
  const segments = ref<Segment[]>([])
  const previewHtml = ref('')
  const previewSupported = ref(false)
  const activeSentenceId = ref<string | null>(null)
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
  const dirtyEntries = ref<Record<string, SegmentUpdatePayload>>({})
  const previewUpdateToken = ref(0)
  const lastPreviewUpdatedSentenceId = ref<string | null>(null)
  const lastPreviewUpdatedText = ref('')
  const totalSegmentCount = ref(0)
  const revisionHistory = ref<Record<string, SegmentRevisionEntry[]>>({})

  const segmentIndexMap = new Map<string, number>()
  let syncTimer: number | null = null
  let loadMorePromise: Promise<boolean> | null = null
  let previewPromise: Promise<void> | null = null
  let previewLoaded = false
  let llmAbortController: AbortController | null = null
  let llmReader: ReadableStreamDefaultReader<Uint8Array> | null = null
  let llmAbortRequested = false

  const dirtyCount = computed(() => Object.keys(dirtyEntries.value).length)
  const canExport = computed(() => Boolean(fileRecord.value?.can_export))
  const loadedSegmentCount = computed(() => segments.value.length)
  const hasMoreSegments = computed(() => loadedSegmentCount.value < totalSegmentCount.value)
  const allSegmentsLoaded = computed(() => (
    totalSegmentCount.value === 0
      ? !loading.value && loadedSegmentCount.value === 0
      : loadedSegmentCount.value >= totalSegmentCount.value
  ))
  const llmProgressPercent = computed(() => {
    if (llmPlannedCount.value <= 0) {
      return 0
    }
    return Math.min(100, Math.round((llmProcessedCount.value / llmPlannedCount.value) * 100))
  })
  const pendingRevisionCount = computed(() => (
    Object.values(revisionHistory.value).reduce((count, entries) => (
      count + entries.filter((entry) => entry.status === 'pending').length
    ), 0)
  ))

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
    return revisionHistory.value[sentenceId]?.find((entry) => entry.status === 'pending') || null
  }

  function resetPreviewState() {
    previewHtml.value = ''
    previewSupported.value = false
    previewLoading.value = false
    previewPromise = null
    previewLoaded = false
  }

  function resetSegments(nextSegments: Segment[] = []) {
    segmentIndexMap.clear()
    nextSegments.forEach((segment, index) => {
      segmentIndexMap.set(segment.sentence_id, index)
    })
    segments.value = nextSegments
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

  async function fetchSegmentPage(fileRecordId: string, skip: number, limit: number) {
    const { data } = await http.get<FileRecordDetail>(`/file-records/${fileRecordId}`, {
      params: {
        skip,
        limit,
      },
    })
    return data
  }

  async function loadRevisions(fileRecordId: string) {
    const { data } = await http.get<SegmentRevisionEntry[]>(`/file-records/${fileRecordId}/revisions`)
    setRevisionEntries(data)
    return data
  }

  function resetState() {
    if (syncTimer !== null) {
      window.clearTimeout(syncTimer)
      syncTimer = null
    }

    fileRecord.value = null
    resetSegments()
    resetPreviewState()
    totalSegmentCount.value = 0
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
    dirtyEntries.value = {}
    previewUpdateToken.value = 0
    lastPreviewUpdatedSentenceId.value = null
    lastPreviewUpdatedText.value = ''
    revisionHistory.value = {}
    loadMorePromise = null
    llmAbortController = null
    llmReader = null
    llmAbortRequested = false
  }

  async function loadTask(fileRecordId: string) {
    resetState()
    loading.value = true
    try {
      const detail = await fetchSegmentPage(fileRecordId, 0, SEGMENT_PAGE_SIZE)
      fileRecord.value = {
        ...detail,
        segments: [],
      }
      totalSegmentCount.value = detail.total_segments
      resetSegments(detail.segments)
      await loadRevisions(fileRecordId)
    } finally {
      loading.value = false
    }
  }

  async function loadMoreSegments() {
    if (!fileRecord.value || !hasMoreSegments.value) {
      return false
    }

    if (loadMorePromise) {
      return loadMorePromise
    }

    loadingMoreSegments.value = true
    loadMorePromise = (async () => {
      const detail = await fetchSegmentPage(
        fileRecord.value!.id,
        segments.value.length,
        SEGMENT_PAGE_SIZE,
      )
      totalSegmentCount.value = detail.total_segments
      appendSegments(detail.segments)
      return detail.segments.length > 0
    })()

    try {
      return await loadMorePromise
    } finally {
      loadMorePromise = null
      loadingMoreSegments.value = false
    }
  }

  async function ensureAllSegmentsLoaded() {
    if (!fileRecord.value || !hasMoreSegments.value) {
      return
    }

    if (loadingAllSegments.value) {
      while (loadingAllSegments.value) {
        await new Promise<void>((resolve) => window.setTimeout(resolve, 60))
      }
      return
    }

    loadingAllSegments.value = true
    try {
      while (hasMoreSegments.value) {
        const loaded = await loadMoreSegments()
        if (!loaded) {
          break
        }
      }
    } finally {
      loadingAllSegments.value = false
    }
  }

  async function fetchPreview(fileRecordId: string) {
    const { data } = await http.get<FileRecordPreview>(`/file-records/${fileRecordId}/preview`)
    return data
  }

  async function ensurePreviewLoaded() {
    if (!fileRecord.value || previewLoaded) {
      return
    }

    if (previewPromise) {
      return previewPromise
    }

    previewLoading.value = true
    previewPromise = (async () => {
      const preview = await fetchPreview(fileRecord.value!.id)
      previewHtml.value = preview.preview_html
      previewSupported.value = preview.supports_preview
      previewLoaded = true
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

  async function ensureSentenceLoaded(sentenceId: string) {
    let index = getSegmentIndex(sentenceId)
    while (index === -1 && hasMoreSegments.value) {
      const loaded = await loadMoreSegments()
      if (!loaded) {
        break
      }
      index = getSegmentIndex(sentenceId)
    }
    return index
  }

  function markPreviewUpdate(sentenceId: string, targetText: string) {
    lastPreviewUpdatedSentenceId.value = sentenceId
    lastPreviewUpdatedText.value = targetText
    previewUpdateToken.value += 1
  }

  function updateTarget(sentenceId: string, targetText: string) {
    const index = getSegmentIndex(sentenceId)
    if (index === -1) {
      return
    }

    const segment = segments.value[index]
    segments.value[index] = {
      ...segment,
      target_text: targetText,
      source: 'manual',
      status: 'confirmed',
    }
    markPreviewUpdate(sentenceId, targetText)

    dirtyEntries.value = {
      ...dirtyEntries.value,
      [sentenceId]: {
        sentence_id: sentenceId,
        target_text: targetText,
        source: 'manual',
      },
    }
    syncMessage.value = translate('stores.segment.syncPending', { count: dirtyCount.value })
    scheduleSync()
  }

  function setActiveSentence(sentenceId: string | null) {
    activeSentenceId.value = sentenceId
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
    if (!fileRecord.value || dirtyCount.value === 0 || saving.value) {
      return
    }

    if (syncTimer !== null) {
      window.clearTimeout(syncTimer)
      syncTimer = null
    }

    const updates = Object.values(dirtyEntries.value)
    saving.value = true
    syncMessage.value = translate('stores.segment.syncing', { count: updates.length })
    try {
      await http.put(`/file-records/${fileRecord.value.id}/segments`, {
        updates,
      })
      dirtyEntries.value = {}
      await loadRevisions(fileRecord.value.id)
      lastSyncedAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
      syncMessage.value = translate('stores.segment.syncedAt', { time: lastSyncedAt.value })
    } finally {
      saving.value = false
    }
  }

  async function acceptRevision(id: string) {
    const { data } = await http.patch<SegmentRevisionEntry>(`/revisions/${id}`, {
      status: 'accepted',
    })
    upsertRevisionEntry(data)
    // 接受修订：将 after_text 应用到 segment
    applyLLMUpdate(data.sentence_id, data.after_text, data.source, 'confirmed')
    return data
  }

  async function rejectRevision(id: string) {
    const { data } = await http.patch<SegmentRevisionEntry>(`/revisions/${id}`, {
      status: 'rejected',
    })
    upsertRevisionEntry(data)
    // 拒绝修订：恢复 before_text 到 segment
    applyLLMUpdate(data.sentence_id, data.before_text, data.source, 'confirmed')
    return data
  }

  async function batchAcceptRevisions() {
    if (!fileRecord.value) {
      return 0
    }

    const { data } = await http.post<{ updated_count: number }>(
      `/file-records/${fileRecord.value.id}/revisions/batch-accept`,
    )
    await loadRevisions(fileRecord.value.id)
    return data.updated_count
  }

  async function batchRejectRevisions() {
    if (!fileRecord.value) {
      return 0
    }

    const { data } = await http.post<{ updated_count: number }>(
      `/file-records/${fileRecord.value.id}/revisions/batch-reject`,
    )
    await loadRevisions(fileRecord.value.id)
    return data.updated_count
  }

  function applyLLMUpdate(sentenceId: string, targetText: string, source = 'llm', status = 'confirmed') {
    const index = getSegmentIndex(sentenceId)
    if (index === -1) {
      return
    }

    segments.value[index] = {
      ...segments.value[index],
      target_text: targetText,
      source,
      status,
    }
    markPreviewUpdate(sentenceId, targetText)

    const nextDirtyEntries = { ...dirtyEntries.value }
    delete nextDirtyEntries[sentenceId]
    dirtyEntries.value = nextDirtyEntries
  }

  async function startLLMTranslation(scope: LLMTranslateScope, provider: LLMProvider) {
    if (!fileRecord.value || llmRunning.value) {
      return
    }

    if (syncTimer !== null) {
      window.clearTimeout(syncTimer)
      syncTimer = null
    }
    if (dirtyCount.value > 0) {
      await syncToBackend()
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

      if (!response.body) {
        throw new Error(translate('stores.segment.llmNoStream'))
      }

      llmReader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      while (true) {
        const { done, value } = await llmReader.read()
        if (done) {
          break
        }

        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          const event = parseSSEChunk(part)
          if (!event) {
            continue
          }
          if (llmAbortRequested) {
            continue
          }
          handleLLMEvent(event.event, event.data)
        }
      }

      if (!llmAbortRequested && buffer.trim()) {
        const event = parseSSEChunk(buffer)
        if (event) {
          handleLLMEvent(event.event, event.data)
        }
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
        'confirmed',
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
      await syncToBackend()
    }

    const response = await http.get(`/file-records/${fileRecord.value.id}/export`, {
      responseType: 'blob',
    })
    const filename = resolveDownloadFilename(
      response.headers['content-disposition'],
      buildTranslatedTaskFilename(fileRecord.value.filename),
    )
    downloadBlob(response.data, filename)
  }

  return {
    fileRecord,
    segments,
    previewHtml,
    previewSupported,
    activeSentenceId,
    loading,
    loadingMoreSegments,
    loadingAllSegments,
    previewLoading,
    saving,
    llmRunning,
    syncMessage,
    llmMessage,
    llmPlannedCount,
    llmProcessedCount,
    llmErrorCount,
    llmProgressPercent,
    dirtyCount,
    canExport,
    pendingRevisionCount,
    loadedSegmentCount,
    totalSegmentCount,
    hasMoreSegments,
    allSegmentsLoaded,
    previewUpdateToken,
    lastPreviewUpdatedSentenceId,
    lastPreviewUpdatedText,
    revisionHistory,
    getPendingRevision,
    loadTask,
    loadMoreSegments,
    ensureAllSegmentsLoaded,
    ensurePreviewLoaded,
    ensureSentenceLoaded,
    loadRevisions,
    updateTarget,
    setActiveSentence,
    syncToBackend,
    acceptRevision,
    rejectRevision,
    batchAcceptRevisions,
    batchRejectRevisions,
    startLLMTranslation,
    abortLLM,
    downloadTranslatedFile,
    resetState,
  }
})
