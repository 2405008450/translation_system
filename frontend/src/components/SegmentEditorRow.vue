<script setup lang="ts">
import { computed, onMounted, ref, watch, nextTick } from 'vue'

import InteractiveDiffText from './InteractiveDiffText.vue'

import { getSegmentSourceMeta, getSegmentStatusMeta } from '../constants/status'
import type { Segment, SegmentRevisionEntry, TermEntryRecord } from '../types/api'
import { computeDiff } from '../utils/textDiff'

const props = withDefaults(defineProps<{
  segment: Segment
  index: number
  active: boolean
  disabled?: boolean
  pendingRevision?: SegmentRevisionEntry | null
  revisionBusy?: boolean
  matchedTerms?: TermEntryRecord[]
  sourceSearchQuery?: string
}>(), {
  disabled: false,
  pendingRevision: null,
  revisionBusy: false,
  matchedTerms: () => [],
  sourceSearchQuery: '',
})

const emit = defineEmits<{
  update: [sentenceId: string, value: string]
  focus: [sentenceId: string]
  activateTarget: [sentenceId: string]
  applyPartialRevision: [revisionId: string, newText: string]
}>()

const editorRef = ref<HTMLDivElement | null>(null)
const isFocused = ref(false)
const isComposing = ref(false)
const MAX_EDITOR_HISTORY_SIZE = 100

interface EditorHistorySnapshot {
  text: string
  caretOffset: number
}

type HighlightKind = 'term' | 'search'
type HighlightPart = { text: string; highlight: boolean; kind?: HighlightKind }

const undoStack = ref<EditorHistorySnapshot[]>([])
const redoStack = ref<EditorHistorySnapshot[]>([])
const isApplyingHistory = ref(false)
const compositionSnapshotRecorded = ref(false)
const canUndoEditorChange = computed(() => undoStack.value.length > 0)
const canRedoEditorChange = computed(() => redoStack.value.length > 0)

const statusClass = computed(() => `segment-row--${props.segment.status || 'none'}`)
const parityClass = computed(() => (props.index % 2 === 0 ? 'segment-row--odd' : 'segment-row--even'))
const sourceClass = computed(() => `segment-row__tag--source-${props.segment.source || 'none'}`)
const isEmptyTarget = computed(() => {
  const targetText = props.pendingRevision?.after_text ?? props.segment.target_text ?? ''
  return targetText.trim().length === 0
})
const statusMeta = computed(() => getSegmentStatusMeta(props.segment.status))
const sourceMeta = computed(() => getSegmentSourceMeta(props.segment.source))
const sourceLabel = computed(() => {
  if (props.segment.source === 'llm') {
    return props.segment.llm_model?.trim() || sourceMeta.value.label
  }
  return sourceMeta.value.label
})
const sourceTitle = computed(() => {
  if (props.segment.source === 'llm' && props.segment.llm_model?.trim()) {
    return props.segment.llm_provider
      ? `${props.segment.llm_model} (${props.segment.llm_provider})`
      : props.segment.llm_model
  }
  return sourceMeta.value.label
})
const revisionSourceMeta = computed(() => getSegmentSourceMeta(props.pendingRevision?.source || 'manual'))
const revisionAuthorRole = computed(() => props.pendingRevision?.author?.role || 'admin')
const hasPendingRevision = computed(() => Boolean(props.pendingRevision))
const revisionAuthorClass = computed(() => (
  revisionAuthorRole.value === 'user' ? 'is-revision-author-user' : 'is-revision-author-admin'
))
const scorePercent = computed(() => {
  if (!props.segment.score || props.segment.score <= 0) return null
  return Math.round(props.segment.score * 100)
})

