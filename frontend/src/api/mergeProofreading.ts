import { http } from './http'
import type { ProofreadingExportFormat } from './proofreading'

export type MergeProofreadingScope = 'unreviewed' | 'failed_only' | 'all'
export interface MergeProofreadingFile {
  file_record_id: string
  filename: string
  batch_id?: string | null
  pending: number
  reviewed: number
  protected: number
  failed: number
  legacy_unverified?: number
  skipped_reason: string | null
  available_formats: ProofreadingExportFormat[]
  unavailable_formats?: Partial<Record<ProofreadingExportFormat, string>>
  readiness?: { unconfirmed: number; missing_translation: number; llm_failed: number }
}
export interface MergeProofreadingPreview {
  files: MergeProofreadingFile[]
  pending: number
  reviewed: number
  protected: number
  available_formats: ProofreadingExportFormat[]
  format_file_counts: Partial<Record<ProofreadingExportFormat, number>>
  has_warnings: boolean
}
export interface MergeProofreadingRun {
  id: string
  kind: 'proofreading' | 'export'
  status: string
  progress: number
  cancel_requested: boolean
  can_cancel: boolean
  download_available: boolean
  files: Array<{ file_record_id: string; filename: string; status: string; progress: number; message: string; counts: MergeProofreadingFile }>
}
export interface MergeProofreadingPayload {
  file_id?: string
  provider: 'auto' | 'deepseek' | 'openrouter'
  model?: string
  user_instructions?: string
  retry_scope: MergeProofreadingScope
}
const base = (viewId: string) => `/merge-views/${viewId}/proofreading`
export async function previewMergeProofreading(viewId: string, payload: MergeProofreadingPayload) {
  return (await http.post<MergeProofreadingPreview>(`${base(viewId)}/preview`, payload)).data
}
export async function startMergeProofreading(viewId: string, payload: MergeProofreadingPayload) {
  return (await http.post<MergeProofreadingRun>(`${base(viewId)}/runs`, payload)).data
}
export async function listMergeProofreadingRuns(viewId: string) {
  return (await http.get<{ items: MergeProofreadingRun[] }>(`${base(viewId)}/runs`)).data.items || []
}
export async function cancelMergeProofreadingRun(viewId: string, runId: string) {
  return (await http.post<MergeProofreadingRun>(`${base(viewId)}/runs/${runId}/cancel`)).data
}
export async function getMergeProofreadingExportReadiness(viewId: string, fileId?: string) {
  return (await http.get<MergeProofreadingPreview>(`${base(viewId)}/export-readiness`, { params: { file_id: fileId } })).data
}
export async function startMergeProofreadingExport(viewId: string, format: ProofreadingExportFormat, acknowledgeWarnings: boolean, fileId?: string) {
  return (await http.post<MergeProofreadingRun>(`${base(viewId)}/export-tasks`, { format, acknowledge_warnings: acknowledgeWarnings, file_id: fileId })).data
}
export async function downloadMergeProofreadingExport(viewId: string, runId: string) {
  return http.get<Blob>(`${base(viewId)}/runs/${runId}/download`, { responseType: 'blob' })
}
