<script setup lang="ts">
import { computed, onMounted, ref, watch, nextTick } from 'vue'

import InteractiveDiffText from './InteractiveDiffText.vue'

import { getSegmentSourceMeta, getSegmentStatusMeta } from '../constants/status'
import type { Segment, SegmentRevisionEntry, TermEntryRecord } from '../types/api'
import { computeDiff } from '../utils/textDiff'
import type { TextFormat } from '../composables/useRichTextEditor'

const props = withDefaults(defineProps<{
  segment: Segment
  index: number
  active: boolean
  disabled?: boolean
  sourceEditing?: boolean
  selected?: boolean
  pendingRevision?: SegmentRevisionEntry | null
  revisionBusy?: boolean
  matchedTerms?: TermEntryRecord[]
  sourceSearchQuery?: string
  targetSearchQuery?: string
  showVisibleChars?: boolean
  pendingFormats?: Record<TextFormat, boolean> & { _overrideActive?: boolean }
}>(), {
  disabled: false,
  sourceEditing: false,
  selected: false,
  pendingRevision: null,
  revisionBusy: false,
  matchedTerms: () => [],
  sourceSearchQuery: '',
  targetSearchQuery: '',
  showVisibleChars: false,
  pendingFormats: () => ({
    bold: false,
    italic: false,
    underline: false,
    strikethrough: false,
    subscript: false,
    superscript: false,
    _overrideActive: false,
  }),
})

const emit = defineEmits<{
  update: [sentenceId: string, value: string, html?: string]
  updateSource: [sentenceId: string, value: string]
  focus: [sentenceId: string]
  activateTarget: [sentenceId: string]
  applyPartialRevision: [revisionId: string, newText: string]
  ctrlClick: [sentenceId: string, event: MouseEvent]
  toggleProjectSync: [sentenceId: string, disabled: boolean]
}>()

const editorRef = ref<HTMLDivElement | null>(null)
const sourceEditorRef = ref<HTMLDivElement | null>(null)
const isFocused = ref(false)
const isSourceFocused = ref(false)
const isComposing = ref(false)
const MAX_EDITOR_HISTORY_SIZE = 100
const EDITOR_HISTORY_GROUP_TIMEOUT_MS = 1200
const EDITOR_HISTORY_WORD_BOUNDARY_REGEXP = /[\s.,!?;:\uFF0C\u3002\uFF01\uFF1F\uFF1B\uFF1A]/

interface EditorHistorySnapshot {
  text: string
  html: string | null
  caretOffset: number
}

interface HistoryRecordOptions {
  force?: boolean
  inputType?: string
  data?: string | null
}

type HighlightKind = 'term' | 'search'
type HighlightPart = { text: string; highlight: boolean; kind?: HighlightKind }
type BasicFormatTag = 'b' | 'i' | 'u' | 's' | 'sub' | 'sup'

