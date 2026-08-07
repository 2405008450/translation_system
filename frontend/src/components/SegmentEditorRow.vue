<script setup lang="ts">
import { Copy, CornerDownLeft, Link2, Link2Off } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue'

import InteractiveDiffText from './InteractiveDiffText.vue'

import { getSegmentSourceMeta, getSegmentStatusMeta } from '../constants/status'
import { getLanguageDirection } from '../constants/languages'
import { useAuthStore } from '../stores/auth'
import type { LiveSpellingIssue, RevisionDisplaySettings, Segment, SegmentQAIssue, SegmentRevisionEntry, TermEntryRecord } from '../types/api'
import { findTermTextRanges } from '../utils/termMatching'
import { computeDiff } from '../utils/textDiff'
import type { TextFormat } from '../composables/useRichTextEditor'

type ProofreadingSuggestion = {
  text: string
  label: string
  tone: 'changed' | 'unchanged' | 'error' | 'pending'
}

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
  qaIssues?: Array<SegmentQAIssue | LiveSpellingIssue>
  sourceSearchQuery?: string
  targetSearchQuery?: string
  searchCaseSensitive?: boolean
  showVisibleChars?: boolean
  /** 是否在译文（非编辑态）显示行内样式标记（⟦1⟧…⟦/1⟧）；关闭时隐藏，聚焦编辑时始终显示以便安全编辑 */
  showFormatMarks?: boolean
  /** 标签编辑模式：开启后样式预览区可选中文字一键加/删样式标签（只写 target_layout_text） */
  tagEditMode?: boolean
  pendingFormats?: Record<TextFormat, boolean> & { _overrideActive?: boolean }
  /** 句段对外标识：单文件模式即 sentence_id；合并模式为复合键 ${file_record_id}:${sentence_id} */
  segmentKey?: string
  sourceLanguage?: string | null
  targetLanguage?: string | null
  /** Excel 校对导入时的不可变原译文；非校对任务为 null。 */
  originalTargetText?: string | null
  /** 校对任务中是否显示原译文与校对版的行内差异。 */
  showProofreadingDiff?: boolean
  /** 校对工作台专用的 LLM 修改建议；非校对任务为 null。 */
  proofreadingSuggestion?: ProofreadingSuggestion | null
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
  showFormatMarks: false,
  tagEditMode: false,
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
  sourceLanguage: null,
  targetLanguage: null,
  originalTargetText: null,
  showProofreadingDiff: true,
  proofreadingSuggestion: null,
})

const emit = defineEmits<{
  update: [sentenceId: string, value: string, html?: string]
  updateSource: [sentenceId: string, value: string]
  updateTargetLayout: [sentenceId: string, targetLayoutText: string]
  focus: [sentenceId: string]
  activateTarget: [sentenceId: string]
  activateSource: [sentenceId: string]
  sourceCaretChange: [sentenceId: string, offset: number]
  copySourceToTarget: [sentenceId: string]
  applyPartialRevision: [revisionId: string, newText: string]
  ctrlClick: [sentenceId: string, event: MouseEvent]
  toggleProjectSync: [sentenceId: string, disabled: boolean]
}>()

const editorRef = ref<HTMLDivElement | null>(null)
const sourceEditorRef = ref<HTMLDivElement | null>(null)
const authStore = useAuthStore()
const isFocused = ref(false)
const isSourceFocused = ref(false)
const isComposing = ref(false)
const editorDirtySinceFocus = ref(false)
const pendingSourceFocus = ref(false)
const pendingSourceFocusPoint = ref<{ x: number; y: number } | null>(null)

// 对外标识：合并视图使用复合键，单文件回退为 sentence_id
const segmentKey = computed(() => props.segmentKey || props.segment.sentence_id)
const sourceDirection = computed(() => getLanguageDirection(props.sourceLanguage))
const targetDirection = computed(() => getLanguageDirection(props.targetLanguage))
const MAX_EDITOR_HISTORY_SIZE = 100
const EDITOR_HISTORY_GROUP_TIMEOUT_MS = 1200
const REVISION_RERENDER_DEBOUNCE_MS = 150
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

interface LocalEditorEcho {
  sentenceId: string
  text: string
  html: string | null
}

type HighlightKind = 'term' | 'search' | 'qa'
type HighlightPart = { text: string; highlight: boolean; kind?: HighlightKind; title?: string }
type BasicFormatTag = 'b' | 'i' | 'u' | 's' | 'sub' | 'sup'
type BasicFormatRun = { text: string; tags: BasicFormatTag[] }
type InlineSpellingIssue = SegmentQAIssue | LiveSpellingIssue

