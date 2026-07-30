<script setup lang="ts">
import {
  Bot, Download, Layers, List, Loader2, RotateCcw, Settings, X,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, ref } from 'vue'
import Modal from './base/Modal.vue'
import {
  applyTranslationReviewBatch, applyTranslationReviewItem,
  createFileTranslationReviewTask, createMergeViewTranslationReviewTask,
  fetchFileTranslationReviewReport, fetchMergeViewTranslationReviewReport,
  fetchTranslationReviewReport, fetchTranslationReviewTask,
  rejectTranslationReviewItem, rerunTranslationReview, restoreTranslationReviewItem,
  setTranslationReviewItemsIgnored, undoTranslationReviewBatch,
  type TranslationReviewTaskOptions,
} from '../api/translationReview'
import type { TranslationReviewReport, TranslationReviewReportItem } from '../types/api'
import { llmModelOptions } from '../constants/llm'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'
import { http } from '../api/http'
import { useToast } from '../composables/useToast'

const props = defineProps<{
  fileRecordId: string | null
  mergeViewId: string | null
  isMergeWorkbench: boolean
  onFocusSentence: (sentenceId: string, fileRecordId?: string) => void
  onActiveCountChange?: (count: number | null) => void
}>()

const toast = useToast()

// ─── Core state ───────────────────────────────────────────
const report = ref<TranslationReviewReport | null>(null)
const loading = ref(false)
const generating = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | null = null

// ─── Settings modal ───────────────────────────────────────
const showSettingsModal = ref(false)
const settingsCategories = ref<string[]>([])
const settingsSegmentScope = ref('all')
const settingsProvider = ref('auto')
const settingsModel = ref('')
const settingsWebVerify = ref('none')

// ─── Filter state ─────────────────────────────────────────
type FilterStatus = 'all' | 'open' | 'applied' | 'ignored' | 'rejected' | 'stale'
const filterStatus = ref<FilterStatus>('open')
const filterCategoryKey = ref('all')
const filterFileId = ref('all')
const viewMode = ref<'list' | 'grouped'>('list')

// ─── Action state ─────────────────────────────────────────
const busyItemId = ref<string | null>(null)
const batchBusy = ref(false)
const exportingDocx = ref(false)

// ─── Category metadata ────────────────────────────────────
const ALL_CATEGORIES = [
  { key: 'tense', label: '时态' },
  { key: 'symbols', label: '英文符号' },
  { key: 'casing', label: '大小写' },
  { key: 'number_format', label: '数字格式' },
  { key: 'proper_noun', label: '专有名词' },
  { key: 'fixed_syntax', label: '固定句法' },
  { key: 'noun_merge', label: '名词合并' },
  { key: 'omission', label: '避免漏译' },
  { key: 'comprehension', label: '原文理解' },
  { key: 'syntax_polish', label: '句法优化' },
]

const SCOPE_OPTIONS = [
  { value: 'all', label: '全部句段' },
  { value: 'translated_only', label: '仅有译文' },
  { value: 'unconfirmed_only', label: '仅未确认' },
  { value: 'confirmed_only', label: '仅已确认' },
]

const WEB_VERIFY_OPTIONS = [
  { value: 'none', label: '关闭' },
  { value: 'openrouter', label: 'OpenRouter 联网（需 OpenRouter 模型）' },
]

// ─── Computed ─────────────────────────────────────────────
const isRunning = computed(() => report.value?.status === 'running')

const overallProgress = computed(() =>
  isRunning.value ? (report.value?.progress?.overall_percent ?? 0) : 100
)

const currentCategoryLabel = computed(() => {
  if (!isRunning.value) return ''
  const key = report.value?.progress?.current_category
  return ALL_CATEGORIES.find(c => c.key === key)?.label ?? key ?? ''
})

const filteredItems = computed((): TranslationReviewReportItem[] => {
  const items = report.value?.items ?? []
  return items.filter(item => {
    if (filterStatus.value !== 'all' && item.status !== filterStatus.value) return false
    if (filterCategoryKey.value !== 'all' && item.category_key !== filterCategoryKey.value) return false
    if (props.isMergeWorkbench && filterFileId.value !== 'all' && item.file_record_id !== filterFileId.value) return false
    return true
  })
})

