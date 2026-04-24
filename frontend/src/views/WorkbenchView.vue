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
const llmProvider = ref<LLMProvider>('auto')
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
  { key: 'source-preview' as const, label: t('workbench.tools.sourcePreview'), icon: FileText },
  { key: 'target-preview' as const, label: t('workbench.tools.targetPreview'), icon: FileCheck },
  { key: 'split-preview' as const, label: t('workbench.tools.splitPreview'), icon: Columns },
  { key: 'match-info' as const, label: t('workbench.tools.matchInfo'), icon: Info },
  { key: 'terms' as const, label: t('workbench.tools.terms'), icon: Languages },
  { key: 'notes' as const, label: t('workbench.tools.notes'), icon: MessageSquare },
  { key: 'history' as const, label: t('workbench.tools.history'), icon: History },
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
  void loadTask()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
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
        <button class="button" type="button" @click="goBack">
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

        <button class="button" type="button" :disabled="segmentStore.saving" @click="saveNow">
          <Loader2 v-if="segmentStore.saving" class="lucide-spin" :size="14" />
          <Save v-else :size="14" />
          {{ segmentStore.saving ? t('common.actions.saving') : t('workbench.saveNow') }}
        </button>

        <button class="button" type="button" :disabled="!segmentStore.canExport" @click="exportTranslatedFile">
          <Download :size="14" />
          {{ exportButtonLabel }}
        </button>

        <div class="workbench-revision-menu">
          <button
            class="button"
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
          class="button button--primary"
          type="button"
          @click="runLLMTranslation"
        >
          <Bot :size="14" />
          {{ t('workbench.runAi') }}
        </button>
        <button
          v-else
          class="button button--danger"
          type="button"
          @click="stopLLMTranslation"
        >
          <Loader2 class="lucide-spin" :size="14" />
          {{ t('workbench.stopAi') }}
        </button>

        <button class="button" type="button" @click="showShortcutHelp = true">
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

        <div v-if="authStore.isAdmin" class="workbench-resource-panel__actions">
          <button class="button" type="button" @click="openImportDialog('tm')">
            <Upload :size="14" />
            {{ t('workbench.importTm') }}
          </button>
          <button class="button" type="button" @click="openImportDialog('term')">
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
          class="button"
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
              class="button"
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
                class="button"
                type="button"
                :disabled="searchLoadingAllSegments || editorSegments.length === 0"
                @click="void focusMatchedSegment(-1)"
              >
                <ArrowUp :size="14" />
                {{ t('workbench.search.prev') }}
              </button>
              <button
                class="button"
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
            :collection-name="segmentStore.fileRecord?.collection_name ?? null"
            :term-base-name="segmentStore.fileRecord?.term_base_name ?? null"
            :term-entries="termEntries"
            :active-source-text="activeSegmentSourceText"
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
            :class="{ 'is-active': activeTool === tool.key }"
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
