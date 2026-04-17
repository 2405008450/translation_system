<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

import type { CommentAnchorDraft, Segment, SegmentComment } from '../types/api'
import PreviewPanel from './PreviewPanel.vue'

const props = withDefaults(defineProps<{
  sourceHtml: string
  targetHtml: string
  sourceSupported: boolean
  targetSupported: boolean
  activeSentenceId: string | null
  targetRenderMode?: 'static' | 'target'
  targetSegments?: Segment[]
  targetUpdatedSentenceId?: string | null
  targetUpdatedSentenceText?: string
  targetUpdateToken?: number
  comments?: SegmentComment[]
  activeCommentId?: string | null
}>(), {
  targetRenderMode: 'static',
  targetSegments: () => [],
  targetUpdatedSentenceId: null,
  targetUpdatedSentenceText: '',
  targetUpdateToken: 0,
  comments: () => [],
  activeCommentId: null,
})

const emit = defineEmits<{
  close: []
  focusSentence: [sentenceId: string]
  focusComment: [commentId: string]
  requestComment: [draft: CommentAnchorDraft]
}>()

const layoutRef = ref<HTMLElement | null>(null)
const splitRatio = ref(0.5)
const syncScroll = ref(true)
const sourceSyncSentenceId = ref<string | null>(null)
const targetSyncSentenceId = ref<string | null>(null)

let dragCleanup: (() => void) | null = null

function clearSyncTargets() {
  sourceSyncSentenceId.value = null
  targetSyncSentenceId.value = null
}

function clampSplitRatio(nextValue: number) {
  return Math.min(0.7, Math.max(0.3, nextValue))
}

function handleVisibleSentence(panel: 'source' | 'target', sentenceId: string) {
  if (!syncScroll.value || !sentenceId) {
    return
  }

  if (panel === 'source') {
    sourceSyncSentenceId.value = null
    targetSyncSentenceId.value = sentenceId
    return
  }

  targetSyncSentenceId.value = null
  sourceSyncSentenceId.value = sentenceId
}

function handleDividerPointerDown(event: PointerEvent) {
  const layout = layoutRef.value
  if (!layout || window.innerWidth <= 1180) {
    return
  }

  const bounds = layout.getBoundingClientRect()
  const updateRatio = (clientX: number) => {
    splitRatio.value = clampSplitRatio((clientX - bounds.left) / bounds.width)
  }

  updateRatio(event.clientX)

  const handlePointerMove = (moveEvent: PointerEvent) => {
    updateRatio(moveEvent.clientX)
  }
  const handlePointerEnd = () => {
    window.removeEventListener('pointermove', handlePointerMove)
    window.removeEventListener('pointerup', handlePointerEnd)
    window.removeEventListener('pointercancel', handlePointerEnd)
    dragCleanup = null
  }

  window.addEventListener('pointermove', handlePointerMove)
  window.addEventListener('pointerup', handlePointerEnd)
  window.addEventListener('pointercancel', handlePointerEnd)
  dragCleanup = handlePointerEnd
}

watch(syncScroll, (enabled) => {
  if (!enabled) {
    clearSyncTargets()
  }
})

onBeforeUnmount(() => {
  dragCleanup?.()
})
</script>

<template>
  <section class="split-preview">
    <div class="split-preview__header">
      <div>
        <div class="section-title section-title--tight">分屏对照</div>
        <p class="panel-subtitle">原文与译文按句段联动，滚动时可保持双侧同步。</p>
      </div>

      <div class="split-preview__actions">
        <label class="split-preview__toggle">
          <input v-model="syncScroll" type="checkbox">
          <span>同步滚动</span>
        </label>
        <button class="button preview-panel__close" type="button" @click="emit('close')">关闭</button>
      </div>
    </div>

    <div
      ref="layoutRef"
      class="split-preview__layout"
      :style="{ '--split-left': `${splitRatio * 100}%` }"
    >
      <div class="split-preview__pane">
        <PreviewPanel
          title="原文预览"
          :html="sourceHtml"
          :supported="sourceSupported"
          :active-sentence-id="activeSentenceId"
          :comments="comments"
          :active-comment-id="activeCommentId"
          :enable-comment-selection="true"
          :sync-sentence-id="sourceSyncSentenceId"
          :closable="false"
          @focus-sentence="emit('focusSentence', $event)"
          @focus-comment="emit('focusComment', $event)"
          @request-comment="emit('requestComment', $event)"
          @visible-sentence-change="handleVisibleSentence('source', $event)"
        />
      </div>

      <button
        class="split-preview__divider"
        type="button"
        aria-label="调整左右预览宽度"
        @pointerdown="handleDividerPointerDown"
      />

      <div class="split-preview__pane">
        <PreviewPanel
          title="译文预览"
          :html="targetHtml"
          :supported="targetSupported"
          :active-sentence-id="activeSentenceId"
          :comments="comments"
          :active-comment-id="activeCommentId"
          :enable-comment-selection="true"
          :sync-sentence-id="targetSyncSentenceId"
          :render-mode="targetRenderMode"
          :segments="targetSegments"
          :updated-sentence-id="targetUpdatedSentenceId"
          :updated-sentence-text="targetUpdatedSentenceText"
          :update-token="targetUpdateToken"
          :closable="false"
          @focus-sentence="emit('focusSentence', $event)"
          @focus-comment="emit('focusComment', $event)"
          @request-comment="emit('requestComment', $event)"
          @visible-sentence-change="handleVisibleSentence('target', $event)"
        />
      </div>
    </div>
  </section>
</template>
