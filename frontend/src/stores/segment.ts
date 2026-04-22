import { computed, ref } from 'vue'
import { defineStore, storeToRefs } from 'pinia'

import { http } from '../api/http'
import { useTaskStore } from './task'
import type {
  FileRecordDetail,
  FileRecordPreview,
  LLMProvider,
  LLMTranslateScope,
  RevisionAuthorSummary,
  RevisionMark,
  Segment,
  SegmentRevision,
  SegmentUpdatePayload,
  TermMatch,
} from '../types/api'

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
  const taskStore = useTaskStore()
  
  const fileRecord = ref<FileRecordDetail | null>(null)
  const segments = ref<Segment[]>([])
  const previewHtml = ref('')
  const previewSupported = ref(false)
  const activeSentenceId = ref<string | null>(null)
  const activeSourceText = ref('')
  const termMatchesMap = ref<Record<string, TermMatch[]>>({})
  const termbaseCollectionIds = ref<string[]>([])
  const loading = ref(false)
  const saving = ref(false)
  const llmRunning = ref(false)
  const syncMessage = ref('暂无未保存修改')
  const llmMessage = ref('可按范围对 exact / fuzzy / none 句段执行 AI 修正。')
  const lastSyncedAt = ref<string | null>(null)
  const dirtyEntries = ref<Record<string, SegmentUpdatePayload>>({})
  const previewUpdateToken = ref(0)
  const lastPreviewUpdatedSentenceId = ref<string | null>(null)
  const lastPreviewUpdatedText = ref('')

  // 修订跟踪相关状态
  const revisionEnabled = ref(false)
  const revisionLoading = ref(false)
  const revisions = ref<Map<string, SegmentRevision>>(new Map())
  const revisionSelectedAuthorId = ref<string | null>(null)
  const revisionNavigationIndex = ref(0)

  let syncTimer: number | null = null

  const dirtyCount = computed(() => Object.keys(dirtyEntries.value).length)

  // 修订跟踪相关计算属性
  const revisionAllMarks = computed(() => {
    const marks: { sentenceId: string; markIndex: number; mark: RevisionMark }[] = []
    for (const [sentenceId, segmentRevision] of revisions.value) {
      const filteredMarks = revisionSelectedAuthorId.value
        ? segmentRevision.marks.filter(m => m.author_id === revisionSelectedAuthorId.value)
        : segmentRevision.marks
      filteredMarks.forEach((mark, index) => {
        marks.push({ sentenceId, markIndex: index, mark })
      })
    }
    return marks
  })

  const revisionTotalCount = computed(() => revisionAllMarks.value.length)

  const revisionAuthorSummary = computed<RevisionAuthorSummary[]>(() => {
    const authorMap = new Map<string, { id: string; username: string; count: number }>()
    for (const [, segmentRevision] of revisions.value) {
      for (const mark of segmentRevision.marks) {
        if (mark.author_id && mark.author_username) {
          const existing = authorMap.get(mark.author_id)
          if (existing) {
            existing.count++
          } else {
            authorMap.set(mark.author_id, {
              id: mark.author_id,
              username: mark.author_username,
              count: 1,
            })
          }
        }
      }
    }
    return Array.from(authorMap.values()).sort((a, b) => b.count - a.count)
  })

  function resetState() {
    if (syncTimer !== null) {
      window.clearTimeout(syncTimer)
      syncTimer = null
    }
    fileRecord.value = null
    segments.value = []
    previewHtml.value = ''
    previewSupported.value = false
    activeSentenceId.value = null
    activeSourceText.value = ''
    termMatchesMap.value = {}
    termbaseCollectionIds.value = []
    saving.value = false
    llmRunning.value = false
    syncMessage.value = '暂无未保存修改'
    llmMessage.value = '可按范围对 exact / fuzzy / none 句段执行 AI 修正。'
    lastSyncedAt.value = null
    dirtyEntries.value = {}
    previewUpdateToken.value = 0
    lastPreviewUpdatedSentenceId.value = null
    lastPreviewUpdatedText.value = ''
    // 重置修订状态
    revisionEnabled.value = false
    revisionLoading.value = false
    revisions.value = new Map()
    revisionSelectedAuthorId.value = null
    revisionNavigationIndex.value = 0
  }

  async function loadTask(fileRecordId: string) {
    resetState()
    loading.value = true
    // 加载术语库选择
    termbaseCollectionIds.value = taskStore.getTermbaseCollections(fileRecordId)
    try {
      const [detail, preview] = await Promise.all([
        fetchAllSegments(fileRecordId),
        fetchPreview(fileRecordId),
      ])
      fileRecord.value = detail
      segments.value = detail.segments
      previewHtml.value = preview.preview_html
      previewSupported.value = preview.supports_preview
      // 加载修订数据
      await loadRevisions(fileRecordId)
    } finally {
      loading.value = false
    }
  }

  async function fetchAllSegments(fileRecordId: string) {
    let skip = 0
    let total = 0
    let baseDetail: FileRecordDetail | null = null
    const allSegments: Segment[] = []

    do {
      const { data } = await http.get<FileRecordDetail>(`/file-records/${fileRecordId}`, {
        params: {
          skip,
          limit: SEGMENT_PAGE_SIZE,
        },
      })

      if (!baseDetail) {
        baseDetail = {
          ...data,
          segments: [],
        }
      }

      total = data.total_segments
      allSegments.push(...data.segments)
      skip += data.segments.length
    } while (skip < total)

    if (!baseDetail) {
      throw new Error('任务详情加载失败。')
    }

    return {
      ...baseDetail,
      segments: allSegments,
      skip: 0,
      limit: allSegments.length,
    }
  }

  async function fetchPreview(fileRecordId: string) {
    const { data } = await http.get<FileRecordPreview>(`/file-records/${fileRecordId}/preview`)
    return data
  }

  function markPreviewUpdate(sentenceId: string, targetText: string) {
    lastPreviewUpdatedSentenceId.value = sentenceId
    lastPreviewUpdatedText.value = targetText
    previewUpdateToken.value += 1
  }

  function updateTarget(sentenceId: string, targetText: string) {
    const index = segments.value.findIndex((segment) => segment.sentence_id === sentenceId)
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
    syncMessage.value = `待同步 ${dirtyCount.value} 条修改`
    scheduleSync()
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
      const params: Record<string, unknown> = { text: sourceText }
      if (termbaseCollectionIds.value.length > 0) {
        params.collection_ids = termbaseCollectionIds.value
      }
      const { data } = await http.get<{ matches: TermMatch[] }>('/termbase/match', { params })
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

  function setTermbaseCollections(collectionIds: string[]) {
    termbaseCollectionIds.value = collectionIds
    // 同步保存到 taskStore
    if (fileRecord.value) {
      taskStore.setTermbaseCollections(fileRecord.value.id, collectionIds)
    }
    // 清空已缓存的术语匹配，下次选中句段时会重新加载
    termMatchesMap.value = {}
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
    syncMessage.value = `正在同步 ${updates.length} 条修改...`
    try {
      await http.put(`/file-records/${fileRecord.value.id}/segments`, {
        updates,
      })
      dirtyEntries.value = {}
      lastSyncedAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
      syncMessage.value = `已同步，最近一次保存于 ${lastSyncedAt.value}`
    } finally {
      saving.value = false
    }
  }

  function applyLLMUpdate(sentenceId: string, targetText: string, source = 'llm', status = 'confirmed') {
    const index = segments.value.findIndex((segment) => segment.sentence_id === sentenceId)
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
    llmMessage.value = '正在请求 AI 修正译文...'

    try {
      const token = window.localStorage.getItem('token')
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
      })

      if (!response.ok) {
        let message = 'AI 修正请求失败。'
        try {
          const payload = await response.json()
          message = String(payload.detail || message)
        } catch {
          // ignore parsing error
        }
        throw new Error(message)
      }

      if (!response.body) {
        throw new Error('AI 修正未返回流式结果。')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
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
          handleLLMEvent(event.event, event.data)
        }
      }

      if (buffer.trim()) {
        const event = parseSSEChunk(buffer)
        if (event) {
          handleLLMEvent(event.event, event.data)
        }
      }
    } finally {
      llmRunning.value = false
    }
  }

  function handleLLMEvent(event: string, data: Record<string, unknown>) {
    if (event === 'start') {
      const total = Number(data.total || 0)
      llmMessage.value = `AI 修正已开始，本次计划处理 ${total} 条句段。`
      return
    }

    if (event === 'segment') {
      applyLLMUpdate(
        String(data.sentence_id || ''),
        String(data.target_text || ''),
        String(data.source || 'llm'),
        'confirmed',
      )
      llmMessage.value = `AI 已写回句段 ${String(data.sentence_id || '')}`
      return
    }

    if (event === 'error') {
      llmMessage.value = `AI 修正出现错误：${String(data.message || '未知错误')}`
      return
    }

    if (event === 'complete') {
      const updatedCount = Number(data.updated_count || 0)
      const errorCount = Number(data.error_count || 0)
      llmMessage.value = `AI 修正完成，成功 ${updatedCount} 条，失败 ${errorCount} 条。`
    }
  }

  async function downloadTranslatedDocx() {
    if (!fileRecord.value) {
      return
    }

    if (dirtyCount.value > 0) {
      await syncToBackend()
    }

    const response = await http.get(`/file-records/${fileRecord.value.id}/export-docx`, {
      responseType: 'blob',
    })
    const blobUrl = window.URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = `${fileRecord.value.filename.replace(/\.docx$/i, '') || 'translated'}-translated.docx`
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(blobUrl)
  }

  // ========== 修订跟踪相关函数 ==========

  async function loadRevisions(fileRecordId: string) {
    revisionLoading.value = true
    try {
      const { data } = await http.get<Record<string, SegmentRevision>>(
        `/file-records/${fileRecordId}/revisions`
      )
      revisions.value = new Map(Object.entries(data))
    } catch (e) {
      console.error('Failed to load revisions:', e)
      revisions.value = new Map()
    } finally {
      revisionLoading.value = false
    }
  }

  function getRevisionMarks(sentenceId: string): RevisionMark[] {
    const segmentRevision = revisions.value.get(sentenceId)
    if (!segmentRevision) return []
    if (!revisionSelectedAuthorId.value) return segmentRevision.marks
    return segmentRevision.marks.filter(m => m.author_id === revisionSelectedAuthorId.value)
  }

  async function acceptRevision(sentenceId: string, markId: string) {
    if (!fileRecord.value) return
    try {
      await http.post(`/file-records/${fileRecord.value.id}/revisions/${sentenceId}/accept`, {
        mark_id: markId,
      })
      // 本地移除 mark，不重新加载
      const revision = revisions.value.get(sentenceId)
      if (revision) {
        revision.marks = revision.marks.filter(m => m.id !== markId)
        if (revision.marks.length === 0) {
          revisions.value.delete(sentenceId)
        }
      }
    } catch (e) {
      console.error('Failed to accept revision:', e)
      throw e
    }
  }

  async function rejectRevision(sentenceId: string, markId: string) {
    if (!fileRecord.value) return
    try {
      await http.post(`/file-records/${fileRecord.value.id}/revisions/${sentenceId}/reject`, {
        mark_id: markId,
      })
      // 本地更新：回退文本 + 移除修订
      const revision = revisions.value.get(sentenceId)
      if (revision) {
        const segment = segments.value.find(s => s.sentence_id === sentenceId)
        if (segment) {
          segment.target_text = revision.previous_text
          markPreviewUpdate(sentenceId, revision.previous_text)
        }
        revisions.value.delete(sentenceId)
      }
    } catch (e) {
      console.error('Failed to reject revision:', e)
      throw e
    }
  }

  async function acceptAllRevisions() {
    if (!fileRecord.value) return
    try {
      await http.post(`/file-records/${fileRecord.value.id}/revisions/accept-all`, {
        author_id: revisionSelectedAuthorId.value,
      })
      // 本地移除所有匹配的修订
      if (revisionSelectedAuthorId.value) {
        for (const [sentenceId, revision] of revisions.value) {
          revision.marks = revision.marks.filter(m => m.author_id !== revisionSelectedAuthorId.value)
          if (revision.marks.length === 0) {
            revisions.value.delete(sentenceId)
          }
        }
      } else {
        revisions.value.clear()
      }
    } catch (e) {
      console.error('Failed to accept all revisions:', e)
      throw e
    }
  }

  async function rejectAllRevisions() {
    if (!fileRecord.value) return
    try {
      await http.post(`/file-records/${fileRecord.value.id}/revisions/reject-all`, {
        author_id: revisionSelectedAuthorId.value,
      })
      // 本地更新：回退所有匹配句段的文本
      for (const [sentenceId, revision] of revisions.value) {
        const hasMatchingMark = revisionSelectedAuthorId.value
          ? revision.marks.some(m => m.author_id === revisionSelectedAuthorId.value)
          : true
        if (hasMatchingMark) {
          const segment = segments.value.find(s => s.sentence_id === sentenceId)
          if (segment) {
            segment.target_text = revision.previous_text
            markPreviewUpdate(sentenceId, revision.previous_text)
          }
          revisions.value.delete(sentenceId)
        }
      }
    } catch (e) {
      console.error('Failed to reject all revisions:', e)
      throw e
    }
  }

  function revisionNavigatePrev(): { sentenceId: string; markIndex: number } | null {
    if (revisionAllMarks.value.length === 0) return null
    revisionNavigationIndex.value = 
      (revisionNavigationIndex.value - 1 + revisionAllMarks.value.length) % revisionAllMarks.value.length
    const item = revisionAllMarks.value[revisionNavigationIndex.value]
    return { sentenceId: item.sentenceId, markIndex: item.markIndex }
  }

  function revisionNavigateNext(): { sentenceId: string; markIndex: number } | null {
    if (revisionAllMarks.value.length === 0) return null
    revisionNavigationIndex.value = 
      (revisionNavigationIndex.value + 1) % revisionAllMarks.value.length
    const item = revisionAllMarks.value[revisionNavigationIndex.value]
    return { sentenceId: item.sentenceId, markIndex: item.markIndex }
  }

  function setRevisionEnabled(value: boolean) {
    revisionEnabled.value = value
  }

  function setRevisionSelectedAuthorId(value: string | null) {
    revisionSelectedAuthorId.value = value
    revisionNavigationIndex.value = 0
  }

  return {
    fileRecord,
    segments,
    previewHtml,
    previewSupported,
    activeSentenceId,
    activeSourceText,
    termMatchesMap,
    termbaseCollectionIds,
    loading,
    saving,
    llmRunning,
    syncMessage,
    llmMessage,
    dirtyCount,
    previewUpdateToken,
    lastPreviewUpdatedSentenceId,
    lastPreviewUpdatedText,
    // 修订跟踪
    revisionEnabled,
    revisionLoading,
    revisionTotalCount,
    revisionAuthorSummary,
    revisionSelectedAuthorId,
    revisionNavigationIndex,
    loadTask,
    updateTarget,
    setActiveSentence,
    getTermMatches,
    setTermbaseCollections,
    syncToBackend,
    startLLMTranslation,
    downloadTranslatedDocx,
    resetState,
    // 修订跟踪函数
    getRevisionMarks,
    acceptRevision,
    rejectRevision,
    acceptAllRevisions,
    rejectAllRevisions,
    revisionNavigatePrev,
    revisionNavigateNext,
    setRevisionEnabled,
    setRevisionSelectedAuthorId,
  }
})
