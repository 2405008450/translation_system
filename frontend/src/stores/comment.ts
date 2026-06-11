import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { http } from '../api/http'
import { translate } from '../i18n'
import type {
  CommentAnchorDraft,
  CommentCreatePayload,
  CommentReplyPayload,
  CommentUpdatePayload,
  SegmentComment,
} from '../types/api'

const DEFAULT_MESSAGE = translate('stores.comment.defaultMessage')
const COMMENT_POLL_INTERVAL_MS = 8000

export interface CommentWindowQuery {
  page: number
  pageSize: number
  scope?: string
  sourceQuery?: string
  targetQuery?: string
  searchFuzzy?: boolean
  statusFilters?: string[]
  matchFilters?: string[]
  sourceFilters?: string[]
  workflowStepIds?: string[]
}

function sortComments(comments: SegmentComment[]) {
  return [...comments].sort((left, right) => {
    const timeDiff = new Date(left.created_at).getTime() - new Date(right.created_at).getTime()
    if (timeDiff !== 0) {
      return timeDiff
    }
    return left.id.localeCompare(right.id)
  })
}

function buildCommentsSignature(comments: SegmentComment[]) {
  return comments
    .map((comment) => `${comment.id}:${comment.updated_at}:${comment.status}:${comment.parent_id || ''}`)
    .join('|')
}

function buildCommentWindowParams(query?: CommentWindowQuery | null) {
  if (!query) {
    return undefined
  }
  const page = Math.max(1, query.page)
  const pageSize = Math.max(1, query.pageSize)
  const serializeArray = (value?: string[]) => (value && value.length > 0 ? value.join(',') : undefined)
  return {
    skip: (page - 1) * pageSize,
    limit: pageSize,
    scope: query.scope ?? 'all',
    source_query: query.sourceQuery ?? '',
    target_query: query.targetQuery ?? '',
    search_fuzzy: query.searchFuzzy ?? false,
    status_filters: serializeArray(query.statusFilters),
    match_filters: serializeArray(query.matchFilters),
    source_filters: serializeArray(query.sourceFilters),
    workflow_step_ids: serializeArray(query.workflowStepIds),
  }
}

