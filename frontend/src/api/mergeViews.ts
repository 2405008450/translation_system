import { http } from './http'

import type {
  MergeView,
  MergeViewDetail,
  MergeViewSegmentPageResponse,
  SegmentPositionResponse,
  TermQAReport,
  TermQAReportListResponse,
  WorkbenchQAResult,
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

export async function listVisibleMergeViews(params: { projectId?: string } = {}) {
  const { data } = await http.get<{ items: MergeView[] }>('/merge-views', {
    params: {
      project_id: params.projectId,
    },
  })
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

export async function createMergeViewTermQAReport(viewId: string) {
  const { data } = await http.post<TermQAReport>(`/merge-views/${viewId}/term-qa-reports`)
  return data
}

export async function listMergeViewTermQAReports(
  viewId: string,
  params: { limit?: number; includeItems?: boolean } = {},
) {
  const { data } = await http.get<TermQAReportListResponse>(
    `/merge-views/${viewId}/term-qa-reports`,
    {
      params: {
        limit: params.limit,
        include_items: params.includeItems,
      },
    },
  )
  return data
}

export async function fetchMergeViewQAResult(viewId: string) {
  const { data } = await http.get<WorkbenchQAResult>(`/merge-views/${viewId}/qa-results`)
  return data
}

export async function createMergeViewQAResult(viewId: string) {
  const { data } = await http.post<WorkbenchQAResult>(`/merge-views/${viewId}/qa-results`)
  return data
}

export async function fetchMergeViewSegmentPosition(
  viewId: string,
  fileRecordId: string,
  sentenceId: string,
  params: { pageSize?: number } = {},
) {
  const { data } = await http.get<SegmentPositionResponse>(
    `/merge-views/${viewId}/segments/${fileRecordId}/${encodeURIComponent(sentenceId)}/position`,
    {
      params: {
        page_size: params.pageSize,
      },
    },
  )
  return data
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
    case_sensitive: query.caseSensitive || undefined,
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
  const pageSize = query.pageSize ?? 100
  const requestPage = async (page: number) => {
    const { data } = await http.get<MergeViewSegmentPageResponse>(
      `/merge-views/${viewId}/segments`,
      { params: buildMergeViewSegmentParams({ ...query, page, pageSize }) },
    )
    return data
  }

  const requestedPage = Math.max(1, Math.floor(query.page ?? 1))
  let data = await requestPage(requestedPage)
  const maxPage = Math.max(1, Math.ceil(data.matched_segments / pageSize))
  if (requestedPage > maxPage) {
    data = await requestPage(maxPage)
  }
  return data
}
