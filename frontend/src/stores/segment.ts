import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { http } from '../api/http'
import type {
  FileRecordDetail,
  FileRecordPreview,
  LLMProvider,
  LLMTranslateScope,
  Segment,
  SegmentUpdatePayload,
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
  const fileRecord = ref<FileRecordDetail | null>(null)
  const segments = ref<Segment[]>([])
  const previewHtml = ref('')
  const previewSupported = ref(false)
  const activeSentenceId = ref<string | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const llmRunning = ref(false)
  const syncMessage = ref('暂无未保存修改')
  const llmMessage = ref('可按范围对 exact / fuzzy / none 句段执行 AI 修正。')
  const lastSyncedAt = ref<string | null>(null)
  const dirtyEntries = ref<Record<string, SegmentUpdatePayload>>({})

  let syncTimer: number | null = null

  const dirtyCount = computed(() => Object.keys(dirtyEntries.value).length)

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
    saving.value = false
    llmRunning.value = false
    syncMessage.value = '暂无未保存修改'
    llmMessage.value = '可按范围对 exact / fuzzy / none 句段执行 AI 修正。'
    lastSyncedAt.value = null
    dirtyEntries.value = {}
  }

  async function loadTask(fileRecordId: string) {
    resetState()
    loading.value = true
    try {
      const [detail, preview] = await Promise.all([
        fetchAllSegments(fileRecordId),
        fetchPreview(fileRecordId),
      ])
      fileRecord.value = detail
      segments.value = detail.segments
      previewHtml.value = preview.preview_html
      previewSupported.value = preview.supports_preview
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

  return {
    fileRecord,
    segments,
    previewHtml,
    previewSupported,
    activeSentenceId,
    loading,
    saving,
    llmRunning,
    syncMessage,
    llmMessage,
    dirtyCount,
    loadTask,
    updateTarget,
    setActiveSentence,
    syncToBackend,
    startLLMTranslation,
    downloadTranslatedDocx,
    resetState,
  }
})
