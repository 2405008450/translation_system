<script setup lang="ts">
import axios from 'axios'
import {
  ArrowLeft,
  ArrowRight,
  Bold,
  Bot,
  BrushCleaning,
  Check,
  ChevronDown,
  ChevronUp,
  CircleHelp,
  Columns,
  Combine,
  Copy,
  Download,
  ExternalLink,
  FileCheck,
  FileText,
  Flag,
  History,
  Info,
  Italic,
  Languages,
  Loader2,
  MessageSquare,
  Redo2,
  Save,
  Search,
  ShieldCheck,
  Sigma,
  Split,
  SquarePen,
  Strikethrough,
  Subscript,
  Superscript,
  Type,
  Underline,
  Undo2,
  Upload,
  X,
} from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import Modal from '../components/base/Modal.vue'
import IssueMarkerDialog from '../components/IssueMarkerDialog.vue'
import NotesPanel from '../components/NotesPanel.vue'
import Pagination from '../components/Pagination.vue'
import PreviewPanel from '../components/PreviewPanel.vue'
import ResourceImportDialog from '../components/ResourceImportDialog.vue'
import SegmentEditorRow from '../components/SegmentEditorRow.vue'
import SplitPreviewPanel from '../components/SplitPreviewPanel.vue'
import TMMatchPanel from '../components/TMMatchPanel.vue'
import VirtualList from '../components/VirtualList.vue'
import WorkbenchHistoryPanel from '../components/WorkbenchHistoryPanel.vue'
import WorkbenchMatchPanel from '../components/WorkbenchMatchPanel.vue'
import WorkbenchTermsPanel from '../components/WorkbenchTermsPanel.vue'
import { http } from '../api/http'
import { useConfirm } from '../composables/useConfirm'
import { usePageHeader } from '../composables/usePageHeader'
import { useToast } from '../composables/useToast'
import { useWorkbenchShortcuts } from '../composables/useWorkbenchShortcuts'
import { getTaskExportFormatLabel } from '../constants/taskFiles'
import { llmModelOptions, llmProviderOptions, llmScopeOptions } from '../constants/llm'
import { formatLanguagePair } from '../constants/languages'
import { isProgressComplete } from '../utils/progress'
import { useAuthStore } from '../stores/auth'
import { useCommentStore, type CommentWindowQuery } from '../stores/comment'
import { useSegmentStore } from '../stores/segment'
import type {
  CommentAnchorDraft,
  CommentCreatePayload,
  CommentStatus,
  GuidelineTemplateSummary,
  IssueMarker,
  LLMProvider,
  LLMTranslateScope,
  SaveToTMResult,
  Segment,
  TMCollection,
  TermBase,
  TermEntryRecord,
} from '../types/api'
import { buildDocumentPreviewHtml } from '../utils/documentPreview'

const props = defineProps<{
  id: string
  standalone?: boolean
}>()

type ToolKey = 'source-preview' | 'target-preview' | 'split-preview' | 'match-info' | 'terms' | 'notes' | 'history'
type ResourceImportTab = 'tm' | 'term'
type SaveToTMScope = 'translated' | 'confirmed'
type SaveToTMTargetMode = 'new' | 'existing'
type SegmentDisplayScope = 'all' | 'exact_only' | 'fuzzy_only' | 'none_only' | 'confirmed_only' | 'empty_target'
type RevisionMenuKind = 'track' | 'accept' | 'reject'
type SegmentEditorRowPublic = ComponentPublicInstance & {
  undoEditorChange: () => boolean
  redoEditorChange: () => boolean
}
type SaveToTMPayload = {
  collection_mode: SaveToTMTargetMode
  collection_id?: string
  collection_name?: string
  scope: SaveToTMScope
}

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const commentStore = useCommentStore()
const segmentStore = useSegmentStore()
const toast = useToast()
const confirm = useConfirm()
const { t } = useI18n()

const virtualListRef = ref<{
  scrollToIndex: (index: number, align?: ScrollLogicalPosition) => Promise<boolean>
  focusIndex: (index: number, selector?: string, align?: ScrollLogicalPosition) => Promise<boolean>
} | null>(null)
const segmentEditorRowRefs = new Map<string, SegmentEditorRowPublic>()

const sidecarRef = ref<HTMLElement | null>(null)
const sidecarWidth = ref<number | null>(null)
const isResizing = ref(false)

const sidecarWidthStyle = computed(() => {
  if (sidecarWidth.value === null) return {}
  return { width: `${sidecarWidth.value}px` }
})

function startResize(event: MouseEvent) {
  event.preventDefault()
  isResizing.value = true
  const startX = event.clientX
  const startWidth = sidecarRef.value?.offsetWidth || 400

  function onMouseMove(e: MouseEvent) {
    const delta = startX - e.clientX
    const newWidth = Math.max(200, Math.min(startWidth + delta, window.innerWidth * 0.75))
    sidecarWidth.value = newWidth
  }

  function onMouseUp() {
    isResizing.value = false
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }

  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}

const pageError = ref('')
const llmScope = ref<LLMTranslateScope>('all')
const llmProvider = ref<LLMProvider>('deepseek')
const llmModel = ref('')
const itemHeight = ref(resolveItemHeight())
const activeTool = ref<ToolKey | null>(null)
const showImportDialog = ref(false)
const showIssueDialog = ref(false)
const importDialogInitialTab = ref<ResourceImportTab>('tm')
const showShortcutHelp = ref(false)
const showSaveToTMDialog = ref(false)
const openConfirmMenu = ref(false)
const confirmationActionLoading = ref(false)
const openRevisionMenu = ref<RevisionMenuKind | null>(null)
const revisionTraceVisible = ref(false)
const revisionActionLoading = ref(false)
const segmentSearchOpen = ref(false)
const sourceSearchInputRef = ref<HTMLInputElement | null>(null)
const guidelinesEditorRef = ref<HTMLTextAreaElement | null>(null)
const segmentDisplayScope = ref<SegmentDisplayScope>('all')
const sourceSearchQuery = ref('')
const targetSearchQuery = ref('')
const replaceSearchText = ref('')
const searchFuzzyEnabled = ref(false)
const searchLoadingAllSegments = ref(false)
const retainedEmptyTargetSentenceIds = ref<Set<string>>(new Set())
const tmCollections = ref<TMCollection[]>([])
const loadingTMCollections = ref(false)
const savingToTM = ref(false)
const saveToTMScope = ref<SaveToTMScope>('translated')
const saveToTMTargetMode = ref<SaveToTMTargetMode>('new')
const saveToTMCollectionId = ref('')
const saveToTMNewCollectionName = ref('')

const termBases = ref<TermBase[]>([])
const termEntries = ref<TermEntryRecord[]>([])
const selectedTermBaseId = ref('')
const loadingTermBases = ref(false)
const loadingTermEntries = ref(false)
const addingTerm = ref(false)
const termsMessage = ref(t('workbench.terms.defaultMessage'))
let searchLoadRequestId = 0
let suppressSegmentFilterWatch = false

const segmentPageSizes = [100, 200, 500]

const llmModelSelectOptions = computed(() => [
  { id: '', name: t('workbench.aiModelDefault') },
  ...llmModelOptions.filter((option) => (
    llmProvider.value === 'auto' || option.provider === llmProvider.value
  )),
])

watch(llmModel, (modelId) => {
  if (modelId.startsWith('google/') || modelId.startsWith('openai/')) {
    llmProvider.value = 'openrouter'
  }
})

watch(llmProvider, (provider) => {
  if (!llmModel.value || provider === 'auto') {
    return
  }
  const selectedModel = llmModelOptions.find((option) => option.id === llmModel.value)
  if (selectedModel && selectedModel.provider !== provider) {
    llmModel.value = ''
  }
})

function getCommentWindowQuery(): CommentWindowQuery | null {
  if (!segmentStore.segments.length) {
    return null
  }
  return {
    page: segmentStore.currentPage,
    pageSize: segmentStore.pageSize,
    scope: segmentDisplayScope.value,
    sourceQuery: sourceSearchQuery.value,
    targetQuery: targetSearchQuery.value,
    searchFuzzy: searchFuzzyEnabled.value,
  }
}

// 导出相关状态
const showGuidelinesPanel = ref(false)
const workbenchGuidelines = ref('')
const guidelineTemplates = ref<GuidelineTemplateSummary[]>([])
const selectedGuidelineTemplateId = ref('')
const loadingGuidelineTemplates = ref(false)
const importingGuidelineTemplate = ref(false)
const guidelineTemplateInputRef = ref<HTMLInputElement | null>(null)

const showExportMenu = ref(false)
const exportOptions = ref<Array<{ id: string; name: string; description: string; extension: string }>>([])
const loadingExportOptions = ref(false)
const exporting = ref(false)

// 导出格式映射（用于原格式导出按钮显示）
const exportFormatMap: Record<string, { format: string; label: string; note?: string }> = {
  '.rar': { format: 'zip', label: 'ZIP', note: 'RAR 将转换为 ZIP' },
  '.pdf': { format: 'docx', label: 'DOCX', note: 'PDF 将转换为 DOCX' },
}

const exportInfo = computed(() => {
  const filename = segmentStore.fileRecord?.filename || ''
  const ext = filename.substring(filename.lastIndexOf('.')).toLowerCase()

  if (exportFormatMap[ext]) {
    return exportFormatMap[ext]
  }

  // 默认导出原格式
  const format = ext.replace('.', '').toUpperCase() || 'DOCX'
  return { format: ext.replace('.', ''), label: format }
})

// 是否为 Office 格式（Office 格式只支持原格式导出）
const isOfficeFormat = computed(() => {
  const filename = segmentStore.fileRecord?.filename || ''
  const ext = filename.substring(filename.lastIndexOf('.')).toLowerCase()
  return ['.docx', '.xlsx', '.pptx', '.pdf'].includes(ext)
})

function resolveItemHeight() {
  if (window.innerWidth <= 720) {
    return 276
  }
  if (window.innerWidth <= 1180) {
    return 168
  }
  return 124
}

function handleResize() {
  itemHeight.value = resolveItemHeight()
}

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

function normalizeTextForSaveToTM(value: string | null | undefined) {
  return (value || '').trim()
}

function buildDefaultSaveToTMCollectionName() {
  const filename = segmentStore.fileRecord?.filename?.trim() || t('workbench.currentTask')
  const baseName = filename.replace(/\.[^./\\]+$/, '').trim() || filename
  return t('workbench.saveToTMDefaultCollectionName', { name: baseName })
}

function selectDefaultSaveToTMCollection() {
  const boundCollectionId = taskTMCollectionId.value
  if (boundCollectionId && tmCollections.value.some((collection) => collection.id === boundCollectionId)) {
    saveToTMCollectionId.value = boundCollectionId
    return
  }
  saveToTMCollectionId.value = orderedSaveToTMCollections.value[0]?.id || ''
}

function buildSaveToTMEntries(scope: SaveToTMScope) {
  const entries: Array<{
    source_text: string
    target_text: string
    source_language: string | null
    target_language: string | null
  }> = []

  for (const segment of segmentStore.segments) {
    const sourceText = normalizeTextForSaveToTM(segment.source_text)
    const targetText = normalizeTextForSaveToTM(segment.target_text)
    const inScope = scope === 'confirmed'
      ? segment.status === 'confirmed'
      : Boolean(targetText)

    if (!inScope || !sourceText || !targetText) {
      continue
    }

    entries.push({
      source_text: sourceText,
      target_text: targetText,
      source_language: segmentStore.fileRecord?.source_language ?? null,
      target_language: segmentStore.fileRecord?.target_language ?? null,
    })
  }

  return entries
}

async function createSaveToTMCollection(name: string) {
  const baseName = name.trim() || buildDefaultSaveToTMCollectionName()
  const sourceLanguage = segmentStore.fileRecord?.source_language
  const targetLanguage = segmentStore.fileRecord?.target_language

  for (let index = 1; index <= 30; index += 1) {
    const candidateName = index === 1 ? baseName : `${baseName} (${index})`
    try {
      const { data } = await http.post<TMCollection>('/translation-memory/collections', {
        name: candidateName,
        description: segmentStore.fileRecord?.filename
          ? `由任务「${segmentStore.fileRecord.filename}」保存生成`
          : '',
        source_language: sourceLanguage,
        target_language: targetLanguage,
      })
      return data
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        continue
      }
      throw error
    }
  }

  throw new Error(t('workbench.errors.saveToTMCollectionCreateConflict'))
}

async function saveToTMThroughBatchFallback(payload: SaveToTMPayload): Promise<SaveToTMResult> {
  const entries = buildSaveToTMEntries(payload.scope)
  let collectionId = payload.collection_id || ''
  let collectionName = ''
  let createdCollection = false

  if (payload.collection_mode === 'new') {
    if (entries.length === 0) {
      return {
        created_count: 0,
        updated_count: 0,
        skipped_count: segmentStore.segments.length,
        total_segments: segmentStore.segments.length,
        collection_id: null,
        collection_name: null,
        created_collection: false,
      }
    }

    const collection = await createSaveToTMCollection(payload.collection_name || buildDefaultSaveToTMCollectionName())
    collectionId = collection.id
    collectionName = collection.name
    createdCollection = true
  } else {
    const collection = tmCollections.value.find((item) => item.id === collectionId)
    collectionName = collection?.name || ''
  }

  const { data } = await http.post<{ created: number; updated: number; skipped: number }>('/translation-memory/entries/batch', {
    collection_id: collectionId,
    source_language: segmentStore.fileRecord?.source_language ?? null,
    target_language: segmentStore.fileRecord?.target_language ?? null,
    entries,
  })

  const skippedByScope = Math.max(segmentStore.segments.length - entries.length, 0)
  return {
    created_count: data.created,
    updated_count: data.updated,
    skipped_count: skippedByScope + data.skipped,
    total_segments: segmentStore.segments.length,
    collection_id: collectionId,
    collection_name: collectionName,
    created_collection: createdCollection,
  }
}

const hasProjectReturnContext = computed(() => (
  route.query.from === 'project' && typeof route.query.pid === 'string'
))

const isStandaloneWorkbench = computed(() => Boolean(props.standalone))

const projectReturnId = computed(() => (
  typeof route.query.pid === 'string' ? route.query.pid : ''
))

const projectReturnParent = computed(() => (
  route.query.parent === 'tasks' ? 'tasks' : ''
))

const statusSummary = computed(() => {
  const counters = segmentStore.segmentStatusStats

  return [
    {
      key: 'exact',
      label: t('workbench.statusSummary.exact'),
      value: counters.exact,
      tone: 'exact',
      scope: 'exact_only' as const,
      description: '精确匹配：原文与记忆库条目完全一致的句段。',
    },
    {
      key: 'fuzzy',
      label: t('workbench.statusSummary.fuzzy'),
      value: counters.fuzzy,
      tone: 'fuzzy',
      scope: 'fuzzy_only' as const,
      description: '模糊匹配：原文与记忆库条目相近，但仍需要人工确认的句段。',
    },
    {
      key: 'none',
      label: t('workbench.statusSummary.none'),
      value: counters.none,
      tone: 'none',
      scope: 'none_only' as const,
      description: '无匹配：没有从记忆库找到可用匹配的句段。',
    },
    {
      key: 'confirmed',
      label: '已确认译文',
      value: counters.confirmed,
      tone: 'confirmed',
      scope: 'confirmed_only' as const,
      description: '已确认译文：表示人工保存的译文，后续批处理默认会跳过。',
    },
    {
      key: 'empty_target',
      label: '空译文',
      value: counters.empty_target,
      tone: 'empty',
      scope: 'empty_target' as const,
      description: '空译文：当前译文为空的句段。',
    },
  ]
})

const confirmableSegmentCount = computed(() => Math.max(
  0,
  segmentStore.totalSegmentCount - segmentStore.segmentStatusStats.confirmed,
))
const confirmedSegmentCount = computed(() => segmentStore.segmentStatusStats.confirmed)

const currentLanguagePair = computed(() => (
  formatLanguagePair(
    segmentStore.fileRecord?.source_language ?? null,
    segmentStore.fileRecord?.target_language ?? null,
  )
))