const groupedItems = computed(() => {
  if (viewMode.value !== 'grouped') return null
  const groups = new Map<string, { sentenceId: string; fileRecordId: string; fileName: string; sourceText: string; targetText: string; items: TranslationReviewReportItem[] }>()
  for (const item of filteredItems.value) {
    const key = `${item.file_record_id}:${item.sentence_id}`
    if (!groups.has(key)) {
      groups.set(key, { sentenceId: item.sentence_id, fileRecordId: item.file_record_id, fileName: item.file_name, sourceText: item.source_text, targetText: item.target_text, items: [] })
    }
    groups.get(key)!.items.push(item)
  }
  return [...groups.values()]
})

const categoryStats = computed(() => {
  const counts = (report.value?.category_counts ?? {}) as Record<string, number>
  return ALL_CATEGORIES.map(c => ({
    ...c,
    count: counts[c.key] ?? 0,
    agentStatus: report.value?.agent_runs?.find(r => r.category_key === c.key)?.status ?? 'pending',
  }))
})

const mergeViewFiles = computed(() => {
  if (!props.isMergeWorkbench || !report.value) return []
  const fileIds: string[] = report.value.file_ids ?? []
  const fileCounts = (report.value.file_counts ?? {}) as Record<string, number | string>
  return fileIds.map(fid => ({
    id: fid,
    name: report.value!.items.find(i => i.file_record_id === fid)?.file_name ?? fid,
    count: Number(fileCounts[fid] ?? 0),
  }))
})

const programBatchCount = computed(() =>
  (report.value?.items ?? []).filter(i => i.origin === 'program' && i.status === 'open' && i.apply_mode !== 'manual').length
)

const highConfBatchCount = computed(() =>
  (report.value?.items ?? []).filter(i =>
    i.status === 'open' && i.confidence === 'high' && i.apply_mode === 'anchor' &&
    i.locate_status !== 'unlocatable' && i.locate_status !== 'ambiguous' && i.severity !== 'suggestion'
  ).length
)

const reportMetaText = computed(() => {
  const r = report.value
  if (!r) return ''
  const parts = [
    r.total_segments ? `检查 ${r.total_segments} 句段` : '',
    r.web_search_requests > 0 ? `联网 ${r.web_search_requests} 次` : '',
    r.created_at ? r.created_at.slice(0, 16).replace('T', ' ') : '',
  ].filter(Boolean)
  return parts.join(' · ')
})

// ─── Data ────────────────────────────────────────────────
async function loadExistingReport() {
  loading.value = true
  try {
    let fetched: TranslationReviewReport | null = null
    if (props.isMergeWorkbench && props.mergeViewId) {
      fetched = await fetchMergeViewTranslationReviewReport(props.mergeViewId)
    } else if (props.fileRecordId) {
      fetched = await fetchFileTranslationReviewReport(props.fileRecordId)
    }
    report.value = fetched
    props.onActiveCountChange?.(fetched?.active_issue_count ?? null)
    if (fetched?.status === 'running') startPolling(fetched.id)
  } catch {}
  finally { loading.value = false }
}

async function startGeneration() {
  stopPolling()
  generating.value = true
  report.value = null
  showSettingsModal.value = false
  const options: TranslationReviewTaskOptions = {
    categories: settingsCategories.value.length < ALL_CATEGORIES.length ? settingsCategories.value : undefined,
    segmentScope: settingsSegmentScope.value,
    provider: settingsProvider.value,
    model: settingsModel.value || undefined,
    webVerify: settingsWebVerify.value,
  }
  try {
    let result: { task_id: string; report_id: string }
    if (props.isMergeWorkbench && props.mergeViewId) {
      result = await createMergeViewTranslationReviewTask(props.mergeViewId, options)
    } else if (props.fileRecordId) {
      result = await createFileTranslationReviewTask(props.fileRecordId, options)
    } else { generating.value = false; return }
    report.value = await fetchTranslationReviewReport(result.report_id)
    startPolling(result.report_id)
  } catch (e) {
    toast.error({ title: '翻译校对启动失败', message: String(e) })
    generating.value = false
  }
}

function startPolling(reportId: string) {
  stopPolling()
  pollTimer = setTimeout(() => pollProgress(reportId), 1500)
}

