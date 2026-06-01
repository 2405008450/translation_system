<script setup lang="ts">
import axios from 'axios'
import {
  ArrowLeft,
  ArrowDown,
  ArrowUp,
  Bot,
  ChevronDown,
  ChevronUp,
  CircleHelp,
  Columns,
  Download,
  FileCheck,
  FileText,
  Flag,
  History,
  Info,
  Languages,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Save,
  Search,
  Upload,
  X,
} from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import Modal from '../components/base/Modal.vue'
import IssueMarkerDialog from '../components/IssueMarkerDialog.vue'
import NotesPanel from '../components/NotesPanel.vue'
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
import { usePageHeader } from '../composables/usePageHeader'
import { useToast } from '../composables/useToast'
import { useWorkbenchShortcuts } from '../composables/useWorkbenchShortcuts'
import { getTaskExportFormatLabel } from '../constants/taskFiles'
import { llmProviderOptions, llmScopeOptions } from '../constants/llm'
import { formatLanguagePair } from '../constants/languages'
import { isProgressComplete } from '../utils/progress'
import { useAuthStore } from '../stores/auth'
import { useCommentStore } from '../stores/comment'
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
}>()

type ToolKey = 'source-preview' | 'target-preview' | 'split-preview' | 'match-info' | 'terms' | 'notes' | 'history'
type ResourceImportTab = 'tm' | 'term'
type SaveToTMScope = 'translated' | 'confirmed'
type SaveToTMTargetMode = 'new' | 'existing'
type SegmentDisplayScope = 'all' | 'fuzzy_only' | 'none_only' | 'empty_target'
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
const { t } = useI18n()

const virtualListRef = ref<{
  scrollToIndex: (index: number, align?: ScrollLogicalPosition) => Promise<boolean>
  focusIndex: (index: number, selector?: string, align?: ScrollLogicalPosition) => Promise<boolean>
} | null>(null)

const pageError = ref('')
const llmScope = ref<LLMTranslateScope>('all')
const llmProvider = ref<LLMProvider>('deepseek')
const itemHeight = ref(resolveItemHeight())
const activeTool = ref<ToolKey | null>(null)
const showImportDialog = ref(false)
const showIssueDialog = ref(false)
const importDialogInitialTab = ref<ResourceImportTab>('tm')
const showShortcutHelp = ref(false)
const showSaveToTMDialog = ref(false)
const openRevisionMenu = ref(false)
const revisionActionLoading = ref(false)
const segmentSearchOpen = ref(false)
const sourceSearchInputRef = ref<HTMLInputElement | null>(null)
const guidelinesEditorRef = ref<HTMLTextAreaElement | null>(null)
const segmentDisplayScope = ref<SegmentDisplayScope>('all')
const sourceSearchQuery = ref('')
const targetSearchQuery = ref('')
const searchLoadingAllSegments = ref(false)
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
const termsMessage = ref(t('workbench.terms.defaultMessage'))
let searchLoadRequestId = 0

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

const projectReturnId = computed(() => (
  typeof route.query.pid === 'string' ? route.query.pid : ''
))

const projectReturnParent = computed(() => (
  route.query.parent === 'tasks' ? 'tasks' : ''
))

const statusSummary = computed(() => {
  const counters = {
    exact: 0,
    fuzzy: 0,
    none: 0,
    confirmed: 0,
  }

  for (const segment of segmentStore.segments) {
    if (segment.status in counters) {
      counters[segment.status as keyof typeof counters] += 1
    }
  }

  return [
    { key: 'exact', label: t('workbench.statusSummary.exact'), value: counters.exact, tone: 'exact' },
    { key: 'fuzzy', label: t('workbench.statusSummary.fuzzy'), value: counters.fuzzy, tone: 'fuzzy' },
    { key: 'none', label: t('workbench.statusSummary.none'), value: counters.none, tone: 'none' },
    { key: 'confirmed', label: t('workbench.statusSummary.confirmed'), value: counters.confirmed, tone: 'confirmed' },
  ]
})

const currentLanguagePair = computed(() => (
  formatLanguagePair(
    segmentStore.fileRecord?.source_language ?? null,
    segmentStore.fileRecord?.target_language ?? null,
  )
))

const activeSegment = computed(() => (
  segmentStore.segments.find((segment) => segment.sentence_id === segmentStore.activeSentenceId) ?? null
))