function formatWorkbenchStatusTime(value: string | null | undefined) {
  if (!value) {
    return ''
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  return date
    .toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
    .replace(/\//g, '-')
}

const lastModifiedStatusText = computed(() => {
  const modifiedAt = (
    segmentStore.lastModifiedAt
    || segmentStore.lastSyncedAt
    || segmentStore.fileRecord?.updated_at
    || null
  )
  const formatted = formatWorkbenchStatusTime(modifiedAt)
  const pendingSuffix = segmentStore.dirtyCount > 0 ? `（待保存 ${segmentStore.dirtyCount} 条）` : ''
  return `最后修改：${formatted || '--'}${pendingSuffix}`
})

const ribbonStatusTitle = computed(() => (
  `${lastModifiedStatusText.value} · ${segmentStore.syncMessage} · ${segmentStore.llmMessage}`
))

const activeSegment = computed(() => (
  segmentStore.segments.find((segment) => segment.sentence_id === segmentStore.activeSentenceId) ?? null
))

const activeSegmentHistory = computed(() => (
  segmentStore.activeSentenceId
    ? segmentStore.revisionHistory[segmentStore.activeSentenceId] || []
    : []
))

const activePendingRevision = computed(() => (
  revisionTraceVisible.value && segmentStore.activeSentenceId
    ? segmentStore.getRevisionTrace(segmentStore.activeSentenceId)
    : null
))

const activeSegmentSourceText = computed(() => activeSegment.value?.source_text || '')

// 计算当前激活段落匹配的术语（用于原文高亮）
const activeMatchedTerms = computed(() => {
  if (!activeSegmentSourceText.value || termEntries.value.length === 0) return []
  const sourceText = activeSegmentSourceText.value.toLowerCase()
  return termEntries.value
    .filter((entry) => sourceText.includes(entry.source_text.toLowerCase()))
    .slice()
    .sort((left, right) => right.source_text.length - left.source_text.length)
})

function normalizeSearchText(value: string) {
  return value.replace(/\s+/g, ' ').trim().toLocaleLowerCase()
}

function normalizeFuzzySearchText(value: string) {
  return normalizeSearchText(value).replace(/[\s.,，。;；:：'"“”‘’!?！？()[\]{}<>《》、\\/|_-]+/g, '')
}

function buildSourceSearchableText(segment: Segment) {
  const displayText = segment.display_text || ''
  if (displayText && displayText !== segment.source_text) {
    return `${displayText}\n${segment.source_text}`
  }
  return displayText || segment.source_text
}

function isSubsequenceMatch(value: string, keyword: string) {
  const normalizedValue = normalizeFuzzySearchText(value)
  const normalizedKeyword = normalizeFuzzySearchText(keyword)
  if (!normalizedKeyword) {
    return true
  }

  let cursor = 0
  for (const char of normalizedValue) {
    if (char === normalizedKeyword[cursor]) {
      cursor += 1
      if (cursor >= normalizedKeyword.length) {
        return true
      }
    }
  }
  return false
}

function matchesSearchKeyword(value: string, keyword: string) {
  if (!keyword) {
    return true
  }
  const normalizedValue = normalizeSearchText(value)
  return normalizedValue.includes(keyword)
    || (searchFuzzyEnabled.value && isSubsequenceMatch(value, keyword))
}

const normalizedSourceSearchQuery = computed(() => normalizeSearchText(sourceSearchQuery.value))
const normalizedTargetSearchQuery = computed(() => normalizeSearchText(targetSearchQuery.value))

const hasSegmentSearch = computed(() => (
  Boolean(normalizedSourceSearchQuery.value || normalizedTargetSearchQuery.value)
))

const hasSegmentDisplayScope = computed(() => segmentDisplayScope.value !== 'all')
const hasEditorSegmentFilter = computed(() => hasSegmentSearch.value || hasSegmentDisplayScope.value)

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function buildTargetReplaceRegExp(global = false) {
  const keyword = targetSearchQuery.value.trim()
  if (!keyword) {
    return null
  }
  return new RegExp(escapeRegExp(keyword), global ? 'gi' : 'i')
}

function countTargetReplaceOccurrences(segment: Segment) {
  const regexp = buildTargetReplaceRegExp(true)
  if (!regexp) {
    return 0
  }
  return (segment.target_text || '').match(regexp)?.length || 0
}

const segmentDisplayScopeOptions = computed<Array<{ value: SegmentDisplayScope; label: string }>>(() => [
  { value: 'all', label: t('workbench.search.scopes.all') },
  { value: 'exact_only', label: t('workbench.statusSummary.exact') },
  { value: 'fuzzy_only', label: t('workbench.search.scopes.fuzzyOnly') },
  { value: 'none_only', label: t('workbench.search.scopes.noneOnly') },
  { value: 'confirmed_only', label: '已确认译文' },
  { value: 'empty_target', label: '空译文' },
])

function matchesSegmentDisplayScope(segment: Segment) {
  if (segmentDisplayScope.value === 'exact_only') {
    return segment.status === 'exact'
  }
  if (segmentDisplayScope.value === 'fuzzy_only') {
    return segment.status === 'fuzzy'
  }
  if (segmentDisplayScope.value === 'none_only') {
    return segment.status === 'none'
  }
  if (segmentDisplayScope.value === 'confirmed_only') {
    return segment.status === 'confirmed'
  }
  if (segmentDisplayScope.value === 'empty_target') {
    return !normalizeTextForSaveToTM(segment.target_text)
      || retainedEmptyTargetSentenceIds.value.has(segment.sentence_id)
  }
  return true
}

const editorSegments = computed(() => segmentStore.segments)

const replaceableOccurrenceCount = computed(() => (
  editorSegments.value.reduce((count, segment) => count + countTargetReplaceOccurrences(segment), 0)
))

const activeEditorIndex = computed(() => {
  if (!segmentStore.activeSentenceId) {
    return -1
  }
  return editorSegments.value.findIndex((segment) => segment.sentence_id === segmentStore.activeSentenceId)
})

const segmentOrdinalMap = computed(() => {
  const nextMap = new Map<string, number>()
  const startIndex = (segmentStore.currentPage - 1) * segmentStore.pageSize
  segmentStore.segments.forEach((segment, index) => {
    const displayIndex = typeof segment.display_index === 'number' && Number.isFinite(segment.display_index)
      ? segment.display_index
      : startIndex + index
    nextMap.set(segment.sentence_id, displayIndex)
  })
  return nextMap
})

const targetPreviewRenderMode = computed<'static' | 'target'>(() => {
  if (segmentStore.previewSupported && segmentStore.previewHtml) {
    return 'target'
  }
  return 'static'
})

const sourcePreviewHtml = computed(() => (
  activeTool.value === 'split-preview'
    ? buildDocumentPreviewHtml(segmentStore.segments, 'source')
    : segmentStore.previewHtml
))

const sourcePreviewSupported = computed(() => (
  activeTool.value === 'split-preview'
    ? segmentStore.segments.length > 0
    : segmentStore.previewSupported
))

const targetPreviewHtml = computed(() => {
  if (targetPreviewRenderMode.value === 'target') {
    return segmentStore.previewHtml
  }
  return buildDocumentPreviewHtml(segmentStore.segments, 'target')
})

const targetPreviewSupported = computed(() => {
  if (targetPreviewRenderMode.value === 'target') {
    return Boolean(segmentStore.previewHtml)
  }
  return segmentStore.segments.length > 0
})

const exportButtonLabel = computed(() => t('common.actions.export'))

const exportButtonTitle = computed(() => (
  `${t('common.actions.export')} ${getTaskExportFormatLabel(segmentStore.fileRecord?.filename)}`
))

const saveToTMPreviewStats = computed(() => {
  if (segmentStore.saveToTMStats) {
    return {
      matchedCount: segmentStore.saveToTMStats.matched_count,
      validCount: segmentStore.saveToTMStats.valid_count,
      skippedCount: segmentStore.saveToTMStats.skipped_count,
    }
  }

  let matchedCount = 0
  let validCount = 0

  for (const segment of segmentStore.segments) {
    const sourceText = normalizeTextForSaveToTM(segment.source_text)
    const targetText = normalizeTextForSaveToTM(segment.target_text)
    const inScope = saveToTMScope.value === 'confirmed'
      ? segment.status === 'confirmed'
      : Boolean(targetText)

    if (!inScope) {
      continue
    }
    matchedCount += 1
    if (sourceText && targetText) {
      validCount += 1
    }
  }

  return {
    matchedCount,
    validCount,
    skippedCount: Math.max(matchedCount - validCount, 0),
  }
})

const taskTMCollectionId = computed(() => segmentStore.fileRecord?.collection_id || '')
const canOpenIssueDialog = computed(() => Boolean(segmentStore.fileRecord?.project_id))

const selectedTermBaseName = computed(() => (
  termBases.value.find((termBase) => termBase.id === selectedTermBaseId.value)?.name
  || segmentStore.fileRecord?.term_base_name
  || null
))

const orderedSaveToTMCollections = computed(() => {
  const selectedId = taskTMCollectionId.value
  return tmCollections.value.slice().sort((left, right) => {
    if (left.id === selectedId) return -1
    if (right.id === selectedId) return 1
    return left.name.localeCompare(right.name)
  })
})

const saveToTMCanSubmit = computed(() => {
  if (savingToTM.value) {
    return false
  }
  if (saveToTMTargetMode.value === 'new') {
    return Boolean(saveToTMNewCollectionName.value.trim())
  }
  return Boolean(saveToTMCollectionId.value)
})

const sourcePreviewLoading = computed(() =>
  activeTool.value === 'source-preview'
  && segmentStore.previewLoading,
)

const targetPreviewLoading = computed(() => {
  if (activeTool.value !== 'target-preview' && activeTool.value !== 'split-preview') {
    return false
  }
  if (targetPreviewRenderMode.value === 'target') {
    return segmentStore.previewLoading
  }
  return false
})

const toolButtons = computed(() => ([
  { key: 'source-preview' as const, label: t('workbench.tools.sourcePreview'), icon: FileText, tone: 'paper' },
  { key: 'target-preview' as const, label: t('workbench.tools.targetPreview'), icon: FileCheck, tone: 'success' },
  { key: 'split-preview' as const, label: t('workbench.tools.splitPreview'), icon: Columns, tone: 'layout' },
  { key: 'match-info' as const, label: t('workbench.tools.matchInfo'), icon: Info, tone: 'info' },
  { key: 'terms' as const, label: t('workbench.tools.terms'), icon: Languages, tone: 'language' },
  { key: 'notes' as const, label: t('workbench.tools.notes'), icon: MessageSquare, tone: 'note' },
  { key: 'history' as const, label: t('workbench.tools.history'), icon: History, tone: 'history' },
]))

usePageHeader(() => ({
  title: segmentStore.fileRecord?.filename || t('workbench.titleFallback'),
  description: segmentStore.fileRecord
    ? t('workbench.overviewDescription', {
        pair: currentLanguagePair.value,
        loaded: segmentStore.loadedSegmentCount,
        total: segmentStore.totalSegmentCount,
      })
    : t('workbench.description'),
  breadcrumbs: hasProjectReturnContext.value
    ? [
        {
          label: projectReturnParent.value ? t('shell.sections.tasks') : t('shell.sections.workspace'),
          to: projectReturnParent.value ? { name: 'tasks' } : { name: 'projects' },
        },
        {
          label: t('workbench.breadcrumbProject'),
          to: {
            name: 'project-detail',
            params: { id: projectReturnId.value },
            ...(projectReturnParent.value ? { query: { from: projectReturnParent.value } } : {}),
          },
        },
        { label: segmentStore.fileRecord?.filename || t('workbench.titleFallback') },
      ]
    : [
        { label: t('shell.sections.tasks'), to: { name: 'tasks' } },
        { label: segmentStore.fileRecord?.filename || t('workbench.titleFallback') },
      ],
}))

useWorkbenchShortcuts({
  save: () => { void saveNow() },
  runAI: () => { void runLLMTranslation() },
  focusPrev: () => { void focusSentenceByOffset(-1) },
  focusNext: () => { void focusSentenceByOffset(1) },
  confirmCurrent: () => { confirmCurrentSentence() },
  closePanel: () => { closeActiveWorkbenchPanel() },
  toggleHelp: () => { showShortcutHelp.value = !showShortcutHelp.value },
})

function getTermBaseStorageKey() {
  return `workbench-term-base:${segmentStore.fileRecord?.source_language || 'na'}:${segmentStore.fileRecord?.target_language || 'na'}`
}

async function loadTermBases() {
  loadingTermBases.value = true
  termsMessage.value = t('workbench.terms.loadingBases')
  try {
    const { data } = await http.get<TermBase[]>('/term-bases')
    const filtered = data.filter((termBase) => (
      termBase.source_language === segmentStore.fileRecord?.source_language
      && termBase.target_language === segmentStore.fileRecord?.target_language
    ))

    termBases.value = filtered.length > 0 ? filtered : data

    const savedId = window.localStorage.getItem(getTermBaseStorageKey())
    const fallbackId = termBases.value[0]?.id || ''
    selectedTermBaseId.value = termBases.value.some((termBase) => termBase.id === savedId)
      ? String(savedId)
      : fallbackId

    termsMessage.value = selectedTermBaseId.value
      ? t('workbench.terms.selected')
      : t('workbench.terms.noBase')
  } catch (error) {
    termsMessage.value = getErrorMessage(error, t('workbench.errors.termBasesLoad'))
  } finally {
    loadingTermBases.value = false
  }
}

async function loadTermEntries() {
  if (!selectedTermBaseId.value) {
    termEntries.value = []
    return
  }

  loadingTermEntries.value = true
  try {
    const { data } = await http.get<{ items: TermEntryRecord[] }>(`/term-bases/${selectedTermBaseId.value}/entries`, {
      params: { limit: 200 },
    })
    termEntries.value = data.items
    window.localStorage.setItem(getTermBaseStorageKey(), selectedTermBaseId.value)
    termsMessage.value = t('workbench.terms.loadedEntries', { count: data.items.length })
  } catch (error) {
    termsMessage.value = getErrorMessage(error, t('workbench.errors.termEntriesLoad'))
  } finally {
    loadingTermEntries.value = false
  }
}

async function loadGuidelineTemplates() {
  loadingGuidelineTemplates.value = true
  try {
    const { data } = await http.get<GuidelineTemplateSummary[]>('/guideline-templates')
    guidelineTemplates.value = data
    if (
      selectedGuidelineTemplateId.value
      && !data.some((template) => template.id === selectedGuidelineTemplateId.value)
    ) {
      selectedGuidelineTemplateId.value = ''
    }
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.guidelineTemplatesLoad'))
  } finally {
    loadingGuidelineTemplates.value = false
  }
}

function openGuidelineTemplateImport() {
  guidelineTemplateInputRef.value?.click()
}

async function importGuidelineTemplate(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) {
    return
  }

  importingGuidelineTemplate.value = true
  pageError.value = ''
  try {
    const formData = new FormData()
    formData.append('file', file)
    const { data } = await http.post<GuidelineTemplateSummary>('/guideline-templates/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    await loadGuidelineTemplates()
    selectedGuidelineTemplateId.value = data.id
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.guidelineTemplateImport'))
  } finally {
    importingGuidelineTemplate.value = false
  }
}

async function openTool(tool: ToolKey) {
  if (activeTool.value === tool) {
    activeTool.value = null
    sidecarWidth.value = null
    return
  }

  pageError.value = ''
  activeTool.value = tool
  sidecarWidth.value = null

  try {
    if (tool === 'source-preview') {
      await segmentStore.ensurePreviewLoaded('source')
      return
    }

    if (tool === 'target-preview' || tool === 'split-preview') {
      await segmentStore.ensurePreviewLoaded('target')
      return
    }

    if (tool === 'terms' && termBases.value.length === 0 && !loadingTermBases.value) {
      await loadTermBases()
    }

    if (tool === 'match-info') {
      // 确保术语库列表和条目已加载
      if (termBases.value.length === 0 && !loadingTermBases.value) {
        await loadTermBases()
      }
      if (!selectedTermBaseId.value && termBases.value.length > 0) {
        selectedTermBaseId.value = termBases.value[0].id
      }
      if (selectedTermBaseId.value && termEntries.value.length === 0 && !loadingTermEntries.value) {
        await loadTermEntries()
      }
    }
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.sidePanel'))
  }
}

async function handleEditorReachEnd() {
  // 分页模式下不再滚动累积全文；底部使用分页控件切换页。
}

function openImportDialog(tab: ResourceImportTab = 'tm') {
  importDialogInitialTab.value = tab
  showImportDialog.value = true
}

async function handleResourceImported(payload: { tab: ResourceImportTab }) {
  showImportDialog.value = false
  if (payload.tab === 'term') {
    await loadTermBases()
  }
}

function closeActiveWorkbenchPanel() {
  if (segmentSearchOpen.value) {
    closeSegmentSearchPanel()
    return
  }
  if (showGuidelinesPanel.value) {
    closeGuidelinesPanel()
    return
  }
  activeTool.value = null
}

function closeGuidelinesPanel() {
  showGuidelinesPanel.value = false
}

async function toggleGuidelinesPanel() {
  showGuidelinesPanel.value = !showGuidelinesPanel.value
  if (showGuidelinesPanel.value) {
    await nextTick()
    guidelinesEditorRef.value?.focus({ preventScroll: true })
  }
}

function resetSegmentSearch() {
  searchLoadRequestId += 1
  segmentSearchOpen.value = false
  segmentDisplayScope.value = 'all'
  sourceSearchQuery.value = ''
  targetSearchQuery.value = ''
  replaceSearchText.value = ''
  searchFuzzyEnabled.value = false
  searchLoadingAllSegments.value = false
  retainedEmptyTargetSentenceIds.value = new Set()
}

function closeSegmentSearchPanel() {
  segmentSearchOpen.value = false
}

function setSegmentDisplayScope(scope: SegmentDisplayScope) {
  if (segmentDisplayScope.value !== scope) {
    retainedEmptyTargetSentenceIds.value = new Set()
  }
  segmentDisplayScope.value = scope
}

function handleSegmentDisplayScopeChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value as SegmentDisplayScope
  setSegmentDisplayScope(value)
}

function toggleSegmentSummaryScope(scope: SegmentDisplayScope) {
  setSegmentDisplayScope(segmentDisplayScope.value === scope ? 'all' : scope)
}

function retainEmptyTargetSegmentDuringEdit(sentenceId: string, previousTargetText: string | null | undefined) {
  if (segmentDisplayScope.value !== 'empty_target' || normalizeTextForSaveToTM(previousTargetText)) {
    return
  }

  retainedEmptyTargetSentenceIds.value = new Set([
    ...retainedEmptyTargetSentenceIds.value,
    sentenceId,
  ])
}

function updateSegmentTarget(sentenceId: string, targetText: string) {
  const segment = segmentStore.segments.find((item) => item.sentence_id === sentenceId)
  retainEmptyTargetSegmentDuringEdit(sentenceId, segment?.target_text)
  segmentStore.updateTarget(sentenceId, targetText)
}

async function toggleSegmentSearchPanel() {
  segmentSearchOpen.value = !segmentSearchOpen.value
  if (segmentSearchOpen.value) {
    await nextTick()
    sourceSearchInputRef.value?.focus({ preventScroll: true })
  }
}

async function ensureFilteredCorpusLoaded() {
  searchLoadRequestId += 1
  searchLoadingAllSegments.value = false
}

function getCurrentSegmentIndex() {
  if (activeEditorIndex.value === -1) {
    return 0
  }
  return activeEditorIndex.value
}

async function focusEditorSegmentAtIndex(index: number) {
  const target = editorSegments.value[index]
  if (!target) {
    return
  }

  segmentStore.setActiveSentence(target.sentence_id)
  await nextTick()
  await virtualListRef.value?.focusIndex(index, '[data-segment-target="true"]', 'nearest')
}

async function focusMatchedSegment(offset: number) {
  if (!hasEditorSegmentFilter.value) {
    return
  }

  await ensureFilteredCorpusLoaded()

  const matches = editorSegments.value
  if (!matches.length) {
    return
  }

  let targetIndex = activeEditorIndex.value
  if (targetIndex === -1) {
    targetIndex = offset > 0 ? 0 : matches.length - 1
  } else {
    targetIndex = (targetIndex + offset + matches.length) % matches.length
  }

  await focusEditorSegmentAtIndex(targetIndex)
}

function replaceTargetText(targetText: string, replaceAll = false) {
  const regexp = buildTargetReplaceRegExp(replaceAll)
  if (!regexp) {
    return targetText
  }
  return targetText.replace(regexp, replaceSearchText.value)
}

async function replaceCurrentSearchMatch() {
  if (!targetSearchQuery.value.trim()) {
    toast.warn(t('workbench.search.targetRequiredForReplace'))
    return
  }

  await ensureFilteredCorpusLoaded()

  const exactActiveSegment = activeSegment.value && countTargetReplaceOccurrences(activeSegment.value) > 0
    ? activeSegment.value
    : null
  const targetSegment = exactActiveSegment
    || editorSegments.value.find((segment) => countTargetReplaceOccurrences(segment) > 0)

  if (!targetSegment) {
    toast.warn(t('workbench.search.replaceNoMatch'))
    return
  }

  updateSegmentTarget(targetSegment.sentence_id, replaceTargetText(targetSegment.target_text || '', false))
  const targetIndex = editorSegments.value.findIndex((segment) => segment.sentence_id === targetSegment.sentence_id)
  if (targetIndex >= 0) {
    await focusEditorSegmentAtIndex(targetIndex)
  }
  toast.success(t('workbench.search.replacedOne'))
}

async function replaceAllSearchMatches() {
  if (!targetSearchQuery.value.trim()) {
    toast.warn(t('workbench.search.targetRequiredForReplace'))
    return
  }

  if (!segmentStore.fileRecord) {
    return
  }

  pageError.value = ''
  try {
    const synced = await segmentStore.syncToBackend()
    if (!synced) {
      return
    }
    const { data } = await http.post<{ updated_count: number; occurrence_count: number }>(
      `/file-records/${segmentStore.fileRecord.id}/segments/replace`,
      {
        scope: segmentDisplayScope.value,
        source_query: sourceSearchQuery.value,
        target_query: targetSearchQuery.value,
        replace_text: replaceSearchText.value,
        search_fuzzy: searchFuzzyEnabled.value,
        replace_all: true,
      },
    )
    if (data.updated_count === 0) {
      toast.warn(t('workbench.search.replaceNoMatch'))
      return
    }
    await refreshSegmentPage(segmentStore.currentPage, segmentStore.pageSize)
    toast.success(t('workbench.search.replacedAll', {
      count: data.occurrence_count,
      segments: data.updated_count,
    }))
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.save'))
  }
}

async function focusSentenceByOffset(offset: number) {
  if (hasEditorSegmentFilter.value) {
    await focusMatchedSegment(offset)
    return
  }

  let targetIndex = getCurrentSegmentIndex() + offset
  targetIndex = Math.max(0, targetIndex)

  targetIndex = Math.min(targetIndex, Math.max(editorSegments.value.length - 1, 0))
  await focusEditorSegmentAtIndex(targetIndex)
}

function getEditorSegmentDisplayIndex(sentenceId: string, fallbackIndex: number) {
  return segmentOrdinalMap.value.get(sentenceId) ?? fallbackIndex
}

function confirmCurrentSentence() {
  if (!activeSegment.value) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }
  updateSegmentTarget(activeSegment.value.sentence_id, activeSegment.value.target_text || '')
  toast.success(t('workbench.messages.confirmed'))
}

function toggleConfirmMenu() {
  if (confirmationActionLoading.value || segmentStore.totalSegmentCount === 0) {
    return
  }
  openConfirmMenu.value = !openConfirmMenu.value
  openRevisionMenu.value = null
}

function closeConfirmMenu() {
  openConfirmMenu.value = false
}

function handleConfirmCurrentFromMenu() {
  closeConfirmMenu()
  confirmCurrentSentence()
}

async function handleConfirmAllSegments() {
  if (confirmableSegmentCount.value === 0) {
    toast.info('当前文件全部句段都已确认。')
    closeConfirmMenu()
    return
  }

  pageError.value = ''
  confirmationActionLoading.value = true
  try {
    const updatedCount = await segmentStore.updateAllSegmentConfirmations('confirm')
    toast.success(updatedCount > 0 ? `已确认 ${updatedCount} 个句段` : '没有需要确认的句段')
    closeConfirmMenu()
  } catch (error) {
    pageError.value = getErrorMessage(error, '全部确认失败')
  } finally {
    confirmationActionLoading.value = false
  }
}

async function handleCancelAllSegmentConfirmations() {
  const count = confirmedSegmentCount.value
  if (count === 0) {
    toast.info('当前文件没有已确认句段。')
    closeConfirmMenu()
    return
  }

  closeConfirmMenu()
  const accepted = await confirm({
    title: '确认全部取消',
    message: `确定要取消当前文件全部 ${count} 个已确认句段的确认状态吗？译文内容会保留，但这些句段将不再显示为已确认。`,
    confirmText: '全部取消',
    cancelText: t('common.actions.cancel'),
    danger: true,
  })
  if (!accepted) {
    return
  }

  pageError.value = ''
  confirmationActionLoading.value = true
  try {
    const updatedCount = await segmentStore.updateAllSegmentConfirmations('cancel')
    toast.success(updatedCount > 0 ? `已取消确认 ${updatedCount} 个句段` : '没有需要取消确认的句段')
    closeConfirmMenu()
  } catch (error) {
    pageError.value = getErrorMessage(error, '全部取消失败')
  } finally {
    confirmationActionLoading.value = false
  }
}

function showRibbonPlaceholder(name: string) {
  toast.info({
    title: t('workbench.ribbon.placeholderTitle'),
    message: t('workbench.ribbon.placeholderMessage', { name }),
  })
}

function setSegmentEditorRowRef(sentenceId: string, instance: Element | ComponentPublicInstance | null) {
  if (instance) {
    segmentEditorRowRefs.set(sentenceId, instance as SegmentEditorRowPublic)
  } else {
    segmentEditorRowRefs.delete(sentenceId)
  }
}

function getActiveSegmentEditorRow() {
  const sentenceId = segmentStore.activeSentenceId
  return sentenceId ? segmentEditorRowRefs.get(sentenceId) || null : null
}

function undoActiveSegmentEdit() {
  if (!activeSegment.value) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }

  const applied = getActiveSegmentEditorRow()?.undoEditorChange() ?? false
  if (!applied) {
    toast.warn('当前句段没有可撤回的编辑。')
  }
}

function redoActiveSegmentEdit() {
  if (!activeSegment.value) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }

  const applied = getActiveSegmentEditorRow()?.redoEditorChange() ?? false
  if (!applied) {
    toast.warn('当前句段没有可恢复的编辑。')
  }
}

