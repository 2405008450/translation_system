import { http } from './http'

export interface ProofreadingColumnPreview {
  index: number
  letter: string
  header: string
  samples: string[]
  suggested_language: string | null
  suggested_role: 'source' | 'target' | 'other'
}

export interface ProofreadingSheetPreview {
  sheet_index: number
  name: string
  header_row: number
  max_row: number
  max_column: number
  columns: ProofreadingColumnPreview[]
  header_candidates: Array<{ row_index: number; values: string[] }>
  supported: boolean
  blocked_reasons: string[]
}

export interface ProofreadingPreview {
  preview_token: string
  filename: string
  file_hash: string
  sheets: ProofreadingSheetPreview[]
}

export interface ProofreadingTargetMapping {
  target_column: number
  target_language: string
}

export interface ProofreadingSheetMapping {
  sheet_index: number
  header_row: number
  source_column: number
  targets: ProofreadingTargetMapping[]
}

export interface ProofreadingBinding {
  id: string
  file_record_id: string
  sheet_index: number
  sheet_name: string
  header_row: number
  source_column: number
  target_column: number
  output_column: number
  source_header: string
  target_header: string
  target_language: string
}

export interface ProofreadingBatch {
  id: string
  project_id: string
  filename: string
  source_language: string
  target_language?: string
  batch_kind?: 'xlsx_columns' | 'document_pair'
  alignment_status?: 'not_applicable' | 'aligning' | 'canceling' | 'canceled' | 'draft' | 'confirmed' | 'failed'
  workflow_stage?: 'not_applicable' | 'import' | 'alignment' | 'proofreading'
  status: 'aligning' | 'draft' | 'ready' | 'queued' | 'running' | 'canceling' | 'completed' | 'partial_failed' | 'failed' | 'canceled'
  progress: number
  message: string
  error_message: string
  cancel_requested?: boolean
  total_segments: number
  changed_segments: number
  skipped_segments: number
  failed_segments: number
  export_status: 'idle' | 'queued' | 'running' | 'completed' | 'failed'
  export_progress: number
  export_error_message: string
  export_filename: string
  created_at: string | null
  finished_at: string | null
  generation_settings: {
    provider: 'auto' | 'deepseek' | 'openrouter'
    model: string
    user_instructions: string
    actual_provider: string
    actual_model: string
  }
  bindings: ProofreadingBinding[]
}

export type ProofreadingExportFormat =
  | 'proofreading_docx_layout'
  | 'proofreading_docx_ordered'
  | 'proofreading_audit_xlsx'
  | 'proofreading_xlsx_original'

export interface ProofreadingExportReadiness {
  batch_id: string
  total: number
  confirmed: number
  unconfirmed: number
  missing_translation: number
  translation_only: number
  translation_only_unreviewed: number
  llm_failed: number
  available_formats: ProofreadingExportFormat[]
  has_warnings: boolean
}

export interface ProofreadingExportTask {
  task_id: string
  file_record_id: string
  export_type: ProofreadingExportFormat
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  message: string
  filename?: string | null
  error?: string | null
}

export async function previewProofreadingWorkbook(projectId: string, file: File) {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await http.post<ProofreadingPreview>(
    `/projects/${projectId}/proofreading/preview`,
    formData,
  )
  return data
}

export async function createProofreadingBatch(
  projectId: string,
  payload: { preview_token: string; source_language: string; mappings: ProofreadingSheetMapping[] },
) {
  const { data } = await http.post<ProofreadingBatch>(`/projects/${projectId}/proofreading-batches`, payload)
  return data
}

export async function listProofreadingBatches(projectId: string) {
  const { data } = await http.get<{ items: ProofreadingBatch[] }>(`/projects/${projectId}/proofreading-batches`)
  return data.items
}

export async function getProofreadingBatch(batchId: string) {
  const { data } = await http.get<ProofreadingBatch>(`/proofreading-batches/${batchId}`)
  return data
}

export async function completeAlignment(batchId: string) {
  const { data } = await http.post<ProofreadingBatch>(`/proofreading-batches/${batchId}/alignment/complete`)
  return data
}

export async function reopenAlignment(batchId: string) {
  const { data } = await http.post<ProofreadingBatch>(`/proofreading-batches/${batchId}/alignment/reopen`)
  return data
}

export async function generateProofreadingBatch(
  batchId: string,
  payload: {
    provider: 'auto' | 'deepseek' | 'openrouter'
    model?: string
    user_instructions?: string
    retry_scope?: 'all' | 'failed_only'
  },
) {
  await http.post(`/proofreading-batches/${batchId}/generate`, payload)
}

export async function cancelProofreadingBatch(batchId: string) {
  const { data } = await http.post<ProofreadingBatch>(`/proofreading-batches/${batchId}/cancel`)
  return data
}

export async function exportProofreadingBatch(batchId: string) {
  await http.post(`/proofreading-batches/${batchId}/exports`)
}

export async function downloadProofreadingBatchExport(batchId: string) {
  return http.get<Blob>(`/proofreading-batches/${batchId}/exports/latest`, { responseType: 'blob' })
}

export async function getProofreadingExportReadiness(batchId: string) {
  const { data } = await http.get<ProofreadingExportReadiness>(
    `/proofreading-batches/${batchId}/export-readiness`,
  )
  return data
}

export async function createProofreadingExportTask(
  batchId: string,
  format: ProofreadingExportFormat,
  acknowledgeWarnings: boolean,
) {
  const { data } = await http.post<ProofreadingExportTask>(
    `/proofreading-batches/${batchId}/export-tasks`,
    { format, acknowledge_warnings: acknowledgeWarnings },
  )
  return data
}

export async function getProofreadingExportTask(taskId: string) {
  const { data } = await http.get<ProofreadingExportTask>(`/file-records/export-tasks/${taskId}`)
  return data
}

export async function downloadProofreadingExportTask(taskId: string) {
  return http.get<Blob>(`/file-records/export-tasks/${taskId}/download`, { responseType: 'blob' })
}
