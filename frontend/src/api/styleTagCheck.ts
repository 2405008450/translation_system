import { http } from './http'
import type { StyleTagCheckReport } from '../types/api'

interface GenerateOptions {
  runAi?: boolean
  provider?: string
  model?: string
}

function buildParams(options: GenerateOptions = {}) {
  const params: Record<string, unknown> = {}
  if (options.runAi !== undefined) {
    params.run_ai = options.runAi
  }
  if (options.provider) {
    params.provider = options.provider
  }
  if (options.model) {
    params.model = options.model
  }
  return params
}

export async function fetchFileStyleTagCheckReport(fileRecordId: string) {
  const { data } = await http.get<{ items: StyleTagCheckReport[] }>(
    `/file-records/${fileRecordId}/style-tag-check-reports`,
    { params: { limit: 1 } },
  )
  return data.items[0] ?? null
}

export async function createFileStyleTagCheckReport(fileRecordId: string, options: GenerateOptions = {}) {
  const { data } = await http.post<StyleTagCheckReport>(
    `/file-records/${fileRecordId}/style-tag-check-reports`,
    null,
    { params: buildParams({ runAi: true, ...options }) },
  )
  return data
}

export async function recheckStyleTagCheckReport(
  reportId: string,
  itemIds: string[] = [],
  options: GenerateOptions = {},
) {
  const { data } = await http.post<StyleTagCheckReport>(
    `/style-tag-check-reports/${reportId}/ai-recheck`,
    { item_ids: itemIds },
    { params: buildParams(options) },
  )
  return data
}

export async function rerunStyleTagCheckItem(itemId: string, options: GenerateOptions = {}) {
  const { data } = await http.post<StyleTagCheckReport>(
    `/style-tag-check-report-items/${itemId}/rerun`,
    null,
    { params: buildParams(options) },
  )
  return data
}

export async function applyStyleTagCheckItem(itemId: string) {
  const { data } = await http.patch<StyleTagCheckReport>(
    `/style-tag-check-report-items/${itemId}/apply`,
  )
  return data
}

export async function rejectStyleTagCheckItem(itemId: string) {
  const { data } = await http.patch<StyleTagCheckReport>(
    `/style-tag-check-report-items/${itemId}/reject`,
  )
  return data
}

export async function restoreStyleTagCheckItem(itemId: string) {
  const { data } = await http.patch<StyleTagCheckReport>(
    `/style-tag-check-report-items/${itemId}/restore`,
  )
  return data
}

export async function applyAllStyleTagCheckItems(reportId: string, itemIds: string[] = []) {
  const { data } = await http.post<StyleTagCheckReport & { applied_count: number }>(
    `/style-tag-check-reports/${reportId}/apply-all`,
    { item_ids: itemIds },
  )
  return data
}

export async function fetchMergeViewStyleTagCheckReport(viewId: string) {
  const { data } = await http.get<{ items: StyleTagCheckReport[] }>(
    `/merge-views/${viewId}/style-tag-check-reports`,
    { params: { limit: 1 } },
  )
  return data.items[0] ?? null
}

export async function createMergeViewStyleTagCheckReport(viewId: string, options: GenerateOptions = {}) {
  const { data } = await http.post<StyleTagCheckReport>(
    `/merge-views/${viewId}/style-tag-check-reports`,
    null,
    { params: buildParams({ runAi: true, ...options }) },
  )
  return data
}
