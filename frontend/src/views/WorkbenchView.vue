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
  Pilcrow,
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
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, type ComponentPublicInstance } from 'vue'
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
import ReferencePanel from '../components/ReferencePanel.vue'
import { http } from '../api/http'
import { refreshGlobalNotifications } from '../utils/notifications'
import { useConfirm } from '../composables/useConfirm'
import { usePageHeader } from '../composables/usePageHeader'
import { useRichTextEditor, type TextFormat, type CaseType } from '../composables/useRichTextEditor'
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
  SegmentPositionResponse,
  TMCollection,
  TermBase,
  TermEntryRecord,
  TermQAReport,
  TermQAReportItem,
  TermQAReportListResponse,
} from '../types/api'
import { buildDocumentPreviewHtml } from '../utils/documentPreview'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'

const props = defineProps<{
  id: string
  standalone?: boolean
}>()

type BottomToolKey = 'qa-result' | 'history' | 'source-preview' | 'target-preview' | 'split-preview'
type BottomDrawerToolKey = Exclude<BottomToolKey, 'qa-result'>
type SideToolKey = 'match-info' | 'terms' | 'resource-search' | 'notes' | 'reference'
type ResourceImportTab = 'tm' | 'term'
type SaveToTMScope = 'translated' | 'confirmed'
type SaveToTMTargetMode = 'new' | 'existing'
type SegmentDisplayScope = 'all' | 'exact_only' | 'fuzzy_only' | 'none_only' | 'confirmed_only' | 'empty_target'
type RevisionMenuKind = 'track' | 'accept' | 'reject'
type ResourceSearchMode = 'exact' | 'fuzzy'
type FileExportStatus = 'queued' | 'running' | 'completed' | 'failed'
type WorkflowTransitionDirection = 'forward' | 'back'
type WorkflowSourceStatus = 'none' | 'exact' | 'fuzzy' | 'confirmed'
interface FileExportTask {
  task_id: string
  status: FileExportStatus
  progress: number
  message?: string
  error?: string | null
}
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
type ResourceSearchItem = {
  id: string
  type: 'tm' | 'term'
  library_id: string | null
  library_name: string | null
  source_text: string
  target_text: string
  source_language: string | null
  target_language: string | null
  updated_at: string | null
}
type ResourceSearchResponse = {
  items: ResourceSearchItem[]
  total: number
  tm_total: number
  term_total: number
  collection_ids: string[]
  term_base_ids: string[]
  query: string
  mode: ResourceSearchMode
}

const REVISION_TRACE_VISIBLE_STORAGE_KEY = 'workbench.revisionTraceEnabled'
const BOTTOM_DRAWER_MIN_HEIGHT = 260
const BOTTOM_DRAWER_TOP_GUTTER = 70
const BOTTOM_DRAWER_KEYBOARD_STEP = 32
const BOTTOM_DRAWER_KEYBOARD_LARGE_STEP = 80

function getInitialRevisionTraceVisible() {
  if (typeof window === 'undefined') {
    return false
  }
  return window.localStorage.getItem(REVISION_TRACE_VISIBLE_STORAGE_KEY) === '1'
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
const segmentEditorResultsRef = ref<HTMLElement | null>(null)
const segmentEditorRowRefs = new Map<string, SegmentEditorRowPublic>()

const bottomPanelRef = ref<HTMLElement | null>(null)
const bottomDrawerRef = ref<HTMLElement | null>(null)
const sidecarRef = ref<HTMLElement | null>(null)
const sidecarWidth = ref<number | null>(null)
const bottomDrawerHeight = ref<number | null>(null)
const isResizing = ref(false)
const isBottomDrawerResizing = ref(false)
const viewportHeight = ref(window.innerHeight)
let stopBottomDrawerResize: (() => void) | null = null
let segmentEditorResizeObserver: ResizeObserver | null = null
let segmentEditorScrollbarFrame: number | null = null

const sidecarWidthStyle = computed(() => {
  if (sidecarWidth.value === null) return {}
  return { width: `${sidecarWidth.value}px` }
})

const bottomDrawerMaxHeight = computed(() =>
  Math.max(BOTTOM_DRAWER_MIN_HEIGHT, viewportHeight.value - BOTTOM_DRAWER_TOP_GUTTER),
)

const bottomDrawerHeightStyle = computed(() => {
  if (bottomDrawerHeight.value === null) return {}
  const height = `${bottomDrawerHeight.value}px`
  return {
    '--workbench-bottom-panel-height': height,
    '--workbench-visible-bottom-panel-height': height,
  }
})

const bottomDrawerResizeValue = computed(() =>
  bottomDrawerHeight.value ?? resolveDefaultBottomDrawerHeight(),
)

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

function resolveDefaultBottomDrawerHeight() {
  const minHeight = props.standalone ? 380 : 360
  const maxHeight = props.standalone ? 560 : 520
  const preferredHeight = Math.round(viewportHeight.value * 0.5)
  return clampBottomDrawerHeight(Math.min(Math.max(preferredHeight, minHeight), maxHeight))
}

function clampBottomDrawerHeight(height: number) {
  return Math.round(Math.min(Math.max(height, BOTTOM_DRAWER_MIN_HEIGHT), bottomDrawerMaxHeight.value))
}

function setBottomDrawerHeight(height: number) {
  bottomDrawerHeight.value = clampBottomDrawerHeight(height)
}

function startBottomDrawerResize(event: PointerEvent) {
  if (!isPreviewDrawerResizable.value) {
    return
  }

  event.preventDefault()
  stopBottomDrawerResize?.()
  isBottomDrawerResizing.value = true

  const startY = event.clientY
  const startHeight = bottomDrawerRef.value?.offsetHeight ?? bottomDrawerResizeValue.value
  const previousCursor = document.body.style.cursor
  const previousUserSelect = document.body.style.userSelect

  function onPointerMove(pointerEvent: PointerEvent) {
    const deltaY = startY - pointerEvent.clientY
    setBottomDrawerHeight(startHeight + deltaY)
  }

  function stopResize() {
    isBottomDrawerResizing.value = false
    document.removeEventListener('pointermove', onPointerMove)
    document.removeEventListener('pointerup', stopResize)
    document.removeEventListener('pointercancel', stopResize)
    document.body.style.cursor = previousCursor
    document.body.style.userSelect = previousUserSelect
    stopBottomDrawerResize = null
  }

  document.body.style.cursor = 'ns-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('pointermove', onPointerMove)
  document.addEventListener('pointerup', stopResize)
  document.addEventListener('pointercancel', stopResize)
  stopBottomDrawerResize = stopResize
}

function handleBottomDrawerResizeKeydown(event: KeyboardEvent) {
  const currentHeight = bottomDrawerRef.value?.offsetHeight ?? bottomDrawerResizeValue.value
  const step = event.shiftKey ? BOTTOM_DRAWER_KEYBOARD_LARGE_STEP : BOTTOM_DRAWER_KEYBOARD_STEP
  let nextHeight: number | null = null

  if (event.key === 'ArrowUp') {
    nextHeight = currentHeight + step
  } else if (event.key === 'ArrowDown') {
    nextHeight = currentHeight - step
  } else if (event.key === 'PageUp') {
    nextHeight = currentHeight + BOTTOM_DRAWER_KEYBOARD_LARGE_STEP
  } else if (event.key === 'PageDown') {
    nextHeight = currentHeight - BOTTOM_DRAWER_KEYBOARD_LARGE_STEP
  } else if (event.key === 'Home') {
    nextHeight = BOTTOM_DRAWER_MIN_HEIGHT
  } else if (event.key === 'End') {
    nextHeight = bottomDrawerMaxHeight.value
  }

  if (nextHeight === null) {
    return
  }

  event.preventDefault()
  setBottomDrawerHeight(nextHeight)
}

const pageError = ref('')
const llmScope = ref<LLMTranslateScope>('current_segment')
const llmProvider = ref<LLMProvider>('deepseek')
const llmModel = ref('')
const itemHeight = ref(resolveItemHeight())
const activeSideTool = ref<SideToolKey | null>(null)
const activeBottomTool = ref<BottomToolKey | null>(null)
const openingBottomTool = ref<BottomDrawerToolKey | null>(null)
const previewPanelRendering = ref(false)
const showImportDialog = ref(false)
const showIssueDialog = ref(false)
const importDialogInitialTab = ref<ResourceImportTab>('tm')
const showShortcutHelp = ref(false)
const showSaveToTMDialog = ref(false)
const openConfirmMenu = ref(false)
const confirmationActionLoading = ref(false)
const openRevisionMenu = ref<RevisionMenuKind | null>(null)
const revisionTraceVisible = ref(getInitialRevisionTraceVisible())
const revisionActionLoading = ref(false)
const segmentSearchOpen = ref(false)
const sourceEditing = ref(false)
const sourceSearchInputRef = ref<HTMLInputElement | null>(null)
const guidelinesEditorRef = ref<HTMLTextAreaElement | null>(null)
const segmentDisplayScope = ref<SegmentDisplayScope>('all')
const sourceSearchQuery = ref('')
const targetSearchQuery = ref('')
const replaceSearchText = ref('')
const searchFuzzyEnabled = ref(false)
const searchLoadingAllSegments = ref(false)
const segmentSearchReturnTarget = ref<{ sentenceId: string; displayIndex: number | null; page: number } | null>(null)
const retainedEmptyTargetSentenceIds = ref<Set<string>>(new Set())
const tmCollections = ref<TMCollection[]>([])
const loadingTMCollections = ref(false)
const savingToTM = ref(false)
const saveToTMScope = ref<SaveToTMScope>('translated')
const saveToTMTargetMode = ref<SaveToTMTargetMode>('new')
const saveToTMCollectionId = ref('')
const saveToTMNewCollectionName = ref('')
const termQAReport = ref<TermQAReport | null>(null)
const loadingTermQAReport = ref(false)
const generatingTermQAReport = ref(false)
const downloadingTermQAReport = ref(false)
const locatingTermQAReportItemId = ref<string | null>(null)
const updatingTermQAIgnore = ref(false)
const selectedTermQAItemIds = ref<Set<string>>(new Set())
const showWorkflowTransitionDialog = ref(false)
const workflowTransitionDirection = ref<WorkflowTransitionDirection>('forward')
const workflowTransitionLoading = ref(false)
const workflowTransitionPreviewLoading = ref(false)
const workflowTransitionMatchedCount = ref<number | null>(null)
const workflowTransitionForm = reactive({
  all_segments: true,
  range_start: 1,
  range_end: 1,
  from_step_id: '',
  source_status: 'all' as 'all' | 'confirmed' | 'unconfirmed',
  source_statuses: ['none', 'exact', 'fuzzy', 'confirmed'] as WorkflowSourceStatus[],
  target_step_id: '',
  target_status: 'unconfirmed' as 'confirmed' | 'unconfirmed',
})

// 富文本编辑相关
const richTextEditor = useRichTextEditor()
const showCaseMenu = ref(false)
const showClearFormatMenu = ref(false)
const showSpecialCharMenu = ref(false)
const recentSpecialChars = ref<string[]>([])
const RECENT_CHARS_STORAGE_KEY = 'workbench.recentSpecialChars'

// 初始化最近使用的特殊字符
function loadRecentSpecialChars() {
  try {
    const saved = window.localStorage.getItem(RECENT_CHARS_STORAGE_KEY)
    if (saved) {
      recentSpecialChars.value = JSON.parse(saved)
    }
  } catch {}
}
loadRecentSpecialChars()

// 特殊字符列表（按行排列）
const specialCharacters = [
  ['&', '"', '@', '\\', '·', '^', '†', '‡', '°', '※'],
  ['#', '№', '°', 'ª', '%', '‰', '‱', '+', '–', '×'],
  ['÷', '=', '±', '≤', '≥', '≠', '~', '¶', '\'', '"'],
  ['"', '§', '~', '_', '|', '¡', '©', '®', '℠', '™'],
  ['™', '¤', '£', '$', '¥', '₩', '₫', 'ı', '€', '₭'],
  ['₱', '₫', '₹', '₺', '≠', '♪', '₽', '₿', 'ß'],
]

// 创建一个响应式的格式状态副本，确保传递给子组件时保持响应性
const pendingFormatsForEditor = computed(() => ({
  bold: richTextEditor.activeFormats.bold,
  italic: richTextEditor.activeFormats.italic,
  underline: richTextEditor.activeFormats.underline,
  strikethrough: richTextEditor.activeFormats.strikethrough,
  subscript: richTextEditor.activeFormats.subscript,
  superscript: richTextEditor.activeFormats.superscript,
  _overrideActive: richTextEditor.formatOverrideActive.value,
}))

const termBases = ref<TermBase[]>([])
const termEntries = ref<TermEntryRecord[]>([])
const selectedTermBaseId = ref('')
const loadingTermBases = ref(false)
const loadingTermEntries = ref(false)
const resourceSearchQuery = ref('')
const resourceSearchMode = ref<ResourceSearchMode>('exact')
const resourceSearchLoading = ref(false)
const resourceSearchMessage = ref('请输入关键词，搜索当前文件绑定的记忆库和术语库。')
const resourceSearchItems = ref<ResourceSearchItem[]>([])
const resourceSearchTotal = ref(0)
const resourceSearchTMTotal = ref(0)
const resourceSearchTermTotal = ref(0)
let resourceSearchRequestId = 0
const addingTerm = ref(false)
const showAddTermDialog = ref(false)
const addTermSourceText = ref('')
const addTermTargetText = ref('')
const addTermTargetBaseId = ref('')
const addTermFormError = ref('')
const termsMessage = ref(t('workbench.terms.defaultMessage'))

// 参考文件匹配结果

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
  const selectedModel = llmModelOptions.find((option) => option.id === modelId)
  if (selectedModel) {
    llmProvider.value = selectedModel.provider
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
const exportProgress = ref(0)
const exportMessage = ref('')
let exportPollTimer: number | null = null

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
  return ['.doc', '.docx', '.xlsx', '.pptx', '.pdf'].includes(ext)
})

function resolveItemHeight() {
  const stableGrid = Boolean(props.standalone)
  if (stableGrid && window.innerWidth > 1180) {
    return 52
  }
  if (window.innerWidth <= 720) {
    return 276
  }
  if (window.innerWidth <= 1180) {
    return 168
  }
  return 124
}

function handleResize() {
  viewportHeight.value = window.innerHeight
  itemHeight.value = resolveItemHeight()
  scheduleSegmentEditorScrollbarGutterUpdate()
  if (bottomDrawerHeight.value !== null) {
    setBottomDrawerHeight(bottomDrawerHeight.value)
  }
}

function updateSegmentEditorScrollbarGutter() {
  const results = segmentEditorResultsRef.value
    ?? document.querySelector<HTMLElement>('.workbench-page .segment-editor-results')
  if (!results) return

  const scrollContainer = results.querySelector<HTMLElement>('.segment-editor-list-stage > .virtual-list')
  let gutter = 0
  if (scrollContainer) {
    const style = window.getComputedStyle(scrollContainer)
    const paddingInlineStart = Number.parseFloat(style.paddingInlineStart) || 0
    const paddingInlineEnd = Number.parseFloat(style.paddingInlineEnd) || 0
    gutter = Math.max(
      0,
      scrollContainer.offsetWidth - scrollContainer.clientWidth + paddingInlineStart + paddingInlineEnd,
    )
  }
  results.style.setProperty('--segment-editor-scrollbar-gutter', `${gutter}px`)
}

function scheduleSegmentEditorScrollbarGutterUpdate() {
  if (segmentEditorScrollbarFrame !== null) {
    window.cancelAnimationFrame(segmentEditorScrollbarFrame)
  }
  segmentEditorScrollbarFrame = window.requestAnimationFrame(() => {
    segmentEditorScrollbarFrame = null
    updateSegmentEditorScrollbarGutter()
  })
}

