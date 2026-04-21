<script setup lang="ts">
import axios from 'axios'
import {
  ArrowLeft,
  Bot,
  CircleHelp,
  Columns,
  Download,
  FileCheck,
  FileText,
  History,
  Languages,
  Loader2,
  MessageSquare,
  Save,
  Upload,
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
import WorkbenchTermsPanel from '../components/WorkbenchTermsPanel.vue'
import { http } from '../api/http'
import { usePageHeader } from '../composables/usePageHeader'
import { useToast } from '../composables/useToast'
import { useWorkbenchShortcuts } from '../composables/useWorkbenchShortcuts'
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

type ToolKey = 'source-preview' | 'target-preview' | 'split-preview' | 'terms' | 'notes' | 'history'
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

const termBases = ref<TermBase[]>([])
const termEntries = ref<TermEntryRecord[]>([])
const selectedTermBaseId = ref('')
const loadingTermBases = ref(false)
const loadingTermEntries = ref(false)
const termsMessage = ref(t('workbench.terms.defaultMessage'))

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
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.sidePanel'))
  }
}

async function handleEditorReachEnd() {
  try {
    await segmentStore.loadMoreSegments()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.loadMore'))
  }
}

function openImportDialog(tab: ResourceImportTab = 'tm') {
  importDialogInitialTab.value = tab
  showImportDialog.value = true
}

function getCurrentSegmentIndex() {
  if (!segmentStore.activeSentenceId) {
    return 0
  }
  return Math.max(0, segmentStore.segments.findIndex((segment) => segment.sentence_id === segmentStore.activeSentenceId))
}

async function focusSentenceByOffset(offset: number) {
  let targetIndex = getCurrentSegmentIndex() + offset
  targetIndex = Math.max(0, targetIndex)

  while (targetIndex >= segmentStore.segments.length && segmentStore.hasMoreSegments) {
    const loaded = await segmentStore.loadMoreSegments()
    if (!loaded) {
      break
    }
  }

  targetIndex = Math.min(targetIndex, Math.max(segmentStore.segments.length - 1, 0))
  const target = segmentStore.segments[targetIndex]
  if (!target) {
    return
  }

  segmentStore.setActiveSentence(target.sentence_id)
  await nextTick()
  await virtualListRef.value?.focusIndex(targetIndex, '[data-segment-target="true"]', 'nearest')
}

function confirmCurrentSentence() {
  if (!activeSegment.value) {
    return
  }
  segmentStore.updateTarget(activeSegment.value.sentence_id, activeSegment.value.target_text || '')
  toast.success(t('workbench.messages.confirmed'))
}

async function loadTask() {
  pageError.value = ''
  activeTool.value = null
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

async function exportDocx() {
  pageError.value = ''
  try {
    await segmentStore.downloadTranslatedDocx()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('workbench.errors.export'))
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

        <button class="button" type="button" @click="exportDocx">
          <Download :size="14" />
          {{ t('workbench.exportDocx') }}
        </button>

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

        <VirtualList
          ref="virtualListRef"
          :items="segmentStore.segments"
          :item-height="itemHeight"
          :adaptive="true"
          :active-descendant="segmentStore.activeSentenceId ? `segment-${segmentStore.activeSentenceId}` : null"
          @reach-end="handleEditorReachEnd"
        >
          <template #default="{ item, index }">
            <SegmentEditorRow
              :segment="item"
              :index="index"
              :active="segmentStore.activeSentenceId === item.sentence_id"
              @focus="segmentStore.setActiveSentence"
              @update="segmentStore.updateTarget"
            />
          </template>
        </VirtualList>
      </section>

      <div
        class="workbench-sidecar"
        :class="{ 'is-preview-open': activeTool && activeTool !== 'split-preview', 'is-split-open': activeTool === 'split-preview' }"
      >
        <Transition name="preview-drawer">
          <SplitPreviewPanel
            v-if="activeTool === 'split-preview'"
            :key="activeTool"
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
        </Transition>

        <Transition name="preview-drawer">
          <PreviewPanel
            v-if="activeTool === 'source-preview' || activeTool === 'target-preview'"
            :key="activeTool"
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
        </Transition>

        <Transition name="preview-drawer">
          <WorkbenchTermsPanel
            v-if="activeTool === 'terms'"
            :term-bases="termBases"
            :selected-term-base-id="selectedTermBaseId"
            :entries="termEntries"
            :active-source-text="activeSegmentSourceText"
            :loading-bases="loadingTermBases"
            :loading-entries="loadingTermEntries"
            :message="termsMessage"
            @update:selected-term-base-id="selectedTermBaseId = $event"
          />
        </Transition>

        <Transition name="preview-drawer">
          <NotesPanel
            v-if="activeTool === 'notes'"
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
        </Transition>

        <Transition name="preview-drawer">
          <WorkbenchHistoryPanel
            v-if="activeTool === 'history'"
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
.workbench-load-all {
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

.workbench-resource-panel__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
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
  .shortcut-item {
    flex-direction: column;
  }
}
</style>