function copySourceToTarget() {
  if (!activeSegment.value) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }

  const sourceText = activeSegment.value.display_text || activeSegment.value.source_text || ''
  updateSegmentTarget(activeSegment.value.sentence_id, sourceText)
  toast.success(t('workbench.ribbon.messages.sourceCopied'))
}

function clearActiveTarget() {
  if (!activeSegment.value) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }

  updateSegmentTarget(activeSegment.value.sentence_id, '')
  toast.success(t('workbench.ribbon.messages.targetCleared'))
}

async function addActiveSegmentTerm() {
  if (!activeSegment.value) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }
  if (!selectedTermBaseId.value) {
    toast.warn(t('workbench.ribbon.messages.selectTermBaseFirst'))
    activeTool.value = 'terms'
    return
  }

  const sourceText = normalizeTextForSaveToTM(activeSegment.value.source_text)
  const targetText = normalizeTextForSaveToTM(activeSegment.value.target_text)
  if (!sourceText || !targetText) {
    toast.warn(t('workbench.ribbon.messages.termTextRequired'))
    return
  }

  addingTerm.value = true
  pageError.value = ''
  try {
    await http.post(`/term-bases/${selectedTermBaseId.value}/entries`, {
      source_text: sourceText,
      target_text: targetText,
    })
    await loadTermEntries()
    activeTool.value = 'terms'
    toast.success(t('workbench.ribbon.messages.termAdded'))
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.ribbon.messages.termAddFailed'))
  } finally {
    addingTerm.value = false
  }
}

async function handleApplyPartialRevision(revisionId: string, newText: string) {
  pageError.value = ''
  revisionActionLoading.value = true
  try {
    await segmentStore.applyPartialRevision(revisionId, newText)
    toast.success('已应用修订')
  } catch (error) {
    pageError.value = getErrorMessage(error, '应用修订失败')
  } finally {
    revisionActionLoading.value = false
  }
}

function toggleRevisionMenu(kind: RevisionMenuKind) {
  openRevisionMenu.value = openRevisionMenu.value === kind ? null : kind
  openConfirmMenu.value = false
}

function setRevisionTraceVisible(visible: boolean) {
  revisionTraceVisible.value = visible
  if (visible) {
    segmentStore.startRevisionTracking()
  } else {
    segmentStore.stopRevisionTracking()
  }
  openRevisionMenu.value = null
  toast.success(visible ? t('workbench.ribbon.messages.revisionTraceShown') : t('workbench.ribbon.messages.revisionTraceHidden'))
}

function openRevisionTraceSettings() {
  openRevisionMenu.value = null
  toast.info(t('workbench.ribbon.messages.revisionTraceSettingsHint'))
}

async function handleAcceptActiveRevision() {
  const revision = activePendingRevision.value
  if (!revision) {
    toast.warn(t('workbench.ribbon.messages.currentRevisionRequired'))
    return
  }

  pageError.value = ''
  revisionActionLoading.value = true
  try {
    await segmentStore.acceptRevision(revision.id)
    openRevisionMenu.value = null
    toast.success(t('workbench.ribbon.messages.currentRevisionAccepted'))
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.ribbon.messages.currentRevisionAcceptFailed'))
  } finally {
    revisionActionLoading.value = false
  }
}

async function handleRejectActiveRevision() {
  const revision = activePendingRevision.value
  if (!revision) {
    toast.warn(t('workbench.ribbon.messages.currentRevisionRequired'))
    return
  }

  pageError.value = ''
  revisionActionLoading.value = true
  try {
    await segmentStore.rejectRevision(revision.id)
    openRevisionMenu.value = null
    toast.success(t('workbench.ribbon.messages.currentRevisionRejected'))
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.ribbon.messages.currentRevisionRejectFailed'))
  } finally {
    revisionActionLoading.value = false
  }
}

async function handleBatchAcceptRevisions() {
  const count = segmentStore.pendingRevisionCount
  if (count === 0) {
    return
  }

  const confirmed = await confirm({
    title: '确认全部接受修订',
    message: `确定要接受当前全部 ${count} 条待处理修订吗？接受后这些译文会直接应用到句段中。`,
    confirmText: '全部接受',
    cancelText: t('common.actions.cancel'),
  })

  if (!confirmed) {
    return
  }

  pageError.value = ''
  revisionActionLoading.value = true
  try {
    const updatedCount = await segmentStore.batchAcceptRevisions()
    openRevisionMenu.value = null
    toast.success(updatedCount > 0 ? `已接受 ${updatedCount} 条修订` : '没有待处理的修订')
  } catch (error) {
    pageError.value = getErrorMessage(error, '批量接受修订失败')
  } finally {
    revisionActionLoading.value = false
  }
}

async function handleBatchRejectRevisions() {
  const count = segmentStore.pendingRevisionCount
  if (count === 0) {
    return
  }

  const confirmed = await confirm({
    title: '确认全部拒绝修订',
    message: `确定要拒绝当前全部 ${count} 条待处理修订吗？拒绝后这些修订建议不会应用到句段中。`,
    confirmText: '全部拒绝',
    cancelText: t('common.actions.cancel'),
    danger: true,
  })

  if (!confirmed) {
    return
  }

  pageError.value = ''
  revisionActionLoading.value = true
  try {
    const updatedCount = await segmentStore.batchRejectRevisions()
    openRevisionMenu.value = null
    toast.success(updatedCount > 0 ? `已拒绝 ${updatedCount} 条修订` : '没有待处理的修订')
  } catch (error) {
    pageError.value = getErrorMessage(error, '批量拒绝修订失败')
  } finally {
    revisionActionLoading.value = false
  }
}

function handleApplyTMTarget(sentenceId: string, targetText: string) {
  updateSegmentTarget(sentenceId, targetText)
}

function getRouteQueryString(value: unknown) {
  if (Array.isArray(value)) {
    return value[0] == null ? undefined : String(value[0])
  }
  return value == null ? undefined : String(value)
}

async function replaceSegmentPageQueryIfNeeded() {
  const currentPageQuery = getRouteQueryString(route.query.page)
  const currentPageSizeQuery = getRouteQueryString(route.query.pageSize)
  if (currentPageQuery === undefined && currentPageSizeQuery === undefined) {
    return
  }

  const nextPage = String(segmentStore.currentPage)
  const nextPageSize = String(segmentStore.pageSize)
  if (currentPageQuery === nextPage && currentPageSizeQuery === nextPageSize) {
    return
  }

  await router.replace({
    query: {
      ...route.query,
      page: nextPage,
      pageSize: nextPageSize,
    },
  })
}

async function loadTask() {
  pageError.value = ''
  activeTool.value = null
  openRevisionMenu.value = null
  suppressSegmentFilterWatch = true
  resetSegmentSearch()
  suppressSegmentFilterWatch = false
  commentStore.stopPolling()
  termEntries.value = []
  termBases.value = []
  selectedTermBaseId.value = ''

  try {
    await segmentStore.loadTask(props.id, {
      page: Number(route.query.page || 1),
      pageSize: Number(route.query.pageSize || segmentStore.pageSize || 100),
      scope: segmentDisplayScope.value,
      sourceQuery: sourceSearchQuery.value,
      targetQuery: targetSearchQuery.value,
      searchFuzzy: searchFuzzyEnabled.value,
    })
    await replaceSegmentPageQueryIfNeeded()
    if (segmentStore.fileRecord?.is_edit_locked) {
      const message = segmentStore.fileRecord.active_operation_message || t('workbench.errors.editLocked')
      pageError.value = message
      toast.warn({
        title: t('workbench.errors.editLocked'),
        message,
      })
      const projectId = segmentStore.fileRecord.project_id
      if (projectId) {
        void router.replace({ name: 'project-detail', params: { id: projectId } })
      }
      return
    }
    if (segmentStore.segments[0] && !segmentStore.activeSentenceId) {
      segmentStore.setActiveSentence(segmentStore.segments[0].sentence_id)
    }
    if (revisionTraceVisible.value) {
      segmentStore.startRevisionTracking()
    }

    try {
      const commentQuery = getCommentWindowQuery()
      if (commentQuery) {
        await commentStore.loadComments(props.id, commentQuery)
      } else {
        commentStore.resetState()
      }
      commentStore.startPolling(props.id, getCommentWindowQuery)
    } catch (error) {
      commentStore.message = getErrorMessage(error, t('workbench.errors.commentsUnavailable'))
    }

    workbenchGuidelines.value = ''

    await loadTermBases()

    const boundTermBaseId = segmentStore.fileRecord?.term_base_id
    if (boundTermBaseId) {
      selectedTermBaseId.value = boundTermBaseId
    }
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.taskLoad'))
  }
}

async function refreshSegmentPage(page = segmentStore.currentPage, size = segmentStore.pageSize) {
  if (!segmentStore.fileRecord) {
    return
  }

  pageError.value = ''
  try {
    const synced = await segmentStore.syncToBackend()
    if (!synced) {
      return
    }
    await segmentStore.loadSegmentPage({
      page,
      pageSize: size,
      scope: segmentDisplayScope.value,
      sourceQuery: sourceSearchQuery.value,
      targetQuery: targetSearchQuery.value,
      searchFuzzy: searchFuzzyEnabled.value,
    })
    if (revisionTraceVisible.value) {
      segmentStore.startRevisionTracking()
    }
    const commentQuery = getCommentWindowQuery()
    try {
      if (commentQuery) {
        await commentStore.loadComments(props.id, commentQuery)
        commentStore.startPolling(props.id, getCommentWindowQuery)
      } else {
        commentStore.resetState()
      }
    } catch (error) {
      commentStore.message = getErrorMessage(error, t('workbench.errors.commentsUnavailable'))
    }
    await nextTick()
    await virtualListRef.value?.scrollToIndex(0, 'start')
    await router.replace({
      query: {
        ...route.query,
        page: String(segmentStore.currentPage),
        pageSize: String(segmentStore.pageSize),
      },
    })
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.loadMore'))
  }
}

async function handleSegmentPageChange(page: number) {
  await refreshSegmentPage(page, segmentStore.pageSize)
}

async function handleSegmentPageSizeChange(size: number) {
  await refreshSegmentPage(1, size)
}

async function runLLMTranslation() {
  pageError.value = ''
  try {
    await segmentStore.startLLMTranslation(llmScope.value, llmProvider.value, {
      guidelineTemplateId: selectedGuidelineTemplateId.value || undefined,
      temporaryPrompt: workbenchGuidelines.value,
      model: llmModel.value || undefined,
    })
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.llm'))
  }
}

async function stopLLMTranslation() {
  await segmentStore.abortLLM()
}

async function loadTMCollections() {
  loadingTMCollections.value = true
  try {
    const { data } = await http.get<TMCollection[]>('/translation-memory/collections')
    tmCollections.value = data
    selectDefaultSaveToTMCollection()
  } finally {
    loadingTMCollections.value = false
  }
}

async function openSaveToTMDialog() {
  pageError.value = ''
  saveToTMScope.value = 'translated'
  saveToTMTargetMode.value = 'new'
  saveToTMNewCollectionName.value = buildDefaultSaveToTMCollectionName()
  try {
    await segmentStore.loadSaveToTMStats(saveToTMScope.value)
  } catch {
    segmentStore.saveToTMStats = null
  }
  if (!tmCollections.value.length) {
    try {
      await loadTMCollections()
    } catch (error) {
      pageError.value = getErrorMessage(error, t('workbench.errors.saveToTMCollectionsLoad'))
      return
    }
  } else {
    selectDefaultSaveToTMCollection()
  }
  showSaveToTMDialog.value = true
}

async function saveToTM() {
  if (!segmentStore.fileRecord) {
    return
  }
  if (saveToTMTargetMode.value === 'new' && !saveToTMNewCollectionName.value.trim()) {
    toast.warn(t('workbench.errors.saveToTMCollectionNameRequired'))
    return
  }
  if (saveToTMTargetMode.value === 'existing' && !saveToTMCollectionId.value) {
    toast.warn(t('workbench.errors.saveToTMCollectionRequired'))
    return
  }

  pageError.value = ''
  savingToTM.value = true
  try {
    const synced = await segmentStore.syncToBackend()
    if (!synced) {
      return
    }

    const payload: SaveToTMPayload = saveToTMTargetMode.value === 'new'
      ? {
          collection_mode: 'new',
          collection_name: saveToTMNewCollectionName.value.trim(),
          scope: saveToTMScope.value,
        }
      : {
          collection_mode: 'existing',
          collection_id: saveToTMCollectionId.value,
          scope: saveToTMScope.value,
        }

    let data: SaveToTMResult
    try {
      const response = await http.post<SaveToTMResult>(`/file-records/${segmentStore.fileRecord.id}/save-to-tm`, payload)
      data = response.data
    } catch (error) {
      if (!axios.isAxiosError(error) || error.response?.status !== 405) {
        throw error
      }
      data = await saveToTMThroughBatchFallback(payload)
    }

    if (data.created_collection) {
      try {
        await loadTMCollections()
      } catch {
        // 保存已经完成，列表刷新失败时保留当前选择，避免把成功结果误报为失败。
      }
    }
    if (data.collection_id) {
      saveToTMCollectionId.value = data.collection_id
    }

    toast.success(t('workbench.messages.saveToTMResult', {
      collection: data.collection_name || saveToTMNewCollectionName.value || '',
      created: data.created_count,
      updated: data.updated_count,
      skipped: data.skipped_count,
    }))
    showSaveToTMDialog.value = false
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.saveToTM'))
  } finally {
    savingToTM.value = false
  }
}

function openIssueDialog() {
  if (!canOpenIssueDialog.value) {
    toast.warn(t('issueMarker.errors.missingProject'))
    return
  }
  showIssueDialog.value = true
}

function handleIssueSaved(_marker: IssueMarker) {
  showIssueDialog.value = false
  if (segmentStore.fileRecord) {
    segmentStore.fileRecord.issue_count += 1
    segmentStore.fileRecord.open_issue_count += 1
  }
  toast.success(t('issueMarker.messages.saved'))
}

