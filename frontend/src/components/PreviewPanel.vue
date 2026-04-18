<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { CommentAnchorDraft, SegmentComment } from '../types/api'
import FloatingCommentButton from './FloatingCommentButton.vue'

interface PreviewSegment {
  sentence_id: string
  source_text: string
  display_text?: string | null
  target_text?: string | null
}

const props = withDefaults(defineProps<{
  html: string
  activeSentenceId: string | null
  syncSentenceId?: string | null
  supported: boolean
  title?: string
  closable?: boolean
  renderMode?: 'static' | 'target'
  segments?: PreviewSegment[]
  updatedSentenceId?: string | null
  updatedSentenceText?: string
  updateToken?: number
  comments?: SegmentComment[]
  activeCommentId?: string | null
  enableCommentSelection?: boolean
}>(), {
  syncSentenceId: null,
  title: '原文预览',
  closable: true,
  renderMode: 'static',
  segments: () => [],
  updatedSentenceId: null,
  updatedSentenceText: '',
  updateToken: 0,
  comments: () => [],
  activeCommentId: null,
  enableCommentSelection: false,
})

const emit = defineEmits<{
  close: []
  focusSentence: [sentenceId: string]
  focusComment: [commentId: string]
  requestComment: [draft: CommentAnchorDraft]
  visibleSentenceChange: [sentenceId: string]
}>()

const containerRef = ref<HTMLElement | null>(null)
const currentPage = ref(1)
const totalPages = ref(1)
const isRendering = ref(false)
const pendingCommentSelection = ref<{
  top: number
  left: number
  draft: CommentAnchorDraft
} | null>(null)
const pageSummary = computed(() => {
  if (!props.supported) {
    return ''
  }
  return `分页感知：第 ${currentPage.value} / ${totalPages.value} 页`
})

let resizeObserver: ResizeObserver | null = null
let scrollFrame = 0
let selectionFrame = 0
let programmaticScrollTimer = 0
let ignoreScrollEvents = false
let lastVisibleSentenceId: string | null = null
let renderedHtmlSignature = ''
let renderSequence = 0
const sentenceNodeMap = new Map<string, HTMLElement>()
const appliedSentenceTexts = new Map<string, string>()

function escapeHtml(text: string) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function escapeAttribute(text: string) {
  return escapeHtml(text)
}

function getSentenceNode(sentenceId: string | null | undefined) {
  if (!sentenceId) {
    return null
  }
  const cachedNode = sentenceNodeMap.get(sentenceId)
  if (cachedNode) {
    return cachedNode
  }
  const node = containerRef.value?.querySelector<HTMLElement>(
    `.doc-sentence[data-sentence-id="${sentenceId}"]`,
  ) || null
  if (node) {
    sentenceNodeMap.set(sentenceId, node)
  }
  return node
}

function rebuildSentenceNodeMap(container: HTMLElement) {
  sentenceNodeMap.clear()
  for (const node of container.querySelectorAll<HTMLElement>('.doc-sentence[data-sentence-id]')) {
    const sentenceId = node.dataset.sentenceId
    if (sentenceId) {
      sentenceNodeMap.set(sentenceId, node)
    }
  }
}

function resolveSegmentPreviewText(segment: PreviewSegment) {
  return segment.target_text || segment.display_text || segment.source_text || ''
}

function resolveCommentRange(text: string, comment: SegmentComment) {
  if (comment.anchor_mode === 'sentence') {
    return text ? { start: 0, end: text.length } : null
  }

  const start = comment.range_start_offset
  const end = comment.range_end_offset
  const anchorText = comment.anchor_text || ''
  if (
    typeof start === 'number'
    && typeof end === 'number'
    && start >= 0
    && end > start
    && end <= text.length
    && text.slice(start, end) === anchorText
  ) {
    return { start, end }
  }

  if (anchorText) {
    const fallbackStart = text.indexOf(anchorText)
    if (fallbackStart !== -1) {
      return {
        start: fallbackStart,
        end: fallbackStart + anchorText.length,
      }
    }
  }

  if (
    typeof start === 'number'
    && typeof end === 'number'
    && start >= 0
    && end > start
    && end <= text.length
  ) {
    return { start, end }
  }

  return null
}

