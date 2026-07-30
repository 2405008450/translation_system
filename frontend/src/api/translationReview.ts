import { http } from './http'
import type { TranslationReviewReport } from '../types/api'

export interface TranslationReviewTaskOptions {
  categories?: string[]
  segmentScope?: string
  provider?: string
  model?: string
  webVerify?: string
}

export interface ApplyBatchOptions {
  mode: 'program' | 'high_confidence' | 'category' | 'selected'
  categoryKey?: string
  itemIds?: string[]
}

export interface ApplyBatchResult {
  applied_count: number
  stale_count: number
  skipped_count: number
  apply_batch_id: string
}

export interface UndoBatchResult {
  restored_count: number
}

function buildTaskBody(options: TranslationReviewTaskOptions = {}) {
  return {
    categories: options.categories ?? null,
    segment_scope: options.segmentScope ?? 'all',
    provider: options.provider ?? 'auto',
    model: options.model ?? '',
    web_verify: options.webVerify ?? 'none',
  }
}

// ─── 任务创建 + 进度轮询 ─────────────────────────────────

export async function createFileTranslationReviewTask(
  fileRecordId: string,
  options: TranslationReviewTaskOptions = {},
) {
  const { data } = await http.post<{ task_id: string; report_id: string }>(
    `/file-records/${fileRecordId}/translation-review-tasks`,
    buildTaskBody(options),
  )
  return data
}

export async function createMergeViewTranslationReviewTask(
  viewId: string,
  options: TranslationReviewTaskOptions = {},
) {
  const { data } = await http.post<{ task_id: string; report_id: string }>(
    `/merge-views/${viewId}/translation-review-tasks`,
    buildTaskBody(options),
  )
  return data
}

export async function fetchTranslationReviewTask(taskId: string) {
  const { data } = await http.get<{
    status: string
    report_id: string
    progress: TranslationReviewReport['progress']
    error_message: string
  }>(`/translation-review-tasks/${taskId}`)
  return data
}

// ─── 报告查询 ────────────────────────────────────────────

export async function fetchFileTranslationReviewReport(fileRecordId: string) {
  const { data } = await http.get<{ items: TranslationReviewReport[] }>(
    `/file-records/${fileRecordId}/translation-review-reports`,
    { params: { limit: 1 } },
  )
  return data.items[0] ?? null
}

export async function fetchMergeViewTranslationReviewReport(viewId: string) {
  const { data } = await http.get<{ items: TranslationReviewReport[] }>(
    `/merge-views/${viewId}/translation-review-reports`,
    { params: { limit: 1 } },
  )
  return data.items[0] ?? null
}

export async function fetchTranslationReviewReport(reportId: string) {
  const { data } = await http.get<TranslationReviewReport>(
    `/translation-review-reports/${reportId}`,
  )
  return data
}

// ─── 单条处置 ────────────────────────────────────────────

export async function applyTranslationReviewItem(itemId: string) {
  const { data } = await http.post<{ status: string }>(
    `/translation-review-report-items/${itemId}/apply`,
  )
  return data
}

export async function restoreTranslationReviewItem(itemId: string) {
  const { data } = await http.post<{ success: boolean }>(
    `/translation-review-report-items/${itemId}/restore`,
  )
  return data
}

export async function rejectTranslationReviewItem(itemId: string) {
  const { data } = await http.post<{ status: string }>(
    `/translation-review-report-items/${itemId}/reject`,
  )
  return data
}

export async function setTranslationReviewItemsIgnored(itemIds: string[], ignored: boolean) {
  const { data } = await http.patch<{ changed_count: number }>(
    '/translation-review-report-items/ignore',
    { item_ids: itemIds, ignored },
  )
  return data
}

// ─── 批量操作 ────────────────────────────────────────────

export async function applyTranslationReviewBatch(
  reportId: string,
  options: ApplyBatchOptions,
) {
  const { data } = await http.post<ApplyBatchResult>(
    `/translation-review-reports/${reportId}/apply-batch`,
    {
      mode: options.mode,
      category_key: options.categoryKey ?? null,
      item_ids: options.itemIds ?? null,
    },
  )
  return data
}

export async function undoTranslationReviewBatch(
  reportId: string,
  applyBatchId?: string,
) {
  const { data } = await http.post<UndoBatchResult>(
    `/translation-review-reports/${reportId}/undo-batch`,
    applyBatchId ? { apply_batch_id: applyBatchId } : {},
  )
  return data
}

// ─── 重跑 ────────────────────────────────────────────────

export async function rerunTranslationReview(
  reportId: string,
  categoryKeys?: string[],
) {
  const { data } = await http.post<{ task_id: string; report_id: string }>(
    `/translation-review-reports/${reportId}/rerun`,
    categoryKeys?.length ? { category_keys: categoryKeys } : {},
  )
  return data
}