async function saveNow() {
  pageError.value = ''
  try {
    const synced = await segmentStore.syncToBackend()
    if (!synced) {
      return
    }
    toast.success(t('workbench.messages.synced'))
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.save'))
  }
}

async function exportTranslatedFile() {
  pageError.value = ''
  try {
    await segmentStore.downloadTranslatedFile()
  } catch (error) {
    pageError.value = getErrorMessage(error, '导出失败。')
  }
}

async function goBack() {
  if (hasProjectReturnContext.value) {
    await router.push({
      name: 'project-detail',
      params: { id: projectReturnId.value },
      ...(projectReturnParent.value ? { query: { from: projectReturnParent.value } } : {}),
    })
    return
  }
  await router.push({ name: 'tasks' })
}

function openFocusWorkbench() {
  const resolved = router.resolve({
    name: 'workbench-focus',
    params: { id: props.id },
    query: { ...route.query },
  })
  window.open(resolved.href, '_blank', 'noopener,noreferrer')
}

async function loadAllSegments() {
  toast.info('大文档模式已启用分页加载，请使用分页控件切换句段。')
}

async function handlePreviewFocus(sentenceId: string) {
  segmentStore.setActiveSentence(sentenceId)

  if (hasEditorSegmentFilter.value) {
    await ensureFilteredCorpusLoaded()
    const filteredIndex = editorSegments.value.findIndex((segment) => segment.sentence_id === sentenceId)
    if (filteredIndex === -1) {
      return
    }

    await nextTick()
    await virtualListRef.value?.focusIndex(filteredIndex, '[data-segment-target="true"]', 'nearest')
    return
  }

  const index = await segmentStore.ensureSentenceLoaded(sentenceId)
  if (index === -1) {
    return
  }

  await nextTick()
  await virtualListRef.value?.focusIndex(index, '[data-segment-target="true"]', 'nearest')
}

async function loadExportOptions() {
  if (!segmentStore.fileRecord) return

  loadingExportOptions.value = true
  try {
    const { data } = await http.get(`/file-records/${segmentStore.fileRecord.id}/export-options`)
    exportOptions.value = data.export_options || []
  } catch (error) {
    console.error('加载导出选项失败:', error)
    exportOptions.value = []
  } finally {
    loadingExportOptions.value = false
  }
}

async function toggleExportMenu() {
  if (showExportMenu.value) {
    showExportMenu.value = false
    return
  }

  // 加载导出选项
  await loadExportOptions()
  showExportMenu.value = true
}

async function exportWithType(exportType: string) {
  if (!segmentStore.fileRecord) return

  pageError.value = ''
  exporting.value = true
  showExportMenu.value = false

  try {
    const response = await http.get(
      `/file-records/${segmentStore.fileRecord.id}/export/${exportType}`,
      { responseType: 'blob' }
    )

    // 从响应头获取文件名
    const contentDisposition = response.headers['content-disposition']
    let filename = `export.${exportType}`
    if (contentDisposition) {
      const filenameMatch = contentDisposition.match(/filename\*=UTF-8''(.+?)(?:;|$)/)
      if (filenameMatch) {
        filename = decodeURIComponent(filenameMatch[1])
      } else {
        const simpleMatch = contentDisposition.match(/filename="?(.+?)"?(?:;|$)/)
        if (simpleMatch) {
          filename = simpleMatch[1]
        }
      }
    }

    // 下载文件
    const url = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', filename)
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '导出失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '导出失败。'
  } finally {
    exporting.value = false
  }
}

// 点击外部关闭导出菜单
function handleClickOutside(event: MouseEvent) {
  const target = event.target as HTMLElement
  if (!target.closest('.export-dropdown')) {
    showExportMenu.value = false
  }
  if (
    !target.closest('.workbench-confirm-menu')
    && !target.closest('.workbench-confirm-menu__dropdown')
  ) {
    openConfirmMenu.value = false
  }
  if (
    !target.closest('.workbench-revision-menu')
    && !target.closest('.workbench-revision-menu__dropdown')
    && !target.closest('.workbench-revision-trigger')
  ) {
    openRevisionMenu.value = null
  }
}

async function handleCommentDraft(draft: CommentAnchorDraft) {
  commentStore.setDraftAnchor(draft)
  commentStore.setActiveComment(null)
  segmentStore.setActiveSentence(draft.sentence_id)
  await handlePreviewFocus(draft.sentence_id)
  activeTool.value = 'notes'
}

async function handleCommentFocus(commentId: string) {
  commentStore.setDraftAnchor(null)
  commentStore.setActiveComment(commentId)
  const comment = commentStore.comments.find((item) => item.id === commentId)
  if (comment?.sentence_id) {
    segmentStore.setActiveSentence(comment.sentence_id)
    await handlePreviewFocus(comment.sentence_id)
  }
  activeTool.value = 'notes'
}

async function handleCreateComment(payload: CommentCreatePayload) {
  if (!segmentStore.fileRecord) {
    return
  }

  pageError.value = ''
  try {
    const comment = await commentStore.createComment(segmentStore.fileRecord.id, payload)
    if (comment.sentence_id) {
      segmentStore.setActiveSentence(comment.sentence_id)
      await handlePreviewFocus(comment.sentence_id)
    }
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.commentSave'))
  }
}

async function handleReplyComment(commentId: string, body: string) {
  pageError.value = ''
  try {
    const comment = await commentStore.replyToComment(commentId, { body })
    if (comment.sentence_id) {
      segmentStore.setActiveSentence(comment.sentence_id)
      await handlePreviewFocus(comment.sentence_id)
    }
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.replySave'))
  }
}

async function handleUpdateComment(commentId: string, payload: { body?: string; status?: CommentStatus }) {
  pageError.value = ''
  try {
    const comment = await commentStore.updateComment(commentId, payload)
    if (comment.sentence_id) {
      segmentStore.setActiveSentence(comment.sentence_id)
    }
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.commentUpdate'))
  }
}

async function handleDeleteComment(commentId: string) {
  pageError.value = ''
  try {
    await commentStore.deleteComment(commentId)
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.commentDelete'))
  }
}

function needsInlineSpace(leftText: string, rightText: string) {
  if (!leftText || !rightText) {
    return false
  }

  return /[A-Za-z0-9)]$/.test(leftText) && /^[A-Za-z0-9(]/.test(rightText)
}

function appendToCurrentText(currentText: string, nextText: string) {
  if (!currentText) {
    return nextText
  }
  if (!nextText) {
    return currentText
  }

  const trimmedCurrent = currentText.trimEnd()
  const trimmedNext = nextText.trim()
  if (trimmedCurrent.endsWith(trimmedNext)) {
    return currentText
  }

  const separator = /\s$/.test(currentText) || /^\s/.test(nextText)
    ? ''
    : (needsInlineSpace(currentText.slice(-1), nextText.charAt(0)) ? ' ' : '')

  return `${currentText}${separator}${nextText}`
}

function handleReplaceText(text: string) {
  if (!activeSegment.value) {
    return
  }

  updateSegmentTarget(activeSegment.value.sentence_id, text)
  toast.success(t('matchPanel.textInserted'))
}

function handleAppendText(text: string) {
  if (!activeSegment.value) {
    return
  }

  const nextText = appendToCurrentText(activeSegment.value.target_text || '', text)
  updateSegmentTarget(activeSegment.value.sentence_id, nextText)
  toast.success(t('matchPanel.textInserted'))
}

async function ensureMatchInfoPanelOpen() {
  pageError.value = ''
  activeTool.value = 'match-info'

  try {
    if (termBases.value.length === 0 && !loadingTermBases.value) {
      await loadTermBases()
    }
    if (!selectedTermBaseId.value && termBases.value.length > 0) {
      selectedTermBaseId.value = termBases.value[0].id
    }
    if (selectedTermBaseId.value && termEntries.value.length === 0 && !loadingTermEntries.value) {
      await loadTermEntries()
    }
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.sidePanel'))
  }
}

function handleSegmentTargetActivate(sentenceId: string) {
  segmentStore.setActiveSentence(sentenceId)
}

watch(() => props.id, () => {
  void loadTask()
})

watch(selectedTermBaseId, () => {
  if (!selectedTermBaseId.value) {
    termEntries.value = []
    return
  }
  void loadTermEntries()
})

watch(saveToTMScope, () => {
  if (showSaveToTMDialog.value) {
    void segmentStore.loadSaveToTMStats(saveToTMScope.value)
  }
})

watch([segmentDisplayScope, sourceSearchQuery, targetSearchQuery, searchFuzzyEnabled], async () => {
  if (suppressSegmentFilterWatch) {
    return
  }
  searchLoadRequestId += 1
  searchLoadingAllSegments.value = false
  if (segmentStore.fileRecord) {
    await refreshSegmentPage(1, segmentStore.pageSize)
  }
})

onMounted(() => {
  window.addEventListener('resize', handleResize)
  document.addEventListener('click', handleClickOutside)
  void loadTask()
  void loadGuidelineTemplates()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  document.removeEventListener('click', handleClickOutside)
  commentStore.stopPolling()
})

onBeforeRouteLeave(async () => {
  commentStore.stopPolling()
  await segmentStore.syncToBackend()
})
</script>