function buildSentenceMarkup(text: string, comments: SegmentComment[]) {
  if (!comments.length) {
    return escapeHtml(text)
  }

  const ranges = comments
    .map((comment) => {
      const range = resolveCommentRange(text, comment)
      if (!range) {
        return null
      }
      return {
        comment,
        ...range,
      }
    })
    .filter((item): item is { comment: SegmentComment; start: number; end: number } => Boolean(item))
    .sort((left, right) => {
      if (left.start !== right.start) {
        return left.start - right.start
      }
      if (left.end !== right.end) {
        return left.end - right.end
      }
      return left.comment.id.localeCompare(right.comment.id)
    })

  if (!ranges.length) {
    return escapeHtml(text)
  }

  const parts: string[] = []
  let cursor = 0

  for (const range of ranges) {
    if (range.start < cursor) {
      continue
    }

    if (range.start > cursor) {
      parts.push(escapeHtml(text.slice(cursor, range.start)))
    }

    const classes = ['doc-comment-anchor']
    if (range.comment.status === 'resolved') {
      classes.push('is-resolved')
    }
    if (range.comment.id === props.activeCommentId) {
      classes.push('is-active')
    }

    parts.push(
      `<mark class="${classes.join(' ')}" data-comment-id="${escapeAttribute(range.comment.id)}">`
      + `${escapeHtml(text.slice(range.start, range.end))}`
      + '</mark>',
    )
    cursor = range.end
  }

  if (cursor < text.length) {
    parts.push(escapeHtml(text.slice(cursor)))
  }

  return parts.join('')
}

function renderSentenceComments(sentenceId: string) {
  const node = getSentenceNode(sentenceId)
  if (!node) {
    return
  }

  const text = node.textContent || ''
  const sentenceComments = props.comments.filter((comment) => comment.sentence_id === sentenceId)

  if (!sentenceComments.length) {
    node.textContent = text
    node.classList.remove('has-comments')
    delete node.dataset.commentCount
    return
  }

  node.innerHTML = buildSentenceMarkup(text, sentenceComments)
  node.classList.add('has-comments')
  node.dataset.commentCount = String(sentenceComments.length)
}

function applyCommentDecorations() {
  for (const sentenceId of sentenceNodeMap.keys()) {
    renderSentenceComments(sentenceId)
  }
}

function applySentenceText(
  sentenceId: string | null | undefined,
  text: string,
  force = false,
) {
  if (!sentenceId) {
    return
  }

  const node = getSentenceNode(sentenceId)
  if (!node) {
    return
  }

  if (!force && appliedSentenceTexts.get(sentenceId) === text) {
    return
  }

  node.textContent = text
  appliedSentenceTexts.set(sentenceId, text)
  renderSentenceComments(sentenceId)
}

function applyTargetSentenceTexts(force = false) {
  if (props.renderMode !== 'target') {
    return
  }

  for (const segment of props.segments) {
    applySentenceText(segment.sentence_id, resolveSegmentPreviewText(segment), force)
  }
}

async function renderPreviewContent(forceHtml = false) {
  await nextTick()
  const container = containerRef.value
  if (!container || !props.supported) {
    return
  }

  if (forceHtml || renderedHtmlSignature !== props.html) {
    container.innerHTML = props.html
    renderedHtmlSignature = props.html
    appliedSentenceTexts.clear()
    rebuildSentenceNodeMap(container)
  }

  applyTargetSentenceTexts(forceHtml)
  applyCommentDecorations()
}

function waitForPaint() {
  return new Promise<void>((resolve) => {
    if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
      resolve()
      return
    }
    window.requestAnimationFrame(() => resolve())
  })
}