async function pollProgress(reportId: string) {
  try {
    const task = await fetchTranslationReviewTask(reportId)
    const full = await fetchTranslationReviewReport(reportId)
    report.value = full
    props.onActiveCountChange?.(full.active_issue_count ?? null)
    if (task.status === 'running') {
      pollTimer = setTimeout(() => pollProgress(reportId), 1500)
    } else {
      generating.value = false
      if (task.status === 'completed') {
        toast.show({ tone: 'success', title: '翻译校对完成', message: `发现 ${full.active_issue_count} 条问题` })
      } else if (task.status === 'partial_failed') {
        toast.show({ tone: 'warn', title: '翻译校对部分完成', message: `部分类别失败：${full.failed_categories?.join('、')}` })
      } else {
        toast.error({ title: '翻译校对失败', message: full.error_message || '未知错误' })
      }
    }
  } catch { generating.value = false }
}

function stopPolling() {
  if (pollTimer !== null) { clearTimeout(pollTimer); pollTimer = null }
}

onBeforeUnmount(stopPolling)

// ─── Actions ─────────────────────────────────────────────
async function handleApplyItem(item: TranslationReviewReportItem) {
  if (busyItemId.value) return
  busyItemId.value = item.id
  try { await applyTranslationReviewItem(item.id); await refreshReport(); toast.success('已应用') }
  catch (e) { toast.error({ title: '应用失败', message: String(e) }) }
  finally { busyItemId.value = null }
}

async function handleRestoreItem(item: TranslationReviewReportItem) {
  if (busyItemId.value) return
  busyItemId.value = item.id
  try { await restoreTranslationReviewItem(item.id); await refreshReport(); toast.success('已恢复') }
  catch (e) { toast.error({ title: '恢复失败', message: String(e) }) }
  finally { busyItemId.value = null }
}

async function handleRejectItem(item: TranslationReviewReportItem) {
  if (busyItemId.value) return
  busyItemId.value = item.id
  try { await rejectTranslationReviewItem(item.id); await refreshReport() }
  catch (e) { toast.error({ title: '操作失败', message: String(e) }) }
  finally { busyItemId.value = null }
}

async function handleIgnoreItem(item: TranslationReviewReportItem, ignored: boolean) {
  if (busyItemId.value) return
  busyItemId.value = item.id
  try { await setTranslationReviewItemsIgnored([item.id], ignored); await refreshReport() }
  catch (e) { toast.error({ title: '操作失败', message: String(e) }) }
  finally { busyItemId.value = null }
}

async function handleApplyProgramBatch() {
  if (batchBusy.value || !report.value) return
  batchBusy.value = true
  try {
    const res = await applyTranslationReviewBatch(report.value.id, { mode: 'program' })
    await refreshReport()
    toast.success(`已应用 ${res.applied_count} 条程序检查修改${res.stale_count ? `，${res.stale_count} 条需重查` : ''}`)
  } catch (e) { toast.error({ title: '批量失败', message: String(e) }) }
  finally { batchBusy.value = false }
}

async function handleApplyHighConfBatch() {
  if (batchBusy.value || !report.value) return
  batchBusy.value = true
  try {
    const res = await applyTranslationReviewBatch(report.value.id, { mode: 'high_confidence' })
    await refreshReport()
    toast.success(`已应用 ${res.applied_count} 条高置信度修改${res.stale_count ? `，${res.stale_count} 条需重查` : ''}`)
  } catch (e) { toast.error({ title: '批量失败', message: String(e) }) }
  finally { batchBusy.value = false }
}

async function handleUndoBatch() {
  if (batchBusy.value || !report.value) return
  batchBusy.value = true
  try {
    const res = await undoTranslationReviewBatch(report.value.id)
    await refreshReport()
    toast.success(`已撤销 ${res.restored_count} 条应用`)
  } catch (e) { toast.error({ title: '撤销失败', message: String(e) }) }
  finally { batchBusy.value = false }
}

async function handleExportDocx() {
  if (!report.value || exportingDocx.value) return
  exportingDocx.value = true
  try {
    const response = await http.get(`/translation-review-reports/${report.value.id}/export-docx`, { responseType: 'blob' })
    downloadBlob(response.data, resolveDownloadFilename(response.headers['content-disposition'], `review-${report.value.id}.docx`))
  } catch (e) { toast.error({ title: '导出失败', message: String(e) }) }
  finally { exportingDocx.value = false }
}