<template>
  <div
    class="content-stack content-stack--workbench workbench-page"
    :class="{ 'is-standalone': isStandaloneWorkbench }"
    data-testid="workbench-page"
  >
    <section v-if="isStandaloneWorkbench" class="toolbar-panel workbench-toolbar workbench-ribbon" data-testid="workbench-ribbon">
      <div class="workbench-ribbon__tabs">
        <button class="workbench-ribbon__tab is-active" type="button">
          {{ t('workbench.ribbon.startTab') }}
        </button>
        <div class="workbench-ribbon__task">
          <strong>{{ segmentStore.fileRecord?.filename || t('workbench.titleFallback') }}</strong>
          <span>{{ currentLanguagePair }}</span>
        </div>
        <div class="workbench-ribbon__top-actions" aria-label="任务操作">
          <button
            class="workbench-ribbon__top-action"
            data-testid="workbench-save-button"
            type="button"
            :disabled="segmentStore.saving"
            @click="saveNow"
          >
            <Loader2 v-if="segmentStore.saving" class="lucide-spin" :size="15" />
            <Save v-else :size="15" />
            <span>{{ segmentStore.saving ? t('common.actions.saving') : t('workbench.saveNow') }}</span>
          </button>
          <button class="workbench-ribbon__top-action" type="button" @click="void openSaveToTMDialog()">
            <FileCheck :size="15" />
            <span>{{ t('workbench.saveToTM') }}</span>
          </button>
          <button
            class="workbench-ribbon__top-action"
            data-testid="workbench-export-button"
            type="button"
            :disabled="!segmentStore.canExport"
            :title="exportButtonTitle"
            @click="exportTranslatedFile"
          >
            <Download :size="15" />
            <span>{{ exportButtonLabel }}</span>
          </button>
        </div>
        <button
          class="workbench-ribbon__help"
          type="button"
          :title="t('workbench.shortcuts')"
          :aria-label="t('workbench.shortcuts')"
          @click="showShortcutHelp = true"
        >
          <CircleHelp :size="16" />
        </button>
      </div>

      <div class="workbench-ribbon__ai-strip" aria-label="AI">
        <label class="ai-strip__field">
          <span>{{ t('workbench.aiScopeShort') }}</span>
          <select v-model="llmScope" class="field__control">
            <option v-for="option in llmScopeOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="ai-strip__field">
          <span>{{ t('workbench.aiProviderShort') }}</span>
          <select v-model="llmProvider" class="field__control">
            <option v-for="option in llmProviderOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="ai-strip__field ai-strip__field--model">
          <span>{{ t('workbench.aiModelShort') }}</span>
          <select v-model="llmModel" class="field__control">
            <option v-for="option in llmModelSelectOptions" :key="option.id" :value="option.id">
              {{ option.name }}
            </option>
          </select>
        </label>
        <button
          v-if="!segmentStore.llmRunning"
          class="ai-strip__button ai-strip__button--primary"
          type="button"
          :title="segmentStore.llmMessage"
          @click="runLLMTranslation"
        >
          <Bot :size="15" />
          <span>{{ t('workbench.runAi') }}</span>
        </button>
        <button
          v-else
          class="ai-strip__button ai-strip__button--danger"
          type="button"
          @click="stopLLMTranslation"
        >
          <Loader2 class="lucide-spin" :size="15" />
          <span>{{ t('workbench.stopAi') }}</span>
        </button>
        <button
          class="ai-strip__button"
          type="button"
          :class="{ 'is-active': showGuidelinesPanel }"
          @click="void toggleGuidelinesPanel()"
        >
          <FileText :size="15" />
          <span>{{ t('workbench.guidelinesShort') }}</span>
        </button>
        <button class="ai-strip__button" type="button" aria-disabled="true" @click="showRibbonPlaceholder(t('workbench.ribbon.autoTagging'))">
          <Type :size="15" />
          <span>{{ t('workbench.ribbon.autoTagging') }}</span>
        </button>
      </div>

      <div class="workbench-ribbon__container container">
        <div class="tool-group tool-group--confirm workbench-confirm-menu">
          <button
            class="tool-col tool-col--big tool-button"
            data-testid="workbench-confirm-menu"
            type="button"
            :class="{ active: openConfirmMenu }"
            :disabled="confirmationActionLoading || segmentStore.totalSegmentCount === 0"
            :aria-expanded="openConfirmMenu"
            aria-haspopup="menu"
            @click.stop="toggleConfirmMenu"
          >
            <span class="tool-line line1 with-big-icon">
              <span class="icon-text-area has_dropdown">
                <Loader2 v-if="confirmationActionLoading" class="lucide-spin tool-single-icon" :size="28" />
                <Check v-else class="tool-single-icon tool-single-icon--confirm" :size="28" />
              </span>
              <span class="dropdown-link" aria-hidden="true">
                <ChevronDown :size="12" />
              </span>
            </span>
            <span class="tool-line"><span class="label">{{ t('workbench.ribbon.confirmSegment') }}</span></span>
          </button>
          <div v-if="openConfirmMenu" class="workbench-confirm-menu__dropdown" role="menu">
            <button
              data-testid="workbench-confirm-current"
              type="button"
              role="menuitem"
              :disabled="confirmationActionLoading || !activeSegment"
              @click="handleConfirmCurrentFromMenu"
            >
              确认当前句段
            </button>
            <button
              data-testid="workbench-confirm-all"
              type="button"
              role="menuitem"
              :disabled="confirmationActionLoading || confirmableSegmentCount === 0"
              @click="void handleConfirmAllSegments()"
            >
              全部确认
            </button>
            <button
              class="is-danger"
              data-testid="workbench-cancel-all-confirmations"
              type="button"
              role="menuitem"
              :disabled="confirmationActionLoading || confirmedSegmentCount === 0"
              @click="void handleCancelAllSegmentConfirmations()"
            >
              全部取消
            </button>
          </div>
        </div>

        <div class="tool-group">
          <span class="tool-col">
            <button class="tool-line tool-button" type="button" :disabled="!activeSegment" @click="undoActiveSegmentEdit">
              <span class="icon-text-area">
                <span class="tool-item">
                  <Undo2 class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.undo') }}</span>
                </span>
              </span>
            </button>
            <button class="tool-line tool-button" type="button" :disabled="!activeSegment" @click="redoActiveSegmentEdit">
              <span class="icon-text-area">
                <span class="tool-item">
                  <Redo2 class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.redo') }}</span>
                </span>
              </span>
            </button>
          </span>
        </div>

        <div class="tool-group">
          <span class="tool-col align-left">
            <button class="tool-line tool-button" type="button" :disabled="!activeSegment" @click="copySourceToTarget">
              <span class="icon-text-area has_dropdown">
                <span class="tool-item">
                  <Copy class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.copySource') }}</span>
                </span>
              </span>
              <span class="dropdown-link" aria-hidden="true">
                <ChevronDown :size="11" />
              </span>
            </button>
            <button class="tool-line tool-button" type="button" :disabled="!activeSegment" @click="clearActiveTarget">
              <span class="icon-text-area">
                <span class="tool-item">
                  <X class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.clearTarget') }}</span>
                </span>
              </span>
            </button>
          </span>
        </div>

        <div class="tool-group custom-style">
          <span class="tool-col align-left">
            <button class="tool-line style-item tool-button" type="button" aria-disabled="true" :title="t('workbench.ribbon.bold')" @click="showRibbonPlaceholder(t('workbench.ribbon.bold'))">
              <span class="icon-text-area"><span class="tool-item"><Bold class="tool-label-icon" :size="15" /></span></span>
            </button>
            <button class="tool-line style-item tool-button" type="button" aria-disabled="true" :title="t('workbench.ribbon.strike')" @click="showRibbonPlaceholder(t('workbench.ribbon.strike'))">
              <span class="icon-text-area"><span class="tool-item"><Strikethrough class="tool-label-icon" :size="15" /></span></span>
            </button>
          </span>
          <span class="tool-col align-left">
            <button class="tool-line style-item tool-button" type="button" aria-disabled="true" :title="t('workbench.ribbon.italic')" @click="showRibbonPlaceholder(t('workbench.ribbon.italic'))">
              <span class="icon-text-area"><span class="tool-item"><Italic class="tool-label-icon" :size="15" /></span></span>
            </button>
            <button class="tool-line style-item tool-button" type="button" aria-disabled="true" :title="t('workbench.ribbon.superscript')" @click="showRibbonPlaceholder(t('workbench.ribbon.superscript'))">
              <span class="icon-text-area"><span class="tool-item"><Superscript class="tool-label-icon" :size="15" /></span></span>
            </button>
          </span>
          <span class="tool-col align-left">
            <button class="tool-line style-item tool-button" type="button" aria-disabled="true" :title="t('workbench.ribbon.underline')" @click="showRibbonPlaceholder(t('workbench.ribbon.underline'))">
              <span class="icon-text-area"><span class="tool-item"><Underline class="tool-label-icon" :size="15" /></span></span>
            </button>
            <button class="tool-line style-item tool-button" type="button" aria-disabled="true" :title="t('workbench.ribbon.subscript')" @click="showRibbonPlaceholder(t('workbench.ribbon.subscript'))">
              <span class="icon-text-area"><span class="tool-item"><Subscript class="tool-label-icon" :size="15" /></span></span>
            </button>
          </span>
          <span class="tool-col align-left">
            <button class="tool-line style-item tool-button" type="button" aria-disabled="true" :title="t('workbench.ribbon.caseChange')" @click="showRibbonPlaceholder(t('workbench.ribbon.caseChange'))">
              <span class="icon-text-area has_dropdown"><span class="tool-item"><Type class="tool-label-icon" :size="15" /></span></span>
              <span class="dropdown-link" aria-hidden="true">
                <ChevronDown :size="10" />
              </span>
            </button>
            <button class="tool-line style-item tool-button" type="button" aria-disabled="true" :title="t('workbench.ribbon.visibleCharacters')" @click="showRibbonPlaceholder(t('workbench.ribbon.visibleCharacters'))">
              <span class="icon-text-area"><span class="tool-item"><Sigma class="tool-label-icon" :size="15" /></span></span>
            </button>
          </span>
        </div>

        <div class="tool-group">
          <button class="tool-col tool-col--big tool-button" type="button" aria-disabled="true" @click="showRibbonPlaceholder(t('workbench.ribbon.clearFormat'))">
            <span class="tool-line line1 with-big-icon">
              <span class="icon-text-area has_dropdown">
                <BrushCleaning class="tool-single-icon" :size="27" />
              </span>
              <span class="dropdown-link" aria-hidden="true">
                <ChevronDown :size="12" />
              </span>
            </span>
            <span class="tool-line"><span class="label">{{ t('workbench.ribbon.clearFormat') }}</span></span>
          </button>
        </div>

        <div class="tool-group">
          <button class="tool-col tool-col--big tool-button" type="button" aria-disabled="true" @click="showRibbonPlaceholder(t('workbench.ribbon.editSource'))">
            <span class="tool-line line1 with-big-icon">
              <span class="icon-text-area">
                <SquarePen class="tool-single-icon" :size="27" />
              </span>
            </span>
            <span class="tool-line"><span class="label">{{ t('workbench.ribbon.editSource') }}</span></span>
          </button>
        </div>

        <div class="tool-group">
          <span class="tool-col align-left">
            <button class="tool-line tool-button" type="button" aria-disabled="true" @click="showRibbonPlaceholder(t('workbench.ribbon.mergeSegment'))">
              <span class="icon-text-area">
                <span class="tool-item disabled">
                  <Combine class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.mergeSegment') }}</span>
                </span>
              </span>
            </button>
            <button class="tool-line tool-button" type="button" aria-disabled="true" @click="showRibbonPlaceholder(t('workbench.ribbon.splitSegment'))">
              <span class="icon-text-area">
                <span class="tool-item">
                  <Split class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.splitSegment') }}</span>
                </span>
              </span>
            </button>
          </span>
        </div>

        <div class="tool-group">
          <button class="tool-col tool-col--big tool-button" type="button" :class="{ active: segmentSearchOpen }" @click="void toggleSegmentSearchPanel()">
            <span class="tool-line line1 with-big-icon">
              <span class="icon-text-area">
                <Search class="tool-single-icon" :size="28" />
              </span>
            </span>
            <span class="tool-line"><span class="label">{{ t('workbench.search.title') }}</span></span>
          </button>
        </div>

        <div class="tool-group tool-group--revision">
          <button
            class="tool-col tool-col--big tool-button workbench-revision-trigger"
            data-testid="workbench-revision-toggle"
            type="button"
            :class="{ active: revisionTraceVisible }"
            :aria-pressed="revisionTraceVisible"
            @click.stop="setRevisionTraceVisible(!revisionTraceVisible)"
          >
            <span class="tool-line line1 with-big-icon" :class="{ active: revisionTraceVisible }">
              <span class="icon-text-area">
                <Type class="tool-single-icon" :size="27" />
              </span>
            </span>
            <span class="tool-line" :class="{ active: revisionTraceVisible }"><span class="label">{{ t('workbench.ribbon.trackChanges') }}</span></span>
          </button>
          <span class="tool-col align-left revision-action-col">
            <div class="workbench-revision-menu workbench-revision-menu--ribbon">
              <button
                class="tool-line tool-button"
                data-testid="workbench-revision-accept-menu"
                type="button"
                :disabled="revisionActionLoading || !revisionTraceVisible || segmentStore.pendingRevisionCount === 0"
                @click="toggleRevisionMenu('accept')"
              >
                <span class="icon-text-area has_dropdown">
                  <span class="tool-item">
                    <FileCheck class="tool-label-icon" :size="16" />
                    <span class="text">{{ t('workbench.ribbon.acceptRevisions') }}</span>
                  </span>
                </span>
                <span class="dropdown-link" aria-hidden="true">
                  <ChevronDown :size="11" />
                </span>
              </button>
              <div v-if="openRevisionMenu === 'accept'" class="workbench-revision-menu__dropdown">
                <button
                  data-testid="workbench-revision-accept-current"
                  type="button"
                  :disabled="revisionActionLoading || !activePendingRevision"
                  @click="void handleAcceptActiveRevision()"
                >
                  {{ t('workbench.ribbon.acceptCurrentRevision') }}
                </button>
                <button
                  data-testid="workbench-revision-accept-all"
                  type="button"
                  :disabled="revisionActionLoading || !revisionTraceVisible || segmentStore.pendingRevisionCount === 0"
                  @click="void handleBatchAcceptRevisions()"
                >
                  {{ t('workbench.ribbon.acceptAllRevisions') }}
                </button>
              </div>
            </div>
            <div class="workbench-revision-menu workbench-revision-menu--ribbon">
              <button
                class="tool-line tool-button"
                data-testid="workbench-revision-reject-menu"
                type="button"
                :disabled="revisionActionLoading || !revisionTraceVisible || segmentStore.pendingRevisionCount === 0"
                @click="toggleRevisionMenu('reject')"
              >
                <span class="icon-text-area has_dropdown">
                  <span class="tool-item">
                    <X class="tool-label-icon" :size="16" />
                    <span class="text">{{ t('workbench.ribbon.rejectRevisions') }}</span>
                  </span>
                </span>
                <span class="dropdown-link" aria-hidden="true">
                  <ChevronDown :size="11" />
                </span>
              </button>
              <div v-if="openRevisionMenu === 'reject'" class="workbench-revision-menu__dropdown">
                <button
                  class="is-danger"
                  data-testid="workbench-revision-reject-current"
                  type="button"
                  :disabled="revisionActionLoading || !activePendingRevision"
                  @click="void handleRejectActiveRevision()"
                >
                  {{ t('workbench.ribbon.rejectCurrentRevision') }}
                </button>
                <button
                  class="is-danger"
                  data-testid="workbench-revision-reject-all"
                  type="button"
                  :disabled="revisionActionLoading || !revisionTraceVisible || segmentStore.pendingRevisionCount === 0"
                  @click="void handleBatchRejectRevisions()"
                >
                  {{ t('workbench.ribbon.rejectAllRevisions') }}
                </button>
              </div>
            </div>
          </span>
        </div>

        <div class="tool-group">
          <button class="tool-col tool-col--big tool-button" type="button" :disabled="addingTerm" @click="void addActiveSegmentTerm()">
            <span class="tool-line line1 with-big-icon">
              <span class="icon-text-area">
                <Loader2 v-if="addingTerm" class="lucide-spin tool-single-icon" :size="27" />
                <Languages v-else class="tool-single-icon" :size="27" />
              </span>
            </span>
            <span class="tool-line"><span class="label">{{ t('workbench.ribbon.addTerm') }}</span></span>
          </button>
        </div>

        <div class="tool-group">
          <span class="tool-col align-left">
            <button class="tool-line tool-button" type="button" aria-disabled="true" @click="showRibbonPlaceholder(t('workbench.ribbon.specialCharacters'))">
              <span class="icon-text-area">
                <span class="tool-item">
                  <Sigma class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.specialCharacters') }}</span>
                </span>
              </span>
            </button>
            <button class="tool-line tool-button" type="button" aria-disabled="true" @click="showRibbonPlaceholder(t('workbench.ribbon.qaSettings'))">
              <span class="icon-text-area has_dropdown">
                <span class="tool-item">
                  <ShieldCheck class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.qaSettings') }}</span>
                </span>
              </span>
              <span class="dropdown-link" aria-hidden="true">
                <ChevronDown :size="11" />
              </span>
            </button>
          </span>
        </div>

        <div class="tool-group">
          <span class="tool-col align-left">
            <button class="tool-line tool-button" type="button" aria-disabled="true" @click="showRibbonPlaceholder(t('workbench.ribbon.workflowForward'))">
              <span class="icon-text-area">
                <span class="tool-item disabled">
                  <ArrowRight class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.workflowForward') }}</span>
                </span>
              </span>
            </button>
            <button class="tool-line tool-button" type="button" aria-disabled="true" @click="showRibbonPlaceholder(t('workbench.ribbon.workflowBack'))">
              <span class="icon-text-area has_dropdown">
                <span class="tool-item">
                  <ArrowLeft class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.workflowBack') }}</span>
                </span>
              </span>
              <span class="dropdown-link" aria-hidden="true">
                <ChevronDown :size="11" />
              </span>
            </button>
          </span>
        </div>

        <div class="tool-group tool-group--return">
          <button class="tool-col tool-col--big tool-button" type="button" @click="goBack">
            <span class="tool-line line1 with-big-icon">
              <span class="icon-text-area">
                <ArrowLeft class="tool-single-icon" :size="27" />
              </span>
            </span>
            <span class="tool-line"><span class="label">{{ t('workbench.ribbon.return') }}</span></span>
          </button>
        </div>
      </div>

      <div class="workbench-ribbon__status" role="status" :title="ribbonStatusTitle">
        <span>{{ lastModifiedStatusText }}</span>
        <span aria-hidden="true">·</span>
        <span>{{ segmentStore.syncMessage }}</span>
        <span aria-hidden="true">·</span>
        <span>{{ segmentStore.llmMessage }}</span>
      </div>

      <div v-if="segmentStore.llmRunning" class="workbench-toolbar__progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div
              class="progress-bar__fill"
              :class="{ 'is-complete': isProgressComplete(segmentStore.llmProgressPercent) }"
              :style="{ width: `${segmentStore.llmProgressPercent}%` }"
            />
          </div>
          <span class="progress-bar__text">{{ segmentStore.llmProgressPercent }}%</span>
        </div>
        <span class="hint-text">
          {{ t('workbench.llmProcessed', {
            processed: segmentStore.llmProcessedCount,
            planned: segmentStore.llmPlannedCount,
            error: segmentStore.llmErrorCount,
          }) }}
        </span>
      </div>
    </section>

    <section v-else class="toolbar-panel workbench-toolbar">
      <div class="toolbar-panel__group workbench-toolbar__group">
        <button
          class="button workbench-action workbench-action--back workbench-toolbar__icon-btn"
          type="button"
          :title="hasProjectReturnContext ? t('workbench.backToProject') : t('workbench.backToTasks')"
          :aria-label="hasProjectReturnContext ? t('workbench.backToProject') : t('workbench.backToTasks')"
          @click="goBack"
        >
          <ArrowLeft :size="16" />
        </button>

        <label class="field field--compact workbench-toolbar__field">
          <span class="field__label">{{ t('workbench.aiScopeShort') }}</span>
          <select v-model="llmScope" class="field__control" :title="t('workbench.aiScope')">
            <option v-for="option in llmScopeOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="field field--compact workbench-toolbar__field">
          <span class="field__label">{{ t('workbench.aiProviderShort') }}</span>
          <select v-model="llmProvider" class="field__control" :title="t('workbench.aiProvider')">
            <option v-for="option in llmProviderOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="field field--compact workbench-toolbar__field workbench-toolbar__field--model">
          <span class="field__label">{{ t('workbench.aiModelShort') }}</span>
          <select v-model="llmModel" class="field__control" :title="t('workbench.aiModel')">
            <option v-for="option in llmModelSelectOptions" :key="option.id" :value="option.id">
              {{ option.name }}
            </option>
          </select>
        </label>

        <button
          class="button workbench-action"
          type="button"
          :class="{ 'is-active': showGuidelinesPanel }"
          :title="t('workbench.guidelinesToggle')"
          @click="void toggleGuidelinesPanel()"
        >
          <FileText :size="14" />
          {{ t('workbench.guidelinesShort') }}
        </button>

        <div class="workbench-toolbar__actions">
          <button
            class="button workbench-action workbench-action--save"
            data-testid="workbench-save-button"
            type="button"
            :disabled="segmentStore.saving"
            @click="saveNow"
          >
            <Loader2 v-if="segmentStore.saving" class="lucide-spin" :size="14" />
            <Save v-else :size="14" />
            {{ segmentStore.saving ? t('common.actions.saving') : t('workbench.saveNow') }}
          </button>

          <button
            class="button workbench-action workbench-action--export"
            data-testid="workbench-export-button"
            type="button"
            :disabled="!segmentStore.canExport"
            :title="exportButtonTitle"
            @click="exportTranslatedFile"
          >
            <Download :size="14" />
            {{ exportButtonLabel }}
          </button>

          <button
            class="button workbench-action"
            type="button"
            @click="void openSaveToTMDialog()"
          >
            <FileCheck :size="14" />
            {{ t('workbench.saveToTM') }}
          </button>

          <button
            class="button workbench-action workbench-action--issue"
            type="button"
            :disabled="!canOpenIssueDialog"
            :title="canOpenIssueDialog ? t('issueMarker.actions.open') : t('issueMarker.errors.missingProject')"
            @click="openIssueDialog"
          >
            <Flag :size="14" />
            {{ t('issueMarker.actions.open') }}
          </button>

          <button
            v-if="!segmentStore.llmRunning"
            class="button workbench-action workbench-action--ai"
            type="button"
            :title="segmentStore.llmMessage"
            @click="runLLMTranslation"
          >
            <Bot :size="14" />
            {{ t('workbench.runAi') }}
          </button>
          <button
            v-else
            class="button workbench-action workbench-action--stop"
            type="button"
            @click="stopLLMTranslation"
          >
            <Loader2 class="lucide-spin" :size="14" />
            {{ t('workbench.stopAi') }}
          </button>

          <button
            class="button workbench-action workbench-action--help workbench-toolbar__icon-btn"
            type="button"
            :title="t('workbench.shortcuts')"
            :aria-label="t('workbench.shortcuts')"
            @click="showShortcutHelp = true"
          >
            <CircleHelp :size="16" />
          </button>
        </div>
      </div>

      <div class="workbench-toolbar__status" role="status" :title="segmentStore.llmMessage">
        <span class="workbench-toolbar__status-sync">{{ segmentStore.syncMessage }}</span>
        <span class="workbench-toolbar__status-sep" aria-hidden="true">·</span>
        <span class="workbench-toolbar__status-llm">{{ segmentStore.llmMessage }}</span>
      </div>

      <div v-if="segmentStore.llmRunning" class="workbench-toolbar__progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div
              class="progress-bar__fill"
              :class="{ 'is-complete': isProgressComplete(segmentStore.llmProgressPercent) }"
              :style="{ width: `${segmentStore.llmProgressPercent}%` }"
            />
          </div>
          <span class="progress-bar__text">{{ segmentStore.llmProgressPercent }}%</span>
        </div>
        <span class="hint-text">
          {{ t('workbench.llmProcessed', {
            processed: segmentStore.llmProcessedCount,
            planned: segmentStore.llmPlannedCount,
            error: segmentStore.llmErrorCount,
          }) }}
        </span>
      </div>
    </section>

    <Transition name="workbench-panel-pop">
      <section
        v-if="showGuidelinesPanel"
        class="panel workbench-guidelines-panel"
        @keydown.esc.stop="closeGuidelinesPanel"
      >
        <div class="workbench-guidelines-panel__head">
          <span class="workbench-guidelines-panel__title">{{ t('workbench.guidelinesTitle') }}</span>
          <div class="workbench-guidelines-panel__actions">
            <button
              class="button button--small"
              type="button"
              :disabled="importingGuidelineTemplate || loadingGuidelineTemplates"
              @click="openGuidelineTemplateImport"
            >
              <Upload :size="13" />
              {{ importingGuidelineTemplate ? t('common.actions.saving') : t('workbench.guidelineImport') }}
            </button>
            <button
              class="button button--small workbench-guidelines-panel__close"
              type="button"
              title="收起细则"
              aria-label="收起细则"
              @click="closeGuidelinesPanel"
            >
              <X :size="13" />
            </button>
          </div>
        </div>
        <input
          ref="guidelineTemplateInputRef"
          class="workbench-guidelines-panel__file"
          type="file"
          accept=".md,.markdown,.txt"
          @change="importGuidelineTemplate"
        >
        <label class="field">
          <span class="field__label">{{ t('workbench.guidelineTemplate') }}</span>
          <select
            v-model="selectedGuidelineTemplateId"
            class="field__control"
            :disabled="loadingGuidelineTemplates"
          >
            <option value="">{{ t('workbench.guidelineTemplateNone') }}</option>
            <option v-for="template in guidelineTemplates" :key="template.id" :value="template.id">
              {{ template.name }}
            </option>
          </select>
        </label>
        <textarea
          ref="guidelinesEditorRef"
          v-model="workbenchGuidelines"
          class="field__control workbench-guidelines-panel__editor"
          rows="4"
          :placeholder="t('workbench.guidelinesPlaceholder')"
        />
        <p class="hint-text">{{ t('workbench.guidelinesHint') }}</p>
      </section>
    </Transition>

    <section v-if="false" class="panel panel--header workbench-overview">
      <div class="workbench-overview__line">
        <button
          v-for="item in statusSummary"
          :key="item.key"
          class="workbench-stat"
          :class="[`workbench-stat--${item.tone}`, { 'is-active': segmentDisplayScope === item.scope }]"
          type="button"
          :aria-pressed="segmentDisplayScope === item.scope"
          :title="item.description"
          @click="toggleSegmentSummaryScope(item.scope)"
        >
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </button>

        <p class="workbench-overview__tip">
          Tips：已确认译文表示人工保存的译文，后续批处理默认会跳过。
        </p>

        <div class="workbench-load-all">
          <span class="hint-text">
            {{ segmentStore.loadingMoreSegments ? t('workbench.loadingMore') : (segmentStore.hasMoreSegments ? t('workbench.loadMoreHint') : t('workbench.loadedAll')) }}
          </span>
          <button
            v-if="segmentStore.hasMoreSegments"
            class="button workbench-action workbench-action--load"
            type="button"
            :disabled="segmentStore.loadingAllSegments"
            @click="loadAllSegments"
          >
            {{ segmentStore.loadingAllSegments ? t('table.loading') : t('workbench.loadAll', { total: segmentStore.totalSegmentCount }) }}
          </button>
        </div>
      </div>
    </section>

    <p v-if="pageError" class="form-message is-error">{{ pageError }}</p>

    <section v-if="segmentStore.loading" class="panel">
      <div class="empty-state">
        <Loader2 class="lucide-spin" :size="32" />
        {{ t('workbench.loadingTask') }}
      </div>
    </section>

    <section v-else class="workbench-layout" :class="{ 'has-active-tool': activeTool }">
      <section class="panel panel--stretch panel--editor" :class="{ 'has-search-open': segmentSearchOpen }">
        <div class="panel-header panel-header--compact segment-editor-toolbar">
          <div class="segment-editor-toolbar__title">
            <div class="section-title section-title--tight">{{ t('workbench.editorTitle') }}</div>
            <span class="hint-text">
              当前句段 {{ segmentStore.currentPageStart }}-{{ segmentStore.currentPageEnd }} / {{ segmentStore.matchedSegmentCount }}，总句段 {{ segmentStore.totalSegmentCount }}
            </span>
          </div>
          <div class="segment-editor-toolbar__overview">
            <button
              v-for="item in statusSummary"
              :key="item.key"
              class="workbench-stat workbench-stat--compact"
              :class="[`workbench-stat--${item.tone}`, { 'is-active': segmentDisplayScope === item.scope }]"
              type="button"
              :aria-pressed="segmentDisplayScope === item.scope"
              :title="item.description"
              @click="toggleSegmentSummaryScope(item.scope)"
            >
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
            </button>
            <span class="segment-editor-toolbar__tip" title="已确认译文表示人工保存的译文，后续批处理默认会跳过。">
              Tips：已确认译文后续批处理默认跳过。
            </span>
            <span class="segment-editor-toolbar__loaded">
              {{ t('workbench.pageModeHint') }}
            </span>
          </div>
          <div class="segment-editor-toolbar__actions">
            <label class="segment-editor-toolbar__filter">
              <span class="segment-editor-toolbar__filter-label">{{ t('workbench.search.scopeLabel') }}</span>
              <select
                class="field__control segment-editor-toolbar__filter-select"
                :value="segmentDisplayScope"
                @change="handleSegmentDisplayScopeChange"
              >
                <option
                  v-for="option in segmentDisplayScopeOptions"
                  :key="option.value"
                  :value="option.value"
                >
                  {{ option.label }}
                </option>
              </select>
            </label>
          </div>
        </div>

        <Transition name="workbench-panel-pop">
          <div
            v-if="segmentSearchOpen"
            id="workbench-segment-search"
            class="workbench-search-panel"
            @keydown.esc.stop="closeSegmentSearchPanel"
          >
            <div class="workbench-search-panel__form workbench-search-panel__form--compact">
              <div class="workbench-search-panel__line">
                <label class="workbench-search-panel__field">
                  <span>{{ t('workbench.search.sourceShortLabel') }}</span>
                  <input
                    ref="sourceSearchInputRef"
                    v-model="sourceSearchQuery"
                    class="field__control"
                    type="text"
                    :placeholder="t('workbench.search.sourcePlaceholder')"
                    @keydown.enter.prevent="void focusMatchedSegment(1)"
                  />
                </label>

                <label class="workbench-search-panel__field">
                  <span>{{ t('workbench.search.targetShortLabel') }}</span>
                  <input
                    v-model="targetSearchQuery"
                    class="field__control"
                    type="text"
                    :placeholder="t('workbench.search.targetPlaceholder')"
                    @keydown.enter.prevent="void focusMatchedSegment(1)"
                  />
                </label>

                <label class="workbench-search-panel__toggle" :title="t('workbench.search.fuzzyHint')">
                  <input v-model="searchFuzzyEnabled" type="checkbox">
                  <span>{{ t('workbench.search.fuzzy') }}</span>
                </label>

                <div class="workbench-search-panel__actions">
                  <button
                    class="button workbench-action workbench-action--search workbench-search-panel__nav"
                    type="button"
                    :disabled="searchLoadingAllSegments || editorSegments.length === 0"
                    :title="t('workbench.search.prev')"
                    :aria-label="t('workbench.search.prev')"
                    @click="void focusMatchedSegment(-1)"
                  >
                    <ChevronUp :size="14" />
                  </button>
                  <button
                    class="button workbench-action workbench-action--search workbench-search-panel__nav"
                    type="button"
                    :disabled="searchLoadingAllSegments || editorSegments.length === 0"
                    :title="t('workbench.search.next')"
                    :aria-label="t('workbench.search.next')"
                    @click="void focusMatchedSegment(1)"
                  >
                    <ChevronDown :size="14" />
                  </button>
                </div>
              </div>

              <div class="workbench-search-panel__line workbench-search-panel__line--replace">
                <label class="workbench-search-panel__field workbench-search-panel__field--replace">
                  <span>{{ t('workbench.search.replaceLabel') }}</span>
                  <input
                    v-model="replaceSearchText"
                    class="field__control"
                    type="text"
                    :placeholder="t('workbench.search.replacePlaceholder')"
                    @keydown.enter.prevent="void replaceCurrentSearchMatch()"
                  />
                </label>

                <button
                  class="button workbench-search-panel__replace-button"
                  type="button"
                  :disabled="!targetSearchQuery.trim()"
                  @click="void replaceCurrentSearchMatch()"
                >
                  {{ t('workbench.search.replace') }}
                </button>
                <button
                  class="button workbench-search-panel__replace-button"
                  type="button"
                  :disabled="!targetSearchQuery.trim()"
                  @click="void replaceAllSearchMatches()"
                >
                  {{ t('workbench.search.replaceAll', { count: segmentStore.matchedSegmentCount }) }}
                </button>
                <button
                  class="button workbench-action workbench-action--clear"
                  :class="{ 'is-layout-placeholder': !hasEditorSegmentFilter && !replaceSearchText }"
                  type="button"
                  :disabled="!hasEditorSegmentFilter && !replaceSearchText"
                  :aria-hidden="!hasEditorSegmentFilter && !replaceSearchText"
                  :tabindex="hasEditorSegmentFilter || replaceSearchText ? 0 : -1"
                  @click="resetSegmentSearch"
                >
                  <X :size="14" />
                  {{ t('workbench.search.clear') }}
                </button>
                <button
                  class="button workbench-action workbench-action--search workbench-search-panel__close"
                  type="button"
                  :title="t('workbench.search.collapse')"
                  :aria-label="t('workbench.search.collapse')"
                  @click="closeSegmentSearchPanel"
                >
                  <X :size="14" />
                </button>
              </div>
            </div>

            <div class="workbench-search-panel__meta">
              <span v-if="hasEditorSegmentFilter" class="hint-text">
                {{
                  t('workbench.search.resultSummary', {
                    count: segmentStore.matchedSegmentCount,
                    total: segmentStore.totalSegmentCount,
                  })
                }}
              </span>
              <span v-if="searchLoadingAllSegments" class="hint-text">
                {{ t('workbench.search.loadingAll') }}
              </span>
            </div>
          </div>
        </Transition>

        <div class="segment-editor-shell">
          <div class="segment-editor-results">
            <div class="segment-table-head" aria-hidden="true">
              <span>句段</span>
              <span>原文</span>
              <span>译文</span>
            </div>

            <div class="segment-editor-list-stage">
              <div
                v-if="hasEditorSegmentFilter && !searchLoadingAllSegments && editorSegments.length === 0"
                class="empty-state workbench-search-empty"
              >
                <Search :size="28" />
                {{ t('workbench.search.noMatch') }}
              </div>

              <VirtualList
                v-else
                ref="virtualListRef"
                :items="editorSegments"
                :item-height="itemHeight"
                item-key="sentence_id"
                :adaptive="true"
                :active-descendant="segmentStore.activeSentenceId ? `segment-${segmentStore.activeSentenceId}` : null"
                @reach-end="handleEditorReachEnd"
              >
                <template #default="{ item, index }">
                  <SegmentEditorRow
                    :ref="(instance) => setSegmentEditorRowRef(item.sentence_id, instance)"
                    :segment="item"
                    :index="getEditorSegmentDisplayIndex(item.sentence_id, index)"
                    :active="segmentStore.activeSentenceId === item.sentence_id"
                    :pending-revision="revisionTraceVisible ? segmentStore.getRevisionTrace(item.sentence_id) : null"
                    :revision-busy="revisionActionLoading"
                    :matched-terms="segmentStore.activeSentenceId === item.sentence_id ? activeMatchedTerms : []"
                    :source-search-query="sourceSearchQuery"
                    @focus="segmentStore.setActiveSentence"
                    @activate-target="handleSegmentTargetActivate"
                    @update="updateSegmentTarget"
                    @apply-partial-revision="handleApplyPartialRevision"
                  />
                </template>
              </VirtualList>
            </div>
          </div>

          <Pagination
            :total="segmentStore.matchedSegmentCount"
            :page="segmentStore.currentPage"
            :page-size="segmentStore.pageSize"
            :page-sizes="segmentPageSizes"
            @update:page="handleSegmentPageChange"
            @update:page-size="handleSegmentPageSizeChange"
          />

        </div>
      </section>

      <div
        v-if="activeTool"
        class="workbench-resizer"
        @mousedown="startResize"
      >
        <div class="workbench-resizer__handle" />
      </div>

      <div
        v-if="activeTool"
        ref="sidecarRef"
        class="workbench-sidecar"
        :class="{ 'is-preview-open': activeTool && activeTool !== 'split-preview', 'is-split-open': activeTool === 'split-preview' }"
        :style="sidecarWidthStyle"
      >
        <div class="workbench-sidecar__panel">
        <Transition name="preview-drawer" mode="out-in">
          <SplitPreviewPanel
            v-if="activeTool === 'split-preview'"
            key="split-preview"
            :source-html="sourcePreviewHtml"
            :target-html="targetPreviewHtml"
            :source-supported="sourcePreviewSupported"
            :target-supported="targetPreviewSupported"
            :source-loading="sourcePreviewLoading"
            :target-loading="targetPreviewLoading"
            :active-sentence-id="segmentStore.activeSentenceId"
            :target-render-mode="targetPreviewRenderMode"
            :target-segments="targetPreviewRenderMode === 'target' ? segmentStore.segments : []"
            :target-updated-sentence-id="segmentStore.lastPreviewUpdatedSentenceId"
            :target-updated-sentence-text="segmentStore.lastPreviewUpdatedText"
            :target-update-token="segmentStore.previewUpdateToken"
            :comments="commentStore.comments"
            :active-comment-id="commentStore.activeCommentId"
            @close="activeTool = null"
            @focus-sentence="handlePreviewFocus"
            @focus-comment="handleCommentFocus"
            @request-comment="handleCommentDraft"
          />
          <PreviewPanel
            v-else-if="activeTool === 'source-preview' || activeTool === 'target-preview'"
            key="single-preview"
            class="preview-panel--drawer"
            :title="activeTool === 'source-preview' ? t('workbench.tools.sourcePreview') : t('workbench.tools.targetPreview')"
            :html="activeTool === 'source-preview' ? segmentStore.previewHtml : targetPreviewHtml"
            :supported="activeTool === 'source-preview' ? segmentStore.previewSupported : targetPreviewSupported"
            :loading="activeTool === 'source-preview' ? sourcePreviewLoading : targetPreviewLoading"
            :active-sentence-id="segmentStore.activeSentenceId"
            :comments="commentStore.comments"
            :active-comment-id="commentStore.activeCommentId"
            :enable-comment-selection="true"
            :render-mode="activeTool === 'target-preview' ? targetPreviewRenderMode : 'static'"
            :segments="activeTool === 'target-preview' && targetPreviewRenderMode === 'target' ? segmentStore.segments : []"
            :updated-sentence-id="activeTool === 'target-preview' ? segmentStore.lastPreviewUpdatedSentenceId : null"
            :updated-sentence-text="activeTool === 'target-preview' ? segmentStore.lastPreviewUpdatedText : ''"
            :update-token="activeTool === 'target-preview' ? segmentStore.previewUpdateToken : 0"
            @focus-sentence="handlePreviewFocus"
            @focus-comment="handleCommentFocus"
            @request-comment="handleCommentDraft"
            @close="activeTool = null"
          />
          <WorkbenchMatchPanel
            v-else-if="activeTool === 'match-info'"
            key="match-info"
            :segment="activeSegment"
            :collection-id="segmentStore.fileRecord?.collection_id || null"
            :collection-name="segmentStore.fileRecord?.collection_name || null"
            :term-base-id="selectedTermBaseId || segmentStore.fileRecord?.term_base_id || null"
            :term-base-name="selectedTermBaseName"
            :term-entries="termEntries"
            :active-source-text="activeSegmentSourceText"
            :file-record-id="segmentStore.fileRecord?.id || null"
            @replace-text="handleReplaceText"
            @append-text="handleAppendText"
          />
          <WorkbenchTermsPanel
            v-else-if="activeTool === 'terms'"
            key="terms"
            :term-bases="termBases"
            v-model:selected-term-base-id="selectedTermBaseId"
            :entries="termEntries"
            :active-source-text="activeSegmentSourceText"
            :loading-bases="loadingTermBases"
            :loading-entries="loadingTermEntries"
            :message="termsMessage"
          />
          <NotesPanel
            v-else-if="activeTool === 'notes'"
            key="notes"
            class="notes-panel--drawer"
            :comments="commentStore.comments"
            :loading="commentStore.loading"
            :saving="commentStore.saving"
            :polling="commentStore.polling"
            :active-comment-id="commentStore.activeCommentId"
            :draft-anchor="commentStore.draftAnchor"
            :current-user-id="authStore.user?.id || null"
            :message="commentStore.message"
            @close="activeTool = null"
            @select-comment="handleCommentFocus"
            @create-comment="handleCreateComment"
            @update-comment="handleUpdateComment"
            @delete-comment="handleDeleteComment"
            @reply-comment="handleReplyComment"
            @cancel-draft="commentStore.setDraftAnchor(null)"
          />
          <WorkbenchHistoryPanel
            v-else-if="activeTool === 'history'"
            key="history"
            :active-sentence-id="segmentStore.activeSentenceId"
            :comments="commentStore.comments"
            :history="activeSegmentHistory"
          />
        </Transition>
        </div>
      </div>

      <aside class="segment-editor-side-tools" aria-label="工作台工具">
        <button
          v-if="!isStandaloneWorkbench"
          class="button segment-editor-side-tool segment-editor-side-tool--focus"
          data-testid="workbench-open-focus"
          type="button"
          :title="t('workbench.openFocus')"
          :aria-label="t('workbench.openFocus')"
          @click="openFocusWorkbench"
        >
          <ExternalLink :size="16" />
          <span>{{ t('workbench.openFocus') }}</span>
        </button>
        <button
          v-for="tool in toolButtons"
          :key="tool.key"
          class="button segment-editor-side-tool"
          :class="[
            `segment-editor-side-tool--${tool.tone}`,
            { 'is-active': activeTool === tool.key },
          ]"
          type="button"
          :title="tool.label"
          :aria-pressed="activeTool === tool.key"
          @click="void openTool(tool.key)"
        >
          <component :is="tool.icon" :size="16" />
          <span>{{ tool.label }}</span>
        </button>
      </aside>
    </section>

    <ResourceImportDialog
      :open="showImportDialog"
      :initial-tab="importDialogInitialTab"
      :context-label="t('workbench.importContext', { name: segmentStore.fileRecord?.filename || t('workbench.currentTask') })"
      @close="showImportDialog = false"
      @imported="handleResourceImported"
    />

    <IssueMarkerDialog
      :open="showIssueDialog"
      :project-id="segmentStore.fileRecord?.project_id || projectReturnId || null"
      :file-record-id="segmentStore.fileRecord?.id || props.id"
      :context-label="segmentStore.fileRecord?.filename || t('workbench.currentTask')"
      @close="showIssueDialog = false"
      @saved="handleIssueSaved"
    />

    <Modal
      :open="showSaveToTMDialog"
      :title="t('workbench.saveToTMTitle')"
      :description="t('workbench.saveToTMDescription')"
      width="620px"
      @close="showSaveToTMDialog = false"
    >
      <div class="save-to-tm-dialog">
        <fieldset class="save-to-tm-dialog__scope">
          <legend>{{ t('workbench.saveToTMTargetModeLabel') }}</legend>
          <label class="save-to-tm-dialog__scope-item">
            <input v-model="saveToTMTargetMode" type="radio" value="new">
            <span>{{ t('workbench.saveToTMTargetModes.new') }}</span>
          </label>
          <label class="save-to-tm-dialog__scope-item">
            <input v-model="saveToTMTargetMode" type="radio" value="existing" :disabled="loadingTMCollections || tmCollections.length === 0">
            <span>{{ t('workbench.saveToTMTargetModes.existing') }}</span>
          </label>
        </fieldset>

        <label v-if="saveToTMTargetMode === 'new'" class="field">
          <span class="field__label">{{ t('workbench.saveToTMNewCollectionName') }}</span>
          <input
            v-model="saveToTMNewCollectionName"
            class="field__control"
            type="text"
            :placeholder="buildDefaultSaveToTMCollectionName()"
          >
        </label>

        <label v-else class="field">
          <span class="field__label">{{ t('workbench.saveToTMCollection') }}</span>
          <select
            v-model="saveToTMCollectionId"
            class="field__control"
            :disabled="loadingTMCollections"
          >
            <option value="">{{ t('workbench.saveToTMCollectionPlaceholder') }}</option>
            <option v-for="collection in orderedSaveToTMCollections" :key="collection.id" :value="collection.id">
              {{ collection.name }}{{ collection.id === taskTMCollectionId ? t('workbench.saveToTMTaskCollectionSuffix') : '' }}
            </option>
          </select>
        </label>

        <fieldset class="save-to-tm-dialog__scope">
          <legend>{{ t('workbench.saveToTMScopeLabel') }}</legend>
          <label class="save-to-tm-dialog__scope-item">
            <input v-model="saveToTMScope" type="radio" value="translated">
            <span>{{ t('workbench.saveToTMScope.translated') }}</span>
          </label>
          <label class="save-to-tm-dialog__scope-item">
            <input v-model="saveToTMScope" type="radio" value="confirmed">
            <span>{{ t('workbench.saveToTMScope.confirmed') }}</span>
          </label>
        </fieldset>

        <div class="save-to-tm-dialog__stats">
          <span>{{ t('workbench.saveToTMPreviewMatched', { count: saveToTMPreviewStats.matchedCount }) }}</span>
          <span>{{ t('workbench.saveToTMPreviewValid', { count: saveToTMPreviewStats.validCount }) }}</span>
          <span>{{ t('workbench.saveToTMPreviewSkipped', { count: saveToTMPreviewStats.skippedCount }) }}</span>
        </div>

        <div class="save-to-tm-dialog__actions">
          <button class="button button--ghost" type="button" @click="showSaveToTMDialog = false">
            {{ t('common.actions.cancel') }}
          </button>
          <button class="button" type="button" :disabled="!saveToTMCanSubmit" @click="void saveToTM()">
            <Loader2 v-if="savingToTM" class="lucide-spin" :size="14" />
            <span>{{ savingToTM ? t('common.actions.saving') : t('workbench.saveToTMSubmit') }}</span>
          </button>
        </div>
      </div>
    </Modal>

    <Modal
      :open="showShortcutHelp"
      :title="t('workbench.shortcutDialogTitle')"
      :description="t('workbench.shortcutDialogDescription')"
      width="520px"
      @close="showShortcutHelp = false"
    >
      <div class="shortcut-list">
        <div class="shortcut-item"><strong>Ctrl + S</strong><span>{{ t('workbench.shortcutItems.save') }}</span></div>
        <div class="shortcut-item"><strong>Ctrl + Shift + T</strong><span>{{ t('workbench.shortcutItems.runAi') }}</span></div>
        <div class="shortcut-item"><strong>Ctrl + Enter</strong><span>{{ t('workbench.shortcutItems.confirm') }}</span></div>
        <div class="shortcut-item"><strong>Alt + ↑ / ↓</strong><span>{{ t('workbench.shortcutItems.move') }}</span></div>
        <div class="shortcut-item"><strong>Esc</strong><span>{{ t('workbench.shortcutItems.closePanel') }}</span></div>
        <div class="shortcut-item"><strong>?</strong><span>{{ t('workbench.shortcutItems.help') }}</span></div>
      </div>
    </Modal>
  </div>