const BASIC_FORMAT_TAGS = ['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'del', 'sub', 'sup']
const BASIC_FORMAT_RENDER_ORDER: BasicFormatTag[] = ['b', 'i', 'u', 's', 'sub', 'sup']
const DROPPED_HTML_TAGS = new Set(['script', 'style', 'noscript', 'iframe', 'object', 'embed', 'link', 'meta'])

const undoStack = ref<EditorHistorySnapshot[]>([])
const redoStack = ref<EditorHistorySnapshot[]>([])
const isApplyingHistory = ref(false)
const compositionSnapshotRecorded = ref(false)
let lastHistorySnapshotAt = 0
let lastHistoryInputKind = ''
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
const showStatusTag = computed(() => {
  const status = props.segment.status || 'none'
  return status !== 'none' && status !== 'fuzzy'
})
const showSourceTag = computed(() => {
  if (props.segment.status === 'none' || props.segment.status === 'fuzzy') {
    return false
  }
  const source = props.segment.source || 'none'
  return source !== 'none' && source !== 'fuzzy'
})
const showProjectSyncToggle = computed(() => props.segment.source === 'project_sync' || Boolean(props.segment.project_sync_disabled))
const projectSyncToggleLabel = computed(() => (
  props.segment.project_sync_disabled ? '开启同步' : '关闭同步'
))
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

const sourceTextContent = computed(() => props.segment.display_text || props.segment.source_text || '')

const highlightedSourceText = computed(() => {
  const text = sourceTextContent.value
  return highlightSearchText(text, props.sourceSearchQuery) || highlightText(text, props.matchedTerms || [], 'source_text')
})

// 高亮译文中匹配的术语
const highlightedTargetText = computed(() => {
  const text = props.segment.target_text || ''
  return highlightSearchText(text, props.targetSearchQuery) || highlightText(text, props.matchedTerms || [], 'target_text')
})

// 生成带高亮的 HTML
const targetHtmlContent = computed(() => {
  // 如果有保存的格式化 HTML，优先使用
  if (props.segment.target_html) {
    return renderTargetHtmlWithHighlights(sanitizeHtml(props.segment.target_html))
  }

  return renderTargetWithSourceFormats(props.segment.target_text || '')
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

function renderHighlightPartsAsHtml(parts: HighlightPart[] | null, text: string): string {
  const sourceParts: HighlightPart[] = parts || [{ text, highlight: false }]
  return sourceParts
    .map((seg) =>
      seg.highlight
        ? `<mark class="${seg.kind === 'search' ? 'segment-row__search-highlight' : 'segment-row__term-highlight'}">${textToVisibleChars(seg.text)}</mark>`
        : textToVisibleChars(seg.text)
    )
    .join('')
}

function renderSourceTextWithHighlights(text: string): string {
  return renderHighlightPartsAsHtml(
    highlightSearchText(text, props.sourceSearchQuery)
      || highlightText(text, props.matchedTerms || [], 'source_text'),
    text,
  )
}

function hasSourceHighlights(): boolean {
  return Boolean(props.sourceSearchQuery.trim()) || (props.matchedTerms || []).some((term) => Boolean(term.source_text))
}

function renderTargetTextWithHighlights(text: string): string {
  const parts = highlightSearchText(text, props.targetSearchQuery)
    || highlightText(text, props.matchedTerms || [], 'target_text')
  if (!parts) {
    return textToVisibleChars(text)
  }
  return parts
    .map((seg) =>
      seg.highlight
        ? `<mark class="${seg.kind === 'search' ? 'segment-row__search-highlight' : 'segment-row__term-highlight'}">${textToVisibleChars(seg.text)}</mark>`
        : textToVisibleChars(seg.text)
    )
    .join('')
}

function hasTargetHighlights(): boolean {
  return Boolean(props.targetSearchQuery.trim()) || (props.matchedTerms || []).some((term) => Boolean(term.target_text))
}

function renderSourceHtmlWithHighlights(sourceHtml: string): string {
  if (typeof document === 'undefined') {
    return sourceHtml
  }

  const template = document.createElement('template')
  template.innerHTML = sourceHtml

  function processNode(node: Node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent || ''
      if (!text) return
      const wrapper = document.createElement('span')
      wrapper.innerHTML = hasSourceHighlights()
        ? renderSourceTextWithHighlights(text)
        : textToVisibleChars(text)
      const textNode = node as ChildNode
      textNode.replaceWith(...Array.from(wrapper.childNodes))
      return
    }

    if (node.nodeType !== Node.ELEMENT_NODE) {
      return
    }

    const element = node as HTMLElement
    if (
      element.matches('script, style')
      || element.classList.contains('doc-math')
      || element.classList.contains('segment-row__term-highlight')
      || element.classList.contains('segment-row__search-highlight')
    ) {
      return
    }

    Array.from(element.childNodes).forEach(processNode)
  }

  Array.from(template.content.childNodes).forEach(processNode)
  return template.innerHTML
}

function renderTargetHtmlWithHighlights(targetHtml: string): string {
  if (!hasTargetHighlights() || typeof document === 'undefined') {
    return targetHtml
  }

  const template = document.createElement('template')
  template.innerHTML = targetHtml

  function processNode(node: Node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent || ''
      if (!text) return
      const wrapper = document.createElement('span')
      wrapper.innerHTML = renderTargetTextWithHighlights(text)
      const textNode = node as ChildNode
      textNode.replaceWith(...Array.from(wrapper.childNodes))
      return
    }

    if (node.nodeType !== Node.ELEMENT_NODE) {
      return
    }

    const element = node as HTMLElement
    if (
      element.matches('script, style')
      || element.classList.contains('doc-math')
      || element.classList.contains('segment-row__term-highlight')
      || element.classList.contains('segment-row__search-highlight')
    ) {
      return
    }

    Array.from(element.childNodes).forEach(processNode)
  }

  Array.from(template.content.childNodes).forEach(processNode)
  return template.innerHTML
}

const sourceHtmlContent = computed(() => {
  if (props.segment.source_html) {
    return renderSourceHtmlWithHighlights(sanitizeHtml(props.segment.source_html))
  }
  return renderHighlightPartsAsHtml(highlightedSourceText.value, sourceTextContent.value)
})

/**
 * 将文本转换为显示标记模式（显示空格、制表符、换行符）
 */
function textToVisibleChars(text: string): string {
  const escaped = escapeHtml(text)
  if (!props.showVisibleChars) return escaped
  return escaped
    .replace(/ /g, '<span class="visible-char visible-char--space" contenteditable="false">·</span>')
    .replace(/\t/g, '<span class="visible-char visible-char--tab" contenteditable="false">→</span>')
    .replace(/\n/g, '<span class="visible-char visible-char--newline" contenteditable="false">¶</span>\n')
}

function renderTargetTextHtml(text: string): string {
  return renderTargetTextWithHighlights(text)
}

// 保存和恢复光标位置
function renderTargetWithSourceFormats(text: string): string {
  const targetHtml = renderTargetTextHtml(text)
  if (!text || !props.segment.source_html) {
    return targetHtml
  }
  const sourceFormatTags = getPrimarySourceFormatTags(props.segment.source_html)
  return wrapWithBasicFormats(targetHtml, sourceFormatTags)
}

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
  if (
    node.nodeType === Node.ELEMENT_NODE
    && (node as HTMLElement).classList.contains('visible-char')
  ) {
    return 1
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
  if (
    node.nodeType === Node.ELEMENT_NODE
    && (node as HTMLElement).classList.contains('visible-char')
  ) {
    const el = node as HTMLElement
    if (el.classList.contains('visible-char--space')) return ' '
    if (el.classList.contains('visible-char--tab')) return '\t'
    if (el.classList.contains('visible-char--newline')) return '\n'
  }
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent || ''
  }
  if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR') {
    return '\n'
  }
  return Array.from(node.childNodes).map(serializeEditorContent).join('')
}

