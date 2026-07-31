<script setup lang="ts">
import {
  AlertCircle, Bot, Download, Layers, List, Loader2, RotateCcw, Settings, Upload,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, ref } from 'vue'
import Modal from './base/Modal.vue'
import {
  applyTranslationReviewBatch, applyTranslationReviewItem,
  createFileTranslationReviewTask, createMergeViewTranslationReviewTask,
  fetchFileTranslationReviewReport, fetchMergeViewTranslationReviewReport,
  fetchTranslationReviewReport, fetchTranslationReviewTask,
  fetchTranslationRulesInfo, uploadTranslationRules, deleteTranslationRules,
  rerunTranslationReview, restoreTranslationReviewItem,
  setTranslationReviewItemsIgnored, undoTranslationReviewBatch,
  type TranslationReviewTaskOptions, type TranslationRulesInfo,
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
  projectId: string | null
  onFocusSentence: (sentenceId: string, fileRecordId?: string) => void
  onActiveCountChange?: (count: number | null) => void
}>()

const toast = useToast()

// ─── Core state ───────────────────────────────────────────
const report = ref<TranslationReviewReport | null>(null)
const loading = ref(false)
const generating = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | null = null

// ─── Rules file state ─────────────────────────────────────
const rulesInfo = ref<TranslationRulesInfo | null>(null)
const loadingRules = ref(false)
const uploadingRules = ref(false)
const rulesFileInput = ref<HTMLInputElement | null>(null)

// ─── Settings modal ───────────────────────────────────────
const showSettingsModal = ref(false)
const settingsSegmentScope = ref('all')
const settingsProvider = ref('auto')
const settingsModel = ref('')

// ─── Filter state ─────────────────────────────────────────
type FilterStatus = 'all' | 'open' | 'applied' | 'ignored' | 'rejected' | 'stale'
const filterStatus = ref<FilterStatus>('open')
const filterCategoryKey = ref('all')
const filterFileId = ref('all')
const viewMode = ref<'list' | 'grouped'>('list')

// ─── Action state ─────────────────────────────────────────
const busyItemId = ref<string | null>(null)
const activeItemId = ref<string | null>(null)
const batchBusy = ref(false)
const exportingDocx = ref(false)

const SCOPE_OPTIONS = [
  { value: 'all', label: '全部句段' },
  { value: 'translated_only', label: '仅有译文' },
  { value: 'unconfirmed_only', label: '仅未确认' },
  { value: 'confirmed_only', label: '仅已确认' },
]

// ─── Computed ─────────────────────────────────────────────
const isRunning = computed(() => report.value?.status === 'running')

const hasRules = computed(() => (rulesInfo.value?.char_count ?? 0) > 0)

const overallProgress = computed(() =>
  isRunning.value ? (report.value?.progress?.overall_percent ?? 0) : 100
)

