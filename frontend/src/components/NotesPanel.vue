<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import type {
  CommentAnchorDraft,
  CommentCreatePayload,
  CommentStatus,
  SegmentComment,
} from '../types/api'

interface CommentThread {
  root: SegmentComment
  replies: SegmentComment[]
}

const props = withDefaults(defineProps<{
  comments: SegmentComment[]
  loading: boolean
  saving: boolean
  polling?: boolean
  activeCommentId: string | null
  draftAnchor: CommentAnchorDraft | null
  currentUserId?: string | null
  message?: string
}>(), {
  polling: false,
  currentUserId: null,
  message: '',
})

const emit = defineEmits<{
  close: []
  selectComment: [commentId: string]
  createComment: [payload: CommentCreatePayload]
  updateComment: [commentId: string, payload: { body?: string; status?: CommentStatus }]
  deleteComment: [commentId: string]
  replyComment: [commentId: string, body: string]
  cancelDraft: []
}>()

const draftBody = ref('')
const draftTextareaRef = ref<HTMLTextAreaElement | null>(null)
const editingCommentId = ref<string | null>(null)
const editingBody = ref('')
const replyingToCommentId = ref<string | null>(null)
const replyBody = ref('')
const { t } = useI18n()

const threads = computed<CommentThread[]>(() => {
  const repliesByParentId = new Map<string, SegmentComment[]>()

  for (const comment of props.comments) {
    if (!comment.parent_id) {
      continue
    }
    const bucket = repliesByParentId.get(comment.parent_id) || []
    bucket.push(comment)
    repliesByParentId.set(comment.parent_id, bucket)
  }

  return props.comments
    .filter((comment) => !comment.parent_id)
    .map((root) => ({
      root,
      replies: (repliesByParentId.get(root.id) || []).sort((left, right) =>
        new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
      ),
    }))
})

const pendingThreadsCount = computed(() => (
  threads.value.filter((thread) => thread.root.status === 'open').length
))

const draftSummary = computed(() => {
  if (!props.draftAnchor) {
    return ''
  }
  if (props.draftAnchor.anchor_mode === 'range' && props.draftAnchor.anchor_text) {
    return t('notes.selectedContent', { text: props.draftAnchor.anchor_text })
  }
  return t('notes.sentence', { id: props.draftAnchor.sentence_id })
})