async function runRenderCycle(forceHtml = false, scrollToActive = false) {
  const sequence = ++renderSequence
  isRendering.value = true

  try {
    await nextTick()
    await waitForPaint()
    await renderPreviewContent(forceHtml)
    await applyHighlight(scrollToActive)
    highlightActiveComment(false)
  } finally {
    if (sequence === renderSequence) {
      isRendering.value = false
    }
  }
}

function updatePagination() {
  const container = containerRef.value
  if (!container) {
    currentPage.value = 1
    totalPages.value = 1
    return
  }

  const viewportHeight = Math.max(container.clientHeight, 1)
  container.style.setProperty('--preview-page-height', `${viewportHeight}px`)

  const explicitBreaks = Array.from(container.querySelectorAll<HTMLElement>('.doc-page-break'))
  const estimatedPages = Math.max(1, Math.ceil(container.scrollHeight / viewportHeight))
  totalPages.value = explicitBreaks.length ? Math.max(explicitBreaks.length + 1, estimatedPages) : estimatedPages

  if (explicitBreaks.length) {
    const scrollAnchor = container.scrollTop + 8
    currentPage.value = 1 + explicitBreaks.filter((item) => item.offsetTop <= scrollAnchor).length
    return
  }

  currentPage.value = Math.min(
    totalPages.value,
    Math.max(1, Math.floor(container.scrollTop / viewportHeight) + 1),
  )
}

function findVisibleSentenceId() {
  const container = containerRef.value
  if (!container) {
    return null
  }

  const sentences = Array.from(
    container.querySelectorAll<HTMLElement>('.doc-sentence[data-sentence-id]'),
  )
  if (!sentences.length) {
    return null
  }

  const viewportCenter = container.scrollTop + container.clientHeight / 2
  let bestSentenceId = sentences[0].dataset.sentenceId || null
  let bestDistance = Number.POSITIVE_INFINITY

  for (const sentence of sentences) {
    const distance = Math.abs(sentence.offsetTop + sentence.offsetHeight / 2 - viewportCenter)
    if (distance < bestDistance) {
      bestDistance = distance
      bestSentenceId = sentence.dataset.sentenceId || null
    }
  }

  return bestSentenceId
}

function notifyVisibleSentence() {
  const sentenceId = findVisibleSentenceId()
  if (!sentenceId || sentenceId === lastVisibleSentenceId) {
    return
  }
  lastVisibleSentenceId = sentenceId
  emit('visibleSentenceChange', sentenceId)
}

function scheduleScrollMetrics() {
  if (scrollFrame) {
    window.cancelAnimationFrame(scrollFrame)
  }

  scrollFrame = window.requestAnimationFrame(() => {
    scrollFrame = 0
    updatePagination()
    notifyVisibleSentence()
  })
}

function markProgrammaticScroll(durationMs = 220) {
  ignoreScrollEvents = true
  window.clearTimeout(programmaticScrollTimer)
  programmaticScrollTimer = window.setTimeout(() => {
    ignoreScrollEvents = false
    updatePagination()
    notifyVisibleSentence()
  }, durationMs)
}

function scrollSentenceIntoView(sentenceId: string | null | undefined, behavior: ScrollBehavior = 'smooth') {
  const target = getSentenceNode(sentenceId)
  if (!target) {
    return
  }

  markProgrammaticScroll(260)
  target.scrollIntoView({
    block: 'center',
    behavior,
  })
}

function scrollCommentIntoView(commentId: string, behavior: ScrollBehavior = 'smooth') {
  const target = containerRef.value?.querySelector<HTMLElement>(
    `.doc-comment-anchor[data-comment-id="${commentId}"]`,
  )
  if (!target) {
    return
  }

  markProgrammaticScroll(260)
  target.scrollIntoView({
    block: 'center',
    behavior,
  })
}

async function applyHighlight(scrollToActive = true) {
  await nextTick()
  const container = containerRef.value
  if (!container) {
    return
  }

  const current = container.querySelector('.doc-sentence.is-active')
  current?.classList.remove('is-active')

  if (!props.activeSentenceId) {
    updatePagination()
    notifyVisibleSentence()
    return
  }

  const target = getSentenceNode(props.activeSentenceId)
  if (!target) {
    updatePagination()
    notifyVisibleSentence()
    return
  }

  target.classList.add('is-active')
  if (scrollToActive) {
    scrollSentenceIntoView(props.activeSentenceId)
    return
  }

  updatePagination()
  notifyVisibleSentence()
}