// 通用的文本高亮函数
function highlightText(
  text: string,
  terms: TermEntryRecord[],
  field: 'source_text' | 'target_text'
): HighlightPart[] | null {
  if (!text || terms.length === 0) {
    return null
  }

  // 按长度降序排列，优先匹配长术语
  const sortedTerms = [...terms].sort(
    (a, b) => b[field].length - a[field].length
  )

  // 找出所有匹配位置
  const matches: Array<{ start: number; end: number }> = []
  const lowerText = text.toLowerCase()

  for (const term of sortedTerms) {
    const termText = term[field]
    if (!termText) continue
    const lowerTerm = termText.toLowerCase()
    let pos = 0
    while ((pos = lowerText.indexOf(lowerTerm, pos)) !== -1) {
      // 检查是否与已有匹配重叠
      const overlaps = matches.some(
        (m) => !(pos + lowerTerm.length <= m.start || pos >= m.end)
      )
      if (!overlaps) {
        matches.push({ start: pos, end: pos + lowerTerm.length })
      }
      pos += 1
    }
  }

  if (matches.length === 0) {
    return null
  }

  // 按位置排序
  matches.sort((a, b) => a.start - b.start)

  // 构建分段
  const segments: HighlightPart[] = []
  let lastEnd = 0

  for (const match of matches) {
    if (match.start > lastEnd) {
      segments.push({ text: text.slice(lastEnd, match.start), highlight: false })
    }
    segments.push({ text: text.slice(match.start, match.end), highlight: true, kind: 'term' })
    lastEnd = match.end
  }

  if (lastEnd < text.length) {
    segments.push({ text: text.slice(lastEnd), highlight: false })
  }

  return segments
}

// 高亮原文中匹配的术语
function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function highlightSearchText(text: string, keyword: string): HighlightPart[] | null {
  const query = keyword.trim()
  if (!text || !query) {
    return null
  }

  const regexp = new RegExp(escapeRegExp(query), 'gi')
  const matches = Array.from(text.matchAll(regexp))
    .map((match) => ({
      start: match.index ?? 0,
      end: (match.index ?? 0) + match[0].length,
    }))
    .filter((match) => match.end > match.start)

  if (matches.length === 0) {
    return null
  }

  const segments: HighlightPart[] = []
  let lastEnd = 0

  for (const match of matches) {
    if (match.start > lastEnd) {
      segments.push({ text: text.slice(lastEnd, match.start), highlight: false })
    }
    segments.push({ text: text.slice(match.start, match.end), highlight: true, kind: 'search' })
    lastEnd = match.end
  }

  if (lastEnd < text.length) {
    segments.push({ text: text.slice(lastEnd), highlight: false })
  }

  return segments
}

const highlightedSourceText = computed(() => {
  const text = props.segment.display_text || props.segment.source_text
  return highlightSearchText(text, props.sourceSearchQuery) || highlightText(text, props.matchedTerms || [], 'source_text')
})

// 高亮译文中匹配的术语
const highlightedTargetText = computed(() => {
  const text = props.segment.target_text || ''
  return highlightText(text, props.matchedTerms || [], 'target_text')
})

// 生成带高亮的 HTML
const targetHtmlContent = computed(() => {
  const segments = highlightedTargetText.value
  if (!segments) {
    return escapeHtml(props.segment.target_text || '')
  }
  return segments
    .map((seg) =>
      seg.highlight
        ? `<mark class="segment-row__term-highlight">${escapeHtml(seg.text)}</mark>`
        : escapeHtml(seg.text)
    )
    .join('')
})

const editorHtmlContent = computed(() => {
  const revision = props.pendingRevision
  if (!revision) {
    return targetHtmlContent.value
  }
  return computeDiff(revision.before_text || '', revision.after_text || '')
    .map((segment) => {
      const editableAttr = segment.type === 'delete' ? ' contenteditable="false"' : ''
      return [
        `<span class="segment-row__revision-segment segment-row__revision-${segment.type}"`,
        ` data-revision-type="${segment.type}"`,
        ` data-testid="segment-revision-${segment.type}"`,
        editableAttr,
        '>',
        renderTargetTextHtml(segment.text),
        '</span>',
      ].join('')
    })
    .join('')
})

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderTargetTextHtml(text: string): string {
  const segments = highlightText(text, props.matchedTerms || [], 'target_text')
  if (!segments) {
    return escapeHtml(text)
  }
  return segments
    .map((seg) =>
      seg.highlight
        ? `<mark class="segment-row__term-highlight">${escapeHtml(seg.text)}</mark>`
        : escapeHtml(seg.text)
    )
    .join('')
}

// 保存和恢复光标位置
function saveCaretPosition(el: HTMLElement): number {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) return 0
  
  const range = selection.getRangeAt(0)
  const preCaretRange = range.cloneRange()
  preCaretRange.selectNodeContents(el)
  preCaretRange.setEnd(range.startContainer, range.startOffset)
  return preCaretRange.toString().length
}

