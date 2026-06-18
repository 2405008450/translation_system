import { http } from './http'

import type {
  MergeView,
  MergeViewDetail,
  MergeViewSegmentPageResponse,
} from '../types/api'
import type { SegmentPageQuery } from '../stores/segment'

export interface MergeViewCreatePayload {
  name: string
  file_ids: string[]
}

export interface MergeViewUpdatePayload {
  name?: string
  file_ids?: string[]
}

export async function listProjectMergeViews(projectId: string) {
  const { data } = await http.get<{ project_id: string; items: MergeView[] }>(
    `/projects/${projectId}/merge-views`,
  )
  return data
}

export async function createProjectMergeView(projectId: string, payload: MergeViewCreatePayload) {
  const { data } = await http.post<MergeView>(`/projects/${projectId}/merge-views`, payload)
  return data
}

export async function getMergeViewDetail(viewId: string) {
  const { data } = await http.get<MergeViewDetail>(`/merge-views/${viewId}`)
  return data
}

export async function updateMergeView(viewId: string, payload: MergeViewUpdatePayload) {
  const { data } = await http.patch<MergeView>(`/merge-views/${viewId}`, payload)
  return data
}

export async function deleteMergeView(viewId: string) {
  await http.delete(`/merge-views/${viewId}`)
}

/** 把 SegmentPageQuery 序列化为 merge-views 段聚合端点的查询参数 */
function buildMergeViewSegmentParams(query: SegmentPageQuery) {
  const pageSize = query.pageSize ?? 100
  const page = query.page ?? 1
  return {
    skip: (page - 1) * pageSize,
    limit: pageSize,
    scope: query.scope ?? 'all',
    source_query: query.sourceQuery || undefined,
    target_query: query.targetQuery || undefined,
    source_exclude: query.sourceExclude || undefined,
    target_exclude: query.targetExclude || undefined,
    search_fuzzy: query.searchFuzzy || undefined,
    'status_filters[]': query.statusFilters,
    'match_filters[]': query.matchFilters,
    'source_filters[]': query.sourceFilters,
    'workflow_step_ids[]': query.workflowStepIds,
  }
}

export async function fetchMergeViewSegmentPage(
  viewId: string,
  query: SegmentPageQuery,
) {
  const { data } = await http.get<MergeViewSegmentPageResponse>(
    `/merge-views/${viewId}/segments`,
    { params: buildMergeViewSegmentParams(query) },
  )
  return data
}