function highlightActiveComment(scrollIntoView = true) {
  applyCommentDecorations()
  if (props.activeCommentId && scrollIntoView) {
    scrollCommentIntoView(props.activeCommentId)
  }
}

function getSentenceElementFromNode(node: Node | null) {
  if (!node) {
    return null
  }
  if (node instanceof HTMLElement) {
    return node.closest<HTMLElement>('.doc-sentence[data-sentence-id]')
  }
  return node.parentElement?.closest<HTMLElement>('.doc-sentence[data-sentence-id]') || null
}

function getTextOffsetWithin(root: HTMLElement, targetNode: Node, targetOffset: number) {
  let total = 0
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)

  while (walker.nextNode()) {
    const currentNode = walker.currentNode
    if (currentNode === targetNode) {
      return total + targetOffset
    }
    total += currentNode.textContent?.length || 0
  }

  return -1
}

function clearPendingCommentSelection(removeSelection = false) {
  pendingCommentSelection.value = null
  if (removeSelection) {
    window.getSelection()?.removeAllRanges()
  }
}

function syncPendingCommentSelection() {
  if (!props.enableCommentSelection) {
    clearPendingCommentSelection()
    return
  }

  const container = containerRef.value
  const selection = window.getSelection()
  if (!container || !selection || selection.rangeCount === 0 || selection.isCollapsed) {
    clearPendingCommentSelection()
    return
  }

  const range = selection.getRangeAt(0)
  if (!container.contains(range.commonAncestorContainer)) {
    clearPendingCommentSelection()
    return
  }

  const startSentence = getSentenceElementFromNode(range.startContainer)
  const endSentence = getSentenceElementFromNode(range.endContainer)
  if (!startSentence || !endSentence || startSentence !== endSentence) {
    clearPendingCommentSelection()
    return
  }

  const sentenceId = startSentence.dataset.sentenceId
  if (!sentenceId) {
    clearPendingCommentSelection()
    return
  }

  const selectedText = selection.toString().trim()
  if (!selectedText) {
    clearPendingCommentSelection()
    return
  }

  const startOffset = getTextOffsetWithin(startSentence, range.startContainer, range.startOffset)
  const endOffset = getTextOffsetWithin(startSentence, range.endContainer, range.endOffset)
  if (startOffset < 0 || endOffset <= startOffset) {
    clearPendingCommentSelection()
    return
  }

  const rect = range.getBoundingClientRect()
  if (!rect.width && !rect.height) {
    clearPendingCommentSelection()
    return
  }

  pendingCommentSelection.value = {
    top: Math.max(16, rect.top - 44),
    left: Math.min(Math.max(rect.left + rect.width / 2, 88), window.innerWidth - 88),
    draft: {
      sentence_id: sentenceId,
      anchor_mode: 'range',
      range_start_offset: startOffset,
      range_end_offset: endOffset,
      anchor_text: selectedText,
    },
  }
}

function handleSelectionChange() {
  if (selectionFrame) {
    window.cancelAnimationFrame(selectionFrame)
  }
  selectionFrame = window.requestAnimationFrame(() => {
    selectionFrame = 0
    syncPendingCommentSelection()
  })
}

function requestPendingComment() {
  if (!pendingCommentSelection.value) {
    return
  }
  emit('requestComment', pendingCommentSelection.value.draft)
  clearPendingCommentSelection(true)
}

function handleScroll() {
  clearPendingCommentSelection()
  if (ignoreScrollEvents) {
    return
  }
  scheduleScrollMetrics()
}