function isOwnComment(comment: SegmentComment) {
  return Boolean(props.currentUserId && comment.author.id === props.currentUserId)
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function getAuthorDisplayName(comment: SegmentComment) {
  return comment.author.nickname || comment.author.username
}

function buildAnchorLabel(comment: SegmentComment) {
  if (comment.anchor_mode === 'range' && comment.anchor_text) {
    return `${comment.sentence_id || t('notes.unresolvedSentence')} · ${comment.anchor_text}`
  }
  return comment.sentence_id || t('notes.unresolvedSegment')
}

function submitDraft() {
  if (!props.draftAnchor || !draftBody.value.trim()) {
    return
  }

  emit('createComment', {
    ...props.draftAnchor,
    body: draftBody.value,
  })
  draftBody.value = ''
}

function startReply(commentId: string) {
  replyingToCommentId.value = replyingToCommentId.value === commentId ? null : commentId
  replyBody.value = ''
  editingCommentId.value = null
}

function submitReply(commentId: string) {
  if (!replyBody.value.trim()) {
    return
  }
  emit('replyComment', commentId, replyBody.value)
  replyingToCommentId.value = null
  replyBody.value = ''
}

function startEdit(comment: SegmentComment) {
  editingCommentId.value = comment.id
  editingBody.value = comment.body
  replyingToCommentId.value = null
}

function saveEdit(commentId: string) {
  if (!editingBody.value.trim()) {
    return
  }
  emit('updateComment', commentId, { body: editingBody.value })
  editingCommentId.value = null
  editingBody.value = ''
}

function toggleStatus(comment: SegmentComment) {
  emit('updateComment', comment.id, {
    status: comment.status === 'open' ? 'resolved' : 'open',
  })
}

watch(() => props.draftAnchor, async (draftAnchor) => {
  if (draftAnchor) {
    draftBody.value = ''
    await nextTick()
    draftTextareaRef.value?.focus()
  }
}, { immediate: true })
</script>

<template>
  <section class="panel notes-panel">
    <div class="notes-panel__header">
      <div>
        <div class="section-title section-title--tight">{{ t('notes.title') }}</div>
        <p class="panel-subtitle">{{ message || t('stores.comment.defaultMessage') }}</p>
      </div>
      <button class="button preview-panel__close" type="button" @click="emit('close')">{{ t('notes.close') }}</button>
    </div>

    <div class="notes-panel__summary">
      <span>{{ t('notes.summary', { count: threads.length }) }}</span>
      <span>{{ t('notes.pending', { count: pendingThreadsCount }) }}</span>
      <span v-if="polling">{{ t('notes.polling') }}</span>
    </div>

    <section v-if="draftAnchor" class="notes-panel__composer">
      <div class="notes-panel__composer-head">
        <strong>{{ t('notes.newComment') }}</strong>
        <button class="button" type="button" @click="emit('cancelDraft')">{{ t('common.actions.cancel') }}</button>
      </div>
      <div class="notes-panel__anchor">{{ draftSummary }}</div>
      <textarea
        ref="draftTextareaRef"
        v-model="draftBody"
        class="notes-panel__textarea"
        rows="4"
        :placeholder="t('notes.placeholder')"
      />
      <div class="notes-panel__actions">
        <button class="button button--primary" type="button" :disabled="saving || !draftBody.trim()" @click="submitDraft">
          {{ saving ? t('common.actions.saving') : t('notes.saveComment') }}
        </button>
      </div>
    </section>

    <div v-if="loading" class="empty-state">{{ t('notes.loading') }}</div>
    <div v-else-if="threads.length === 0" class="empty-state">
      {{ t('notes.empty') }}
    </div>
    <div v-else class="notes-panel__threads">
      <article
        v-for="thread in threads"
        :key="thread.root.id"
        class="notes-thread"
        :class="{ 'has-active-comment': [thread.root.id, ...thread.replies.map((reply) => reply.id)].includes(activeCommentId || '') }"
      >
        <section
          class="notes-comment"
          :class="{
            'is-active': activeCommentId === thread.root.id,
            'is-resolved': thread.root.status === 'resolved',
          }"
          @click="emit('selectComment', thread.root.id)"
        >
          <div class="notes-comment__meta">
            <strong>{{ getAuthorDisplayName(thread.root) }}</strong>
            <span>{{ formatDateTime(thread.root.created_at) }}</span>
            <span class="notes-comment__status">{{ thread.root.status === 'open' ? t('notes.processing') : t('notes.resolved') }}</span>
          </div>
          <div class="notes-comment__anchor">{{ buildAnchorLabel(thread.root) }}</div>
          <p v-if="editingCommentId !== thread.root.id" class="notes-comment__body">{{ thread.root.body }}</p>
          <div v-else class="notes-comment__editor">
            <textarea v-model="editingBody" class="notes-panel__textarea" rows="4" />
            <div class="notes-panel__actions">
              <button class="button button--primary" type="button" :disabled="saving || !editingBody.trim()" @click.stop="saveEdit(thread.root.id)">
                {{ saving ? t('common.actions.saving') : t('notes.saveEdit') }}
              </button>
              <button class="button" type="button" @click.stop="editingCommentId = null">{{ t('common.actions.cancel') }}</button>
            </div>
          </div>
          <div class="notes-comment__actions">
            <button class="button" type="button" @click.stop="startReply(thread.root.id)">{{ t('common.actions.reply') }}</button>
            <button class="button" type="button" @click.stop="toggleStatus(thread.root)">
              {{ thread.root.status === 'open' ? t('notes.markResolved') : t('notes.reopen') }}
            </button>
            <button
              v-if="isOwnComment(thread.root)"
              class="button"
              type="button"
              @click.stop="startEdit(thread.root)"
            >
              {{ t('common.actions.edit') }}
            </button>
            <button
              v-if="isOwnComment(thread.root)"
              class="button"
              type="button"
              @click.stop="emit('deleteComment', thread.root.id)"
            >
              {{ t('common.actions.delete') }}
            </button>
          </div>
          <div v-if="replyingToCommentId === thread.root.id" class="notes-comment__reply-box">
            <textarea
              v-model="replyBody"
              class="notes-panel__textarea"
              rows="3"
              :placeholder="t('notes.replyPlaceholder')"
            />
            <div class="notes-panel__actions">
              <button class="button button--primary" type="button" :disabled="saving || !replyBody.trim()" @click.stop="submitReply(thread.root.id)">
                {{ saving ? t('notes.sendingReply') : t('notes.sendReply') }}
              </button>
              <button class="button" type="button" @click.stop="replyingToCommentId = null">{{ t('common.actions.cancel') }}</button>
            </div>
          </div>
        </section>

        <section
          v-for="reply in thread.replies"
          :key="reply.id"
          class="notes-comment notes-comment--reply"
          :class="{ 'is-active': activeCommentId === reply.id }"
          @click="emit('selectComment', reply.id)"
        >
          <div class="notes-comment__meta">
            <strong>{{ getAuthorDisplayName(reply) }}</strong>
            <span>{{ formatDateTime(reply.created_at) }}</span>
          </div>
          <p v-if="editingCommentId !== reply.id" class="notes-comment__body">{{ reply.body }}</p>
          <div v-else class="notes-comment__editor">
            <textarea v-model="editingBody" class="notes-panel__textarea" rows="3" />
            <div class="notes-panel__actions">
              <button class="button button--primary" type="button" :disabled="saving || !editingBody.trim()" @click.stop="saveEdit(reply.id)">
                {{ saving ? t('common.actions.saving') : t('notes.saveEdit') }}
              </button>
              <button class="button" type="button" @click.stop="editingCommentId = null">{{ t('common.actions.cancel') }}</button>
            </div>
          </div>
          <div v-if="editingCommentId !== reply.id" class="notes-comment__actions">
            <button
              v-if="isOwnComment(reply)"
              class="button"
              type="button"
              @click.stop="startEdit(reply)"
            >
              {{ t('common.actions.edit') }}
            </button>
            <button
              v-if="isOwnComment(reply)"
              class="button"
              type="button"
              @click.stop="emit('deleteComment', reply.id)"
            >
              {{ t('common.actions.delete') }}
            </button>
          </div>
        </section>
      </article>
    </div>
  </section>
</template>