async function refreshReport() {
  if (!report.value) return
  try {
    report.value = await fetchTranslationReviewReport(report.value.id)
    props.onActiveCountChange?.(report.value?.active_issue_count ?? null)
  } catch {}
}

function jumpToSegment(item: TranslationReviewReportItem) {
  props.onFocusSentence(item.sentence_id, item.file_record_id)
}

function highlightedTarget(item: TranslationReviewReportItem): string {
  const text = item.target_text || ''
  if (!item.quote || item.locate_status === 'unlocatable' || item.locate_status === 'ambiguous') {
    return esc(text)
  }
  const start = item.quote_start >= 0 ? item.quote_start : text.indexOf(item.quote)
  const end   = item.quote_end >= 0 ? item.quote_end : start + item.quote.length
  if (start < 0 || end <= start) return esc(text)
  return esc(text.slice(0, start)) + `<mark class="tr-panel__mark">${esc(text.slice(start, end))}</mark>` + esc(text.slice(end))
}

/**
 * 分组视图专用：把一个句段下所有 item 的 quote 区间叠加高亮。
 * 处理流程：
 *   1. 收集所有可定位的 [start, end) 区间
 *   2. 对区间排序并合并重叠/相邻区间（防止 HTML 嵌套破坏）
 *   3. 一次性按区间序列渲染，未覆盖片段为普通文本
 *   4. 无任何可定位区间时直接返回 esc(text)
 */
function highlightedTargetMulti(items: TranslationReviewReportItem[]): string {
  if (!items.length) return ''
  const text = items[0].target_text || ''
  if (!text) return ''

  // 收集可定位区间
  const spans: Array<{ start: number; end: number }> = []
  for (const item of items) {
    if (!item.quote || item.locate_status === 'unlocatable' || item.locate_status === 'ambiguous') continue
    const start = item.quote_start >= 0 ? item.quote_start : text.indexOf(item.quote)
    const end   = item.quote_end >= 0   ? item.quote_end   : start + item.quote.length
    if (start >= 0 && end > start && end <= text.length) {
      spans.push({ start, end })
    }
  }

  if (!spans.length) return esc(text)

  // 排序 + 合并重叠区间
  spans.sort((a, b) => a.start - b.start || a.end - b.end)
  const merged: Array<{ start: number; end: number }> = [{ ...spans[0] }]
  for (let i = 1; i < spans.length; i++) {
    const last = merged[merged.length - 1]
    const cur  = spans[i]
    if (cur.start <= last.end) {
      // 重叠或相邻 → 合并
      last.end = Math.max(last.end, cur.end)
    } else {
      merged.push({ ...cur })
    }
  }

  // 渲染
  let html = ''
  let cursor = 0
  for (const { start, end } of merged) {
    if (start > cursor) html += esc(text.slice(cursor, start))
    html += `<mark class="tr-panel__mark">${esc(text.slice(start, end))}</mark>`
    cursor = end
  }
  if (cursor < text.length) html += esc(text.slice(cursor))
  return html
}

function esc(s: string) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function severityClass(s: string) {
  return s === 'error' ? 'is-error' : s === 'warning' ? 'is-warning' : 'is-suggestion'
}

function canApply(item: TranslationReviewReportItem) {
  return item.status === 'open' && item.apply_mode !== 'manual'
    && item.locate_status !== 'unlocatable' && item.locate_status !== 'ambiguous'
}

function segLabel(item: TranslationReviewReportItem) {
  return item.display_index >= 0 ? String(item.display_index + 1) : item.sentence_id
}

// Init
loadExistingReport()
settingsCategories.value = ALL_CATEGORIES.map(c => c.key)
</script>