function restoreCaretPosition(el: HTMLElement, offset: number) {
  const selection = window.getSelection()
  if (!selection) return

  const range = document.createRange()
  let currentOffset = 0
  let found = false

  function traverse(node: Node): boolean {
    if (node.nodeType === Node.TEXT_NODE) {
      const textLength = node.textContent?.length || 0
      if (currentOffset + textLength >= offset) {
        range.setStart(node, offset - currentOffset)
        range.collapse(true)
        return true
      }
      currentOffset += textLength
    } else {
      for (const child of Array.from(node.childNodes)) {
        if (traverse(child)) return true
      }
    }
    return false
  }

  found = traverse(el)
  if (!found) {
    // 如果没找到，放到末尾
    range.selectNodeContents(el)
    range.collapse(false)
  }

  selection.removeAllRanges()
  selection.addRange(range)
}

function isRevisionDeleteNode(node: Node): boolean {
  const element = node.nodeType === Node.ELEMENT_NODE
    ? node as Element
    : node.parentElement
  return Boolean(element?.closest('[data-revision-type="delete"]'))
}

function getSerializableNodeLength(node: Node): number {
  if (isRevisionDeleteNode(node)) {
    return 0
  }
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent?.length || 0
  }
  if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR') {
    return 1
  }
  return Array.from(node.childNodes).reduce((total, child) => total + getSerializableNodeLength(child), 0)
}

function serializeEditorContent(node: Node): string {
  if (isRevisionDeleteNode(node)) {
    return ''
  }
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent || ''
  }
  if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR') {
    return '\n'
  }
  return Array.from(node.childNodes).map(serializeEditorContent).join('')
}

function saveSerializableCaretPosition(el: HTMLElement): number {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) return 0

  const range = selection.getRangeAt(0)
  if (!el.contains(range.startContainer)) {
    return 0
  }

  let offset = 0
  let found = false

  function traverse(node: Node): boolean {
    if (found) {
      return true
    }

    if (node === range.startContainer) {
      if (node.nodeType === Node.TEXT_NODE) {
        offset += isRevisionDeleteNode(node)
          ? 0
          : (node.textContent || '').slice(0, range.startOffset).length
      } else {
        const children = Array.from(node.childNodes).slice(0, range.startOffset)
        offset += children.reduce((total, child) => total + getSerializableNodeLength(child), 0)
      }
      found = true
      return true
    }

    if (node.nodeType === Node.TEXT_NODE || (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR')) {
      offset += getSerializableNodeLength(node)
      return false
    }

    for (const child of Array.from(node.childNodes)) {
      if (traverse(child)) {
        return true
      }
    }
    return false
  }

  traverse(el)
  return offset
}

function restoreSerializableCaretPosition(el: HTMLElement, offset: number) {
  const selection = window.getSelection()
  if (!selection) return

  const range = document.createRange()
  let currentOffset = 0
  let found = false

  function traverse(node: Node): boolean {
    if (isRevisionDeleteNode(node)) {
      return false
    }
    if (node.nodeType === Node.TEXT_NODE) {
      const textLength = node.textContent?.length || 0
      if (currentOffset + textLength >= offset) {
        range.setStart(node, offset - currentOffset)
        range.collapse(true)
        return true
      }
      currentOffset += textLength
    } else if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR') {
      if (currentOffset + 1 >= offset) {
        range.setStartBefore(node)
        range.collapse(true)
        return true
      }
      currentOffset += 1
    } else {
      for (const child of Array.from(node.childNodes)) {
        if (traverse(child)) return true
      }
    }
    return false
  }

  found = traverse(el)
  if (!found) {
    range.selectNodeContents(el)
    range.collapse(false)
  }

  selection.removeAllRanges()
  selection.addRange(range)
}

function getCurrentEditorText(): string {
  if (editorRef.value) {
    return serializeEditorContent(editorRef.value)
  }
  return props.pendingRevision?.after_text ?? props.segment.target_text ?? ''
}

function getCurrentEditorSnapshot(): EditorHistorySnapshot {
  const text = getCurrentEditorText()
  return {
    text,
    caretOffset: editorRef.value
      ? saveSerializableCaretPosition(editorRef.value)
      : text.length,
  }
}

function pushHistorySnapshot(stack: EditorHistorySnapshot[], snapshot: EditorHistorySnapshot) {
  const lastSnapshot = stack[stack.length - 1]
  if (lastSnapshot?.text === snapshot.text) {
    return
  }
  stack.push(snapshot)
  if (stack.length > MAX_EDITOR_HISTORY_SIZE) {
    stack.shift()
  }
}

function clearEditorHistory() {
  undoStack.value = []
  redoStack.value = []
  compositionSnapshotRecorded.value = false
}

