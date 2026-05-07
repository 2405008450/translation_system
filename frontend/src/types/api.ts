export type UserRole = 'admin' | 'user'

export interface User {
  id: string
  username: string
  nickname: string | null
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
  source_language: string | null
  target_language: string | null
  created_at: string
  updated_at: string
}

export interface TMMatchCandidate {
  source_text: string
  target_text: string
  score: number
  diff_html?: string | null
  collection_name: string | null
  creator_name: string | null
  created_at: string | null
  updated_at: string | null
}

export interface TermMatchCandidate {
  source_text: string
  target_text: string
  term_base_name: string | null
  creator_name: string | null
  updated_at: string | null
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
  matched_collection_name: string | null
  matched_creator_name: string | null
  matched_created_at: string | null
  matched_updated_at: string | null
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
  source_language: string | null
  target_language: string | null
  collection_id: string | null
  collection_name: string | null
  term_base_id: string | null
  term_base_name: string | null
  created_at: string
  updated_at: string
  total_segments: number
  skip: number
  limit: number
  source_extension: string
  has_source_document: boolean
  can_export: boolean
  segments: Segment[]
}

export interface FileRecordPreview {
  id: string
  filename: string
  source_extension: string
  supports_preview: boolean
  preview_html: string
}

export type CommentAnchorMode = 'sentence' | 'range'
export type CommentStatus = 'open' | 'resolved'

export interface CommentAnchorDraft {
  sentence_id: string
  anchor_mode: CommentAnchorMode
  range_start_offset: number | null
  range_end_offset: number | null
  anchor_text: string | null
}

export interface SegmentComment {
  id: string
  file_record_id: string
  segment_id: string | null
  sentence_id: string | null
  anchor_mode: CommentAnchorMode
  range_start_offset: number | null
  range_end_offset: number | null
  anchor_text: string | null
  body: string
  author: User
  parent_id: string | null
  status: CommentStatus
  created_at: string
  updated_at: string
  resolved_at: string | null
}

export interface TermBase {
  id: string
  name: string
  description: string | null
  source_language: string
  target_language: string
  created_at: string
  updated_at: string
  entry_count: number
}

export interface TMCollection {
  id: string
  name: string
  description: string | null
  source_language: string | null
  target_language: string | null
  created_at: string
  updated_at: string
  entry_count: number
}

export interface TMEntryRecord {
  id: string
  collection_id: string | null
  source_text: string
  target_text: string
  source_language: string | null
  target_language: string | null
  created_at: string
  updated_at: string
}

export interface TermEntryRecord {
  id: string
  term_base_id: string
  source_text: string
  target_text: string
  source_language: string
  target_language: string
  creator_name: string | null
  created_at: string
  updated_at: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  skip: number
  limit: number
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
  source_language: string
  target_language: string
}

export interface TermImportSummary {
  filename: string
  created_rows: number
  updated_rows: number
  skipped_empty_rows: number
  skipped_header_rows: number
  imported_rows: number
  term_base_id: string
  term_base_name: string
  source_language: string
  target_language: string
}

export interface SegmentUpdatePayload {
  sentence_id: string
  target_text: string
  source: string
}

export interface SegmentRevisionEntry {
  id: string
  file_record_id: string
  segment_id: string
  sentence_id: string
  source: string
  status: 'pending' | 'accepted' | 'rejected'
  before_text: string
  after_text: string
  author: User | null
  resolved_by: User | null
  created_at: string
  resolved_at: string | null
}

export interface CommentCreatePayload extends CommentAnchorDraft {
  segment_id?: string | null
  body: string
}

export interface CommentUpdatePayload {
  body?: string
  status?: CommentStatus
}

export interface CommentReplyPayload {
  body: string
}

export type LLMTranslateScope = 'fuzzy_only' | 'none_only' | 'all' | 'all_with_exact'
export type LLMProvider = 'auto' | 'deepseek' | 'openrouter'

export interface LLMEvent {
  event: string
  data: Record<string, unknown>
}

export interface TermbaseCollection {
  id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
  entry_count: number
}

export interface Term {
  id: string
  source_text: string
  target_text: string
  collection_id: string | null
  created_at: string
}

export interface TermMatch {
  term_id: string
  source_text: string
  target_text: string
  start: number
  end: number
}

export interface TermbaseImportSummary {
  filename: string
  created_rows: number
  updated_rows: number
  skipped_rows: number
  imported_rows: number
  collection_id: string | null
  collection_name: string | null
}