</template>

<style scoped>
.workbench-toolbar,
.workbench-toolbar__progress,
.workbench-load-all,
.workbench-search-panel {
  display: grid;
  gap: 6px;
}

.workbench-page {
  --workbench-editor-stage-height: clamp(420px, calc(100vh - 410px), 660px);
  overflow-x: hidden;
  overflow-x: clip;
}

.workbench-page.is-standalone {
  --workbench-editor-stage-height: clamp(420px, calc(100vh - 440px), 620px);
  min-height: 100vh;
  padding: 0 10px 14px;
  border-radius: 0;
  box-shadow: none;
}

.workbench-ribbon {
  position: sticky;
  top: 0;
  z-index: 1200;
  gap: 0;
  padding: 0;
  border-radius: 0;
  background: #f7f9fb;
  box-shadow: 0 4px 10px rgba(17, 49, 42, 0.08);
}

.workbench-page .workbench-ribbon {
  padding: 0;
}

.workbench-ribbon__tabs {
  display: flex;
  align-items: center;
  min-height: 40px;
  border-bottom: 1px solid #d8e0e5;
  background: linear-gradient(180deg, #eef2f5, #e7edf1);
}

.workbench-ribbon__tab {
  align-self: stretch;
  min-width: 90px;
  padding: 0 16px;
  border: 0;
  border-right: 1px solid #d8e0e5;
  background: transparent;
  color: #17313b;
  font-weight: 600;
  box-shadow: none;
}

.workbench-ribbon__tab.is-active {
  border-bottom: 2px solid #0d7a68;
  background: #fff;
}

.workbench-ribbon__task {
  display: flex;
  flex: 1 1 auto;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
  padding: 0 14px;
  color: var(--text-secondary);
  font-size: 12px;
}

.workbench-ribbon__task strong {
  max-width: min(520px, 38vw);
  overflow: hidden;
  color: var(--text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workbench-ribbon__top-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  flex: 0 0 auto;
  margin-left: auto;
  padding: 0 6px;
}

.workbench-ribbon__top-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 28px;
  min-width: max-content;
  padding: 4px 8px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: #44545c;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
  box-shadow: none;
}

.workbench-ribbon__top-action svg {
  flex: 0 0 auto;
  color: #74b8e9;
}

.workbench-ribbon__top-action span {
  flex: 0 0 auto;
}

.workbench-ribbon__top-action:hover:not(:disabled),
.workbench-ribbon__top-action:focus-visible {
  border-color: #c6d8e2;
  background: #fff;
  color: #1f4250;
  outline: none;
}

.workbench-ribbon__top-action:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}

