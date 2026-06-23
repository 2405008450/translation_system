<script setup lang="ts">
import { Link2, Link2Off } from 'lucide-vue-next'
import { computed, onMounted, ref, watch, nextTick } from 'vue'

import InteractiveDiffText from './InteractiveDiffText.vue'

import { getLLMModelShortLabel } from '../constants/llm'
import { getSegmentSourceMeta, getSegmentStatusMeta } from '../constants/status'
import type { RevisionDisplaySettings, Segment, SegmentQAIssue, SegmentRevisionEntry, TermEntryRecord } from '../types/api'
import { findTermTextRanges } from '../utils/termMatching'
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
  revisionSettings?: RevisionDisplaySettings | null
  revisionBusy?: boolean
  matchedTerms?: TermEntryRecord[]
  qaIssues?: SegmentQAIssue[]
  sourceSearchQuery?: string
  targetSearchQuery?: string
  searchCaseSensitive?: boolean
  showVisibleChars?: boolean
  pendingFormats?: Record<TextFormat, boolean> & { _overrideActive?: boolean }
  /** 句段对外标识：单文件模式即 sentence_id；合并模式为复合键 ${file_record_id}:${sentence_id} */
  segmentKey?: string
}>(), {
  disabled: false,
  sourceEditing: false,
  selected: false,
  pendingRevision: null,
  revisionSettings: null,
  revisionBusy: false,
  matchedTerms: () => [],
  qaIssues: () => [],
  sourceSearchQuery: '',
  targetSearchQuery: '',
  searchCaseSensitive: false,
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
  segmentKey: '',
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

// 对外标识：合并视图使用复合键，单文件回退为 sentence_id
const segmentKey = computed(() => props.segmentKey || props.segment.sentence_id)
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

interface EditorUndoBoundaryOptions {
  inputType?: string
  data?: string | null
  preserveNextTargetSync?: boolean
}

interface CommittedEditorContent {
  sentenceId: string
  text: string
  html: string | null
}

interface EditorTextRange {
  start: number
  end: number
}

type HighlightKind = 'term' | 'search' | 'qa'
type HighlightPart = { text: string; highlight: boolean; kind?: HighlightKind; title?: string }
type BasicFormatTag = 'b' | 'i' | 'u' | 's' | 'sub' | 'sup'

const BASIC_FORMAT_TAGS = ['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'del', 'sub', 'sup']
const BASIC_FORMAT_RENDER_ORDER: BasicFormatTag[] = ['b', 'i', 'u', 's', 'sub', 'sup']
const DROPPED_HTML_TAGS = new Set(['script', 'style', 'noscript', 'iframe', 'object', 'embed', 'link', 'meta'])

const undoStack = ref<EditorHistorySnapshot[]>([])
const redoStack = ref<EditorHistorySnapshot[]>([])
const isApplyingHistory = ref(false)
const compositionSnapshotRecorded = ref(false)
const lastTargetSelectionRange = ref<EditorTextRange | null>(null)
let lastHistorySnapshotAt = 0
let lastHistoryInputKind = ''
let preserveNextTargetSync = false
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
    const modelId = props.segment.llm_model?.trim()
    return modelId ? getLLMModelShortLabel(modelId) : sourceMeta.value.label
  }
  return sourceMeta.value.label
})
const compactSourceLabel = computed(() => (
  props.segment.source === 'project_sync' ? '同步' : sourceLabel.value
))
const workflowLabel = computed(() => props.segment.workflow_step_name || '翻译')
const showStatusTag = computed(() => {
  const status = props.segment.status || 'none'
  return status !== 'none' && status !== 'fuzzy'
})
const showSourceTag = computed(() => {
  const source = props.segment.source || 'none'
  if (source === 'none' || source === 'fuzzy') {
    return false
  }
  if (source === 'llm') {
    return !isEmptyTarget.value
  }
  return props.segment.status !== 'none' && props.segment.status !== 'fuzzy'
})
const showProjectSyncToggle = computed(() => true)
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
const revisionInsertColor = computed(() => {
  const settings = props.revisionSettings
  const authorId = props.pendingRevision?.author?.id || ''
  return settings?.author_colors?.[authorId]?.insert || settings?.default_insert_color || '#2563eb'
})
const revisionDeleteColor = computed(() => {
  const settings = props.revisionSettings
  const authorId = props.pendingRevision?.author?.id || ''
  return settings?.author_colors?.[authorId]?.delete || settings?.default_delete_color || '#dc2626'
})
const revisionColorStyle = computed(() => (
  hasPendingRevision.value
    ? {
      '--rev-insert-color': revisionInsertColor.value,
      '--rev-delete-color': revisionDeleteColor.value,
    }
    : {}
))
const revisionTooltip = computed(() => {
  if (!props.pendingRevision || props.revisionSettings?.show_author_time === false) {
    return ''
  }
  const author = props.pendingRevision.author
  const authorName = author?.nickname || author?.username || '未知用户'
  const createdAt = props.pendingRevision.created_at
    ? new Date(props.pendingRevision.created_at).toLocaleString('zh-CN', { hour12: false })
    : ''
  return createdAt ? `${authorName} · ${createdAt}` : authorName
})
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

  const matches: Array<{ start: number; end: number }> = []

  for (const term of sortedTerms) {
    const termText = term[field]
    if (!termText) continue
    for (const range of findTermTextRanges(text, termText)) {
      // 检查是否与已有匹配重叠
      const overlaps = matches.some(
        (m) => !(range.end <= m.start || range.start >= m.end)
      )
      if (!overlaps) {
        matches.push(range)
      }
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

function resolveSearchKeyword(value: string) {
  const normalized = value.replace(/\s+/g, ' ')
  const trimmed = normalized.trim()
  return trimmed || (normalized ? ' ' : '')
}

function highlightSearchText(text: string, keyword: string, caseSensitive = false): HighlightPart[] | null {
  const query = resolveSearchKeyword(keyword)
  if (!text || !query) {
    return null
  }

  const regexp = new RegExp(escapeRegExp(query), caseSensitive ? 'g' : 'gi')
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

const automaticNumberingTitle = 'Word 自动编号，导出时会自动生成，译文无需输入编号'
const automaticNumberingText = computed(() => (props.segment.automatic_numbering_text || '').trim())
const hasAutomaticNumbering = computed(() => automaticNumberingText.value.length > 0)
const targetAutomaticNumberingText = computed(() => (
  props.segment.target_automatic_numbering_text || automaticNumberingText.value
).trim())
const hasTargetAutomaticNumbering = computed(() => targetAutomaticNumberingText.value.length > 0)
const sourceTextContent = computed(() => {
  if (hasAutomaticNumbering.value) {
    return props.segment.source_body_text || props.segment.source_text || ''
  }
  return props.segment.display_text || props.segment.source_text || ''
})

const highlightedSourceText = computed(() => {
  const text = sourceTextContent.value
  return highlightSearchText(text, props.sourceSearchQuery, props.searchCaseSensitive) || highlightText(text, props.matchedTerms || [], 'source_text')
})

// 高亮译文中匹配的术语
const highlightedTargetText = computed(() => {
  const text = props.segment.target_text || ''
  return highlightSearchText(text, props.targetSearchQuery, props.searchCaseSensitive) || highlightText(text, props.matchedTerms || [], 'target_text')
})

const activeQAIssues = computed(() => {
  const textLength = (props.segment.target_text || '').length
  return (props.qaIssues || props.segment.qa_issues || [])
    .filter((issue) => issue.status === 'open' && issue.length > 0 && issue.offset < textLength)
    .map((issue) => ({
      ...issue,
      offset: Math.max(0, issue.offset),
      length: Math.min(issue.length, Math.max(0, textLength - Math.max(0, issue.offset))),
    }))
    .filter((issue) => issue.length > 0)
    .sort((a, b) => a.offset - b.offset || b.length - a.length)
})

function highlightQAText(text: string, issues: SegmentQAIssue[]): HighlightPart[] | null {
  if (!text || issues.length === 0) {
    return null
  }

  const ranges: Array<{ start: number; end: number; title: string }> = []
  for (const issue of issues) {
    const start = Math.max(0, issue.offset)
    const end = Math.min(text.length, start + Math.max(0, issue.length))
    if (end <= start) continue
    const overlaps = ranges.some((range) => !(end <= range.start || start >= range.end))
    if (overlaps) continue
    ranges.push({
      start,
      end,
      title: issue.short_message || issue.message || '拼写/语法问题',
    })
  }

  if (ranges.length === 0) {
    return null
  }
  ranges.sort((a, b) => a.start - b.start)

  const parts: HighlightPart[] = []
  let lastEnd = 0
  for (const range of ranges) {
    if (range.start > lastEnd) {
      parts.push({ text: text.slice(lastEnd, range.start), highlight: false })
    }
    parts.push({
      text: text.slice(range.start, range.end),
      highlight: true,
      kind: 'qa',
      title: range.title,
    })
    lastEnd = range.end
  }
  if (lastEnd < text.length) {
    parts.push({ text: text.slice(lastEnd), highlight: false })
  }
  return parts
}

// 生成带高亮的 HTML
const targetHtmlContent = computed(() => {
  // 如果有保存的格式化 HTML，优先使用
  if (props.segment.target_html && !hasAutomaticNumbering.value) {
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
      const titleAttr = revisionTooltip.value ? ` title="${escapeHtml(revisionTooltip.value)}"` : ''
      return [
        `<span class="segment-row__revision-segment segment-row__revision-${segment.type}"`,
        ` data-revision-type="${segment.type}"`,
        ` data-testid="segment-revision-${segment.type}"`,
        titleAttr,
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

function escapeAttribute(text: string): string {
  return escapeHtml(text).replace(/'/g, '&#39;')
}

function renderHighlightPartsAsHtml(parts: HighlightPart[] | null, text: string): string {
  const sourceParts: HighlightPart[] = parts || [{ text, highlight: false }]
  return sourceParts
    .map((seg) =>
      seg.highlight
        ? seg.kind === 'qa'
          ? `<span class="segment-row__qa-highlight" title="${escapeAttribute(seg.title || '拼写/语法问题')}">${textToVisibleChars(seg.text)}</span>`
          : `<mark class="${seg.kind === 'search' ? 'segment-row__search-highlight' : 'segment-row__term-highlight'}">${textToVisibleChars(seg.text)}</mark>`
        : textToVisibleChars(seg.text)
    )
    .join('')
}

function renderSourceTextWithHighlights(text: string): string {
  return renderHighlightPartsAsHtml(
    highlightSearchText(text, props.sourceSearchQuery, props.searchCaseSensitive)
      || highlightText(text, props.matchedTerms || [], 'source_text'),
    text,
  )
}

function hasSourceHighlights(): boolean {
  return Boolean(resolveSearchKeyword(props.sourceSearchQuery)) || (props.matchedTerms || []).some((term) => Boolean(term.source_text))
}

function renderTargetTextWithHighlights(text: string): string {
  const parts = highlightSearchText(text, props.targetSearchQuery, props.searchCaseSensitive)
    || highlightText(text, props.matchedTerms || [], 'target_text')
    || highlightQAText(text, activeQAIssues.value)
  if (!parts) {
    return textToVisibleChars(text)
  }
  return renderHighlightPartsAsHtml(parts, text)
}

function hasTargetHighlights(): boolean {
  return Boolean(resolveSearchKeyword(props.targetSearchQuery))
    || (props.matchedTerms || []).some((term) => Boolean(term.target_text))
    || activeQAIssues.value.length > 0
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
      || element.classList.contains('segment-row__qa-highlight')
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
      || element.classList.contains('segment-row__qa-highlight')
    ) {
      return
    }

    Array.from(element.childNodes).forEach(processNode)
  }

  Array.from(template.content.childNodes).forEach(processNode)
  return template.innerHTML
}

const sourceHtmlContent = computed(() => {
  if (props.segment.source_html && !hasAutomaticNumbering.value) {
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

function getSerializableOffsetForPosition(el: HTMLElement, container: Node, positionOffset: number): number {
  let offset = 0
  let found = false

  function traverse(node: Node): boolean {
    if (found) {
      return true
    }

    if (node === container) {
      if (node.nodeType === Node.TEXT_NODE) {
        offset += isRevisionDeleteNode(node)
          ? 0
          : (node.textContent || '').slice(0, positionOffset).length
      } else {
        const children = Array.from(node.childNodes).slice(0, positionOffset)
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

function saveSerializableCaretPosition(el: HTMLElement): number {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) return 0

  const range = selection.getRangeAt(0)
  if (!el.contains(range.startContainer)) {
    return 0
  }

  return getSerializableOffsetForPosition(el, range.startContainer, range.startOffset)
}

function getSerializableSelectionRange(el: HTMLElement): EditorTextRange | null {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) return null

  const range = selection.getRangeAt(0)
  if (!el.contains(range.startContainer) || !el.contains(range.endContainer)) {
    return null
  }

  const start = getSerializableOffsetForPosition(el, range.startContainer, range.startOffset)
  const end = getSerializableOffsetForPosition(el, range.endContainer, range.endOffset)
  return {
    start: Math.min(start, end),
    end: Math.max(start, end),
  }
}

function getNodeIndex(node: Node): number {
  return Array.from(node.parentNode?.childNodes || []).findIndex((child) => child === node)
}

function resolveSerializablePosition(el: HTMLElement, targetOffset: number): { node: Node; offset: number } {
  const normalizedOffset = Math.max(0, targetOffset)
  let currentOffset = 0
  let fallback: { node: Node; offset: number } = { node: el, offset: el.childNodes.length }
  let resolved: { node: Node; offset: number } | null = null

  function traverse(node: Node): boolean {
    if (isRevisionDeleteNode(node)) {
      return false
    }

    if (node.nodeType === Node.TEXT_NODE) {
      const textLength = node.textContent?.length || 0
      if (currentOffset + textLength >= normalizedOffset) {
        resolved = {
          node,
          offset: Math.max(0, Math.min(textLength, normalizedOffset - currentOffset)),
        }
        return true
      }
      currentOffset += textLength
      fallback = { node, offset: textLength }
      return false
    }

    if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR') {
      const parent = node.parentNode || el
      const index = Math.max(0, getNodeIndex(node))
      if (currentOffset + 1 >= normalizedOffset) {
        resolved = { node: parent, offset: index }
        return true
      }
      currentOffset += 1
      fallback = { node: parent, offset: index + 1 }
      return false
    }

    return Array.from(node.childNodes).some((child) => {
      if (traverse(child)) {
        return true
      }
      return false
    })
  }

  traverse(el)
  return resolved || fallback
}

function restoreSerializableCaretPosition(el: HTMLElement, offset: number) {
  const selection = window.getSelection()
  if (!selection) return

  const range = document.createRange()
  const position = resolveSerializablePosition(el, offset)
  range.setStart(position.node, position.offset)
  range.collapse(true)

  selection.removeAllRanges()
  selection.addRange(range)
}

function restoreSerializableSelectionRange(el: HTMLElement, textRange: EditorTextRange): boolean {
  const selection = window.getSelection()
  if (!selection) return false

  const start = Math.max(0, Math.min(textRange.start, textRange.end))
  const end = Math.max(start, textRange.end)
  const startPosition = resolveSerializablePosition(el, start)
  const endPosition = resolveSerializablePosition(el, end)
  const range = document.createRange()
  range.setStart(startPosition.node, startPosition.offset)
  range.setEnd(endPosition.node, endPosition.offset)
  selection.removeAllRanges()
  selection.addRange(range)
  return true
}

function getCurrentEditorText(): string {
  if (editorRef.value) {
    return serializeEditorContent(editorRef.value)
  }
  return getTargetStateText()
}

function getTargetStateText(): string {
  return props.pendingRevision?.after_text ?? props.segment.target_text ?? ''
}

function getCurrentEditorHtml(): string | null {
  if (!editorRef.value) {
    return props.segment.target_html || null
  }
  const html = serializeEditorContentWithFormat(editorRef.value)
  return /<(b|strong|i|em|u|s|strike|del|sub|sup)>/i.test(html) ? html : null
}

function commitEditorContent(): CommittedEditorContent | null {
  if (!editorRef.value || isApplyingHistory.value || isComposing.value) {
    return null
  }

  const text = serializeEditorContent(editorRef.value)
  const html = getCurrentEditorHtml()
  const currentText = props.pendingRevision?.after_text ?? props.segment.target_text ?? ''
  const currentHtml = props.segment.target_html || null

  if (text !== currentText || html !== currentHtml) {
    emit('update', segmentKey.value, text, html || undefined)
  }

  return {
    sentenceId: segmentKey.value,
    text,
    html,
  }
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
    return false
  }
  stack.push(snapshot)
  if (stack.length > MAX_EDITOR_HISTORY_SIZE) {
    stack.shift()
  }
  return true
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
    return false
  }
  if (!shouldStartNewHistoryGroup(options)) {
    return false
  }
  const recorded = pushHistorySnapshot(undoStack.value, getCurrentEditorSnapshot())
  lastHistorySnapshotAt = Date.now()
  lastHistoryInputKind = getHistoryInputKind(options.inputType)
  if (clearRedo) {
    redoStack.value = []
  }
  return recorded
}

function recordEditorUndoBoundary(options: EditorUndoBoundaryOptions = {}) {
  if (props.disabled || !editorRef.value || isApplyingHistory.value || isComposing.value) {
    return false
  }
  const recorded = recordUndoSnapshot(true, {
    inputType: options.inputType || 'programmaticEdit',
    data: options.data,
    force: true,
  })
  if (options.preserveNextTargetSync) {
    preserveNextTargetSync = true
  }
  return recorded
}

function cacheTargetSelectionFromDom() {
  const editor = editorRef.value
  if (!editor) {
    return
  }
  const textRange = getSerializableSelectionRange(editor)
  if (textRange) {
    lastTargetSelectionRange.value = textRange
  }
}

function insertOrReplaceTargetText(text: string): boolean {
  const editor = editorRef.value
  if (!editor || props.disabled || isApplyingHistory.value || isComposing.value || !text) {
    return false
  }

  const activeRange = getSerializableSelectionRange(editor)
  const textRange = activeRange
    || lastTargetSelectionRange.value
    || {
      start: getCurrentEditorText().length,
      end: getCurrentEditorText().length,
    }

  recordEditorUndoBoundary({
    inputType: 'insertTargetTextFromMatchPanel',
    data: text,
    preserveNextTargetSync: true,
  })
  editor.focus({ preventScroll: true })
  restoreSerializableSelectionRange(editor, textRange)
  document.execCommand('insertText', false, text)
  const caretOffset = Math.min(textRange.start, textRange.end) + text.length
  lastTargetSelectionRange.value = { start: caretOffset, end: caretOffset }
  handleInput()
  return true
}

function shouldPreserveHistoryForStateSync() {
  if (preserveNextTargetSync || isFocused.value || isApplyingHistory.value) {
    return true
  }
  if (!editorRef.value) {
    return false
  }
  const stateText = props.pendingRevision?.after_text ?? props.segment.target_text ?? ''
  return serializeEditorContent(editorRef.value) === stateText
}

function applyHistorySnapshot(snapshot: EditorHistorySnapshot) {
  isApplyingHistory.value = true
  emit('update', segmentKey.value, snapshot.text, snapshot.html || undefined)
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
  emit('focus', segmentKey.value)
  cacheTargetSelectionFromDom()
}

function handleBlur() {
  cacheTargetSelectionFromDom()
  commitEditorContent()
  isFocused.value = false
  resetHistoryGroup()
  void nextTick(() => syncEditorHtmlFromState(false))
}

function isSegmentMultiSelectEvent(event?: MouseEvent) {
  return Boolean(event && (event.ctrlKey || event.metaKey || event.shiftKey))
}

function handleSelectMouseDown(event: MouseEvent) {
  if (isSegmentMultiSelectEvent(event)) {
    event.preventDefault()
  }
}

function handleClick(event: MouseEvent) {
  resetHistoryGroup()
  if (isSegmentMultiSelectEvent(event)) {
    emit('ctrlClick', segmentKey.value, event)
    return
  }
  cacheTargetSelectionFromDom()
  emit('activateTarget', segmentKey.value)
}

function handleProjectSyncToggle() {
  if (props.disabled) {
    return
  }
  emit('toggleProjectSync', segmentKey.value, !props.segment.project_sync_disabled)
}

function handleSourceFocus() {
  isSourceFocused.value = true
}

function handleSourceBlur() {
  isSourceFocused.value = false
}

function handleSourceInput() {
  if (!sourceEditorRef.value) return
  if (!props.sourceEditing || props.disabled) {
    // 非编辑模式下恢复原文内容
    syncSourceEditorFromState(true)
    return
  }
  const text = sourceEditorRef.value.textContent || ''
  emit('updateSource', segmentKey.value, text)
}

function handleSourceBeforeInput(event: Event) {
  if (!props.sourceEditing || props.disabled) {
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

function handleEditorShellClick(event: MouseEvent) {
  handleClick(event)
  if (isSegmentMultiSelectEvent(event)) {
    return
  }
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

function insertEditorLineBreak() {
  if (!editorRef.value || props.disabled || isApplyingHistory.value || isComposing.value) {
    return
  }

  recordUndoSnapshot(true, { force: true, inputType: 'insertLineBreak' })
  if (props.showVisibleChars) {
    document.execCommand(
      'insertHTML',
      false,
      '<span class="visible-char visible-char--newline" contenteditable="false">¶</span>\n',
    )
  } else {
    document.execCommand('insertLineBreak')
  }
  handleInput()
}

function handleKeydown(event: KeyboardEvent) {
  if (props.disabled || isApplyingHistory.value || event.altKey) {
    return
  }

  if (event.key === 'Enter' && (event.isComposing || isComposing.value)) {
    event.stopPropagation()
    return
  }

  if (event.key === 'Enter' && !event.isComposing) {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault()
      insertEditorLineBreak()
      return
    }

    if (!event.shiftKey) {
      event.preventDefault()
      return
    }
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
  cacheTargetSelectionFromDom()

  // 检查是否有格式标签
  const cleanHtml = serializeEditorContentWithFormat(editorRef.value)
  const hasFormatTags = /<(b|strong|i|em|u|s|strike|del|sub|sup)>/i.test(cleanHtml)

  // 获取纯文本内容用于保存
  const text = serializeEditorContent(editorRef.value)

  // 如果有格式标签，同时传递 HTML
  if (hasFormatTags) {
    // 清理 HTML，只保留格式标签
    emit('update', segmentKey.value, text, cleanHtml)
    return
  }

  // 没有格式标签，只传递纯文本
  emit('update', segmentKey.value, text)
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
  const clipboardData = event.clipboardData
  const html = clipboardData?.getData('text/html') || ''
  const text = clipboardData?.getData('text/plain') || ''

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

function syncFocusedTargetHighlightsFromState() {
  const editor = editorRef.value
  if (!editor || isApplyingHistory.value || isComposing.value) {
    return
  }
  if (isFocused.value && serializeEditorContent(editor) !== getTargetStateText()) {
    return
  }
  syncEditorHtmlFromState(isFocused.value)
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
    lastTargetSelectionRange.value = null
    clearEditorHistory()
  }
)

watch(
  () => props.segment.target_text,
  () => {
    const shouldPreserveHistory = shouldPreserveHistoryForStateSync()
    preserveNextTargetSync = false
    if (!shouldPreserveHistory) {
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
    const shouldPreserveHistory = shouldPreserveHistoryForStateSync()
    preserveNextTargetSync = false
    if (!shouldPreserveHistory) {
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

watch(
  () => (props.matchedTerms || [])
    .map((term) => `${term.id}\u0000${term.source_text}\u0000${term.target_text}`)
    .join('\u0001'),
  () => {
    syncFocusedTargetHighlightsFromState()
  },
  { flush: 'post' },
)

defineExpose({
  undoEditorChange,
  redoEditorChange,
  recordEditorUndoBoundary,
  insertOrReplaceTargetText,
  commitEditorContent,
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
    :id="`segment-${segmentKey}`"
    data-testid="segment-row"
    :data-sentence-id="segmentKey"
    :data-has-pending-revision="hasPendingRevision ? 'true' : 'false'"
    role="group"
    :aria-label="`segment ${index + 1}`"
  >
    <div class="segment-row__meta">
      <span class="segment-row__index">{{ index + 1 }}</span>
    </div>

    <div class="segment-row__cell segment-row__cell--source" @mousedown="handleSelectMouseDown" @click="handleClick">
      <div class="segment-row__source-content">
        <span
          v-if="hasAutomaticNumbering"
          class="segment-row__automatic-numbering-badge"
          :title="automaticNumberingTitle"
          aria-hidden="true"
          contenteditable="false"
        >
          {{ automaticNumberingText }}
        </span>
        <div
          v-if="active"
          ref="sourceEditorRef"
          class="segment-row__source-editor"
          :class="{ 'is-focused': isSourceFocused, 'is-readonly': !sourceEditing }"
          :contenteditable="sourceEditing && !disabled"
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
    </div>

    <div class="segment-row__cell segment-row__cell--target" :class="{ 'is-pending': hasPendingRevision }">
      <div
        class="segment-row__editor-shell"
        :class="{ 'is-focused': isFocused, 'is-disabled': disabled, 'has-revision': hasPendingRevision }"
        @mousedown="handleSelectMouseDown"
        @click="handleEditorShellClick"
      >
      <div
        v-if="false && pendingRevision"
        class="segment-row__revision-inline"
        data-testid="segment-revision-inline"
        :data-sentence-id="segmentKey"
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
      <div class="segment-row__target-content">
        <span
          v-if="hasTargetAutomaticNumbering"
          class="segment-row__automatic-numbering-badge segment-row__automatic-numbering-badge--target"
          :title="automaticNumberingTitle"
          aria-hidden="true"
          contenteditable="false"
        >
          {{ targetAutomaticNumberingText }}
        </span>
        <div
          ref="editorRef"
          class="segment-row__editor"
          :class="[
            { 'is-focused': isFocused, 'has-revision': hasPendingRevision },
            revisionAuthorClass,
          ]"
          :style="revisionColorStyle"
          :contenteditable="!disabled"
          tabindex="0"
          data-testid="segment-target-editor"
          :data-revision-visible="hasPendingRevision ? 'true' : 'false'"
          data-segment-target="true"
          :data-sentence-id="segmentKey"
          :aria-label="`translation for segment ${index + 1}`"
          spellcheck="false"
          @focus="handleFocus"
          @blur="handleBlur"
          @mousedown="handleSelectMouseDown"
          @mouseup="cacheTargetSelectionFromDom"
          @click.stop="handleClick"
          @keydown="handleKeydown"
          @keyup="cacheTargetSelectionFromDom"
          @compositionstart="handleCompositionStart"
          @compositionend="handleCompositionEnd"
          @beforeinput="handleBeforeInput"
          @input="handleInput"
          @paste="handlePaste"
        />
      </div>
      </div>
    </div>

    <div class="segment-row__cell segment-row__cell--state" :title="statusMeta.label">
      <span
        v-if="segment.status === 'confirmed'"
        class="segment-row__confirm-mark"
        aria-label="已确认"
      >√</span>
      <span v-if="scorePercent !== null" class="segment-row__match-rate">
        {{ scorePercent }}%
      </span>
      <span
        v-if="showStatusTag && segment.status !== 'confirmed' && scorePercent === null"
        class="segment-row__compact-tag segment-row__compact-tag--status"
      >
        {{ statusMeta.label }}
      </span>
      <span v-if="showSourceTag" class="segment-row__compact-tag" :class="sourceClass" :title="sourceTitle">
        {{ compactSourceLabel }}
      </span>
      <button
        v-if="showProjectSyncToggle"
        class="segment-row__sync-toggle"
        :class="{ 'is-disabled-sync': segment.project_sync_disabled }"
        type="button"
        :title="projectSyncToggleLabel"
        :aria-label="projectSyncToggleLabel"
        :aria-pressed="!segment.project_sync_disabled"
        :disabled="disabled"
        @click.stop="handleProjectSyncToggle"
      >
        <component
          :is="segment.project_sync_disabled ? Link2 : Link2Off"
          :size="13"
          :stroke-width="2.2"
          aria-hidden="true"
        />
        <span class="sr-only">{{ projectSyncToggleLabel }}</span>
      </button>
      <span
        v-if="hasPendingRevision"
        class="segment-row__compact-tag segment-row__tag--revision"
        data-testid="segment-revision-tag"
        :title="`修订来源：${revisionSourceMeta.label}`"
      >
        待审校
      </span>
    </div>

    <div class="segment-row__cell segment-row__cell--workflow">
      <span class="segment-row__workflow-label">{{ workflowLabel }}</span>
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

.segment-row__source-content,
.segment-row__target-content {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
}

.segment-row__source-content .segment-row__text,
.segment-row__source-content .segment-row__source-editor,
.segment-row__target-content .segment-row__editor {
  flex: 1 1 auto;
  min-width: 0;
}

.segment-row__automatic-numbering-badge {
  flex: 0 0 auto;
  max-width: 72px;
  margin-top: 8px;
  padding: 1px 6px;
  border: 1px solid rgba(91, 115, 132, 0.24);
  border-radius: 4px;
  background: rgba(241, 245, 249, 0.92);
  color: #526574;
  font-size: 12px;
  font-weight: 650;
  line-height: 1.45;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  user-select: none;
}

.segment-row__automatic-numbering-badge--target {
  margin-top: 9px;
}

.segment-row__cell--state,
.segment-row__cell--workflow {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  min-height: 0;
  padding: 6px 4px;
  border-left: 1px solid rgba(214, 226, 222, 0.9);
  background:
    linear-gradient(0deg, var(--segment-cell-stripe, transparent), var(--segment-cell-stripe, transparent)),
    rgba(248, 250, 252, 0.92);
  color: var(--text-primary);
}

.segment-row__cell--state {
  flex-direction: column;
  gap: 3px;
}

.segment-row__cell--workflow {
  color: #1f4f7a;
  font-size: 0;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
}

.segment-row__workflow-label {
  font-size: 13px;
}

.segment-row__confirm-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  min-height: 18px;
  color: #4fa873;
  font-size: 18px;
  font-weight: 800;
  line-height: 1;
}

.segment-row__match-rate {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 18px;
  padding: 0 4px;
  border-radius: 2px;
  background: #4fa873;
  color: #ffffff;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.segment-row__compact-tag {
  max-width: 100%;
  min-height: 16px;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(232, 239, 241, 0.96);
  color: #556d72;
  font-size: 10px;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.segment-row__compact-tag--status {
  color: #0d726b;
  background: rgba(223, 241, 239, 0.96);
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
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  min-width: 24px;
  min-height: 24px;
  padding: 0;
  border: 1px solid rgba(34, 127, 88, 0.28);
  border-radius: 6px;
  background: rgba(34, 127, 88, 0.08);
  color: #146c49;
  line-height: 1;
  cursor: pointer;
}

.segment-row__sync-toggle:hover {
  background: rgba(34, 127, 88, 0.14);
}

.segment-row__sync-toggle.is-disabled-sync {
  border-color: rgba(91, 115, 132, 0.26);
  background: rgba(91, 115, 132, 0.08);
  color: #526574;
}

.segment-row__term-highlight {
  background: rgba(247, 187, 42, 0.46);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  box-shadow: inset 0 0 0 1px rgba(152, 103, 0, 0.32);
  font-weight: 600;
}

.segment-row__search-highlight {
  background: #fff176;
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  box-shadow: inset 0 0 0 1px rgba(138, 103, 0, 0.2);
  font-weight: 600;
}

.segment-row__qa-highlight {
  color: inherit;
  text-decoration-line: underline;
  text-decoration-style: wavy;
  text-decoration-color: #d92d20;
  text-decoration-thickness: 1.5px;
  text-underline-offset: 3px;
}

/* 穿透 scoped 样式，让 innerHTML 插入的 mark 标签也能应用样式 */
.segment-row__text :deep(.segment-row__term-highlight),
.segment-row__source-editor :deep(.segment-row__term-highlight) {
  background: rgba(247, 187, 42, 0.46);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  box-shadow: inset 0 0 0 1px rgba(152, 103, 0, 0.32);
  font-weight: 600;
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

.segment-row__text :deep(.segment-row__qa-highlight),
.segment-row__source-editor :deep(.segment-row__qa-highlight) {
  color: inherit;
  text-decoration-line: underline;
  text-decoration-style: wavy;
  text-decoration-color: #d92d20;
  text-decoration-thickness: 1.5px;
  text-underline-offset: 3px;
}

.segment-row__editor :deep(.segment-row__term-highlight) {
  background: rgba(247, 187, 42, 0.46);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  box-shadow: inset 0 0 0 1px rgba(152, 103, 0, 0.32);
  font-weight: 600;
}

.segment-row__editor :deep(.segment-row__search-highlight) {
  background: #fff176;
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  box-shadow: inset 0 0 0 1px rgba(138, 103, 0, 0.2);
  font-weight: 600;
}

.segment-row__editor :deep(.segment-row__qa-highlight) {
  color: inherit;
  text-decoration-line: underline;
  text-decoration-style: wavy;
  text-decoration-color: #d92d20;
  text-decoration-thickness: 1.5px;
  text-underline-offset: 3px;
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
  color: var(--rev-insert-color, #2563eb);
  text-decoration: underline;
  text-decoration-color: var(--rev-insert-color, #2563eb);
  text-decoration-thickness: 1px;
  text-underline-offset: 2px;
}

.segment-row__editor :deep(.segment-row__revision-delete) {
  color: var(--rev-delete-color, #dc2626);
  text-decoration: line-through;
  text-decoration-color: var(--rev-delete-color, #dc2626);
  text-decoration-thickness: 1px;
  user-select: text;
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