function recordUndoSnapshot(clearRedo = true) {
  if (props.disabled || !editorRef.value || isApplyingHistory.value || isComposing.value) {
    return
  }
  pushHistorySnapshot(undoStack.value, getCurrentEditorSnapshot())
  if (clearRedo) {
    redoStack.value = []
  }
}

function applyHistorySnapshot(snapshot: EditorHistorySnapshot) {
  isApplyingHistory.value = true
  emit('update', props.segment.sentence_id, snapshot.text)
  void nextTick(() => {
    if (editorRef.value) {
      editorRef.value.innerHTML = editorHtmlContent.value
      editorRef.value.focus({ preventScroll: true })
      restoreSerializableCaretPosition(editorRef.value, snapshot.caretOffset)
    }
    isApplyingHistory.value = false
  })
}

function undoEditorChange() {
  const targetSnapshot = undoStack.value.pop()
  if (!targetSnapshot) {
    return false
  }
  pushHistorySnapshot(redoStack.value, getCurrentEditorSnapshot())
  applyHistorySnapshot(targetSnapshot)
  return true
}

function redoEditorChange() {
  const targetSnapshot = redoStack.value.pop()
  if (!targetSnapshot) {
    return false
  }
  pushHistorySnapshot(undoStack.value, getCurrentEditorSnapshot())
  applyHistorySnapshot(targetSnapshot)
  return true
}

function handleFocus() {
  isFocused.value = true
  emit('focus', props.segment.sentence_id)
}

function handleBlur() {
  isFocused.value = false
}

function handleClick() {
  emit('activateTarget', props.segment.sentence_id)
}

function handleEditorShellClick() {
  handleClick()
  void nextTick(() => {
    editorRef.value?.focus({ preventScroll: true })
  })
}

function handleBeforeInput(event: Event) {
  const inputEvent = event as InputEvent
  if (props.disabled || isApplyingHistory.value) {
    return
  }

  if (inputEvent.inputType === 'historyUndo') {
    inputEvent.preventDefault()
    if (!isComposing.value) {
      undoEditorChange()
    }
    return
  }

  if (inputEvent.inputType === 'historyRedo') {
    inputEvent.preventDefault()
    if (!isComposing.value) {
      redoEditorChange()
    }
    return
  }

  if (isComposing.value || inputEvent.isComposing || inputEvent.inputType === 'insertFromPaste') {
    return
  }

  recordUndoSnapshot()
}

function handleKeydown(event: KeyboardEvent) {
  if (props.disabled || isApplyingHistory.value || event.altKey) {
    return
  }

  const usesShortcutModifier = event.ctrlKey || event.metaKey
  if (!usesShortcutModifier) {
    return
  }

  const key = event.key.toLowerCase()
  if (key === 'z') {
    event.preventDefault()
    if (isComposing.value) {
      return
    }
    if (event.shiftKey) {
      redoEditorChange()
    } else {
      undoEditorChange()
    }
    return
  }

  if (key === 'y') {
    event.preventDefault()
    if (!isComposing.value) {
      redoEditorChange()
    }
  }
}

function handleInput() {
  if (!editorRef.value) return
  if (isApplyingHistory.value) return
  if (isComposing.value) return

  const text = serializeEditorContent(editorRef.value)
  emit('update', props.segment.sentence_id, text)
}

// 监听外部数据变化，更新编辑器内容
function handleCompositionStart() {
  if (!compositionSnapshotRecorded.value) {
    recordUndoSnapshot()
    compositionSnapshotRecorded.value = true
  }
  isComposing.value = true
}

function handleCompositionEnd() {
  isComposing.value = false
  handleInput()
  compositionSnapshotRecorded.value = false
}

function handlePaste(event: ClipboardEvent) {
  event.preventDefault()
  recordUndoSnapshot()
  const text = event.clipboardData?.getData('text/plain') || ''
  document.execCommand('insertText', false, text)
}

function syncEditorHtmlFromState(preserveCaret: boolean) {
  const editor = editorRef.value
  if (!editor || isApplyingHistory.value || isComposing.value) {
    return
  }

  const nextHtml = editorHtmlContent.value
  if (editor.innerHTML === nextHtml) {
    return
  }

  const caretPos = preserveCaret ? saveSerializableCaretPosition(editor) : 0
  editor.innerHTML = nextHtml
  if (preserveCaret && isFocused.value) {
    restoreSerializableCaretPosition(editor, caretPos)
  }
}