.workbench-ribbon__help {
  display: inline-grid;
  place-items: center;
  width: 32px;
  height: 32px;
  margin-right: 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  box-shadow: none;
}

.workbench-ribbon__help:hover {
  border-color: var(--line-soft);
  background: #fff;
  color: var(--brand-700);
}

.workbench-ribbon__container {
  position: relative;
  z-index: 3;
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 0;
  min-height: 68px;
  overflow: visible;
  border-top: 1px solid #edf2f5;
  background: #fff;
}

.workbench-ribbon__ai-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 4px 10px;
  border-top: 1px solid #cce3ec;
  border-bottom: 1px solid #c5dfe9;
  background: linear-gradient(180deg, #edf8fb 0%, #dff1f6 100%);
  color: #164d60;
}

.ai-strip__field {
  display: grid;
  grid-template-columns: auto minmax(96px, 140px);
  align-items: center;
  gap: 5px;
  min-width: 0;
  color: #2d6274;
  font-size: 11px;
}

.ai-strip__field--model {
  grid-template-columns: auto minmax(150px, 240px);
}

.ai-strip__field .field__control {
  min-height: 25px;
  padding: 2px 18px 2px 6px;
  border-color: #9fc7d5;
  border-radius: 3px;
  background-color: #fff;
  color: #163f4d;
  font-size: 11px;
}

.ai-strip__button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 25px;
  padding: 3px 8px;
  border: 1px solid #9fc7d5;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.62);
  color: #17495a;
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
  box-shadow: none;
}

.ai-strip__button:hover:not(:disabled),
.ai-strip__button:focus-visible {
  border-color: #6daabd;
  background: #fff;
  outline: none;
}

.ai-strip__button[aria-disabled='true'] {
  color: #5f7f89;
}

.ai-strip__button.is-active {
  border-color: #4197ad;
  background: #fff;
  color: #0f596d;
  box-shadow: inset 0 -2px 0 #4197ad;
}

