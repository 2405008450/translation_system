export type UserRole = 'admin' | 'user'

export interface User {
  id: string
  username: string
  role: UserRole
  is_active: boolean
  created_at: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: User
}

export interface InitStatusResponse {
  initialized: boolean
  requires_init: boolean
  table_exists: boolean
  message: string | null
}

export interface FileRecordSummary {
  id: string
  filename: string
  status: string
  created_at: string
  updated_at: string
}

export interface Segment {
  id: string
  sentence_id: string
  source_text: string
  display_text: string
  target_text: string
  status: string
  score: number
  matched_source_text: string | null
  source: string
  block_type: string
  block_index: number
  row_index?: number | null
  cell_index?: number | null
}

export interface FileRecordDetail {
  id: string
  filename: string
  status: string
  created_at: string
  updated_at: string
  total_segments: number
  skip: number
  limit: number
  segments: Segment[]
}

export interface FileRecordPreview {
  id: string
  filename: string
  supports_preview: boolean
  preview_html: string
}

export interface TMCollection {
  id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
  entry_count: number
}

export interface TMImportSummary {
  filename: string
  created_rows: number
  updated_rows: number
  skipped_empty_rows: number
  skipped_header_rows: number
  imported_rows: number
  collection_id: string | null
  collection_name: string | null
}

export interface SegmentUpdatePayload {
  sentence_id: string
  target_text: string
  source: string
}

export type LLMTranslateScope = 'fuzzy_only' | 'none_only' | 'all'
export type LLMProvider = 'auto' | 'deepseek' | 'openrouter'

export interface LLMEvent {
  event: string
  data: Record<string, unknown>
}
