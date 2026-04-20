import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { http } from '../api/http'
import type {
  CommentAnchorDraft,
  CommentCreatePayload,
  CommentReplyPayload,
  CommentUpdatePayload,
  SegmentComment,
} from '../types/api'

const DEFAULT_MESSAGE = '在预览中选中文字后即可添加批注。'
const COMMENT_POLL_INTERVAL_MS = 8000

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

  async function refreshComments(fileRecordId: string, silent = false) {
    if (saving.value && silent) {
      return
    }

    try {
      const { data } = await http.get<SegmentComment[]>(`/file-records/${fileRecordId}/comments`)
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
            ? `批注已自动刷新，共 ${totalCount.value} 条。`
            : `已加载 ${totalCount.value} 条批注。`
        } else {
          message.value = DEFAULT_MESSAGE
        }
      }
    } catch (error) {
      if (!silent) {
        throw error
      }
      message.value = error instanceof Error ? error.message : '批注自动刷新失败。'
    }
  }

  async function loadComments(fileRecordId: string) {
    resetState()
    loading.value = true
    try {
      await refreshComments(fileRecordId, false)
    } finally {
      loading.value = false
    }
  }

  function startPolling(fileRecordId: string) {
    stopPolling()
    polling.value = true
    pollTimer = window.setInterval(() => {
      void refreshComments(fileRecordId, true)
    }, COMMENT_POLL_INTERVAL_MS)
  }

  function setActiveComment(commentId: string | null) {
    activeCommentId.value = commentId
  }

  function setDraftAnchor(anchor: CommentAnchorDraft | null) {
    draftAnchor.value = anchor
    if (anchor) {
      message.value = '已定位到选区，输入内容后即可保存。'
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
      message.value = '批注已保存。'
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
      message.value = '回复已保存。'
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
      message.value = data.status === 'resolved' ? '批注已标记为已解决。' : '批注已更新。'
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
      message.value = '批注已删除。'
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