function observeSegmentEditorResults() {
  segmentEditorResizeObserver?.disconnect()
  const results = segmentEditorResultsRef.value
    ?? document.querySelector<HTMLElement>('.workbench-page .segment-editor-results')
  if (!results || typeof ResizeObserver === 'undefined') return

  segmentEditorResizeObserver = new ResizeObserver(scheduleSegmentEditorScrollbarGutterUpdate)
  segmentEditorResizeObserver.observe(results)

  const scrollContainer = results.querySelector<HTMLElement>('.segment-editor-list-stage > .virtual-list')
  if (scrollContainer) {
    segmentEditorResizeObserver.observe(scrollContainer)
  }
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

const activeTermQAReportItems = computed(() => termQAReport.value?.items.filter((item) => !item.ignored) ?? [])

const ignoredTermQAReportItems = computed(() => termQAReport.value?.items.filter((item) => item.ignored) ?? [])

const selectedTermQAReportItems = computed(() => (
  termQAReport.value?.items.filter((item) => selectedTermQAItemIds.value.has(item.id)) ?? []
))

const selectedActiveTermQAReportItems = computed(() => (
  selectedTermQAReportItems.value.filter((item) => !item.ignored)
))

const allActiveTermQAItemsSelected = computed(() => (
  activeTermQAReportItems.value.length > 0
  && activeTermQAReportItems.value.every((item) => selectedTermQAItemIds.value.has(item.id))
))

const termQAReportCreatedAtText = computed(() => (
  formatWorkbenchStatusTime(termQAReport.value?.created_at)
))

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
const activeSegmentCanWrite = computed(() => Boolean(activeSegment.value?.can_write))
const workflowSteps = computed(() => segmentStore.fileRecord?.workflow_steps || [])
const workflowStepById = computed(() => new Map(workflowSteps.value.map((step) => [step.id, step])))
const workflowTargetSteps = computed(() => {
  const sourceStep = workflowStepById.value.get(workflowTransitionForm.from_step_id)
  if (!sourceStep) return []
  return workflowSteps.value.filter((step) => (
    workflowTransitionDirection.value === 'forward'
      ? step.sort_order > sourceStep.sort_order
      : step.sort_order < sourceStep.sort_order
  ))
})
const canOpenWorkflowTransition = computed(() => (
  Boolean(activeSegment.value?.workflow_step_id)
  && activeSegmentCanWrite.value
  && workflowSteps.value.length > 1
))
const workflowSourceStatusOptions: Array<{ value: WorkflowSourceStatus; label: string }> = [
  { value: 'none', label: '未翻译' },
  { value: 'exact', label: '完全匹配' },
  { value: 'fuzzy', label: '模糊匹配' },
  { value: 'confirmed', label: '已确认' },
]
const workflowTransitionHasSourceStatus = computed(() => workflowTransitionForm.source_statuses.length > 0)

function hasWorkflowTransitionTarget(direction: WorkflowTransitionDirection) {
  const stepId = activeSegment.value?.workflow_step_id
  const sourceStep = stepId ? workflowStepById.value.get(stepId) : null
  if (!sourceStep || !activeSegmentCanWrite.value) return false
  return workflowSteps.value.some((step) => (
    direction === 'forward'
      ? step.sort_order > sourceStep.sort_order
      : step.sort_order < sourceStep.sort_order
  ))
}

function isWorkflowSourceStatusChecked(status: WorkflowSourceStatus) {
  return workflowTransitionForm.source_statuses.includes(status)
}

function toggleWorkflowSourceStatus(status: WorkflowSourceStatus, checked: boolean) {
  if (checked) {
    if (!workflowTransitionForm.source_statuses.includes(status)) {
      workflowTransitionForm.source_statuses = [...workflowTransitionForm.source_statuses, status]
    }
    return
  }
  workflowTransitionForm.source_statuses = workflowTransitionForm.source_statuses.filter((item) => item !== status)
}

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

function getSegmentCopyableSourceText(segment: Segment) {
  if (segment.automatic_numbering_text) {
    return segment.source_body_text || segment.source_text || ''
  }
  return segment.display_text || segment.source_text || ''
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
  activeBottomTool.value === 'split-preview'
    ? buildDocumentPreviewHtml(segmentStore.segments, 'source')
    : segmentStore.previewHtml
))