.ai-strip__button--primary {
  border-color: #0f7d78;
  background: linear-gradient(180deg, #1f9d95, #0f756f);
  color: #fff;
}

.ai-strip__button--primary:hover:not(:disabled) {
  border-color: #0b6762;
  background: linear-gradient(180deg, #28aaa2, #0f756f);
}

.ai-strip__button--danger {
  border-color: #bf5457;
  background: linear-gradient(180deg, #d86b70, #b83c41);
  color: #fff;
}

.tool-group {
  display: inline-flex;
  align-items: center;
  flex: 0 1 auto;
  min-width: 0;
  min-height: 68px;
  padding: 3px 4px 5px;
  border-right: 1px solid #dde5ea;
  background: linear-gradient(180deg, #fff 0%, #fbfdfe 100%);
}

.tool-group--return {
  order: -1;
  flex: 0 0 auto;
  margin-left: 0;
  border-left: 0;
  background: #f7fafb;
}

.tool-group--revision {
  position: relative;
  gap: 4px;
  overflow: visible;
}

.tool-group--confirm {
  position: relative;
  overflow: visible;
}

.workbench-confirm-menu__dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 4px;
  z-index: 1250;
  display: grid;
  min-width: 136px;
  padding: 4px;
  border: 1px solid var(--line-soft);
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: 0 8px 20px rgba(20, 45, 55, 0.16);
}

.workbench-confirm-menu__dropdown button {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 30px;
  padding: 6px 9px;
  border: none;
  border-radius: 3px;
  background: transparent;
  color: var(--text-primary);
  font-size: 12px;
  text-align: left;
  white-space: nowrap;
}

.workbench-confirm-menu__dropdown button:hover:not(:disabled) {
  background: rgba(13, 122, 104, 0.08);
}

.workbench-confirm-menu__dropdown button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.workbench-confirm-menu__dropdown button.is-danger {
  color: #a43a3d;
}

.workbench-confirm-menu__dropdown button.is-danger:hover:not(:disabled) {
  background: rgba(194, 59, 63, 0.08);
}

.tool-col {
  display: inline-grid;
  grid-template-rows: 24px 24px;
  align-content: center;
  align-items: center;
  gap: 1px;
  min-width: 0;
}

.tool-col + .tool-col {
  margin-left: 3px;
}

.tool-col.align-left {
  justify-items: start;
}

.revision-action-col {
  width: 108px;
}

.tool-col--big {
  grid-template-rows: 39px 16px;
  justify-items: center;
  width: 58px;
  min-height: 58px;
  padding: 1px 2px 2px;
}

.tool-line {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  height: 24px;
  color: #43545c;
  font-size: 11px;
  line-height: 1;
}

.tool-line.line1.with-big-icon {
  position: relative;
  justify-content: center;
  width: 100%;
  height: 39px;
}

.tool-line.active,
.tool-button.active,
.tool-button.active-soft {
  color: #0a705f;
}

.tool-button {
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  box-shadow: none;
  cursor: pointer;
}

.tool-button.tool-line {
  justify-content: flex-start;
  width: 100%;
  max-width: none;
  padding: 1px 3px;
}

.tool-button.tool-col {
  color: #43545c;
}

.tool-button:hover:not(:disabled),
.tool-button:focus-visible {
  border-color: #b8cbd4;
  background: linear-gradient(180deg, #f8fbfc 0%, #edf5f7 100%);
  outline: none;
}

.tool-button:disabled {
  cursor: not-allowed;
  opacity: 0.44;
}

.tool-button[aria-disabled='true'] {
  color: #7e8a90;
}

.tool-button.danger-soft {
  color: #a73a3f;
}

.tool-button.danger-soft:hover:not(:disabled) {
  border-color: #e0b3b6;
  background: #fff3f2;
}

.icon-text-area {
  display: inline-flex;
  align-items: center;
  min-width: 0;
}

.icon-text-area.has_dropdown {
  padding-right: 2px;
}

.tool-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.tool-item.disabled {
  opacity: 0.62;
}

.tool-label-icon {
  flex: 0 0 auto;
  color: #747d82;
  stroke-width: 2;
}

.tool-single-icon {
  flex: 0 0 auto;
  color: #6d767b;
  stroke-width: 1.9;
}

.tool-single-icon--confirm {
  color: #3c9b55;
  stroke-width: 2.35;
}

.dropdown-link {
  display: inline-grid;
  place-items: center;
  flex: 0 0 auto;
  width: 12px;
  color: #69767d;
}

.line1.with-big-icon .dropdown-link {
  position: absolute;
  right: 1px;
  bottom: 4px;
}

.label,
.text {
  display: inline-block;
  min-width: 0;
  white-space: nowrap;
}

.label {
  max-width: none;
  color: #334a55;
  font-size: 11px;
  text-align: center;
}

.text {
  max-width: none;
  color: currentColor;
}

.custom-style {
  gap: 0;
  padding-right: 5px;
  padding-left: 5px;
}

.custom-style .tool-col {
  width: 24px;
}

.custom-style .tool-col + .tool-col {
  margin-left: 2px;
}

.style-item.tool-button {
  justify-content: center;
  width: 23px;
  height: 23px;
  padding: 0;
}

.style-item .dropdown-link {
  width: 8px;
  margin-left: -3px;
}

.workbench-revision-menu--ribbon {
  position: relative;
  width: 100%;
}

.workbench-revision-menu--ribbon .tool-button {
  justify-content: space-between;
  min-width: 104px;
}

.workbench-revision-menu--ribbon .workbench-revision-menu__dropdown {
  top: calc(100% + 4px);
  left: 0;
  z-index: 1250;
  min-width: 146px;
  padding: 4px;
  border-radius: 4px;
  box-shadow: 0 8px 20px rgba(20, 45, 55, 0.16);
}

.workbench-revision-menu--ribbon .workbench-revision-menu__dropdown button {
  min-height: 30px;
  padding: 6px 9px;
  border-radius: 3px;
  font-size: 12px;
}

.workbench-revision-menu__dropdown.workbench-revision-menu__dropdown--track {
  top: calc(100% + 4px);
  left: 4px;
  z-index: 1250;
  min-width: 136px;
  padding: 4px;
  border-radius: 4px;
  box-shadow: 0 8px 20px rgba(20, 45, 55, 0.16);
}

.workbench-revision-menu__dropdown.workbench-revision-menu__dropdown--track button {
  justify-content: space-between;
  min-height: 30px;
  gap: 12px;
  padding: 6px 9px;
  border-radius: 3px;
  font-size: 12px;
}

.workbench-revision-menu__dropdown button.is-selected {
  color: #0070c0;
}

.workbench-revision-menu__check {
  flex: 0 0 auto;
  stroke-width: 2.3;
}

.workbench-ribbon__status {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 24px;
  padding: 3px 10px;
  border-top: 1px solid #e5ebef;
  background: #f9fbfc;
  color: var(--text-muted);
  font-size: 11px;
}

.workbench-ribbon__status span:not([aria-hidden='true']) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workbench-ribbon__status span:first-child {
  flex: 0 0 auto;
  max-width: min(360px, 44vw);
  color: #40515a;
}

.workbench-ribbon__status span:last-child {
  flex: 1 1 auto;
}

.workbench-ribbon__status span[aria-hidden='true'] {
  flex: 0 0 auto;
}

.workbench-page.is-standalone .workbench-sidecar {
  top: 174px;
  max-height: calc(100vh - 194px);
}

.workbench-page.is-standalone .workbench-sidecar__panel {
  max-height: calc(100vh - 194px);
}

.workbench-page.is-standalone .workbench-sidecar__panel > .preview-panel,
.workbench-page.is-standalone .workbench-sidecar__panel > .split-preview {
  height: calc(100vh - 194px);
}

.workbench-page.is-standalone .segment-editor-side-tools {
  top: 174px;
  max-height: calc(100vh - 194px);
}

.workbench-toolbar {
  min-height: var(--route-top-panel-min-height, 90px);
  align-content: center;
}

.workbench-toolbar__group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
}

.workbench-toolbar__group > .workbench-action--back {
  align-self: center;
  margin-top: 0;
}

.workbench-toolbar__field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 1 auto;
  min-width: 0;
  max-width: min(220px, 42vw);
  min-height: 32px;
}

.workbench-toolbar__field--model {
  max-width: min(300px, 58vw);
}

.workbench-toolbar__field .field__label {
  flex: 0 0 auto;
  font-size: 12px;
  line-height: 32px;
  color: var(--text-muted, #64748b);
  white-space: nowrap;
}

.workbench-toolbar__field .field__control {
  flex: 1 1 auto;
  width: auto;
  min-width: 120px;
  min-height: 32px;
  height: 32px;
  padding: 4px 8px;
  font-size: 12px;
  border-radius: 6px;
}

.workbench-toolbar__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  align-self: center;
  gap: 6px;
  margin-left: auto;
}

.workbench-toolbar__icon-btn {
  min-width: 34px;
  min-height: 32px;
  padding: 4px 8px;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(37, 61, 70, 0.08);
}

.workbench-action--back.workbench-toolbar__icon-btn {
  min-width: 46px;
  min-height: 32px;
  padding-inline: 12px;
}

.workbench-toolbar__status {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 6px;
  max-width: 100%;
  padding: 2px 0 0;
  border: none;
  background: transparent;
  color: var(--text-muted, #64748b);
  font-size: 11px;
  line-height: 1.35;
}

.workbench-toolbar__status-sep {
  opacity: 0.45;
  user-select: none;
}

.workbench-toolbar__status-llm {
  flex: 1 1 160px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workbench-toolbar__status-sync {
  flex: 0 1 auto;
  white-space: nowrap;
}

.save-to-tm-dialog {
  display: grid;
  gap: 12px;
}

.save-to-tm-dialog__scope {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--border-color, #dbe3ea);
  border-radius: 10px;
  display: grid;
  gap: 8px;
}

.save-to-tm-dialog__scope-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.save-to-tm-dialog__stats {
  display: grid;
  gap: 4px;
  color: var(--text-muted, #64748b);
  font-size: 12px;
}

.save-to-tm-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.workbench-toolbar__progress {
  gap: 4px;
}

.workbench-toolbar__progress .progress-bar__track {
  height: 6px;
}

.workbench-toolbar__progress .progress-bar__text {
  font-size: 11px;
}

.workbench-toolbar__progress .hint-text {
  font-size: 11px;
  line-height: 1.3;
}

.workbench-toolbar .workbench-action:not(.workbench-toolbar__icon-btn) {
  min-height: 32px;
  padding: 5px 10px;
  font-size: 12px;
  gap: 4px;
}

.workbench-toolbar .workbench-action.is-active {
  border-color: #69ad9d;
  background: linear-gradient(180deg, #e2f2ee, #cbe6df);
  color: #0b6658;
  box-shadow: 0 5px 14px rgba(13, 122, 104, 0.16);
}

.workbench-guidelines-panel {
  position: relative;
  padding: 10px 14px;
  display: grid;
  gap: 8px;
  overflow: hidden;
  transform-origin: top center;
}

.workbench-guidelines-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.workbench-guidelines-panel__title {
  font-weight: 600;
  font-size: 13px;
}

.workbench-guidelines-panel__actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.workbench-guidelines-panel__close,
.workbench-search-panel__close {
  min-width: 34px;
  padding-inline: 9px;
}

.workbench-guidelines-panel__file {
  display: none;
}

.workbench-guidelines-panel__editor {
  resize: vertical;
  min-height: 60px;
  max-height: 200px;
  font-size: 12px;
  line-height: 1.5;
}

.workbench-action {
  --action-bg: linear-gradient(180deg, #f4f7f8, #e8eef1);
  --action-border: #ccd9de;
  --action-color: #2d4651;
  --action-shadow: rgba(37, 61, 70, 0.08);
  --action-hover-shadow: rgba(37, 61, 70, 0.12);

  border-color: var(--action-border);
  background: var(--action-bg);
  color: var(--action-color);
  font-weight: 600;
  box-shadow: 0 3px 8px var(--action-shadow);
  transition:
    border-color 160ms ease,
    background 160ms ease,
    color 160ms ease,
    box-shadow 160ms ease,
    transform 160ms ease;
}

.workbench-action:not(:disabled):hover {
  border-color: color-mix(in srgb, var(--action-border) 82%, #17313b);
  box-shadow: 0 4px 12px var(--action-hover-shadow);
  transform: translateY(-1px);
}

.workbench-action:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--action-border) 36%, transparent);
  outline-offset: 2px;
}

.workbench-action--save,
.workbench-action--ai {
  --action-bg: linear-gradient(180deg, #2f9786, #0d7a68);
  --action-border: #0d7a68;
  --action-color: #ffffff;
  --action-shadow: rgba(13, 122, 104, 0.2);
  --action-hover-shadow: rgba(13, 122, 104, 0.28);
}

.workbench-action--export {
  --action-bg: linear-gradient(180deg, #3f85c6, #2268a8);
  --action-border: #2268a8;
  --action-color: #ffffff;
  --action-shadow: rgba(34, 104, 168, 0.18);
  --action-hover-shadow: rgba(34, 104, 168, 0.26);
}

.workbench-action--review {
  --action-bg: linear-gradient(180deg, #ffe8a8, #f4ce72);
  --action-border: #dfb653;
  --action-color: #68430c;
  --action-shadow: rgba(154, 102, 13, 0.12);
  --action-hover-shadow: rgba(154, 102, 13, 0.2);
}

.workbench-action--issue {
  --action-bg: linear-gradient(180deg, #fff5df, #f4ddb2);
  --action-border: #d9b86f;
  --action-color: #6d4a12;
  --action-shadow: rgba(148, 103, 20, 0.1);
  --action-hover-shadow: rgba(148, 103, 20, 0.18);
}

.workbench-action--stop {
  --action-bg: linear-gradient(180deg, #d75f63, #b83c41);
  --action-border: #ad363a;
  --action-color: #ffffff;
  --action-shadow: rgba(184, 60, 65, 0.2);
  --action-hover-shadow: rgba(184, 60, 65, 0.28);
}

.workbench-action--help {
  --action-bg: linear-gradient(180deg, #eef1fb, #dde5f7);
  --action-border: #c8d4ee;
  --action-color: #33486f;
  --action-shadow: rgba(51, 72, 111, 0.1);
  --action-hover-shadow: rgba(51, 72, 111, 0.16);
}

.workbench-action--import-tm,
.workbench-action--load {
  --action-bg: linear-gradient(180deg, #e4f2fb, #cfe6f5);
  --action-border: #afd1e7;
  --action-color: #24506f;
  --action-shadow: rgba(36, 80, 111, 0.1);
  --action-hover-shadow: rgba(36, 80, 111, 0.16);
}

.workbench-action--import-term {
  --action-bg: linear-gradient(180deg, #e6f4ef, #cfe9e0);
  --action-border: #abd7c7;
  --action-color: #145c4f;
  --action-shadow: rgba(20, 92, 79, 0.1);
  --action-hover-shadow: rgba(20, 92, 79, 0.16);
}

.workbench-action--clear {
  --action-bg: linear-gradient(180deg, #f7eee9, #efdcd4);
  --action-border: #dfbeb0;
  --action-color: #87452c;
  --action-shadow: rgba(135, 69, 44, 0.1);
  --action-hover-shadow: rgba(135, 69, 44, 0.16);
}

.workbench-action--search,
.workbench-action--back {
  --action-bg: linear-gradient(180deg, #f3f7f8, #e7eef1);
  --action-border: #cbd9df;
  --action-color: #2d4651;
  --action-shadow: rgba(45, 70, 81, 0.08);
  --action-hover-shadow: rgba(45, 70, 81, 0.14);
}

.workbench-action--review .workbench-revision-menu__badge {
  background: rgba(255, 255, 255, 0.54);
  color: #5d3d0c;
  box-shadow: inset 0 0 0 1px rgba(122, 80, 12, 0.12);
}

.workbench-load-all {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 10px;
  margin-top: 0;
  flex-wrap: wrap;
}

.workbench-load-all .hint-text {
  white-space: nowrap;
}

.workbench-search-panel {
  margin-bottom: 8px;
  padding: 8px 10px;
  border: 1px solid #d4dee5;
  border-radius: 2px;
  background: #f5f8fa;
  overflow: hidden;
  transform-origin: top center;
}

.workbench-search-panel__form--compact {
  display: grid;
  gap: 6px;
}

.workbench-search-panel__line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}

.workbench-search-panel__line--replace {
  padding-left: 231px;
}

.workbench-search-panel__field {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 0 1 auto;
  min-width: 0;
  margin: 0;
}

.workbench-search-panel__field > span {
  flex: 0 0 auto;
  color: #2f3d45;
  font-size: 13px;
  line-height: 1;
  white-space: nowrap;
}

.workbench-search-panel__field .field__control {
  width: 180px;
  min-height: 28px;
  padding: 4px 8px;
  border-color: #c7d2da;
  border-radius: 3px;
  background: #fff;
  font-size: 13px;
  box-shadow: inset 0 1px 2px rgba(17, 34, 51, 0.04);
}

.workbench-search-panel__field--replace .field__control {
  width: 180px;
}

.workbench-search-panel__toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 28px;
  margin: 0 4px 0 2px;
  color: #485861;
  font-size: 13px;
  white-space: nowrap;
}

.workbench-search-panel__toggle input {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: #0d7a68;
}

.workbench-search-panel__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.workbench-search-panel .button,
.segment-editor-toolbar__filter-select,
.segment-editor-toolbar__search {
  min-height: 34px;
  padding: 7px 10px;
  font-size: 13px;
  box-shadow: none;
}

.workbench-search-panel__nav {
  min-width: 26px;
  min-height: 28px !important;
  padding: 0 6px !important;
  border-color: #c7d2da;
  border-radius: 3px;
  background: #fff;
}

.workbench-search-panel__replace-button {
  min-height: 29px !important;
  padding: 5px 10px !important;
  border: 1px solid #c7d2da;
  border-radius: 3px;
  background: linear-gradient(180deg, #fff, #edf2f5);
  color: #2d4651;
  font-size: 13px !important;
  box-shadow: none;
}

.workbench-search-panel__replace-button:hover:not(:disabled) {
  border-color: #9fb2bd;
  background: #fff;
}

.workbench-search-panel__replace-button:disabled {
  cursor: not-allowed;
  opacity: 0.52;
}

.workbench-search-panel__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  min-height: 20px;
  padding: 0 5px;
  border-radius: 999px;
  background: rgba(13, 122, 104, 0.14);
  color: #0b6b5b;
  font-size: 11px;
}

.workbench-search-panel__badge.is-hidden {
  visibility: hidden;
}

.workbench-search-panel__meta {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 20px;
  margin-top: 4px;
}

.workbench-search-panel__actions .is-layout-placeholder {
  pointer-events: none;
  visibility: hidden;
}

.segment-editor-toolbar {
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.panel--editor,
.segment-editor-shell,
.segment-editor-results,
.segment-editor-list-stage {
  min-width: 0;
  overflow-anchor: none;
}

.segment-editor-shell {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: stretch;
  gap: 8px;
}

.segment-editor-results {
  --segment-editor-grid-template: 128px minmax(0, 1fr) minmax(0, 1fr);
  --segment-editor-scrollbar-gutter: 12px;
  --virtual-list-inline-end-gap: 4px;
  display: grid;
  grid-template-rows: auto minmax(390px, var(--workbench-editor-stage-height));
  min-height: 0;
}

.segment-editor-side-tools {
  grid-column: 2;
  grid-row: 1;
  position: sticky;
  top: 24px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  width: 74px;
  max-height: calc(100vh - 140px);
  overflow-y: auto;
  padding: 8px 6px;
  border-left: 1px solid #d9e4e8;
  background: #f8fbfb;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.segment-editor-side-tools::-webkit-scrollbar {
  width: 0;
  height: 0;
}

.workbench-layout.has-active-tool .segment-editor-side-tools {
  grid-column: 4;
}

.segment-editor-side-tool {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  width: 62px;
  min-height: 70px;
  padding: 8px 6px;
  border: 1px solid #bfd5d8;
  border-radius: 7px;
  background: linear-gradient(180deg, #ffffff, #edf8f6);
  color: #21515b;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.28;
  letter-spacing: 0;
  text-align: center;
  box-shadow: 0 1px 2px rgba(22, 54, 64, 0.06);
}

.segment-editor-side-tool svg {
  flex: 0 0 auto;
  color: #2d6f7c;
  stroke-width: 1.9;
}

.segment-editor-side-tool span {
  display: block;
  width: 100%;
  max-width: 52px;
  overflow: visible;
  line-height: 1.28;
  white-space: normal;
  word-break: normal;
  overflow-wrap: anywhere;
}

.segment-editor-side-tool:hover,
.segment-editor-side-tool:focus-visible {
  border-color: #80b6bd;
  background: #fff;
  outline: none;
}

.segment-editor-side-tool.is-active {
  border-color: #40a391;
  background: linear-gradient(180deg, #e9f8f4, #d7eee8);
  color: #0b6658;
  box-shadow: inset 0 0 0 1px rgba(13, 122, 104, 0.12);
}

.segment-editor-side-tool.is-active svg {
  color: #0b6658;
}

.segment-editor-list-stage {
  height: var(--workbench-editor-stage-height);
  min-height: 390px;
  overflow: hidden;
}

.segment-editor-list-stage > .virtual-list {
  height: 100%;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: scroll;
  scrollbar-width: thin;
  scrollbar-color: #52717a #e7f0ef;
}

.segment-editor-list-stage > .virtual-list::-webkit-scrollbar {
  width: 12px;
}

.segment-editor-list-stage > .virtual-list::-webkit-scrollbar-track {
  border-left: 1px solid #cfdfdd;
  background: #e7f0ef;
  border-radius: 999px;
}

.segment-editor-list-stage > .virtual-list::-webkit-scrollbar-thumb {
  border: 2px solid #e7f0ef;
  border-radius: 999px;
  background: #52717a;
}

.segment-editor-list-stage > .virtual-list::-webkit-scrollbar-thumb:hover {
  background: #31525b;
}

.segment-editor-toolbar__search.is-active {
  border-color: #69ad9d;
  background: linear-gradient(180deg, #edf7f4, #d7ece6);
  color: #0b6658;
  box-shadow: 0 6px 14px rgba(13, 122, 104, 0.12);
}

.segment-editor-toolbar .workbench-action:not(:disabled):hover,
.segment-editor-toolbar .workbench-action:not(:disabled):active {
  transform: none;
}

.segment-editor-toolbar__title {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.segment-editor-toolbar__overview {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 520px;
  flex-wrap: wrap;
  min-width: 0;
}

.segment-editor-toolbar__overview .workbench-stat--compact {
  min-height: 30px;
  padding: 5px 9px;
  gap: 6px;
  border-radius: 999px;
  box-shadow: none;
}

.segment-editor-toolbar__overview .workbench-stat--compact span {
  font-size: 12px;
  line-height: 1;
}

.segment-editor-toolbar__overview .workbench-stat--compact strong {
  font-size: 14px;
  line-height: 1;
}

.segment-editor-toolbar__tip,
.segment-editor-toolbar__loaded {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.2;
  white-space: nowrap;
}

.segment-editor-toolbar__tip {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.segment-editor-toolbar__load {
  min-height: 30px;
  padding: 5px 9px;
  font-size: 12.5px;
}

.segment-editor-toolbar__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
  margin-left: auto;
}

.segment-editor-toolbar__filter {
  flex: 0 1 auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  min-width: 0;
  margin: 0;
}

.segment-editor-toolbar__filter-label {
  flex: 0 0 auto;
  margin: 0;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
}

.segment-editor-toolbar__filter-select {
  width: 148px;
  min-width: 148px;
  background-color: var(--surface-panel);
  box-shadow: none;
}

.workbench-search-empty {
  min-height: 0;
  height: 100%;
  border: 1px dashed var(--line-soft);
  border-radius: 0 0 6px 6px;
  background: rgba(248, 250, 252, 0.72);
}

.workbench-panel-pop-enter-active,
.workbench-panel-pop-leave-active {
  overflow: hidden;
  transition:
    opacity 180ms cubic-bezier(0.2, 0.8, 0.2, 1),
    transform 200ms cubic-bezier(0.2, 0.8, 0.2, 1),
    filter 180ms ease,
    max-height 220ms ease,
    margin 220ms ease,
    padding-top 220ms ease,
    padding-bottom 220ms ease;
}

.workbench-panel-pop-enter-from,
.workbench-panel-pop-leave-to {
  max-height: 0;
  margin-top: 0;
  margin-bottom: 0;
  padding-top: 0;
  padding-bottom: 0;
  opacity: 0;
  filter: blur(1px);
  transform: translateY(-8px) scale(0.985);
}

.workbench-panel-pop-enter-to,
.workbench-panel-pop-leave-from {
  max-height: 520px;
  opacity: 1;
  filter: blur(0);
  transform: translateY(0) scale(1);
}

.workbench-resource-panel__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-left: auto;
}

.workbench-revision-menu {
  position: relative;
}

.workbench-revision-menu__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  min-height: 22px;
  padding: 0 6px;
  border-radius: 999px;
  background: rgba(13, 122, 104, 0.14);
  color: #0b6b5b;
  font-size: 12px;
}

.workbench-revision-menu__dropdown {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 20;
  display: grid;
  min-width: 160px;
  padding: 8px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.98);
  box-shadow: var(--shadow-medium);
}

.workbench-revision-menu__dropdown button {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 36px;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  text-align: left;
}

.workbench-revision-menu__dropdown button:hover:not(:disabled) {
  background: rgba(13, 122, 104, 0.08);
}

.workbench-revision-menu__dropdown button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.workbench-revision-menu__dropdown button.is-danger {
  color: #a43a3d;
}

.workbench-revision-menu__dropdown button.is-danger:hover:not(:disabled) {
  background: rgba(194, 59, 63, 0.08);
}

.workbench-page .workbench-rail__button {
  --rail-bg: linear-gradient(180deg, #f7faf9, #eaf1ef);
  --rail-hover-bg: linear-gradient(180deg, #ffffff, #edf4f1);
  --rail-active-bg: linear-gradient(180deg, #d8eee8, #bdded5);
  --rail-border: #cedbd8;
  --rail-hover-border: #b8cbc5;
  --rail-active-border: #69ad9d;
  --rail-color: #39565f;
  --rail-active-text: #0b6658;
  --rail-shadow: rgba(42, 68, 65, 0.08);
  --rail-active-shadow: rgba(13, 122, 104, 0.16);

  border-color: var(--rail-border);
  background: var(--rail-bg);
  color: var(--rail-color);
  box-shadow: 0 8px 16px var(--rail-shadow);
}

.workbench-page .workbench-rail__button:hover {
  border-color: var(--rail-hover-border);
  background: var(--rail-hover-bg);
  color: var(--rail-active-text);
  box-shadow: 0 10px 18px var(--rail-shadow);
  transform: translateX(-1px);
}

.workbench-page .workbench-rail__button.is-active {
  border-color: var(--rail-active-border);
  background: var(--rail-active-bg);
  color: var(--rail-active-text);
  box-shadow: 0 12px 22px var(--rail-active-shadow);
  transform: translateX(-2px);
}

.workbench-rail__button--paper {
  --rail-bg: linear-gradient(180deg, #f7fbfb, #e8f0f1);
  --rail-hover-bg: linear-gradient(180deg, #ffffff, #eef5f5);
  --rail-active-bg: linear-gradient(180deg, #d9ecef, #c2dde2);
  --rail-active-border: #79aebb;
  --rail-active-text: #285866;
  --rail-active-shadow: rgba(40, 88, 102, 0.15);
}

.workbench-rail__button--success {
  --rail-bg: linear-gradient(180deg, #f2faf6, #dcefe8);
  --rail-hover-bg: linear-gradient(180deg, #ffffff, #e6f5ef);
  --rail-active-bg: linear-gradient(180deg, #cdebe1, #a9d9c9);
  --rail-active-border: #5ea891;
  --rail-active-text: #116554;
  --rail-active-shadow: rgba(17, 101, 84, 0.16);
}

.workbench-rail__button--layout {
  --rail-bg: linear-gradient(180deg, #f2f6fb, #dde9f4);
  --rail-hover-bg: linear-gradient(180deg, #ffffff, #e8f1f8);
  --rail-active-bg: linear-gradient(180deg, #cfe2f5, #accce9);
  --rail-active-border: #6fa1cc;
  --rail-active-text: #2d5e87;
  --rail-active-shadow: rgba(45, 94, 135, 0.16);
}

.workbench-rail__button--info {
  --rail-bg: linear-gradient(180deg, #f1f5fb, #e1eafa);
  --rail-hover-bg: linear-gradient(180deg, #ffffff, #eaf0fb);
  --rail-active-bg: linear-gradient(180deg, #d4e1f6, #bacdf0);
  --rail-active-border: #839ed5;
  --rail-active-text: #3d558e;
  --rail-active-shadow: rgba(61, 85, 142, 0.16);
}

.workbench-rail__button--language {
  --rail-bg: linear-gradient(180deg, #f3faf7, #dfeee9);
  --rail-hover-bg: linear-gradient(180deg, #ffffff, #eaf6f1);
  --rail-active-bg: linear-gradient(180deg, #d1eadf, #b5dccc);
  --rail-active-border: #76ab95;
  --rail-active-text: #2b6754;
  --rail-active-shadow: rgba(43, 103, 84, 0.16);
}

.workbench-rail__button--note {
  --rail-bg: linear-gradient(180deg, #fbf6ed, #f2e4c8);
  --rail-hover-bg: linear-gradient(180deg, #fffdf8, #f7ead2);
  --rail-active-bg: linear-gradient(180deg, #f4dca8, #e4c27d);
  --rail-active-border: #c89c4f;
  --rail-active-text: #704d15;
  --rail-active-shadow: rgba(112, 77, 21, 0.16);
}

.workbench-rail__button--history {
  --rail-bg: linear-gradient(180deg, #f7f4fb, #e8e1f2);
  --rail-hover-bg: linear-gradient(180deg, #ffffff, #f0eaf8);
  --rail-active-bg: linear-gradient(180deg, #ddd1ee, #c9b7e1);
  --rail-active-border: #9a82bf;
  --rail-active-text: #5b4380;
  --rail-active-shadow: rgba(91, 67, 128, 0.16);
}

.shortcut-list {
  display: grid;
  gap: 10px;
}

.shortcut-item {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--surface-panel);
}

.shortcut-item strong {
  color: var(--text-primary);
}

.shortcut-item span {
  color: var(--text-secondary);
  text-align: right;
}

@media (max-width: 1180px) {
  .segment-editor-side-tools {
    grid-column: 1;
    order: -1;
    position: static;
    flex-direction: row;
    align-items: stretch;
    width: 100%;
    max-height: none;
    overflow-x: auto;
    overflow-y: hidden;
    padding: 6px 0;
    border-left: 0;
    border-bottom: 1px solid #d9e4e8;
  }

  .segment-editor-side-tool {
    flex: 0 0 64px;
  }

  .segment-editor-results {
    grid-template-rows: auto 420px;
  }

  .segment-editor-list-stage {
    height: 420px;
    min-height: 0;
  }
}

@media (max-width: 720px) {
  .segment-editor-toolbar__overview {
    flex: 1 1 100%;
  }

  .segment-editor-toolbar__tip {
    max-width: 100%;
  }

  .segment-editor-toolbar__actions {
    width: 100%;
    justify-content: stretch;
    margin-left: 0;
  }

  .segment-editor-toolbar__filter,
  .segment-editor-toolbar__search {
    flex: 1 1 100%;
  }

  .segment-editor-toolbar__filter {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .segment-editor-toolbar__filter-label {
    width: 100%;
  }

  .segment-editor-toolbar__filter-select {
    width: 100%;
    min-width: 0;
  }

  .workbench-search-panel__actions {
    width: auto;
    margin-left: 0;
  }

  .workbench-search-panel__actions .button {
    flex: 0 0 auto;
  }

  .workbench-search-panel__line--replace {
    padding-left: 0;
  }

  .workbench-search-panel__field,
  .workbench-search-panel__field .field__control,
  .workbench-search-panel__field--replace .field__control {
    width: 100%;
  }

  .workbench-search-panel__field {
    flex: 1 1 100%;
  }

  .segment-editor-shell {
    grid-template-columns: minmax(0, 1fr);
  }

  .segment-editor-results {
    grid-template-rows: auto 520px;
  }

  .segment-editor-list-stage {
    height: 520px;
    min-height: 0;
  }

  .workbench-guidelines-panel__head,
  .workbench-guidelines-panel__actions {
    align-items: stretch;
  }

  .workbench-guidelines-panel__head {
    flex-direction: column;
  }

  .shortcut-item {
    flex-direction: column;
  }
}

@media (prefers-reduced-motion: reduce) {
  .workbench-action,
  .workbench-panel-pop-enter-active,
  .workbench-panel-pop-leave-active {
    transition: none;
  }

  .workbench-action:not(:disabled):hover,
  .workbench-panel-pop-enter-from,
  .workbench-panel-pop-leave-to {
    transform: none;
  }
}
</style>

