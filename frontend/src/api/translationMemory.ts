import { http } from './http'
import type { TMMatchCandidate } from '../types/api'

export function getSegmentTMCandidates(fileId: string, segmentId: string, collectionIds: readonly string[]) {
  const params = new URLSearchParams()
  for (const id of collectionIds) {
    params.append('collection_ids', id)
  }
  return http.get<{ candidates: TMMatchCandidate[]; timed_out?: boolean }>(
    `/file-records/${fileId}/segments/${segmentId}/tm-candidates`,
    { params },
  )
}