const activeSegmentHistory = computed(() => (
  segmentStore.activeSentenceId
    ? segmentStore.revisionHistory[segmentStore.activeSentenceId] || []
    : []
))

const revisionSentenceIds = computed(() => (
  Object.entries(segmentStore.revisionHistory)
    .filter(([, entries]) => entries.some((e) => e.status === 'pending'))
    .map(([sentenceId]) => sentenceId)
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

function buildSourceSearchableText(segment: Segment) {
  const displayText = segment.display_text || ''
  if (displayText && displayText !== segment.source_text) {
    return `${displayText}\n${segment.source_text}`
  }
  return displayText || segment.source_text
}

function matchesSearchKeyword(value: string, keyword: string) {
  if (!keyword) {
    return true
  }
  return normalizeSearchText(value).includes(keyword)
}

const normalizedSourceSearchQuery = computed(() => normalizeSearchText(sourceSearchQuery.value))
const normalizedTargetSearchQuery = computed(() => normalizeSearchText(targetSearchQuery.value))

const hasSegmentSearch = computed(() => (
  Boolean(normalizedSourceSearchQuery.value || normalizedTargetSearchQuery.value)
))

const hasSegmentDisplayScope = computed(() => segmentDisplayScope.value !== 'all')
const hasEditorSegmentFilter = computed(() => hasSegmentSearch.value || hasSegmentDisplayScope.value)

const segmentDisplayScopeOptions = computed<Array<{ value: SegmentDisplayScope; label: string }>>(() => [
  { value: 'all', label: t('workbench.search.scopes.all') },
  { value: 'fuzzy_only', label: t('workbench.search.scopes.fuzzyOnly') },
  { value: 'none_only', label: t('workbench.search.scopes.noneOnly') },
  { value: 'empty_target', label: t('workbench.search.scopes.emptyTarget') },
])

function matchesSegmentDisplayScope(segment: Segment) {
  if (segmentDisplayScope.value === 'fuzzy_only') {
    return segment.status === 'fuzzy'
  }
  if (segmentDisplayScope.value === 'none_only') {
    return segment.status === 'none'
  }
  if (segmentDisplayScope.value === 'empty_target') {
    return !normalizeTextForSaveToTM(segment.target_text)
  }
  return true
}

const editorSegments = computed(() => {
  const sourceKeyword = normalizedSourceSearchQuery.value
  const targetKeyword = normalizedTargetSearchQuery.value

  if (!sourceKeyword && !targetKeyword && !hasSegmentDisplayScope.value) {
    return segmentStore.segments
  }

  return segmentStore.segments.filter((segment) => (
    matchesSegmentDisplayScope(segment)
    &&
    matchesSearchKeyword(buildSourceSearchableText(segment), sourceKeyword)
    && matchesSearchKeyword(segment.target_text || '', targetKeyword)
  ))
})

const activeEditorIndex = computed(() => {
  if (!segmentStore.activeSentenceId) {
    return -1
  }
  return editorSegments.value.findIndex((segment) => segment.sentence_id === segmentStore.activeSentenceId)
})

const segmentOrdinalMap = computed(() => {
  const nextMap = new Map<string, number>()
  segmentStore.segments.forEach((segment, index) => {
    nextMap.set(segment.sentence_id, index)
  })
  return nextMap
})

const targetPreviewRenderMode = computed<'static' | 'target'>(() => {
  if (segmentStore.previewSupported && segmentStore.previewHtml) {
    return 'target'
  }
  return 'static'
})

const targetPreviewHtml = computed(() => {
  if (targetPreviewRenderMode.value === 'target') {
    return segmentStore.previewHtml
  }
  if (!segmentStore.allSegmentsLoaded) {
    return ''
  }
  return buildDocumentPreviewHtml(segmentStore.segments, 'target')
})

const targetPreviewSupported = computed(() => {
  if (targetPreviewRenderMode.value === 'target') {
    return Boolean(segmentStore.previewHtml)
  }
  return segmentStore.allSegmentsLoaded && segmentStore.segments.length > 0
})

const exportButtonLabel = computed(() => t('common.actions.export'))

const exportButtonTitle = computed(() => (
  `${t('common.actions.export')} ${getTaskExportFormatLabel(segmentStore.fileRecord?.filename)}`
))

const saveToTMPreviewStats = computed(() => {
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
  (activeTool.value === 'source-preview' || activeTool.value === 'split-preview')
  && segmentStore.previewLoading,
)

const targetPreviewLoading = computed(() => {
  if (activeTool.value !== 'target-preview' && activeTool.value !== 'split-preview') {
    return false
  }
  if (targetPreviewRenderMode.value === 'target') {
    return segmentStore.previewLoading || segmentStore.loadingAllSegments
  }
  return segmentStore.loadingAllSegments
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
    return
  }

  pageError.value = ''
  activeTool.value = tool

  try {
    if (tool === 'source-preview') {
      await segmentStore.ensurePreviewLoaded()
      return
    }

    if (tool === 'target-preview' || tool === 'split-preview') {
      await Promise.all([
        segmentStore.ensurePreviewLoaded(),
        segmentStore.ensureAllSegmentsLoaded(),
      ])
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
  try {
    if (hasEditorSegmentFilter.value) {
      await ensureFilteredCorpusLoaded()
      return
    }
    await segmentStore.loadMoreSegments()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.loadMore'))
  }
}

function openImportDialog(tab: ResourceImportTab = 'tm') {
  importDialogInitialTab.value = tab
  showImportDialog.value = true
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
  searchLoadingAllSegments.value = false
}

function closeSegmentSearchPanel() {
  segmentSearchOpen.value = false
}

async function toggleSegmentSearchPanel() {
  segmentSearchOpen.value = !segmentSearchOpen.value
  if (segmentSearchOpen.value) {
    await nextTick()
    sourceSearchInputRef.value?.focus({ preventScroll: true })
  }
}

async function ensureFilteredCorpusLoaded() {
  if (!hasEditorSegmentFilter.value || !segmentStore.hasMoreSegments) {
    searchLoadingAllSegments.value = false
    return
  }

  const requestId = ++searchLoadRequestId
  searchLoadingAllSegments.value = true

  try {
    await segmentStore.ensureAllSegmentsLoaded()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.loadAll'))
  } finally {
    if (requestId === searchLoadRequestId) {
      searchLoadingAllSegments.value = false
    }
  }
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

async function focusSentenceByOffset(offset: number) {
  if (hasEditorSegmentFilter.value) {
    await focusMatchedSegment(offset)
    return
  }

  let targetIndex = getCurrentSegmentIndex() + offset
  targetIndex = Math.max(0, targetIndex)

  while (targetIndex >= editorSegments.value.length && segmentStore.hasMoreSegments) {
    const loaded = await segmentStore.loadMoreSegments()
    if (!loaded) {
      break
    }
  }

  targetIndex = Math.min(targetIndex, Math.max(editorSegments.value.length - 1, 0))
  await focusEditorSegmentAtIndex(targetIndex)
}

function getEditorSegmentDisplayIndex(sentenceId: string, fallbackIndex: number) {
  return segmentOrdinalMap.value.get(sentenceId) ?? fallbackIndex
}

function confirmCurrentSentence() {
  if (!activeSegment.value) {
    return
  }
  segmentStore.updateTarget(activeSegment.value.sentence_id, activeSegment.value.target_text || '')
  toast.success(t('workbench.messages.confirmed'))
}

async function handleAcceptRevision(revisionId: string) {
  pageError.value = ''
  revisionActionLoading.value = true
  try {
    await segmentStore.acceptRevision(revisionId)
    toast.success('修订已接受')
  } catch (error) {
    pageError.value = getErrorMessage(error, '接受修订失败')
  } finally {
    revisionActionLoading.value = false
  }
}

async function handleRejectRevision(revisionId: string) {
  pageError.value = ''
  revisionActionLoading.value = true
  try {
    await segmentStore.rejectRevision(revisionId)
    toast.success('修订已拒绝')
  } catch (error) {
    pageError.value = getErrorMessage(error, '拒绝修订失败')
  } finally {
    revisionActionLoading.value = false
  }
}

async function handleBatchAcceptRevisions() {
  pageError.value = ''
  revisionActionLoading.value = true
  try {
    const updatedCount = await segmentStore.batchAcceptRevisions()
    openRevisionMenu.value = false
    toast.success(updatedCount > 0 ? `已接受 ${updatedCount} 条修订` : '没有待处理的修订')
  } catch (error) {
    pageError.value = getErrorMessage(error, '批量接受修订失败')
  } finally {
    revisionActionLoading.value = false
  }
}

async function handleBatchRejectRevisions() {
  pageError.value = ''
  revisionActionLoading.value = true
  try {
    const updatedCount = await segmentStore.batchRejectRevisions()
    openRevisionMenu.value = false
    toast.success(updatedCount > 0 ? `已拒绝 ${updatedCount} 条修订` : '没有待处理的修订')
  } catch (error) {
    pageError.value = getErrorMessage(error, '批量拒绝修订失败')
  } finally {
    revisionActionLoading.value = false
  }
}

async function focusRevisionByOffset(offset: 1 | -1) {
  const ids = revisionSentenceIds.value
  if (!ids.length) {
    return
  }

  const currentIdx = segmentStore.activeSentenceId
    ? ids.indexOf(segmentStore.activeSentenceId)
    : -1
  const nextIdx = currentIdx === -1
    ? (offset > 0 ? 0 : ids.length - 1)
    : (currentIdx + offset + ids.length) % ids.length

  await handlePreviewFocus(ids[nextIdx])
}

function handleApplyTMTarget(sentenceId: string, targetText: string) {
  segmentStore.updateTarget(sentenceId, targetText)
}

async function loadTask() {
  pageError.value = ''
  activeTool.value = null
  openRevisionMenu.value = false
  resetSegmentSearch()
  commentStore.stopPolling()
  termEntries.value = []
  termBases.value = []
  selectedTermBaseId.value = ''

  try {
    await segmentStore.loadTask(props.id)
    if (segmentStore.segments[0] && !segmentStore.activeSentenceId) {
      segmentStore.setActiveSentence(segmentStore.segments[0].sentence_id)
    }

    try {
      await commentStore.loadComments(props.id)
      commentStore.startPolling(props.id)
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

async function runLLMTranslation() {
  pageError.value = ''
  try {
    await segmentStore.startLLMTranslation(llmScope.value, llmProvider.value, {
      guidelineTemplateId: selectedGuidelineTemplateId.value || undefined,
      temporaryPrompt: workbenchGuidelines.value,
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
    await segmentStore.syncToBackend()
    if (segmentStore.hasMoreSegments) {
      await segmentStore.ensureAllSegmentsLoaded()
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
    await segmentStore.syncToBackend()
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

async function loadAllSegments() {
  pageError.value = ''
  try {
    await segmentStore.ensureAllSegmentsLoaded()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.loadAll'))
  }
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

  segmentStore.updateTarget(activeSegment.value.sentence_id, text)
  toast.success(t('matchPanel.textInserted'))
}

function handleAppendText(text: string) {
  if (!activeSegment.value) {
    return
  }

  const nextText = appendToCurrentText(activeSegment.value.target_text || '', text)
  segmentStore.updateTarget(activeSegment.value.sentence_id, nextText)
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
  // 只有当右侧面板未打开时才自动打开记忆匹配面板
  // 如果已有面板打开（如原文预览），则保持当前面板，仅进行定位
  if (!activeTool.value) {
    void ensureMatchInfoPanelOpen()
  }
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

watch([segmentDisplayScope, sourceSearchQuery, targetSearchQuery], async () => {
  if (hasEditorSegmentFilter.value) {
    void ensureFilteredCorpusLoaded()
  } else {
    searchLoadRequestId += 1
    searchLoadingAllSegments.value = false
  }

  await nextTick()
  await virtualListRef.value?.scrollToIndex(0, 'start')
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
  <div class="content-stack content-stack--workbench workbench-page">
    <section class="toolbar-panel workbench-toolbar">
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
          <button class="button workbench-action workbench-action--save" type="button" :disabled="segmentStore.saving" @click="saveNow">
            <Loader2 v-if="segmentStore.saving" class="lucide-spin" :size="14" />
            <Save v-else :size="14" />
            {{ segmentStore.saving ? t('common.actions.saving') : t('workbench.saveNow') }}
          </button>

          <div class="export-dropdown" style="position: relative; display: inline-block;">
            <button
              class="button workbench-action workbench-action--export"
              type="button"
              :disabled="!segmentStore.canExport || exporting"
              :title="exportButtonTitle"
              @click="exportTranslatedFile"
            >
              <Loader2 v-if="exporting" class="lucide-spin" :size="14" />
              <Download v-else :size="14" />
              {{ exportButtonLabel }}
            </button>
            <button
              class="button workbench-action workbench-action--export-toggle"
              type="button"
              :disabled="!segmentStore.canExport || exporting"
              title="更多导出选项"
              aria-label="更多导出选项"
              @click="toggleExportMenu"
              style="padding: 4px 6px; margin-left: -1px; border-left: 1px solid rgba(255,255,255,0.3);"
            >
              <ChevronDown :size="14" />
            </button>
            <div
              v-if="showExportMenu"
              class="export-dropdown__menu"
              style="position: absolute; top: 100%; right: 0; z-index: 999; min-width: 200px; margin-top: 4px; background: var(--color-bg-elevated, #fff); border: 1px solid var(--color-border, #e0e0e0); border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); padding: 4px 0;"
            >
              <div v-if="loadingExportOptions" style="padding: 12px 16px; color: var(--color-text-secondary, #666); font-size: 13px;">
                加载中...
              </div>
              <template v-else>
                <button
                  v-for="option in exportOptions"
                  :key="option.id"
                  class="export-dropdown__item"
                  type="button"
                  style="display: block; width: 100%; text-align: left; padding: 8px 16px; border: none; background: none; cursor: pointer; font-size: 13px; color: var(--color-text, #333); line-height: 1.4;"
                  @mouseenter="($event.target as HTMLElement).style.background = 'var(--color-bg-hover, #f5f5f5)'"
                  @mouseleave="($event.target as HTMLElement).style.background = 'none'"
                  @click="exportWithType(option.id)"
                >
                  <div style="font-weight: 500;">{{ option.name }}</div>
                  <div style="font-size: 12px; color: var(--color-text-secondary, #999); margin-top: 2px;">{{ option.description }}</div>
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

    <section class="panel panel--header workbench-overview">
      <div class="workbench-overview__line">
        <div
          v-for="item in statusSummary"
          :key="item.key"
          class="workbench-stat"
          :class="`workbench-stat--${item.tone}`"
        >
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </div>

      </div>

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
    </section>

    <p v-if="pageError" class="form-message is-error">{{ pageError }}</p>

    <section v-if="segmentStore.loading" class="panel">
      <div class="empty-state">
        <Loader2 class="lucide-spin" :size="32" />
        {{ t('workbench.loadingTask') }}
      </div>
    </section>

    <section v-else class="workbench-layout">
      <section class="panel panel--stretch panel--editor" :class="{ 'has-search-open': segmentSearchOpen }">
        <div class="panel-header panel-header--compact segment-editor-toolbar">
          <div class="segment-editor-toolbar__title">
            <div class="section-title section-title--tight">{{ t('workbench.editorTitle') }}</div>
            <span class="hint-text">
              {{ segmentStore.loadedSegmentCount }} / {{ segmentStore.totalSegmentCount }}
            </span>
          </div>
          <div class="segment-editor-toolbar__actions">
            <button
              class="button workbench-action workbench-action--search segment-editor-toolbar__search"
              :class="{ 'is-active': segmentSearchOpen }"
              type="button"
              :aria-expanded="segmentSearchOpen"
              aria-controls="workbench-segment-search"
              @click="void toggleSegmentSearchPanel()"
            >
              <Search :size="14" />
              {{ t('workbench.search.title') }}
              <span v-if="hasEditorSegmentFilter" class="workbench-search-panel__badge">
                {{ editorSegments.length }}
              </span>
              <ChevronUp v-if="segmentSearchOpen" :size="14" />
              <ChevronDown v-else :size="14" />
            </button>

            <div class="workbench-search-panel__revision segment-editor-toolbar__revision">
              <div class="workbench-revision-menu">
                <button
                  class="button workbench-action workbench-action--search"
                  type="button"
                  :disabled="segmentStore.pendingRevisionCount === 0"
                  @click="openRevisionMenu = !openRevisionMenu"
                >
                  <MoreHorizontal :size="14" />
                  修订
                  <span v-if="segmentStore.pendingRevisionCount > 0" class="workbench-search-panel__badge">
                    {{ segmentStore.pendingRevisionCount }}
                  </span>
                </button>
                <div v-if="openRevisionMenu" class="workbench-revision-menu__dropdown">
                  <button
                    type="button"
                    :disabled="revisionActionLoading || segmentStore.pendingRevisionCount === 0"
                    @click="void handleBatchAcceptRevisions()"
                  >
                    全部接受
                  </button>
                  <button
                    class="is-danger"
                    type="button"
                    :disabled="revisionActionLoading || segmentStore.pendingRevisionCount === 0"
                    @click="void handleBatchRejectRevisions()"
                  >
                    全部拒绝
                  </button>
                </div>
              </div>
              <button
                class="button workbench-action workbench-action--search"
                type="button"
                :disabled="segmentStore.pendingRevisionCount === 0"
                title="上一条修订"
                @click="void focusRevisionByOffset(-1)"
              >
                <ChevronUp :size="14" />
              </button>
              <button
                class="button workbench-action workbench-action--search"
                type="button"
                :disabled="segmentStore.pendingRevisionCount === 0"
                title="下一条修订"
                @click="void focusRevisionByOffset(1)"
              >
                <ChevronDown :size="14" />
              </button>
            </div>
          </div>
        </div>

        <Transition name="workbench-panel-pop">
          <div
            v-if="segmentSearchOpen"
            id="workbench-segment-search"
            class="workbench-search-panel"
            @keydown.esc.stop="closeSegmentSearchPanel"
          >
            <div class="workbench-search-panel__form">
              <label class="field field--compact workbench-search-panel__scope">
                <span class="field__label">{{ t('workbench.search.scopeLabel') }}</span>
                <select v-model="segmentDisplayScope" class="field__control">
                  <option v-for="option in segmentDisplayScopeOptions" :key="option.value" :value="option.value">
                    {{ option.label }}
                  </option>
                </select>
              </label>

              <label class="field field--compact">
                <span class="field__label">{{ t('workbench.search.sourceLabel') }}</span>
                <input
                  ref="sourceSearchInputRef"
                  v-model="sourceSearchQuery"
                  class="field__control"
                  type="text"
                  :placeholder="t('workbench.search.sourcePlaceholder')"
                  @keydown.enter.prevent="void focusMatchedSegment(1)"
                />
              </label>

              <label class="field field--compact">
                <span class="field__label">{{ t('workbench.search.targetLabel') }}</span>
                <input
                  v-model="targetSearchQuery"
                  class="field__control"
                  type="text"
                  :placeholder="t('workbench.search.targetPlaceholder')"
                  @keydown.enter.prevent="void focusMatchedSegment(1)"
                />
              </label>

              <div class="workbench-search-panel__actions">
                <button
                  class="button workbench-action workbench-action--search"
                  type="button"
                  :disabled="searchLoadingAllSegments || editorSegments.length === 0"
                  @click="void focusMatchedSegment(-1)"
                >
                  <ArrowUp :size="14" />
                  {{ t('workbench.search.prev') }}
                </button>
                <button
                  class="button workbench-action workbench-action--search"
                  type="button"
                  :disabled="searchLoadingAllSegments || editorSegments.length === 0"
                  @click="void focusMatchedSegment(1)"
                >
                  <ArrowDown :size="14" />
                  {{ t('workbench.search.next') }}
                </button>
                <button
                  v-if="hasEditorSegmentFilter"
                  class="button workbench-action workbench-action--clear"
                  type="button"
                  @click="resetSegmentSearch"
                >
                  <X :size="14" />
                  {{ t('workbench.search.clear') }}
                </button>
                <button
                  class="button workbench-action workbench-action--search workbench-search-panel__close"
                  type="button"
                  title="收起句段检索"
                  aria-label="收起句段检索"
                  @click="closeSegmentSearchPanel"
                >
                  <X :size="14" />
                </button>
              </div>
            </div>

            <div v-if="hasEditorSegmentFilter || searchLoadingAllSegments" class="workbench-search-panel__meta">
              <span v-if="hasEditorSegmentFilter" class="hint-text">
                {{
                  t('workbench.search.resultSummary', {
                    count: editorSegments.length,
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

        <div
          v-if="hasEditorSegmentFilter && !searchLoadingAllSegments && editorSegments.length === 0"
          class="empty-state workbench-search-empty"
        >
          <Search :size="28" />
          {{ t('workbench.search.noMatch') }}
        </div>

        <template v-else>
          <div class="segment-table-head" aria-hidden="true">
            <span>句段</span>
            <span>原文</span>
            <span>译文</span>
          </div>

          <VirtualList
            ref="virtualListRef"
            :items="editorSegments"
            :item-height="itemHeight"
            :adaptive="true"
            :active-descendant="segmentStore.activeSentenceId ? `segment-${segmentStore.activeSentenceId}` : null"
            @reach-end="handleEditorReachEnd"
          >
            <template #default="{ item, index }">
              <SegmentEditorRow
                :segment="item"
                :index="getEditorSegmentDisplayIndex(item.sentence_id, index)"
                :active="segmentStore.activeSentenceId === item.sentence_id"
                :pending-revision="segmentStore.getPendingRevision(item.sentence_id)"
                :revision-busy="revisionActionLoading"
                :matched-terms="segmentStore.activeSentenceId === item.sentence_id ? activeMatchedTerms : []"
                @focus="segmentStore.setActiveSentence"
                @activate-target="handleSegmentTargetActivate"
                @update="segmentStore.updateTarget"
                @accept-revision="handleAcceptRevision"
                @reject-revision="handleRejectRevision"
              />
            </template>
          </VirtualList>
        </template>
      </section>

      <div
        class="workbench-sidecar"
        :class="{ 'is-preview-open': activeTool && activeTool !== 'split-preview', 'is-split-open': activeTool === 'split-preview' }"
      >
        <div v-if="activeTool" class="workbench-sidecar__panel">
        <Transition name="preview-drawer" mode="out-in">
          <SplitPreviewPanel
            v-if="activeTool === 'split-preview'"
            key="split-preview"
            :source-html="segmentStore.previewHtml"
            :target-html="targetPreviewHtml"
            :source-supported="segmentStore.previewSupported"
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
          <WorkbenchTermsPanel
            v-else-if="activeTool === 'terms'"
            key="terms"
            :term-bases="termBases"
            :selected-term-base-id="selectedTermBaseId"
            :entries="termEntries"
            :active-source-text="activeSegmentSourceText"
            :loading-bases="loadingTermBases"
            :loading-entries="loadingTermEntries"
            :message="termsMessage"
            @update:selected-term-base-id="selectedTermBaseId = $event"
          />
          <WorkbenchMatchPanel
            v-else-if="activeTool === 'match-info'"
            key="match-info"
            :segment="activeSegment"
            :collection-id="segmentStore.fileRecord?.collection_id ?? null"
            :collection-name="segmentStore.fileRecord?.collection_name ?? null"
            :term-base-id="segmentStore.fileRecord?.term_base_id ?? null"
            :term-base-name="segmentStore.fileRecord?.term_base_name ?? null"
            :term-entries="termEntries"
            :active-source-text="activeSegmentSourceText"
            :file-record-id="segmentStore.fileRecord?.id ?? null"
            @replace-text="handleReplaceText"
            @append-text="handleAppendText"
          />
          <NotesPanel
            v-else-if="activeTool === 'notes'"
            key="notes"
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

        <aside class="workbench-rail" :aria-label="t('workbench.toolsLabel')">
          <button
            v-for="tool in toolButtons"
            :key="tool.key"
            class="workbench-rail__button"
            :class="[`workbench-rail__button--${tool.tone}`, { 'is-active': activeTool === tool.key }]"
            type="button"
            :title="tool.label"
            :aria-label="tool.label"
            @click="void openTool(tool.key)"
          >
            <component :is="tool.icon" :size="20" />
            <span>{{ tool.label }}</span>
          </button>
        </aside>
      </div>
    </section>

    <ResourceImportDialog
      :open="showImportDialog"
      :initial-tab="importDialogInitialTab"
      :context-label="segmentStore.fileRecord?.filename ? t('workbench.importContext', { name: segmentStore.fileRecord.filename }) : t('workbench.currentTask')"
      :source-language="segmentStore.fileRecord?.source_language ?? null"
      :target-language="segmentStore.fileRecord?.target_language ?? null"
      @close="showImportDialog = false"
    />
    <IssueMarkerDialog
      :open="showIssueDialog"
      :project-id="segmentStore.fileRecord?.project_id ?? null"
      :file-record-id="segmentStore.fileRecord?.id ?? null"
      :context-label="segmentStore.fileRecord?.filename ? t('workbench.importContext', { name: segmentStore.fileRecord.filename }) : t('workbench.currentTask')"
      @close="showIssueDialog = false"
      @saved="handleIssueSaved"
    />

    <Modal
      :open="showSaveToTMDialog"
      :title="t('workbench.saveToTMTitle')"
      :description="t('workbench.saveToTMDescription')"
      width="min(560px, calc(100vw - 32px))"
      @close="showSaveToTMDialog = false"
    >
      <div class="save-to-tm-dialog">
        <fieldset class="save-to-tm-dialog__scope" :disabled="savingToTM">
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
            :disabled="savingToTM"
          >
        </label>

        <label v-else class="field">
          <span class="field__label">{{ t('workbench.saveToTMCollection') }}</span>
          <select
            v-model="saveToTMCollectionId"
            class="field__control"
            :disabled="loadingTMCollections || savingToTM"
          >
            <option value="">{{ t('workbench.saveToTMCollectionPlaceholder') }}</option>
            <option v-for="collection in orderedSaveToTMCollections" :key="collection.id" :value="collection.id">
              {{ collection.name }}{{ collection.id === taskTMCollectionId ? t('workbench.saveToTMTaskCollectionSuffix') : '' }}
            </option>
          </select>
        </label>

        <fieldset class="save-to-tm-dialog__scope" :disabled="savingToTM">
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
          <button class="button button--ghost" type="button" :disabled="savingToTM" @click="showSaveToTMDialog = false">
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
      width="min(520px, calc(100vw - 32px))"
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
  justify-content: space-between;
  gap: 10px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.workbench-search-panel {
  margin-bottom: 8px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.96), rgba(255, 255, 255, 0.98));
  overflow: hidden;
  transform-origin: top center;
}

.workbench-search-panel__header,
.workbench-search-panel__form,
.workbench-search-panel__actions,
.workbench-search-panel__meta {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  flex-wrap: wrap;
}

.workbench-search-panel__header {
  align-items: flex-start;
  justify-content: space-between;
}

.workbench-search-panel__form > .field {
  flex: 1 1 220px;
}

.workbench-search-panel__form > .workbench-search-panel__scope {
  flex: 0 0 160px;
}

.workbench-search-panel .field__label {
  font-size: 12px;
}

.workbench-search-panel .field__control {
  min-height: 34px;
  padding: 7px 10px;
  font-size: 13px;
}

.workbench-search-panel .button,
.segment-editor-toolbar__search,
.segment-editor-toolbar__revision .workbench-action {
  min-height: 34px;
  padding: 7px 10px;
  font-size: 13px;
  box-shadow: none;
}

.workbench-search-panel__actions {
  display: flex;
  gap: 6px;
  margin-left: auto;
}

.workbench-search-panel__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.workbench-search-panel__revision {
  display: flex;
  align-items: center;
  gap: 6px;
}

.workbench-search-panel__revision .workbench-action:last-child,
.workbench-search-panel__revision .workbench-action:nth-last-child(2) {
  min-width: 40px;
  padding: 0 12px;
}

.workbench-search-panel__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  min-height: 20px;
  padding: 0 5px;
  border-radius: 999px;
  background: rgba(13, 122, 104, 0.14);
  color: #0b6b5b;
  font-size: 11px;
}

.workbench-search-panel__meta {
  align-items: center;
  min-height: 20px;
}

.segment-editor-toolbar {
  align-items: center;
  margin-bottom: 8px;
}

.segment-editor-toolbar__search.is-active {
  border-color: #69ad9d;
  background: linear-gradient(180deg, #edf7f4, #d7ece6);
  color: #0b6658;
  box-shadow: 0 6px 14px rgba(13, 122, 104, 0.12);
}

.segment-editor-toolbar__title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.segment-editor-toolbar__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  margin-left: auto;
}

.segment-editor-toolbar__revision {
  justify-content: flex-end;
}

.workbench-search-empty {
  min-height: 240px;
  border: 1px dashed var(--line-soft);
  border-radius: 14px;
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

@media (max-width: 720px) {
  .segment-editor-toolbar__actions {
    width: 100%;
    justify-content: stretch;
    margin-left: 0;
  }

  .segment-editor-toolbar__search,
  .segment-editor-toolbar__revision {
    flex: 1 1 100%;
  }

  .workbench-search-panel__actions {
    width: 100%;
    margin-left: 0;
  }

  .workbench-search-panel__actions .button {
    flex: 1 1 0;
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
