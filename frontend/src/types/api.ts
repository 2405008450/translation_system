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

export type AnalyticsGranularity = 'day' | 'month'

export interface AnalyticsSummary {
  total_projects: number
  total_files: number
  total_source_word_count: number
  translated_source_word_count: number
  llm_processed_source_word_count: number
  active_users_today: number
  translation_progress: number
}

export interface AnalyticsSeriesPoint {
  bucket: string
  project_created_count: number
  translated_source_word_count: number
  llm_processed_source_word_count: number
  active_user_count: number
}

export interface AnalyticsLanguagePair {
  source_language: string | null
  target_language: string | null
  project_count: number
  file_count: number
  translated_source_word_count: number
  llm_processed_source_word_count: number
}

export interface AnalyticsSourceBreakdown {
  source: string
  label: string
  event_count: number
  source_word_count: number
}

export interface AnalyticsDashboardResponse {
  granularity: AnalyticsGranularity
  summary: AnalyticsSummary
  series: AnalyticsSeriesPoint[]
  language_pairs: AnalyticsLanguagePair[]
  source_breakdown: AnalyticsSourceBreakdown[]
}

export interface FileRecordSummary {
  id: string
  filename: string
  status: string
  active_operation?: string | null
  active_operation_message?: string
  is_edit_locked?: boolean
  document_parse_mode?: DocumentParseMode
  document_parse_options?: DocumentParseOptions
  document_statistics?: DocumentStatistics
  source_language: string | null
  target_language: string | null
  created_at: string
  updated_at: string
}

export type DocumentParseMode = 'full' | 'body_only'

export interface DocumentParseOptions {
  include_headers_footers: boolean
  include_footnotes_endnotes: boolean
  include_comments: boolean
  clean_format: boolean
  translate_code_blocks: boolean
  extract_links: boolean
  skip_non_translatable: boolean
  xml_inline_elements_no_split: boolean
  custom_parse_config: boolean
  translate_idml_comments: boolean
  translate_idml_hidden_layers: boolean
  pptx_translate_comments: boolean
  pptx_translate_notes: boolean
  pptx_translate_document_properties: boolean
  xlsx_translate_comments: boolean
  xlsx_translate_drawing_text: boolean
  xlsx_translate_sheet_names: boolean
  xlsx_translate_hidden_content: boolean
  xlsx_translate_document_properties: boolean
  xlsx_translate_numeric_cells: boolean
  xlsx_translate_date_cells: boolean
  xlsx_translate_boolean_cells: boolean
  xlsx_translate_formula_cells: boolean
  xlsx_skip_fill_colors: string[]
}

export interface DocumentStatistics {
  source: string
  engine: string | null
  license_status: string | null
  include_textboxes_footnotes_endnotes: boolean | null
  pages: number | null
  words: number | null
  characters: number | null
  characters_with_spaces: number | null
  paragraphs: number | null
  lines: number | null
}

export interface UploadParseMode {
  id: string
  label: string
  description: string
}

export interface UploadCapability {
  extensions: string[]
  accept: string
  label: string
  category: string
  max_size_mb: number
  can_export_original: boolean
  parse_modes: UploadParseMode[]
  settings?: Array<{
    id: keyof DocumentParseOptions
    label: string
    kind?: 'checkbox' | 'color_palette'
    default: boolean | string[]
    options?: Array<{
      label: string
      value: string
    }>
    disabled?: boolean
    description?: string
  }>
  settings_select_all?: boolean
  features: string[]
}

export interface UploadCapabilitiesResponse {
  extensions: string[]
  accept: string
  formats: UploadCapability[]
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
  display_index?: number | null
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
  llm_provider: string | null
  llm_model: string | null
  block_type: string
  block_index: number
  row_index?: number | null
  cell_index?: number | null
}

export interface FileRecordDetail {
  id: string
  project_id: string | null
  filename: string
  status: string
  active_operation: string | null
  active_operation_message: string
  is_edit_locked: boolean
  document_parse_mode: DocumentParseMode
  document_parse_options: DocumentParseOptions
  document_statistics: DocumentStatistics
  source_language: string | null
  target_language: string | null
  collection_id: string | null
  collection_name: string | null
  term_base_id: string | null
  term_base_name: string | null
  term_base_ids: string[]
  term_base_names: string[]
  translation_guidelines: string
  created_at: string
  updated_at: string
  total_segments: number
  skip: number
  limit: number
  source_extension: string
  has_source_document: boolean
  can_export: boolean
  issue_count: number
  open_issue_count: number
  status_stats: SegmentStatusStats
  segments: Segment[]
}

