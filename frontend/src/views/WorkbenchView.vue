<script setup lang="ts">
import axios from 'axios'
import {
  ArrowLeft,
  ArrowDown,
  ArrowUp,
  Bot,
  CircleHelp,
  Columns,
  Download,
  FileCheck,
  FileText,
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
import { useAuthStore } from '../stores/auth'
import { useCommentStore } from '../stores/comment'
import { useSegmentStore } from '../stores/segment'
import type {
  CommentAnchorDraft,
  CommentCreatePayload,
  CommentStatus,
  LLMProvider,
  LLMTranslateScope,
  Segment,
  TermBase,
  TermEntryRecord,
} from '../types/api'
import { buildDocumentPreviewHtml } from '../utils/documentPreview'

const props = defineProps<{
  id: string
}>()

type ToolKey = 'source-preview' | 'target-preview' | 'split-preview' | 'match-info' | 'terms' | 'notes' | 'history'
type ResourceImportTab = 'tm' | 'term'

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
const importDialogInitialTab = ref<ResourceImportTab>('tm')
const showShortcutHelp = ref(false)
const openRevisionMenu = ref(false)
const revisionActionLoading = ref(false)
const sourceSearchQuery = ref('')
const targetSearchQuery = ref('')
const searchLoadingAllSegments = ref(false)

const termBases = ref<TermBase[]>([])
const termEntries = ref<TermEntryRecord[]>([])
const selectedTermBaseId = ref('')
const loadingTermBases = ref(false)
const loadingTermEntries = ref(false)
const termsMessage = ref(t('workbench.terms.defaultMessage'))
let searchLoadRequestId = 0

// 导出相关状态
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
    return 388
  }
  if (window.innerWidth <= 1180) {
    return 272
  }
  return 244
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

const hasProjectReturnContext = computed(() => (
  route.query.from === 'project' && typeof route.query.pid === 'string'
))