/**
 * 序列化编辑器内容，保留格式标签
 */
function serializeEditorContentWithFormat(node: Node): string {
  if (isRevisionDeleteNode(node)) {
    return ''
  }
  if (
    node.nodeType === Node.ELEMENT_NODE
    && (node as HTMLElement).classList.contains('visible-char')
  ) {
    return escapeHtml(serializeEditorContent(node))
  }
  if (node.nodeType === Node.TEXT_NODE) {
    return escapeHtml(node.textContent || '')
  }
  if (node.nodeType === Node.ELEMENT_NODE) {
    const el = node as HTMLElement
    const tagName = el.tagName.toLowerCase()

    // BR 标签转换为换行
    if (tagName === 'br') {
      return '\n'
    }

    // 处理子节点
    const childContent = Array.from(el.childNodes)
      .map(child => serializeEditorContentWithFormat(child))
      .join('')

    // 保留格式标签
    const formatTags = ['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'del', 'sub', 'sup']
    if (formatTags.includes(tagName)) {
      const normalizedTag = normalizeTagName(tagName)
      return `<${normalizedTag}>${childContent}</${normalizedTag}>`
    }

    return childContent
  }
  return ''
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

function getCurrentEditorHtml(): string | null {
  if (!editorRef.value) {
    return props.segment.target_html || null
  }
  const html = serializeEditorContentWithFormat(editorRef.value)
  return /<(b|strong|i|em|u|s|strike|del|sub|sup)>/i.test(html) ? html : null
}

function getCurrentEditorSnapshot(): EditorHistorySnapshot {
  const text = getCurrentEditorText()
  return {
    text,
    html: getCurrentEditorHtml(),
    caretOffset: editorRef.value
      ? saveSerializableCaretPosition(editorRef.value)
      : text.length,
  }
}

function pushHistorySnapshot(stack: EditorHistorySnapshot[], snapshot: EditorHistorySnapshot) {
  const lastSnapshot = stack[stack.length - 1]
  if (lastSnapshot?.text === snapshot.text && lastSnapshot.html === snapshot.html) {
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
  resetHistoryGroup()
}

function resetHistoryGroup() {
  lastHistorySnapshotAt = 0
  lastHistoryInputKind = ''
}

function getHistoryInputKind(inputType = '') {
  if (inputType.startsWith('delete')) {
    return 'delete'
  }
  if (inputType === 'insertFromPaste') {
    return 'paste'
  }
  if (inputType === 'insertParagraph' || inputType === 'insertLineBreak') {
    return 'line-break'
  }
  if (inputType.startsWith('format')) {
    return 'format'
  }
  return inputType || 'edit'
}

function hasExpandedEditorSelection() {
  const editor = editorRef.value
  const selection = window.getSelection()
  if (!editor || !selection || selection.rangeCount === 0 || selection.isCollapsed) {
    return false
  }
  const range = selection.getRangeAt(0)
  return editor.contains(range.commonAncestorContainer)
}

function shouldStartNewHistoryGroup(options: HistoryRecordOptions) {
  if (options.force || hasExpandedEditorSelection()) {
    return true
  }

  const inputKind = getHistoryInputKind(options.inputType)
  const now = Date.now()

  if (!lastHistorySnapshotAt || now - lastHistorySnapshotAt > EDITOR_HISTORY_GROUP_TIMEOUT_MS) {
    return true
  }
  if (inputKind !== lastHistoryInputKind) {
    return true
  }
  if (inputKind === 'line-break' || inputKind === 'paste' || inputKind === 'format') {
    return true
  }
  if (inputKind === 'insertText' && EDITOR_HISTORY_WORD_BOUNDARY_REGEXP.test(options.data || '')) {
    return true
  }

  return false
}

function recordUndoSnapshot(clearRedo = true, options: HistoryRecordOptions = {}) {
  if (props.disabled || !editorRef.value || isApplyingHistory.value || isComposing.value) {
    return
  }
  if (!shouldStartNewHistoryGroup(options)) {
    return
  }
  pushHistorySnapshot(undoStack.value, getCurrentEditorSnapshot())
  lastHistorySnapshotAt = Date.now()
  lastHistoryInputKind = getHistoryInputKind(options.inputType)
  if (clearRedo) {
    redoStack.value = []
  }
}

function applyHistorySnapshot(snapshot: EditorHistorySnapshot) {
  isApplyingHistory.value = true
  emit('update', props.segment.sentence_id, snapshot.text, snapshot.html || undefined)
  void nextTick(() => {
    if (editorRef.value) {
      editorRef.value.innerHTML = editorHtmlContent.value
      editorRef.value.focus({ preventScroll: true })
      restoreSerializableCaretPosition(editorRef.value, snapshot.caretOffset)
    }
    isApplyingHistory.value = false
    resetHistoryGroup()
  })
}

function undoEditorChange() {
  const targetSnapshot = undoStack.value.pop()
  if (!targetSnapshot) {
    return false
  }
  pushHistorySnapshot(redoStack.value, getCurrentEditorSnapshot())
  resetHistoryGroup()
  applyHistorySnapshot(targetSnapshot)
  return true
}

function redoEditorChange() {
  const targetSnapshot = redoStack.value.pop()
  if (!targetSnapshot) {
    return false
  }
  pushHistorySnapshot(undoStack.value, getCurrentEditorSnapshot())
  resetHistoryGroup()
  applyHistorySnapshot(targetSnapshot)
  return true
}

function handleFocus() {
  isFocused.value = true
  emit('focus', props.segment.sentence_id)
}

function handleBlur() {
  isFocused.value = false
  resetHistoryGroup()
  void nextTick(() => syncEditorHtmlFromState(false))
}

function handleClick(event?: MouseEvent) {
  resetHistoryGroup()
  if (event && (event.ctrlKey || event.metaKey)) {
    emit('ctrlClick', props.segment.sentence_id, event)
    return
  }
  emit('activateTarget', props.segment.sentence_id)
}

function handleProjectSyncToggle() {
  emit('toggleProjectSync', props.segment.sentence_id, !props.segment.project_sync_disabled)
}

function handleSourceFocus() {
  isSourceFocused.value = true
}

function handleSourceBlur() {
  isSourceFocused.value = false
}

function handleSourceInput() {
  if (!sourceEditorRef.value) return
  if (!props.sourceEditing) {
    // 非编辑模式下恢复原文内容
    syncSourceEditorFromState(true)
    return
  }
  const text = sourceEditorRef.value.textContent || ''
  emit('updateSource', props.segment.sentence_id, text)
}

function handleSourceBeforeInput(event: Event) {
  if (!props.sourceEditing) {
    event.preventDefault()
  }
}

function handleSourceKeydown(event: KeyboardEvent) {
  if (!props.sourceEditing) {
    // 允许光标移动键和选择键
    const allowedKeys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']
    if (!allowedKeys.includes(event.key) && !event.ctrlKey && !event.metaKey) {
      event.preventDefault()
    }
  }
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

  recordUndoSnapshot(true, {
    inputType: inputEvent.inputType,
    data: inputEvent.data,
    force: inputEvent.inputType !== 'insertText' && inputEvent.inputType !== 'insertCompositionText',
  })

  if (inputEvent.inputType !== 'insertText' && inputEvent.inputType !== 'insertCompositionText') {
    return
  }
  if (!props.pendingFormats?._overrideActive) {
    return
  }

  const data = inputEvent.data
  if (!data) return

  inputEvent.preventDefault()
  const wrappedHtml = wrapTextWithFormats(data)
  document.execCommand('insertHTML', false, wrappedHtml)
  handleInput()
}

function handleKeydown(event: KeyboardEvent) {
  if (props.disabled || isApplyingHistory.value || event.altKey) {
    return
  }

  const usesShortcutModifier = event.ctrlKey || event.metaKey
  if (!usesShortcutModifier && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) {
    resetHistoryGroup()
    return
  }

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

  // 检查是否有格式标签
  const cleanHtml = serializeEditorContentWithFormat(editorRef.value)
  const hasFormatTags = /<(b|strong|i|em|u|s|strike|del|sub|sup)>/i.test(cleanHtml)

  // 获取纯文本内容用于保存
  const text = serializeEditorContent(editorRef.value)

  // 如果有格式标签，同时传递 HTML
  if (hasFormatTags) {
    // 清理 HTML，只保留格式标签
    emit('update', props.segment.sentence_id, text, cleanHtml)
    return
  }

  // 没有格式标签，只传递纯文本
  emit('update', props.segment.sentence_id, text)
}

/**
 * 检查是否有待应用的格式
 */
function hasPendingFormats(): boolean {
  if (!props.pendingFormats) return false
  return Object.entries(props.pendingFormats)
    .filter(([key]) => key !== '_overrideActive')
    .some(([, value]) => value)
}

/**
 * 根据待应用的格式包装文本
 */
function wrapTextWithFormats(text: string): string {
  let result = escapeHtml(text)

  // 按顺序应用格式标签
  if (props.pendingFormats.subscript) {
    result = `<sub>${result}</sub>`
  }
  if (props.pendingFormats.superscript) {
    result = `<sup>${result}</sup>`
  }
  if (props.pendingFormats.strikethrough) {
    result = `<s>${result}</s>`
  }
  if (props.pendingFormats.underline) {
    result = `<u>${result}</u>`
  }
  if (props.pendingFormats.italic) {
    result = `<i>${result}</i>`
  }
  if (props.pendingFormats.bold) {
    result = `<b>${result}</b>`
  }

  return result
}

// 监听外部数据变化，更新编辑器内容
function handleCompositionStart() {
  if (!compositionSnapshotRecorded.value) {
    recordUndoSnapshot(true, { force: true, inputType: 'insertCompositionText' })
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
  recordUndoSnapshot(true, { force: true, inputType: 'insertFromPaste' })
  // 优先获取 HTML 格式，保留格式标签
  const html = event.clipboardData?.getData('text/html') || ''
  const text = event.clipboardData?.getData('text/plain') || ''

  // 如果有待应用的格式且粘贴的是纯文本，应用格式
  if (!html && text && hasPendingFormats()) {
    const wrappedHtml = wrapTextWithFormats(text)
    document.execCommand('insertHTML', false, wrappedHtml)
    handleInput()
    return
  }

  if (html) {
    // 清理 HTML，只保留允许的格式标签
    const cleanHtml = sanitizeHtml(html, { dropStructuralWhitespace: true })
    if (hasSerializableFormatTags(cleanHtml)) {
      document.execCommand('insertHTML', false, cleanHtml)
    } else {
      document.execCommand('insertText', false, text)
    }
  } else {
    document.execCommand('insertText', false, text)
  }
  handleInput()
}

function hasSerializableFormatTags(html: string): boolean {
  return /<(b|i|u|s|sub|sup)>/i.test(html)
}

/**
 * 清理 HTML，只保留允许的格式标签
 */
function sanitizeHtml(
  html: string,
  options: { dropStructuralWhitespace?: boolean } = {},
): string {
  if (typeof document === 'undefined') {
    return escapeHtml(html)
  }

  const tempDiv = document.createElement('div')
  tempDiv.innerHTML = html

  // 递归处理节点
  function processNode(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent || ''
      if (options.dropStructuralWhitespace && /^[\t\n\r ]+$/.test(text) && /[\t\n\r]/.test(text)) {
        return ''
      }
      return escapeHtml(text)
    }

    if (node.nodeType === Node.ELEMENT_NODE) {
      const el = node as HTMLElement
      const tagName = el.tagName.toLowerCase()

      if (DROPPED_HTML_TAGS.has(tagName)) {
        return ''
      }

      if (tagName === 'br') {
        return '\n'
      }

      // 处理子节点
      const childContent = Array.from(el.childNodes)
        .map(child => processNode(child))
        .join('')

      const formatTags: BasicFormatTag[] = []
      const normalizedBasicTag = normalizeBasicFormatTag(tagName)
      if (normalizedBasicTag) {
        formatTags.push(normalizedBasicTag)
      }
      formatTags.push(...getStyleFormatTags(el))
      if (formatTags.length > 0) {
        return wrapWithBasicFormats(childContent, formatTags)
      }

      // 否则只返回内容
      return childContent
    }

    return ''
  }

  return processNode(tempDiv)
}

/**
 * 只把句段编辑中允许渲染的基础格式规范化为内部标签名。
 */
function getPrimarySourceFormatTags(sourceHtml: string): BasicFormatTag[] {
  if (typeof document === 'undefined') {
    return []
  }

  const template = document.createElement('template')
  template.innerHTML = sanitizeHtml(sourceHtml)
  const textRuns: Array<{ text: string; tags: BasicFormatTag[] }> = []

  function walk(node: Node, inheritedTags: BasicFormatTag[]) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent || ''
      if (text) {
        textRuns.push({ text, tags: normalizeFormatTagList(inheritedTags) })
      }
      return
    }

    if (node.nodeType !== Node.ELEMENT_NODE) {
      return
    }

    const element = node as HTMLElement
    const nextTags = [...inheritedTags]
    const tag = normalizeBasicFormatTag(element.tagName.toLowerCase())
    if (tag) {
      nextTags.push(tag)
    }
    nextTags.push(...getStyleFormatTags(element))
    Array.from(element.childNodes).forEach((child) => walk(child, nextTags))
  }

  Array.from(template.content.childNodes).forEach((child) => walk(child, []))
  const firstNonEmptyRun = textRuns.find((run) => run.text.trim())
  if (firstNonEmptyRun?.tags.length) {
    return firstNonEmptyRun.tags
  }
  return textRuns.find((run) => run.tags.length)?.tags || []
}

function normalizeFormatTagList(tags: BasicFormatTag[]): BasicFormatTag[] {
  return BASIC_FORMAT_RENDER_ORDER.filter((tag) => tags.includes(tag))
}

function normalizeBasicFormatTag(tag: string): BasicFormatTag | null {
  if (!BASIC_FORMAT_TAGS.includes(tag)) {
    return null
  }
  const normalizedTag = normalizeTagName(tag)
  return BASIC_FORMAT_RENDER_ORDER.includes(normalizedTag as BasicFormatTag)
    ? normalizedTag as BasicFormatTag
    : null
}

function getStyleFormatTags(element: HTMLElement): BasicFormatTag[] {
  const tags: BasicFormatTag[] = []
  const style = element.style
  const fontWeight = style.fontWeight.trim().toLowerCase()
  const numericWeight = Number.parseInt(fontWeight, 10)

  if (fontWeight === 'bold' || fontWeight === 'bolder' || numericWeight >= 600) {
    tags.push('b')
  }

  const fontStyle = style.fontStyle.trim().toLowerCase()
  if (fontStyle.includes('italic') || fontStyle.includes('oblique')) {
    tags.push('i')
  }

  const textDecoration = `${style.textDecorationLine} ${style.textDecoration}`.toLowerCase()
  if (textDecoration.includes('underline')) {
    tags.push('u')
  }
  if (textDecoration.includes('line-through')) {
    tags.push('s')
  }

  const verticalAlign = style.verticalAlign.trim().toLowerCase()
  if (verticalAlign === 'sub') {
    tags.push('sub')
  }
  if (verticalAlign === 'super') {
    tags.push('sup')
  }

  return tags
}

function wrapWithBasicFormats(content: string, tags: BasicFormatTag[]): string {
  if (!content || tags.length === 0) {
    return content
  }
  return BASIC_FORMAT_RENDER_ORDER
    .filter((tag) => tags.includes(tag))
    .reduceRight((inner, tag) => `<${tag}>${inner}</${tag}>`, content)
}

function normalizeTagName(tag: string): string {
  const map: Record<string, string> = {
    strong: 'b',
    em: 'i',
    strike: 's',
    del: 's',
  }
  return map[tag] || tag
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

function syncSourceEditorFromState(preserveCaret: boolean) {
  const editor = sourceEditorRef.value
  if (!editor) {
    return
  }

  const caretPos = preserveCaret ? saveCaretPosition(editor) : 0
  if (props.sourceEditing) {
    const nextText = sourceTextContent.value
    if (editor.textContent !== nextText) {
      editor.textContent = nextText
    }
  } else {
    const nextHtml = sourceHtmlContent.value
    if (editor.innerHTML !== nextHtml) {
      editor.innerHTML = nextHtml
    }
  }
  if (preserveCaret && isSourceFocused.value) {
    restoreCaretPosition(editor, caretPos)
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
    if (isFocused.value && !isApplyingHistory.value) {
      return
    }
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

// 将光标移动到元素末尾
function moveCursorToEnd(el: HTMLElement) {
  const range = document.createRange()
  const selection = window.getSelection()
  if (!selection) return

  range.selectNodeContents(el)
  range.collapse(false) // false 表示折叠到末尾
  selection.removeAllRanges()
  selection.addRange(range)
}

// 原文编辑器的当前文本（用于避免响应式干扰）
const sourceEditText = ref('')

// 监听 active 变化，初始化原文编辑器内容（允许放置光标）
watch(
  () => props.active,
  (isActive) => {
    if (isActive) {
      sourceEditText.value = sourceTextContent.value
      nextTick(() => {
        syncSourceEditorFromState(false)
      })
    }
  },
  { immediate: true }
)

// 进入原文编辑模式时聚焦
watch(
  () => props.sourceEditing && props.active,
  (shouldEdit) => {
    nextTick(() => {
      syncSourceEditorFromState(false)
      if (shouldEdit) {
        if (sourceEditorRef.value) {
          sourceEditorRef.value.focus()
          moveCursorToEnd(sourceEditorRef.value)
        }
      }
    })
  },
)

watch(
  sourceHtmlContent,
  () => {
    if (props.active) {
      nextTick(() => {
        syncSourceEditorFromState(isSourceFocused.value)
      })
    }
  },
  { flush: 'post' },
)

</script>

<template>
  <article
    class="segment-row"
    :class="[statusClass, parityClass, { 'is-active': active, 'is-selected': selected, 'has-pending-revision': hasPendingRevision, 'is-empty-target': isEmptyTarget }]"
    :id="`segment-${segment.sentence_id}`"
    data-testid="segment-row"
    :data-sentence-id="segment.sentence_id"
    :data-has-pending-revision="hasPendingRevision ? 'true' : 'false'"
    role="group"
    :aria-label="`segment ${index + 1}`"
  >
    <div class="segment-row__meta">
      <span class="segment-row__index">#{{ index + 1 }}</span>
      <span v-if="showStatusTag" class="segment-row__tag segment-row__tag--status">{{ statusMeta.label }}</span>
      <span v-if="showSourceTag" class="segment-row__tag is-muted" :class="sourceClass" :title="sourceTitle">{{ sourceLabel }}</span>
      <button
        v-if="showProjectSyncToggle"
        class="segment-row__sync-toggle"
        type="button"
        :aria-pressed="!segment.project_sync_disabled"
        @click.stop="handleProjectSyncToggle"
      >
        {{ projectSyncToggleLabel }}
      </button>
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
      <div
        v-if="active"
        ref="sourceEditorRef"
        class="segment-row__source-editor"
        :class="{ 'is-focused': isSourceFocused, 'is-readonly': !sourceEditing }"
        :contenteditable="true"
        tabindex="0"
        spellcheck="false"
        @focus="handleSourceFocus"
        @blur="handleSourceBlur"
        @input="handleSourceInput"
        @keydown="handleSourceKeydown"
        @beforeinput="handleSourceBeforeInput"
      ></div>
      <div v-else class="segment-row__text" v-html="sourceHtmlContent"></div>
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
        @keydown="handleKeydown"
        @compositionstart="handleCompositionStart"
        @compositionend="handleCompositionEnd"
        @beforeinput="handleBeforeInput"
        @input="handleInput"
        @paste="handlePaste"
      />
      </div>
    </div>
  </article>
</template>

<style scoped>
.segment-row.is-selected {
  background-color: rgba(13, 122, 104, 0.12);
  outline: 2px solid rgba(13, 122, 104, 0.45);
  outline-offset: -2px;
  border-radius: 4px;
}

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
  font-size: 15px;
  line-height: 1.58;
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

.segment-row__sync-toggle {
  min-height: 24px;
  padding: 2px 8px;
  border: 1px solid rgba(34, 127, 88, 0.28);
  border-radius: 6px;
  background: rgba(34, 127, 88, 0.08);
  color: #146c49;
  font: inherit;
  font-size: 12px;
  line-height: 1.2;
  cursor: pointer;
}

.segment-row__sync-toggle:hover {
  background: rgba(34, 127, 88, 0.14);
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
.segment-row__text :deep(.segment-row__term-highlight),
.segment-row__source-editor :deep(.segment-row__term-highlight) {
  background: rgba(216, 183, 78, 0.28);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  font-weight: 500;
}

.segment-row__text :deep(.segment-row__search-highlight),
.segment-row__source-editor :deep(.segment-row__search-highlight) {
  background: #fff176;
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  box-shadow: inset 0 0 0 1px rgba(138, 103, 0, 0.2);
  font-weight: 600;
}

.segment-row__editor :deep(.segment-row__term-highlight) {
  background: rgba(216, 183, 78, 0.28);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  font-weight: 500;
}

.segment-row__editor :deep(.segment-row__search-highlight) {
  background: #fff176;
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  box-shadow: inset 0 0 0 1px rgba(138, 103, 0, 0.2);
  font-weight: 600;
}

.segment-row__editor {
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 76px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 5px;
  background:
    linear-gradient(
      0deg,
      var(--segment-editor-stripe, transparent),
      var(--segment-editor-stripe, transparent)
    );
  font-size: 15px;
  line-height: 1.58;
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

.segment-row__source-editor {
  flex: 1 1 auto;
  width: 100%;
  min-height: 64px;
  padding: 6px 8px;
  border: 1px solid var(--brand-700, #0d7a68);
  border-radius: 5px;
  background: var(--surface-panel, #fff);
  font-size: 13px;
  line-height: 1.45;
  color: var(--text-primary);
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  overflow: auto;
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.12);
}

.segment-row__source-editor.is-focused {
  border-color: var(--brand-700, #0d7a68);
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.18);
}

.segment-row__source-editor.is-readonly {
  border-color: var(--border-muted, #e2e8f0);
  background: var(--surface-panel, #fff);
  box-shadow: none;
  cursor: text;
}

.segment-row__source-editor.is-readonly.is-focused {
  border-color: var(--brand-400, #5bb5a6);
  box-shadow: 0 0 0 2px rgba(13, 122, 104, 0.08);
}

/* 显示标记样式 */
.segment-row__text :deep(.visible-char),
.segment-row__source-editor :deep(.visible-char),
.segment-row__editor :deep(.visible-char) {
  color: #64748b;
  font-size: 0.85em;
  font-weight: 700;
  user-select: none;
  pointer-events: none;
}

.segment-row__text :deep(.visible-char--space),
.segment-row__source-editor :deep(.visible-char--space),
.segment-row__editor :deep(.visible-char--space) {
  color: #6b7280;
}

.segment-row__text :deep(.visible-char--tab),
.segment-row__source-editor :deep(.visible-char--tab),
.segment-row__editor :deep(.visible-char--tab) {
  color: #3b82f6;
}

.segment-row__text :deep(.visible-char--newline),
.segment-row__source-editor :deep(.visible-char--newline),
.segment-row__editor :deep(.visible-char--newline) {
  color: #ef4444;
}

/* 富文本格式样式 */
.segment-row__editor :deep(b),
.segment-row__editor :deep(strong) {
  font-weight: 700;
}

.segment-row__editor :deep(i),
.segment-row__editor :deep(em) {
  font-style: italic;
}

.segment-row__editor :deep(u) {
  text-decoration: underline;
}

.segment-row__editor :deep(s),
.segment-row__editor :deep(strike),
.segment-row__editor :deep(del) {
  text-decoration: line-through;
}

.segment-row__editor :deep(sub) {
  vertical-align: sub;
  font-size: 0.75em;
}

.segment-row__editor :deep(sup) {
  vertical-align: super;
  font-size: 0.75em;
}
</style>
