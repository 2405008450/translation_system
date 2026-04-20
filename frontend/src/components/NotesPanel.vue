<script setup lang="ts">
import { computed, ref, watch } from 'vue'

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
const editingCommentId = ref<string | null>(null)
const editingBody = ref('')
const replyingToCommentId = ref<string | null>(null)
const replyBody = ref('')

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

const draftSummary = computed(() => {
  if (!props.draftAnchor) {
    return ''
  }
  if (props.draftAnchor.anchor_mode === 'range' && props.draftAnchor.anchor_text) {
    return `选中内容：${props.draftAnchor.anchor_text}`
  }
  return `句段：${props.draftAnchor.sentence_id}`
})

function isOwnComment(comment: SegmentComment) {
  return Boolean(props.currentUserId && comment.author.id === props.currentUserId)
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function buildAnchorLabel(comment: SegmentComment) {
  if (comment.anchor_mode === 'range' && comment.anchor_text) {
    return `${comment.sentence_id || '未定位'} · ${comment.anchor_text}`
  }
  return comment.sentence_id || '未定位句段'
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

watch(() => props.draftAnchor, (draftAnchor) => {
  if (draftAnchor) {
    draftBody.value = ''
  }
})
</script>

<template>
  <section class="panel notes-panel">
    <div class="notes-panel__header">
      <div>
        <div class="section-title section-title--tight">批注</div>
        <p class="panel-subtitle">{{ message || '在预览中选中文字后即可添加批注。' }}</p>
      </div>
      <button class="button preview-panel__close" type="button" @click="emit('close')">关闭</button>
    </div>

    <div class="notes-panel__summary">
      <span>共 {{ threads.length }} 条主批注</span>
      <span>待处理 {{ threads.filter((thread) => thread.root.status === 'open').length }} 条</span>
      <span v-if="polling">自动刷新中</span>
    </div>

    <section v-if="draftAnchor" class="notes-panel__composer">
      <div class="notes-panel__composer-head">
        <strong>新建批注</strong>
        <button class="button" type="button" @click="emit('cancelDraft')">取消</button>
      </div>
      <div class="notes-panel__anchor">{{ draftSummary }}</div>
      <textarea
        v-model="draftBody"
        class="notes-panel__textarea"
        rows="4"
        placeholder="输入批注内容"
      />
      <div class="notes-panel__actions">
        <button class="button button--primary" type="button" :disabled="saving || !draftBody.trim()" @click="submitDraft">
          {{ saving ? '保存中...' : '保存批注' }}
        </button>
      </div>
    </section>

    <div v-if="loading" class="empty-state">批注加载中...</div>
    <div v-else-if="threads.length === 0" class="empty-state">
      暂时还没有批注，在预览里选中文字后即可创建。
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
            <strong>{{ thread.root.author.username }}</strong>
            <span>{{ formatDateTime(thread.root.created_at) }}</span>
            <span class="notes-comment__status">{{ thread.root.status === 'open' ? '处理中' : '已解决' }}</span>
          </div>
          <div class="notes-comment__anchor">{{ buildAnchorLabel(thread.root) }}</div>
          <p v-if="editingCommentId !== thread.root.id" class="notes-comment__body">{{ thread.root.body }}</p>
          <div v-else class="notes-comment__editor">
            <textarea v-model="editingBody" class="notes-panel__textarea" rows="4" />
            <div class="notes-panel__actions">
              <button class="button button--primary" type="button" :disabled="saving || !editingBody.trim()" @click.stop="saveEdit(thread.root.id)">
                {{ saving ? '保存中...' : '保存修改' }}
              </button>
              <button class="button" type="button" @click.stop="editingCommentId = null">取消</button>
            </div>
          </div>
          <div class="notes-comment__actions">
            <button class="button" type="button" @click.stop="startReply(thread.root.id)">回复</button>
            <button class="button" type="button" @click.stop="toggleStatus(thread.root)">
              {{ thread.root.status === 'open' ? '标记已解决' : '重新打开' }}
            </button>
            <button
              v-if="isOwnComment(thread.root)"
              class="button"
              type="button"
              @click.stop="startEdit(thread.root)"
            >
              编辑
            </button>
            <button
              v-if="isOwnComment(thread.root)"
              class="button"
              type="button"
              @click.stop="emit('deleteComment', thread.root.id)"
            >
              删除
            </button>
          </div>
          <div v-if="replyingToCommentId === thread.root.id" class="notes-comment__reply-box">
            <textarea
              v-model="replyBody"
              class="notes-panel__textarea"
              rows="3"
              placeholder="输入回复内容"
            />
            <div class="notes-panel__actions">
              <button class="button button--primary" type="button" :disabled="saving || !replyBody.trim()" @click.stop="submitReply(thread.root.id)">
                {{ saving ? '发送中...' : '发送回复' }}
              </button>
              <button class="button" type="button" @click.stop="replyingToCommentId = null">取消</button>
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
            <strong>{{ reply.author.username }}</strong>
            <span>{{ formatDateTime(reply.created_at) }}</span>
          </div>
          <p v-if="editingCommentId !== reply.id" class="notes-comment__body">{{ reply.body }}</p>
          <div v-else class="notes-comment__editor">
            <textarea v-model="editingBody" class="notes-panel__textarea" rows="3" />
            <div class="notes-panel__actions">
              <button class="button button--primary" type="button" :disabled="saving || !editingBody.trim()" @click.stop="saveEdit(reply.id)">
                {{ saving ? '保存中...' : '保存修改' }}
              </button>
              <button class="button" type="button" @click.stop="editingCommentId = null">取消</button>
            </div>
          </div>
          <div v-if="editingCommentId !== reply.id" class="notes-comment__actions">
            <button
              v-if="isOwnComment(reply)"
              class="button"
              type="button"
              @click.stop="startEdit(reply)"
            >
              编辑
            </button>
            <button
              v-if="isOwnComment(reply)"
              class="button"
              type="button"
              @click.stop="emit('deleteComment', reply.id)"
            >
              删除
            </button>
          </div>
        </section>
      </article>
    </div>
  </section>
</template>