<template>
  <div class="workbench-bottom-drawer__qa">
    <!-- Header — mirrors number-check header exactly -->
    <div class="workbench-bottom-drawer__header workbench-bottom-drawer__header--qa">
      <div class="workbench-bottom-drawer__header-lead">
        <div class="section-title section-title--tight">翻译内容校对</div>
        <p class="panel-subtitle">按广东地方志翻译规则检查时态、符号、大小写、数字、专有名词等 10 个类别。</p>
      </div>

      <!-- Summary stats -->
      <div v-if="report" class="term-qa-dialog__summary">
        <span class="term-qa-stat">
          <em class="term-qa-stat__value">{{ report.active_issue_count }}</em>
          <span class="term-qa-stat__label">待处理</span>
        </span>
        <span class="term-qa-stat is-muted">
          <em class="term-qa-stat__value">{{ report.ignored_count }}</em>
          <span class="term-qa-stat__label">已忽略</span>
        </span>
        <span class="term-qa-dialog__meta">{{ reportMetaText }}</span>
      </div>

      <!-- Actions — 与数字专检完全相同的一行布局 -->
      <div class="term-qa-dialog__actions">
        <!-- 设置 -->
        <button
          class="button button--ghost term-qa-dialog__action-button"
          type="button"
          title="翻译校对设置"
          @click="showSettingsModal = true"
        >
          <Settings :size="14" />
          设置
        </button>

        <!-- 类别筛选 select（与数字专检筛选 select 完全一致） -->
        <select
          v-if="report"
          v-model="filterCategoryKey"
          class="term-qa-dialog__filter-select"
          title="按类别筛选"
        >
          <option value="all">全部类别</option>
          <option v-for="cat in categoryStats" :key="cat.key" :value="cat.key">
            {{ cat.label }}{{ cat.count > 0 ? ` (${cat.count})` : '' }}
          </option>
        </select>

        <!-- 状态筛选 select -->
        <select
          v-if="report"
          v-model="filterStatus"
          class="term-qa-dialog__filter-select"
          title="按状态筛选"
        >
          <option value="all">全部状态</option>
          <option value="open">待处理</option>
          <option value="applied">已应用</option>
          <option value="ignored">已忽略</option>
          <option value="rejected">已拒绝</option>
          <option value="stale">需重查</option>
        </select>

        <!-- 文件筛选（仅合并视图） -->
        <select
          v-if="report && isMergeWorkbench && mergeViewFiles.length > 1"
          v-model="filterFileId"
          class="term-qa-dialog__filter-select"
          title="按文件筛选"
        >
          <option value="all">全部文件</option>
          <option v-for="f in mergeViewFiles" :key="f.id" :value="f.id">
            {{ f.name }}{{ f.count > 0 ? ` (${f.count})` : '' }}
          </option>
        </select>

        <!-- 视图切换 -->
        <div v-if="report" class="tr-panel__view-toggle" role="group" aria-label="显示方式">
          <button class="tr-panel__view-btn" :class="{ 'is-active': viewMode === 'list' }" type="button" title="按问题列表" @click="viewMode = 'list'">
            <List :size="13" />
          </button>
          <button class="tr-panel__view-btn" :class="{ 'is-active': viewMode === 'grouped' }" type="button" title="按句段分组" @click="viewMode = 'grouped'">
            <Layers :size="13" />
          </button>
        </div>

        <!-- 应用程序项 -->
        <button
          v-if="report && !isRunning"
          class="button button--ghost term-qa-dialog__action-button"
          type="button"
          :disabled="programBatchCount === 0 || batchBusy"
          title="应用所有程序检查项（确定性规则，最安全）"
          @click="handleApplyProgramBatch"
        >
          <Loader2 v-if="batchBusy" class="lucide-spin" :size="14" />
          应用程序项{{ programBatchCount > 0 ? ` ${programBatchCount}` : '' }}
        </button>

        <!-- 高置信度批量 -->
        <button
          v-if="report && !isRunning"
          class="button button--ghost term-qa-dialog__action-button"
          type="button"
          :disabled="highConfBatchCount === 0 || batchBusy"
          title="应用所有高置信度修改"
          @click="handleApplyHighConfBatch"
        >
          高置信度{{ highConfBatchCount > 0 ? ` ${highConfBatchCount}` : '' }}
        </button>

        <!-- 撤销批量 -->
        <button
          v-if="report && !isRunning"
          class="button button--ghost term-qa-dialog__action-button"
          type="button"
          :disabled="batchBusy"
          title="撤销上一次批量应用"
          @click="handleUndoBatch"
        >
          <RotateCcw :size="14" />
          撤销批量
        </button>

        <!-- 导出 Word -->
        <button
          v-if="report && !isRunning"
          class="button button--ghost term-qa-dialog__action-button"
          type="button"
          :disabled="exportingDocx"
          title="导出 Word 校对报告"
          @click="handleExportDocx"
        >
          <Loader2 v-if="exportingDocx" class="lucide-spin" :size="14" />
          <Download v-else :size="14" />
          导出
        </button>

        <!-- 重新检查 / 开始检查 -->
        <button
          class="button button--ghost term-qa-dialog__action-button"
          type="button"
          :disabled="generating && !isRunning"
          @click="startGeneration"
        >
          <Loader2 v-if="generating || isRunning" class="lucide-spin" :size="14" />
          <Bot v-else :size="14" />
          {{ report ? '重新检查' : '开始检查' }}
        </button>
      </div>
    </div>

    <!-- Progress bar (generating) — mirrors number-check progress -->
    <div v-if="(isRunning || generating) && report" class="number-check__progress">
      <div class="number-check__progress-head">
        <span class="number-check__progress-text">
          正在检查{{ currentCategoryLabel ? `：${currentCategoryLabel}` : '' }}
        </span>
        <span class="number-check__progress-pct">{{ overallProgress }}%</span>
      </div>
      <div class="number-check__progress-track">
        <div class="number-check__progress-fill" :style="{ width: `${overallProgress}%` }" />
      </div>
    </div>

    <!-- ── Loading / empty ── -->
    <div v-if="loading" class="empty-state">
      <Loader2 class="lucide-spin" :size="28" />正在加载翻译校对结果
    </div>
    <div v-else-if="generating && !report" class="empty-state">
      <Loader2 class="lucide-spin" :size="28" />正在启动检查…
    </div>
    <div v-else-if="!report" class="empty-state">
      暂无翻译校对结果
      <button class="button" type="button" @click="startGeneration">
        <Bot :size="14" />开始检查
      </button>
    </div>

    <!-- ── List view (mirrors number-check table layout) ── -->
    <div v-else-if="viewMode === 'list'" class="term-qa-dialog__table-wrap">
      <div v-if="filteredItems.length === 0" class="empty-state">当前筛选条件下无结果。</div>
      <table v-else class="term-qa-dialog__table number-check__table">
        <thead>
          <tr>
            <th class="term-qa-dialog__col-segment">句段</th>
            <th v-if="isMergeWorkbench" class="term-qa-dialog__col-file">文件</th>
            <th style="width:80px">类别</th>
            <th style="width:54px">规则</th>
            <th class="number-check__col-reason">问题说明</th>
            <th class="number-check__col-target">译文（问题高亮）</th>
            <th style="width:50px">置信</th>
            <th class="number-check__col-status">状态</th>
            <th class="number-check__col-action">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in filteredItems"
            :key="item.id"
            class="term-qa-dialog__row"
            :class="{ 'is-ignored': item.status === 'ignored' || item.status === 'rejected' }"
            tabindex="0"
            @click="jumpToSegment(item)"
            @keydown.enter.prevent="jumpToSegment(item)"
          >
            <td>
              <span class="term-qa-dialog__segment">
                <Loader2 v-if="busyItemId === item.id" class="lucide-spin" :size="13" />
                {{ segLabel(item) }}
              </span>
            </td>
            <td v-if="isMergeWorkbench" class="number-check__cell" :title="item.file_name">{{ item.file_name }}</td>
            <td>
              <span class="number-check__status-tag" :class="severityClass(item.severity)">
                {{ item.category_label }}
              </span>
            </td>
            <td class="hint-text">{{ item.rule_ref }}</td>
            <td class="number-check__cell">
              <div class="number-check__reason">{{ item.reason }}</div>
              <div v-if="item.suggested_value" class="number-check__nums">建议：{{ item.suggested_value }}</div>
            </td>
            <td class="number-check__cell">
              <div class="number-check__fix" v-html="highlightedTarget(item)" />
            </td>
            <td class="hint-text">{{ item.confidence === 'high' ? '高' : item.confidence === 'medium' ? '中' : '低' }}</td>
            <td class="number-check__cell">
              <div class="number-check__status">
                <span
                  class="number-check__status-tag"
                  :class="{
                    'number-check__status-tag--muted': item.status === 'open',
                    'number-check__status-tag--done':  item.status === 'applied',
                    'number-check__status-tag--warn':  item.status === 'stale',
                    'number-check__status-tag--ok':    item.status === 'ignored' || item.status === 'rejected',
                  }"
                >
                  {{ item.status === 'open' ? '待处理' : item.status === 'applied' ? '已应用' : item.status === 'ignored' ? '已忽略' : item.status === 'rejected' ? '已拒绝' : '需重查' }}
                </span>
              </div>
            </td>
            <td @click.stop>
              <div class="term-qa-dialog__inline-actions number-check__actions">
                <button
                  v-if="item.status === 'open' && canApply(item)"
                  class="button button--ghost term-qa-dialog__inline-action number-check__action"
                  type="button"
                  :disabled="busyItemId === item.id"
                  title="按建议修改译文"
                  @click="handleApplyItem(item)"
                >应用</button>
                <button
                  v-if="item.status === 'applied'"
                  class="button button--ghost term-qa-dialog__inline-action number-check__action"
                  type="button"
                  :disabled="busyItemId === item.id"
                  @click="handleRestoreItem(item)"
                >恢复</button>
                <button
                  v-if="item.status === 'open'"
                  class="button button--ghost term-qa-dialog__inline-action number-check__action"
                  type="button"
                  :disabled="busyItemId === item.id"
                  @click="handleRejectItem(item)"
                >拒绝</button>
                <button
                  class="button button--ghost term-qa-dialog__inline-action number-check__action"
                  type="button"
                  :disabled="busyItemId === item.id"
                  @click="handleIgnoreItem(item, item.status !== 'ignored')"
                >{{ item.status === 'ignored' ? '恢复' : '忽略' }}</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- ── Grouped view ── -->
    <div v-else-if="viewMode === 'grouped'" class="term-qa-dialog__table-wrap">
      <div v-if="!groupedItems || groupedItems.length === 0" class="empty-state">当前筛选条件下无结果。</div>
      <div v-else>
        <div
          v-for="group in groupedItems"
          :key="`${group.fileRecordId}:${group.sentenceId}`"
          class="tr-panel__group"
        >
          <div
            class="tr-panel__group-header term-qa-dialog__row"
            tabindex="0"
            @click="jumpToSegment(group.items[0])"
            @keydown.enter.prevent="jumpToSegment(group.items[0])"
          >
            <span class="term-qa-dialog__segment">{{ segLabel(group.items[0]) }}</span>
            <span v-if="isMergeWorkbench" class="hint-text">{{ group.fileName }}</span>
            <span class="hint-text">{{ group.items.length }} 个问题</span>
          </div>
          <div class="tr-panel__group-source hint-text">{{ group.sourceText?.slice(0, 160) }}</div>
          <div class="tr-panel__group-target" v-html="highlightedTargetMulti(group.items)" />
          <div
            v-for="item in group.items"
            :key="item.id"
            class="tr-panel__group-item"
          >
            <span class="number-check__status-tag" :class="severityClass(item.severity)">{{ item.category_label }}</span>
            <span class="hint-text">§{{ item.rule_ref }}</span>
            <span>{{ item.reason }}</span>
            <span v-if="item.suggested_value" class="hint-text">→ {{ item.suggested_value }}</span>
            <!-- 定位预览：当 quote 存在且可定位时，在问题行尾显示对应片段 -->
            <span
              v-if="item.quote && item.locate_status !== 'unlocatable' && item.locate_status !== 'ambiguous'"
              class="tr-panel__quote-preview"
              :title="`问题片段：${item.quote}`"
            >「{{ item.quote.slice(0, 40) }}{{ item.quote.length > 40 ? '…' : '' }}」</span>
            <div class="term-qa-dialog__inline-actions number-check__actions" @click.stop>
              <button v-if="item.status === 'open' && canApply(item)" class="button button--ghost term-qa-dialog__inline-action number-check__action" type="button" :disabled="busyItemId === item.id" @click="handleApplyItem(item)">应用</button>
              <button v-if="item.status === 'applied'" class="button button--ghost term-qa-dialog__inline-action number-check__action" type="button" @click="handleRestoreItem(item)">恢复</button>
              <button v-if="item.status === 'open'" class="button button--ghost term-qa-dialog__inline-action number-check__action" type="button" @click="handleRejectItem(item)">拒绝</button>
              <button class="button button--ghost term-qa-dialog__inline-action number-check__action" type="button" @click="handleIgnoreItem(item, item.status !== 'ignored')">{{ item.status === 'ignored' ? '恢复' : '忽略' }}</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ── Settings Modal (mirrors showNumberCheckSettings modal) ── -->
  <Modal
    :open="showSettingsModal"
    title="翻译内容校对设置"
    width="min(500px, calc(100vw - 32px))"
    @close="showSettingsModal = false"
  >
    <div class="number-check__settings-dialog">
      <div class="number-check__settings-group">
        <div class="number-check__settings-label">检查范围</div>
        <label v-for="o in SCOPE_OPTIONS" :key="o.value" class="number-check__settings-option">
          <input type="radio" :value="o.value" v-model="settingsSegmentScope" />
          {{ o.label }}
        </label>
      </div>
      <div class="number-check__settings-group">
        <div class="number-check__settings-label">AI 模型</div>
        <select class="term-qa-dialog__filter-select" v-model="settingsModel">
          <option value="">使用配置默认模型</option>
          <option v-for="m in llmModelOptions" :key="m.id" :value="m.id">{{ m.name }}</option>
        </select>
      </div>
      <div class="number-check__settings-group">
        <div class="number-check__settings-label">联网查证</div>
        <label v-for="o in WEB_VERIFY_OPTIONS" :key="o.value" class="number-check__settings-option">
          <input type="radio" :value="o.value" v-model="settingsWebVerify" />
          {{ o.label }}
        </label>
        <p v-if="settingsWebVerify === 'openrouter'" class="hint-text" style="margin-top:4px">
          联网查证仅对「专有名词」类别生效，会产生额外搜索费用，需使用 OpenRouter 模型。
        </p>
      </div>
      <div class="number-check__settings-group">
        <div class="number-check__settings-label">启用类别</div>
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:6px 12px">
          <label v-for="c in ALL_CATEGORIES" :key="c.key" class="number-check__settings-option">
            <input type="checkbox" :value="c.key" v-model="settingsCategories" />
            {{ c.label }}
          </label>
        </div>
      </div>
      <p class="hint-text">设置在下次「开始检查 / 重新检查」时生效。</p>
    </div>
    <template #footer>
      <button class="button button--primary" type="button" @click="showSettingsModal = false">完成</button>
    </template>
  </Modal>