export interface SegmentStatusStats {
  total: number
  exact: number
  fuzzy: number
  none: number
  confirmed: number
  empty_target: number
}

export interface SegmentPageFilters {
  scope: string
  source_query: string
  target_query: string
  search_fuzzy: boolean
}

export interface SegmentPageResponse {
  file_record_id: string
  total_segments: number
  matched_segments: number
  status_stats: SegmentStatusStats
  skip: number
  limit: number
  filters: SegmentPageFilters
  segments: Segment[]
}

export interface FileRecordPreview {
  id: string
  filename: string
  source_extension: string
  supports_preview: boolean
  preview_html: string
  preview_mode?: 'full' | 'window'
  render_mode?: 'source' | 'target'
  skip?: number
  limit?: number
  supports_full_preview?: boolean
}

export interface SaveToTMStats {
  total_segments: number
  matched_count: number
  valid_count: number
  skipped_count: number
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

export type IssueCategory = 'bug' | 'translation' | 'format' | 'performance' | 'data' | 'other'
export type IssueSeverity = 'low' | 'medium' | 'high' | 'critical'
export type IssueStatus = 'open' | 'resolved'

export interface IssueMarker {
  id: string
  project_id: string
  project_name: string | null
  file_record_id: string | null
  file_record_name: string | null
  title: string
  description: string
  category: IssueCategory
  severity: IssueSeverity
  status: IssueStatus
  page_url: string | null
  user_agent: string | null
  reporter: User | null
  reporter_name: string | null
  resolved_by: User | null
  resolved_by_name: string | null
  created_at: string
  updated_at: string
  resolved_at: string | null
}

export interface IssueMarkerCreatePayload {
  file_record_id?: string | null
  title?: string | null
  description: string
  category: IssueCategory
  severity: IssueSeverity
  page_url?: string | null
  user_agent?: string | null
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

export interface TermEntryConflict {
  id: string
  term_base_id: string
  source_text: string
  target_text: string
  source_language: string
  target_language: string
}

export interface ExtractedTermDraft {
  index: number
  source_text: string
  target_text: string
  source_normalized: string
  has_conflict: boolean
  conflict: TermEntryConflict | null
}

export interface TermExtractionModelResult {
  provider: string
  model: string
  terms: ExtractedTermDraft[]
  total: number
}

export interface TermExtractionModelError {
  model: string
  message: string
}

export interface TermExtractionResult {
  file_record: {
    id: string
    filename: string
    term_base_id: string | null
    total_segments: number
  }
  term_base_id: string | null
  source_language: string
  target_language: string
  provider: string
  model: string
  available_models?: string[]
  results: TermExtractionModelResult[]
  merged_terms: ExtractedTermDraft[]
  terms: ExtractedTermDraft[]
  total: number
  errors?: TermExtractionModelError[]
}

export interface TermBatchSaveItem {
  index: number
  source_text: string
  target_text: string
  source_normalized: string
  action: 'add' | 'replace' | 'skip'
  status: 'created' | 'updated' | 'skipped' | 'conflict'
  message: string
  conflict: TermEntryConflict | null
}

export interface TermBatchSaveResult {
  created_count: number
  updated_count: number
  skipped_count: number
  conflict_count: number
  items: TermBatchSaveItem[]
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

export interface SaveToTMResult {
  created_count: number
  updated_count: number
  skipped_count: number
  total_segments: number
  collection_id: string | null
  collection_name: string | null
  created_collection: boolean
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

export interface GuidelineTemplateSummary {
  id: string
  name: string
  filename: string
  size_bytes: number
  updated_at: string
  content_preview: string
}

export interface GuidelineTemplateDetail extends GuidelineTemplateSummary {
  content: string
}

export interface SegmentUpdatePayload {
  sentence_id: string
  target_text: string
  source: string
  track_revision?: boolean
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

export type LLMTranslateScope = 'fuzzy_only' | 'none_only' | 'empty_target_only' | 'all' | 'all_with_exact'
export type LLMProvider = 'auto' | 'deepseek' | 'openrouter'

export interface LLMGuidelineOptions {
  guidelineTemplateId?: string
  temporaryPrompt?: string
  model?: string
}

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