const projectReturnId = computed(() => (
  typeof route.query.pid === 'string' ? route.query.pid : ''
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

const activeSegmentSourceText = computed(() => activeSegment.value?.source_text || '')

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

const editorSegments = computed(() => {
  const sourceKeyword = normalizedSourceSearchQuery.value
  const targetKeyword = normalizedTargetSearchQuery.value

  if (!sourceKeyword && !targetKeyword) {
    return segmentStore.segments
  }

  return segmentStore.segments.filter((segment) => (
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

const exportButtonLabel = computed(() => (
  `${t('common.actions.export')} ${getTaskExportFormatLabel(segmentStore.fileRecord?.filename)}`
))

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
        { label: t('shell.sections.workspace'), to: { name: 'projects' } },
        { label: t('workbench.breadcrumbProject'), to: { name: 'project-detail', params: { id: projectReturnId.value } } },
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
  closePanel: () => { activeTool.value = null },
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
    if (hasSegmentSearch.value) {
      await ensureSearchCorpusLoaded()
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

function resetSegmentSearch() {
  searchLoadRequestId += 1
  sourceSearchQuery.value = ''
  targetSearchQuery.value = ''
  searchLoadingAllSegments.value = false
}

async function ensureSearchCorpusLoaded() {
  if (!hasSegmentSearch.value || !segmentStore.hasMoreSegments) {
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
  if (!hasSegmentSearch.value) {
    return
  }

  await ensureSearchCorpusLoaded()

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
  if (hasSegmentSearch.value) {
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

    await loadTermBases()

    // 如果有绑定的术语库，自动加载术语条目
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
    await segmentStore.startLLMTranslation(llmScope.value, llmProvider.value)
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.llm'))
  }
}

async function stopLLMTranslation() {
  await segmentStore.abortLLM()
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
    await router.push({ name: 'project-detail', params: { id: projectReturnId.value } })
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

  if (hasSegmentSearch.value) {
    await ensureSearchCorpusLoaded()
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
  void ensureMatchInfoPanelOpen()
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

watch([sourceSearchQuery, targetSearchQuery], async () => {
  if (hasSegmentSearch.value) {
    void ensureSearchCorpusLoaded()
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
      <div class="toolbar-panel__group">
        <button class="button workbench-action workbench-action--back" type="button" @click="goBack">
          <ArrowLeft :size="14" />
          {{ hasProjectReturnContext ? t('workbench.backToProject') : t('workbench.backToTasks') }}
        </button>

        <label class="field field--compact">
          <span class="field__label">{{ t('workbench.aiScope') }}</span>
          <select v-model="llmScope" class="field__control">
            <option v-for="option in llmScopeOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="field field--compact">
          <span class="field__label">{{ t('workbench.aiProvider') }}</span>
          <select v-model="llmProvider" class="field__control">
            <option v-for="option in llmProviderOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>

        <button class="button workbench-action workbench-action--save" type="button" :disabled="segmentStore.saving" @click="saveNow">
          <Loader2 v-if="segmentStore.saving" class="lucide-spin" :size="14" />
          <Save v-else :size="14" />
          {{ segmentStore.saving ? t('common.actions.saving') : t('workbench.saveNow') }}
        </button>

        <button class="button workbench-action workbench-action--export" type="button" :disabled="!segmentStore.canExport" @click="exportTranslatedFile">
          <Download :size="14" />
          {{ exportButtonLabel }}
        </button>

        <div class="workbench-revision-menu">
          <button
            class="button workbench-action workbench-action--review"
            type="button"
            :disabled="segmentStore.pendingRevisionCount === 0"
            @click="openRevisionMenu = !openRevisionMenu"
          >
            <MoreHorizontal :size="14" />
            修订管理
            <span class="workbench-revision-menu__badge">{{ segmentStore.pendingRevisionCount }}</span>
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
          v-if="!segmentStore.llmRunning"
          class="button workbench-action workbench-action--ai"
          type="button"
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

        <button class="button workbench-action workbench-action--help" type="button" @click="showShortcutHelp = true">
          <CircleHelp :size="14" />
          {{ t('workbench.shortcuts') }}
        </button>
      </div>

      <div class="toolbar-panel__status workbench-toolbar__status">
        <span>{{ t('workbench.pendingSync', { count: segmentStore.dirtyCount }) }}</span>
        <span>{{ segmentStore.syncMessage }}</span>
        <span>{{ segmentStore.llmMessage }}</span>
      </div>

      <div v-if="segmentStore.llmRunning" class="workbench-toolbar__progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div class="progress-bar__fill" :style="{ width: `${segmentStore.llmProgressPercent}%` }" />
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

    <section class="panel panel--header">
      <div class="panel-header panel-header--compact">
        <div>
          <div class="section-title section-title--tight">{{ t('workbench.taskOverview') }}</div>
          <p class="panel-subtitle">
            {{ t('workbench.overviewDescription', {
              pair: currentLanguagePair,
              loaded: segmentStore.loadedSegmentCount,
              total: segmentStore.totalSegmentCount,
            }) }}
          </p>
        </div>

        <div class="workbench-resource-panel__actions">
          <button class="button workbench-action workbench-action--import-tm" type="button" @click="openImportDialog('tm')">
            <Upload :size="14" />
            {{ t('workbench.importTm') }}
          </button>
          <button class="button workbench-action workbench-action--import-term" type="button" @click="openImportDialog('term')">
            <Upload :size="14" />
            {{ t('workbench.importTerm') }}
          </button>
        </div>
      </div>

      <div class="workbench-stats">
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
      <section class="panel panel--stretch panel--editor">
        <div class="panel-header panel-header--compact">
          <div>
            <div class="section-title section-title--tight">{{ t('workbench.editorTitle') }}</div>
            <p class="panel-subtitle">{{ t('workbench.editorDescription') }}</p>
          </div>
        </div>

        <div class="workbench-search-panel">
          <div class="workbench-search-panel__header">
            <div>
              <div class="section-title section-title--tight">{{ t('workbench.search.title') }}</div>
              <p class="panel-subtitle">{{ t('workbench.search.description') }}</p>
            </div>
            <button
              v-if="hasSegmentSearch"
              class="button workbench-action workbench-action--clear"
              type="button"
              @click="resetSegmentSearch"
            >
              <X :size="14" />
              {{ t('workbench.search.clear') }}
            </button>
          </div>

          <div class="workbench-search-panel__form">
            <label class="field field--compact">
              <span class="field__label">{{ t('workbench.search.sourceLabel') }}</span>
              <input
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
            </div>
          </div>

          <div class="workbench-search-panel__meta">
            <span class="hint-text">
              {{
                hasSegmentSearch
                  ? t('workbench.search.resultSummary', {
                      count: editorSegments.length,
                      total: segmentStore.totalSegmentCount,
                    })
                  : t('workbench.search.idle')
              }}
            </span>
            <span v-if="searchLoadingAllSegments" class="hint-text">
              {{ t('workbench.search.loadingAll') }}
            </span>
          </div>
        </div>

        <div
          v-if="hasSegmentSearch && !searchLoadingAllSegments && editorSegments.length === 0"
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
              @focus="segmentStore.setActiveSentence"
              @activate-target="handleSegmentTargetActivate"
              @update="segmentStore.updateTarget"
              @accept-revision="handleAcceptRevision"
              @reject-revision="handleRejectRevision"
            />
          </template>
        </VirtualList>
      </section>

      <div
        class="workbench-sidecar"
        :class="{ 'is-preview-open': activeTool && activeTool !== 'split-preview', 'is-split-open': activeTool === 'split-preview' }"
      >
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
  gap: 12px;
}

.workbench-toolbar__status {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.workbench-action {
  --action-bg: linear-gradient(180deg, #f4f7f8, #e8eef1);
  --action-border: #ccd9de;
  --action-color: #2d4651;
  --action-shadow: rgba(37, 61, 70, 0.1);
  --action-hover-shadow: rgba(37, 61, 70, 0.16);

  border-color: var(--action-border);
  background: var(--action-bg);
  color: var(--action-color);
  font-weight: 600;
  box-shadow: 0 8px 16px var(--action-shadow);
}

.workbench-action:not(:disabled):hover {
  border-color: color-mix(in srgb, var(--action-border) 82%, #17313b);
  box-shadow: 0 10px 20px var(--action-hover-shadow);
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
  margin-top: 14px;
}

.workbench-search-panel {
  margin-bottom: 16px;
  padding: 16px;
  border: 1px solid var(--line-soft);
  border-radius: 14px;
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.96), rgba(255, 255, 255, 0.98));
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
  flex: 1 1 260px;
}

.workbench-search-panel__actions {
  margin-left: auto;
}

.workbench-search-panel__meta {
  align-items: center;
}

.workbench-search-empty {
  min-height: 240px;
  border: 1px dashed var(--line-soft);
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.72);
}

.workbench-resource-panel__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
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
}

.workbench-page .workbench-rail__button.is-active {
  border-color: var(--rail-active-border);
  background: var(--rail-active-bg);
  color: var(--rail-active-text);
  box-shadow: 0 12px 22px var(--rail-active-shadow);
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
  .workbench-search-panel__actions {
    width: 100%;
    margin-left: 0;
  }

  .workbench-search-panel__actions .button {
    flex: 1 1 0;
  }

  .shortcut-item {
    flex-direction: column;
  }
}
</style>