const sourcePreviewSupported = computed(() => (
  activeBottomTool.value === 'split-preview'
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

const exportButtonLabel = computed(() => (
  exporting.value ? `导出中 ${exportProgress.value}%` : t('common.actions.export')
))

const exportButtonTitle = computed(() => (
  exporting.value
    ? (exportMessage.value || `导出中 ${exportProgress.value}%`)
    : `${t('common.actions.export')} ${getTaskExportFormatLabel(segmentStore.fileRecord?.filename)}`
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

const boundTermBaseIds = computed(() => {
  const fileRecord = segmentStore.fileRecord
  if (!fileRecord) {
    return []
  }
  if (fileRecord.term_base_ids?.length) {
    return fileRecord.term_base_ids
  }
  return fileRecord.term_base_id ? [fileRecord.term_base_id] : []
})

const boundResourceSummary = computed(() => {
  const collectionCount = segmentStore.fileRecord?.collection_ids?.length || (segmentStore.fileRecord?.collection_id ? 1 : 0)
  const termBaseCount = boundTermBaseIds.value.length
  return `已绑定 ${collectionCount} 个记忆库、${termBaseCount} 个术语库`
})

const addTermTargetTermBases = computed(() => {
  const boundIds = new Set(boundTermBaseIds.value)
  const writableIds = segmentStore.fileRecord?.term_base_write_ids || []
  const targetIds = writableIds.length > 0
    ? writableIds.filter((id) => boundIds.has(id))
    : boundTermBaseIds.value
  const allowedIds = new Set(targetIds)
  return termBases.value.filter((termBase) => allowedIds.has(termBase.id))
})

const addTermCanSubmit = computed(() => Boolean(
  addTermSourceText.value.trim()
  && addTermTargetText.value.trim()
  && addTermTargetBaseId.value
  && addTermTargetTermBases.value.some((termBase) => termBase.id === addTermTargetBaseId.value)
  && !addingTerm.value,
))

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
  activeBottomTool.value === 'source-preview'
  && (segmentStore.previewLoading || openingBottomTool.value === 'source-preview'),
)

const targetPreviewLoading = computed(() => {
  if (activeBottomTool.value !== 'target-preview' && activeBottomTool.value !== 'split-preview') {
    return false
  }
  if (openingBottomTool.value === 'target-preview' || openingBottomTool.value === 'split-preview') {
    return true
  }
  if (targetPreviewRenderMode.value === 'target') {
    return segmentStore.previewLoading
  }
  return false
})

const bottomToolButtons = computed(() => ([
  { key: 'history' as const, label: '历史记录', icon: History, tone: 'history' },
  { key: 'source-preview' as const, label: t('workbench.tools.sourcePreview'), icon: FileText, tone: 'paper' },
  { key: 'target-preview' as const, label: t('workbench.tools.targetPreview'), icon: FileCheck, tone: 'success' },
  { key: 'split-preview' as const, label: '对照预览', icon: Columns, tone: 'layout' },
]))

function isPreviewBottomTool(tool: BottomDrawerToolKey) {
  return tool === 'source-preview' || tool === 'target-preview' || tool === 'split-preview'
}

const isPreviewDrawerResizable = computed(() =>
  activeBottomTool.value === 'source-preview'
  || activeBottomTool.value === 'target-preview'
  || activeBottomTool.value === 'split-preview',
)

function isBottomToolLoading(tool: BottomDrawerToolKey) {
  if (tool === 'source-preview') {
    return sourcePreviewLoading.value
  }
  if (tool === 'target-preview' || tool === 'split-preview') {
    return targetPreviewLoading.value
  }
  return false
}

function isBottomToolDisabled(tool: BottomDrawerToolKey) {
  if (!isPreviewBottomTool(tool)) {
    return false
  }
  return isBottomToolLoading(tool) || Boolean(openingBottomTool.value) || segmentStore.previewLoading
}

const bottomDrawerPreviewLoading = computed(() => {
  if (activeBottomTool.value === 'source-preview') {
    return sourcePreviewLoading.value
  }
  if (activeBottomTool.value === 'target-preview' || activeBottomTool.value === 'split-preview') {
    return targetPreviewLoading.value
  }
  return false
})

const bottomDrawerPreviewBusy = computed(() => bottomDrawerPreviewLoading.value || previewPanelRendering.value)

const bottomDrawerPreviewLoadingTitle = computed(() => {
  const action = previewPanelRendering.value && !bottomDrawerPreviewLoading.value ? '渲染中...' : '加载中...'
  if (activeBottomTool.value === 'source-preview') {
    return `原文预览${action}`
  }
  if (activeBottomTool.value === 'target-preview') {
    return `译文预览${action}`
  }
  if (activeBottomTool.value === 'split-preview') {
    return `对照预览${action}`
  }
  return `预览${action}`
})

const bottomDrawerPreviewLoadingMessage = computed(() => (
  previewPanelRendering.value && !bottomDrawerPreviewLoading.value
    ? '正在排版预览内容，请稍候'
    : '正在准备预览内容，请稍候'
))

function handleBottomPreviewRenderingChange(rendering: boolean) {
  previewPanelRendering.value = rendering
}

const sideToolButtons = computed(() => ([
  { key: 'match-info' as const, label: t('workbench.tools.matchInfo'), icon: Info, tone: 'info' },
  { key: 'terms' as const, label: t('workbench.tools.terms'), icon: Languages, tone: 'language' },
  { key: 'resource-search' as const, label: '搜索', icon: Search, tone: 'search' },
  { key: 'notes' as const, label: t('workbench.tools.notes'), icon: MessageSquare, tone: 'note' },
  { key: 'reference' as const, label: '参考文件', icon: FileText, tone: 'reference' },
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
  closePanel: () => { void closeActiveWorkbenchPanel() },
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
      && boundTermBaseIds.value.includes(termBase.id)
    ))

    termBases.value = filtered

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

function resetResourceSearchResults(message = '请输入关键词，搜索当前文件绑定的记忆库和术语库。') {
  resourceSearchItems.value = []
  resourceSearchTotal.value = 0
  resourceSearchTMTotal.value = 0
  resourceSearchTermTotal.value = 0
  resourceSearchMessage.value = message
}

async function runResourceSearch() {
  const keyword = resourceSearchQuery.value.trim()
  if (!keyword) {
    resetResourceSearchResults()
    return
  }

  const fileRecordId = segmentStore.fileRecord?.id || props.id
  const requestId = ++resourceSearchRequestId
  resourceSearchLoading.value = true
  resourceSearchMessage.value = '正在搜索绑定的资源库...'
  try {
    const { data } = await http.get<ResourceSearchResponse>(`/file-records/${fileRecordId}/resource-search`, {
      params: {
        q: keyword,
        mode: resourceSearchMode.value,
        limit: 80,
      },
    })
    if (requestId !== resourceSearchRequestId) {
      return
    }
    resourceSearchItems.value = data.items
    resourceSearchTotal.value = data.total
    resourceSearchTMTotal.value = data.tm_total
    resourceSearchTermTotal.value = data.term_total
    resourceSearchMessage.value = data.total
      ? `找到 ${data.total} 条结果：记忆库 ${data.tm_total} 条，术语库 ${data.term_total} 条。`
      : '未找到匹配的记忆库或术语库句段。'
  } catch (error) {
    if (requestId === resourceSearchRequestId) {
      resetResourceSearchResults(getErrorMessage(error, '资源库搜索失败。'))
    }
  } finally {
    if (requestId === resourceSearchRequestId) {
      resourceSearchLoading.value = false
    }
  }
}

function prepareResourceSearchQuery() {
  if (!resourceSearchQuery.value.trim() && activeSegmentSourceText.value.trim()) {
    resourceSearchQuery.value = activeSegmentSourceText.value.trim()
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

function closeBottomDrawer() {
  openingBottomTool.value = null
  previewPanelRendering.value = false
  activeBottomTool.value = null
}

async function scrollBottomPanelIntoView() {
  await nextTick()
  await new Promise((resolve) => window.requestAnimationFrame(resolve))
  const target = bottomPanelRef.value
  if (!target) {
    return
  }

  const stickyOffset = isStandaloneWorkbench.value ? 12 : 68
  const targetRect = target.getBoundingClientRect()

  let scrollParent: HTMLElement | null = target.parentElement
  while (scrollParent && scrollParent !== document.body) {
    const style = window.getComputedStyle(scrollParent)
    if (
      /(auto|scroll)/.test(style.overflowY)
      && scrollParent.scrollHeight > scrollParent.clientHeight
    ) {
      const parentRect = scrollParent.getBoundingClientRect()
      scrollParent.scrollTo({
        top: scrollParent.scrollTop + targetRect.top - parentRect.top - stickyOffset,
        behavior: 'smooth',
      })
      return
    }
    scrollParent = scrollParent.parentElement
  }

  const scrollingElement = document.scrollingElement || document.documentElement
  scrollingElement.scrollTo({
    top: scrollingElement.scrollTop + targetRect.top - stickyOffset,
    behavior: 'smooth',
  })
}

async function openBottomTool(tool: BottomDrawerToolKey) {
  if (activeBottomTool.value === tool) {
    closeBottomDrawer()
    return
  }

  pageError.value = ''
  previewPanelRendering.value = false
  openingBottomTool.value = tool
  activeBottomTool.value = tool
  await scrollBottomPanelIntoView()

  try {
    if (tool === 'source-preview') {
      await segmentStore.ensurePreviewLoaded('source')
      return
    }

    if (tool === 'target-preview' || tool === 'split-preview') {
      await segmentStore.ensurePreviewLoaded('target')
    }
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.sidePanel'))
  } finally {
    if (openingBottomTool.value === tool) {
      openingBottomTool.value = null
    }
  }
}

async function openSideTool(tool: SideToolKey) {
  if (activeSideTool.value === tool) {
    activeSideTool.value = null
    sidecarWidth.value = null
    return
  }

  pageError.value = ''
  activeSideTool.value = tool
  sidecarWidth.value = null

  try {
    if (tool === 'terms' && termBases.value.length === 0 && !loadingTermBases.value) {
      await loadTermBases()
    }

    if (tool === 'resource-search') {
      prepareResourceSearchQuery()
      if (resourceSearchQuery.value.trim() && resourceSearchItems.value.length === 0) {
        await runResourceSearch()
      }
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

async function closeActiveWorkbenchPanel() {
  if (segmentSearchOpen.value) {
    await closeSegmentSearchPanel()
    return
  }
  if (showGuidelinesPanel.value) {
    closeGuidelinesPanel()
    return
  }
  if (activeBottomTool.value) {
    closeBottomDrawer()
    return
  }
  activeSideTool.value = null
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
  segmentSearchReturnTarget.value = null
  retainedEmptyTargetSentenceIds.value = new Set()
}

function captureSegmentSearchReturnTarget() {
  const sentenceId = segmentStore.activeSentenceId
  if (!sentenceId) {
    segmentSearchReturnTarget.value = null
    return
  }

  const segment = editorSegments.value.find((item) => item.sentence_id === sentenceId)
  const displayIndex = typeof segment?.display_index === 'number' && Number.isFinite(segment.display_index)
    ? segment.display_index
    : (segmentOrdinalMap.value.get(sentenceId) ?? null)
  segmentSearchReturnTarget.value = {
    sentenceId,
    displayIndex,
    page: segmentStore.currentPage,
  }
}

function clearSegmentSearchFilters() {
  searchLoadRequestId += 1
  segmentDisplayScope.value = 'all'
  sourceSearchQuery.value = ''
  targetSearchQuery.value = ''
  searchFuzzyEnabled.value = false
  searchLoadingAllSegments.value = false
  retainedEmptyTargetSentenceIds.value = new Set()
}

async function focusEditorSegmentBySentenceId(sentenceId: string) {
  const index = editorSegments.value.findIndex((segment) => segment.sentence_id === sentenceId)
  if (index < 0) {
    return false
  }
  await focusEditorSegmentAtIndex(index)
  return true
}

function getReturnTargetPage(target: { displayIndex: number | null; page: number }) {
  if (typeof target.displayIndex === 'number' && Number.isFinite(target.displayIndex) && target.displayIndex >= 0) {
    return Math.floor(target.displayIndex / segmentStore.pageSize) + 1
  }
  return target.page
}

async function resolveSegmentReturnTargetPage(target: { sentenceId: string; displayIndex: number | null; page: number }) {
  const localPage = getReturnTargetPage(target)
  if (typeof target.displayIndex === 'number' && Number.isFinite(target.displayIndex) && target.displayIndex >= 0) {
    return localPage
  }

  const fileRecordId = segmentStore.fileRecord?.id || props.id
  try {
    const { data } = await http.get<SegmentPositionResponse>(
      `/file-records/${fileRecordId}/segments/${encodeURIComponent(target.sentenceId)}/position`,
      { params: { page_size: segmentStore.pageSize } },
    )
    return data.page || localPage
  } catch (error) {
    console.warn('Failed to resolve segment return page:', error)
    return localPage
  }
}

async function restoreSegmentSearchReturnTarget() {
  const target = segmentSearchReturnTarget.value
  segmentSearchReturnTarget.value = null
  if (!target) {
    return
  }

  if (await focusEditorSegmentBySentenceId(target.sentenceId)) {
    return
  }

  const targetPage = await resolveSegmentReturnTargetPage(target)
  suppressSegmentFilterWatch = true
  clearSegmentSearchFilters()
  suppressSegmentFilterWatch = false
  await refreshSegmentPage(targetPage, segmentStore.pageSize)
  await focusEditorSegmentBySentenceId(target.sentenceId)
}

async function clearSourceSegmentSearchQuery() {
  const target = segmentSearchReturnTarget.value
  const targetPage = target ? await resolveSegmentReturnTargetPage(target) : segmentStore.currentPage
  searchLoadRequestId += 1
  searchLoadingAllSegments.value = false
  suppressSegmentFilterWatch = true
  sourceSearchQuery.value = ''
  await nextTick()
  suppressSegmentFilterWatch = false

  if (segmentStore.fileRecord) {
    await refreshSegmentPage(targetPage, segmentStore.pageSize)
  }

  if (target) {
    await focusEditorSegmentBySentenceId(target.sentenceId)
    return
  }

  await nextTick()
  sourceSearchInputRef.value?.focus({ preventScroll: true })
}

async function closeSegmentSearchPanel() {
  segmentSearchOpen.value = false
  await nextTick()
  await restoreSegmentSearchReturnTarget()
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

function updateSegmentTarget(
  sentenceId: string,
  targetText: string,
  targetHtml?: string,
  options: { confirm?: boolean } = {},
) {
  const segment = segmentStore.segments.find((item) => item.sentence_id === sentenceId)
  if (segment && !segment.can_write) {
    toast.warn('当前流程阶段无编辑权限')
    return
  }
  retainEmptyTargetSegmentDuringEdit(sentenceId, segment?.target_text)
  segmentStore.updateTarget(sentenceId, targetText, targetHtml, options)
}

async function updateSegmentSource(sentenceId: string, sourceText: string) {
  const segment = segmentStore.segments.find((item) => item.sentence_id === sentenceId)
  if (segment && !segment.can_write) {
    toast.warn('当前流程阶段无编辑权限')
    return
  }
  try {
    await segmentStore.updateSource(sentenceId, sourceText)
  } catch (error) {
    console.error('Failed to update source text:', error)
  }
}

async function toggleProjectSegmentSync(sentenceId: string, disabled: boolean) {
  const segment = segmentStore.segments.find((item) => item.sentence_id === sentenceId)
  if (segment && !segment.can_write) {
    toast.warn('当前流程阶段无编辑权限')
    return
  }
  await segmentStore.setProjectSyncDisabled(sentenceId, disabled)
}

async function toggleSegmentSearchPanel() {
  if (segmentSearchOpen.value) {
    await closeSegmentSearchPanel()
    return
  }

  captureSegmentSearchReturnTarget()
  segmentSearchOpen.value = true
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
  if (!activeSegmentCanWrite.value) {
    toast.warn('当前流程阶段无编辑权限')
    return
  }
  updateSegmentTarget(
    activeSegment.value.sentence_id,
    activeSegment.value.target_text || '',
    activeSegment.value.target_html ?? undefined,
    { confirm: true },
  )
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

async function confirmAndMoveToNextUnconfirmed() {
  if (!activeSegment.value) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }

  confirmCurrentSentence()
  const currentIndex = activeEditorIndex.value
  const segments = editorSegments.value
  let nextUnconfirmedIndex = -1

  for (let i = currentIndex + 1; i < segments.length; i++) {
    if (segments[i].status !== 'confirmed') {
      nextUnconfirmedIndex = i
      break
    }
  }

  if (nextUnconfirmedIndex === -1) {
    for (let i = 0; i < currentIndex; i++) {
      if (segments[i].status !== 'confirmed') {
        nextUnconfirmedIndex = i
        break
      }
    }
  }

  if (nextUnconfirmedIndex !== -1) {
    await focusEditorSegmentAtIndex(nextUnconfirmedIndex)
  }
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

function buildWorkflowTransitionPayload() {
  const sourceStatuses = [...workflowTransitionForm.source_statuses]
  const hasConfirmed = sourceStatuses.includes('confirmed')
  const hasUnconfirmed = sourceStatuses.some((item) => item !== 'confirmed')
  return {
    from_step_id: workflowTransitionForm.from_step_id,
    target_step_id: workflowTransitionForm.target_step_id,
    all_segments: workflowTransitionForm.all_segments,
    range_start: Number(workflowTransitionForm.range_start || 1),
    range_end: Number(workflowTransitionForm.range_end || workflowTransitionForm.range_start || 1),
    source_status: hasConfirmed && hasUnconfirmed ? 'all' : hasConfirmed ? 'confirmed' : 'unconfirmed',
    source_statuses: sourceStatuses,
    target_status: workflowTransitionForm.target_status,
  }
}

async function refreshWorkflowTransitionPreview() {
  if (!segmentStore.fileRecord || !workflowTransitionForm.from_step_id || !workflowTransitionForm.target_step_id) {
    workflowTransitionMatchedCount.value = null
    return
  }
  workflowTransitionPreviewLoading.value = true
  try {
    const { data } = await http.post<{ matched_count: number }>(
      `/file-records/${segmentStore.fileRecord.id}/workflow/transition/preview`,
      buildWorkflowTransitionPayload(),
    )
    workflowTransitionMatchedCount.value = data.matched_count
  } catch (error) {
    workflowTransitionMatchedCount.value = null
    toast.error(getErrorMessage(error, '流程预览失败'))
  } finally {
    workflowTransitionPreviewLoading.value = false
  }
}

function openWorkflowTransitionDialog(direction: WorkflowTransitionDirection) {
  if (!canOpenWorkflowTransition.value || !activeSegment.value) {
    toast.warn('当前句段无可流转的编辑权限')
    return
  }
  workflowTransitionDirection.value = direction
  workflowTransitionForm.all_segments = true
  workflowTransitionForm.range_start = 1
  workflowTransitionForm.range_end = segmentStore.totalSegmentCount || 1
  workflowTransitionForm.from_step_id = activeSegment.value.workflow_step_id || workflowSteps.value[0]?.id || ''
  workflowTransitionForm.source_status = 'all'
  workflowTransitionForm.source_statuses = ['none', 'exact', 'fuzzy', 'confirmed']
  workflowTransitionForm.target_status = 'unconfirmed'
  const target = workflowTargetSteps.value[direction === 'forward' ? 0 : workflowTargetSteps.value.length - 1]
  if (!target) {
    toast.warn(direction === 'forward' ? '没有可前进的目标流程' : '没有可退回的目标流程')
    return
  }
  workflowTransitionForm.target_step_id = target?.id || ''
  workflowTransitionMatchedCount.value = null
  showWorkflowTransitionDialog.value = true
  void refreshWorkflowTransitionPreview()
}

async function submitWorkflowTransition() {
  if (!segmentStore.fileRecord || !workflowTransitionForm.target_step_id) {
    return
  }
  workflowTransitionLoading.value = true
  try {
    await segmentStore.syncToBackend()
    const { data } = await http.post<{ updated_count: number }>(
      `/file-records/${segmentStore.fileRecord.id}/workflow/transition`,
      buildWorkflowTransitionPayload(),
    )
    await segmentStore.refreshCurrentSegmentPage()
    showWorkflowTransitionDialog.value = false
    toast.success(`已流转 ${data.updated_count} 个句段`)
  } catch (error) {
    toast.error(getErrorMessage(error, '流程流转失败'))
  } finally {
    workflowTransitionLoading.value = false
  }
}

watch(
  () => [
    showWorkflowTransitionDialog.value,
    workflowTransitionForm.all_segments,
    workflowTransitionForm.range_start,
    workflowTransitionForm.range_end,
    workflowTransitionForm.from_step_id,
    workflowTransitionForm.source_statuses.join(','),
    workflowTransitionForm.target_step_id,
    workflowTransitionForm.target_status,
  ],
  () => {
    if (showWorkflowTransitionDialog.value) {
      void refreshWorkflowTransitionPreview()
    }
  },
)

watch(
  () => [workflowTransitionDirection.value, workflowTransitionForm.from_step_id],
  () => {
    if (!showWorkflowTransitionDialog.value) return
    if (!workflowTargetSteps.value.some((step) => step.id === workflowTransitionForm.target_step_id)) {
      workflowTransitionForm.target_step_id = workflowTargetSteps.value[0]?.id || ''
    }
  },
)

function setCurrentTermQAReport(report: TermQAReport | null) {
  termQAReport.value = report
  const validIds = new Set(report?.items.filter((item) => !item.ignored).map((item) => item.id) ?? [])
  selectedTermQAItemIds.value = new Set(
    [...selectedTermQAItemIds.value].filter((id) => validIds.has(id)),
  )
}

async function loadLatestTermQAReport() {
  if (!segmentStore.fileRecord || loadingTermQAReport.value) {
    return
  }
  loadingTermQAReport.value = true
  try {
    const { data } = await http.get<TermQAReportListResponse>(
      `/file-records/${segmentStore.fileRecord.id}/term-qa-reports`,
      {
        params: {
          limit: 1,
          include_items: true,
        },
      },
    )
    setCurrentTermQAReport(data.items[0] || null)
  } catch (error) {
    console.error('Failed to load latest term QA report:', error)
    setCurrentTermQAReport(null)
  } finally {
    loadingTermQAReport.value = false
  }
}

async function generateCurrentFileTermQAReport() {
  if (!segmentStore.fileRecord || generatingTermQAReport.value) {
    return
  }
  generatingTermQAReport.value = true
  try {
    const synced = await segmentStore.syncToBackend()
    if (!synced) {
      return
    }
    const { data } = await http.post<TermQAReport>(
      `/file-records/${segmentStore.fileRecord.id}/term-qa-reports`,
    )
    setCurrentTermQAReport(data)
    activeBottomTool.value = 'qa-result'
    await scrollBottomPanelIntoView()
    toast.show({
      tone: data.issue_count > 0 ? 'warn' : 'success',
      title: '术语QA报告已生成',
      message: `发现 ${data.issue_count} 条术语问题。`,
    })
  } catch (error) {
    toast.error({
      title: '术语QA报告生成失败',
      message: getErrorMessage(error, '术语QA报告生成失败。'),
    })
  } finally {
    generatingTermQAReport.value = false
  }
}

async function openTermQAResult() {
  if (loadingTermQAReport.value || generatingTermQAReport.value) {
    return
  }
  if (activeBottomTool.value === 'qa-result') {
    closeBottomDrawer()
    return
  }
  previewPanelRendering.value = false
  activeBottomTool.value = 'qa-result'
  await scrollBottomPanelIntoView()
  if (termQAReport.value) {
    return
  }
  await generateCurrentFileTermQAReport()
}

async function clearSegmentFiltersForTermQANavigation() {
  if (!hasEditorSegmentFilter.value) {
    return
  }
  suppressSegmentFilterWatch = true
  resetSegmentSearch()
  await nextTick()
  suppressSegmentFilterWatch = false
}

function toggleTermQAItemSelection(itemId: string, checked: boolean) {
  const next = new Set(selectedTermQAItemIds.value)
  if (checked) {
    next.add(itemId)
  } else {
    next.delete(itemId)
  }
  selectedTermQAItemIds.value = next
}

function handleTermQAItemSelectionChange(itemId: string, event: Event) {
  toggleTermQAItemSelection(itemId, (event.target as HTMLInputElement).checked)
}

function toggleAllActiveTermQAItems(checked: boolean) {
  if (!checked) {
    selectedTermQAItemIds.value = new Set(
      [...selectedTermQAItemIds.value].filter((id) => ignoredTermQAReportItems.value.some((item) => item.id === id)),
    )
    return
  }
  selectedTermQAItemIds.value = new Set([
    ...selectedTermQAItemIds.value,
    ...activeTermQAReportItems.value.map((item) => item.id),
  ])
}

async function setTermQAReportItemsIgnored(itemIds: string[], ignored: boolean) {
  if (!termQAReport.value || updatingTermQAIgnore.value || itemIds.length === 0) {
    return
  }
  updatingTermQAIgnore.value = true
  try {
    const { data } = await http.patch<TermQAReport>(
      `/term-qa-reports/${termQAReport.value.id}/items/ignore`,
      {
        item_ids: itemIds,
        ignored,
      },
    )
    setCurrentTermQAReport(data)
    toast.success(ignored ? '已忽略所选术语 QA 项。' : '已恢复所选术语 QA 项。')
  } catch (error) {
    toast.error({
      title: ignored ? '忽略失败' : '恢复失败',
      message: getErrorMessage(error, ignored ? '忽略术语 QA 项失败。' : '恢复术语 QA 项失败。'),
    })
  } finally {
    updatingTermQAIgnore.value = false
  }
}

async function setSingleTermQAReportItemIgnored(item: TermQAReportItem, ignored: boolean) {
  if (updatingTermQAIgnore.value) {
    return
  }
  updatingTermQAIgnore.value = true
  try {
    const { data } = await http.patch<TermQAReport>(
      `/term-qa-report-items/${item.id}/ignore`,
      { ignored },
    )
    setCurrentTermQAReport(data)
    toast.success(ignored ? '已忽略该术语 QA 项。' : '已恢复该术语 QA 项。')
  } catch (error) {
    toast.error({
      title: ignored ? '忽略失败' : '恢复失败',
      message: getErrorMessage(error, ignored ? '忽略术语 QA 项失败。' : '恢复术语 QA 项失败。'),
    })
  } finally {
    updatingTermQAIgnore.value = false
  }
}

async function ignoreSelectedTermQAReportItems() {
  if (selectedActiveTermQAReportItems.value.length === 0) {
    toast.warn('请先选择未忽略的术语 QA 项。')
    return
  }
  await setTermQAReportItemsIgnored(
    selectedActiveTermQAReportItems.value.map((item) => item.id),
    true,
  )
}

async function focusTermQAReportItem(item: TermQAReportItem) {
  const fileRecord = segmentStore.fileRecord
  if (!fileRecord || locatingTermQAReportItemId.value) {
    return
  }
  if (item.file_record_id !== fileRecord.id) {
    toast.warn('该报告项不属于当前文件。')
    return
  }

  locatingTermQAReportItemId.value = item.id
  try {
    const currentPageIndex = editorSegments.value.findIndex(
      (segment) => segment.sentence_id === item.sentence_id,
    )
    if (currentPageIndex >= 0) {
      await focusEditorSegmentAtIndex(currentPageIndex)
      closeBottomDrawer()
      return
    }

    const synced = await segmentStore.syncToBackend()
    if (!synced) {
      return
    }

    const { data } = await http.get<SegmentPositionResponse>(
      `/file-records/${fileRecord.id}/segments/${encodeURIComponent(item.sentence_id)}/position`,
      {
        params: {
          page_size: segmentStore.pageSize,
        },
      },
    )
    await clearSegmentFiltersForTermQANavigation()
    await refreshSegmentPage(data.page, data.page_size)
    const targetIndex = editorSegments.value.findIndex(
      (segment) => segment.sentence_id === item.sentence_id,
    )
    if (targetIndex === -1) {
      toast.warn('已切换到目标页，但未找到对应句段。')
      return
    }
    await focusEditorSegmentAtIndex(targetIndex)
    closeBottomDrawer()
  } catch (error) {
    toast.error({
      title: '跳转 QA 句段失败',
      message: getErrorMessage(error, '无法定位报告中的句段。'),
    })
  } finally {
    locatingTermQAReportItemId.value = null
  }
}

async function downloadCurrentTermQAReport() {
  if (!termQAReport.value || downloadingTermQAReport.value) {
    return
  }
  downloadingTermQAReport.value = true
  try {
    const response = await http.get(`/term-qa-reports/${termQAReport.value.id}/export-xlsx`, {
      responseType: 'blob',
    })
    downloadBlob(
      response.data,
      resolveDownloadFilename(
        response.headers['content-disposition'],
        `term-qa-report-${termQAReport.value.id}.xlsx`,
      ),
    )
  } catch (error) {
    toast.error({
      title: '术语QA报告导出失败',
      message: getErrorMessage(error, '术语QA报告导出失败。'),
    })
  } finally {
    downloadingTermQAReport.value = false
  }
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

// ========== 拆分/合并句段 ==========

const selectedSentenceIds = ref<Set<string>>(new Set())
const lastSourceCaretOffset = ref<number | null>(null)

// 判断当前文件是否为 CAD 文件（DWG/DXF）
const isCadFile = computed(() => {
  const filename = segmentStore.fileRecord?.filename || ''
  const ext = filename.substring(filename.lastIndexOf('.')).toLowerCase()
  return ext === '.dwg' || ext === '.dxf'
})

// 切换激活句段时清除光标缓存
watch(() => segmentStore.activeSentenceId, () => {
  lastSourceCaretOffset.value = null
})

function handleSegmentClick(sentenceId: string, event: MouseEvent) {
  if (event.ctrlKey || event.metaKey) {
    // Ctrl+Click 多选
    const next = new Set(selectedSentenceIds.value)
    if (next.has(sentenceId)) {
      next.delete(sentenceId)
    } else {
      next.add(sentenceId)
    }
    selectedSentenceIds.value = next
  } else {
    // 普通点击清除多选
    selectedSentenceIds.value = new Set()
  }
}

/**
 * 监听原文编辑器中的光标变化，实时缓存偏移量
 */
function trackSourceCaretPosition() {
  if (!segmentStore.activeSentenceId) return
  const row = document.querySelector(`[data-sentence-id="${segmentStore.activeSentenceId}"]`)
  if (!row) return
  const sourceEditor = row.querySelector('.segment-row__source-editor') as HTMLElement | null
  if (!sourceEditor) return

  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) return
  if (!sourceEditor.contains(selection.anchorNode)) return

  const range = selection.getRangeAt(0)
  const preCaretRange = range.cloneRange()
  preCaretRange.selectNodeContents(sourceEditor)
  preCaretRange.setEnd(range.startContainer, range.startOffset)
  lastSourceCaretOffset.value = preCaretRange.toString().length
}

const canSplitSegment = computed(() => {
  if (!activeSegment.value) return false
  return activeSegmentCanWrite.value && (activeSegment.value.source_text || '').length >= 2
})

const canMergeSegment = computed(() => {
  if (selectedSentenceIds.value.size < 2) return false
  return Array.from(selectedSentenceIds.value).every((sentenceId) => {
    const segment = segmentStore.segments.find((item) => item.sentence_id === sentenceId)
    return Boolean(segment?.can_write)
  })
})

async function handleSplitSegment() {
  if (!activeSegment.value || !canSplitSegment.value) return

  const caretOffset = lastSourceCaretOffset.value
  if (caretOffset === null || caretOffset <= 0) {
    toast.warn({ message: t('workbench.messages.splitNoCaret') })
    return
  }

  const sourceText = activeSegment.value.source_text || ''
  if (caretOffset >= sourceText.length) {
    toast.warn({ message: t('workbench.messages.splitNoCaret') })
    return
  }

  try {
    await segmentStore.splitSegment(activeSegment.value.sentence_id, caretOffset)
    lastSourceCaretOffset.value = null
    // 刷新预览（如果预览面板已打开）
    if (activeBottomTool.value === 'source-preview' || activeBottomTool.value === 'split-preview') {
      await segmentStore.ensurePreviewLoaded('source')
    } else if (activeBottomTool.value === 'target-preview') {
      await segmentStore.ensurePreviewLoaded('target')
    }
    toast.success({ message: t('workbench.messages.splitSuccess') })
  } catch (error: any) {
    toast.error({ message: error?.response?.data?.detail || t('workbench.messages.splitFailed') })
  }
}

async function handleMergeSegment() {
  if (!canMergeSegment.value) {
    toast.warn({ message: t('workbench.messages.mergeSelectAtLeast') })
    return
  }

  // 按当前显示顺序排列选中的句段
  const selectedIds = selectedSentenceIds.value
  const orderedSegments = editorSegments.value.filter((s) => selectedIds.has(s.sentence_id))

  if (orderedSegments.length < 2) {
    toast.warn({ message: t('workbench.messages.mergeSelectAtLeast') })
    return
  }

  // CAD 文件（DWG/DXF）：允许跨 block 合并，不检查相邻性
  // 其他格式：必须同一 block
  const first = orderedSegments[0]
  if (isCadFile.value) {
    // CAD 文件：用户选择的句段即可合并，不强制检查相邻性
    // CAD 图纸的实体位置关系复杂，由用户自行判断
  } else {
    // 其他格式：检查是否属于同一段落
    const notSameBlock = orderedSegments.some(
      (s) =>
        s.block_index !== first.block_index
        || s.row_index !== first.row_index
        || s.cell_index !== first.cell_index,
    )
    if (notSameBlock) {
      toast.warn({ message: t('workbench.messages.mergeDifferentBlock') })
      return
    }
  }

  try {
    const baseSentenceId = orderedSegments[0].sentence_id
    for (let i = 1; i < orderedSegments.length; i++) {
      await segmentStore.mergeSegment(baseSentenceId, orderedSegments[i].sentence_id)
    }
    selectedSentenceIds.value = new Set()
    // 刷新预览（如果预览面板已打开）
    if (activeBottomTool.value === 'source-preview' || activeBottomTool.value === 'split-preview') {
      await segmentStore.ensurePreviewLoaded('source')
    } else if (activeBottomTool.value === 'target-preview') {
      await segmentStore.ensurePreviewLoaded('target')
    }
    toast.success({ message: t('workbench.messages.mergeSuccess') })
  } catch (error: any) {
    toast.error({ message: error?.response?.data?.detail || t('workbench.messages.mergeFailed') })
  }
}

// ========== 富文本格式化功能 ==========

/**
 * 获取当前活动的编辑器元素
 */
function getActiveEditorElement(): HTMLElement | null {
  if (!segmentStore.activeSentenceId) return null
  const selector = `[data-segment-target="true"][data-sentence-id="${segmentStore.activeSentenceId}"]`
  return document.querySelector(selector)
}

/**
 * 应用文本格式（粗体、斜体、下划线等）
 */
function applyTextFormat(format: TextFormat) {
  const editor = getActiveEditorElement()

  if (editor) {
    // 确保编辑器获得焦点
    editor.focus()

    // 如果没有选区或选区不在编辑器内，将光标放到编辑器末尾
    const selection = window.getSelection()
    if (!selection || selection.rangeCount === 0 || !editor.contains(selection.anchorNode)) {
      const range = document.createRange()
      range.selectNodeContents(editor)
      range.collapse(false)
      selection?.removeAllRanges()
      selection?.addRange(range)
    }

    // 应用格式（会同时切换 activeFormats 状态）
    richTextEditor.applyFormat(format, editor)

    // 触发 input 事件以同步数据
    editor.dispatchEvent(new Event('input', { bubbles: true }))
  } else {
    // 没有活动编辑器时，只切换格式状态
    richTextEditor.activeFormats[format] = !richTextEditor.activeFormats[format]
    richTextEditor.formatOverrideActive.value = true
  }
}

/**
 * 清除格式
 * - 有选中文本时：清除选中文本的格式
 * - 没有选中文本时：清除整个段落的所有格式
 */
function clearSelectedFormat() {
  const editor = getActiveEditorElement()
  if (!editor) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }

  editor.focus()

  const selection = window.getSelection()
  const hasSelectedText = selection && !selection.isCollapsed && editor.contains(selection.anchorNode)

  if (hasSelectedText) {
    // 有选中文本：清除选中文本的格式
    if (richTextEditor.clearFormat(editor)) {
      editor.dispatchEvent(new Event('input', { bubbles: true }))
      toast.success(t('workbench.ribbon.messages.formatCleared'))
    }
  } else {
    // 没有选中文本：清除整个段落的所有格式
    const plainText = richTextEditor.clearAllFormatInElement(editor)
    if (activeSegment.value) {
      updateSegmentTarget(activeSegment.value.sentence_id, plainText)
    }
    toast.success(t('workbench.ribbon.messages.allFormatCleared'))
  }

  // 重置格式状态
  richTextEditor.resetActiveFormats()
  showClearFormatMenu.value = false
}

/**
 * 清除整个段落的所有格式
 */
function clearAllFormat() {
  const editor = getActiveEditorElement()
  if (!editor) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }

  const plainText = richTextEditor.clearAllFormatInElement(editor)
  if (activeSegment.value) {
    updateSegmentTarget(activeSegment.value.sentence_id, plainText)
  }
  toast.success(t('workbench.ribbon.messages.allFormatCleared'))
  showClearFormatMenu.value = false
}

/**
 * 转换大小写
 */
function applyCase(caseType: CaseType) {
  const editor = getActiveEditorElement()
  if (!editor) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }

  editor.focus()

  if (richTextEditor.changeCase(caseType, editor)) {
    editor.dispatchEvent(new Event('input', { bubbles: true }))
  } else {
    toast.warn(t('workbench.ribbon.messages.selectTextFirst'))
  }
  showCaseMenu.value = false
}

/**
 * 切换显示标记
 */
function toggleVisibleCharacters() {
  const enabled = richTextEditor.toggleVisibleCharacters()
  if (enabled) {
    toast.info(t('workbench.ribbon.messages.visibleCharsOn'))
  } else {
    toast.info(t('workbench.ribbon.messages.visibleCharsOff'))
  }
}

/**
 * 关闭所有下拉菜单
 */
function closeAllMenus() {
  showCaseMenu.value = false
  showClearFormatMenu.value = false
  showSpecialCharMenu.value = false
}

/**
 * 插入特殊字符到当前活动编辑器
 */
function insertSpecialChar(char: string) {
  const editor = getActiveEditorElement()
  if (!editor) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    showSpecialCharMenu.value = false
    return
  }

  editor.focus()

  // 确保光标在编辑器内
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0 || !editor.contains(selection.anchorNode)) {
    const range = document.createRange()
    range.selectNodeContents(editor)
    range.collapse(false)
    selection?.removeAllRanges()
    selection?.addRange(range)
  }

  document.execCommand('insertText', false, char)
  editor.dispatchEvent(new Event('input', { bubbles: true }))

  // 记录到最近使用
  const recent = recentSpecialChars.value.filter(c => c !== char)
  recent.unshift(char)
  recentSpecialChars.value = recent.slice(0, 10)
  try {
    window.localStorage.setItem(RECENT_CHARS_STORAGE_KEY, JSON.stringify(recentSpecialChars.value))
  } catch {}

  showSpecialCharMenu.value = false
}

// ========== 富文本格式化功能结束 ==========

function toggleSourceEditing() {
  if (!segmentStore.activeSentenceId) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }
  if (!activeSegmentCanWrite.value) {
    toast.warn('当前流程阶段无编辑权限')
    return
  }
  sourceEditing.value = !sourceEditing.value
}

function copySourceToTarget() {
  if (!activeSegment.value) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }
  if (!activeSegmentCanWrite.value) {
    toast.warn('当前流程阶段无编辑权限')
    return
  }

  const sourceText = getSegmentCopyableSourceText(activeSegment.value)
  updateSegmentTarget(activeSegment.value.sentence_id, sourceText)
  toast.success(t('workbench.ribbon.messages.sourceCopied'))
}

function clearActiveTarget() {
  if (!activeSegment.value) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }
  if (!activeSegmentCanWrite.value) {
    toast.warn('当前流程阶段无编辑权限')
    return
  }

  updateSegmentTarget(activeSegment.value.sentence_id, '')
  toast.success(t('workbench.ribbon.messages.targetCleared'))
}

function resolveAddTermTargetBaseId() {
  const candidates = addTermTargetTermBases.value
  if (candidates.some((termBase) => termBase.id === addTermTargetBaseId.value)) {
    return addTermTargetBaseId.value
  }
  if (candidates.some((termBase) => termBase.id === selectedTermBaseId.value)) {
    return selectedTermBaseId.value
  }
  const legacyBoundId = segmentStore.fileRecord?.term_base_id || ''
  if (legacyBoundId && candidates.some((termBase) => termBase.id === legacyBoundId)) {
    return legacyBoundId
  }
  return candidates[0]?.id || ''
}

async function openAddTermDialog() {
  if (!activeSegment.value) {
    toast.warn(t('workbench.ribbon.noActiveSegment'))
    return
  }

  if (termBases.value.length === 0 && !loadingTermBases.value) {
    await loadTermBases()
  }

  if (addTermTargetTermBases.value.length === 0) {
    toast.warn('当前文件没有可写入的已启用术语库。')
    activeSideTool.value = 'terms'
    return
  }

  addTermSourceText.value = ''
  addTermTargetText.value = ''
  addTermTargetBaseId.value = resolveAddTermTargetBaseId()
  addTermFormError.value = ''
  showAddTermDialog.value = true
}

function closeAddTermDialog() {
  if (addingTerm.value) {
    return
  }
  showAddTermDialog.value = false
  addTermFormError.value = ''
}

async function submitAddTermForm() {
  const sourceText = normalizeTextForSaveToTM(addTermSourceText.value)
  const targetText = normalizeTextForSaveToTM(addTermTargetText.value)
  const targetBaseId = addTermTargetBaseId.value

  if (!sourceText || !targetText) {
    addTermFormError.value = '请填写原文内容和译文内容。'
    return
  }
  if (!addTermTargetTermBases.value.some((termBase) => termBase.id === targetBaseId)) {
    addTermFormError.value = '请选择当前文件可写入的已启用术语库。'
    return
  }

  addingTerm.value = true
  pageError.value = ''
  addTermFormError.value = ''
  try {
    await http.post(`/term-bases/${targetBaseId}/entries`, {
      source_text: sourceText,
      target_text: targetText,
    })
    selectedTermBaseId.value = targetBaseId
    await loadTermEntries()
    activeSideTool.value = 'terms'
    showAddTermDialog.value = false
    toast.success(t('workbench.ribbon.messages.termAdded'))
  } catch (error) {
    addTermFormError.value = getErrorMessage(error, t('workbench.ribbon.messages.termAddFailed'))
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
  try {
    window.localStorage.setItem(REVISION_TRACE_VISIBLE_STORAGE_KEY, visible ? '1' : '0')
  } catch {}
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
  activeSideTool.value = null
  closeBottomDrawer()
  openRevisionMenu.value = null
  suppressSegmentFilterWatch = true
  resetSegmentSearch()
  suppressSegmentFilterWatch = false
  commentStore.stopPolling()
  termEntries.value = []
  termBases.value = []
  selectedTermBaseId.value = ''
  resourceSearchQuery.value = ''
  resetResourceSearchResults()
  setCurrentTermQAReport(null)

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
    await loadLatestTermQAReport()

    const boundTermBaseId = segmentStore.fileRecord?.term_base_id
    if (boundTermBaseId) {
      selectedTermBaseId.value = boundTermBaseId
    }
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.taskLoad'))
  } finally {
    await nextTick()
    observeSegmentEditorResults()
    scheduleSegmentEditorScrollbarGutterUpdate()
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
    observeSegmentEditorResults()
    scheduleSegmentEditorScrollbarGutterUpdate()
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
  const currentSentenceId = activeSegment.value?.sentence_id || ''
  if (llmScope.value === 'current_segment' && currentSentenceId && !activeSegmentCanWrite.value) {
    toast.warn('当前流程阶段无编辑权限')
    return
  }
  if (llmScope.value === 'current_segment' && !currentSentenceId) {
    toast.warn('请先选中一个句段。')
    return
  }
  try {
    await segmentStore.startLLMTranslation(llmScope.value, llmProvider.value, {
      guidelineTemplateId: selectedGuidelineTemplateId.value || undefined,
      temporaryPrompt: workbenchGuidelines.value,
      model: llmModel.value || undefined,
      sentenceId: llmScope.value === 'current_segment' ? currentSentenceId : undefined,
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
    if (data.created_count + data.updated_count > 0) {
      refreshGlobalNotifications()
    }
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
  if (exporting.value) {
    return
  }
  if (showExportMenu.value) {
    showExportMenu.value = false
    return
  }

  // 加载导出选项
  await loadExportOptions()
  showExportMenu.value = true
}

function clearExportPollTimer() {
  if (exportPollTimer !== null) {
    window.clearTimeout(exportPollTimer)
    exportPollTimer = null
  }
}

function waitForExportPoll(ms: number) {
  clearExportPollTimer()
  return new Promise<void>((resolve) => {
    exportPollTimer = window.setTimeout(() => {
      exportPollTimer = null
      resolve()
    }, ms)
  })
}

async function waitForFileExportTask(task: FileExportTask) {
  let currentTask = task
  while (true) {
    exportProgress.value = currentTask.progress
    exportMessage.value = currentTask.message || `导出处理中：${currentTask.progress}%`

    if (currentTask.status === 'completed') {
      return currentTask
    }
    if (currentTask.status === 'failed') {
      throw new Error(currentTask.error || currentTask.message || '导出失败。')
    }

    await waitForExportPoll(1200)
    const { data } = await http.get<FileExportTask>(`/file-records/export-tasks/${currentTask.task_id}`)
    currentTask = data
  }
}

async function exportWithType(exportType: string) {
  if (!segmentStore.fileRecord || exporting.value) return

  pageError.value = ''
  exporting.value = true
  exportProgress.value = 0
  exportMessage.value = '导出任务提交中。'
  showExportMenu.value = false

  try {
    const { data: task } = await http.post<FileExportTask>(
      `/file-records/${segmentStore.fileRecord.id}/exports`,
      null,
      { params: { type: exportType } },
    )
    const completedTask = await waitForFileExportTask(task)
    const response = await http.get(
      `/file-records/export-tasks/${completedTask.task_id}/download`,
      { responseType: 'blob' },
    )

    // 从响应头获取文件名
    const resolvedFilename = resolveDownloadFilename(response.headers['content-disposition'], `export.${exportType}`)
    downloadBlob(response.data, resolvedFilename)
    toast.success('导出完成，文件已开始下载。')
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '导出失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '导出失败。'
  } finally {
    clearExportPollTimer()
    exporting.value = false
    exportProgress.value = 0
    exportMessage.value = ''
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
  if (
    !target.closest('.confirm-segment-menu')
    && !target.closest('.confirm-segment-menu__dropdown')
  ) {
    openConfirmMenu.value = false
  }
  // 关闭格式化相关的下拉菜单
  if (!target.closest('.case-menu') && !target.closest('.clear-format-menu') && !target.closest('.special-char-menu')) {
    closeAllMenus()
  }
}

async function handleCommentDraft(draft: CommentAnchorDraft) {
  commentStore.setDraftAnchor(draft)
  commentStore.setActiveComment(null)
  segmentStore.setActiveSentence(draft.sentence_id)
  await handlePreviewFocus(draft.sentence_id)
  activeSideTool.value = 'notes'
}

async function handleCommentFocus(commentId: string) {
  commentStore.setDraftAnchor(null)
  commentStore.setActiveComment(commentId)
  const comment = commentStore.comments.find((item) => item.id === commentId)
  if (comment?.sentence_id) {
    segmentStore.setActiveSentence(comment.sentence_id)
    await handlePreviewFocus(comment.sentence_id)
  }
  activeSideTool.value = 'notes'
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

// 加载参考文件匹配结果（工作台初始化时调用）
// 已废弃：参考资料分析后会自动同步到项目级 TM/术语库，匹配通过原生通道命中。

async function handleReferenceAITranslateComplete(result: { updated_count: number; error_count: number }) {
  // AI翻译完成后重新加载句段
  const message = result.error_count > 0 
    ? `AI翻译完成: 成功 ${result.updated_count} 条，失败 ${result.error_count} 条`
    : `AI翻译完成: 成功翻译 ${result.updated_count} 条`
  toast.success(message)
  await segmentStore.fetchSegments({ 
    fileRecordId: props.id, 
    skip: 0, 
    limit: segmentStore.pageSize 
  })
}

async function ensureMatchInfoPanelOpen() {
  pageError.value = ''
  activeSideTool.value = 'match-info'

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
  selectedSentenceIds.value = new Set()
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

watch(resourceSearchMode, () => {
  if (activeSideTool.value === 'resource-search' && resourceSearchQuery.value.trim()) {
    void runResourceSearch()
  }
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

watch(
  () => [
    editorSegments.value.length,
    hasEditorSegmentFilter.value,
    searchLoadingAllSegments.value,
    isStandaloneWorkbench.value,
  ],
  async () => {
    await nextTick()
    observeSegmentEditorResults()
    scheduleSegmentEditorScrollbarGutterUpdate()
  },
  { flush: 'post', immediate: true },
)

/**
 * 处理选区变化，更新格式按钮状态
 * 注意：只在有选中文本时才更新状态，避免覆盖手动设置的格式状态
 */
function handleSelectionChange() {
  const selection = window.getSelection()
  // 只有当有选中文本时才根据选区更新格式状态
  if (selection && !selection.isCollapsed) {
    richTextEditor.updateActiveFormats()
  }
  // 追踪原文编辑器中的光标位置（用于拆分句段）
  trackSourceCaretPosition()
}

onMounted(() => {
  window.addEventListener('resize', handleResize)
  document.addEventListener('click', handleClickOutside)
  document.addEventListener('selectionchange', handleSelectionChange)
  void nextTick(() => {
    observeSegmentEditorResults()
    scheduleSegmentEditorScrollbarGutterUpdate()
  })
  void loadTask()
  void loadGuidelineTemplates()
})

onBeforeUnmount(() => {
  stopBottomDrawerResize?.()
  segmentEditorResizeObserver?.disconnect()
  if (segmentEditorScrollbarFrame !== null) {
    window.cancelAnimationFrame(segmentEditorScrollbarFrame)
  }
  window.removeEventListener('resize', handleResize)
  document.removeEventListener('click', handleClickOutside)
  document.removeEventListener('selectionchange', handleSelectionChange)
  clearExportPollTimer()
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
    :class="{ 'is-standalone': isStandaloneWorkbench, 'is-stable-grid': isStandaloneWorkbench }"
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
          <div class="export-dropdown">
            <button
              class="workbench-ribbon__top-action"
              data-testid="workbench-export-button"
              type="button"
              :disabled="!segmentStore.canExport || exporting"
              :title="exportButtonTitle"
              @click="toggleExportMenu"
            >
              <Loader2 v-if="exporting" class="lucide-spin" :size="15" />
              <Download v-else :size="15" />
              <span>{{ exportButtonLabel }}</span>
              <ChevronDown :size="12" />
            </button>
            <div v-if="showExportMenu" class="export-dropdown__menu">
              <div v-if="loadingExportOptions" class="export-dropdown__loading">
                <Loader2 class="lucide-spin" :size="14" />
                <span>{{ t('common.loading') }}</span>
              </div>
              <template v-else>
                <button
                  v-for="option in exportOptions"
                  :key="option.id"
                  type="button"
                  class="export-dropdown__item"
                  :disabled="exporting"
                  @click="exportWithType(option.id)"
                >
                  <span class="export-dropdown__item-name">{{ option.name }}</span>
                  <span class="export-dropdown__item-desc">{{ option.description }}</span>
                </button>
              </template>
            </div>
          </div>
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
              :disabled="confirmationActionLoading || !activeSegmentCanWrite"
              @click="handleConfirmCurrentFromMenu"
            >
              确认当前句段
            </button>
            <button
              data-testid="workbench-confirm-next"
              type="button"
              role="menuitem"
              :disabled="confirmationActionLoading || !activeSegmentCanWrite"
              @click="openConfirmMenu = false; void confirmAndMoveToNextUnconfirmed()"
            >
              确认并跳到下一个
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
            <button class="tool-line tool-button" type="button" :disabled="!activeSegmentCanWrite" @click="undoActiveSegmentEdit">
              <span class="icon-text-area">
                <span class="tool-item">
                  <Undo2 class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.undo') }}</span>
                </span>
              </span>
            </button>
            <button class="tool-line tool-button" type="button" :disabled="!activeSegmentCanWrite" @click="redoActiveSegmentEdit">
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
            <button class="tool-line tool-button" type="button" :disabled="!activeSegmentCanWrite" @click="copySourceToTarget">
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
            <button class="tool-line tool-button" type="button" :disabled="!activeSegmentCanWrite" @click="clearActiveTarget">
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
            <button class="tool-line style-item tool-button" type="button" :class="{ 'is-active': richTextEditor.activeFormats.bold }" :disabled="!activeSegmentCanWrite" :title="t('workbench.ribbon.bold')" @mousedown.prevent @click="applyTextFormat('bold')">
              <span class="icon-text-area"><span class="tool-item"><Bold class="tool-label-icon" :size="15" /></span></span>
            </button>
            <button class="tool-line style-item tool-button" type="button" :class="{ 'is-active': richTextEditor.activeFormats.strikethrough }" :disabled="!activeSegmentCanWrite" :title="t('workbench.ribbon.strike')" @mousedown.prevent @click="applyTextFormat('strikethrough')">
              <span class="icon-text-area"><span class="tool-item"><Strikethrough class="tool-label-icon" :size="15" /></span></span>
            </button>
          </span>
          <span class="tool-col align-left">
            <button class="tool-line style-item tool-button" type="button" :class="{ 'is-active': richTextEditor.activeFormats.italic }" :disabled="!activeSegmentCanWrite" :title="t('workbench.ribbon.italic')" @mousedown.prevent @click="applyTextFormat('italic')">
              <span class="icon-text-area"><span class="tool-item"><Italic class="tool-label-icon" :size="15" /></span></span>
            </button>
            <button class="tool-line style-item tool-button" type="button" :class="{ 'is-active': richTextEditor.activeFormats.superscript }" :disabled="!activeSegmentCanWrite" :title="t('workbench.ribbon.superscript')" @mousedown.prevent @click="applyTextFormat('superscript')">
              <span class="icon-text-area"><span class="tool-item"><Superscript class="tool-label-icon" :size="15" /></span></span>
            </button>
          </span>
          <span class="tool-col align-left">
            <button class="tool-line style-item tool-button" type="button" :class="{ 'is-active': richTextEditor.activeFormats.underline }" :disabled="!activeSegmentCanWrite" :title="t('workbench.ribbon.underline')" @mousedown.prevent @click="applyTextFormat('underline')">
              <span class="icon-text-area"><span class="tool-item"><Underline class="tool-label-icon" :size="15" /></span></span>
            </button>
            <button class="tool-line style-item tool-button" type="button" :class="{ 'is-active': richTextEditor.activeFormats.subscript }" :disabled="!activeSegmentCanWrite" :title="t('workbench.ribbon.subscript')" @mousedown.prevent @click="applyTextFormat('subscript')">
              <span class="icon-text-area"><span class="tool-item"><Subscript class="tool-label-icon" :size="15" /></span></span>
            </button>
          </span>
          <span class="tool-col align-left">
            <div class="case-menu">
              <button class="tool-line style-item tool-button" type="button" :disabled="!activeSegmentCanWrite" :title="t('workbench.ribbon.caseChange')" @mousedown.prevent @click.stop="showCaseMenu = !showCaseMenu">
                <span class="icon-text-area has_dropdown"><span class="tool-item"><Type class="tool-label-icon" :size="15" /></span></span>
                <span class="dropdown-link" aria-hidden="true">
                  <ChevronDown :size="10" />
                </span>
              </button>
              <div v-if="showCaseMenu" class="case-menu__dropdown">
                <button type="button" class="case-menu__item" @mousedown.prevent @click="applyCase('upper')">{{ t('workbench.ribbon.caseUpper') }}</button>
                <button type="button" class="case-menu__item" @mousedown.prevent @click="applyCase('lower')">{{ t('workbench.ribbon.caseLower') }}</button>
                <button type="button" class="case-menu__item" @mousedown.prevent @click="applyCase('capitalize')">{{ t('workbench.ribbon.caseCapitalize') }}</button>
                <button type="button" class="case-menu__item" @mousedown.prevent @click="applyCase('sentence')">{{ t('workbench.ribbon.caseSentence') }}</button>
              </div>
            </div>
            <button class="tool-line style-item tool-button" type="button" :class="{ 'is-active': richTextEditor.visibleCharactersEnabled.value }" :disabled="!activeSegmentCanWrite" :title="t('workbench.ribbon.visibleCharacters')" @click="toggleVisibleCharacters">
              <span class="icon-text-area"><span class="tool-item"><Pilcrow class="tool-label-icon" :size="15" /></span></span>
            </button>
          </span>
        </div>

        <div class="tool-group">
          <div class="clear-format-menu">
            <button
              class="tool-col tool-col--big tool-button clear-format-menu__main"
              type="button"
              :disabled="!activeSegmentCanWrite"
              @mousedown.prevent
              @click="clearSelectedFormat"
            >
              <span class="tool-line line1 with-big-icon">
                <span class="icon-text-area">
                  <BrushCleaning class="tool-single-icon" :size="27" />
                </span>
              </span>
              <span class="tool-line"><span class="label">{{ t('workbench.ribbon.clearFormat') }}</span></span>
            </button>
            <button
              class="clear-format-menu__toggle"
              type="button"
              :disabled="!activeSegmentCanWrite"
              @click.stop="showClearFormatMenu = !showClearFormatMenu"
            >
              <ChevronDown :size="12" />
            </button>
            <div v-if="showClearFormatMenu" class="clear-format-menu__dropdown">
              <button type="button" class="clear-format-menu__item" @mousedown.prevent @click="clearSelectedFormat">{{ t('workbench.ribbon.clearSelectedFormat') }}</button>
              <button type="button" class="clear-format-menu__item" @mousedown.prevent @click="clearAllFormat">{{ t('workbench.ribbon.clearAllFormat') }}</button>
            </div>
          </div>
        </div>

        <div class="tool-group">
          <button class="tool-col tool-col--big tool-button" type="button" :class="{ active: sourceEditing }" @click="toggleSourceEditing">
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
            <button class="tool-line tool-button" type="button" :disabled="!canMergeSegment" @click="handleMergeSegment">
              <span class="icon-text-area">
                <span class="tool-item" :class="{ disabled: !canMergeSegment }">
                  <Combine class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.mergeSegment') }}</span>
                </span>
              </span>
            </button>
            <button class="tool-line tool-button" type="button" :disabled="!canSplitSegment" @click="handleSplitSegment">
              <span class="icon-text-area">
                <span class="tool-item" :class="{ disabled: !canSplitSegment }">
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
          <button class="tool-col tool-col--big tool-button" type="button" :disabled="addingTerm" @click="void openAddTermDialog()">
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
            <div class="special-char-menu">
              <button class="tool-line tool-button" type="button" :disabled="!activeSegment" @mousedown.prevent @click.stop="showSpecialCharMenu = !showSpecialCharMenu">
                <span class="icon-text-area">
                  <span class="tool-item">
                    <Sigma class="tool-label-icon" :size="16" />
                    <span class="text">{{ t('workbench.ribbon.specialCharacters') }}</span>
                  </span>
                </span>
              </button>
              <div v-if="showSpecialCharMenu" class="special-char-menu__dropdown">
                <div class="special-char-menu__title">{{ t('workbench.ribbon.specialCharacters') }}</div>
                <div class="special-char-menu__grid">
                  <template v-for="(row, rowIdx) in specialCharacters" :key="rowIdx">
                    <button
                      v-for="(char, colIdx) in row"
                      :key="`${rowIdx}-${colIdx}`"
                      type="button"
                      class="special-char-menu__char"
                      :title="char"
                      @mousedown.prevent
                      @click="insertSpecialChar(char)"
                    >{{ char }}</button>
                  </template>
                </div>
                <div v-if="recentSpecialChars.length" class="special-char-menu__recent">
                  <div class="special-char-menu__recent-row">
                    <button
                      v-for="(char, idx) in recentSpecialChars"
                      :key="idx"
                      type="button"
                      class="special-char-menu__char"
                      :title="char"
                      @mousedown.prevent
                      @click="insertSpecialChar(char)"
                    >{{ char }}</button>
                  </div>
                </div>
              </div>
            </div>
            <button
              class="tool-line tool-button"
              type="button"
              :disabled="generatingTermQAReport"
              @click="generateCurrentFileTermQAReport"
            >
              <span class="icon-text-area has_dropdown">
                <span class="tool-item">
                  <Loader2 v-if="generatingTermQAReport" class="tool-label-icon lucide-spin" :size="16" />
                  <ShieldCheck v-else class="tool-label-icon" :size="16" />
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
            <button
              class="tool-line tool-button"
              type="button"
              :disabled="!canOpenWorkflowTransition || !hasWorkflowTransitionTarget('forward')"
              @click="openWorkflowTransitionDialog('forward')"
            >
              <span class="icon-text-area">
                <span class="tool-item">
                  <ArrowRight class="tool-label-icon" :size="16" />
                  <span class="text">{{ t('workbench.ribbon.workflowForward') }}</span>
                </span>
              </span>
            </button>
            <button
              class="tool-line tool-button"
              type="button"
              :disabled="!canOpenWorkflowTransition || !hasWorkflowTransitionTarget('back')"
              @click="openWorkflowTransitionDialog('back')"
            >
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

          <div class="export-dropdown export-dropdown--toolbar">
            <button
              class="button workbench-action workbench-action--export"
              data-testid="workbench-export-button-toolbar"
              type="button"
              :disabled="!segmentStore.canExport || exporting"
              :title="exportButtonTitle"
              @click="toggleExportMenu"
            >
              <Loader2 v-if="exporting" class="lucide-spin" :size="14" />
              <Download v-else :size="14" />
              {{ exportButtonLabel }}
              <ChevronDown :size="12" />
            </button>
            <div v-if="showExportMenu" class="export-dropdown__menu">
              <div v-if="loadingExportOptions" class="export-dropdown__loading">
                <Loader2 class="lucide-spin" :size="14" />
                <span>{{ t('common.loading') }}</span>
              </div>
              <template v-else>
                <button
                  v-for="option in exportOptions"
                  :key="option.id"
                  type="button"
                  class="export-dropdown__item"
                  :disabled="exporting"
                  @click="exportWithType(option.id)"
                >
                  <span class="export-dropdown__item-name">{{ option.name }}</span>
                  <span class="export-dropdown__item-desc">{{ option.description }}</span>
                </button>
              </template>
            </div>
          </div>

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

    <Modal
      :open="showGuidelinesPanel"
      width="min(760px, calc(100vw - 32px))"
      @close="closeGuidelinesPanel"
    >
      <div class="workbench-guidelines-panel">
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
      </div>
    </Modal>

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

    <section v-else class="workbench-layout" :class="{ 'has-active-tool': activeSideTool }">
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
            @keydown.esc.stop="void closeSegmentSearchPanel()"
          >
            <div class="workbench-search-panel__form workbench-search-panel__form--compact">
              <div class="workbench-search-panel__line">
                <label class="workbench-search-panel__field">
                  <span>{{ t('workbench.search.sourceShortLabel') }}</span>
                  <span class="workbench-search-panel__input-wrap">
                    <input
                      ref="sourceSearchInputRef"
                      v-model="sourceSearchQuery"
                      class="field__control"
                      :class="{ 'has-clear-action': sourceSearchQuery }"
                      type="text"
                      :placeholder="t('workbench.search.sourcePlaceholder')"
                      @keydown.enter.prevent="void focusMatchedSegment(1)"
                    />
                    <button
                      v-if="sourceSearchQuery"
                      class="workbench-search-panel__input-clear"
                      type="button"
                      :title="t('workbench.search.clear')"
                      :aria-label="t('workbench.search.clear')"
                      @mousedown.prevent
                      @click="void clearSourceSegmentSearchQuery()"
                    >
                      <X :size="12" />
                    </button>
                  </span>
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
                  class="button workbench-action workbench-action--search workbench-search-panel__close"
                  type="button"
                  :title="t('workbench.search.collapse')"
                  :aria-label="t('workbench.search.collapse')"
                  @click="void closeSegmentSearchPanel()"
                >
                  <ChevronUp :size="14" />
                  <span>{{ t('workbench.search.collapseShort') }}</span>
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
          <div ref="segmentEditorResultsRef" class="segment-editor-results">
            <div class="segment-table-head" aria-hidden="true">
              <span>句段</span>
              <span>原文</span>
              <span>译文</span>
              <span>状态</span>
              <span>阶段</span>
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
                :adaptive="!isStandaloneWorkbench"
                :virtualized="!isStandaloneWorkbench"
                :overscan="isStandaloneWorkbench ? 8 : 4"
                :active-descendant="segmentStore.activeSentenceId ? `segment-${segmentStore.activeSentenceId}` : null"
                @reach-end="handleEditorReachEnd"
              >
                <template #default="{ item, index }">
                  <SegmentEditorRow
                    :ref="(instance) => setSegmentEditorRowRef(item.sentence_id, instance)"
                    :segment="item"
                    :index="getEditorSegmentDisplayIndex(item.sentence_id, index)"
                    :active="segmentStore.activeSentenceId === item.sentence_id"
                    :disabled="!item.can_write"
                    :source-editing="sourceEditing"
                    :selected="selectedSentenceIds.has(item.sentence_id)"
                    :pending-revision="revisionTraceVisible ? segmentStore.getRevisionTrace(item.sentence_id) : null"
                    :revision-busy="revisionActionLoading"
                    :matched-terms="segmentStore.activeSentenceId === item.sentence_id ? activeMatchedTerms : []"
                    :source-search-query="sourceSearchQuery"
                    :target-search-query="targetSearchQuery"
                    :show-visible-chars="richTextEditor.visibleCharactersEnabled.value"
                    :pending-formats="pendingFormatsForEditor"
                    @focus="segmentStore.setActiveSentence"
                    @activate-target="handleSegmentTargetActivate"
                    @update="updateSegmentTarget"
                    @update-source="updateSegmentSource"
                    @toggle-project-sync="toggleProjectSegmentSync"
                    @apply-partial-revision="handleApplyPartialRevision"
                    @ctrl-click="handleSegmentClick"
                  />
                </template>
              </VirtualList>
            </div>
          </div>

          <div class="segment-editor-footer">
          <Pagination
            :total="segmentStore.matchedSegmentCount"
            :page="segmentStore.currentPage"
            :page-size="segmentStore.pageSize"
            :page-sizes="segmentPageSizes"
            @update:page="handleSegmentPageChange"
            @update:page-size="handleSegmentPageSizeChange"
          />

          <div
            ref="bottomPanelRef"
            class="segment-editor-bottom-tools"
            :class="{ 'is-docked': activeBottomTool }"
            aria-label="工作台底部工具栏"
          >
            <button
              class="segment-editor-bottom-tool segment-editor-bottom-tool--qa"
              :class="{ 'is-active': activeBottomTool === 'qa-result' }"
              type="button"
              :title="termQAReport ? `QA结果：${termQAReport.active_issue_count} 条待处理` : '生成 QA 报告'"
              :aria-pressed="activeBottomTool === 'qa-result'"
              :disabled="loadingTermQAReport || generatingTermQAReport"
              @click="void openTermQAResult()"
            >
              <Loader2 v-if="loadingTermQAReport || generatingTermQAReport" class="lucide-spin" :size="14" />
              <ShieldCheck v-else :size="14" />
              <span>QA结果</span>
              <span
                v-if="termQAReport"
                class="segment-editor-bottom-tool__badge"
                :class="{ 'is-clean': termQAReport.active_issue_count === 0 }"
              >
                {{ termQAReport.active_issue_count }}
              </span>
            </button>
            <button
              v-for="tool in bottomToolButtons"
              :key="tool.key"
              class="segment-editor-bottom-tool"
              :class="[
                `segment-editor-bottom-tool--${tool.tone}`,
                {
                  'is-active': activeBottomTool === tool.key,
                  'is-loading': isBottomToolLoading(tool.key),
                },
              ]"
              type="button"
              :title="isBottomToolLoading(tool.key) ? t('common.loading') : tool.label"
              :aria-pressed="activeBottomTool === tool.key"
              :aria-busy="isBottomToolLoading(tool.key)"
              :disabled="isBottomToolDisabled(tool.key)"
              @click="void openBottomTool(tool.key)"
            >
              <Loader2 v-if="isBottomToolLoading(tool.key)" class="lucide-spin" :size="14" />
              <component :is="tool.icon" v-else :size="14" />
              <span>{{ isBottomToolLoading(tool.key) ? t('common.loading') : tool.label }}</span>
            </button>
          </div>

          </div>

          <Transition name="workbench-bottom-drawer">
            <section
              v-if="activeBottomTool"
              ref="bottomDrawerRef"
              class="workbench-bottom-drawer"
              :class="[
                `workbench-bottom-drawer--${activeBottomTool}`,
                {
                  'is-wide': activeBottomTool === 'split-preview' || activeBottomTool === 'qa-result',
                  'is-loading': bottomDrawerPreviewBusy,
                  'is-resizable': isPreviewDrawerResizable,
                  'is-resizing': isBottomDrawerResizing,
                },
              ]"
              :style="isPreviewDrawerResizable ? bottomDrawerHeightStyle : undefined"
              :aria-busy="bottomDrawerPreviewBusy"
              @keydown.esc.stop="closeBottomDrawer"
            >
              <div
                v-if="isPreviewDrawerResizable"
                class="workbench-bottom-drawer__resize-handle"
                role="separator"
                tabindex="0"
                title="拖动调整预览高度"
                aria-label="拖动调整预览高度"
                aria-orientation="horizontal"
                :aria-valuemin="BOTTOM_DRAWER_MIN_HEIGHT"
                :aria-valuemax="bottomDrawerMaxHeight"
                :aria-valuenow="bottomDrawerResizeValue"
                @pointerdown="startBottomDrawerResize"
                @keydown="handleBottomDrawerResizeKeydown"
              />

              <button
                v-if="activeBottomTool === 'history'"
                class="workbench-bottom-drawer__close"
                type="button"
                title="关闭"
                aria-label="关闭"
                @click="closeBottomDrawer"
              >
                <X :size="14" />
              </button>

              <div
                v-if="bottomDrawerPreviewBusy"
                class="workbench-bottom-drawer__loading"
                role="status"
                aria-live="polite"
              >
                <div class="workbench-bottom-drawer__loading-body">
                  <Loader2 class="lucide-spin" :size="30" />
                  <div>
                    <strong>{{ bottomDrawerPreviewLoadingTitle }}</strong>
                    <span>{{ bottomDrawerPreviewLoadingMessage }}</span>
                  </div>
                </div>
              </div>

              <SplitPreviewPanel
                v-if="activeBottomTool === 'split-preview'"
                key="bottom-split-preview"
                class="workbench-bottom-drawer__preview workbench-bottom-drawer__preview--split"
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
                @close="closeBottomDrawer"
                @focus-sentence="handlePreviewFocus"
                @focus-comment="handleCommentFocus"
                @request-comment="handleCommentDraft"
                @rendering-change="handleBottomPreviewRenderingChange"
              />

              <PreviewPanel
                v-else-if="activeBottomTool === 'source-preview' || activeBottomTool === 'target-preview'"
                key="bottom-single-preview"
                class="preview-panel--drawer workbench-bottom-drawer__preview"
                :title="activeBottomTool === 'source-preview' ? t('workbench.tools.sourcePreview') : t('workbench.tools.targetPreview')"
                :html="activeBottomTool === 'source-preview' ? segmentStore.previewHtml : targetPreviewHtml"
                :supported="activeBottomTool === 'source-preview' ? segmentStore.previewSupported : targetPreviewSupported"
                :loading="activeBottomTool === 'source-preview' ? sourcePreviewLoading : targetPreviewLoading"
                :active-sentence-id="segmentStore.activeSentenceId"
                :comments="commentStore.comments"
                :active-comment-id="commentStore.activeCommentId"
                :enable-comment-selection="true"
                :render-mode="activeBottomTool === 'target-preview' ? targetPreviewRenderMode : 'static'"
                :segments="activeBottomTool === 'target-preview' && targetPreviewRenderMode === 'target' ? segmentStore.segments : []"
                :updated-sentence-id="activeBottomTool === 'target-preview' ? segmentStore.lastPreviewUpdatedSentenceId : null"
                :updated-sentence-text="activeBottomTool === 'target-preview' ? segmentStore.lastPreviewUpdatedText : ''"
                :update-token="activeBottomTool === 'target-preview' ? segmentStore.previewUpdateToken : 0"
                @focus-sentence="handlePreviewFocus"
                @focus-comment="handleCommentFocus"
                @request-comment="handleCommentDraft"
                @close="closeBottomDrawer"
                @rendering-change="handleBottomPreviewRenderingChange"
              />

              <WorkbenchHistoryPanel
                v-else-if="activeBottomTool === 'history'"
                key="bottom-history"
                class="workbench-bottom-drawer__history"
                :active-sentence-id="segmentStore.activeSentenceId"
                :comments="commentStore.comments"
                :history="activeSegmentHistory"
              />

              <div v-else class="workbench-bottom-drawer__qa">
                <div class="workbench-bottom-drawer__header">
                  <div>
                    <div class="section-title section-title--tight">术语 QA 报告</div>
                    <p class="panel-subtitle">{{ segmentStore.fileRecord?.filename || '' }}</p>
                  </div>
                  <button
                    class="button preview-panel__close"
                    type="button"
                    @click="closeBottomDrawer"
                  >
                    关闭
                  </button>
                </div>

                <div v-if="loadingTermQAReport || (generatingTermQAReport && !termQAReport)" class="empty-state">
                  <Loader2 class="lucide-spin" :size="28" />
                  {{ generatingTermQAReport ? '正在生成术语 QA 报告' : '正在加载最近报告' }}
                </div>

                <template v-else-if="termQAReport">
                  <div class="term-qa-dialog__summary">
                    <span>检查句段：{{ termQAReport.checked_segments }}</span>
                    <span>总问题数：{{ termQAReport.issue_count }}</span>
                    <span>待处理：{{ termQAReport.active_issue_count }}</span>
                    <span>已忽略：{{ termQAReport.ignored_count }}</span>
                    <span>报告时间：{{ termQAReport.created_at || '' }}</span>
                  </div>

                  <div class="term-qa-dialog__actions">
                    <button
                      class="button button--ghost"
                      type="button"
                      :disabled="activeTermQAReportItems.length === 0 || updatingTermQAIgnore"
                      @click="toggleAllActiveTermQAItems(!allActiveTermQAItemsSelected)"
                    >
                      {{ allActiveTermQAItemsSelected ? '取消全选' : '全选未忽略' }}
                    </button>
                    <button
                      class="button button--ghost"
                      type="button"
                      :disabled="selectedActiveTermQAReportItems.length === 0 || updatingTermQAIgnore"
                      @click="void ignoreSelectedTermQAReportItems()"
                    >
                      <Loader2 v-if="updatingTermQAIgnore" class="lucide-spin" :size="14" />
                      忽略选中 {{ selectedActiveTermQAReportItems.length || '' }}
                    </button>
                    <button
                      class="button"
                      type="button"
                      :disabled="downloadingTermQAReport"
                      @click="downloadCurrentTermQAReport"
                    >
                      <Loader2 v-if="downloadingTermQAReport" class="lucide-spin" :size="14" />
                      <Download v-else :size="14" />
                      导出 XLSX
                    </button>
                    <button
                      class="button button--ghost"
                      type="button"
                      :disabled="generatingTermQAReport"
                      @click="void generateCurrentFileTermQAReport()"
                    >
                      <Loader2 v-if="generatingTermQAReport" class="lucide-spin" :size="14" />
                      重新生成
                    </button>
                  </div>

                  <div v-if="termQAReport.items.length === 0" class="empty-state">
                    未发现术语不一致问题。
                  </div>
                  <div v-else class="term-qa-dialog__table-wrap">
                    <table class="term-qa-dialog__table">
                      <thead>
                        <tr>
                          <th>选择</th>
                          <th>句段</th>
                          <th>原文术语</th>
                          <th>期望译文</th>
                          <th>当前译文</th>
                          <th>状态</th>
                          <th>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr
                          v-for="item in termQAReport.items.slice(0, 50)"
                          :key="item.id"
                          class="term-qa-dialog__row"
                          :class="{
                            'is-locating': locatingTermQAReportItemId === item.id,
                            'is-ignored': item.ignored,
                          }"
                          tabindex="0"
                          :aria-label="`跳转到句段 ${item.sentence_id}`"
                          @click="void focusTermQAReportItem(item)"
                          @keydown.enter.prevent="void focusTermQAReportItem(item)"
                          @keydown.space.prevent="void focusTermQAReportItem(item)"
                        >
                          <td>
                            <input
                              type="checkbox"
                              :checked="selectedTermQAItemIds.has(item.id)"
                              :disabled="item.ignored"
                              aria-label="选择报告项"
                              @click.stop
                              @change.stop="handleTermQAItemSelectionChange(item.id, $event)"
                            >
                          </td>
                          <td>
                            <span class="term-qa-dialog__segment">
                              <Loader2
                                v-if="locatingTermQAReportItemId === item.id"
                                class="lucide-spin"
                                :size="13"
                              />
                              {{ item.sentence_id }}
                            </span>
                          </td>
                          <td>{{ item.source_term }}</td>
                          <td>{{ item.expected_target_term }}</td>
                          <td>{{ item.target_text || '未填写' }}</td>
                          <td>
                            <span class="term-qa-dialog__status" :class="{ 'is-ignored': item.ignored }">
                              {{ item.ignored ? '已忽略' : '待处理' }}
                            </span>
                            <small v-if="item.ignored_at" class="term-qa-dialog__ignored-meta">
                              {{ item.ignored_by_name || item.ignored_by_id || '' }}
                            </small>
                          </td>
                          <td>
                            <button
                              class="button button--ghost term-qa-dialog__inline-action"
                              type="button"
                              :disabled="updatingTermQAIgnore"
                              @click.stop="void setSingleTermQAReportItemIgnored(item, !item.ignored)"
                            >
                              {{ item.ignored ? '恢复' : '忽略' }}
                            </button>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                    <p v-if="termQAReport.items.length > 50" class="hint-text">
                      已显示前 50 条，完整报告请导出 XLSX。
                    </p>
                  </div>
                </template>

                <div v-else class="empty-state">
                  暂无术语 QA 报告
                  <button
                    class="button"
                    type="button"
                    :disabled="generatingTermQAReport"
                    @click="void generateCurrentFileTermQAReport()"
                  >
                    <Loader2 v-if="generatingTermQAReport" class="lucide-spin" :size="14" />
                    生成报告
                  </button>
                </div>
              </div>
            </section>
          </Transition>

        </div>
      </section>

      <div
        v-if="activeSideTool"
        class="workbench-resizer"
        @mousedown="startResize"
      >
        <div class="workbench-resizer__handle" />
      </div>

      <div
        v-if="activeSideTool"
        ref="sidecarRef"
        class="workbench-sidecar is-preview-open"
        :style="sidecarWidthStyle"
      >
        <div class="workbench-sidecar__panel">
        <Transition name="preview-drawer" mode="out-in">
          <WorkbenchMatchPanel
            v-if="activeSideTool === 'match-info'"
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
            v-else-if="activeSideTool === 'terms'"
            key="terms"
            :term-bases="termBases"
            v-model:selected-term-base-id="selectedTermBaseId"
            :entries="termEntries"
            :active-source-text="activeSegmentSourceText"
            :loading-bases="loadingTermBases"
            :loading-entries="loadingTermEntries"
            :message="termsMessage"
          />
          <section
            v-else-if="activeSideTool === 'resource-search'"
            key="resource-search"
            class="resource-search-panel"
          >
            <div class="resource-search-panel__header">
              <div>
                <div class="section-title section-title--tight">资源库搜索</div>
                <p class="panel-subtitle">{{ boundResourceSummary }}</p>
              </div>
            </div>

            <form class="resource-search-panel__bar" @submit.prevent="void runResourceSearch()">
              <select v-model="resourceSearchMode" class="resource-search-panel__select" aria-label="搜索模式">
                <option value="exact">精确匹配</option>
                <option value="fuzzy">模糊匹配</option>
              </select>
              <input
                v-model="resourceSearchQuery"
                class="resource-search-panel__input"
                type="search"
                placeholder="输入原文或译文关键词"
              />
              <button
                class="button resource-search-panel__button"
                type="submit"
                :disabled="resourceSearchLoading"
                title="搜索"
                aria-label="搜索"
              >
                <Loader2 v-if="resourceSearchLoading" class="lucide-spin" :size="16" />
                <Search v-else :size="16" />
              </button>
            </form>

            <div class="resource-search-panel__message">{{ resourceSearchMessage }}</div>

            <div v-if="resourceSearchItems.length" class="resource-search-panel__table-wrap">
              <table class="resource-search-panel__table">
                <thead>
                  <tr>
                    <th>类型</th>
                    <th>原文</th>
                    <th>译文</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in resourceSearchItems" :key="`${item.type}-${item.id}`">
                    <td>
                      <span
                        class="resource-search-panel__badge"
                        :class="`is-${item.type}`"
                        :title="item.library_name || ''"
                      >
                        {{ item.type === 'tm' ? 'TM' : 'TB' }}
                      </span>
                    </td>
                    <td>{{ item.source_text }}</td>
                    <td>{{ item.target_text }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-else-if="!resourceSearchLoading" class="empty-state resource-search-panel__empty">
              暂无搜索结果
            </div>
          </section>
          <NotesPanel
            v-else-if="activeSideTool === 'notes'"
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
            @close="activeSideTool = null"
            @select-comment="handleCommentFocus"
            @create-comment="handleCreateComment"
            @update-comment="handleUpdateComment"
            @delete-comment="handleDeleteComment"
            @reply-comment="handleReplyComment"
            @cancel-draft="commentStore.setDraftAnchor(null)"
          />
          <ReferencePanel
            v-else-if="activeSideTool === 'reference'"
            key="reference"
            :file-record-id="segmentStore.fileRecord?.id || null"
            @ai-translate-complete="handleReferenceAITranslateComplete"
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
          v-for="tool in sideToolButtons"
          :key="tool.key"
          class="button segment-editor-side-tool"
          :class="[
            `segment-editor-side-tool--${tool.tone}`,
            { 'is-active': activeSideTool === tool.key },
          ]"
          type="button"
          :title="tool.label"
          :aria-label="tool.label"
          :aria-pressed="activeSideTool === tool.key"
          @click="void openSideTool(tool.key)"
        >
          <component :is="tool.icon" :size="15" />
          <span>{{ tool.label }}</span>
        </button>
      </aside>
    </section>

    <ResourceImportDialog
      :open="showImportDialog"
      :initial-tab="importDialogInitialTab"
      :context-label="t('workbench.importContext', { name: segmentStore.fileRecord?.filename || t('workbench.currentTask') })"
      :source-language="segmentStore.fileRecord?.source_language || null"
      :target-language="segmentStore.fileRecord?.target_language || null"
      :default-tm-collection-id="segmentStore.fileRecord?.collection_id || ''"
      :default-term-base-id="segmentStore.fileRecord?.term_base_id || ''"
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
      :open="showWorkflowTransitionDialog"
      :title="workflowTransitionDirection === 'forward' ? '前进句段' : '退回句段'"
      width="min(720px, calc(100vw - 32px))"
      :close-on-overlay="!workflowTransitionLoading"
      :close-on-esc="!workflowTransitionLoading"
      @close="showWorkflowTransitionDialog = false"
    >
      <div class="workflow-transition-dialog">
        <div class="workflow-transition-grid">
          <label class="field">
            <span class="field__label">句段范围</span>
            <input v-model.number="workflowTransitionForm.range_start" class="field__control" type="number" min="1" :disabled="workflowTransitionForm.all_segments" />
          </label>
          <label class="field">
            <span class="field__label">&nbsp;</span>
            <input v-model.number="workflowTransitionForm.range_end" class="field__control" type="number" min="1" :disabled="workflowTransitionForm.all_segments" />
          </label>
          <label class="workflow-transition-check">
            <input v-model="workflowTransitionForm.all_segments" type="checkbox" />
            <span>全部句段</span>
          </label>

          <label class="field">
            <span class="field__label">当前流程</span>
            <select v-model="workflowTransitionForm.from_step_id" class="field__control">
              <option v-for="step in workflowSteps" :key="step.id" :value="step.id">{{ step.name }}</option>
            </select>
          </label>
          <label class="field">
            <span class="field__label">当前状态</span>
            <div class="workflow-source-statuses">
              <label
                v-for="option in workflowSourceStatusOptions"
                :key="option.value"
                class="workflow-source-status"
              >
                <input
                  type="checkbox"
                  :checked="isWorkflowSourceStatusChecked(option.value)"
                  @change="toggleWorkflowSourceStatus(option.value, ($event.target as HTMLInputElement).checked)"
                />
                <span>{{ option.label }}</span>
              </label>
            </div>
          </label>

          <label class="field">
            <span class="field__label">目标流程</span>
            <select v-model="workflowTransitionForm.target_step_id" class="field__control">
              <option v-for="step in workflowTargetSteps" :key="step.id" :value="step.id">{{ step.name }}</option>
            </select>
          </label>
          <label class="field">
            <span class="field__label">目标状态</span>
            <select v-model="workflowTransitionForm.target_status" class="field__control">
              <option value="unconfirmed">未确认</option>
              <option value="confirmed">已确认</option>
            </select>
          </label>
        </div>
        <p class="workflow-transition-count">
          {{ workflowTransitionPreviewLoading ? '正在查询命中句段...' : `已查询出 ${workflowTransitionMatchedCount ?? 0} 个句段` }}
        </p>
      </div>

      <template #footer>
        <button class="button" type="button" :disabled="workflowTransitionLoading" @click="showWorkflowTransitionDialog = false">
          {{ t('common.actions.cancel') }}
        </button>
        <button
          class="button button--primary"
          type="button"
          :disabled="workflowTransitionLoading || !workflowTransitionForm.target_step_id || !workflowTransitionHasSourceStatus || (workflowTransitionMatchedCount ?? 0) === 0"
          @click="void submitWorkflowTransition()"
        >
          <Loader2 v-if="workflowTransitionLoading" class="lucide-spin" :size="14" />
          <ArrowRight v-else-if="workflowTransitionDirection === 'forward'" :size="14" />
          <ArrowLeft v-else :size="14" />
          {{ workflowTransitionDirection === 'forward' ? '前进句段' : '退回句段' }}
        </button>
      </template>
    </Modal>

    <Modal
      :open="showAddTermDialog"
      title="添加术语"
      :description="segmentStore.fileRecord?.filename || ''"
      width="min(680px, calc(100vw - 32px))"
      :close-on-overlay="!addingTerm"
      :close-on-esc="!addingTerm"
      @close="closeAddTermDialog"
    >
      <form class="add-term-dialog" @submit.prevent="void submitAddTermForm()">
        <label class="field">
          <span class="field__label">原文内容</span>
          <textarea
            v-model="addTermSourceText"
            class="field__control add-term-dialog__textarea"
            rows="3"
            :disabled="addingTerm"
            placeholder="请输入原文术语"
          />
        </label>

        <label class="field">
          <span class="field__label">译文内容</span>
          <textarea
            v-model="addTermTargetText"
            class="field__control add-term-dialog__textarea"
            rows="3"
            :disabled="addingTerm"
            placeholder="请输入对应译文"
          />
        </label>

        <label class="field">
          <span class="field__label">目标术语库</span>
          <select
            v-model="addTermTargetBaseId"
            class="field__control"
            :disabled="addingTerm || loadingTermBases"
          >
            <option value="">请选择目标术语库</option>
            <option v-for="termBase in addTermTargetTermBases" :key="termBase.id" :value="termBase.id">
              {{ termBase.name }}（{{ termBase.entry_count }} 条）
            </option>
          </select>
        </label>

        <p class="hint-text">仅显示当前文件已启用且允许写入的术语库。</p>
        <p v-if="addTermFormError" class="form-message is-error">{{ addTermFormError }}</p>

        <div class="add-term-dialog__actions">
          <button class="button button--ghost" type="button" :disabled="addingTerm" @click="closeAddTermDialog">
            {{ t('common.actions.cancel') }}
          </button>
          <button class="button" type="submit" :disabled="!addTermCanSubmit">
            <Loader2 v-if="addingTerm" class="lucide-spin" :size="14" />
            <span>{{ addingTerm ? t('common.actions.saving') : '添加术语' }}</span>
          </button>
        </div>
      </form>
    </Modal>

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
  --workbench-bottom-panel-height: clamp(360px, 50vh, 520px);
  --workbench-visible-bottom-panel-height: min(var(--workbench-bottom-panel-height), calc(100dvh - 70px));
  --workbench-side-tools-width: 48px;
  --workbench-toolbar-left: calc(var(--sidebar-width) + 24px);
  --workbench-drawer-left: calc(var(--sidebar-width) + 16px);
  --workbench-fixed-max: calc(100vw - var(--sidebar-width) - 48px);
  padding-bottom: 20px;
  overflow-x: hidden;
  overflow-x: clip;
  overflow-y: hidden;
}

.workbench-page.is-standalone {
  --workbench-editor-stage-height: clamp(460px, calc(100vh - 300px), 860px);
  --workbench-bottom-panel-height: clamp(380px, 50vh, 560px);
  --workbench-visible-bottom-panel-height: min(var(--workbench-bottom-panel-height), calc(100dvh - 70px));
  --workbench-toolbar-left: 16px;
  --workbench-drawer-left: 16px;
  --workbench-fixed-max: calc(100vw - 32px);
  height: 100dvh;
  min-height: 0;
  padding: 0 10px 8px;
  border-radius: 0;
  box-shadow: none;
}

.workbench-page.is-standalone {
  grid-template-rows: auto minmax(0, 1fr);
}

.workbench-page.is-standalone .workbench-layout,
.workbench-page.is-standalone .panel--editor,
.workbench-page.is-standalone .segment-editor-shell,
.workbench-page.is-standalone .segment-editor-results {
  min-height: 0;
}

.workbench-page.is-standalone .panel--editor {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.workbench-page.is-standalone .segment-editor-shell {
  flex: 1 1 auto;
  grid-template-rows: minmax(0, 1fr) auto;
}

.workbench-page.is-standalone .segment-editor-results {
  grid-template-rows: auto minmax(0, 1fr);
}

.workbench-page.is-standalone .segment-editor-list-stage {
  height: auto;
  min-height: 0;
}

.workbench-page.is-stable-grid {
  gap: 8px;
  background: #f3f6f8;
}

.workbench-page.is-stable-grid .workbench-ribbon {
  box-shadow: 0 1px 0 #cfd8df;
}

.workbench-page.is-stable-grid .workbench-layout {
  gap: 8px;
  align-items: stretch;
}

.workbench-page.is-stable-grid .workbench-layout.has-active-tool {
  grid-template-columns: minmax(0, 1fr) auto auto var(--workbench-side-tools-width);
}

.workbench-page.is-stable-grid .panel--editor {
  border-radius: 0;
  border-color: #d4dde5;
}

.workbench-page.is-stable-grid .segment-editor-results {
  --segment-editor-grid-template: 64px minmax(320px, 1fr) minmax(360px, 1fr) 78px 64px;
  --virtual-list-inline-end-gap: 0;
}

.workbench-page.is-stable-grid .segment-table-head {
  min-height: 32px;
  margin-top: 0;
  border-color: #cfd8df;
  border-radius: 0;
  background: #edf2f5;
  color: #223843;
  font-size: 13.5px;
}

.workbench-page.is-stable-grid .segment-editor-list-stage {
  border-right: 1px solid #cfd8df;
  border-bottom: 1px solid #cfd8df;
  border-left: 1px solid #cfd8df;
  background: #ffffff;
}

.workbench-page.is-stable-grid .segment-editor-list-stage > .virtual-list {
  background: #ffffff;
}

.workbench-page.is-stable-grid .workbench-sidecar,
.workbench-page.is-stable-grid .workbench-resizer__handle,
.workbench-page.is-stable-grid .segment-editor-side-tool,
.workbench-page.is-stable-grid .workbench-rail__button,
.workbench-page.is-stable-grid .workbench-rail__button svg,
.workbench-page.is-stable-grid .workbench-panel-pop-enter-active,
.workbench-page.is-stable-grid .workbench-panel-pop-leave-active {
  transition: none;
}

.workbench-page.is-stable-grid .workbench-rail__button:hover,
.workbench-page.is-stable-grid .workbench-rail__button.is-active {
  transform: none;
}

.workbench-page.is-stable-grid .workbench-panel-pop-enter-from,
.workbench-page.is-stable-grid .workbench-panel-pop-leave-to,
.workbench-page.is-stable-grid .workbench-panel-pop-enter-to,
.workbench-page.is-stable-grid .workbench-panel-pop-leave-from {
  filter: none;
  transform: none;
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

.tool-button.active {
  border-color: #b8cbd4;
  background: linear-gradient(180deg, #f8fbfc 0%, #edf5f7 100%);
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

.add-term-dialog,
.save-to-tm-dialog {
  display: grid;
  gap: 12px;
}

.add-term-dialog__textarea {
  min-height: 96px;
  resize: vertical;
}

.add-term-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
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

.term-qa-dialog {
  display: grid;
  gap: 12px;
}

.term-qa-dialog__summary,
.term-qa-dialog__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.term-qa-dialog__summary {
  color: var(--text-muted, #64748b);
  font-size: 13px;
}

.term-qa-dialog__actions {
  justify-content: flex-end;
}

.term-qa-dialog__table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border-color, #dbe3ea);
  border-radius: 8px;
}

.term-qa-dialog__table {
  width: 100%;
  min-width: 920px;
  border-collapse: collapse;
  font-size: 13px;
}

.term-qa-dialog__table th,
.term-qa-dialog__table td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--border-color, #dbe3ea);
  text-align: left;
  vertical-align: top;
}

.term-qa-dialog__table th {
  color: var(--text-muted, #64748b);
  background: var(--surface-muted, #f8fafc);
  font-weight: 600;
}

.term-qa-dialog__table tr:last-child td {
  border-bottom: 0;
}

.term-qa-dialog__row {
  cursor: pointer;
}

.term-qa-dialog__row:hover,
.term-qa-dialog__row:focus-visible,
.term-qa-dialog__row.is-locating {
  background: #f4faf9;
  outline: none;
}

.term-qa-dialog__row.is-ignored {
  color: #73828a;
  background: #f8faf9;
}

.term-qa-dialog__segment {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #195d6a;
  font-weight: 700;
}

.term-qa-dialog__status {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 7px;
  border-radius: 999px;
  background: #fff4e5;
  color: #8a4b10;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.term-qa-dialog__status.is-ignored {
  background: #edf2f3;
  color: #64747c;
}

.term-qa-dialog__ignored-meta {
  display: block;
  margin-top: 4px;
  color: #7c8b92;
  font-size: 11px;
}

.term-qa-dialog__inline-action {
  min-height: 28px;
  padding: 4px 8px;
  font-size: 12px;
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

.workbench-search-panel__close {
  min-width: 64px;
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
  flex: 0 0 auto;
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

.workbench-search-panel__field > span:not(.workbench-search-panel__input-wrap) {
  flex: 0 0 auto;
  color: #2f3d45;
  font-size: 13px;
  line-height: 1;
  white-space: nowrap;
}

.workbench-search-panel__input-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
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

.workbench-search-panel__field .field__control.has-clear-action {
  padding-right: 28px;
}

.workbench-search-panel__input-clear {
  position: absolute;
  right: 5px;
  top: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: #657580;
  cursor: pointer;
  transform: translateY(-50%);
}

.workbench-search-panel__input-clear:hover,
.workbench-search-panel__input-clear:focus-visible {
  background: #e8eef2;
  color: #26343d;
  outline: none;
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

.workflow-transition-dialog {
  display: grid;
  gap: 14px;
}

.workflow-transition-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr)) auto;
  align-items: end;
  gap: 12px;
}

.workflow-transition-check {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  color: var(--text-secondary);
  font-size: 13px;
}

.workflow-source-statuses {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  min-height: 38px;
  padding: 7px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-panel);
}

.workflow-source-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.workflow-transition-count {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
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
  --segment-editor-grid-template: 72px minmax(0, 1fr) minmax(0, 1fr) 76px 76px;
  --segment-editor-scrollbar-gutter: 10px;
  --virtual-list-inline-end-gap: 4px;
  display: grid;
  grid-template-rows: auto minmax(390px, var(--workbench-editor-stage-height));
  min-height: 0;
}

.workbench-layout {
  grid-template-columns: minmax(0, 1fr) var(--workbench-side-tools-width);
}

.workbench-layout.has-active-tool {
  grid-template-columns: minmax(0, 1fr) auto auto var(--workbench-side-tools-width);
}

.segment-editor-side-tools {
  grid-column: 2;
  grid-row: 1;
  position: sticky;
  top: 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: var(--workbench-side-tools-width);
  max-height: calc(100vh - 140px);
  overflow-y: auto;
  padding: 4px;
  border-left: 1px solid #d9e4e8;
  background: #f8fbfb;
  scrollbar-width: thin;
  scrollbar-color: #adc7ca transparent;
}

.segment-editor-side-tools::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

.workbench-layout.has-active-tool .segment-editor-side-tools {
  grid-column: 4;
}

.segment-editor-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 44px;
  min-width: 0;
  padding: 6px 0 0;
  background: transparent;
}

.segment-editor-footer :deep(.pagination) {
  flex: 1 1 auto;
  min-width: 0;
  padding: 0;
}

.segment-editor-side-tool {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 0;
  width: 38px;
  min-height: 38px;
  height: 38px;
  padding: 0;
  border: 1px solid #bfd5d8;
  border-radius: 6px;
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
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  clip-path: inset(50%);
  white-space: nowrap;
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

.segment-editor-side-tool--search svg {
  color: #0d7bdc;
}

.resource-search-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
  height: 100%;
  padding: 14px;
  background: #f8fbfb;
}

.resource-search-panel__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.resource-search-panel__bar {
  display: grid;
  grid-template-columns: 118px minmax(0, 1fr) 44px;
  align-items: center;
  overflow: hidden;
  border: 1px solid #c9d7dd;
  border-radius: 4px;
  background: #fff;
}

.resource-search-panel__select,
.resource-search-panel__input {
  min-width: 0;
  height: 34px;
  border: 0;
  background: transparent;
  color: #233943;
  font-size: 13px;
  line-height: 1.2;
  outline: none;
}

.resource-search-panel__select {
  padding: 0 8px;
  border-right: 1px solid #dbe5ea;
}

.resource-search-panel__input {
  padding: 0 10px;
  border-right: 1px solid #dbe5ea;
}

.resource-search-panel__button {
  width: 44px;
  height: 34px;
  min-height: 34px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: #fff;
  color: #1f6fbc;
}

.resource-search-panel__button:hover,
.resource-search-panel__button:focus-visible {
  background: #eef6ff;
}

.resource-search-panel__message {
  min-height: 20px;
  color: #647780;
  font-size: 12px;
  line-height: 1.5;
}

.resource-search-panel__table-wrap {
  min-height: 0;
  overflow: auto;
  border: 1px solid #d6e1e5;
  background: #fff;
}

.resource-search-panel__table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 13px;
  color: #21343d;
}

.resource-search-panel__table th,
.resource-search-panel__table td {
  padding: 6px 8px;
  border-bottom: 1px solid #dce7ec;
  text-align: left;
  vertical-align: top;
}

.resource-search-panel__table th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #eef5f7;
  color: #49616b;
  font-weight: 600;
}

.resource-search-panel__table th:first-child,
.resource-search-panel__table td:first-child {
  width: 58px;
  text-align: center;
}

.resource-search-panel__table tbody tr:nth-child(even) {
  background: #edf6ff;
}

.resource-search-panel__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  min-height: 24px;
  border-radius: 2px;
  background: #987bdc;
  color: #fff;
  font-weight: 700;
  line-height: 1;
}

.resource-search-panel__badge.is-tm {
  background: #5b8edc;
}

.resource-search-panel__empty {
  margin-top: 16px;
}

.segment-editor-bottom-tools {
  position: static;
  order: -1;
  flex: 0 0 auto;
  right: auto;
  bottom: auto;
  left: auto;
  z-index: 1710;
  display: flex;
  justify-content: flex-start;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  min-width: 0;
  width: max-content;
  max-width: min(540px, 48vw);
  overflow-x: auto;
  overflow-y: hidden;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  pointer-events: none;
  scrollbar-width: none;
}

.segment-editor-bottom-tools::-webkit-scrollbar {
  display: none;
}

.segment-editor-bottom-tools.is-docked {
  position: static;
  right: auto;
  bottom: auto;
  left: auto;
  z-index: 1710;
  width: max-content;
  max-width: min(540px, 48vw);
  min-height: 31px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.segment-editor-bottom-tool {
  --bottom-tool-bg: linear-gradient(180deg, #f7fbff, #e6f0f7);
  --bottom-tool-border: #afc1cc;
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  gap: 5px;
  height: 32px;
  min-width: 100px;
  padding: 0 13px;
  border: 1px solid var(--bottom-tool-border);
  border-radius: 4px;
  background: var(--bottom-tool-bg);
  color: #334852;
  font-size: 13px;
  font-weight: 500;
  line-height: 1;
  letter-spacing: 0;
  white-space: nowrap;
  box-shadow: 0 8px 18px rgba(24, 48, 58, 0.12);
  pointer-events: auto;
}

.segment-editor-bottom-tool:first-child {
  border-left: 1px solid var(--bottom-tool-border);
  border-radius: 4px;
}

.segment-editor-bottom-tool:last-child {
  border-radius: 4px;
}

.segment-editor-bottom-tool svg {
  flex: 0 0 auto;
  color: #697b85;
}

.segment-editor-bottom-tool span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.segment-editor-bottom-tool:hover:not(:disabled),
.segment-editor-bottom-tool:focus-visible {
  z-index: 1;
  border-color: #72acd7;
  background: #ffffff;
  color: #0070c0;
  outline: none;
}

.segment-editor-bottom-tool.is-active {
  z-index: 2;
  border-color: #8abfe8;
  background: #ffffff;
  color: #0070c0;
  box-shadow: 0 8px 18px rgba(24, 48, 58, 0.14), inset 0 -2px 0 #1e9bff;
}

.segment-editor-bottom-tool.is-active svg {
  color: #0070c0;
}

.segment-editor-bottom-tool.is-loading {
  color: #0070c0;
}

.segment-editor-bottom-tool.is-loading svg {
  color: #0070c0;
}

.segment-editor-bottom-tool:disabled {
  cursor: wait;
  opacity: 0.66;
}

.segment-editor-bottom-tool--qa {
  --bottom-tool-bg: linear-gradient(180deg, #eef9f3, #d7eadf);
  --bottom-tool-border: #9bc7ad;
  padding-right: 26px;
}

.segment-editor-bottom-tool--qa svg {
  color: #23805f;
}

.segment-editor-bottom-tool--history {
  --bottom-tool-bg: linear-gradient(180deg, #f5f1fb, #e6ddf2);
  --bottom-tool-border: #b9abd0;
}

.segment-editor-bottom-tool--history svg {
  color: #6e5c91;
}

.segment-editor-bottom-tool--paper {
  --bottom-tool-bg: linear-gradient(180deg, #eef8fb, #dcecf1);
  --bottom-tool-border: #a5c4ce;
}

.segment-editor-bottom-tool--paper svg {
  color: #376f7e;
}

.segment-editor-bottom-tool--success {
  --bottom-tool-bg: linear-gradient(180deg, #effaf5, #d9efe5);
  --bottom-tool-border: #9fcdb8;
}

.segment-editor-bottom-tool--success svg {
  color: #1d7b61;
}

.segment-editor-bottom-tool--layout {
  --bottom-tool-bg: linear-gradient(180deg, #eef6ff, #dce9f7);
  --bottom-tool-border: #a7bdd7;
}

.segment-editor-bottom-tool--layout svg {
  color: #346fa6;
}

.segment-editor-bottom-tool__badge {
  position: absolute;
  top: 2px;
  right: 5px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 999px;
  background: #fff4e5;
  color: #9b4d07;
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
}

.segment-editor-bottom-tool__badge.is-clean {
  background: #e6f6ef;
  color: #15795d;
}

.workbench-bottom-drawer {
  position: fixed;
  right: 16px;
  bottom: 50px;
  left: var(--workbench-drawer-left);
  z-index: 1700;
  width: auto;
  height: var(--workbench-visible-bottom-panel-height);
  min-height: min(260px, var(--workbench-visible-bottom-panel-height));
  max-height: var(--workbench-visible-bottom-panel-height);
  overflow: hidden;
  border: 1px solid #cbd8de;
  border-radius: 6px;
  background: #ffffff;
  box-shadow: 0 18px 40px rgba(24, 48, 58, 0.18);
}

.workbench-bottom-drawer.is-resizable {
  padding-top: 8px;
  border-top-color: #9eb9c4;
}

.workbench-bottom-drawer.is-resizing {
  border-top-color: #0070c0;
}

.workbench-bottom-drawer__resize-handle {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  z-index: 10;
  height: 12px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: ns-resize;
  touch-action: none;
}

.workbench-bottom-drawer__resize-handle::before {
  content: "";
  position: absolute;
  top: 3px;
  left: 50%;
  width: 74px;
  height: 4px;
  transform: translateX(-50%);
  border-radius: 999px;
  background: #a9bec7;
  opacity: 0.85;
  transition:
    background-color 140ms ease,
    opacity 140ms ease;
}

.workbench-bottom-drawer__resize-handle:hover::before,
.workbench-bottom-drawer__resize-handle:focus-visible::before,
.workbench-bottom-drawer.is-resizing .workbench-bottom-drawer__resize-handle::before {
  background: #0070c0;
  opacity: 1;
}

.workbench-bottom-drawer__resize-handle:focus-visible {
  outline: 2px solid rgba(0, 112, 192, 0.28);
  outline-offset: -2px;
}

.workbench-bottom-drawer.is-wide {
  height: var(--workbench-visible-bottom-panel-height);
}

.workbench-bottom-drawer.is-loading {
  background: #f6fafb;
}

.workbench-bottom-drawer__loading {
  position: absolute;
  inset: 0;
  z-index: 8;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    linear-gradient(180deg, rgba(248, 252, 253, 0.96), rgba(238, 247, 248, 0.94));
  color: #244851;
}

.workbench-bottom-drawer__loading-body {
  display: inline-flex;
  align-items: center;
  gap: 14px;
  max-width: min(420px, 92%);
  padding: 16px 20px;
  border: 1px solid #b7d3db;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 12px 28px rgba(36, 72, 81, 0.14);
}

.workbench-bottom-drawer__loading-body svg {
  flex: 0 0 auto;
  color: #0070c0;
}

.workbench-bottom-drawer__loading-body div {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.workbench-bottom-drawer__loading-body strong,
.workbench-bottom-drawer__loading-body span {
  display: block;
  min-width: 0;
}

.workbench-bottom-drawer__loading-body strong {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.25;
}

.workbench-bottom-drawer__loading-body span {
  color: #5a737b;
  font-size: 13px;
  line-height: 1.35;
}

.workbench-bottom-drawer__close {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 3;
  display: inline-grid;
  place-items: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid #cdd9de;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.92);
  color: #40515a;
  box-shadow: none;
}

.workbench-bottom-drawer__close:hover,
.workbench-bottom-drawer__close:focus-visible {
  border-color: #95c4e8;
  background: #fff;
  color: #0070c0;
  outline: none;
}

.workbench-bottom-drawer__preview,
.workbench-bottom-drawer__history,
.workbench-bottom-drawer__qa {
  height: 100%;
  min-height: 0;
}

.workbench-bottom-drawer__history {
  width: 100%;
  max-width: none;
  overflow-y: auto;
  padding: 10px;
}

.workbench-bottom-drawer__qa {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: 10px;
  overflow: hidden;
  padding: 12px;
}

.workbench-bottom-drawer__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.workbench-bottom-drawer__header > div {
  min-width: 0;
}

.workbench-bottom-drawer__header .panel-subtitle {
  max-width: min(760px, 70vw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workbench-bottom-drawer__qa > .empty-state {
  min-height: 0;
}

.workbench-bottom-drawer__qa .term-qa-dialog__table-wrap {
  min-height: 0;
  overflow: auto;
}

.workbench-bottom-drawer__qa .term-qa-dialog__table {
  min-width: 980px;
}

.workbench-bottom-drawer :deep(.preview-panel),
.workbench-bottom-drawer :deep(.split-preview) {
  height: 100%;
  min-height: 0;
  border: 0;
  border-radius: 0;
  gap: 8px;
  padding: 8px 12px 12px;
  box-shadow: none;
}

.workbench-bottom-drawer :deep(.preview-panel__header),
.workbench-bottom-drawer :deep(.split-preview__header) {
  align-items: center;
  min-height: 32px;
  gap: 8px;
}

.workbench-bottom-drawer :deep(.preview-panel__header > div:first-child),
.workbench-bottom-drawer :deep(.split-preview__header > div:first-child) {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 10px;
  max-width: min(560px, 48vw);
}

.workbench-bottom-drawer :deep(.preview-panel__header .section-title),
.workbench-bottom-drawer :deep(.split-preview__header .section-title) {
  line-height: 1.2;
  white-space: nowrap;
}

.workbench-bottom-drawer :deep(.preview-panel__meta) {
  margin: 0;
  font-size: 11px;
  line-height: 1.2;
}

.workbench-bottom-drawer :deep(.split-preview__header .panel-subtitle) {
  display: none;
}

.workbench-bottom-drawer :deep(.preview-panel__zoom-button) {
  width: 26px;
  height: 26px;
  min-height: 26px;
}

.workbench-bottom-drawer :deep(.preview-panel__zoom-value) {
  min-width: 42px;
  height: 26px;
}

.workbench-bottom-drawer :deep(.preview-panel__close) {
  min-height: 30px;
  padding: 4px 10px;
}

.workbench-bottom-drawer :deep(.preview-panel--drawer) {
  position: relative;
  display: block;
  gap: 0;
  overflow: hidden;
}

.workbench-bottom-drawer :deep(.preview-panel--drawer .preview-panel__header) {
  position: absolute;
  top: 8px;
  right: 12px;
  left: 12px;
  z-index: 4;
  pointer-events: none;
}

.workbench-bottom-drawer :deep(.preview-panel--drawer .preview-panel__header > *) {
  pointer-events: auto;
}

.workbench-bottom-drawer :deep(.preview-panel--drawer .preview-panel__viewport) {
  height: 100%;
}

.workbench-bottom-drawer :deep(.preview-panel__viewport) {
  height: auto;
  min-height: 0;
}

.workbench-bottom-drawer :deep(.preview-panel__paper) {
  width: min(100%, 980px);
  max-width: 100%;
  height: 100%;
}

.workbench-bottom-drawer :deep(.split-preview__layout) {
  flex: 1;
  height: auto;
  min-height: 0;
}

.workbench-bottom-drawer-enter-active,
.workbench-bottom-drawer-leave-active {
  transition:
    opacity 180ms ease,
    transform 220ms cubic-bezier(0.2, 0.8, 0.2, 1);
}

.workbench-bottom-drawer-enter-from,
.workbench-bottom-drawer-leave-to {
  opacity: 0;
  transform: translateY(-8px);
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
    padding: 4px 0;
    border-left: 0;
    border-bottom: 1px solid #d9e4e8;
  }

  .segment-editor-side-tool {
    flex: 0 0 40px;
    width: 40px;
    height: 38px;
    min-height: 38px;
    flex-direction: row;
    justify-content: center;
    gap: 0;
    padding: 0;
  }

  .segment-editor-side-tool span {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    clip-path: inset(50%);
    white-space: nowrap;
  }

  .segment-editor-results {
    grid-template-rows: auto 420px;
  }

  .segment-editor-list-stage {
    height: 420px;
    min-height: 0;
  }
}

@media (max-width: 980px) {
  .workbench-page {
    --workbench-toolbar-left: 12px;
    --workbench-drawer-left: 12px;
    --workbench-fixed-max: calc(100vw - 24px);
  }

  .segment-editor-footer {
    flex-wrap: wrap;
  }

  .segment-editor-footer :deep(.pagination) {
    flex-basis: 100%;
  }

  .segment-editor-bottom-tools {
    right: auto;
    max-width: 100%;
  }

  .segment-editor-bottom-tools.is-docked {
    right: auto;
    max-width: 100%;
  }

  .workbench-bottom-drawer {
    right: 12px;
    left: var(--workbench-drawer-left);
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

  .segment-editor-bottom-tool {
    min-width: 88px;
    height: 34px;
    padding-inline: 10px;
    font-size: 12px;
  }

  .segment-editor-bottom-tool--qa {
    padding-right: 24px;
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

/* 确认句段下拉菜单 */
.confirm-segment-menu {
  position: relative;
  display: flex;
  flex-direction: row;
  align-items: stretch;
}

.confirm-segment-menu__main {
  border-radius: 4px 0 0 4px !important;
  border-right: none !important;
}

.confirm-segment-menu__main:hover:not(:disabled),
.confirm-segment-menu__main:focus-visible {
  border-right: none !important;
}

.confirm-segment-menu__toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 0 4px 4px 0;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.confirm-segment-menu:hover .confirm-segment-menu__main:not(:disabled) {
  border-color: #b8cbd4;
  border-right: none !important;
  background: linear-gradient(180deg, #f8fbfc 0%, #edf5f7 100%);
}

.confirm-segment-menu:hover .confirm-segment-menu__toggle:not(:disabled) {
  border-color: #b8cbd4;
  border-left: 1px solid #b8cbd4;
  background: linear-gradient(180deg, #f8fbfc 0%, #edf5f7 100%);
}

.confirm-segment-menu__toggle:hover:not(:disabled) {
  background: linear-gradient(180deg, #eef4f6 0%, #e3eef1 100%) !important;
}

.confirm-segment-menu__toggle:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.confirm-segment-menu__dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 100;
  min-width: 140px;
  padding: 4px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}

.confirm-segment-menu__dropdown button {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 8px 12px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.confirm-segment-menu__dropdown button:hover {
  background: rgba(13, 122, 104, 0.08);
}

/* 大小写转换下拉菜单 */
.case-menu {
  position: relative;
  display: inline-flex;
}

.case-menu__dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 100;
  display: flex;
  flex-direction: column;
  min-width: 120px;
  padding: 4px 0;
  border: 1px solid #d0d7dc;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}

.case-menu__item {
  display: block;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: #43545c;
  font-size: 13px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.case-menu__item:hover {
  background: rgba(13, 122, 104, 0.08);
}

/* 清除格式下拉菜单 */
.clear-format-menu {
  position: relative;
  display: flex;
  flex-direction: row;
  align-items: stretch;
}

.clear-format-menu__main {
  border-radius: 4px 0 0 4px !important;
  border-right: none !important;
}

.clear-format-menu__main:hover:not(:disabled),
.clear-format-menu__main:focus-visible {
  border-right: none !important;
}

.clear-format-menu__toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 0 4px 4px 0;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.clear-format-menu:hover .clear-format-menu__main:not(:disabled) {
  border-color: #b8cbd4;
  border-right: none !important;
  background: linear-gradient(180deg, #f8fbfc 0%, #edf5f7 100%);
}

.clear-format-menu:hover .clear-format-menu__toggle:not(:disabled) {
  border-color: #b8cbd4;
  border-left: 1px solid #b8cbd4;
  background: linear-gradient(180deg, #f8fbfc 0%, #edf5f7 100%);
}

.clear-format-menu__toggle:hover:not(:disabled) {
  background: linear-gradient(180deg, #eef4f6 0%, #e3eef1 100%) !important;
}

.clear-format-menu__toggle:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.clear-format-menu__dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 100;
  min-width: 140px;
  padding: 4px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}

.clear-format-menu__item {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 8px 12px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.clear-format-menu__item:hover {
  background: rgba(13, 122, 104, 0.08);
}

/* 格式按钮激活状态（与 hover 一致） */
.tool-button.is-active {
  border-color: #b8cbd4;
  background: linear-gradient(180deg, #f8fbfc 0%, #edf5f7 100%);
}

/* 特殊字符下拉菜单 */
.special-char-menu {
  position: relative;
  display: inline-flex;
}

.special-char-menu__dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 200;
  padding: 12px;
  border: 1px solid #d0d7dc;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
  min-width: 320px;
}

.special-char-menu__title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #43545c;
}

.special-char-menu__grid {
  display: grid;
  grid-template-columns: repeat(10, 1fr);
  gap: 2px;
}

.special-char-menu__char {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid #e8edf0;
  border-radius: 4px;
  background: #fff;
  color: #2c3e50;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  transition: background-color 0.12s ease, border-color 0.12s ease;
}

.special-char-menu__char:hover {
  background: rgba(13, 122, 104, 0.08);
  border-color: #0d7a68;
  color: #0d7a68;
}

.special-char-menu__char:active {
  background: rgba(13, 122, 104, 0.16);
}

.special-char-menu__recent {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #e8edf0;
}

.special-char-menu__recent-row {
  display: flex;
  gap: 2px;
  flex-wrap: wrap;
}

/* 导出下拉菜单 */
.export-dropdown {
  position: relative;
}

.export-dropdown .workbench-ribbon__top-action {
  display: flex;
  align-items: center;
  gap: 4px;
}

.export-dropdown__menu {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 100;
  min-width: 240px;
  padding: 4px;
  border: 1px solid #e0e4e7;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}

.export-dropdown__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px;
  color: #6b7c85;
  font-size: 13px;
}

.export-dropdown__item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 4px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.12s ease;
}

.export-dropdown__item:hover {
  background: rgba(13, 122, 104, 0.08);
}

.export-dropdown__item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.export-dropdown__item-name {
  font-size: 13px;
  font-weight: 500;
  color: #2c3e50;
}

.export-dropdown__item-desc {
  font-size: 11px;
  color: #6b7c85;
  margin-top: 2px;
}

.export-dropdown--toolbar {
  display: inline-block;
}

.export-dropdown--toolbar .workbench-action--export {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.export-dropdown--toolbar .export-dropdown__menu {
  left: 0;
  right: auto;
}
</style>

