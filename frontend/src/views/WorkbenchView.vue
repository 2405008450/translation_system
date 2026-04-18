<script setup lang="ts">
import axios from 'axios'
import { ArrowLeft, Bot, Download, Loader2, Save, FileText, FileCheck, Columns, Languages, MessageSquare, History } from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'

import NotesPanel from '../components/NotesPanel.vue'
import PreviewPanel from '../components/PreviewPanel.vue'
import SegmentEditorRow from '../components/SegmentEditorRow.vue'
import SplitPreviewPanel from '../components/SplitPreviewPanel.vue'
import TMMatchPanel from '../components/TMMatchPanel.vue'
import VirtualList from '../components/VirtualList.vue'
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
} from '../types/api'
import { buildDocumentPreviewHtml } from '../utils/documentPreview'

const props = defineProps<{
  id: string
}>()

const router = useRouter()
const authStore = useAuthStore()
const commentStore = useCommentStore()
const segmentStore = useSegmentStore()
const virtualListRef = ref<{
  scrollToIndex: (index: number, align?: ScrollLogicalPosition) => Promise<boolean>
  focusIndex: (index: number, selector?: string, align?: ScrollLogicalPosition) => Promise<boolean>
} | null>(null)

type ToolKey = 'source-preview' | 'target-preview' | 'split-preview' | 'tm-match' | 'terms' | 'notes' | 'history'
type ActiveToolConfig =
  | {
      kind: 'preview'
      title: string
      html: string
      supported: boolean
      renderMode?: 'static' | 'target'
      segments?: Segment[]
      updatedSentenceId?: string | null
      updatedSentenceText?: string
      updateToken?: number
    }
  | {
      kind: 'split'
      title: string
    }
  | {
      kind: 'tm-match'
      title: string
    }
  | {
      kind: 'notes'
      title: string
    }
  | {
      kind: 'placeholder'
      title: string
      message: string
    }

const pageError = ref('')
const llmScope = ref<LLMTranslateScope>('all')
const llmProvider = ref<LLMProvider>('auto')
const itemHeight = ref(resolveItemHeight())
const activeTool = ref<ToolKey | null>(null)

function resolveItemHeight() {
  if (window.innerWidth <= 720) {
    return 388
  }
  if (window.innerWidth <= 1180) {
    return 260
  }
  return 236
}

function handleResize() {
  itemHeight.value = resolveItemHeight()
}

function toggleTool(tool: ToolKey) {
  activeTool.value = activeTool.value === tool ? null : tool
}

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
    { key: 'exact', label: '精确匹配', value: counters.exact, tone: 'exact' },
    { key: 'fuzzy', label: '模糊匹配', value: counters.fuzzy, tone: 'fuzzy' },
    { key: 'none', label: '未匹配', value: counters.none, tone: 'none' },
    { key: 'confirmed', label: '已确认', value: counters.confirmed, tone: 'confirmed' },
  ]
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
  return buildDocumentPreviewHtml(segmentStore.segments, 'target')
})

const targetPreviewSupported = computed(() =>
  segmentStore.previewSupported || segmentStore.segments.length > 0,
)

const toolButtons = [
  { key: 'source-preview' as const, label: '原文预览', icon: FileText },
  { key: 'target-preview' as const, label: '译文预览', icon: FileCheck },
  { key: 'split-preview' as const, label: '分屏对照', icon: Columns },
  { key: 'tm-match' as const, label: '记忆/术语匹配', icon: Languages },
  { key: 'terms' as const, label: '术语', icon: Languages },
  { key: 'notes' as const, label: '批注', icon: MessageSquare },
  { key: 'history' as const, label: '历史版本', icon: History },
]

const activeToolConfig = computed<ActiveToolConfig | null>(() => {
  if (activeTool.value === 'source-preview') {
    return {
      kind: 'preview' as const,
      title: '原文预览',
      html: segmentStore.previewHtml,
      supported: segmentStore.previewSupported,
    }
  }

  if (activeTool.value === 'target-preview') {
    return {
      kind: 'preview' as const,
      title: '译文预览',
      html: targetPreviewHtml.value,
      supported: targetPreviewSupported.value,
      renderMode: targetPreviewRenderMode.value,
      segments: targetPreviewRenderMode.value === 'target' ? segmentStore.segments : [],
      updatedSentenceId: segmentStore.lastPreviewUpdatedSentenceId,
      updatedSentenceText: segmentStore.lastPreviewUpdatedText,
      updateToken: segmentStore.previewUpdateToken,
    }
  }

  if (activeTool.value === 'split-preview') {
    return {
      kind: 'split' as const,
      title: '分屏对照',
    }
  }

  if (activeTool.value === 'tm-match') {
    return {
      kind: 'tm-match' as const,
      title: '记忆匹配',
    }
  }

  if (activeTool.value === 'terms') {
    return {
      kind: 'placeholder' as const,
      title: '术语',
      message: '术语面板即将接入。',
    }
  }

  if (activeTool.value === 'notes') {
    return {
      kind: 'notes' as const,
      title: '批注',
    }
  }

  if (activeTool.value === 'history') {
    return {
      kind: 'placeholder' as const,
      title: '历史版本',
      message: '历史版本面板即将接入。',
    }
  }

  return null
})

