import { http } from './http'
import type { ProofreadingBatch } from './proofreading'

export interface AlignmentPreviewSide {
  filename: string
  unit_count: number
  block_types: Record<string, number>
  character_count: number
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
}) {
  const { data } = await http.post<ProofreadingBatch>(`/projects/${projectId}/document-alignment-batches`, payload)
  return data
}

export async function listAlignmentPairs(batchId: string) {
  const { data } = await http.get<{ items: AlignmentPair[]; total: number }>(`/proofreading-batches/${batchId}/alignment-pairs`, { params: { page_size: 500 } })
  return data
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
  await http.post(`/proofreading-batches/${batchId}/alignment-pairs/merge`, { first_pair_id: firstId, second_pair_id: secondId })
}

export async function shiftAlignmentBoundary(batchId: string, pairId: string, direction: 'next_into_current' | 'current_into_next') {
  await http.post(`/proofreading-batches/${batchId}/alignment-pairs/shift-boundary`, { pair_id: pairId, side: 'target', direction })
}

export async function rerunAlignment(batchId: string) {
  await http.post(`/proofreading-batches/${batchId}/alignment/rerun`)
}

export async function confirmAlignment(batchId: string) {
  const { data } = await http.post<{ file_record_id: string }>(`/proofreading-batches/${batchId}/alignment/confirm`)
  return data
}