onMounted(() => {
  if (editorRef.value) {
    editorRef.value.innerHTML = editorHtmlContent.value
  }
})

watch(
  () => props.segment.sentence_id,
  () => {
    clearEditorHistory()
  }
)

watch(
  () => props.segment.target_text,
  () => {
    if (!isFocused.value && !isApplyingHistory.value) {
      clearEditorHistory()
    }
    if (!isFocused.value && editorRef.value) {
      syncEditorHtmlFromState(false)
    }
  }
)

watch(
  () => props.pendingRevision?.id ?? null,
  () => {
    if (!isFocused.value && !isApplyingHistory.value) {
      clearEditorHistory()
    }
  }
)

// 监听高亮内容变化
watch(
  editorHtmlContent,
  () => {
    syncEditorHtmlFromState(isFocused.value)
  },
  { flush: 'post' },
)

defineExpose({
  undoEditorChange,
  redoEditorChange,
  canUndoEditorChange,
  canRedoEditorChange,
})

</script>

<template>
  <article
    class="segment-row"
    :class="[statusClass, parityClass, { 'is-active': active, 'has-pending-revision': hasPendingRevision, 'is-empty-target': isEmptyTarget }]"
    :id="`segment-${segment.sentence_id}`"
    data-testid="segment-row"
    :data-sentence-id="segment.sentence_id"
    :data-has-pending-revision="hasPendingRevision ? 'true' : 'false'"
    role="group"
    :aria-label="`segment ${index + 1}`"
  >
    <div class="segment-row__meta">
      <span class="segment-row__index">#{{ index + 1 }}</span>
      <span class="segment-row__tag segment-row__tag--status">{{ statusMeta.label }}</span>
      <span class="segment-row__tag is-muted" :class="sourceClass" :title="sourceTitle">{{ sourceLabel }}</span>
      <span v-if="scorePercent !== null" class="segment-row__tag segment-row__tag--score">
        {{ scorePercent }}%
      </span>
      <span
        v-if="hasPendingRevision"
        class="segment-row__tag segment-row__tag--revision"
        data-testid="segment-revision-tag"
        :title="`修订来源：${revisionSourceMeta.label}`"
      >
        待审核
      </span>
    </div>

    <div class="segment-row__cell segment-row__cell--source" @click="handleClick">
      <div class="segment-row__text">
        <template v-if="highlightedSourceText">
          <template v-for="(seg, idx) in highlightedSourceText" :key="idx">
            <mark
              v-if="seg.highlight"
              :class="seg.kind === 'search' ? 'segment-row__search-highlight' : 'segment-row__term-highlight'"
            >
              {{ seg.text }}
            </mark>
            <template v-else>{{ seg.text }}</template>
          </template>
        </template>
        <template v-else>{{ segment.display_text || segment.source_text }}</template>
      </div>
    </div>

    <div class="segment-row__cell segment-row__cell--target" :class="{ 'is-pending': hasPendingRevision }">
      <div
        class="segment-row__editor-shell"
        :class="{ 'is-focused': isFocused, 'is-disabled': disabled, 'has-revision': hasPendingRevision }"
        @click="handleEditorShellClick"
      >
      <div
        v-if="false && pendingRevision"
        class="segment-row__revision-inline"
        data-testid="segment-revision-inline"
        :data-sentence-id="segment.sentence_id"
        :aria-label="`translation revision for segment ${index + 1}`"
        @click="handleClick"
      >
        <InteractiveDiffText
          :key="`${pendingRevision?.id || ''}:${pendingRevision?.after_text || ''}`"
          class="segment-row__revision-diff"
          :old-text="pendingRevision?.before_text || ''"
          :new-text="pendingRevision?.after_text || ''"
          :disabled="disabled || revisionBusy"
          :show-context-menu="false"
          :show-pending-hint="false"
          :revision-author-role="revisionAuthorRole"
          empty-text="空"
        />
      </div>
      <div
        ref="editorRef"
        class="segment-row__editor"
        :class="[
          { 'is-focused': isFocused, 'has-revision': hasPendingRevision },
          revisionAuthorClass,
        ]"
        :contenteditable="!disabled"
        tabindex="0"
        data-testid="segment-target-editor"
        :data-revision-visible="hasPendingRevision ? 'true' : 'false'"
        data-segment-target="true"
        :data-sentence-id="segment.sentence_id"
        :aria-label="`translation for segment ${index + 1}`"
        spellcheck="false"
        @focus="handleFocus"
        @blur="handleBlur"
        @click="handleClick"
        @beforeinput="handleBeforeInput"
        @keydown="handleKeydown"
        @compositionstart="handleCompositionStart"
        @compositionend="handleCompositionEnd"
        @input="handleInput"
        @paste="handlePaste"
      />
      </div>
    </div>
  </article>