const activePreviewConfig = computed(() =>
  activeToolConfig.value?.kind === 'preview' ? activeToolConfig.value : null,
)

const activePlaceholderConfig = computed(() =>
  activeToolConfig.value?.kind === 'placeholder' ? activeToolConfig.value : null,
)

const activeSplitConfig = computed(() =>
  activeToolConfig.value?.kind === 'split' ? activeToolConfig.value : null,
)

const activeTMMatchConfig = computed(() =>
  activeToolConfig.value?.kind === 'tm-match' ? activeToolConfig.value : null,
)

const activeNotesConfig = computed(() =>
  activeToolConfig.value?.kind === 'notes' ? activeToolConfig.value : null,
)

function handlePreviewFocus(sentenceId: string) {
  segmentStore.setActiveSentence(sentenceId)
  void focusEditorSentence(sentenceId)
}

async function focusEditorSentence(sentenceId: string) {
  const targetIndex = segmentStore.segments.findIndex((segment) => segment.sentence_id === sentenceId)
  if (targetIndex === -1) {
    return
  }

  await nextTick()
  await virtualListRef.value?.focusIndex(targetIndex, '[data-segment-target="true"]', 'nearest')
}

function handleApplyTMTarget(sentenceId: string, targetText: string) {
  segmentStore.updateTarget(sentenceId, targetText)
}

async function loadTask() {
  pageError.value = ''
  commentStore.stopPolling()
  try {
    await segmentStore.loadTask(props.id)
    try {
      await commentStore.loadComments(props.id)
      commentStore.startPolling(props.id)
    } catch (error) {
      if (axios.isAxiosError(error)) {
        commentStore.message = String(error.response?.data?.detail || '批注暂时不可用。')
      } else {
        commentStore.message = error instanceof Error ? error.message : '批注暂时不可用。'
      }
    }
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '任务加载失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '任务加载失败。'
  }
}

async function runLLMTranslation() {
  pageError.value = ''
  try {
    await segmentStore.startLLMTranslation(llmScope.value, llmProvider.value)
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || 'AI 修正失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : 'AI 修正失败。'
  }
}

async function saveNow() {
  pageError.value = ''
  try {
    await segmentStore.syncToBackend()
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '保存失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '保存失败。'
  }
}

async function exportDocx() {
  pageError.value = ''
  try {
    await segmentStore.downloadTranslatedDocx()
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || 'DOCX 导出失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : 'DOCX 导出失败。'
  }
}

async function handleCommentDraft(draft: CommentAnchorDraft) {
  commentStore.setDraftAnchor(draft)
  commentStore.setActiveComment(null)
  segmentStore.setActiveSentence(draft.sentence_id)
  await focusEditorSentence(draft.sentence_id)
  activeTool.value = 'notes'
}

async function handleCommentFocus(commentId: string) {
  commentStore.setDraftAnchor(null)
  commentStore.setActiveComment(commentId)
  const comment = commentStore.comments.find((item) => item.id === commentId)
  if (comment?.sentence_id) {
    segmentStore.setActiveSentence(comment.sentence_id)
    await focusEditorSentence(comment.sentence_id)
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
      await focusEditorSentence(comment.sentence_id)
    }
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '批注保存失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '批注保存失败。'
  }
}

async function handleReplyComment(commentId: string, body: string) {
  pageError.value = ''
  try {
    const comment = await commentStore.replyToComment(commentId, { body })
    if (comment.sentence_id) {
      segmentStore.setActiveSentence(comment.sentence_id)
      await focusEditorSentence(comment.sentence_id)
    }
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '回复保存失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '回复保存失败。'
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
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '批注更新失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '批注更新失败。'
  }
}

async function handleDeleteComment(commentId: string) {
  pageError.value = ''
  try {
    await commentStore.deleteComment(commentId)
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '批注删除失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '批注删除失败。'
  }
}