const progressFileLabel = computed(() => {
  if (!isRunning.value) return ''
  return report.value?.progress?.current_file_name ?? ''
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

// categoryStats: derived from LLM-returned categories (not hardcoded)
const categoryStats = computed(() => {
  const counts = (report.value?.category_counts ?? {}) as Record<string, number>
  return Object.entries(counts)
    .sort(([, a], [, b]) => b - a)
    .map(([key, count]) => ({ key, label: key, count }))
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
    // Load rules info in parallel
    if (props.projectId) {
      loadingRules.value = true
      fetchTranslationRulesInfo(props.projectId)
        .then(info => { rulesInfo.value = info })
        .catch(() => {})
        .finally(() => { loadingRules.value = false })
    }
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
    segmentScope: settingsSegmentScope.value,
    provider: settingsProvider.value,
    model: settingsModel.value || undefined,
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

async function handleIgnoreItem(item: TranslationReviewReportItem, ignored: boolean) {
  if (busyItemId.value) return
  busyItemId.value = item.id
  try { await setTranslationReviewItemsIgnored([item.id], ignored); await refreshReport() }
  catch (e) { toast.error({ title: '操作失败', message: String(e) }) }
  finally { busyItemId.value = null }
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

function triggerRulesUpload() {
  rulesFileInput.value?.click()
}

async function handleRulesFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  ;(event.target as HTMLInputElement).value = ''
  if (!file || !props.projectId) return
  uploadingRules.value = true
  try {
    rulesInfo.value = await uploadTranslationRules(props.projectId, file)
    toast.success(`规则文件「${file.name}」上传成功，共 ${rulesInfo.value.char_count} 个字符。`)
  } catch (e) { toast.error({ title: '上传失败', message: String(e) }) }
  finally { uploadingRules.value = false }
}

async function handleDeleteRules() {
  if (!props.projectId) return
  try {
    await deleteTranslationRules(props.projectId)
    rulesInfo.value = null
    toast.success('已清除规则文件。')
  } catch (e) { toast.error({ title: '清除失败', message: String(e) }) }
}

async function refreshReport() {
  if (!report.value) return
  try {
    report.value = await fetchTranslationReviewReport(report.value.id)
    props.onActiveCountChange?.(report.value?.active_issue_count ?? null)
  } catch {}
}

function jumpToSegment(item: TranslationReviewReportItem) {
  activeItemId.value = item.id
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
</script>

<template>
  <div class="workbench-bottom-drawer__qa">
    <!-- Header — mirrors number-check header exactly -->
    <div class="workbench-bottom-drawer__header workbench-bottom-drawer__header--qa">
      <div class="workbench-bottom-drawer__header-lead">
        <div class="section-title section-title--tight">翻译内容校对</div>
        <p class="panel-subtitle">基于项目规则文件，由 AI 逐批检查译文并自动归类问题。</p>
        <div v-if="rulesInfo && rulesInfo.char_count > 0" class="tr-panel__rules-line">
          <span class="tr-panel__rules-tag">📄 {{ rulesInfo.filename || '已上传' }} · {{ rulesInfo.char_count.toLocaleString() }} 字符</span>
        </div>
        <div v-else-if="!loadingRules" class="tr-panel__rules-line">
          <span class="tr-panel__rules-tag tr-panel__rules-tag--warn">⚠ 未上传规则文件</span>
        </div>
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
  
    <!-- Progress bar (generating) -->
    <div v-if="(isRunning || generating) && report" class="number-check__progress">
      <div class="number-check__progress-head">
        <span class="number-check__progress-text">
          正在检查{{ progressFileLabel ? `：${progressFileLabel}` : '' }}（每批 50 条句段）
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
            :class="{
              'is-current': activeItemId === item.id,
              'is-ignored': item.status === 'ignored' || item.status === 'rejected',
            }"
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
            :class="{ 'is-current': group.items.some(item => activeItemId === item.id) }"
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
              <button class="button button--ghost term-qa-dialog__inline-action number-check__action" type="button" @click="handleIgnoreItem(item, item.status !== 'ignored')">{{ item.status === 'ignored' ? '恢复' : '忽略' }}</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
  
  <!-- Settings Modal -->
  <Modal
    :open="showSettingsModal"
    title="翻译内容校对设置"
    width="min(500px, calc(100vw - 32px))"
    @close="showSettingsModal = false"
  >
    <div class="number-check__settings-dialog">
      <!-- 规则文件管理 -->
      <div class="number-check__settings-group">
        <div class="number-check__settings-label">翻译规则文件（项目级）</div>
        <div v-if="rulesInfo && rulesInfo.char_count > 0" class="tr-panel__rules-info">
          <span class="tr-panel__rules-filename">📄 {{ rulesInfo.filename || '已上传' }}</span>
          <span class="hint-text">{{ rulesInfo.char_count }} 字符 · {{ rulesInfo.updated_at?.slice(0, 10) ?? '' }}</span>
          <span v-if="rulesInfo.preview" class="tr-panel__rules-preview hint-text">{{ rulesInfo.preview }}…</span>
        </div>
        <div v-else class="hint-text" style="color:#b45309">⚠ 当前项目尚未上传翻译规则文件，无法开始校对。</div>
        <div class="tr-panel__rules-actions">
          <button
            class="button button--ghost term-qa-dialog__action-button"
            type="button"
            :disabled="uploadingRules || !projectId"
            @click="triggerRulesUpload"
          >
            <Loader2 v-if="uploadingRules" class="lucide-spin" :size="14" />
            <Upload v-else :size="14" />
            {{ rulesInfo?.char_count ? '更换规则文件' : '上传规则文件' }}
          </button>
          <button
            v-if="rulesInfo?.char_count"
            class="button button--ghost term-qa-dialog__action-button"
            type="button"
            @click="handleDeleteRules"
          >清除</button>
        </div>
        <p class="hint-text">支持 .docx / .txt / .md 格式，文件内容为翻译规则说明，将整体喂给 AI 作为检查依据。</p>
        <input
          ref="rulesFileInput"
          type="file"
          accept=".docx,.txt,.md,.markdown"
          style="display:none"
          @change="handleRulesFileChange"
        />
      </div>
      <!-- 检查范围 -->
      <div class="number-check__settings-group">
        <div class="number-check__settings-label">检查范围</div>
        <label v-for="o in SCOPE_OPTIONS" :key="o.value" class="number-check__settings-option">
          <input type="radio" :value="o.value" v-model="settingsSegmentScope" />
          {{ o.label }}
        </label>
      </div>
      <!-- AI 模型 -->
      <div class="number-check__settings-group">
        <div class="number-check__settings-label">AI 模型</div>
        <select class="term-qa-dialog__filter-select" v-model="settingsModel">
          <option value="">使用配置默认模型</option>
          <option v-for="m in llmModelOptions" :key="m.id" :value="m.id">{{ m.name }}</option>
        </select>
      </div>
      <p class="hint-text">每批 50 条句段发送一次 AI 请求；规则文件越长每次消耗 token 越多，建议控制在 2 万字符以内。</p>
  </div>
      <template #footer>
        <button class="button button--primary" type="button" @click="showSettingsModal = false">完成</button>
      </template>
    </Modal>

</template>

<style scoped>
/* Header single-row layout matching number-check */
.workbench-bottom-drawer__header--qa {
  flex-wrap: nowrap !important;
  align-items: flex-start;
  gap: 10px;
  padding-right: 34px;
}

.workbench-bottom-drawer__header--qa .workbench-bottom-drawer__header-lead {
  flex: 0 0 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.workbench-bottom-drawer__header--qa .panel-subtitle {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
}

.workbench-bottom-drawer__header--qa .term-qa-dialog__summary {
  flex: 1 1 auto;
  min-width: 0;
  align-self: center;
}

.workbench-bottom-drawer__header--qa .term-qa-dialog__actions {
  flex: 0 0 auto;
  margin-left: auto;
  align-self: center;
  flex-wrap: nowrap;
}

/* Compact action buttons matching number-check size */
.term-qa-dialog__actions .term-qa-dialog__action-button {
  min-height: 26px;
  padding: 3px 7px;
  font-size: 12px;
}

.term-qa-dialog__actions .term-qa-dialog__filter-select {
  min-height: 26px;
  padding: 2px 7px;
  font-size: 12px;
}

/* Rules line in header-lead */
.tr-panel__rules-line {
  margin-top: 1px;
}

.tr-panel__rules-line .tr-panel__rules-tag {
  margin-left: 0;
}

/* Header: vertical introduction, centered summary, top-right actions */
.workbench-bottom-drawer__header.workbench-bottom-drawer__header--qa {
  display: grid !important;
  grid-template-columns: minmax(240px, 1fr) auto auto;
  align-items: start;
  column-gap: 10px;
  flex-wrap: nowrap !important;
}

.workbench-bottom-drawer__header--qa > .workbench-bottom-drawer__header-lead {
  grid-column: 1;
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.workbench-bottom-drawer__header--qa .panel-subtitle {
  margin: 0;
  overflow: hidden;
  color: var(--text-muted, #64748b);
  font-size: 12px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tr-panel__rules-line {
  min-width: 0;
  overflow: hidden;
  line-height: 1.25;
}

.tr-panel__rules-line .tr-panel__rules-tag {
  margin-left: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workbench-bottom-drawer__header--qa > .term-qa-dialog__summary {
  grid-column: 2;
  align-self: center;
  min-width: 0;
  flex-wrap: nowrap;
}

.workbench-bottom-drawer__header--qa > .term-qa-dialog__actions {
  grid-column: 3;
  justify-self: end;
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  margin-left: 0;
  flex-wrap: nowrap !important;
  overflow-x: auto;
  scrollbar-width: none;
}

.workbench-bottom-drawer__header--qa > .term-qa-dialog__actions::-webkit-scrollbar {
  display: none;
}

/* Summary stats match the number-check header. */
.workbench-bottom-drawer__header--qa .term-qa-dialog__summary {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: nowrap;
  color: var(--text-muted, #64748b);
  font-size: 12px;
}

.workbench-bottom-drawer__header--qa .term-qa-stat {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  min-height: 22px;
  padding: 1px 6px;
  border: 1px solid #d7e7e3;
  border-radius: 4px;
  background: #f3faf8;
  white-space: nowrap;
}

.workbench-bottom-drawer__header--qa .term-qa-stat.is-muted {
  border-color: var(--border-color, #dbe3ea);
  background: var(--surface-muted, #f8fafc);
}

.workbench-bottom-drawer__header--qa .term-qa-stat__value {
  font-size: 13px;
  font-weight: 700;
  font-style: normal;
  color: #b4530f;
}

.workbench-bottom-drawer__header--qa .term-qa-stat.is-muted .term-qa-stat__value {
  color: #2f4a53;
}

.workbench-bottom-drawer__header--qa .term-qa-stat__label {
  color: var(--text-muted, #64748b);
  font-size: 11px;
}

.workbench-bottom-drawer__header--qa .term-qa-dialog__meta {
  min-width: 0;
  color: #8694a0;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Compact controls match the number-check toolbar. */
.workbench-bottom-drawer__header--qa .term-qa-dialog__action-button,
.workbench-bottom-drawer__header--qa .term-qa-dialog__filter-select {
  min-height: 26px;
  height: 26px;
  padding: 3px 7px;
  border: 1px solid #c5d6de;
  border-radius: 4px;
  background: #fff;
  color: #2b3a40;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
}

.workbench-bottom-drawer__header--qa .term-qa-dialog__action-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  box-shadow: none;
}

.workbench-bottom-drawer__header--qa .term-qa-dialog__action-button:hover:not(:disabled),
.workbench-bottom-drawer__header--qa .term-qa-dialog__filter-select:hover:not(:disabled) {
  border-color: #8db9c4;
  background: #edf8f6;
  color: #0b6658;
}

.workbench-bottom-drawer__header--qa .term-qa-dialog__action-button:focus-visible,
.workbench-bottom-drawer__header--qa .term-qa-dialog__filter-select:focus-visible {
  outline: 2px solid rgba(13, 122, 104, 0.2);
  outline-offset: 1px;
}

/* Keep the list table grid visible, matching number-check. */
.term-qa-dialog__table-wrap {
  border: 1px solid #dce5ea;
  border-radius: 4px;
  background: #fff;
}

.term-qa-dialog__table.number-check__table {
  width: 100%;
  border: 1px solid #dce5ea;
  border-collapse: collapse;
  font-size: 12px;
  line-height: 1.4;
}

.term-qa-dialog__table.number-check__table th,
.term-qa-dialog__table.number-check__table td {
  border: 1px solid #dce5ea !important;
  padding: 6px 8px;
  vertical-align: top;
}

.term-qa-dialog__table.number-check__table thead th {
  background: #f3f7f9;
  color: #50636b;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.term-qa-dialog__table.number-check__table .term-qa-dialog__row:hover,
.term-qa-dialog__table.number-check__table .term-qa-dialog__row:focus-visible,
.term-qa-dialog__table.number-check__table .term-qa-dialog__row.is-current,
.tr-panel__group-header.is-current {
  background: #e4f3ff;
  outline: none;
  box-shadow: inset 3px 0 0 #2a7fb8;
}

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

/* Rules file info in settings */
.tr-panel__rules-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  background: #e9f5f0;
  color: #0a6b55;
  font-size: 11px;
  margin-left: 6px;
}

.tr-panel__rules-tag--warn {
  background: #fff8e1;
  color: #b45309;
}

.tr-panel__rules-info {
  display: grid;
  gap: 3px;
  padding: 6px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 5px;
  background: var(--surface-muted);
  margin-bottom: 6px;
}

.tr-panel__rules-filename {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.tr-panel__rules-preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
  font-size: 11px;
}

.tr-panel__rules-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
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