function handleClick(event: MouseEvent) {
  const target = event.target instanceof HTMLElement ? event.target : null
  const commentAnchor = target?.closest<HTMLElement>('.doc-comment-anchor[data-comment-id]')
  const commentId = commentAnchor?.dataset.commentId
  if (commentId) {
    clearPendingCommentSelection(true)
    emit('focusComment', commentId)
    return
  }

  const sentence = target?.closest<HTMLElement>('.doc-sentence[data-sentence-id]')
  const sentenceId = sentence?.dataset.sentenceId
  if (!sentenceId) {
    return
  }
  emit('focusSentence', sentenceId)
}

onMounted(() => {
  void runRenderCycle(true, true)

  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      updatePagination()
      notifyVisibleSentence()
    })
    if (containerRef.value) {
      resizeObserver.observe(containerRef.value)
    }
  }

  document.addEventListener('selectionchange', handleSelectionChange)
})

onBeforeUnmount(() => {
  if (scrollFrame) {
    window.cancelAnimationFrame(scrollFrame)
  }
  if (selectionFrame) {
    window.cancelAnimationFrame(selectionFrame)
  }
  window.clearTimeout(programmaticScrollTimer)
  document.removeEventListener('selectionchange', handleSelectionChange)
  resizeObserver?.disconnect()
})

watch(() => props.html, () => {
  lastVisibleSentenceId = null
  clearPendingCommentSelection()
  void runRenderCycle(true, false)
})

watch(() => props.supported, (supported) => {
  if (!supported) {
    renderedHtmlSignature = ''
    lastVisibleSentenceId = null
    sentenceNodeMap.clear()
    appliedSentenceTexts.clear()
    isRendering.value = false
    clearPendingCommentSelection()
    return
  }

  void runRenderCycle(true, false)
})

watch(() => props.renderMode, () => {
  lastVisibleSentenceId = null
  clearPendingCommentSelection()
  void runRenderCycle(true, false)
})

watch(() => props.updateToken, (token, previousToken) => {
  if (props.renderMode !== 'target' || token === previousToken) {
    return
  }

  void nextTick(() => {
    applySentenceText(props.updatedSentenceId, props.updatedSentenceText, false)
    highlightActiveComment(false)
    updatePagination()
    notifyVisibleSentence()
  })
})

watch(() => props.comments, () => {
  void nextTick(() => {
    highlightActiveComment(false)
    updatePagination()
    notifyVisibleSentence()
  })
}, { deep: true })

watch(() => props.activeSentenceId, () => {
  void applyHighlight(true)
})

watch(() => props.activeCommentId, (commentId, previousCommentId) => {
  if (commentId === previousCommentId) {
    return
  }
  void nextTick(() => {
    highlightActiveComment(Boolean(commentId))
  })
})

watch(() => props.syncSentenceId, (sentenceId) => {
  if (!sentenceId || sentenceId === props.activeSentenceId) {
    return
  }
  void nextTick(() => {
    scrollSentenceIntoView(sentenceId, 'auto')
  })
})
</script>

<template>
  <section class="preview-panel">
    <div class="preview-panel__header">
      <div>
        <div class="section-title section-title--tight">{{ title }}</div>
        <p v-if="pageSummary" class="preview-panel__meta">{{ pageSummary }}</p>
      </div>
      <button v-if="closable" class="button preview-panel__close" type="button" @click="emit('close')">
        关闭
      </button>
    </div>

    <div class="preview-panel__viewport">
      <div class="preview-panel__paper">
        <div
          v-if="supported"
          ref="containerRef"
          class="preview-panel__body"
          :aria-busy="isRendering"
          @click="handleClick"
          @scroll="handleScroll"
        />
        <div v-else class="preview-panel__empty">当前任务没有可展示的预览内容</div>
        <div v-if="supported && isRendering" class="preview-panel__loading">
          <span class="preview-panel__spinner" aria-hidden="true" />
          <span>预览加载中...</span>
        </div>
        <FloatingCommentButton
          v-if="pendingCommentSelection"
          :top="pendingCommentSelection.top"
          :left="pendingCommentSelection.left"
          @create="requestPendingComment"
        />
      </div>
    </div>
  </section>
</template>
