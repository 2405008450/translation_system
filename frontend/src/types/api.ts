export type UserRole = 'super_admin' | 'admin' | 'user'
export type TranslatorType = 'internal' | 'external'

export interface User {
  id: string
  username: string
  nickname: string | null
  role: UserRole
  translator_type: TranslatorType
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

export interface AnalyticsUserStat {
  user_id: string | null
  username: string | null
  nickname: string | null
  role: UserRole | null
  translator_type: TranslatorType | null
  is_active: boolean
  display_name: string
  active_day_count: number
  request_count: number
  estimated_active_minutes: number
  first_seen_at: string | null
  last_seen_at: string | null
  new_source_word_count: number
  modified_source_word_count: number
  total_source_word_count: number
  event_count: number
}

export interface AnalyticsDashboardResponse {
  granularity: AnalyticsGranularity
  summary: AnalyticsSummary
  series: AnalyticsSeriesPoint[]
  language_pairs: AnalyticsLanguagePair[]
  source_breakdown: AnalyticsSourceBreakdown[]
  user_stats: AnalyticsUserStat[]
}

export interface WorkflowStep {
  id: string
  step_key: string
  name: string
  step_type: string
  sort_order: number
}

export interface WorkflowTemplate {
  id: string
  name: string
  steps: Array<Omit<WorkflowStep, 'id'> & { id?: string }>
}

export interface WorkflowProgress extends WorkflowStep {
  total_segments: number
  completed_segments: number
  progress: number
}

export interface FileRecordSummary {
  id: string
  project_id?: string | null
  project_name?: string | null
  filename: string
  status: string
  progress?: number
  total_segments?: number
  translated_segments?: number
  confirmed_segments?: number
  pretranslated_segments?: number
  pretranslation_progress?: number
  open_issue_count?: number
  issue_count?: number
  active_operation?: string | null
  active_operation_message?: string
  is_edit_locked?: boolean
  document_parse_mode?: DocumentParseMode
  document_parse_options?: DocumentParseOptions
  document_statistics?: DocumentStatistics
  source_language: string | null
  target_language: string | null
  assignee_id?: string | null
  assignee?: User | null
  assignees?: User[]
  assigned_at?: string | null
  workflow_steps?: WorkflowStep[]
  workflow_progress?: WorkflowProgress[]
  can_manage?: boolean
  can_write?: boolean
  created_at: string
  updated_at: string
  glossary_base_ids?: string[]
}

export interface ProjectAssignmentItem {
  id: string
  project_assignment_id?: string
  assignee_id: string
  assignee: User
  workflow_step_id?: string
  workflow_step?: WorkflowStep | null
  file_record_ids: string[]
  assigned_by_id: string | null
  assigned_at: string
}

export interface ProjectAssignmentsResponse {
  project_id: string
  workflow_steps?: WorkflowStep[]
  assignments: ProjectAssignmentItem[]
}

export interface ProjectAssignmentPayload {
  assignments: Array<{
    assignee_id: string
    workflow_step_id?: string
    file_record_ids: string[]
  }>
}

export interface AssignmentEvent {
  id: string
  project_id: string
  project_name: string | null
  file_record_id: string | null
  file_record_name: string | null
  assignee_id: string
  assignee: User | null
  actor_id: string | null
  actor: User | null
  action: string
  before_payload: Record<string, unknown>
  after_payload: Record<string, unknown>
  created_at: string
}

export interface AssignmentEventsResponse {
  items: AssignmentEvent[]
}

export interface NotificationItem {
  id: string
  type: string
  title: string
  body: string
  project_id: string | null
  project_name: string | null
  file_record_id: string | null
  file_record_name: string | null
  related_event_id: string | null
  read_at: string | null
  created_at: string
}

export interface NotificationsResponse {
  items: NotificationItem[]
  unread_count: number
}

export type DocumentParseMode = 'full' | 'body_only'
export type DocxNumberingLocalization = 'auto' | 'preserve'

export interface DocumentParseOptions {
  include_headers_footers: boolean
  include_footnotes_endnotes: boolean
  include_comments: boolean
  clean_format: boolean
  preserve_hyperlinks: boolean
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
  docx_numbering_localization?: DocxNumberingLocalization
}

export interface DocumentStatistics {
  source: string
  engine: string | null
  engine_version: string | null
  license_status: string | null
  include_textboxes_footnotes_endnotes: boolean | null
  match_analysis: DocumentMatchAnalysis | null
  pages: number | null
  words: number | null
  non_asian_words: number | null
  asian_characters: number | null
  characters: number | null
  characters_with_spaces: number | null
  paragraphs: number | null
  lines: number | null
  internal_repeated_words: number | null
  internal_repeated_characters: number | null
  cross_file_repeated_words: number | null
  cross_file_repeated_characters: number | null
}

export interface DocumentMatchAnalysisRow {
  key: string
  label: string
  segment_count: number
  word_count: number
  percent: number
}

export interface DocumentMatchAnalysis {
  threshold: number
  collection_ids: string[]
  total_segments: number
  total_words: number
  rows: DocumentMatchAnalysisRow[]
}

export interface DocumentStatisticsTotals {
  pages: number | null
  words: number | null
  non_asian_words: number | null
  asian_characters: number | null
  characters: number | null
  characters_with_spaces: number | null
  paragraphs: number | null
  lines: number | null
  internal_repeated_words: number | null
  internal_repeated_characters: number | null
  cross_file_repeated_words: number | null
  cross_file_repeated_characters: number | null
  match_analysis: DocumentMatchAnalysis | null
}

export interface DocumentStatisticsReportItem {
  id: string
  report_id: string
  project_id: string
  file_record_id: string | null
  file_name: string
  source_language: string | null
  target_language: string | null
  file_size_bytes: number | null
  statistics: DocumentStatistics
  created_at: string | null
}

export interface DocumentStatisticsReport {
  id: string
  project_id: string
  created_by_id: string | null
  created_by_name: string | null
  file_ids: string[]
  total_files: number
  available_files: number
  totals: DocumentStatisticsTotals
  status: string
  created_at: string | null
  items: DocumentStatisticsReportItem[]
}

export interface DocumentStatisticsReportsResponse {
  items: DocumentStatisticsReport[]
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

export interface UploadBatchLimits {
  max_files_per_batch: number
  max_total_size_mb: number
  max_expanded_files: number
}

export interface UploadCapabilitiesResponse {
  extensions: string[]
  accept: string
  formats: UploadCapability[]
  limits?: UploadBatchLimits
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
  source_body_text?: string
  automatic_numbering_text?: string | null
  target_automatic_numbering_text?: string | null
  source_html?: string | null
  target_text: string
  target_html?: string | null
  status: string
  project_sync_disabled?: boolean
  version: number
  score: number
  matched_source_text: string | null
  matched_collection_name: string | null
  matched_creator_name: string | null
  matched_created_at: string | null
  matched_updated_at: string | null
  source: string
  llm_provider: string | null
  llm_model: string | null
  last_modified_by_id?: string | null
  last_modified_by?: User | null
  block_type: string
  block_index: number
  row_index?: number | null
  cell_index?: number | null
  workflow_step_id?: string | null
  workflow_step_name?: string | null
  workflow_step_order?: number | null
  can_write?: boolean
  qa_issues?: SegmentQAIssue[]
  updated_at: string | null
  /** 合并视图聚合读取时附带：句段所属文件 id 与文件名 */
  file_record_id?: string
  filename?: string
}

export type SegmentQAIssueSeverity = 'low' | 'medium' | 'high'
export type SegmentQAIssueStatus = 'open' | 'ignored' | 'resolved'

export interface SegmentQAIssue {
  id: string
  project_id: string | null
  file_record_id: string
  segment_id: string
  sentence_id: string
  rule_key: string
  provider: string
  language: string
  severity: SegmentQAIssueSeverity
  message: string
  short_message: string
  rule_id: string
  rule_category: string
  issue_type: string
  context_text: string
  offset: number
  length: number
  replacements: string[]
  target_text_hash: string
  status: SegmentQAIssueStatus
  ignored: boolean
  ignored_at: string | null
  ignored_by_id: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ProjectSegmentSyncSummary {
  filled_count: number
  updated_count: number
  conflict_count: number
  affected_file_count: number
}

export interface ProjectSyncDisableResult {
  updated_count: number
  disabled_count: number
  cleared_count: number
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
  collection_ids: string[]
  tm_match_threshold: number
  collection_name: string | null
  term_base_id: string | null
  term_base_name: string | null
  term_base_ids: string[]
  term_base_names: string[]
  term_base_write_ids: string[]
  term_base_write_names: string[]
  qa_term_base_ids: string[]
  qa_term_base_names: string[]
  glossary_base_ids: string[]
  glossary_base_names: string[]
  translation_guidelines: string
  created_at: string
  updated_at: string
  server_time?: string
  total_segments: number
  skip: number
  limit: number
  source_extension: string
  has_source_document: boolean
  can_export: boolean
  can_manage?: boolean
  can_write?: boolean
  workflow_steps?: WorkflowStep[]
  workflow_progress?: WorkflowProgress[]
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
  source_exclude: string
  target_exclude: string
  search_fuzzy: boolean
  status_filters?: string[]
  match_filters?: string[]
  source_filters?: string[]
  workflow_step_ids?: string[]
}

export interface SegmentPageResponse {
  file_record_id: string
  total_segments: number | null
  matched_segments: number
  status_stats: SegmentStatusStats | null
  skip: number
  limit: number
  filters: SegmentPageFilters
  server_time?: string
  segments: Segment[]
}

export interface SegmentPositionResponse {
  file_record_id: string
  sentence_id: string
  segment_id: string
  index: number
  display_index: number
  page: number
  page_size: number
  page_index: number
}

/** 合并视图摘要（列表项） */
export interface MergeView {
  id: string
  project_id: string
  name: string
  file_ids: string[]
  file_count: number
  available_file_count: number
  creator_id: string | null
  creator_name: string | null
  created_at: string | null
  updated_at: string | null
}

/** 合并视图中的文件元数据（详情用） */
export interface MergeViewFile {
  id: string
  filename: string
  status: string
  total_segments: number
  status_stats: SegmentStatusStats
  source_language: string | null
  target_language: string | null
  progress: number
  can_write?: boolean
  is_edit_locked: boolean
}

export interface MergeViewLanguagePair {
  source_language: string | null
  target_language: string | null
  file_count: number
}

/** 合并视图详情 */
export interface MergeViewDetail {
  id: string
  project_id: string
  name: string
  file_ids: string[]
  files: MergeViewFile[]
  total_files: number
  total_segments: number
  is_mixed_language_pair: boolean
  language_pairs: MergeViewLanguagePair[]
  creator_id: string | null
  created_at: string | null
  updated_at: string | null
}

/** 合并视图聚合分页中的分组边界信息 */
export interface MergeViewSegmentGroup {
  file_record_id: string
  filename: string
  matched_segments: number
  page_segment_count: number
}

/** 合并视图聚合句段分页响应 */
export interface MergeViewSegmentPageResponse {
  merge_view_id: string
  project_id: string
  name: string
  total_segments: number
  matched_segments: number
  skip: number
  limit: number
  filters: SegmentPageFilters
  groups: MergeViewSegmentGroup[]
  server_time?: string
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

export interface GlossaryBase {
  id: string
  name: string
  description: string | null
  source_language: string
  target_language: string
  created_at: string
  updated_at: string
  entry_count: number
}

export interface ProjectTermBaseSettingRow {
  id: string
  name: string
  description: string | null
  source_language: string
  target_language: string
  entry_count: number
  enabled: boolean
  writable: boolean
  qa: boolean
  qa_priority: number | null
}

export interface ProjectTermBaseSettingGroup {
  source_language: string
  target_language: string
  file_count: number
  enabled_term_base_ids: string[]
  writable_term_base_ids: string[]
  qa_term_base_ids: string[]
  term_bases: ProjectTermBaseSettingRow[]
}

export interface ProjectTermBaseSettingsResponse {
  project_id: string
  groups: ProjectTermBaseSettingGroup[]
}

export interface QualityQASettingsResponse {
  project_id: string
  settings: {
    rules: Record<string, {
      enabled: boolean
    }>
    spelling_grammar: {
      enabled: boolean
      severity: SegmentQAIssueSeverity
    }
  }
  languagetool_configured: boolean
  supported_languages: Array<{
    code: string
    label: string
    languagetool_code: string | null
    supported: boolean
  }>
  target_languages: Array<{
    language: string
    file_count: number
    supported: boolean
    languagetool_code: string | null
  }>
}

export interface ProjectTranslationMemorySettingCollection {
  id: string
  name: string
  description: string | null
  source_language: string
  target_language: string
  entry_count: number
}

export interface ProjectTranslationMemorySettingFile {
  id: string
  filename: string
  collection_id: string | null
  collection_ids: string[]
  tm_match_threshold: number
}

export interface ProjectTranslationMemorySettingGroup {
  source_language: string
  target_language: string
  file_count: number
  collections: ProjectTranslationMemorySettingCollection[]
  files: ProjectTranslationMemorySettingFile[]
}

export interface ProjectTranslationMemorySettingsResponse {
  project_id: string
  groups: ProjectTranslationMemorySettingGroup[]
  initial_match_updated_count?: number
}

export interface TermQAReportItem {
  id: string
  report_id: string
  project_id: string | null
  file_record_id: string
  segment_id: string | null
  term_base_id: string | null
  sentence_id: string
  file_name: string
  term_base_name: string
  source_term: string
  expected_target_term: string
  source_text: string
  target_text: string
  block_index: number
  row_index: number | null
  cell_index: number | null
  ignored: boolean
  ignored_at: string | null
  ignored_by_id: string | null
  ignored_by_name: string | null
  created_at: string | null
}

export interface TermQAReport {
  id: string
  project_id: string | null
  file_record_id: string | null
  created_by_id: string | null
  scope: 'project' | 'file'
  file_ids: string[]
  term_base_ids: string[]
  language_pairs: Array<{ source_language: string, target_language: string }>
  total_files: number
  total_segments: number
  checked_segments: number
  issue_count: number
  active_issue_count: number
  ignored_count: number
  status: string
  created_at: string | null
  items: TermQAReportItem[]
}

export interface TermQAReportListResponse {
  items: TermQAReport[]
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
  creator_id?: string | null
  creator_name?: string | null
  last_modified_by_id?: string | null
  last_modified_by_name?: string | null
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
  creator_id?: string | null
  creator_name: string | null
  last_modified_by_id?: string | null
  last_modified_by_name?: string | null
  created_at: string
  updated_at: string
}

export interface GlossaryEntryRecord {
  id: string
  glossary_base_id: string
  source_text: string
  target_text: string
  note: string | null
  source_language: string
  target_language: string
  creator_id?: string | null
  creator_name: string | null
  last_modified_by_id?: string | null
  last_modified_by_name?: string | null
  created_at: string
  updated_at: string
}

export interface GlossaryMatch {
  source_text: string
  target_text: string
  note: string | null
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
  skipped_duplicate_rows?: number
  skipped_empty_rows: number
  skipped_header_rows: number
  imported_rows: number
  collection_id: string | null
  collection_name: string | null
  source_language: string
  target_language: string
}

export type ImportPreviewStatus = 'create' | 'update' | 'keep' | 'duplicate' | 'empty' | 'header' | 'pending'

export interface TMImportPreviewRow {
  row_index: number
  source_text: string
  target_text: string
  status: ImportPreviewStatus
  message: string
}

export interface TMImportPreview {
  filename: string
  rows: TMImportPreviewRow[]
  total_rows: number
  valid_rows: number
  create_rows: number
  update_rows: number
  keep_rows: number
  duplicate_rows: number
  skipped_empty_rows: number
  skipped_header_rows: number
  preview_limit: number
  duplicate_policy: 'overwrite' | 'keep'
  scanned_rows: number
  truncated: boolean
  max_scan_rows?: number
  collection_id: string | null
  collection_name: string
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
  skipped_duplicate_rows?: number
  skipped_empty_rows: number
  skipped_header_rows: number
  imported_rows: number
  term_base_id: string
  term_base_name: string
  source_language: string
  target_language: string
}

export interface TermImportPreviewRow {
  row_index: number
  source_text: string
  target_text: string
  status: ImportPreviewStatus
  message: string
}

export interface TermImportPreview {
  filename: string
  rows: TermImportPreviewRow[]
  total_rows: number
  valid_rows: number
  create_rows: number
  update_rows: number
  duplicate_rows: number
  skipped_empty_rows: number
  skipped_header_rows: number
  preview_limit: number
  scanned_rows: number
  truncated: boolean
  max_scan_rows?: number
  term_base_id: string | null
  term_base_name: string
  source_language: string
  target_language: string
}

export interface GlossaryImportSummary {
  filename: string
  created_rows: number
  updated_rows: number
  skipped_empty_rows: number
  skipped_header_rows: number
  imported_rows: number
  glossary_base_id: string
  glossary_base_name: string
  source_language: string
  target_language: string
}

export interface GlossaryImportPreviewRow {
  row_index: number
  source_text: string
  target_text: string
  note: string
  status: ImportPreviewStatus
  message: string
}

export interface GlossaryImportPreview {
  filename: string
  rows: GlossaryImportPreviewRow[]
  total_rows: number
  valid_rows: number
  create_rows: number
  update_rows: number
  duplicate_rows: number
  skipped_empty_rows: number
  skipped_header_rows: number
  preview_limit: number
  scanned_rows: number
  truncated: boolean
  max_scan_rows?: number
  glossary_base_id: string | null
  glossary_base_name: string
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
  target_html?: string | null
  source: string
  track_revision?: boolean
  base_version?: number | null
  confirm?: boolean
  /** 合并视图模式：该 dirty 条目归属的文件 id（前端分组保存用，后端 PUT 忽略） */
  file_record_id?: string
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

export interface RevisionAuthorColors {
  insert: string
  delete: string
}

export interface RevisionDisplaySettings {
  id: string | null
  file_record_id: string
  show_author_time: boolean
  show_others_revisions: boolean
  default_insert_color: string
  default_delete_color: string
  author_colors: Record<string, RevisionAuthorColors>
  updated_by: User | null
  updated_at: string | null
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

export type LLMTranslateScope = 'current_segment' | 'fuzzy_only' | 'none_only' | 'empty_target_only' | 'all' | 'all_with_exact'
export type LLMProvider = 'auto' | 'deepseek' | 'openrouter'

export interface LLMGuidelineOptions {
  guidelineTemplateId?: string
  temporaryPrompt?: string
  model?: string
  sentenceId?: string
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

// ============ 参考文件分析 ============

export interface ReferenceFile {
  id: string
  filename: string
  file_size: number
  is_bilingual_source: boolean
  is_bilingual_target: boolean
  bilingual_pair_id: string | null
  created_at: string
}

export interface ReferenceProfile {
  id: string
  file_record_id: string | null
  source_files: string[]
  terminology_count: number
  tm_count: number
  style: ReferenceStyleGuide | null
  analysis_report: ReferenceAnalysisReport | null
  overall_confidence: number
  created_at: string
  updated_at: string
}

export interface ReferenceStyleGuide {
  tone: string | null
  person: string | null
  preferences: string[]
  avoid: string[]
}

export interface ReferenceAnalysisReport {
  industry: string
  industry_confidence: number
  industry_signals: string[]
  strategy: string
  strategy_reasoning: string
  preserve_structure: boolean
  client_profile: string
  formality_level: number
  brand_terms: ReferenceTermEntry[]
  abbreviations: ReferenceAbbreviation[]
  term_conflicts: ReferenceTermConflict[]
  risk_points: ReferenceRiskPoint[]
  format_spec: ReferenceFormatSpec
  fixed_patterns: ReferenceSentencePair[]
  overall_confidence: number
  analysis_notes: string[]
}

export interface ReferenceTermEntry {
  source: string
  target: string
  context: string | null
  category: string | null
}

export interface ReferenceAbbreviation {
  abbr: string
  full_form: string
  translation: string | null
}

export interface ReferenceTermConflict {
  source: string
  translations: string[]
  recommendation: string | null
  note: string | null
}

export interface ReferenceRiskPoint {
  category: string
  description: string
  examples: string[]
  suggestion: string | null
}

export interface ReferenceFormatSpec {
  number_format: string | null
  date_format: string | null
  currency_format: string | null
  unit_format: string | null
  heading_style: string | null
  list_style: string | null
  notes: string[]
}

export interface ReferenceSentencePair {
  source: string
  target: string
  similarity: number
}

export interface ReferenceAnalyzeResponse {
  profile_id: string
  source_files: string[]
  terminology_count: number
  tm_count: number
  style: ReferenceStyleGuide | null
  analysis_report: ReferenceAnalysisReport | null
  overall_confidence: number
}

export interface ReferenceMatchResult {
  exact_matches: ReferenceExactMatch[]
  fuzzy_matches: ReferenceFuzzyMatch[]
  term_matches: ReferenceTermMatch[]
  exact_count: number
  fuzzy_count: number
  term_count: number
}

export interface ReferenceExactMatch {
  segment_id: string
  source: string
  target: string
  match_type: 'reference-exact'
  similarity: number
  source_file: string
}

export interface ReferenceFuzzyMatch {
  segment_id: string
  source: string
  matched_source: string
  target: string
  similarity: number
  match_type: 'reference-fuzzy'
  source_file: string
}

export interface ReferenceTermMatch {
  segment_id: string
  terms: Array<{
    source: string
    target: string
    category: string | null
    source_file: string
  }>
  source_file: string
}

export interface ReferenceApplyResult {
  applied_count: number
  skipped_count: number
}
