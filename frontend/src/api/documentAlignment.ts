import axios from 'axios'

import { http } from './http'
import type { ProofreadingBatch } from './proofreading'

export interface AlignmentPreviewSide {
  filename: string
  unit_count: number
  block_types: Record<string, number>
  character_count: number
  paragraph_count: number
  table_count: number
}

export interface AlignmentPreview {
  preview_token: string
  source: AlignmentPreviewSide
  target: AlignmentPreviewSide
  supported_granularities: Array<'sentence' | 'paragraph'>
}

export interface AlignmentPair {
  id: string
  pair_order: number
  src_indices: number[]
  tgt_indices: number[]
  source_text: string
  target_text: string
  confidence: number
  confidence_level: 'high' | 'medium' | 'low'
  method: string
  features: Record<string, unknown>
  locked: boolean
}

export async function previewDocumentAlignment(projectId: string, source: File, target: File) {
  const body = new FormData()
  body.append('source_file', source)
  body.append('target_file', target)
  const { data } = await http.post<AlignmentPreview>(`/projects/${projectId}/document-alignment/preview`, body)
  return data
}

export async function createDocumentAlignmentBatch(projectId: string, payload: {
  preview_token: string
  source_language: string
  target_language: string
  granularity: 'sentence' | 'paragraph'
  use_llm_for_hard_blocks: boolean
  full_review: boolean
  alignment_strategy: 'hierarchical_llm' | 'order_first' | 'structure_aware'
}) {
  const { data } = await http.post<ProofreadingBatch>(`/projects/${projectId}/document-alignment-batches`, payload)
  return data
}

export async function listAlignmentPairs(batchId: string, params: {
  page?: number
  page_size?: number
  confidence_level?: AlignmentPair['confidence_level']
  q?: string
} = {}) {
  const { data } = await http.get<{ items: AlignmentPair[]; total: number }>(`/proofreading-batches/${batchId}/alignment-pairs`, {
    params: { page: 1, page_size: 100, ...params },
  })
  return data
}

const ALIGNMENT_OPENING_PREVIEW_LIMIT = 20
const ALIGNMENT_LOW_CONFIDENCE_LIMIT = 80

/**
 * 项目页只预览开头和低置信度配对，严格限制返回与渲染数量，避免大文档占满内存。
 */
export async function listAlignmentPreviewPairs(batchId: string) {
  const [opening, lowConfidence] = await Promise.all([
    listAlignmentPairs(batchId, { page_size: ALIGNMENT_OPENING_PREVIEW_LIMIT }),
    listAlignmentPairs(batchId, { page_size: ALIGNMENT_LOW_CONFIDENCE_LIMIT, confidence_level: 'low' }),
  ])
  const uniquePairs = new Map<string, AlignmentPair>()
  for (const pair of [...opening.items, ...lowConfidence.items]) uniquePairs.set(pair.id, pair)
  return {
    items: [...uniquePairs.values()].sort((left, right) => left.pair_order - right.pair_order),
    total: opening.total,
    low_confidence_total: lowConfidence.total,
    opening_limit: ALIGNMENT_OPENING_PREVIEW_LIMIT,
    low_confidence_limit: ALIGNMENT_LOW_CONFIDENCE_LIMIT,
  }
}

export async function patchAlignmentPair(pairId: string, payload: Partial<Pick<AlignmentPair, 'src_indices' | 'tgt_indices' | 'locked'>>) {
  const { data } = await http.patch<AlignmentPair>(`/alignment-pairs/${pairId}`, payload)
  return data
}

export async function splitAlignmentPair(batchId: string, pair: AlignmentPair) {
  const { data } = await http.post<{ items: AlignmentPair[] }>(`/proofreading-batches/${batchId}/alignment-pairs/split`, {
    pair_id: pair.id,
    src_at: Math.ceil(pair.src_indices.length / 2),
    tgt_at: Math.ceil(pair.tgt_indices.length / 2),
  })
  return data
}

export async function mergeAlignmentPairs(batchId: string, firstId: string, secondId: string) {
  await mergeAlignmentPairRange(batchId, [firstId, secondId])
}

export async function mergeAlignmentPairRange(batchId: string, pairIds: string[]) {
  try {
    const { data } = await http.post<AlignmentPair>(`/proofreading-batches/${batchId}/alignment-pairs/merge`, {
      pair_ids: pairIds,
    })
    return data
  } catch (error) {
    // 兼容尚未重启的旧后端：旧接口只接受 first_pair_id/second_pair_id。
    // 新请求在进入业务逻辑前即返回 422，因此可以安全降级为连续两两合并。
    if (!axios.isAxiosError(error) || error.response?.status !== 422 || pairIds.length < 2) throw error
    let merged: AlignmentPair | null = null
    for (let index = 1; index < pairIds.length; index += 1) {
      const response = await http.post<AlignmentPair>(`/proofreading-batches/${batchId}/alignment-pairs/merge`, {
        first_pair_id: pairIds[0],
        second_pair_id: pairIds[index],
      })
      merged = response.data
    }
    // 旧接口不会自动锁定人工合并结果，补一次锁定避免后续重跑覆盖。
    if (merged && !merged.locked) {
      merged = await patchAlignmentPair(merged.id, { locked: true })
    }
    if (!merged) throw error
    return merged
  }
}

export async function shiftAlignmentBoundary(batchId: string, pairId: string, direction: 'next_into_current' | 'current_into_next') {
  await http.post(`/proofreading-batches/${batchId}/alignment-pairs/shift-boundary`, { pair_id: pairId, side: 'target', direction })
}

export interface AlignmentPairReplacement {
  src_indices: number[]
  tgt_indices: number[]
  locked: boolean
}

export async function replaceAlignmentPairRange(batchId: string, payload: {
  start_order: number
  delete_count: number
  replacements: AlignmentPairReplacement[]
}) {
  const { data } = await http.post<{ items: AlignmentPair[] }>(
    `/proofreading-batches/${batchId}/alignment-pairs/replace-range`,
    payload,
  )
  return data
}

export async function deleteAlignmentPair(pairId: string) {
  const { data } = await http.delete<{
    deleted_pair_id: string
    neighbor: AlignmentPair | null
  }>(`/alignment-pairs/${pairId}`)
  return data
}

export async function splitAlignmentPairsByCell(batchId: string, pairIds: string[] = []) {
  const { data } = await http.post<{
    changed_pairs: number
    created_pairs: number
    merged_gaps: number
  }>(`/proofreading-batches/${batchId}/alignment-pairs/split-by-cell`, {
    pair_ids: pairIds,
  })
  return data
}

export async function updateAlignmentPairText(
  pairId: string,
  payload: { source_text?: string; target_text?: string },
) {
  const { data } = await http.patch<AlignmentPair>(`/alignment-pairs/${pairId}/text`, payload)
  return data
}

export async function rerunAlignment(batchId: string) {
  await http.post(`/proofreading-batches/${batchId}/alignment/rerun`)
}

export async function cancelAlignment(batchId: string) {
  const { data } = await http.post<ProofreadingBatch>(`/proofreading-batches/${batchId}/alignment/cancel`)
  return data
}

export async function confirmAlignment(batchId: string) {
  const { data } = await http.post<{ batch: ProofreadingBatch; file_record_id: string }>(
    `/proofreading-batches/${batchId}/alignment/confirm`,
  )
  return data
}

export async function downloadAlignmentCsv(batchId: string) {
  return http.get<Blob>(`/proofreading-batches/${batchId}/alignment/export.csv`, { responseType: 'blob' })
}