const BASIC_FORMAT_TAGS = ['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'del', 'sub', 'sup']
const BASIC_FORMAT_RENDER_ORDER: BasicFormatTag[] = ['b', 'i', 'u', 's', 'sub', 'sup']
const DROPPED_HTML_TAGS = new Set(['script', 'style', 'noscript', 'iframe', 'object', 'embed', 'link', 'meta'])
const EXPLICIT_PLAIN_FORMAT_DATA_KEY = 'explicitPlainFormat'

const undoStack = ref<EditorHistorySnapshot[]>([])
const redoStack = ref<EditorHistorySnapshot[]>([])
const isApplyingHistory = ref(false)
const compositionSnapshotRecorded = ref(false)
const lastTargetSelectionRange = ref<EditorTextRange | null>(null)
const localEditorEcho = ref<LocalEditorEcho | null>(null)
let lastHistorySnapshotAt = 0
let lastHistoryInputKind = ''
let preserveNextTargetSync = false
let revisionRerenderTimer: ReturnType<typeof setTimeout> | null = null
const canUndoEditorChange = computed(() => undoStack.value.length > 0)
const canRedoEditorChange = computed(() => redoStack.value.length > 0)

function normalizeMatchText(value: string | null | undefined) {
  return (value || '').trim().replace(/\s+/g, ' ').replace(/[\u3002\uff01\uff1f!?.]+$/u, '')
}

function compactMatchCore(value: string | null | undefined) {
  return normalizeMatchText(value).replace(/[^\w\u4e00-\u9fff]+/gu, '')
}

function isShortStructuralFragment(value: string | null | undefined) {
  const core = compactMatchCore(value)
  return Boolean(core && core.length <= 4 && /^(?:\d+[A-Za-z]?|[A-Za-z]|[ivxlcdmIVXLCDM]{1,4})$/.test(core))
}

function normalizedSequenceRatio(left: string, right: string): number {
  if (left === right) return 1
  const rows = left.length + 1
  const cols = right.length + 1
  const lengths = Array.from({ length: rows }, () => Array<number>(cols).fill(0))
  for (let row = 1; row < rows; row += 1) {
    for (let col = 1; col < cols; col += 1) {
      lengths[row][col] = left[row - 1] === right[col - 1]
        ? lengths[row - 1][col - 1] + 1
        : Math.max(lengths[row - 1][col], lengths[row][col - 1])
    }
  }
  return (2 * lengths[left.length][right.length]) / Math.max(left.length + right.length, 1)
}

function capShortStructuralDisplayScore(
  score: number,
  sourceText: string | null | undefined,
  matchedSourceText: string | null | undefined,
) {
  const normalizedSource = normalizeMatchText(sourceText)
  const normalizedMatchedSource = normalizeMatchText(matchedSourceText)
  if (!normalizedSource || !normalizedMatchedSource || normalizedSource === normalizedMatchedSource) {
    return score
  }
  const sourceCore = compactMatchCore(normalizedSource)
  const matchedCore = compactMatchCore(normalizedMatchedSource)
  if (
    sourceCore
    && sourceCore === matchedCore
    && (isShortStructuralFragment(normalizedSource) || isShortStructuralFragment(normalizedMatchedSource))
  ) {
    return Math.min(score, normalizedSequenceRatio(normalizedSource, normalizedMatchedSource), 0.79)
  }
  return score
}

function normalizeDisplayScore(
  score: number | null | undefined,
  exactTextMatch = false,
  sourceText?: string | null,
  matchedSourceText?: string | null,
): number | null {
  if (score === null || score === undefined || !Number.isFinite(score) || score <= 0) return null
  const safeScore = Math.min(Math.max(score, 0), 1)
  const cappedScore = capShortStructuralDisplayScore(safeScore, sourceText, matchedSourceText)
  return exactTextMatch ? cappedScore : Math.min(cappedScore, 0.99)
}

const hasExactTextMatch = computed(() => {
  const sourceText = normalizeMatchText(props.segment.source_text)
  const displayText = normalizeMatchText(props.segment.display_text)
  const matchedSourceText = normalizeMatchText(props.segment.matched_source_text)
  return Boolean(
    (sourceText && matchedSourceText && matchedSourceText === sourceText)
    || (
      displayText
      && matchedSourceText
      && matchedSourceText === displayText
      && !isShortStructuralFragment(props.segment.source_text)
    )
  )
})
const effectiveSegmentStatus = computed(() => {
  if (props.segment.source === 'project_sync') {
    return 'project_sync'
  }
  if (props.segment.status === 'confirmed') {
    return 'confirmed'
  }
  if (hasExactTextMatch.value) {
    return 'exact'
  }
  const matchedSourceText = normalizeMatchText(props.segment.matched_source_text)
  const score = Number(props.segment.score || 0)
  if (score > 0 || matchedSourceText || props.segment.status === 'fuzzy') {
    return 'fuzzy'
  }
  return 'none'
})
const statusClass = computed(() => `segment-row--${effectiveSegmentStatus.value}`)
const parityClass = computed(() => (props.index % 2 === 0 ? 'segment-row--odd' : 'segment-row--even'))
const sourceClass = computed(() => `segment-row__tag--source-${props.segment.source || 'none'}`)
const isEmptyTarget = computed(() => {
  const targetText = props.pendingRevision?.after_text ?? props.segment.target_text ?? ''
  return targetText.length === 0
})
const proofreadingTargetText = computed(() => (
  props.pendingRevision?.after_text ?? props.segment.target_text ?? ''
))
const isProofreadingChanged = computed(() => (
  props.originalTargetText !== null
  && proofreadingTargetText.value !== (props.originalTargetText || '')
))
const statusMeta = computed(() => getSegmentStatusMeta(effectiveSegmentStatus.value))
const sourceMeta = computed(() => getSegmentSourceMeta(props.segment.source))
const isProjectSynced = computed(() => props.segment.source === 'project_sync')
const sourceLabel = computed(() => {
  if (props.segment.source === 'llm') {
    return 'MT'
  }
  return sourceMeta.value.label
})
const compactSourceLabel = computed(() => (
  isProjectSynced.value ? '同步' : sourceLabel.value
))
const workflowLabel = computed(() => props.segment.workflow_step_name || '翻译')
const showStatusTag = computed(() => {
  if (isProjectSynced.value) {
    return false
  }
  const status = effectiveSegmentStatus.value
  return status !== 'none' && status !== 'fuzzy'
})
const showSourceTag = computed(() => {
  const source = props.segment.source || 'none'
  if (isProjectSynced.value) {
    return true
  }
  if (source === 'none' || source === 'fuzzy') {
    return false
  }
  if (source === 'llm') {
    return !isEmptyTarget.value
  }
  if (source === 'english_variant_conversion') {
    return !isEmptyTarget.value
  }
  return effectiveSegmentStatus.value !== 'none' && effectiveSegmentStatus.value !== 'fuzzy'
})
const showProjectSyncToggle = computed(() => true)
const projectSyncToggleLabel = computed(() => (
  props.segment.project_sync_disabled ? '开启同步' : '关闭同步'
))
const sourceTitle = computed(() => {
  if (props.segment.source === 'llm') {
    if (!authStore.isExternalTranslator && props.segment.llm_model?.trim()) {
      return props.segment.llm_model
    }
    return 'MT'
  }
  return sourceMeta.value.label
})
const revisionSourceMeta = computed(() => getSegmentSourceMeta(props.pendingRevision?.source || 'manual'))
const revisionAuthorRole = computed(() => props.pendingRevision?.author?.role || 'admin')
const hasPendingRevision = computed(() => Boolean(props.pendingRevision))
const visibleRevisionText = computed<{ before: string; after: string } | null>(() => {
  if (props.pendingRevision) {
    return {
      before: props.pendingRevision.before_text || '',
      after: props.pendingRevision.after_text || '',
    }
  }
  if (props.showProofreadingDiff && isProofreadingChanged.value && props.originalTargetText !== null) {
    return {
      before: props.originalTargetText || '',
      after: proofreadingTargetText.value,
    }
  }
  return null
})
const hasVisibleRevisionMarks = computed(() => Boolean(visibleRevisionText.value))
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
  hasVisibleRevisionMarks.value
    ? {
      '--rev-insert-color': revisionInsertColor.value,
      '--rev-delete-color': revisionDeleteColor.value,
    }
    : {}
))
const revisionTooltip = computed(() => {
  if (!props.pendingRevision && hasVisibleRevisionMarks.value) {
    return '原译文 → 校对版'
  }
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
const displayScore = computed(() => (
  normalizeDisplayScore(
    props.segment.score,
    effectiveSegmentStatus.value === 'exact' || effectiveSegmentStatus.value === 'confirmed',
    props.segment.source_text,
    props.segment.matched_source_text,
  )
))
const scorePercent = computed(() => (
  displayScore.value === null ? null : Math.round(displayScore.value * 100)
))
const showMatchRate = computed(() => !isProjectSynced.value && scorePercent.value !== null)
const matchRateTone = computed(() => {
  const score = displayScore.value ?? 0
  if (effectiveSegmentStatus.value === 'exact' && score >= 1) return 'exact'
  if (score >= 0.8) return 'high'
  if (score >= 0.6) return 'medium'
  return 'low'
})
const matchRateLabel = computed(() => {
  if (scorePercent.value === null) {
    return ''
  }
  return `${scorePercent.value}%`
})
const stateCellTitle = computed(() => (
  isProjectSynced.value ? sourceTitle.value : statusMeta.value.label
))

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
  return value
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
  return getTargetHighlightParts(text)
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

function highlightQAText(
  text: string,
  issues: InlineSpellingIssue[],
  absoluteOffset = 0,
): HighlightPart[] | null {
  if (!text || issues.length === 0) {
    return null
  }

  const ranges: Array<{ start: number; end: number; title: string }> = []
  for (const issue of issues) {
    const issueStart = Math.max(0, issue.offset)
    const issueEnd = issueStart + Math.max(0, issue.length)
    const start = Math.max(0, issueStart - absoluteOffset)
    const end = Math.min(text.length, issueEnd - absoluteOffset)
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
  if (hasExplicitTargetHtmlOverride() && !hasAutomaticNumbering.value) {
    return renderTargetHtmlWithHighlights(sanitizeHtml(getTargetStateHtml() ?? ''))
  }

  return renderTargetWithSourceFormats(getTargetStateText())
})

const editorHtmlContent = computed(() => {
  const revision = visibleRevisionText.value
  if (!revision) {
    return targetHtmlContent.value
  }
  return computeDiff(revision.before, revision.after)
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

// 行内样式标记：⟦1⟧ … ⟦/1⟧（容忍括号内空格），与后端 pptx_inline_tags 一致
const FORMAT_MARK_RE = /⟦\s*\/?\s*\d+\s*⟧/g
const FORMAT_MARK_TEST_RE = /⟦\s*\/?\s*\d+\s*⟧/

function hasFormatMarks(text: string): boolean {
  return FORMAT_MARK_TEST_RE.test(text || '')
}

/**
 * 前端侧有效性判定，作为后端校验之外的一层防御：只读预览必须严格基于“剥标签后
 * 与当前纯译文逐字相同”的 target_layout_text，否则宁可不预览，也不能让预览与
 * 实际译文错位（例如本地刚编辑过、还没和服务端同步）。
 */
function isTargetLayoutValid(targetText: string, targetLayoutText: string): boolean {
  if (!targetLayoutText) return false
  return targetLayoutText.replace(FORMAT_MARK_RE, '') === (targetText || '')
}

/**
 * 只读样式预览渲染：按 ⟦n⟧ 把对应译文片段包上逐词样式（内联 style span）。
 * 只用于独立的只读预览元素（segment-row__target-preview），绝不进入可编辑 DOM——
 * 编辑框永远只显示/编辑纯 target_text，这是避免“显示一会就消失/编辑卡死”的关键。
 */
function renderStyledTargetPreview(text: string, formatMap: Record<string, [string, string]>): string {
  const base = formatMap.base || ['', '']
  const out: string[] = []
  const re = /⟦\s*(\/?)\s*(\d+)\s*⟧/g
  let cursor = 0
  let currentId: string | null = null
  let match: RegExpExecArray | null

  const emit = (piece: string, id: string | null) => {
    if (!piece) return
    const [open, close] = id === null ? base : (formatMap[id] || base)
    out.push(`${open}${escapeHtml(piece)}${close}`)
  }

  while ((match = re.exec(text)) !== null) {
    emit(text.slice(cursor, match.index), currentId)
    cursor = match.index + match[0].length
    currentId = match[1] ? null : match[2]
  }
  emit(text.slice(cursor), currentId)
  return out.join('')
}

/**
 * 译文只读样式预览的 HTML：
 * - 有有效 target_layout_text（剥标签后与当前 target_text 逐字相同）：按标签逐词渲染；
 * - 否则统一用 base 样式包裹整句（无论 formatMap 是否只有 base）——这样多样式句段
 *   即使还没被标注（AI 未检查/标注已失效），预览框依然会渲染出来，虚线框和选词
 *   加/删标签的交互入口才不会被隐藏。只要 formatMap 存在且非空就会有预览。
 */
const targetPreviewHtml = computed(() => {
  const formatMap = props.segment.source_format_map
  if (!formatMap || Object.keys(formatMap).length === 0) {
    return ''
  }
  const text = props.segment.target_text || ''
  if (!text) {
    return ''
  }
  const layout = props.segment.target_layout_text || ''
  if (layout && isTargetLayoutValid(text, layout)) {
    return renderStyledTargetPreview(layout, formatMap as Record<string, [string, string]>)
  }
  const base = formatMap.base
  if (base && (base[0] || base[1])) {
    return `${base[0]}${escapeHtml(text)}${base[1]}`
  }
  return escapeHtml(text)
})

/**
 * 是否展示只读样式预览元素（替代可编辑框的视觉呈现，但编辑框本身始终挂载、
 * 内容始终是纯译文——用 v-show 切换可见性，不销毁/重建，聚焦行为不受影响）。
 */
const showTargetPreview = computed(() => (
  Boolean(props.showFormatMarks)
    && !isFocused.value
    && !hasPendingRevision.value
    && Boolean(targetPreviewHtml.value)
))

async function focusTargetPreview() {
  if (props.disabled) return
  if (props.tagEditMode) {
    // 标签编辑模式下点击/聚焦预览区不再跳转到纯文本编辑框，避免打断选词加/删标签操作。
    return
  }
  // 预览元素用 v-show 隐藏编辑框（display:none 时无法 focus）。先把 isFocused 置为
  // true 让 showTargetPreview 计算为 false、等一次 DOM 更新恢复编辑框可见，再真正
  // 聚焦——聚焦后原生 focus 事件会走 handleFocus，其余副作用（emit、缓存光标）照常触发。
  isFocused.value = true
  await nextTick()
  focusTargetEditorAtEnd()
}

// ─────────────────────────────────────────
// 手动选词标注：在只读样式预览区选中文字，一键加/删 ⟦n⟧ 样式标签。
// 只操作 target_layout_text，绝不触碰 target_text；结构规则与后端
// pptx_inline_tags.validate_tagged_text_structure 完全一致（标签成对、扁平、
// 每个 id 最多出现一次）——为保证这一点，任何被选区“部分”触及的已有标签，
// 加/删时都会整段清空该标签，而不是留下断裂的两段同 id 标记。
// ─────────────────────────────────────────

const targetPreviewRef = ref<HTMLDivElement | null>(null)

interface TagPopoverState {
  x: number
  y: number
  start: number
  end: number
  hasExistingTag: boolean
}

const tagPopover = ref<TagPopoverState | null>(null)

const availableStyleTagIds = computed(() => {
  const formatMap = props.segment.source_format_map as Record<string, [string, string]> | undefined
  if (!formatMap) return []
  return Object.keys(formatMap)
    .filter((key) => key !== 'base' && /^\d+$/.test(key))
    .map((key) => Number(key))
    .sort((a, b) => a - b)
})

function styleTagPreviewLabelHtml(id: number): string {
  const formatMap = props.segment.source_format_map as Record<string, [string, string]> | undefined
  const tokens = formatMap?.[String(id)]
  if (!tokens) return 'Aa'
  const [open, close] = tokens
  return `${open || ''}Aa${close || ''}`
}

/** 用 Range.toString() 的长度差技巧计算选区在预览纯文本中的字符偏移（与 target_text 对齐）。 */
function getPlainTextSelectionInPreview(root: HTMLElement): { start: number; end: number } | null {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
    return null
  }
  const range = selection.getRangeAt(0)
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) {
    return null
  }
  const startRange = document.createRange()
  startRange.selectNodeContents(root)
  startRange.setEnd(range.startContainer, range.startOffset)
  const start = startRange.toString().length

  const endRange = document.createRange()
  endRange.selectNodeContents(root)
  endRange.setEnd(range.endContainer, range.endOffset)
  const end = endRange.toString().length

  return { start: Math.min(start, end), end: Math.max(start, end) }
}

/** 把带标签文本展开为逐字符的 tagId 数组（null 表示未打标签/base）。 */
function charTagArrayFromLayout(plainLength: number, layoutText: string): (number | null)[] {
  const result: (number | null)[] = new Array(plainLength).fill(null)
  if (!layoutText) return result
  const markerRe = /⟦\s*(\/?)\s*(\d+)\s*⟧/g
  let currentId: number | null = null
  let cursor = 0
  let plainIndex = 0
  let match: RegExpExecArray | null
  while ((match = markerRe.exec(layoutText)) !== null) {
    const piece = layoutText.slice(cursor, match.index)
    for (let k = 0; k < piece.length && plainIndex < plainLength; k += 1, plainIndex += 1) {
      result[plainIndex] = currentId
    }
    cursor = match.index + match[0].length
    currentId = match[1] ? null : Number(match[2])
  }
  const tail = layoutText.slice(cursor)
  for (let k = 0; k < tail.length && plainIndex < plainLength; k += 1, plainIndex += 1) {
    result[plainIndex] = currentId
  }
  return result
}

/** 把逐字符 tagId 数组重新序列化为带 ⟦n⟧ 标签的文本（同 id 只会有一段，见下方清空逻辑）。 */
function serializeCharTagArray(text: string, tags: (number | null)[]): string {
  const parts: string[] = []
  let i = 0
  while (i < text.length) {
    const tagId = tags[i]
    let j = i + 1
    while (j < text.length && tags[j] === tagId) {
      j += 1
    }
    const piece = text.slice(i, j)
    parts.push(tagId === null ? piece : `⟦${tagId}⟧${piece}⟦/${tagId}⟧`)
    i = j
  }
  return parts.join('')
}

/**
 * 清空所有与 [start,end) 有交集的标签的整段范围（不只是交集部分），保证每个 id
 * 清空后不会残留断裂的两段——加/删标签前都先调用它，是维持“每个 id 最多一段”
 * 结构合法性的关键。
 */
function clearTouchedTagRuns(tags: (number | null)[], start: number, end: number): void {
  const touchedIds = new Set<number>()
  for (let i = start; i < end; i += 1) {
    const id = tags[i]
    if (id !== null) touchedIds.add(id)
  }
  if (touchedIds.size === 0) return
  for (let i = 0; i < tags.length; i += 1) {
    if (tags[i] !== null && touchedIds.has(tags[i] as number)) {
      tags[i] = null
    }
  }
}

function currentCharTagArray(): (number | null)[] {
  const text = props.segment.target_text || ''
  const layout = props.segment.target_layout_text || ''
  const layoutIsValid = layout && isTargetLayoutValid(text, layout)
  return charTagArrayFromLayout(text.length, layoutIsValid ? layout : '')
}

function emitTargetLayoutUpdate(tags: (number | null)[]): void {
  const text = props.segment.target_text || ''
  const hasAnyTag = tags.some((id) => id !== null)
  const nextLayout = hasAnyTag ? serializeCharTagArray(text, tags) : ''
  emit('updateTargetLayout', segmentKey.value, nextLayout)
  tagPopover.value = null
}

function applyAddStyleTag(id: number): void {
  if (!tagPopover.value) return
  const { start, end } = tagPopover.value
  const tags = currentCharTagArray()
  clearTouchedTagRuns(tags, start, end)
  // 该 id 若在选区之外还存在旧的标注，一并清掉，确保标签移动后仍只有一段。
  for (let i = 0; i < tags.length; i += 1) {
    if (tags[i] === id) tags[i] = null
  }
  for (let i = start; i < end; i += 1) {
    tags[i] = id
  }
  emitTargetLayoutUpdate(tags)
  window.getSelection()?.removeAllRanges()
}

function applyRemoveStyleTag(): void {
  if (!tagPopover.value) return
  const { start, end } = tagPopover.value
  const tags = currentCharTagArray()
  clearTouchedTagRuns(tags, start, end)
  emitTargetLayoutUpdate(tags)
  window.getSelection()?.removeAllRanges()
}

function closeTagPopover(): void {
  tagPopover.value = null
}

function handleTargetPreviewMouseUp(): void {
  if (!props.tagEditMode || props.disabled) {
    return
  }
  const root = targetPreviewRef.value
  if (!root) return
  const selectionRange = getPlainTextSelectionInPreview(root)
  if (!selectionRange || selectionRange.end <= selectionRange.start) {
    tagPopover.value = null
    return
  }
  const domRange = window.getSelection()?.getRangeAt(0)
  const rect = domRange?.getBoundingClientRect()
  const tags = currentCharTagArray()
  const hasExistingTag = tags.slice(selectionRange.start, selectionRange.end).some((id) => id !== null)
  tagPopover.value = {
    x: rect ? rect.left + rect.width / 2 : 0,
    y: rect ? rect.top : 0,
    start: selectionRange.start,
    end: selectionRange.end,
    hasExistingTag,
  }
}

function handleTargetPreviewClick(event: MouseEvent): void {
  if (props.tagEditMode) {
    // 标签编辑模式下点击不再触发“聚焦纯文本编辑框”，只在 mouseup 里处理选区。
    event.preventDefault()
    return
  }
  void focusTargetPreview()
}

function renderTargetTextWithHighlights(text: string, absoluteOffset = 0): string {
  // 编辑框永远只展示纯文本（+搜索/术语高亮）。样式/标记只在独立的只读预览元素里
  // 渲染，绝不进入这里——避免样式标记与可编辑 DOM 相互干扰。
  // 对可能残留 ⟦n⟧ 的历史数据做一次防御性剥离，避免裸标记泄漏到编辑框里。
  const plainText = hasFormatMarks(text) ? text.replace(FORMAT_MARK_RE, '') : text
  const parts = absoluteOffset > 0
    ? highlightQAText(plainText, activeQAIssues.value, absoluteOffset)
    : getTargetHighlightParts(plainText)
  return !parts ? textToVisibleChars(plainText) : renderHighlightPartsAsHtml(parts, plainText)
}

function getTargetHighlightParts(text: string): HighlightPart[] | null {
  return highlightQAText(text, activeQAIssues.value)
    || (!isFocused.value
      ? highlightSearchText(text, props.targetSearchQuery, props.searchCaseSensitive)
        || highlightText(text, props.matchedTerms || [], 'target_text')
      : null)
}

function hasRenderedTargetHighlights(): boolean {
  if (activeQAIssues.value.length > 0) {
    return true
  }
  return !isFocused.value && (
    Boolean(resolveSearchKeyword(props.targetSearchQuery))
    || (props.matchedTerms || []).some((term) => Boolean(term.target_text))
  )
}

function hasRenderedEditorDecorations(): boolean {
  return hasVisibleRevisionMarks.value || props.showVisibleChars || hasRenderedTargetHighlights()
}

function editorHasDecorationNodes(editor: HTMLElement): boolean {
  return Boolean(editor.querySelector([
    '[data-revision-type]',
    '.segment-row__revision-segment',
    '.segment-row__term-highlight',
    '.segment-row__search-highlight',
    '.segment-row__qa-highlight',
    '.visible-char',
  ].join(',')))
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
  if (
    (!hasRenderedTargetHighlights() && !props.showVisibleChars)
    || typeof document === 'undefined'
  ) {
    return targetHtml
  }

  const template = document.createElement('template')
  template.innerHTML = targetHtml
  let absoluteOffset = 0

  function processNode(node: Node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent || ''
      if (!text) return
      const wrapper = document.createElement('span')
      wrapper.innerHTML = hasRenderedTargetHighlights()
        ? renderTargetTextWithHighlights(text, absoluteOffset)
        : textToVisibleChars(text)
      absoluteOffset += text.length
      const textNode = node as ChildNode
      textNode.replaceWith(...Array.from(wrapper.childNodes))
      return
    }

    if (node.nodeType !== Node.ELEMENT_NODE) {
      return
    }

    const element = node as HTMLElement
    if (element.tagName === 'BR') {
      absoluteOffset += 1
      return
    }
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
  // 原文列的样式展示与译文样式预览共用同一个开关：关闭时原文也退回纯文本
  // （仍保留搜索/术语高亮），保证“开关控制原文+译文样式显示”的一致体验。
  if (props.segment.source_html && !hasAutomaticNumbering.value && props.showFormatMarks) {
    return renderSourceHtmlWithHighlights(sanitizeSourceHtml(props.segment.source_html))
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
  // 有逐标记样式表时，renderTargetTextHtml 已按标记/ base 完整套好样式，不再叠加
  // 源文首个 run 的基础格式，避免重复加粗或整段错误统一格式。
  const formatMap = props.segment.source_format_map
  const hasFormatMap = Boolean(formatMap && Object.keys(formatMap).length > 0)
  if (!text || !props.segment.source_html || hasFormatMarks(text) || hasFormatMap) {
    return targetHtml
  }
  const inheritedHtml = projectSourceFormatsToTarget(props.segment.source_html, text)
  if (!inheritedHtml) {
    return renderTargetTextHtml(text)
  }
  return renderTargetHtmlWithHighlights(inheritedHtml)
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

function isVisibleCharacterMarker(node: Node | null): node is HTMLElement {
  return Boolean(
    node
    && node.nodeType === Node.ELEMENT_NODE
    && (node as HTMLElement).classList.contains('visible-char'),
  )
}

function isVisibleNewlineMarker(node: Node | null): node is HTMLElement {
  return isVisibleCharacterMarker(node) && node.classList.contains('visible-char--newline')
}

/**
 * 可见换行渲染为“¶ 标记 + \n 文本节点”。后面的 \n 只负责视觉断行，
 * 序列化和光标偏移时必须忽略，否则每次状态回填都会把一个换行复制成两个。
 */
function getSerializableTextNodeValue(node: Node): string {
  const text = node.textContent || ''
  if (
    node.nodeType === Node.TEXT_NODE
    && isVisibleNewlineMarker(node.previousSibling)
    && text.startsWith('\n')
  ) {
    return text.slice(1)
  }
  return text
}

function getSerializableTextNodeDomStart(node: Node): number {
  return (
    node.nodeType === Node.TEXT_NODE
    && isVisibleNewlineMarker(node.previousSibling)
    && (node.textContent || '').startsWith('\n')
  ) ? 1 : 0
}

function getSerializableNodeLength(node: Node): number {
  if (isRevisionDeleteNode(node)) {
    return 0
  }
  if (
    isVisibleCharacterMarker(node)
  ) {
    return 1
  }
  if (node.nodeType === Node.TEXT_NODE) {
    return getSerializableTextNodeValue(node).length
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
    return getSerializableTextNodeValue(node)
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
    return escapeHtml(getSerializableTextNodeValue(node))
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
        if (!isRevisionDeleteNode(node)) {
          const domStart = getSerializableTextNodeDomStart(node)
          offset += Math.max(0, positionOffset - domStart)
        }
      } else {
        const children = Array.from(node.childNodes).slice(0, positionOffset)
        offset += children.reduce((total, child) => total + getSerializableNodeLength(child), 0)
      }
      found = true
      return true
    }

    if (
      node.nodeType === Node.TEXT_NODE
      || isVisibleCharacterMarker(node)
      || (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR')
    ) {
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

    if (isVisibleCharacterMarker(node)) {
      const parent = node.parentNode || el
      const index = Math.max(0, getNodeIndex(node))
      if (normalizedOffset <= currentOffset) {
        resolved = { node: parent, offset: index }
        return true
      }
      currentOffset += 1
      fallback = { node: parent, offset: index + 1 }
      if (normalizedOffset <= currentOffset) {
        resolved = fallback
        return true
      }
      return false
    }

    if (node.nodeType === Node.TEXT_NODE) {
      const domStart = getSerializableTextNodeDomStart(node)
      const textLength = getSerializableTextNodeValue(node).length
      if (currentOffset + textLength >= normalizedOffset) {
        resolved = {
          node,
          offset: domStart + Math.max(0, Math.min(textLength, normalizedOffset - currentOffset)),
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

function getPropTargetStateText(): string {
  // 编辑对象永远是纯译文。带标签版式译文（target_layout_text）只读，走独立的样式
  // 预览/标签编辑通道，绝不进入可编辑 DOM——这是之前“显示一会就消失/编辑卡死”的根源。
  return props.pendingRevision?.after_text ?? props.segment.target_text ?? ''
}

function getPropTargetStateHtml(): string | null {
  return props.segment.target_html || null
}

function getActiveLocalEditorEcho(): LocalEditorEcho | null {
  const echo = localEditorEcho.value
  return echo?.sentenceId === segmentKey.value ? echo : null
}

function setLocalEditorEcho(text: string, html: string | null) {
  localEditorEcho.value = {
    sentenceId: segmentKey.value,
    text,
    html: html || null,
  }
}

function clearLocalEditorEchoIfSynced() {
  const echo = getActiveLocalEditorEcho()
  if (!echo) {
    return
  }
  if (echo.text === getPropTargetStateText() && echo.html === getPropTargetStateHtml()) {
    localEditorEcho.value = null
  }
}

function clearLocalEditorEcho() {
  localEditorEcho.value = null
}

function getTargetStateText(): string {
  return getActiveLocalEditorEcho()?.text ?? getPropTargetStateText()
}

function getTargetStateHtml(): string | null {
  const echo = getActiveLocalEditorEcho()
  return echo ? echo.html : getPropTargetStateHtml()
}

function getCurrentEditorHtml(): string | null {
  if (!editorRef.value) {
    return getTargetStateHtml()
  }
  const html = serializeEditorContentWithFormat(editorRef.value)
  return shouldPersistEditorHtml(html) ? html : null
}

function commitEditorContent(): CommittedEditorContent | null {
  if (!editorRef.value || isApplyingHistory.value || isComposing.value) {
    return null
  }

  const text = serializeEditorContent(editorRef.value)
  const html = getCurrentEditorHtml()
  const currentText = getTargetStateText()
  const currentHtml = getTargetStateHtml()

  const shouldSyncInheritedHtml = currentHtml === null && html !== null
  if (text !== currentText || ((editorDirtySinceFocus.value || shouldSyncInheritedHtml) && html !== currentHtml)) {
    setLocalEditorEcho(text, html)
    emit('update', segmentKey.value, text, html || undefined)
  }

  return {
    sentenceId: segmentKey.value,
    text,
    html,
  }
}

function focusTargetEditorAtEnd(): boolean {
  const editor = editorRef.value
  if (!editor) {
    return false
  }

  editor.focus({ preventScroll: true })
  const caretOffset = getCurrentEditorText().length
  restoreSerializableCaretPosition(editor, caretOffset)
  lastTargetSelectionRange.value = { start: caretOffset, end: caretOffset }
  return true
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
  return serializeEditorContent(editorRef.value) === getTargetStateText()
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
  editorDirtySinceFocus.value = false
  emit('focus', segmentKey.value)
  cacheTargetSelectionFromDom()
}

function handleBlur() {
  clearRevisionRerenderTimer()
  cacheTargetSelectionFromDom()
  commitEditorContent()
  isFocused.value = false
  editorDirtySinceFocus.value = false
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

function handleSourceCellMouseDown(event: MouseEvent) {
  handleSelectMouseDown(event)
  if (isSegmentMultiSelectEvent(event) || props.disabled) {
    return
  }
  // 非激活行点击原文时，先标记待聚焦，激活后把光标落到原文区
  if (!props.active) {
    pendingSourceFocus.value = true
    pendingSourceFocusPoint.value = { x: event.clientX, y: event.clientY }
  }
}

function hasExpandedSelectionWithin(element: HTMLElement): boolean {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
    return false
  }

  const range = selection.getRangeAt(0)
  return element.contains(range.startContainer) && element.contains(range.endContainer)
}

function handleSourceCellClick(event: MouseEvent) {
  resetHistoryGroup()
  if (isSegmentMultiSelectEvent(event)) {
    emit('ctrlClick', segmentKey.value, event)
    return
  }
  const sourceCell = event.currentTarget
  if (sourceCell instanceof HTMLElement && hasExpandedSelectionWithin(sourceCell)) {
    pendingSourceFocus.value = false
    pendingSourceFocusPoint.value = null
    return
  }
  emit('activateSource', segmentKey.value)
  if (props.active) {
    void nextTick(() => {
      focusSourceEditorAtPoint(event.clientX, event.clientY)
      emitSourceCaret()
    })
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

function getSourceCaretOffset(editor: HTMLElement): number {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) return 0
  if (!editor.contains(selection.anchorNode)) return 0

  const range = selection.getRangeAt(0)
  const preCaretRange = range.cloneRange()
  preCaretRange.selectNodeContents(editor)
  preCaretRange.setEnd(range.startContainer, range.startOffset)

  // 可见换行标记渲染为 "¶\n"，长度需折叠为 1；·/→ 与原字符等长
  return preCaretRange.toString().replace(/¶\n/g, '\n').replace(/¶/g, '\n').length
}

function emitSourceCaret() {
  const editor = sourceEditorRef.value
  if (!editor || !isSourceFocused.value) return
  const offset = getSourceCaretOffset(editor)
  emit('sourceCaretChange', segmentKey.value, offset)
}

function focusSourceEditorAtPoint(clientX?: number, clientY?: number) {
  const editor = sourceEditorRef.value
  if (!editor || props.disabled) return
  editor.focus({ preventScroll: true })
  if (clientX === undefined || clientY === undefined) {
    return
  }
  // 尽量按点击坐标放置光标（浏览器 caretRangeFromPoint）
  const doc = document as Document & {
    caretRangeFromPoint?: (x: number, y: number) => Range | null
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null
  }
  let range: Range | null = null
  if (typeof doc.caretRangeFromPoint === 'function') {
    range = doc.caretRangeFromPoint(clientX, clientY)
  } else if (typeof doc.caretPositionFromPoint === 'function') {
    const pos = doc.caretPositionFromPoint(clientX, clientY)
    if (pos) {
      range = document.createRange()
      range.setStart(pos.offsetNode, pos.offset)
      range.collapse(true)
    }
  }
  if (range && editor.contains(range.startContainer)) {
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
  }
}

function handleProjectSyncToggle() {
  if (props.disabled) {
    return
  }
  emit('toggleProjectSync', segmentKey.value, !props.segment.project_sync_disabled)
}

function handleCopySourceToTargetClick() {
  if (props.disabled) {
    return
  }
  resetHistoryGroup()
  emit('copySourceToTarget', segmentKey.value)
}

function handleSourceFocus() {
  isSourceFocused.value = true
  emit('focus', segmentKey.value)
  emitSourceCaret()
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
  // 方向键等会改变光标，下一帧同步偏移
  void nextTick(() => emitSourceCaret())
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
  if (inputEvent.inputType.startsWith('delete')) {
    clearRevisionRerenderTimer()
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
  // 始终只编辑真实内容。¶ 属于显示层，状态同步后再按 showVisibleChars 渲染，
  // 避免相邻换行被写成不可编辑的 “¶¶” DOM。
  document.execCommand('insertLineBreak')
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
    if (event.ctrlKey || event.metaKey || event.shiftKey) {
      event.preventDefault()
      insertEditorLineBreak()
      return
    }

    event.preventDefault()
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
  clearRevisionRerenderTimer()
  editorDirtySinceFocus.value = true
  cacheTargetSelectionFromDom()

  // 检查是否有格式标签
  const cleanHtml = serializeEditorContentWithFormat(editorRef.value)
  const shouldPersistHtml = shouldPersistEditorHtml(cleanHtml)

  // 获取纯文本内容用于保存
  const text = serializeEditorContent(editorRef.value)

  // Persist HTML when it carries formats or an explicit plain-format override.
  if (shouldPersistHtml) {
    setLocalEditorEcho(text, cleanHtml)
    emit('update', segmentKey.value, text, cleanHtml)
    clearExplicitPlainFormatRequest()
    return
  }

  // 没有格式标签，只传递纯文本
  setLocalEditorEcho(text, null)
  emit('update', segmentKey.value, text)
  clearExplicitPlainFormatRequest()
}

function getEditorSelectionRange(): Range | null {
  const editor = editorRef.value
  const selection = window.getSelection()
  if (!editor || !selection || selection.rangeCount === 0 || selection.isCollapsed) {
    return null
  }

  const range = selection.getRangeAt(0)
  if (!editor.contains(range.startContainer) || !editor.contains(range.endContainer)) {
    return null
  }

  return range
}

function writeCleanRevisionSelectionToClipboard(event: ClipboardEvent, range: Range): boolean {
  const clipboardData = event.clipboardData
  if (!clipboardData) {
    return false
  }

  const fragment = range.cloneContents()
  const text = serializeEditorContent(fragment)
  const html = serializeEditorContentWithFormat(fragment)
  event.preventDefault()
  clipboardData.clearData()
  clipboardData.setData('text/plain', text)
  clipboardData.setData('text/html', html)
  return true
}

function handleCopy(event: ClipboardEvent) {
  if (!hasPendingRevision.value) {
    return
  }

  const range = getEditorSelectionRange()
  if (!range) {
    return
  }

  writeCleanRevisionSelectionToClipboard(event, range)
}

function handleCut(event: ClipboardEvent) {
  if (!hasPendingRevision.value) {
    return
  }

  const range = getEditorSelectionRange()
  if (!range || !writeCleanRevisionSelectionToClipboard(event, range)) {
    return
  }

  if (props.disabled || isApplyingHistory.value || isComposing.value) {
    return
  }

  recordUndoSnapshot(true, { force: true, inputType: 'deleteByCut' })
  document.execCommand('delete')
  handleInput()
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
  scheduleRevisionRerender()
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

function hasExplicitTargetHtmlOverride(): boolean {
  return getTargetStateHtml() !== null && getTargetStateHtml() !== undefined
}

function hasExplicitPlainFormatRequest(): boolean {
  return editorRef.value?.dataset[EXPLICIT_PLAIN_FORMAT_DATA_KEY] === 'true'
}

function clearExplicitPlainFormatRequest() {
  const editor = editorRef.value
  if (!editor) return
  delete editor.dataset[EXPLICIT_PLAIN_FORMAT_DATA_KEY]
}

function shouldPersistEditorHtml(html: string): boolean {
  return hasSerializableFormatTags(html) || hasExplicitTargetHtmlOverride() || hasExplicitPlainFormatRequest()
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
 * 原文列展示专用清理：在基础格式标签之外，额外保留 span 上的颜色/字号/字体族等
 * 内联样式，使原文列能真实反映 PPTX 同一文本框内 run 级的字体/字号/颜色差异。
 * 仅用于只读展示，不影响译文编辑所用的基础格式模型（sanitizeHtml）。
 */
function sanitizeSourceHtml(html: string): string {
  if (typeof document === 'undefined') {
    return escapeHtml(html)
  }

  const tempDiv = document.createElement('div')
  tempDiv.innerHTML = html

  function pickSafeStyles(el: HTMLElement): string {
    const style = el.style
    const declarations: string[] = []
    const color = style.color.trim()
    if (color) declarations.push(`color:${color}`)
    const background = style.backgroundColor.trim()
    if (background) declarations.push(`background-color:${background}`)
    const fontSize = style.fontSize.trim()
    if (fontSize) declarations.push(`font-size:${fontSize}`)
    const fontFamily = style.fontFamily.trim()
    if (fontFamily) declarations.push(`font-family:${fontFamily}`)
    return declarations.join(';')
  }

  function processNode(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) {
      return escapeHtml(node.textContent || '')
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return ''
    }

    const el = node as HTMLElement
    const tagName = el.tagName.toLowerCase()
    if (DROPPED_HTML_TAGS.has(tagName)) {
      return ''
    }
    if (tagName === 'br') {
      return '\n'
    }

    let content = Array.from(el.childNodes).map(processNode).join('')

    const formatTags: BasicFormatTag[] = []
    const normalizedBasicTag = normalizeBasicFormatTag(tagName)
    if (normalizedBasicTag) {
      formatTags.push(normalizedBasicTag)
    }
    formatTags.push(...getStyleFormatTags(el))
    if (formatTags.length > 0) {
      content = wrapWithBasicFormats(content, formatTags)
    }

    const safeStyles = pickSafeStyles(el)
    if (safeStyles) {
      content = `<span style="${safeStyles}">${content}</span>`
    }
    return content
  }

  return processNode(tempDiv)
}

/**
 * 只把句段编辑中允许渲染的基础格式规范化为内部标签名。
 */
function collectBasicSourceFormatRuns(sourceHtml: string): BasicFormatRun[] {
  if (typeof document === 'undefined') {
    return []
  }

  const template = document.createElement('template')
  template.innerHTML = sanitizeHtml(sourceHtml)
  const textRuns: BasicFormatRun[] = []

  function walk(node: Node, inheritedTags: BasicFormatTag[]) {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent || ''
      if (text) {
        const tags = normalizeFormatTagList(inheritedTags)
        const previous = textRuns[textRuns.length - 1]
        if (previous && formatTagKey(previous.tags) === formatTagKey(tags)) {
          previous.text += text
        } else {
          textRuns.push({ text, tags })
        }
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
  return textRuns
}

/**
 * 将源文档字符格式保守地投影到译文。
 * 文本未变化时逐 run 保留；文本变化后只继承全段共同格式，并迁移唯一匹配的
 * 带格式片段，避免把首个 run 的下划线或粗体扩散到整句。
 */
function projectSourceFormatsToTarget(sourceHtml: string, targetText: string): string | null {
  if (!targetText || /<\s*a\b/i.test(sourceHtml)) {
    return null
  }

  const sourceRuns = collectBasicSourceFormatRuns(sourceHtml)
  if (!sourceRuns.some((run) => run.tags.length > 0)) {
    return null
  }

  const sourceText = sourceRuns.map((run) => run.text).join('')
  if (
    sourceText === targetText
    || collapseProjectionWhitespace(sourceText) === collapseProjectionWhitespace(targetText)
  ) {
    return sourceRuns
      .map((run) => wrapWithBasicFormats(escapeHtml(run.text), run.tags))
      .join('')
  }

  const meaningfulRuns = sourceRuns.filter((run) => run.text.trim())
  const commonTags = BASIC_FORMAT_RENDER_ORDER.filter((tag) => (
    meaningfulRuns.length > 0 && meaningfulRuns.every((run) => run.tags.includes(tag))
  ))
  const targetTags = Array.from(
    { length: targetText.length },
    () => new Set<BasicFormatTag>(commonTags),
  )

  const candidates = sourceRuns
    .filter((run) => run.tags.length > 0 && (run.text.match(/[\p{L}\p{N}]/gu) || []).length >= 2)
    .sort((left, right) => right.text.length - left.text.length)

  candidates.forEach((run) => {
    const matches = findWhitespaceFlexibleMatches(targetText, run.text)
    if (matches.length !== 1) {
      return
    }
    const [match] = matches
    for (let index = match.start; index < match.end; index += 1) {
      run.tags.forEach((tag) => targetTags[index].add(tag))
    }
  })

  if (!targetTags.some((tags) => tags.size > 0)) {
    return null
  }
  return renderTextWithFormatSets(targetText, targetTags)
}

function collapseProjectionWhitespace(text: string): string {
  return text.trim().replace(/\s+/gu, ' ')
}

function findWhitespaceFlexibleMatches(text: string, candidate: string): EditorTextRange[] {
  const stripped = candidate.trim()
  if (!stripped) {
    return []
  }
  const pattern = stripped
    .split(/\s+/u)
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('\\s+')
  const expression = new RegExp(pattern, 'gu')
  return Array.from(text.matchAll(expression), (match) => ({
    start: match.index,
    end: match.index + match[0].length,
  }))
}

function renderTextWithFormatSets(text: string, formatSets: Array<Set<BasicFormatTag>>): string {
  const parts: string[] = []
  let start = 0
  while (start < text.length) {
    const currentTags = normalizeFormatTagList(Array.from(formatSets[start]))
    const currentKey = formatTagKey(currentTags)
    let end = start + 1
    while (
      end < text.length
      && formatTagKey(normalizeFormatTagList(Array.from(formatSets[end]))) === currentKey
    ) {
      end += 1
    }
    parts.push(wrapWithBasicFormats(escapeHtml(text.slice(start, end)), currentTags))
    start = end
  }
  return parts.join('')
}

function formatTagKey(tags: BasicFormatTag[]): string {
  return normalizeFormatTagList(tags).join('|')
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

function clearRevisionRerenderTimer() {
  if (revisionRerenderTimer === null) {
    return
  }
  clearTimeout(revisionRerenderTimer)
  revisionRerenderTimer = null
}

function canSkipFocusedEditorStateSync(editor: HTMLElement): boolean {
  if (!isFocused.value || hasRenderedEditorDecorations() || editorHasDecorationNodes(editor)) {
    return false
  }
  return serializeEditorContent(editor) === getTargetStateText()
}

function scheduleRevisionRerender() {
  clearRevisionRerenderTimer()
  if (!isFocused.value || isApplyingHistory.value || isComposing.value) {
    return
  }
  const editor = editorRef.value
  if (editor && canSkipFocusedEditorStateSync(editor)) {
    return
  }

  revisionRerenderTimer = setTimeout(() => {
    revisionRerenderTimer = null
    if (!isFocused.value || isApplyingHistory.value || isComposing.value) {
      return
    }
    const currentEditor = editorRef.value
    if (currentEditor && canSkipFocusedEditorStateSync(currentEditor)) {
      return
    }
    syncEditorHtmlFromState(true)
  }, REVISION_RERENDER_DEBOUNCE_MS)
}

function syncEditorHtmlFromState(preserveCaret: boolean) {
  const editor = editorRef.value
  if (!editor || isApplyingHistory.value || isComposing.value) {
    return
  }

  if (canSkipFocusedEditorStateSync(editor)) {
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

function handleDocumentClickForTagPopover(event: MouseEvent) {
  if (!tagPopover.value) return
  const target = event.target as HTMLElement | null
  if (target?.closest('.segment-row__tag-popover') || target?.closest('.segment-row__target-preview')) {
    return
  }
  tagPopover.value = null
}

onMounted(() => {
  if (editorRef.value) {
    editorRef.value.innerHTML = editorHtmlContent.value
  }
  document.addEventListener('mousedown', handleDocumentClickForTagPopover)
})

onBeforeUnmount(() => {
  clearRevisionRerenderTimer()
  document.removeEventListener('mousedown', handleDocumentClickForTagPopover)
})

watch(
  () => props.tagEditMode,
  (enabled) => {
    if (!enabled) {
      tagPopover.value = null
    }
  },
)

watch(
  () => activeQAIssues.value
    .map((issue) => `${issue.id}:${issue.offset}:${issue.length}:${issue.target_text_hash}`)
    .join('|'),
  () => syncFocusedTargetHighlightsFromState(),
  { flush: 'post' },
)

watch(
  () => props.segment.sentence_id,
  () => {
    lastTargetSelectionRange.value = null
    editorDirtySinceFocus.value = false
    clearLocalEditorEcho()
    clearEditorHistory()
  }
)

watch(
  () => [props.segment.target_text, props.segment.target_html, props.pendingRevision?.after_text] as const,
  () => {
    clearLocalEditorEchoIfSynced()
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
    clearLocalEditorEchoIfSynced()
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
      const editor = editorRef.value
      if (editor && canSkipFocusedEditorStateSync(editor)) {
        clearRevisionRerenderTimer()
        return
      }
      if (
        editor
        && !hasRenderedEditorDecorations()
        && editorHasDecorationNodes(editor)
        && serializeEditorContent(editor) === getTargetStateText()
      ) {
        clearRevisionRerenderTimer()
        syncEditorHtmlFromState(true)
        return
      }
      scheduleRevisionRerender()
      return
    }
    clearRevisionRerenderTimer()
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
  focusTargetEditorAtEnd,
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
        if (pendingSourceFocus.value) {
          const point = pendingSourceFocusPoint.value
          pendingSourceFocus.value = false
          pendingSourceFocusPoint.value = null
          focusSourceEditorAtPoint(point?.x, point?.y)
          emitSourceCaret()
        }
      })
    } else {
      pendingSourceFocus.value = false
      pendingSourceFocusPoint.value = null
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
    :class="[statusClass, parityClass, { 'is-active': active, 'is-selected': selected, 'has-pending-revision': hasPendingRevision, 'is-empty-target': isEmptyTarget, 'is-proofreading-changed': isProofreadingChanged }]"
    :id="`segment-${segmentKey}`"
    data-testid="segment-row"
    :data-sentence-id="segmentKey"
    :data-has-pending-revision="hasPendingRevision ? 'true' : 'false'"
    role="group"
    :aria-label="`segment ${index + 1}`"
  >
    <div class="segment-row__meta">
      <span class="segment-row__index">{{ index + 1 }}</span>
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
    </div>

    <div
      class="segment-row__cell segment-row__cell--source"
      @mousedown="handleSourceCellMouseDown"
      @click="handleSourceCellClick"
    >
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
          :contenteditable="!disabled"
          :dir="sourceDirection"
          :lang="sourceLanguage || undefined"
          tabindex="0"
          spellcheck="false"
          @focus="handleSourceFocus"
          @blur="handleSourceBlur"
          @input="handleSourceInput"
          @keydown="handleSourceKeydown"
          @beforeinput="handleSourceBeforeInput"
          @mouseup="emitSourceCaret"
          @keyup="emitSourceCaret"
        ></div>
        <div
          v-else
          class="segment-row__text"
          :dir="sourceDirection"
          :lang="sourceLanguage || undefined"
          v-html="sourceHtmlContent"
        ></div>
      </div>
    </div>

    <div v-if="originalTargetText !== null" class="segment-row__cell segment-row__cell--original-target">
      <div v-if="isProofreadingChanged" class="segment-row__original-target-label">
        <strong>已修订</strong>
      </div>
      <div class="segment-row__original-target-text" :dir="targetDirection" :lang="targetLanguage || undefined">
        {{ originalTargetText || '（空）' }}
      </div>
    </div>

    <div class="segment-row__cell segment-row__cell--target" :class="{ 'is-pending': hasPendingRevision }">
      <div
        class="segment-row__editor-shell"
        :class="{ 'is-focused': isFocused, 'is-disabled': disabled, 'has-revision': hasVisibleRevisionMarks }"
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
        <button
          v-if="originalTargetText === null"
          class="segment-row__copy-source-button"
          type="button"
          data-testid="segment-copy-source-to-target"
          title="用原文填充译文"
          aria-label="用原文填充译文"
          :disabled="disabled"
          @mousedown.stop
          @click.stop="handleCopySourceToTargetClick"
        >
          <Copy :size="13" aria-hidden="true" />
        </button>
        <button
          class="segment-row__line-break-button"
          type="button"
          title="插入换行"
          aria-label="插入换行"
          :disabled="disabled"
          @mousedown.prevent.stop
          @click.stop="insertEditorLineBreak"
        >
          <CornerDownLeft :size="13" aria-hidden="true" />
        </button>
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
            { 'is-focused': isFocused, 'has-revision': hasVisibleRevisionMarks },
            revisionAuthorClass,
          ]"
          :style="revisionColorStyle"
          :contenteditable="!disabled"
          :dir="targetDirection"
          :lang="targetLanguage || undefined"
          tabindex="0"
          data-testid="segment-target-editor"
          :data-revision-visible="hasVisibleRevisionMarks ? 'true' : 'false'"
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
          v-show="!showTargetPreview"
          @paste="handlePaste"
          @copy="handleCopy"
          @cut="handleCut"
        />
        <div
          ref="targetPreviewRef"
          v-show="showTargetPreview"
          class="segment-row__target-preview"
          :class="{ 'is-tag-edit-mode': tagEditMode }"
          data-testid="segment-target-preview"
          :dir="targetDirection"
          :lang="targetLanguage || undefined"
          :aria-label="`translation style preview for segment ${index + 1}`"
          tabindex="0"
          @click="handleTargetPreviewClick"
          @focus="focusTargetPreview"
          @mouseup="handleTargetPreviewMouseUp"
          v-html="targetPreviewHtml"
        ></div>
      </div>
      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="tagPopover"
        class="segment-row__tag-popover"
        :style="{ left: tagPopover.x + 'px', top: tagPopover.y + 'px' }"
        data-testid="segment-tag-popover"
        @mousedown.prevent
      >
        <template v-if="availableStyleTagIds.length > 0">
          <button
            v-for="tagId in availableStyleTagIds"
            :key="tagId"
            class="segment-row__tag-popover-item"
            type="button"
            :title="`标注为样式 ⟦${tagId}⟧`"
            @click="applyAddStyleTag(tagId)"
          >
            <span v-html="styleTagPreviewLabelHtml(tagId)"></span>
          </button>
        </template>
        <button
          v-if="tagPopover.hasExistingTag"
          class="segment-row__tag-popover-item segment-row__tag-popover-item--remove"
          type="button"
          title="删除选中范围内的样式标签"
          @click="applyRemoveStyleTag"
        >
          删除标签
        </button>
        <button
          class="segment-row__tag-popover-item segment-row__tag-popover-item--close"
          type="button"
          title="关闭"
          @click="closeTagPopover"
        >
          ×
        </button>
      </div>
    </Teleport>

    <div
      v-if="proofreadingSuggestion !== null"
      class="segment-row__cell segment-row__cell--suggestion"
      :class="`is-${proofreadingSuggestion.tone}`"
      :title="proofreadingSuggestion.text"
    >
      <span class="segment-row__suggestion-label">{{ proofreadingSuggestion.label }}</span>
      <span class="segment-row__suggestion-text">{{ proofreadingSuggestion.text }}</span>
    </div>

    <div v-else class="segment-row__cell segment-row__cell--state" :title="stateCellTitle">
      <span
        v-if="segment.status === 'confirmed' && !isProjectSynced"
        class="segment-row__confirm-mark"
        aria-label="已确认"
      >√</span>
      <span
        v-if="showMatchRate"
        class="segment-row__match-rate"
        :class="`segment-row__match-rate--${matchRateTone}`"
        :title="statusMeta.label"
      >
        {{ matchRateLabel }}
      </span>
      <span
        v-if="showStatusTag && segment.status !== 'confirmed' && !showMatchRate"
        class="segment-row__compact-tag segment-row__compact-tag--status"
      >
        {{ statusMeta.label }}
      </span>
      <span v-if="showSourceTag" class="segment-row__compact-tag" :class="sourceClass" :title="sourceTitle">
        {{ compactSourceLabel }}
      </span>
      <span
        v-if="hasPendingRevision"
        class="segment-row__compact-tag segment-row__tag--revision"
        data-testid="segment-revision-tag"
        :title="`修订来源：${revisionSourceMeta.label}`"
      >
        待审校
      </span>
    </div>

    <div v-if="proofreadingSuggestion === null" class="segment-row__cell segment-row__cell--workflow">
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

.segment-row__cell--original-target {
  min-width: 0;
  padding: 12px;
  background: #f8fafc;
  color: var(--ink-700);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.segment-row__original-target-label {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-bottom: 5px;
  color: var(--ink-500);
  font-size: 11px;
  font-weight: 700;
}

.segment-row__original-target-label strong {
  padding: 2px 6px;
  border-radius: 999px;
  background: #dbeafe;
  color: #0759b8;
  font-size: 10px;
  line-height: 1.3;
}

.segment-row__original-target-text {
  line-height: 1.55;
}

.segment-row__cell--target.is-pending {
  box-shadow: inset 2px 0 0 rgba(0, 122, 204, 0.36);
}

.segment-row.is-proofreading-changed .segment-row__cell--target {
  background: linear-gradient(0deg, rgba(219, 234, 254, 0.38), rgba(239, 246, 255, 0.5));
  box-shadow: inset 3px 0 0 #3b82f6;
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

.segment-row__text {
  font-size: var(--segment-editor-source-font-size, 13px);
  line-height: var(--segment-editor-source-line-height, 1.45);
  cursor: text;
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

.segment-row__copy-source-button,
.segment-row__line-break-button {
  flex: 0 0 auto;
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  min-width: 24px;
  margin-top: 8px;
  padding: 0;
  border: 1px solid rgba(34, 127, 88, 0.28);
  border-radius: 4px;
  background: rgba(34, 127, 88, 0.08);
  color: #146c49;
  line-height: 1;
  box-shadow: none;
}

.segment-row__copy-source-button:hover:not(:disabled),
.segment-row__copy-source-button:focus-visible,
.segment-row__line-break-button:hover:not(:disabled),
.segment-row__line-break-button:focus-visible {
  border-color: rgba(13, 122, 104, 0.46);
  background: rgba(13, 122, 104, 0.14);
  color: #0b6658;
  outline: none;
}

.segment-row__line-break-button {
  border-color: rgba(91, 115, 132, 0.28);
  background: rgba(91, 115, 132, 0.08);
  color: #526574;
}

.segment-row__copy-source-button:disabled,
.segment-row__line-break-button:disabled {
  cursor: not-allowed;
  opacity: 0.42;
}

.segment-row__cell--state,
.segment-row__cell--workflow,
.segment-row__cell--suggestion {
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

.segment-row__cell--suggestion {
  align-items: flex-start;
  justify-content: center;
  flex-direction: column;
  gap: 5px;
  padding: 7px 10px;
  background: #fffdf5;
  color: #5f4b18;
  line-height: 1.4;
}

.segment-row__cell--suggestion.is-unchanged {
  background: #f6fbf8;
  color: #426257;
}

.segment-row__cell--suggestion.is-error {
  background: #fff5f5;
  color: #9f2d2d;
}

.segment-row__cell--suggestion.is-pending {
  background: #f7f9fb;
  color: #667784;
}

.segment-row__suggestion-label {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(197, 143, 25, 0.13);
  color: inherit;
  font-size: 10px;
  font-weight: 700;
  line-height: 1.2;
}

.segment-row__cell--suggestion.is-unchanged .segment-row__suggestion-label {
  background: rgba(22, 101, 52, 0.1);
}

.segment-row__cell--suggestion.is-error .segment-row__suggestion-label {
  background: rgba(190, 24, 93, 0.1);
}

.segment-row__suggestion-text {
  display: -webkit-box;
  width: 100%;
  overflow: hidden;
  color: inherit;
  font-size: 12px;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.segment-row.is-active .segment-row__suggestion-text {
  display: block;
  overflow: visible;
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
  color: #166534;
  font-size: 18px;
  font-weight: 800;
  line-height: 1;
}

.segment-row__match-rate {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  max-width: 100%;
  min-height: 18px;
  padding: 0 4px;
  border-radius: 2px;
  background: #4fa873;
  color: #ffffff;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.segment-row__match-rate--exact,
.segment-row__match-rate--high {
  background: #4fa873;
}

.segment-row__match-rate--medium {
  background: #d8b74e;
}

.segment-row__match-rate--low {
  background: #c95c62;
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
  font-size: var(--segment-editor-target-font-size, 15px);
  line-height: var(--segment-editor-target-line-height, 1.58);
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
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #146c49;
  line-height: 1;
  cursor: pointer;
}

.segment-row__sync-toggle:hover:not(:disabled),
.segment-row__sync-toggle:focus-visible {
  background: transparent;
  color: #084c35;
  outline: 1px solid currentColor;
  outline-offset: 1px;
}

.segment-row__sync-toggle.is-disabled-sync {
  background: transparent;
  color: #526574;
}

.segment-row__sync-toggle:disabled {
  cursor: not-allowed;
  opacity: 0.42;
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
  font-size: var(--segment-editor-target-font-size, 15px);
  line-height: var(--segment-editor-target-line-height, 1.58);
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
  user-select: none;
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
  font-size: var(--segment-editor-source-font-size, 13px);
  line-height: var(--segment-editor-source-line-height, 1.45);
  color: var(--text-primary);
  caret-color: #0b5f52;
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  overflow: auto;
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.12);
  cursor: text;
}

.segment-row__source-editor.is-focused {
  border-color: var(--brand-700, #0d7a68);
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.18);
  caret-color: #063d35;
}

.segment-row__source-editor.is-readonly {
  border-color: var(--border-muted, #e2e8f0);
  background: var(--surface-panel, #fff);
  box-shadow: none;
  cursor: text;
  caret-color: #0b5f52;
}

.segment-row__source-editor.is-readonly.is-focused {
  border-color: var(--brand-400, #5bb5a6);
  background: #f3fbf9;
  box-shadow: 0 0 0 2px rgba(13, 122, 104, 0.16);
  caret-color: #063d35;
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

/* 译文只读样式预览：只读、非编辑框，展示逐词样式（开关开+非编辑态时替代编辑框的
   视觉呈现）。点击/聚焦转发到真正的编辑框（focusTargetPreview），编辑内容始终纯净。 */
.segment-row__target-preview {
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 76px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 5px;
  font-size: var(--segment-editor-target-font-size, 15px);
  line-height: var(--segment-editor-target-line-height, 1.58);
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  overflow: auto;
  cursor: text;
}

/* 标签编辑模式：预览区改为“选词”光标，提示可以拖选文字加/删样式标签 */
.segment-row__target-preview.is-tag-edit-mode {
  cursor: text;
  outline: 1px dashed rgba(13, 122, 104, 0.35);
  outline-offset: -1px;
}

/* 手动标签选择弹出菜单：Teleport 到 body，跟随选区定位（fixed 坐标） */
.segment-row__tag-popover {
  position: fixed;
  z-index: 2000;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  background: #ffffff;
  border: 1px solid #d8e2e6;
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(15, 40, 45, 0.18);
  transform: translate(-50%, -100%) translateY(-8px);
  white-space: nowrap;
}

.segment-row__tag-popover-item {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 30px;
  height: 28px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: #f3f7f9;
  color: #23805f;
  font-size: 12px;
  cursor: pointer;
}

.segment-row__tag-popover-item:hover {
  background: #e6f4ea;
  border-color: #23805f;
}

.segment-row__tag-popover-item--remove {
  color: #c0392b;
}

.segment-row__tag-popover-item--remove:hover {
  background: #fdecec;
  border-color: #c0392b;
}

.segment-row__tag-popover-item--close {
  color: #9aaab1;
  font-weight: 700;
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