</template>

<style scoped>
/* View toggle */
.tr-panel__view-toggle {
  display: inline-flex;
  border: 1px solid #c5d3d9;
  border-radius: 4px;
  background: #fff;
  overflow: hidden;
}

.tr-panel__view-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
}

.tr-panel__view-btn + .tr-panel__view-btn {
  border-left: 1px solid #c5d3d9;
}

.tr-panel__view-btn:hover { background: var(--surface-muted); }

.tr-panel__view-btn.is-active {
  background: var(--brand-700);
  color: #fff;
}

/* Target cell highlight */
:deep(.tr-panel__mark) {
  background: #fff176;
  color: #8a6d00;
  border-radius: 2px;
  padding: 0 2px;
  font-weight: 600;
}

/* Grouped view */
.tr-panel__group {
  padding: 8px 12px 10px;
  border-bottom: 1px solid var(--line-soft);
}

.tr-panel__group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}

.tr-panel__group-header:hover { color: var(--brand-700); }

.tr-panel__group-source {
  margin: 3px 0 1px;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tr-panel__group-target {
  font-size: 12px;
  margin-bottom: 5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tr-panel__group-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
  padding: 3px 0;
  font-size: 12px;
  border-top: 1px dashed var(--line-soft);
}

/* Severity tags (reuse number-check colors) */
.number-check__status-tag.is-error    { background: #fdecec; color: #c0392b; }
.number-check__status-tag.is-warning  { background: #fff8e1; color: #b45309; }
.number-check__status-tag.is-suggestion { background: #e8f0fe; color: #1a5fb4; }

/* Quote preview chip in grouped item row */
.tr-panel__quote-preview {
  display: inline-block;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 1px 5px;
  border-radius: 3px;
  background: #fff9e5;
  color: #7a5a00;
  border: 1px solid #f0d080;
  font-size: 11px;
  vertical-align: middle;
  cursor: default;
}
</style>