watch(() => props.id, () => {
  void loadTask()
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
  <div class="content-stack content-stack--workbench">
    <section class="panel panel--header panel--hero">
      <div class="panel-header">
        <div>
          <div class="section-title section-title--tight">{{ segmentStore.fileRecord?.filename || '任务加载中' }}</div>
          <p class="panel-subtitle">
            共 {{ segmentStore.segments.length }} 条句段，{{ segmentStore.syncMessage }}
          </p>
        </div>

        <div class="header-actions">
          <button class="button" type="button" @click="router.push({ name: 'tasks' })">
            <ArrowLeft /> 返回任务列表
          </button>
          <button class="button" type="button" :disabled="segmentStore.saving" @click="saveNow">
            <Loader2 v-if="segmentStore.saving" class="lucide-spin" />
            <Save v-else />
            {{ segmentStore.saving ? '保存中...' : '立即保存' }}
          </button>
          <button class="button" type="button" @click="exportDocx">
            <Download /> 导出 DOCX
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
    </section>

    <p v-if="pageError" class="form-message is-error">{{ pageError }}</p>

    <section class="toolbar-panel">
      <div class="toolbar-panel__group">
        <label class="field field--compact">
          <span class="field__label">AI 处理范围</span>
          <select v-model="llmScope" class="field__control">
            <option value="all">fuzzy + none</option>
            <option value="all_with_exact">exact + fuzzy + none</option>
            <option value="fuzzy_only">仅 fuzzy</option>
            <option value="none_only">仅 none</option>
          </select>
        </label>

        <label class="field field--compact">
          <span class="field__label">AI 提供方</span>
          <select v-model="llmProvider" class="field__control">
            <option value="auto">自动</option>
            <option value="deepseek">DeepSeek</option>
            <option value="openrouter">OpenRouter</option>
          </select>
        </label>

        <button
          class="button button--primary"
          type="button"
          :disabled="segmentStore.llmRunning"
          @click="runLLMTranslation"
        >
          <Loader2 v-if="segmentStore.llmRunning" class="lucide-spin" />
          <Bot v-else />
          {{ segmentStore.llmRunning ? 'AI 处理中...' : '执行 AI 修正' }}
        </button>
      </div>

      <div class="toolbar-panel__status">
        <span>待同步 {{ segmentStore.dirtyCount }} 条</span>
        <span>{{ segmentStore.llmMessage }}</span>
      </div>
    </section>

    <section v-if="segmentStore.loading" class="panel">
      <div class="empty-state">
        <Loader2 class="lucide-spin" :size="32" />
        任务内容加载中...
      </div>
    </section>

    <section v-else class="workbench-layout">
      <section class="panel panel--stretch panel--editor">
        <div class="panel-header panel-header--compact">
          <div>
            <div class="section-title section-title--tight">句段编辑</div>
            <p class="panel-subtitle">逐句校对并整理译文。</p>
          </div>
        </div>
        <VirtualList
          ref="virtualListRef"
          :items="segmentStore.segments"
          :item-height="itemHeight"
        >
          <template #default="{ item, index }">
            <SegmentEditorRow
              :segment="item"
              :index="index"
              :active="segmentStore.activeSentenceId === item.sentence_id"
              :term-matches="segmentStore.getTermMatches(item.sentence_id)"
              @focus="segmentStore.setActiveSentence"
              @update="segmentStore.updateTarget"
            />
          </template>
        </VirtualList>
      </section>

      <div
        class="workbench-sidecar"
        :class="{ 'is-preview-open': !!activeToolConfig, 'is-split-open': !!activeSplitConfig }"
      >
        <Transition name="preview-drawer">
          <SplitPreviewPanel
            v-if="activeSplitConfig"
            :key="activeTool || 'split-preview'"
            :source-html="segmentStore.previewHtml"
            :target-html="targetPreviewHtml"
            :source-supported="segmentStore.previewSupported"
            :target-supported="targetPreviewSupported"
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
            v-if="activePreviewConfig"
            :key="activeTool || 'preview-panel'"
            class="preview-panel--drawer"
            :title="activePreviewConfig.title"
            :html="activePreviewConfig.html"
            :supported="activePreviewConfig.supported"
            :active-sentence-id="segmentStore.activeSentenceId"
            :comments="commentStore.comments"
            :active-comment-id="commentStore.activeCommentId"
            :enable-comment-selection="true"
            :render-mode="activePreviewConfig.renderMode || 'static'"
            :segments="activePreviewConfig.segments || []"
            :updated-sentence-id="activePreviewConfig.updatedSentenceId || null"
            :updated-sentence-text="activePreviewConfig.updatedSentenceText || ''"
            :update-token="activePreviewConfig.updateToken || 0"
            @focus-sentence="handlePreviewFocus"
            @focus-comment="handleCommentFocus"
            @request-comment="handleCommentDraft"
            @close="activeTool = null"
          />
        </Transition>

        <Transition name="preview-drawer">
          <TMMatchPanel
            v-if="activeTMMatchConfig"
            :file-record-id="props.id"
            :active-sentence-id="segmentStore.activeSentenceId"
            :active-source-text="segmentStore.activeSourceText"
            @close="activeTool = null"
            @apply-target="handleApplyTMTarget"
          />
        </Transition>

        <Transition name="preview-drawer">
          <NotesPanel
            v-if="activeNotesConfig"
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
          <section v-if="activePlaceholderConfig" class="panel workbench-tool-panel">
            <div class="panel-header panel-header--compact">
              <div class="section-title section-title--tight">{{ activePlaceholderConfig.title }}</div>
              <button class="button preview-panel__close" type="button" @click="activeTool = null">关闭</button>
            </div>
            <div class="empty-state">{{ activePlaceholderConfig.message }}</div>
          </section>
        </Transition>

        <aside class="workbench-rail" aria-label="工作台工具">
          <button
            v-for="tool in toolButtons"
            :key="tool.key"
            class="workbench-rail__button"
            :class="{ 'is-active': activeTool === tool.key }"
            type="button"
            :title="tool.label"
            @click="toggleTool(tool.key)"
          >
            <component :is="tool.icon" :size="20" />
            <span>{{ tool.label }}</span>
          </button>
        </aside>
      </div>
    </section>
  </div>
</template>
