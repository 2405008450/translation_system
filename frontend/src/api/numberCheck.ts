import { http } from './http'
import type { NumberCheckReport } from '../types/api'

interface GenerateOptions {
  runAi?: boolean
  aiScope?: 'program_only' | 'all'
  provider?: string
  model?: string
}

function buildParams(options: GenerateOptions = {}) {
  const params: Record<string, unknown> = {}
  if (options.runAi !== undefined) {
    params.run_ai = options.runAi
  }
  if (options.aiScope) {
    params.ai_scope = options.aiScope
  }
  if (options.provider) {
    params.provider = options.provider
  }
  if (options.model) {
    params.model = options.model
  }
  return params
}

export async function fetchFileNumberCheckReport(fileRecordId: string) {
  const { data } = await http.get<{ items: NumberCheckReport[] }>(
    `/file-records/${fileRecordId}/number-check-reports`,
    { params: { limit: 1 } },
  )
  return data.items[0] ?? null
}

export async function createFileNumberCheckReport(fileRecordId: string, options: GenerateOptions = {}) {
  const { data } = await http.post<NumberCheckReport>(
    `/file-records/${fileRecordId}/number-check-reports`,
    null,
    { params: buildParams({ runAi: true, ...options }) },
  )
  return data
}

export async function fetchMergeViewNumberCheckReport(viewId: string) {
  const { data } = await http.get<{ items: NumberCheckReport[] }>(
    `/merge-views/${viewId}/number-check-reports`,
    { params: { limit: 1 } },
  )
  return data.items[0] ?? null
}

export async function createMergeViewNumberCheckReport(viewId: string, options: GenerateOptions = {}) {
  const { data } = await http.post<NumberCheckReport>(
    `/merge-views/${viewId}/number-check-reports`,
    null,
    { params: buildParams({ runAi: true, ...options }) },
  )
  return data
}

export async function recheckNumberCheckReport(
  reportId: string,
  itemIds: string[] = [],
  options: GenerateOptions = {},
) {
  const { data } = await http.post<NumberCheckReport>(
    `/number-check-reports/${reportId}/ai-recheck`,
    { item_ids: itemIds },
    { params: buildParams(options) },
  )
  return data
}

export async function applyNumberCheckItem(itemId: string) {
  const { data } = await http.patch<NumberCheckReport>(
    `/number-check-report-items/${itemId}/apply`,
  )
  return data
}

export async function restoreNumberCheckItem(itemId: string) {
  const { data } = await http.patch<NumberCheckReport>(
    `/number-check-report-items/${itemId}/restore`,
  )
  return data
}

export async function setNumberCheckItemIgnored(itemId: string, ignored: boolean) {
  const { data } = await http.patch<NumberCheckReport>(
    `/number-check-report-items/${itemId}/ignore`,
    { ignored },
  )
  return data
}

export async function applyAllNumberCheckItems(reportId: string, itemIds: string[] = []) {
  const { data } = await http.post<NumberCheckReport & { applied_count: number }>(
    `/number-check-reports/${reportId}/apply-all`,
    { item_ids: itemIds },
  )
  return data
}

export async function ignoreAllNumberCheckItems(
  reportId: string,
  itemIds: string[] = [],
  ignored = true,
) {
  const { data } = await http.post<NumberCheckReport & { updated_count: number }>(
    `/number-check-reports/${reportId}/ignore-all`,
    { item_ids: itemIds },
    { params: { ignored } },
  )
  return data
}