</template>

<style scoped>
.segment-row__cell--target.is-pending {
  box-shadow: inset 2px 0 0 rgba(0, 122, 204, 0.36);
}

.segment-row__editor-shell {
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 64px;
  display: contents;
  flex-direction: column;
  border: 1px solid transparent;
  border-radius: 5px;
  background:
    linear-gradient(
      0deg,
      var(--segment-editor-stripe, transparent),
      var(--segment-editor-stripe, transparent)
    );
  color: var(--text-primary);
  overflow: hidden;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease;
}

.segment-row__editor-shell.is-focused {
  border-color: var(--brand-700);
  background: var(--surface-panel);
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.12);
}

.segment-row__editor-shell.has-revision {
  border-color: rgba(0, 122, 204, 0.28);
}

.segment-row__editor-shell.is-disabled {
  background: var(--surface-muted);
  cursor: not-allowed;
  opacity: 0.7;
}

.segment-row__revision-inline {
  flex: 0 0 auto;
  width: 100%;
  max-height: 96px;
  display: block;
  padding: 6px 8px 4px;
  border-bottom: 1px dashed rgba(0, 122, 204, 0.24);
  background:
    linear-gradient(
      0deg,
      rgba(0, 122, 204, 0.035),
      rgba(0, 122, 204, 0.035)
    ),
    transparent;
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.45;
  outline: none;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.segment-row__revision-diff {
  min-height: 0;
  overflow: visible;
}

.segment-row__tag--revision {
  background: rgba(0, 122, 204, 0.12);
  color: #0070c0;
  font-size: 0;
}

.segment-row__tag--revision::after {
  content: '待审核';
  font-size: 11px;
}

.segment-row__tag--score {
  background: rgba(216, 183, 78, 0.18);
  color: #8a6700;
}

.segment-row__term-highlight {
  background: rgba(216, 183, 78, 0.28);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  font-weight: 500;
}

.segment-row__search-highlight {
  background: #fff176;
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  box-shadow: inset 0 0 0 1px rgba(138, 103, 0, 0.2);
  font-weight: 600;
}

/* 穿透 scoped 样式，让 innerHTML 插入的 mark 标签也能应用样式 */
.segment-row__editor :deep(.segment-row__term-highlight) {
  background: rgba(216, 183, 78, 0.28);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  font-weight: 500;
}

.segment-row__editor {
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 64px;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 5px;
  background:
    linear-gradient(
      0deg,
      var(--segment-editor-stripe, transparent),
      var(--segment-editor-stripe, transparent)
    );
  font-size: 13px;
  line-height: 1.45;
  color: var(--text-primary);
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  overflow: auto;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.segment-row__editor:focus,
.segment-row__editor.is-focused {
  border-color: var(--brand-700);
  background: var(--surface-panel);
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.12);
}

.segment-row__editor.has-revision {
  border-color: rgba(0, 122, 204, 0.32);
}

.segment-row__editor :deep(.segment-row__revision-segment) {
  white-space: pre-wrap;
}

.segment-row__editor :deep(.segment-row__revision-insert) {
  color: #0070c0;
  text-decoration: underline;
  text-decoration-color: rgba(0, 122, 204, 0.9);
  text-decoration-thickness: 1px;
  text-underline-offset: 2px;
}

.segment-row__editor :deep(.segment-row__revision-delete) {
  color: #d69a00;
  text-decoration: line-through;
  text-decoration-color: rgba(214, 154, 0, 0.95);
  text-decoration-thickness: 1px;
  user-select: text;
}

.segment-row__editor.is-revision-author-user :deep(.segment-row__revision-insert) {
  color: #2e7d32;
  text-decoration-color: rgba(46, 125, 50, 0.9);
}

.segment-row__editor.is-revision-author-user :deep(.segment-row__revision-delete) {
  color: #c62828;
  text-decoration-color: rgba(198, 40, 40, 0.9);
}

.segment-row__editor[contenteditable="false"] {
  background: var(--surface-muted);
  cursor: not-allowed;
  opacity: 0.7;
}

.segment-row__editor:empty::before {
  content: '';
  color: var(--text-placeholder);
}
</style>