export const useCommentStore = defineStore('comment', () => {
  const comments = ref<SegmentComment[]>([])
  const loading = ref(false)
  const saving = ref(false)
  const message = ref(DEFAULT_MESSAGE)
  const activeCommentId = ref<string | null>(null)
  const draftAnchor = ref<CommentAnchorDraft | null>(null)
  const polling = ref(false)

  let pollTimer: number | null = null
  let lastSignature = ''

  const totalCount = computed(() => comments.value.filter((comment) => !comment.parent_id).length)
  const openCount = computed(() =>
    comments.value.filter((comment) => !comment.parent_id && comment.status === 'open').length,
  )

  function stopPolling() {
    polling.value = false
    if (pollTimer !== null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function resetState() {
    stopPolling()
    comments.value = []
    loading.value = false
    saving.value = false
    message.value = DEFAULT_MESSAGE
    activeCommentId.value = null
    draftAnchor.value = null
    lastSignature = ''
  }

  function syncSignature() {
    lastSignature = buildCommentsSignature(comments.value)
  }

  async function refreshComments(fileRecordId: string, silent = false, query?: CommentWindowQuery | null) {
    if (saving.value && silent) {
      return
    }

    try {
      const { data } = await http.get<SegmentComment[]>(`/file-records/${fileRecordId}/comments`, {
        params: buildCommentWindowParams(query),
      })
      const sortedComments = sortComments(data)
      const nextSignature = buildCommentsSignature(sortedComments)
      const hasChanged = nextSignature !== lastSignature

      comments.value = sortedComments
      lastSignature = nextSignature

      if (activeCommentId.value && !sortedComments.some((comment) => comment.id === activeCommentId.value)) {
        activeCommentId.value = null
      }

      if (!silent || hasChanged) {
        if (data.length > 0) {
          message.value = silent
            ? translate('stores.comment.autoRefreshed', { count: totalCount.value })
            : translate('stores.comment.loaded', { count: totalCount.value })
        } else {
          message.value = DEFAULT_MESSAGE
        }
      }
    } catch (error) {
      if (!silent) {
        throw error
      }
      message.value = error instanceof Error ? error.message : translate('stores.comment.refreshFailed')
    }
  }

  async function loadComments(fileRecordId: string, query?: CommentWindowQuery | null) {
    resetState()
    loading.value = true
    try {
      await refreshComments(fileRecordId, false, query)
    } finally {
      loading.value = false
    }
  }

  function startPolling(fileRecordId: string, getWindowQuery: () => CommentWindowQuery | null = () => null) {
    stopPolling()
    polling.value = true
    pollTimer = window.setInterval(() => {
      const query = getWindowQuery()
      if (!query) {
        return
      }
      void refreshComments(fileRecordId, true, query)
    }, COMMENT_POLL_INTERVAL_MS)
  }

  function setActiveComment(commentId: string | null) {
    activeCommentId.value = commentId
  }

  function setDraftAnchor(anchor: CommentAnchorDraft | null) {
    draftAnchor.value = anchor
    if (anchor) {
      message.value = translate('stores.comment.draftReady')
    } else if (!comments.value.length) {
      message.value = DEFAULT_MESSAGE
    }
  }

  function upsertComment(comment: SegmentComment) {
    const nextComments = comments.value.filter((item) => item.id !== comment.id)
    nextComments.push(comment)
    comments.value = sortComments(nextComments)
    syncSignature()
  }

  function collectBranchIds(rootId: string) {
    const ids = new Set<string>()

    const visit = (commentId: string) => {
      ids.add(commentId)
      for (const comment of comments.value) {
        if (comment.parent_id === commentId) {
          visit(comment.id)
        }
      }
    }

    visit(rootId)
    return ids
  }

  async function createComment(fileRecordId: string, payload: CommentCreatePayload) {
    saving.value = true
    try {
      const { data } = await http.post<SegmentComment>(`/file-records/${fileRecordId}/comments`, payload)
      upsertComment(data)
      activeCommentId.value = data.id
      draftAnchor.value = null
      message.value = translate('stores.comment.saved')
      return data
    } finally {
      saving.value = false
    }
  }

  async function replyToComment(commentId: string, payload: CommentReplyPayload) {
    saving.value = true
    try {
      const { data } = await http.post<SegmentComment>(`/comments/${commentId}/replies`, payload)
      upsertComment(data)
      activeCommentId.value = data.id
      message.value = translate('stores.comment.replySaved')
      return data
    } finally {
      saving.value = false
    }
  }

  async function updateComment(commentId: string, payload: CommentUpdatePayload) {
    saving.value = true
    try {
      const { data } = await http.patch<SegmentComment>(`/comments/${commentId}`, payload)
      upsertComment(data)
      activeCommentId.value = data.id
      message.value = data.status === 'resolved'
        ? translate('stores.comment.resolved')
        : translate('stores.comment.updated')
      return data
    } finally {
      saving.value = false
    }
  }

  async function deleteComment(commentId: string) {
    saving.value = true
    try {
      await http.delete(`/comments/${commentId}`)
      const removedIds = collectBranchIds(commentId)
      comments.value = comments.value.filter((comment) => !removedIds.has(comment.id))
      if (activeCommentId.value && removedIds.has(activeCommentId.value)) {
        activeCommentId.value = null
      }
      syncSignature()
      message.value = translate('stores.comment.deleted')
    } finally {
      saving.value = false
    }
  }

  return {
    comments,
    loading,
    saving,
    message,
    activeCommentId,
    draftAnchor,
    polling,
    totalCount,
    openCount,
    resetState,
    loadComments,
    refreshComments,
    startPolling,
    stopPolling,
    setActiveComment,
    setDraftAnchor,
    createComment,
    replyToComment,
    updateComment,
    deleteComment,
  }
})
