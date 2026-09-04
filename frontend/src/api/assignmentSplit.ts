import { http } from './http'

import type {
  AssignmentSplitPreviewRequest,
  AssignmentSplitPreviewResponse,
} from '../types/api'

export async function previewProjectAssignmentSplit(
  projectId: string,
  payload: AssignmentSplitPreviewRequest,
) {
  const { data } = await http.post<AssignmentSplitPreviewResponse>(
    `/projects/${projectId}/assignment-split-preview`,
    payload,
  )
  return data
}
