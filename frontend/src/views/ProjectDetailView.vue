<script setup lang="ts">
import axios from 'axios'
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  ChevronUp,
  CircleHelp,
  Clock3,
  Copy,
  Download,
  FileText,
  Filter,
  Flag,
  FolderOpen,
  Link,
  Loader2,
  MoreHorizontal,
  Plus,
  ReplaceAll,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  Users,
  RotateCcw,
  X,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import {
  isImportTaskAccepted,
  waitForImportTask,
  type ImportTaskAccepted,
} from '../api/importTasks'
import {
  createProjectMergeView,
  deleteMergeView,
  listProjectMergeViews,
  updateMergeView,
} from '../api/mergeViews'
import { http } from '../api/http'
import DataTable from '../components/DataTable.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import DocumentParseSettings from '../components/DocumentParseSettings.vue'
import IssueMarkerDialog from '../components/IssueMarkerDialog.vue'
import Modal from '../components/base/Modal.vue'
import PreTranslateDialog from '../components/PreTranslateDialog.vue'
import Pagination from '../components/Pagination.vue'
import ResourceImportDialog from '../components/ResourceImportDialog.vue'
import StateView from '../components/base/StateView.vue'
import TermExtractionDialog from '../components/TermExtractionDialog.vue'
import WorkflowProgressSummary from '../components/WorkflowProgressSummary.vue'
import { useConfirm } from '../composables/useConfirm'
import { usePageHeader } from '../composables/usePageHeader'
import { useToast } from '../composables/useToast'
import { canonicalizeLanguagePair, formatLanguagePair, getLanguageLabel, languageOptions } from '../constants/languages'
import { getFileStatusMeta } from '../constants/status'
import { buildTranslatedTaskFilename, supportedTaskFileAccept } from '../constants/taskFiles'
import { useAuthStore } from '../stores/auth'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'
import {
  getExportOptionExtensionLabel,
  groupExportOptions,
  type FileExportOption,
} from '../utils/exportOptions'
import { getProgressStyle, isProgressComplete } from '../utils/progress'
import { matchesSearchKeyword, normalizeSearchKeyword, splitSearchKeywords } from '../utils/search'
import type {
  DocumentParseMode,
  DocumentParseOptions,
  DocumentMatchAnalysis,
  DocumentStatistics,
  DocumentStatisticsReport,
  DocumentStatisticsReportItem,
  DocumentStatisticsReportsResponse,
  DocumentStatisticsTotals,
  AssignmentEvent,
  AssignmentEventsResponse,
  IssueMarker,
  IssueStatus,
  MergeView,
  ProjectAssignmentsResponse,
  ProjectSyncDisableResult,
  QualityQASettingsResponse,
  ProjectTranslationMemorySettingCollection,
  ProjectTermBaseSettingGroup,
  ProjectTermBaseSettingRow,
  ProjectTermBaseSettingsResponse,
  ProjectTranslationMemorySettingFile,
  ProjectTranslationMemorySettingGroup,
  ProjectTranslationMemorySettingsResponse,
  TermBase,
  TermQAReport,
  TMCollection,
  UploadCapabilitiesResponse,
  UploadCapability,
  UploadBatchLimits,
  User,
  WorkflowProgress,
  WorkflowStep,
} from '../types/api'

const props = defineProps<{
  id: string
}>()

type ProjectTab = 'files' | 'views' | 'issues' | 'assignments' | 'settings' | 'stats' | 'summary' | 'quote'
type ProjectSettingsSection = 'basic' | 'guidelines' | 'translation-memory' | 'terms' | 'automation' | 'quality-qa' | 'term-qa'
type AccessLevel = 'team' | 'private' | 'public'
type DocumentStatisticNumberKey =
  | 'pages'
  | 'words'
  | 'non_asian_words'
  | 'asian_characters'
  | 'characters'
  | 'characters_with_spaces'
  | 'paragraphs'
  | 'lines'
  | 'internal_repeated_words'
  | 'internal_repeated_characters'
  | 'cross_file_repeated_words'
  | 'cross_file_repeated_characters'
  | 'image_count'
  | 'unique_image_count'
  | 'inline_image_count'
  | 'floating_image_count'
  | 'linked_image_count'
  | 'chart_count'
  | 'smartart_count'

interface ProjectDetail {
  id: string
  name: string
  filename: string
  status: string
  progress: number
  file_count: number
  total_segments: number
  translated_segments: number
  confirmed_segments: number
  pretranslated_segments: number
  pretranslation_progress: number
  project_sync_segment_count: number
  project_sync_disabled_count: number
  workflow_steps?: WorkflowStep[]
  workflow_progress?: WorkflowProgress[]
  source_language: string | null
  target_language: string | null
  creator: string | null
  deadline: string | null
  access_level: AccessLevel | null
  translation_guidelines: string
  term_base_id: string | null
  created_at: string
  updated_at: string
  has_source_document: boolean
  file_size_bytes: number | null
  issue_count: number
  open_issue_count: number
  can_manage?: boolean
  can_write?: boolean
  assigned_users?: User[]
  issue_markers: IssueMarker[]
  files: ProjectFileItem[]
}

interface ProjectFileItem {
  id: string
  project_id: string | null
  filename: string
  status: string
  active_operation: string | null
  active_operation_message: string
  is_edit_locked: boolean
  document_parse_mode: DocumentParseMode
  document_parse_options: DocumentParseOptions
  document_statistics?: DocumentStatistics
  progress: number
  total_segments: number
  translated_segments: number
  confirmed_segments: number
  pretranslated_segments: number
  pretranslation_progress: number
  workflow_steps?: WorkflowStep[]
  workflow_progress?: WorkflowProgress[]
  source_language: string | null
  target_language: string | null
  creator: string | null
  assignee_id: string | null
  assignee: User | null
  assignees?: User[]
  assigned_at: string | null
  can_manage?: boolean
  can_write?: boolean
  deadline: string | null
  access_level: AccessLevel | null
  created_at: string
  updated_at: string
  has_source_document: boolean
  file_size_bytes: number | null
  issue_count: number
  open_issue_count: number
  collection_id: string | null
  collection_ids: string[]
  tm_match_threshold: number
  term_base_id: string | null
  term_base_ids: string[]
  term_base_write_ids: string[]
  qa_term_base_ids: string[]
  glossary_base_ids: string[]
}

interface EnglishVariantCopyResponse {
  file: ProjectFileItem
  summary: {
    processed_segments: number
    changed_segments: number
    replacement_count: number
  }
}

interface AssignmentDraft {
  assignee_id: string
  workflow_step_id: string
  file_record_ids: Set<string>
  file_ranges: Map<string, AssignmentFileRangeDraft>
}

interface AssignmentFileRangeDraft {
  range_start: number | null
  range_end: number | null
}

type AssignmentFileRangeField = 'range_start' | 'range_end'

type AssignmentUserTypeFilter = 'all' | 'internal' | 'external'
type AssignmentUserStateFilter = 'all' | 'selected' | 'unselected'
type AssignmentFileStateFilter = 'all' | 'checked' | 'unchecked'
type ProjectResourceCreateKind = 'tm' | 'term'
type ProjectResourceLanguageAsset = TMCollection | TermBase

interface PreTranslateProgressState {
  progress: number
  status: string
  running: boolean
}

interface PreTranslateProgressPayload extends PreTranslateProgressState {
  fileId: string
}

interface ActivePretranslationTaskStatus {
  id: string
  file_record_id: string
  filename?: string | null
  status: string
  stage: string
  progress: number
  message: string
  provider?: string | null
  model?: string | null
  scope?: string | null
  total_segments: number
  unique_segments: number
  deduplicated_segments: number
  processed_segments: number
  updated_segments: number
  error_segments: number
  error?: string | null
}

interface ActivePretranslationTasksResponse {
  tasks: ActivePretranslationTaskStatus[]
}

interface ProjectDocumentStatisticsResponse {
  files: ProjectFileItem[]
  report: DocumentStatisticsReport
}

type LanguageDetectTone = 'info' | 'success' | 'warning' | 'error'
type LanguagePairSummary = {
  source_language: string | null
  target_language: string | null
  file_count: number
}

const DEFAULT_DOCUMENT_PARSE_OPTIONS: DocumentParseOptions = {
  include_headers_footers: true,
  include_footnotes_endnotes: true,
  include_comments: true,
  clean_format: false,
  preserve_hyperlinks: true,
  translate_code_blocks: true,
  extract_links: false,
  skip_non_translatable: true,
  xml_inline_elements_no_split: true,
  custom_parse_config: false,
  translate_idml_comments: false,
  translate_idml_hidden_layers: false,
  pptx_translate_comments: true,
  pptx_translate_notes: true,
  pptx_translate_document_properties: false,
  xlsx_translate_comments: true,
  xlsx_translate_drawing_text: true,
  xlsx_translate_sheet_names: false,
  xlsx_translate_hidden_content: true,
  xlsx_translate_document_properties: false,
  xlsx_translate_numeric_cells: true,
  xlsx_translate_date_cells: true,
  xlsx_translate_boolean_cells: true,
  xlsx_translate_formula_cells: false,
  xlsx_skip_fill_colors: [],
}

const DEFAULT_UPLOAD_MAX_SIZE_MB = 100
const PROJECT_FILE_SORT_KEYS = new Set<string>([
  'filename',
  'progress',
  'pretranslation_progress',
  'taskManage',
  'status',
  'open_issue_count',
])

const DOCUMENT_STATISTIC_NUMBER_KEYS: DocumentStatisticNumberKey[] = [
  'pages',
  'words',
  'non_asian_words',
  'asian_characters',
  'characters',
  'characters_with_spaces',
  'paragraphs',
  'lines',
  'internal_repeated_words',
  'internal_repeated_characters',
  'cross_file_repeated_words',
  'cross_file_repeated_characters',
  'image_count',
  'unique_image_count',
  'inline_image_count',
  'floating_image_count',
  'linked_image_count',
  'chart_count',
  'smartart_count',
]

const DOCUMENT_MATCH_ANALYSIS_ROW_KEYS = [
  'new',
  'tm_50_74',
  'tm_75_84',
  'tm_85_94',
  'tm_95_99',
  'tm_100',
  'tm_101',
  'tm_102',
  'internal_repeat',
  'cross_file_repeat',
] as const

type DocumentMatchAnalysisRowKey = typeof DOCUMENT_MATCH_ANALYSIS_ROW_KEYS[number]

interface DocumentMatchAnalysisDisplayRow {
  key: DocumentMatchAnalysisRowKey | 'total'
  label: string
  percent: number
  segment_count: number
  word_count: number
  is_total?: boolean
}

interface DocumentFileMatchAnalysisBlock {
  id: string
  file_name: string
  analysis: DocumentMatchAnalysis
  rows: DocumentMatchAnalysisDisplayRow[]
}

interface LanguageDetectResponse {
  language: string | null
  label: string | null
  confidence: number
  supported: boolean
  sample_length: number
  message: string
}

type ProjectRow = ProjectFileItem | Record<string, any>
type DerivedFileKind = 'template-copy' | 'british-copy' | 'american-copy'
type ProjectFileSortKey =
  | 'filename'
  | 'progress'
  | 'pretranslation_progress'
  | 'taskManage'
  | 'status'
  | 'open_issue_count'
type FileExportStatus = 'queued' | 'running' | 'completed' | 'failed'
interface FileExportTask {
  task_id: string
  status: FileExportStatus
  progress: number
  message?: string
  error?: string | null
  filename?: string | null
  size_bytes?: number | null
}
const confirm = useConfirm()
const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { t } = useI18n()

const loading = ref(false)
const deleting = ref(false)
const duplicating = ref(false)
const creatingEnglishVariantCopy = ref(false)
const uploading = ref(false)
const statisticsLoading = ref(false)
const statisticsReportsLoading = ref(false)
const uploadPercent = ref(0)
const project = ref<ProjectDetail | null>(null)
const pageError = ref('')
const uploadMessage = ref('')
const detectingLanguage = ref(false)
const languageDetectMessage = ref('')
const languageDetectTone = ref<LanguageDetectTone>('info')
const selectedFiles = ref<File[]>([])
const uploadSourceLanguage = ref('')
const uploadTargetLanguages = ref<string[]>([])
const uploadTargetLanguageSearch = ref('')
const uploadTargetMenuOpen = ref(false)
const documentParseMode = ref<DocumentParseMode>('full')
const documentParseOptions = ref<DocumentParseOptions>({ ...DEFAULT_DOCUMENT_PARSE_OPTIONS })
const uploadCapabilities = ref<UploadCapability[]>([])
const uploadLimits = ref<UploadBatchLimits>({
  max_files_per_batch: 50,
  max_total_size_mb: 500,
  max_expanded_files: 100,
})
const uploadFileAccept = ref(supportedTaskFileAccept)
const loadingUploadCapabilities = ref(false)
const basicCollapsed = ref(route.hash.startsWith('#project-settings-'))

function toggleBasicCollapsed() {
  const scrollLeft = window.scrollX
  const scrollTop = window.scrollY
  basicCollapsed.value = !basicCollapsed.value

  requestAnimationFrame(() => {
    window.scrollTo(scrollLeft, scrollTop)
  })
  window.setTimeout(() => {
    window.scrollTo(scrollLeft, scrollTop)
  }, 280)
}

function getProjectSettingsSectionFromHash(hash: string): ProjectSettingsSection {
  switch (hash) {
    case '#project-settings-guidelines':
      return 'guidelines'
    case '#project-settings-translation-memory':
      return 'translation-memory'
    case '#project-settings-terms':
      return 'terms'
    case '#project-settings-automation':
      return 'automation'
    case '#project-settings-quality-qa':
      return 'quality-qa'
    case '#project-settings-term-qa':
      return 'term-qa'
    case '#project-settings-basic':
    default:
      return 'basic'
  }
}

const activeTab = ref<ProjectTab>(route.hash.startsWith('#project-settings-') ? 'settings' : 'files')
const activeProjectSettingsSection = ref<ProjectSettingsSection>(getProjectSettingsSectionFromHash(route.hash))
const showUploadModal = ref(false)
const showPreTranslateDialog = ref(false)
const showIssueDialog = ref(false)
const showTermExtractionDialog = ref(false)
const showAssignmentDialog = ref(false)
const showMergeViewDialog = ref(false)
const showTMImportDialog = ref(false)
const showTermImportDialog = ref(false)
const termExtractionNeedsReload = ref(false)
const uploadInputKey = ref(0)
const openActionMenuId = ref<string | null>(null)
const actionMenuStyle = ref<Record<string, string>>({})
const currentPage = ref(1)
const pageSize = ref(10)
const selectedFileIds = ref(new Set<string>())
const showFileSelectionMenu = ref(false)
const fileSelectionRangeStart = ref('1')
const fileSelectionRangeEnd = ref('1')
const fileSelectionRangeError = ref('')
const fileSearchQuery = ref('')
const fileSortKey = ref<ProjectFileSortKey | ''>('')
const fileSortOrder = ref<'asc' | 'desc'>('asc')
const fileStatusFilter = ref('all')
const fileLanguagePairFilter = ref('all')
const fileAssigneeFilter = ref('all')
const mergeViews = ref<MergeView[]>([])
const loadingMergeViews = ref(false)
const savingMergeView = ref(false)
const mergeViewActionId = ref('')
const mergeViewDialogMode = ref<'create' | 'rename'>('create')
const mergeViewDialogError = ref('')
const mergeViewName = ref('')
const activeMergeView = ref<MergeView | null>(null)
const statisticsSelectedFileIds = ref(new Set<string>())
const statisticsResultFileIds = ref(new Set<string>())
const statisticsReports = ref<DocumentStatisticsReport[]>([])
const activeStatisticsReportId = ref('')
const settingsForm = reactive({
  name: '',
  deadline: '',
  access_level: 'team' as AccessLevel,
})
const settingsError = ref('')
const savingSettings = ref(false)
const guidelinesText = ref('')
const savingGuidelines = ref(false)
const preTranslateProgressByFileId = ref<Record<string, PreTranslateProgressState>>({})
const activePretranslationTaskIdByFileId = ref<Record<string, string>>({})
const cancelingPretranslationTaskIds = ref(new Set<string>())
const issueDialogTarget = ref<{
  fileRecordId: string | null
  label: string
} | null>(null)
const updatingIssueId = ref<string | null>(null)
const assignableUsers = ref<User[]>([])
const loadingAssignableUsers = ref(false)
const loadingAssignments = ref(false)
const savingAssignment = ref(false)
const assignmentDrafts = ref<AssignmentDraft[]>([])
const activeAssignmentWorkflowStepId = ref('')
const assignmentUserSearch = ref('')
const assignmentUserTypeFilter = ref<AssignmentUserTypeFilter>('all')
const assignmentUserStateFilter = ref<AssignmentUserStateFilter>('all')
const assignmentFileSearch = ref('')
const assignmentFileStateFilter = ref<AssignmentFileStateFilter>('all')
const assignmentTooltipText = ref('')
const assignmentTooltipStyle = ref<Record<string, string>>({})
const assignmentEvents = ref<AssignmentEvent[]>([])
const assignmentEventsLoading = ref(false)
const translationMemorySettings = ref<ProjectTranslationMemorySettingsResponse | null>(null)
const loadingTranslationMemorySettings = ref(false)
const savingTranslationMemorySettings = ref(false)
const creatingTranslationMemoryPair = ref('')
const translationMemorySettingsError = ref('')
const expandedTMCollectionKey = ref('')
const tmSettingsSearchQuery = ref('')
const showProjectResourceCreateDialog = ref(false)
const projectResourceCreateKind = ref<ProjectResourceCreateKind>('tm')
const projectResourceCreateGroupKey = ref('')
const projectResourceCreateForm = reactive({
  name: '',
  description: '',
  sourceLanguage: '',
  targetLanguage: '',
})
const projectResourceCreateError = ref('')
const projectResourceCreateSubmitting = ref(false)
const projectResourceCreateTitle = computed(() => (
  projectResourceCreateKind.value === 'tm' ? '创建记忆库' : '创建术语库'
))
const projectResourceCreateDescription = computed(() => (
  projectResourceCreateKind.value === 'tm'
    ? '填写名称和说明后，为当前语言对创建新的翻译记忆库。'
    : '填写名称和说明后，为当前语言对创建新的术语库。'
))
const projectResourceCreateNameLabel = computed(() => (
  projectResourceCreateKind.value === 'tm' ? '记忆库名称' : '术语库名称'
))
const projectResourceCreateNamePlaceholder = computed(() => (
  projectResourceCreateKind.value === 'tm'
    ? '例如：技术文档中英记忆库'
    : '例如：医疗器械中英术语库'
))
const projectResourceCreateSubmitText = computed(() => {
  if (projectResourceCreateSubmitting.value) {
    return '创建中...'
  }
  return projectResourceCreateKind.value === 'tm' ? '创建记忆库' : '创建术语库'
})
const showProjectResourceLanguageDialog = ref(false)
const projectResourceLanguageKind = ref<ProjectResourceCreateKind>('tm')
const projectResourceLanguageTarget = reactive({
  sourceLanguage: '',
  targetLanguage: '',
  pairLabel: '',
})
const projectResourceLanguageResources = ref<ProjectResourceLanguageAsset[]>([])
const projectResourceLanguageSearchQuery = ref('')
const projectResourceLanguageSelectedId = ref('')
const projectResourceLanguageLoading = ref(false)
const projectResourceLanguageSubmitting = ref(false)
const projectResourceLanguageError = ref('')
const projectResourceLanguageAssetLabel = computed(() => (
  projectResourceLanguageKind.value === 'tm' ? '记忆库' : '术语库'
))
const projectResourceLanguageEntryLabel = computed(() => (
  projectResourceLanguageKind.value === 'tm' ? '条记忆条目' : '条术语'
))
const projectResourceLanguageTitle = computed(() => (
  `复制${projectResourceLanguageAssetLabel.value}为当前语言对`
))
const projectResourceLanguageDescription = computed(() => (
  `从已有${projectResourceLanguageAssetLabel.value}复制一个当前项目分组语言对的新库，原库不会被修改。`
))
const filteredProjectResourceLanguageResources = computed(() => {
  const keywords = getResourceSettingsSearchKeywords(projectResourceLanguageSearchQuery.value)
  if (keywords.length === 0) {
    return projectResourceLanguageResources.value
  }
  return projectResourceLanguageResources.value.filter((resource) => {
    const searchText = getProjectResourceLanguageSearchText(resource)
    return keywords.every((keyword) => searchText.includes(keyword))
  })
})
const selectedProjectResourceLanguageResource = computed(() => (
  projectResourceLanguageResources.value.find((resource) => resource.id === projectResourceLanguageSelectedId.value) ?? null
))
const tmImportDialogContext = ref<{
  collectionId: string
  collectionName: string
  sourceLanguage: string | null
  targetLanguage: string | null
}>({
  collectionId: '',
  collectionName: '',
  sourceLanguage: null,
  targetLanguage: null,
})
const termBaseSettings = ref<ProjectTermBaseSettingsResponse | null>(null)
const loadingTermBaseSettings = ref(false)
const savingTermBaseSettings = ref(false)
const creatingTermBasePair = ref('')
const pendingTMCollectionTopMoves = new Map<string, Set<string>>()
const pendingTermBaseTopMoves = new Map<string, Set<string>>()
let resourceTopMoveTimer: number | null = null
const termBaseSettingsError = ref('')
const termBaseSettingsSearchQuery = ref('')
const termImportDialogContext = ref<{
  termBaseId: string
  termBaseName: string
  sourceLanguage: string | null
  targetLanguage: string | null
}>({
  termBaseId: '',
  termBaseName: '',
  sourceLanguage: null,
  targetLanguage: null,
})
const qualityQASettings = ref<QualityQASettingsResponse | null>(null)
const loadingQualityQASettings = ref(false)
const savingQualityQASettings = ref(false)
const qualityQASettingsError = ref('')
const projectSyncToggleLoading = ref(false)

const qualityQARules = [
  { key: 'target_without_tag', label: '译文无标记', defaultEnabled: true },
  { key: 'target_tag_missing', label: '译文标记丢失', defaultEnabled: true },
  { key: 'unmatched_closing_tag', label: '结束标记无匹配的开始标记', defaultEnabled: true },
  { key: 'unmatched_opening_tag', label: '开始标记无匹配的结束标记', defaultEnabled: true },
  { key: 'target_placeholder_missing', label: '译文占位符标记丢失', defaultEnabled: true },
  { key: 'spelling_grammar', label: '译文有拼写或语法错误（查看支持的语种）', defaultEnabled: true },
  { key: 'term_inconsistency', label: '术语不一致', defaultEnabled: false },
  { key: 'paired_punctuation_missing', label: '成对标点符号丢失', defaultEnabled: false },
  { key: 'ending_punctuation_mismatch', label: '原文和译文的结束标点不同', defaultEnabled: false },
  { key: 'repeated_punctuation', label: '重复标点', defaultEnabled: false },
  { key: 'extra_space_after_punctuation', label: '标点符号后有多余空格', defaultEnabled: false },
  { key: 'missing_space_after_punctuation', label: '标点符号后遗漏空格', defaultEnabled: false },
] as const

type QualityQAPlaceholderRule = {
  key: string
  label: string
  percent?: number
  suffix?: string
}

const qualityQAPlaceholderRules: readonly QualityQAPlaceholderRule[] = [
  { key: 'punctuation_leading_extra_space', label: '标点符号前有多余空格' },
  { key: 'punctuation_leading_missing_space', label: '标点符号前遗漏空格' },
  { key: 'multiple_spaces', label: '多个空格' },
  { key: 'segment_trailing_extra_space', label: '句段结束后有多余空格' },
  { key: 'source_target_initial_case_mismatch', label: '原文和译文首字母大小写不一致' },
  { key: 'target_word_multiple_upper_initials', label: '译文一个单词中有多个大写首字母' },
  { key: 'source_target_same_word_case_mismatch', label: '原文和译文的同一单词首字母有不同的大小写' },
  { key: 'target_word_count_exceeds_source', label: '译文字数超过原文字数的', percent: 50, suffix: '%' },
  { key: 'target_word_count_below_source', label: '译文字数少于原文字数的', percent: 50, suffix: '%' },
  { key: 'source_target_word_count_gap_too_large', label: '译文与原文字数相差过大' },
  { key: 'context_translation_mismatch', label: '翻译与上下文匹配不一致' },
  { key: 'number_mismatch', label: '原文和译文数字不一致' },
  { key: 'parameter_mismatch', label: '原文与译文参数不一致' },
  { key: 'email_mismatch', label: '原文与译文邮件信息不一致' },
  { key: 'link_mismatch', label: '原文和译文链接信息不一致' },
  { key: 'consecutive_duplicate_words', label: '连续重复单词' },
  { key: 'source_target_identical', label: '原文和译文相同' },
  { key: 'special_symbol_mismatch', label: '特殊符号不一致' },
]

type QualityQARuleKey = typeof qualityQARules[number]['key']
type QualityQARuleDraft = Record<QualityQARuleKey, boolean>

function createQualityQARuleDraft(): QualityQARuleDraft {
  return qualityQARules.reduce((draft, rule) => {
    draft[rule.key] = rule.defaultEnabled
    return draft
  }, {} as QualityQARuleDraft)
}

const qualityQADraft = reactive({
  rules: createQualityQARuleDraft(),
})

const enabledQualityQARuleCount = computed(() => (
  qualityQARules.filter((rule) => qualityQADraft.rules[rule.key]).length
))
const allQualityQARulesEnabled = computed(() => enabledQualityQARuleCount.value === qualityQARules.length)
const partiallyEnabledQualityQARules = computed(() => (
  enabledQualityQARuleCount.value > 0 && !allQualityQARulesEnabled.value
))
const spellingGrammarQAEnabled = computed(() => qualityQADraft.rules.spelling_grammar)
function getSavedQualityQARuleEnabled(rule: typeof qualityQARules[number]) {
  const ruleSetting = qualityQASettings.value?.settings.rules?.[rule.key]
  if (typeof ruleSetting?.enabled === 'boolean') {
    return ruleSetting.enabled
  }
  if (rule.key === 'spelling_grammar') {
    return Boolean(qualityQASettings.value?.settings.spelling_grammar.enabled)
  }
  return rule.defaultEnabled
}
const qualityQASettingsDirty = computed(() => (
  qualityQARules.some((rule) => qualityQADraft.rules[rule.key] !== getSavedQualityQARuleEnabled(rule))
))
const qualityQARuleStatusText = computed(() => {
  if (enabledQualityQARuleCount.value === 0) {
    return '全部关闭'
  }
  if (enabledQualityQARuleCount.value === qualityQARules.length) {
    return `全部启用（${enabledQualityQARuleCount.value}/${qualityQARules.length}）`
  }
  return `已启用 ${enabledQualityQARuleCount.value}/${qualityQARules.length}`
})
const qualityQARuleStatusClass = computed(() => (
  enabledQualityQARuleCount.value > 0 ? 'is-ok' : 'is-warn'
))
const qualityQARuleStatusHint = computed(() => (
  qualityQASettingsDirty.value
    ? '有未保存更改'
    : `已保存 · ${qualityQAPlaceholderRules.length} 项占位`
))

const autoLocalizationOptions = [
  '日期',
  '时间',
  '数字',
  '度量单位',
  '首字母缩写词',
  '字母数字字符串',
]

const lockPlaceholderOptions = [
  '内部重复',
  '跨文件重复',
  '100%记忆库匹配',
  '101%记忆库匹配',
  '102%记忆库匹配',
]
const generatingTermQAReport = ref(false)
const termQAReport = ref<TermQAReport | null>(null)
const downloadingTermQAReport = ref(false)
const exportingFileId = ref('')
const exportingFileType = ref('')
const exportFileProgress = ref(0)
const exportFileMessage = ref('')
const showProjectExportMenu = ref(false)
const loadingProjectExportOptions = ref(false)
const projectExportOptions = ref<FileExportOption[]>([])
const groupedProjectExportOptions = computed(() => groupExportOptions(projectExportOptions.value))
let exportPollTimer: number | null = null
let activePretranslationPollTimer: number | null = null
const ACTIVE_PRETRANSLATION_STATUSES = new Set(['queued', 'running', 'canceling'])
const ACTIVE_PRETRANSLATION_POLL_INTERVAL_MS = 2_500
const FILE_UNASSIGNED_FILTER = '__unassigned__'
const FILE_PAGE_QUERY_KEY = 'filePage'
const FILE_PAGE_SIZE_QUERY_KEY = 'filePageSize'
const FILE_PAGE_SIZES = [10, 20, 50, 100, 200]
const DEFAULT_FILE_PAGE_SIZE = FILE_PAGE_SIZES[0]

function getFirstQueryValue(value: unknown) {
  return Array.isArray(value) ? value[0] : value
}

function readPositiveQueryNumber(value: unknown, fallback: number) {
  const raw = getFirstQueryValue(value)
  const parsed = Number.parseInt(String(raw ?? ''), 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function readFilePageFromQuery() {
  return readPositiveQueryNumber(route.query[FILE_PAGE_QUERY_KEY], 1)
}

function readFilePageSizeFromQuery() {
  const size = readPositiveQueryNumber(route.query[FILE_PAGE_SIZE_QUERY_KEY], DEFAULT_FILE_PAGE_SIZE)
  return FILE_PAGE_SIZES.includes(size) ? size : DEFAULT_FILE_PAGE_SIZE
}

function getExpectedFilePaginationQueryValue(key: string) {
  if (key === FILE_PAGE_QUERY_KEY) {
    return currentPage.value > 1 ? String(currentPage.value) : undefined
  }
  if (key === FILE_PAGE_SIZE_QUERY_KEY) {
    return pageSize.value !== DEFAULT_FILE_PAGE_SIZE ? String(pageSize.value) : undefined
  }
  return undefined
}

function isFilePaginationQuerySynced() {
  const currentPageQuery = getFirstQueryValue(route.query[FILE_PAGE_QUERY_KEY])
  const currentPageSizeQuery = getFirstQueryValue(route.query[FILE_PAGE_SIZE_QUERY_KEY])
  return (
    currentPageQuery === getExpectedFilePaginationQueryValue(FILE_PAGE_QUERY_KEY)
    && currentPageSizeQuery === getExpectedFilePaginationQueryValue(FILE_PAGE_SIZE_QUERY_KEY)
  )
}

function syncFilePaginationToRouteQuery() {
  if (isFilePaginationQuerySynced()) {
    return
  }

  const query = { ...route.query }
  const pageQueryValue = getExpectedFilePaginationQueryValue(FILE_PAGE_QUERY_KEY)
  const pageSizeQueryValue = getExpectedFilePaginationQueryValue(FILE_PAGE_SIZE_QUERY_KEY)

  if (pageQueryValue) {
    query[FILE_PAGE_QUERY_KEY] = pageQueryValue
  } else {
    delete query[FILE_PAGE_QUERY_KEY]
  }
  if (pageSizeQueryValue) {
    query[FILE_PAGE_SIZE_QUERY_KEY] = pageSizeQueryValue
  } else {
    delete query[FILE_PAGE_SIZE_QUERY_KEY]
  }

  void router.replace({
    path: route.path,
    query,
    hash: route.hash,
  })
}

function applyFilePaginationFromRouteQuery() {
  const nextPageSize = readFilePageSizeFromQuery()
  const nextPage = readFilePageFromQuery()
  if (pageSize.value !== nextPageSize) {
    pageSize.value = nextPageSize
  }
  if (currentPage.value !== nextPage) {
    currentPage.value = nextPage
  }
  syncFilePaginationToRouteQuery()
}

function setFilePage(page: number) {
  currentPage.value = Math.max(1, Math.floor(page))
}

function setFilePageSize(size: number) {
  pageSize.value = FILE_PAGE_SIZES.includes(size) ? size : DEFAULT_FILE_PAGE_SIZE
  currentPage.value = 1
}

const tabs = computed(() => ([
  { key: 'files' as const, label: t('projectDetail.tabs.files'), disabled: false },
  { key: 'views' as const, label: t('projectDetail.tabs.views'), disabled: false },
  {
    key: 'issues' as const,
    label: `${t('projectDetail.tabs.issues')}${openIssueCount.value > 0 ? ` (${openIssueCount.value})` : ''}`,
    disabled: false,
  },
  { key: 'assignments' as const, label: '指派记录', disabled: !canAssignProject.value },
  { key: 'settings' as const, label: t('projectDetail.tabs.settings'), disabled: !canManageProject.value },
  { key: 'stats' as const, label: t('projectDetail.tabs.stats'), disabled: !canManageProject.value },
  { key: 'summary' as const, label: t('projectDetail.tabs.summary'), disabled: true },
  { key: 'quote' as const, label: t('projectDetail.tabs.quote'), disabled: true },
]))
const tableRows = computed<ProjectFileItem[]>(() => project.value?.files ?? [])
const projectFileById = computed(() => new Map(tableRows.value.map((file) => [file.id, file])))
const fileStatusFilterOptions = computed(() => {
  const counts = new Map<string, number>()
  for (const file of tableRows.value) {
    const status = String(file.status || '')
    if (!status) {
      continue
    }
    counts.set(status, (counts.get(status) ?? 0) + 1)
  }
  return Array.from(counts.entries())
    .map(([value, count]) => ({
      value,
      count,
      label: formatStatus(value),
    }))
    .sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'))
})
const fileLanguagePairFilterOptions = computed(() => {
  const options = new Map<string, { value: string; label: string; count: number }>()
  for (const file of tableRows.value) {
    const value = getFileLanguagePairKey(file)
    const label = getFileLanguagePairLabel(file)
    const current = options.get(value)
    if (current) {
      current.count += 1
    } else {
      options.set(value, { value, label, count: 1 })
    }
  }
  return Array.from(options.values())
    .sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'))
})
const fileAssigneeFilterOptions = computed(() => {
  const options = new Map<string, { value: string; label: string; count: number }>()
  let unassignedCount = 0
  for (const file of tableRows.value) {
    const assignees = getFileAssignees(file)
    if (assignees.length === 0) {
      unassignedCount += 1
      continue
    }
    for (const user of assignees) {
      const label = getAssigneeDisplayName(user)
      if (!user.id || !label) {
        continue
      }
      const current = options.get(user.id)
      if (current) {
        current.count += 1
      } else {
        options.set(user.id, { value: user.id, label, count: 1 })
      }
    }
  }
  const rows = Array.from(options.values())
    .sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'))
  if (unassignedCount > 0) {
    rows.unshift({ value: FILE_UNASSIGNED_FILTER, label: '未分配', count: unassignedCount })
  }
  return rows
})
const hasFileFilters = computed(() => (
  Boolean(normalizeFileFilterKeyword(fileSearchQuery.value))
  || fileStatusFilter.value !== 'all'
  || fileLanguagePairFilter.value !== 'all'
  || fileAssigneeFilter.value !== 'all'
))
const filteredTableRows = computed<ProjectFileItem[]>(() => {
  const keywords = splitSearchKeywords(fileSearchQuery.value)
  let rows = [...tableRows.value]

  if (keywords.length > 0) {
    rows = rows.filter((file) => {
      const text = getFileSearchText(file)
      return keywords.every((keyword) => matchesSearchKeyword(text, keyword, { minSubsequenceLength: 3 }))
    })
  }

  if (fileStatusFilter.value !== 'all') {
    rows = rows.filter((file) => file.status === fileStatusFilter.value)
  }

  if (fileLanguagePairFilter.value !== 'all') {
    rows = rows.filter((file) => getFileLanguagePairKey(file) === fileLanguagePairFilter.value)
  }

  if (fileAssigneeFilter.value === FILE_UNASSIGNED_FILTER) {
    rows = rows.filter((file) => getFileAssigneeIds(file).length === 0)
  } else if (fileAssigneeFilter.value !== 'all') {
    rows = rows.filter((file) => getFileAssigneeIds(file).includes(fileAssigneeFilter.value))
  }

  if (fileSortKey.value) {
    rows.sort(compareProjectFileRows)
  }

  return rows
})
const fileTableEmptyText = computed(() => (
  hasFileFilters.value ? '没有符合筛选条件的文件' : t('projectDetail.files.empty')
))
const projectWorkflowSteps = computed<WorkflowStep[]>(() => project.value?.workflow_steps ?? [])
const projectWorkflowProgress = computed<WorkflowProgress[]>(() => project.value?.workflow_progress ?? [])
const projectWorkflowLabel = computed(() => (
  projectWorkflowSteps.value.length > 0
    ? projectWorkflowSteps.value.map((step) => step.name).join(' / ')
    : getPlaceholder()
))
const activeAssignmentWorkflowStep = computed(() => (
  projectWorkflowSteps.value.find((step) => step.id === activeAssignmentWorkflowStepId.value) ?? projectWorkflowSteps.value[0] ?? null
))
const activeAssignmentDrafts = computed(() => (
  assignmentDrafts.value.filter((draft) => draft.workflow_step_id === activeAssignmentWorkflowStepId.value)
))
const assignmentMergeViews = computed(() => (
  mergeViews.value.filter((view) => getAssignableMergeViewFileIds(view).length >= 2)
))
const issueMarkers = computed<IssueMarker[]>(() => project.value?.issue_markers ?? [])
const openIssueCount = computed(() => issueMarkers.value.filter((marker) => marker.status === 'open').length)
const actionMenuRow = computed<ProjectFileItem | null>(() => {
  const id = openActionMenuId.value
  if (!id) return null
  return tableRows.value.find((r) => r.id === id) ?? null
})
const pagedRows = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredTableRows.value.slice(start, start + pageSize.value)
})
const indexOffset = computed(() => (currentPage.value - 1) * pageSize.value)
const currentPageFileRangeStart = computed(() => (
  pagedRows.value.length > 0 ? indexOffset.value + 1 : 0
))
const currentPageFileRangeEnd = computed(() => (
  pagedRows.value.length > 0 ? indexOffset.value + pagedRows.value.length : 0
))
const selectedFilteredFileCount = computed(() => (
  filteredTableRows.value.filter((row) => selectedFileIds.value.has(row.id)).length
))
const selectedCurrentPageFileCount = computed(() => (
  pagedRows.value.filter((row) => selectedFileIds.value.has(row.id)).length
))
const allFilteredFilesSelected = computed(() => (
  filteredTableRows.value.length > 0
  && filteredTableRows.value.every((row) => selectedFileIds.value.has(row.id))
))
const fileSelectionAllLabel = computed(() => (
  hasFileFilters.value
    ? `全选筛选结果（${filteredTableRows.value.length}）`
    : `全选全部文件（${filteredTableRows.value.length}）`
))
const fileSelectionRangeHint = computed(() => (
  filteredTableRows.value.length > 0
    ? `按当前筛选后的序号选择，范围 1-${filteredTableRows.value.length}`
    : '当前没有可选择的文件'
))
const selectedProjectFiles = computed(() => (
  tableRows.value.filter((row) => selectedFileIds.value.has(row.id))
))
const hasSelectedLockedFile = computed(() => selectedProjectFiles.value.some((row) => row.is_edit_locked))
const hasSelectedNonWritableFile = computed(() => selectedProjectFiles.value.some((row) => !row.can_write))
const selectedMergeViewFiles = computed(() => (
  selectedProjectFiles.value.filter((row) => canUseFileInMergeView(row))
))
const selectedMergeViewInvalidFiles = computed(() => (
  selectedProjectFiles.value.filter((row) => !canUseFileInMergeView(row))
))
const selectedMergeViewLanguagePairs = computed<LanguagePairSummary[]>(() => {
  const pairMap = new Map<string, LanguagePairSummary>()
  let invalidCount = 0
  for (const file of selectedMergeViewFiles.value) {
    const pair = canonicalizeLanguagePair(
      file.source_language || project.value?.source_language,
      file.target_language || project.value?.target_language,
    )
    if (!pair) {
      invalidCount += 1
      continue
    }
    const key = `${pair.source}__${pair.target}`
    const current = pairMap.get(key)
    if (current) {
      current.file_count += 1
    } else {
      pairMap.set(key, {
        source_language: pair.source,
        target_language: pair.target,
        file_count: 1,
      })
    }
  }
  const pairs = Array.from(pairMap.values())
  if (invalidCount > 0) {
    pairs.push({ source_language: null, target_language: null, file_count: invalidCount })
  }
  return pairs
})
const selectedMergeViewHasMixedLanguagePairs = computed(() => selectedMergeViewLanguagePairs.value.length > 1)
const canOpenMergeViewDialog = computed(() => (
  Boolean(project.value)
  && selectedMergeViewFiles.value.length >= 2
  && !savingMergeView.value
))
const mergeOpenButtonTitle = computed(() => {
  if (selectedProjectFiles.value.length === 0) {
    return t('projectDetail.mergeViews.selectFileFirst')
  }
  if (selectedMergeViewFiles.value.length < 2) {
    return t('projectDetail.mergeViews.selectAtLeastTwo')
  }
  if (selectedMergeViewInvalidFiles.value.length > 0) {
    return t('projectDetail.mergeViews.someFilesIgnored', { count: selectedMergeViewInvalidFiles.value.length })
  }
  return ''
})
const canDeleteSelectedProjectFiles = computed(() => (
  canManageProject.value
  && selectedProjectFiles.value.length > 0
  && !deleting.value
))
const statisticsSelectedFiles = computed(() => (
  tableRows.value.filter((row) => statisticsSelectedFileIds.value.has(row.id))
))
const activeStatisticsReport = computed<DocumentStatisticsReport | null>(() => (
  statisticsReports.value.find((report) => report.id === activeStatisticsReportId.value) ?? null
))
const activeStatisticsItemsByFileId = computed(() => {
  const items = new Map<string, DocumentStatisticsReportItem>()
  for (const item of activeStatisticsReport.value?.items ?? []) {
    if (item.file_record_id) {
      items.set(item.file_record_id, item)
    }
  }
  return items
})
const statisticsResultRows = computed<DocumentStatisticsReportItem[]>(() => (
  activeStatisticsReport.value?.items ?? []
))
const canGenerateStatistics = computed(() => (
  canManageProject.value
  && statisticsSelectedFileIds.value.size > 0
  && !statisticsLoading.value
))
const statisticsAvailableCount = computed(() => (
  activeStatisticsReport.value?.available_files
  ?? statisticsResultRows.value.filter((row) => hasAnyDocumentStatistic(row.statistics)).length
))
const statisticsTotals = computed<DocumentStatisticsTotals>(() => {
  if (activeStatisticsReport.value) {
    return normalizeStatisticsTotals(activeStatisticsReport.value.totals)
  }
  const totals = createEmptyStatisticsTotals()
  const matchAnalyses: DocumentMatchAnalysis[] = []
  for (const row of statisticsResultRows.value) {
    const matchAnalysis = normalizeDocumentMatchAnalysis(row.statistics.match_analysis)
    if (matchAnalysis) {
      matchAnalyses.push(matchAnalysis)
    }
    for (const key of DOCUMENT_STATISTIC_NUMBER_KEYS) {
      const value = getStatisticNumber(row.statistics, key)
      if (value == null) {
        continue
      }
      totals[key] = (totals[key] ?? 0) + value
    }
  }
  totals.match_analysis = mergeDocumentMatchAnalyses(matchAnalyses)
  return totals
})
const statisticsMatchAnalysis = computed(() => (
  reconcileDocumentMatchAnalysisForDisplay(statisticsTotals.value.match_analysis, statisticsTotals.value.words)
))
const statisticsMatchAnalysisRows = computed<DocumentMatchAnalysisDisplayRow[]>(() => (
  buildDocumentMatchAnalysisRows(statisticsMatchAnalysis.value)
))
const statisticsFileMatchAnalysisBlocks = computed<DocumentFileMatchAnalysisBlock[]>(() => (
  statisticsResultRows.value
    .map((row) => {
      const analysis = reconcileDocumentMatchAnalysisForDisplay(
        row.statistics.match_analysis,
        getStatisticNumber(row.statistics, 'words'),
      )
      if (!analysis) {
        return null
      }
      return {
        id: row.file_record_id || row.id,
        file_name: row.file_name,
        analysis,
        rows: buildDocumentMatchAnalysisRows(analysis),
      }
    })
    .filter((row): row is DocumentFileMatchAnalysisBlock => Boolean(row && row.rows.length > 0))
))
const selectedTermExtractionFile = computed<ProjectFileItem | null>(() => (
  selectedProjectFiles.value.length === 1 ? selectedProjectFiles.value[0] : null
))
const selectedTermExtractionSourceLanguage = computed(() => (
  selectedTermExtractionFile.value?.source_language || project.value?.source_language || ''
))
const selectedTermExtractionTargetLanguage = computed(() => (
  selectedTermExtractionFile.value?.target_language || project.value?.target_language || ''
))
const canOpenPreTranslate = computed(() => (
  selectedFileIds.value.size > 0
  && !hasSelectedLockedFile.value
  && !hasSelectedNonWritableFile.value
))
const preTranslateButtonTitle = computed(() => (
  selectedFileIds.value.size === 0
    ? t('projectDetail.preTranslate.selectFileFirst')
    : hasSelectedLockedFile.value
      ? t('projectDetail.preTranslate.fileLocked')
      : hasSelectedNonWritableFile.value
        ? '无权处理所选任务'
        : ''
))
const canAssignSelectedFile = computed(() => (
  canAssignProject.value
  && Boolean(project.value)
))
const filteredAssignableUsers = computed<User[]>(() => {
  let users = [...assignableUsers.value]

  if (assignmentUserTypeFilter.value !== 'all') {
    users = users.filter((user) => user.translator_type === assignmentUserTypeFilter.value)
  }

  if (assignmentUserStateFilter.value !== 'all') {
    users = users.filter((user) => (
      assignmentUserStateFilter.value === 'selected'
        ? isUserInAssignmentDraft(user.id)
        : !isUserInAssignmentDraft(user.id)
    ))
  }

  const keyword = normalizeAssignmentKeyword(assignmentUserSearch.value)
  if (keyword) {
    users = users.filter((user) => getAssignmentUserSearchText(user).includes(keyword))
  }

  return users
})
const termExtractionButtonTitle = computed(() => {
  if (selectedFileIds.value.size === 0) {
    return t('projectDetail.termExtraction.selectFileFirst')
  }
  if (selectedFileIds.value.size > 1) {
    return t('projectDetail.termExtraction.selectOneFile')
  }
  if (!selectedTermExtractionFile.value || Number(selectedTermExtractionFile.value.total_segments || 0) <= 0) {
    return t('projectDetail.termExtraction.noSegments')
  }
  if (!selectedTermExtractionFile.value.can_write) {
    return '无权处理所选任务'
  }
  if (!selectedTermExtractionSourceLanguage.value || !selectedTermExtractionTargetLanguage.value) {
    return t('projectDetail.termExtraction.languageMissing')
  }
  return ''
})
const duplicateTemplateButtonTitle = computed(() => {
  if (selectedFileIds.value.size === 0) {
    return t('projectDetail.files.actions.duplicateTemplateSelectFirst')
  }
  if (selectedFileIds.value.size > 1) {
    return t('projectDetail.files.actions.duplicateTemplateSelectOne')
  }
  return ''
})
const englishVariantCopyDirection = computed<'to-british' | 'to-american' | null>(() => {
  if (selectedProjectFiles.value.length !== 1) {
    return null
  }
  const targetLanguage = selectedProjectFiles.value[0]?.target_language
  if (targetLanguage === 'en-US') {
    return 'to-british'
  }
  if (targetLanguage === 'en-GB') {
    return 'to-american'
  }
  return null
})
const englishVariantCopyLabel = computed(() => {
  if (englishVariantCopyDirection.value === 'to-british') {
    return t('projectDetail.files.actions.britishCopy')
  }
  if (englishVariantCopyDirection.value === 'to-american') {
    return t('projectDetail.files.actions.americanCopy')
  }
  return t('projectDetail.files.actions.englishVariantCopy')
})
const englishVariantCopyShortLabel = computed(() => {
  if (englishVariantCopyDirection.value === 'to-british') {
    return t('projectDetail.files.actions.britishCopyShort')
  }
  if (englishVariantCopyDirection.value === 'to-american') {
    return t('projectDetail.files.actions.americanCopyShort')
  }
  return t('projectDetail.files.actions.englishVariantCopyShort')
})
const englishVariantCopyDisabledReason = computed(() => {
  if (creatingEnglishVariantCopy.value) {
    return t('projectDetail.files.actions.englishVariantCopyCreating')
  }
  if (selectedFileIds.value.size === 0) {
    return t('projectDetail.files.actions.englishVariantCopySelectFirst')
  }
  if (selectedFileIds.value.size > 1) {
    return t('projectDetail.files.actions.englishVariantCopySelectOne')
  }
  const sourceFile = selectedProjectFiles.value[0]
  if (!sourceFile) {
    return t('projectDetail.files.actions.englishVariantCopySelectFirst')
  }
  if (sourceFile.is_edit_locked || sourceFile.active_operation) {
    return sourceFile.active_operation_message || t('projectDetail.files.actions.englishVariantCopyLocked')
  }
  if (!['zh-CN', 'zh-TW', 'zh-HK', 'zh-MO'].includes(sourceFile.source_language || '')
      || !['en-US', 'en-GB'].includes(sourceFile.target_language || '')) {
    return t('projectDetail.files.actions.englishVariantCopyUnsupportedPair')
  }
  if (Number(sourceFile.pretranslated_segments || 0) <= 0) {
    return t('projectDetail.files.actions.englishVariantCopyNoTranslation')
  }
  return ''
})
const deleteSelectedFilesButtonTitle = computed(() => (
  selectedProjectFiles.value.length === 0
    ? t('projectDetail.files.actions.deleteSelectFirst')
    : ''
))
const canDuplicateTemplate = computed(() => canManageProject.value && selectedFileIds.value.size === 1 && !duplicating.value)
const canCreateEnglishVariantCopy = computed(() => (
  canManageProject.value
  && !authStore.isExternalTranslator
  && !englishVariantCopyDisabledReason.value
))
const canOpenTermExtraction = computed(() => (
  Boolean(selectedTermExtractionFile.value)
  && Boolean(selectedTermExtractionFile.value?.can_write)
  && Number(selectedTermExtractionFile.value?.total_segments || 0) > 0
  && Boolean(selectedTermExtractionSourceLanguage.value)
  && Boolean(selectedTermExtractionTargetLanguage.value)
))
const canOpenProjectExportMenu = computed(() => (
  selectedProjectFiles.value.length > 0
  && !exportingFileId.value
  && !loadingProjectExportOptions.value
))
const canExportSelectedProjectFilesAsZip = computed(() => (
  selectedProjectFiles.value.length > 1
  && projectExportOptions.value.some((option) => option.id === 'original')
))
const projectExportButtonTitle = computed(() => {
  if (selectedProjectFiles.value.length === 0) {
    return t('projectDetail.files.actions.exportSelectFirst')
  }
  if (loadingProjectExportOptions.value) {
    return t('projectDetail.files.actions.exportLoading')
  }
  if (exportingFileId.value) {
    return exportFileMessage.value
  }
  return ''
})

const columns = computed<DataTableColumn[]>(() => ([
  { key: 'filename', label: t('projectDetail.files.columns.details'), width: '300px', sortable: true },
  { key: 'progress', label: t('projectDetail.files.columns.progress'), width: '150px', sortable: true },
  { key: 'pretranslation_progress', label: t('projectDetail.files.columns.pretranslationProgress'), width: '150px', sortable: true },
  { key: 'taskManage', label: t('projectDetail.files.columns.task'), width: '140px', sortable: true },
  { key: 'status', label: t('projectDetail.files.columns.status'), width: '100px', sortable: true },
  { key: 'open_issue_count', label: t('issueMarker.list.title'), width: '90px', sortable: true },
]))

const statisticsFileColumns = computed<DataTableColumn[]>(() => ([
  { key: 'filename', label: t('projectDetail.stats.columns.file'), width: '320px' },
  { key: 'source_language', label: t('projectList.form.sourceLanguage'), width: '130px' },
  { key: 'target_language', label: t('projectDetail.files.columns.targetLang'), width: '130px' },
  { key: 'status', label: t('projectDetail.files.columns.status'), width: '120px' },
  { key: 'statistics_status', label: t('projectDetail.stats.columns.statisticsStatus'), width: '160px' },
]))

const canManageProject = computed(() => Boolean(project.value?.can_manage))
const canAssignProject = computed(() => Boolean(project.value) && (canManageProject.value || authStore.isInternalTranslator))
const canCreateProjects = computed(() => authStore.isBusinessManager)
const canUploadProjectFiles = computed(() => Boolean(project.value) && canCreateProjects.value)
const canOpenUploadModal = computed(() => canUploadProjectFiles.value)
const canOpenProjectIssueDialog = computed(() => Boolean(project.value) && !authStore.isExternalTranslator)

const uploadButtonTitle = computed(() => (
  canUploadProjectFiles.value ? '' : '只有管理员或内部译者可以上传项目文件'
))
const projectSyncSegmentCount = computed(() => Number(project.value?.project_sync_segment_count || 0))
const projectSyncDisabledCount = computed(() => {
  const total = projectSyncSegmentCount.value
  const disabled = Number(project.value?.project_sync_disabled_count || 0)
  return Math.max(0, Math.min(disabled, total))
})
const projectSyncAllEnabled = computed(() => (
  projectSyncSegmentCount.value > 0 && projectSyncDisabledCount.value === 0
))
const projectSyncMixed = computed(() => (
  projectSyncDisabledCount.value > 0 && projectSyncDisabledCount.value < projectSyncSegmentCount.value
))
const projectSyncStatusLabel = computed(() => {
  if (projectSyncSegmentCount.value === 0) {
    return '暂无句段'
  }
  if (projectSyncDisabledCount.value === 0) {
    return ''
  }
  if (projectSyncDisabledCount.value >= projectSyncSegmentCount.value) {
    return `已全部关闭（${projectSyncDisabledCount.value}/${projectSyncSegmentCount.value}）`
  }
  return `已关闭 ${projectSyncDisabledCount.value}/${projectSyncSegmentCount.value}`
})
const projectSyncToggleDisabled = computed(() => (
  projectSyncToggleLoading.value
  || !project.value
  || !canManageProject.value
  || projectSyncSegmentCount.value === 0
))
const projectSyncToggleTitle = computed(() => {
  if (projectSyncSegmentCount.value === 0) {
    return '项目内暂无可同步句段'
  }
  if (projectSyncMixed.value) {
    return '部分句段已关闭同步，点击后恢复全项目同步'
  }
  return projectSyncAllEnabled.value ? '点击关闭全项目同步' : '点击开启全项目同步'
})

const accessOptions = computed(() => ([
  { value: 'team' as const, label: t('projectList.form.team') },
  { value: 'private' as const, label: t('projectList.form.private') },
  { value: 'public' as const, label: t('projectList.form.public') },
]))

const projectFileLanguagePairs = computed(() => {
  const pairs = new Map<string, { source: string; target: string }>()
  for (const file of tableRows.value) {
    if (!file.source_language || !file.target_language) {
      continue
    }
    pairs.set(`${file.source_language}->${file.target_language}`, {
      source: file.source_language,
      target: file.target_language,
    })
  }
  return Array.from(pairs.values())
})

const effectiveProjectSourceLanguage = computed(() => {
  if (project.value?.source_language) {
    return project.value.source_language
  }
  const sources = new Set(projectFileLanguagePairs.value.map((pair) => pair.source))
  return sources.size === 1 ? Array.from(sources)[0] : null
})

const effectiveProjectTargetLanguage = computed(() => {
  if (project.value?.target_language) {
    return project.value.target_language
  }
  const targets = new Set(projectFileLanguagePairs.value.map((pair) => pair.target))
  return targets.size === 1 ? Array.from(targets)[0] : null
})

const projectBoundLanguagePair = computed(() => (
  canonicalizeLanguagePair(project.value?.source_language, project.value?.target_language)
))
const projectLanguagePairLabel = computed(() => (
  projectBoundLanguagePair.value
    ? formatLanguagePair(projectBoundLanguagePair.value.source, projectBoundLanguagePair.value.target)
    : projectFileLanguagePairs.value.length > 1
      ? t('projectDetail.settings.multipleLanguagePairs', { count: projectFileLanguagePairs.value.length })
      : formatLanguagePair(effectiveProjectSourceLanguage.value, effectiveProjectTargetLanguage.value)
))
const isProjectLanguagePairBound = computed(() => Boolean(projectBoundLanguagePair.value))
const uploadLanguageDescription = computed(() => (
  isProjectLanguagePairBound.value
    ? t('projectDetail.uploadLanguage.boundHint')
    : t('projectDetail.uploadLanguage.unboundHint')
))
const uploadLanguageBoundMessage = computed(() => (
  t('projectDetail.uploadLanguage.boundMessage', { pair: projectLanguagePairLabel.value })
))

const uploadSupportedSummary = computed(() => {
  if (uploadCapabilities.value.length === 0) {
    return uploadFileAccept.value
  }
  return uploadCapabilities.value
    .flatMap((capability) => capability.extensions)
    .map((extension) => extension.replace('.', '').toUpperCase())
    .join('、')
})
const canDetectSourceLanguage = computed(() => (
  selectedFiles.value.length > 0
  && !uploading.value
  && !detectingLanguage.value
  && !isProjectLanguagePairBound.value
))
const uploadFileValidationError = computed(() => validateSelectedUploadFiles(selectedFiles.value))
const effectiveUploadTargetLanguages = computed(() => (
  projectBoundLanguagePair.value?.target
    ? [projectBoundLanguagePair.value.target]
    : uploadTargetLanguages.value
))
const generatedUploadTaskCount = computed(() => (
  selectedFiles.value.length * effectiveUploadTargetLanguages.value.length
))
const uploadGenerationValidationError = computed(() => {
  if (generatedUploadTaskCount.value <= uploadLimits.value.max_expanded_files) {
    return ''
  }
  return t('projectDetail.errors.tooManyGeneratedTasks', {
    count: generatedUploadTaskCount.value,
    max: uploadLimits.value.max_expanded_files,
  })
})
const filteredUploadTargetLanguageOptions = computed(() => {
  const query = normalizeSearchKeyword(uploadTargetLanguageSearch.value)
  return languageOptions.filter((language) => {
    if (language.code === uploadSourceLanguage.value) {
      return false
    }
    if (!query) {
      return true
    }
    return normalizeSearchKeyword(`${language.label} ${language.code}`).includes(query)
  })
})
const canSubmitSourceUpload = computed(() => (
  selectedFiles.value.length > 0
  && !uploading.value
  && !uploadFileValidationError.value
  && !uploadGenerationValidationError.value
  && Boolean(uploadSourceLanguage.value)
  && effectiveUploadTargetLanguages.value.length > 0
))
const cameFromTasks = computed(() => route.query.from === 'tasks')
const backRoute = computed(() => (
  cameFromTasks.value ? { name: 'tasks' } : { name: 'projects' }
))
const backLabel = computed(() => (
  cameFromTasks.value ? t('workbench.backToTasks') : t('projectDetail.back')
))

usePageHeader(() => ({
  title: project.value?.filename || t('projectDetail.titleFallback'),
  description: t('projectDetail.description'),
  breadcrumbs: cameFromTasks.value
    ? [
        { label: t('shell.sections.tasks'), to: { name: 'tasks' } },
        { label: project.value?.filename || t('projectDetail.titleFallback') },
      ]
    : [
        { label: t('shell.sections.workspace'), to: { name: 'projects' } },
        { label: project.value?.filename || t('projectDetail.titleFallback') },
      ],
}))

function getPlaceholder() {
  return t('projectDetail.common.placeholder')
}

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

function formatDateParts(value: string | null) {
  if (!value) {
    return { date: getPlaceholder(), time: '' }
  }

  const date = new Date(value)
  return {
    date: date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }),
    time: date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }),
  }
}

function formatDateText(value: string | null) {
  const parts = formatDateParts(value)
  return parts.time ? `${parts.date} ${parts.time}` : parts.date
}

function getAssigneeDisplayName(user: User | null | undefined) {
  return user?.nickname || user?.username || ''
}

function ensureActiveAssignmentWorkflowStep() {
  if (activeAssignmentWorkflowStep.value) {
    activeAssignmentWorkflowStepId.value = activeAssignmentWorkflowStep.value.id
    return
  }
  activeAssignmentWorkflowStepId.value = projectWorkflowSteps.value[0]?.id ?? ''
}

function getAssigneeLabel(row: ProjectRow) {
  const assignees = Array.isArray(row.assignees) ? row.assignees : []
  if (assignees.length > 0) {
    return assignees.map((user) => getAssigneeDisplayName(user)).filter(Boolean).join('、')
  }
  return getAssigneeDisplayName(row.assignee) || '未分配'
}

function getTranslatorTypeLabel(user: User) {
  return user.translator_type === 'internal' ? '内部译者' : '外部译者'
}

function getAssigneeSecondaryLabel(user: User) {
  const typeLabel = getTranslatorTypeLabel(user)
  if (user.nickname && user.nickname !== user.username) {
    return `${user.username} · ${typeLabel}`
  }
  return typeLabel
}

function getAssigneeTooltip(user: User) {
  return `${getAssigneeDisplayName(user)} · ${getAssigneeSecondaryLabel(user)}`
}

function normalizeAssignmentKeyword(value: string | null | undefined) {
  return String(value || '').trim().toLowerCase()
}

function normalizeFileFilterKeyword(value: string | null | undefined) {
  return normalizeSearchKeyword(value)
}

function getFileLanguagePairKey(row: ProjectRow) {
  return `${String(row.source_language || '')}->${String(row.target_language || '')}`
}

function getFileLanguagePairLabel(row: ProjectRow) {
  if (!row.source_language || !row.target_language) {
    return '未设置语言对'
  }
  return formatLanguagePair(row.source_language, row.target_language)
}

function getDerivedFileKind(row: ProjectRow): DerivedFileKind | undefined {
  const filename = String(row.filename || '').trim()
  const basename = filename.replace(/\.[^.]+$/, '')
  if (/\s-\s副本(?:\s+\d+)?$/u.test(basename)) {
    return 'template-copy'
  }
  if (/\s-\s英式英语(?:\s+\d+)?$/u.test(basename)) {
    return 'british-copy'
  }
  if (/\s-\s美式英语(?:\s+\d+)?$/u.test(basename)) {
    return 'american-copy'
  }
  return undefined
}

function getProjectFileRowClass(row: ProjectRow) {
  const kind = getDerivedFileKind(row)
  return kind ? `pd-file-row--${kind}` : undefined
}

function getDerivedFileKindLabel(row: ProjectRow) {
  const kind = getDerivedFileKind(row)
  if (kind === 'template-copy') {
    return t('projectDetail.files.kinds.templateCopy')
  }
  if (kind === 'british-copy') {
    return t('projectDetail.files.kinds.britishCopy')
  }
  if (kind === 'american-copy') {
    return t('projectDetail.files.kinds.americanCopy')
  }
  return ''
}

function getFileAssignees(row: ProjectRow): User[] {
  const candidates = [
    ...(Array.isArray(row.assignees) ? row.assignees : []),
    row.assignee,
  ].filter((user): user is User => Boolean(user && typeof user === 'object'))
  const seen = new Set<string>()
  const users: User[] = []
  for (const user of candidates) {
    if (!user.id || seen.has(user.id)) {
      continue
    }
    seen.add(user.id)
    users.push(user)
  }
  return users
}

function getFileAssigneeIds(row: ProjectRow) {
  const ids = new Set(getFileAssignees(row).map((user) => user.id).filter(Boolean))
  if (typeof row.assignee_id === 'string' && row.assignee_id) {
    ids.add(row.assignee_id)
  }
  return Array.from(ids)
}

function getFileSearchText(row: ProjectRow) {
  return [
    row.filename,
    getFileLanguagePairLabel(row),
    row.source_language,
    row.target_language,
    getLanguageLabel(row.source_language),
    getLanguageLabel(row.target_language),
    getAssigneeLabel(row),
    ...getFileAssignees(row).flatMap((user) => [
      user.nickname,
      user.username,
      user.translator_type,
    ]),
    row.assignee_id,
    row.creator,
  ].map(normalizeFileFilterKeyword).join(' ')
}

function isProjectFileSortKey(key: string): key is ProjectFileSortKey {
  return PROJECT_FILE_SORT_KEYS.has(key)
}

function getProjectFileNumberSortValue(value: unknown) {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number : 0
}

function getProjectFileSortValue(row: ProjectRow, key: ProjectFileSortKey) {
  switch (key) {
    case 'filename':
      return row.filename || ''
    case 'progress':
      return getFileDisplayProgress(row)
    case 'pretranslation_progress':
      return getFilePretranslationProgress(row)
    case 'taskManage':
      return getAssigneeLabel(row)
    case 'status':
      return row.status ? formatStatus(String(row.status)) : ''
    case 'open_issue_count':
      return getProjectFileNumberSortValue(row.open_issue_count)
    default:
      return ''
  }
}

function compareProjectFileRows(left: ProjectFileItem, right: ProjectFileItem) {
  const key = fileSortKey.value
  if (!key) {
    return 0
  }

  const leftValue = getProjectFileSortValue(left, key)
  const rightValue = getProjectFileSortValue(right, key)
  const direction = fileSortOrder.value === 'asc' ? 1 : -1
  if (typeof leftValue === 'number' && typeof rightValue === 'number') {
    return (leftValue - rightValue) * direction
  }
  return String(leftValue ?? '').localeCompare(String(rightValue ?? ''), 'zh-CN', {
    numeric: true,
    sensitivity: 'base',
  }) * direction
}

function handleFileSort(key: string, order: 'asc' | 'desc') {
  if (!isProjectFileSortKey(key)) {
    return
  }
  fileSortKey.value = key
  fileSortOrder.value = order
  currentPage.value = 1
  closeFileSelectionMenu()
  closeActionMenu()
}

function resetFileFilters() {
  fileSearchQuery.value = ''
  fileStatusFilter.value = 'all'
  fileLanguagePairFilter.value = 'all'
  fileAssigneeFilter.value = 'all'
}

function getAssignmentUserSearchText(user: User) {
  return [
    getAssigneeDisplayName(user),
    user.username,
    getTranslatorTypeLabel(user),
    user.translator_type,
  ].map(normalizeAssignmentKeyword).join(' ')
}

function formatDateTimeLocalValue(value: string | null) {
  if (!value) {
    return ''
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return ''
  }
  const pad = (n: number) => String(n).padStart(2, '0')
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `${pad(date.getHours())}:${pad(date.getMinutes())}`,
  ].join('T')
}

function syncSettingsForm(data: ProjectDetail) {
  settingsForm.name = data.name || data.filename || ''
  settingsForm.deadline = formatDateTimeLocalValue(data.deadline)
  settingsForm.access_level = data.access_level || 'team'
  settingsError.value = ''
}

function formatStatus(value: string) {
  return getFileStatusMeta(value).label
}

function getStatusClass(status: string) {
  return `project-status--${getFileStatusMeta(status).tone}`
}

function formatBytes(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return getPlaceholder()
  }

  const units = ['B', 'KB', 'MB', 'GB']
  let size = value
  let unitIndex = 0

  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }

  const decimals = size >= 10 || unitIndex === 0 ? 0 : 1
  return `${size.toFixed(decimals)} ${units[unitIndex]}`
}

function createEmptyStatisticsTotals(): DocumentStatisticsTotals {
  return {
    pages: null,
    words: null,
    non_asian_words: null,
    asian_characters: null,
    characters: null,
    characters_with_spaces: null,
    paragraphs: null,
    lines: null,
    internal_repeated_words: null,
    internal_repeated_characters: null,
    cross_file_repeated_words: null,
    cross_file_repeated_characters: null,
    image_count: null,
    unique_image_count: null,
    inline_image_count: null,
    floating_image_count: null,
    linked_image_count: null,
    chart_count: null,
    smartart_count: null,
    match_analysis: null,
  }
}

function normalizeStatisticsTotals(totals: DocumentStatisticsTotals | null | undefined): DocumentStatisticsTotals {
  const normalized = createEmptyStatisticsTotals()
  if (!totals) {
    return normalized
  }
  for (const key of DOCUMENT_STATISTIC_NUMBER_KEYS) {
    const value = totals[key]
    normalized[key] = typeof value === 'number' && Number.isFinite(value) ? value : null
  }
  normalized.match_analysis = normalizeDocumentMatchAnalysis(totals.match_analysis)
  return normalized
}

function normalizeDocumentMatchAnalysis(value: DocumentMatchAnalysis | null | undefined): DocumentMatchAnalysis | null {
  if (!value || !Array.isArray(value.rows)) {
    return null
  }
  const rows = DOCUMENT_MATCH_ANALYSIS_ROW_KEYS.map((key) => {
    const source = value.rows.find((row) => row.key === key)
    const segmentCount = normalizeStatisticNumber(source?.segment_count) ?? 0
    const wordCount = normalizeStatisticNumber(source?.word_count) ?? 0
    return {
      key,
      label: source?.label || getDocumentMatchAnalysisLabel(key),
      segment_count: segmentCount,
      word_count: wordCount,
      percent: normalizeStatisticNumber(source?.percent) ?? 0,
    }
  })
  const totalSegments = normalizeStatisticNumber(value.total_segments)
    ?? rows.reduce((sum, row) => sum + row.segment_count, 0)
  const totalWords = normalizeStatisticNumber(value.total_words)
    ?? rows.reduce((sum, row) => sum + row.word_count, 0)
  return {
    threshold: normalizeStatisticNumber(value.threshold) ?? 0.5,
    collection_ids: Array.isArray(value.collection_ids) ? value.collection_ids.filter(Boolean) : [],
    total_segments: totalSegments,
    total_words: totalWords,
    rows,
  }
}

function reconcileDocumentMatchAnalysisForDisplay(
  value: DocumentMatchAnalysis | null | undefined,
  authoritativeWords: number | null | undefined,
): DocumentMatchAnalysis | null {
  const normalized = normalizeDocumentMatchAnalysis(value)
  const targetTotal = normalizeStatisticNumber(authoritativeWords)
  if (!normalized || targetTotal == null || targetTotal === normalized.total_words) {
    return normalized
  }
  const rows = normalized.rows.map((row) => ({ ...row }))
  const currentTotal = rows.reduce((sum, row) => sum + Math.max(row.word_count, 0), 0)
  if (targetTotal <= 0) {
    rows.forEach((row) => {
      row.word_count = 0
    })
  } else if (currentTotal <= 0 || targetTotal > currentTotal) {
    const newRow = rows.find((row) => row.key === 'new')
    if (newRow) {
      newRow.word_count = Math.max(0, newRow.word_count + targetTotal - currentTotal)
    }
  } else {
    scaleDocumentMatchRowsToWordTotal(rows, targetTotal, currentTotal)
  }
  return rebuildDocumentMatchAnalysisRows({
    ...normalized,
    total_words: targetTotal,
    rows,
  })
}

function scaleDocumentMatchRowsToWordTotal(
  rows: DocumentMatchAnalysis['rows'],
  targetTotal: number,
  currentTotal: number,
) {
  const scaled = rows.map((row, index) => {
    const rawValue = Math.max(row.word_count, 0) * targetTotal / currentTotal
    const floorValue = Math.floor(rawValue)
    return {
      index,
      floorValue,
      remainder: rawValue - floorValue,
    }
  })
  const floorTotal = scaled.reduce((sum, row) => sum + row.floorValue, 0)
  let remaining = Math.max(targetTotal - floorTotal, 0)
  const ranked = [...scaled].sort((left, right) => {
    if (right.remainder !== left.remainder) {
      return right.remainder - left.remainder
    }
    return left.index - right.index
  })
  const extraByIndex = new Map<number, number>()
  for (const row of ranked) {
    if (remaining <= 0) break
    extraByIndex.set(row.index, (extraByIndex.get(row.index) ?? 0) + 1)
    remaining -= 1
  }
  for (const row of scaled) {
    rows[row.index].word_count = row.floorValue + (extraByIndex.get(row.index) ?? 0)
  }
}

function mergeDocumentMatchAnalyses(analyses: DocumentMatchAnalysis[]): DocumentMatchAnalysis | null {
  if (analyses.length === 0) {
    return null
  }
  const counts = new Map<DocumentMatchAnalysisRowKey, { segment_count: number; word_count: number }>()
  for (const key of DOCUMENT_MATCH_ANALYSIS_ROW_KEYS) {
    counts.set(key, { segment_count: 0, word_count: 0 })
  }
  const collectionIds = new Set<string>()
  let threshold = 0.5
  for (const analysis of analyses) {
    threshold = analysis.threshold
    analysis.collection_ids.forEach((id) => collectionIds.add(id))
    for (const row of analysis.rows) {
      if (!DOCUMENT_MATCH_ANALYSIS_ROW_KEYS.includes(row.key as DocumentMatchAnalysisRowKey)) {
        continue
      }
      const key = row.key as DocumentMatchAnalysisRowKey
      const target = counts.get(key)
      if (!target) continue
      target.segment_count += row.segment_count
      target.word_count += row.word_count
    }
  }
  const totalSegments = [...counts.values()].reduce((sum, row) => sum + row.segment_count, 0)
  const totalWords = [...counts.values()].reduce((sum, row) => sum + row.word_count, 0)
  return {
    threshold,
    collection_ids: [...collectionIds],
    total_segments: totalSegments,
    total_words: totalWords,
    rows: buildDocumentMatchRowsFromCounts(counts, totalWords),
  }
}

function rebuildDocumentMatchAnalysisRows(analysis: DocumentMatchAnalysis): DocumentMatchAnalysis {
  const counts = new Map<DocumentMatchAnalysisRowKey, { segment_count: number; word_count: number }>()
  for (const key of DOCUMENT_MATCH_ANALYSIS_ROW_KEYS) {
    const source = analysis.rows.find((row) => row.key === key)
    counts.set(key, {
      segment_count: normalizeStatisticNumber(source?.segment_count) ?? 0,
      word_count: normalizeStatisticNumber(source?.word_count) ?? 0,
    })
  }
  return {
    ...analysis,
    rows: buildDocumentMatchRowsFromCounts(counts, analysis.total_words),
  }
}

function buildDocumentMatchRowsFromCounts(
  counts: Map<DocumentMatchAnalysisRowKey, { segment_count: number; word_count: number }>,
  totalWords: number,
) {
  return DOCUMENT_MATCH_ANALYSIS_ROW_KEYS.map((key) => {
    const row = counts.get(key) ?? { segment_count: 0, word_count: 0 }
    return {
      key,
      label: getDocumentMatchAnalysisLabel(key),
      segment_count: row.segment_count,
      word_count: row.word_count,
      percent: totalWords > 0 ? Number(((row.word_count / totalWords) * 100).toFixed(2)) : 0,
    }
  })
}

function buildDocumentMatchAnalysisRows(
  analysis: DocumentMatchAnalysis | null | undefined,
): DocumentMatchAnalysisDisplayRow[] {
  const normalized = normalizeDocumentMatchAnalysis(analysis)
  if (!normalized) {
    return []
  }
  const rows: DocumentMatchAnalysisDisplayRow[] = normalized.rows.map((row) => ({
    key: row.key as DocumentMatchAnalysisRowKey,
    label: getDocumentMatchAnalysisLabel(row.key as DocumentMatchAnalysisRowKey),
    percent: row.percent,
    segment_count: row.segment_count,
    word_count: row.word_count,
  }))
  rows.push({
    key: 'total',
    label: t('projectDetail.stats.matchAnalysis.total'),
    percent: normalized.total_words > 0 ? 100 : 0,
    segment_count: normalized.total_segments,
    word_count: normalized.total_words,
    is_total: true,
  })
  return rows
}

function normalizeStatisticNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function getStatisticNumber(
  statistics: DocumentStatistics | null | undefined,
  key: DocumentStatisticNumberKey,
) {
  const value = statistics?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function hasAnyDocumentStatistic(statistics: DocumentStatistics | null | undefined) {
  return DOCUMENT_STATISTIC_NUMBER_KEYS.some((key) => getStatisticNumber(statistics, key) != null)
}

function formatStatisticNumber(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return getPlaceholder()
  }
  return new Intl.NumberFormat('zh-CN').format(value)
}

function formatStatisticPercent(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return getPlaceholder()
  }
  const formatted = Number.isInteger(value) ? value.toFixed(0) : value.toFixed(2)
  return `${formatted}%`
}

function getDocumentMatchAnalysisLabel(key: DocumentMatchAnalysisRowKey) {
  return t(`projectDetail.stats.matchAnalysis.rows.${key}`)
}

function getStatisticsSourceLabel(statistics: DocumentStatistics | null | undefined) {
  if (!statistics?.source) {
    return t('projectDetail.stats.sources.notReady')
  }
  if (statistics.source === 'unavailable') {
    return t('projectDetail.stats.sources.unavailable')
  }
  return hasAnyDocumentStatistic(statistics)
    ? t('projectDetail.stats.sources.completed')
    : t('projectDetail.stats.sources.unavailable')
}

function getStatisticsLicenseLabel(statistics: DocumentStatistics | null | undefined) {
  const status = statistics?.license_status
  if (!status) {
    return getPlaceholder()
  }
  const labelKey = `projectDetail.stats.licenseStatus.${status}`
  const label = t(labelKey)
  return label === labelKey ? status : label
}

function getStatisticsStatusClass(statistics: DocumentStatistics | null | undefined) {
  if (!statistics?.source) {
    return 'project-status--default'
  }
  if (statistics.source === 'aspose' || statistics.source === 'libreoffice') {
    return 'project-status--success'
  }
  if (
    statistics.source === 'openxml_computed'
    || statistics.source === 'openxml_word_like'
    || statistics.source === 'pptx_word_like'
  ) {
    return 'project-status--info'
  }
  if (statistics.source === 'docprops_cached') {
    return 'project-status--warning'
  }
  return 'project-status--default'
}

function getStatisticsForFile(row: ProjectRow) {
  const rowId = String(row.id || '')
  return activeStatisticsItemsByFileId.value.get(rowId)?.statistics ?? (row as ProjectFileItem).document_statistics
}

function sortDocumentStatisticsReports(reports: DocumentStatisticsReport[]) {
  return [...reports].sort((left, right) => (
    new Date(right.created_at || 0).getTime() - new Date(left.created_at || 0).getTime()
  ))
}

function mergeDocumentStatisticsReports(reports: DocumentStatisticsReport[]) {
  const merged = new Map<string, DocumentStatisticsReport>()
  for (const report of statisticsReports.value) {
    merged.set(report.id, report)
  }
  for (const report of reports) {
    merged.set(report.id, report)
  }
  statisticsReports.value = sortDocumentStatisticsReports(Array.from(merged.values()))
}

function selectDocumentStatisticsReport(reportId: string, syncSelection = true) {
  activeStatisticsReportId.value = reportId
  const report = statisticsReports.value.find((item) => item.id === reportId) ?? null
  statisticsResultFileIds.value = new Set(report?.file_ids ?? [])
  if (syncSelection && report) {
    statisticsSelectedFileIds.value = new Set(report.file_ids)
  }
}

function formatStatisticsReportOption(report: DocumentStatisticsReport) {
  const createdAt = formatDateText(report.created_at)
  const words = formatStatisticNumber(report.totals?.words)
  return t('projectDetail.stats.reportOption', {
    createdAt,
    files: report.total_files,
    words,
  })
}

function canEnterWorkbench(row: ProjectRow) {
  return Boolean(row.can_write) && Number(row.total_segments ?? 0) > 0 && !row.is_edit_locked
}

function canUseFileInMergeView(row: ProjectRow) {
  return canEnterWorkbench(row)
}

function canManageMergeView(view: MergeView) {
  return canManageProject.value || Boolean(view.can_manage)
}

function getMergeViewFileNames(view: MergeView) {
  const fileById = new Map(tableRows.value.map((file) => [file.id, file.filename]))
  return view.file_ids.map((fileId) => fileById.get(fileId) || fileId)
}

function getMergeViewMetaText(view: MergeView) {
  return [
    t('projectDetail.mergeViews.fileCount', { count: view.file_count }),
    view.available_file_count !== view.file_count
      ? t('projectDetail.mergeViews.availableFileCount', { count: view.available_file_count })
      : '',
    view.creator_name ? t('projectDetail.mergeViews.creator', { name: view.creator_name }) : '',
    formatDateText(view.updated_at || view.created_at),
  ].filter(Boolean).join(' · ')
}

function formatLanguagePairSummary(pair: LanguagePairSummary) {
  return `${formatLanguagePair(pair.source_language, pair.target_language)} · ${pair.file_count} 个文件`
}

function buildDefaultMergeViewName() {
  const names = selectedMergeViewFiles.value
    .slice(0, 2)
    .map((file) => String(file.filename || '').trim())
    .filter(Boolean)
  const suffix = selectedMergeViewFiles.value.length > 2
    ? ` +${selectedMergeViewFiles.value.length - 2}`
    : ''
  return names.length > 0
    ? `${names.join(' + ')}${suffix}`
    : t('projectDetail.mergeViews.defaultName')
}

function getFileDetailHint(row: ProjectRow) {
  if (row.is_edit_locked) {
    return row.active_operation_message || t('projectDetail.files.editLockedHint')
  }
  if (canEnterWorkbench(row)) {
    return t('projectDetail.files.openHint')
  }
  if (row.has_source_document) {
    return t('projectDetail.files.processingHint')
  }
  return t('projectDetail.common.uploadRequired')
}

function getFileMetaText(row: ProjectRow) {
  const languagePair = formatLanguagePair(row.source_language, row.target_language)
  const fileSize = formatBytes(row.file_size_bytes)
  const createdAt = formatDateParts(row.created_at).date
  return [
    getFileDetailHint(row),
    languagePair,
    fileSize,
    createdAt,
  ].filter((item) => item && item !== getPlaceholder()).join(' · ')
}

function clearLanguageDetectState() {
  languageDetectMessage.value = ''
  languageDetectTone.value = 'info'
}

function getUploadFileExtension(filename: string): string {
  const dotIndex = filename.lastIndexOf('.')
  return dotIndex >= 0 ? filename.slice(dotIndex).toLowerCase() : ''
}

function getMaxUploadSizeMbForFile(filename: string): number {
  const extension = getUploadFileExtension(filename)
  for (const capability of uploadCapabilities.value) {
    if (capability.extensions.includes(extension)) {
      return capability.max_size_mb
    }
  }
  return DEFAULT_UPLOAD_MAX_SIZE_MB
}

function validateSelectedUploadFiles(files: File[]): string {
  if (files.length === 0) {
    return ''
  }

  const limits = uploadLimits.value
  if (files.length > limits.max_files_per_batch) {
    return t('projectDetail.errors.tooManyFiles', { max: limits.max_files_per_batch })
  }

  let totalBytes = 0
  for (const file of files) {
    const maxBytes = getMaxUploadSizeMbForFile(file.name) * 1024 * 1024
    if (file.size > maxBytes) {
      return t('projectDetail.errors.fileTooLarge', {
        name: file.name,
        max: getMaxUploadSizeMbForFile(file.name),
      })
    }
    totalBytes += file.size
  }

  const maxTotalBytes = limits.max_total_size_mb * 1024 * 1024
  if (totalBytes > maxTotalBytes) {
    return t('projectDetail.errors.totalTooLarge', { max: limits.max_total_size_mb })
  }

  return ''
}

function updateSelectedFiles(files: File[]) {
  selectedFiles.value = files
  clearLanguageDetectState()
  uploadMessage.value = validateSelectedUploadFiles(files) || uploadGenerationValidationError.value
}

function toggleUploadTargetLanguage(languageCode: string) {
  if (uploading.value || isProjectLanguagePairBound.value || languageCode === uploadSourceLanguage.value) {
    return
  }
  const selected = uploadTargetLanguages.value.includes(languageCode)
  uploadTargetLanguages.value = selected
    ? uploadTargetLanguages.value.filter((code) => code !== languageCode)
    : [...uploadTargetLanguages.value, languageCode]
  uploadMessage.value = uploadFileValidationError.value || uploadGenerationValidationError.value
}

function closeUploadTargetMenu() {
  uploadTargetMenuOpen.value = false
  uploadTargetLanguageSearch.value = ''
}

function toggleUploadTargetMenu() {
  if (uploading.value || isProjectLanguagePairBound.value) {
    return
  }
  uploadTargetMenuOpen.value = !uploadTargetMenuOpen.value
  if (!uploadTargetMenuOpen.value) {
    uploadTargetLanguageSearch.value = ''
  }
}

function removeUploadTargetLanguage(languageCode: string) {
  if (uploading.value || isProjectLanguagePairBound.value) {
    return
  }
  uploadTargetLanguages.value = uploadTargetLanguages.value.filter((code) => code !== languageCode)
  uploadMessage.value = uploadFileValidationError.value || uploadGenerationValidationError.value
}

function onFileChange(event: Event) {
  updateSelectedFiles(Array.from((event.target as HTMLInputElement).files ?? []))
}

function onFileDrop(event: DragEvent) {
  updateSelectedFiles(Array.from(event.dataTransfer?.files ?? []))
}

function clearSelectedUploadFiles() {
  if (uploading.value) {
    return
  }

  selectedFiles.value = []
  uploadMessage.value = ''
  clearLanguageDetectState()
  uploadInputKey.value += 1
}

function resetUploadForm() {
  clearSelectedUploadFiles()
  uploadPercent.value = 0
  closeUploadTargetMenu()
  documentParseMode.value = 'full'
  documentParseOptions.value = { ...DEFAULT_DOCUMENT_PARSE_OPTIONS }
}

function openUploadDialog() {
  if (!canOpenUploadModal.value) {
    return
  }

  resetUploadForm()
  uploadSourceLanguage.value = projectBoundLanguagePair.value?.source || ''
  uploadTargetLanguages.value = projectBoundLanguagePair.value?.target
    ? [projectBoundLanguagePair.value.target]
    : []
  showUploadModal.value = true
}

function closeUploadDialog() {
  if (uploading.value) {
    return
  }

  showUploadModal.value = false
  resetUploadForm()
}

function openPreTranslateDialog() {
  if (!canOpenPreTranslate.value) {
    return
  }
  const hasRunningProgress = Object.values(preTranslateProgressByFileId.value).some((state) => state.running)
  if (!hasRunningProgress) {
    preTranslateProgressByFileId.value = {}
  }
  showPreTranslateDialog.value = true
}

function openTermExtractionDialog() {
  if (!canOpenTermExtraction.value) {
    return
  }
  termExtractionNeedsReload.value = false
  showTermExtractionDialog.value = true
}

async function loadAssignableUsers() {
  if (assignableUsers.value.length > 0 || loadingAssignableUsers.value) {
    return
  }
  loadingAssignableUsers.value = true
  try {
    const { data } = await http.get<User[]>('/auth/assignable-users')
    assignableUsers.value = data.filter((user) => user.role === 'user' && user.is_active)
  } catch (error) {
    toast.error(getErrorMessage(error, '译者列表加载失败。'))
  } finally {
    loadingAssignableUsers.value = false
  }
}

async function loadProjectAssignments() {
  if (!project.value) {
    assignmentDrafts.value = []
    return
  }
  loadingAssignments.value = true
  try {
    const { data } = await http.get<ProjectAssignmentsResponse>(`/projects/${project.value.id}/assignments`)
    if (!activeAssignmentWorkflowStepId.value) {
      activeAssignmentWorkflowStepId.value = data.workflow_steps?.[0]?.id || projectWorkflowSteps.value[0]?.id || ''
    }
    assignmentDrafts.value = data.assignments.map((assignment) => {
      const fileRanges = new Map<string, AssignmentFileRangeDraft>()
      const fileIds = new Set(assignment.file_record_ids)
      for (const range of assignment.file_ranges || []) {
        fileIds.add(range.file_record_id)
        if (range.range_start !== null || range.range_end !== null) {
          fileRanges.set(range.file_record_id, {
            range_start: range.range_start,
            range_end: range.range_end,
          })
        }
      }
      return {
        assignee_id: assignment.assignee_id,
        workflow_step_id: assignment.workflow_step_id || activeAssignmentWorkflowStepId.value,
        file_record_ids: fileIds,
        file_ranges: fileRanges,
      }
    })
  } catch (error) {
    toast.error(getErrorMessage(error, '项目指派加载失败。'))
  } finally {
    loadingAssignments.value = false
  }
}

async function loadAssignmentEvents() {
  if (!project.value || !canAssignProject.value) {
    assignmentEvents.value = []
    return
  }
  assignmentEventsLoading.value = true
  try {
    const { data } = await http.get<AssignmentEventsResponse>(`/projects/${project.value.id}/assignment-events`, {
      params: { limit: 100 },
    })
    assignmentEvents.value = data.items
  } catch (error) {
    toast.error(getErrorMessage(error, '指派记录加载失败。'))
  } finally {
    assignmentEventsLoading.value = false
  }
}

function isUserInAssignmentDraft(userId: string) {
  const workflowStepId = activeAssignmentWorkflowStepId.value
  return assignmentDrafts.value.some((draft) => draft.assignee_id === userId && draft.workflow_step_id === workflowStepId)
}

function getAssignmentDraft(userId: string) {
  const workflowStepId = activeAssignmentWorkflowStepId.value
  return assignmentDrafts.value.find((draft) => (
    draft.assignee_id === userId && draft.workflow_step_id === workflowStepId
  )) || null
}

function getAssignableUserById(userId: string) {
  return assignableUsers.value.find((user) => user.id === userId) || null
}

function getAssignmentUserName(userId: string) {
  return getAssigneeDisplayName(getAssignableUserById(userId)) || '未知译者'
}

function toggleAssignmentUser(user: User) {
  const workflowStepId = activeAssignmentWorkflowStepId.value
  if (!workflowStepId) {
    return
  }
  if (isUserInAssignmentDraft(user.id)) {
    assignmentDrafts.value = assignmentDrafts.value.filter((draft) => !(
      draft.assignee_id === user.id && draft.workflow_step_id === workflowStepId
    ))
    return
  }
  assignmentDrafts.value = [
    ...assignmentDrafts.value,
    {
      assignee_id: user.id,
      workflow_step_id: workflowStepId,
      file_record_ids: new Set<string>(),
      file_ranges: new Map<string, AssignmentFileRangeDraft>(),
    },
  ]
}

function isFileCheckedForUser(userId: string, fileRecordId: string) {
  return getAssignmentDraft(userId)?.file_record_ids.has(fileRecordId) ?? false
}

function toggleAssignmentFile(userId: string, fileRecordId: string) {
  const workflowStepId = activeAssignmentWorkflowStepId.value
  assignmentDrafts.value = assignmentDrafts.value.map((draft) => {
    if (draft.assignee_id !== userId || draft.workflow_step_id !== workflowStepId) {
      return draft
    }
    const nextFileIds = new Set(draft.file_record_ids)
    const nextFileRanges = new Map(draft.file_ranges)
    if (nextFileIds.has(fileRecordId)) {
      nextFileIds.delete(fileRecordId)
      nextFileRanges.delete(fileRecordId)
    } else {
      nextFileIds.add(fileRecordId)
    }
    return {
      ...draft,
      file_record_ids: nextFileIds,
      file_ranges: nextFileRanges,
    }
  })
}

function getFilteredAssignmentFiles(draft: AssignmentDraft) {
  let files = [...tableRows.value]

  const keyword = normalizeAssignmentKeyword(assignmentFileSearch.value)
  if (keyword) {
    files = files.filter((file) => normalizeAssignmentKeyword(file.filename).includes(keyword))
  }

  if (assignmentFileStateFilter.value !== 'all') {
    files = files.filter((file) => {
      const checked = draft.file_record_ids.has(file.id)
      return assignmentFileStateFilter.value === 'checked' ? checked : !checked
    })
  }

  return files
}

function getAssignableMergeViewFileIds(view: MergeView) {
  const projectFileIds = new Set(tableRows.value.map((file) => file.id))
  return (view.file_ids || []).filter((fileId) => projectFileIds.has(fileId))
}

function isAssignmentMergeViewChecked(draft: AssignmentDraft, view: MergeView) {
  const fileIds = getAssignableMergeViewFileIds(view)
  return fileIds.length > 0 && fileIds.every((fileId) => draft.file_record_ids.has(fileId))
}

function isAssignmentMergeViewPartial(draft: AssignmentDraft, view: MergeView) {
  const fileIds = getAssignableMergeViewFileIds(view)
  if (fileIds.length === 0 || isAssignmentMergeViewChecked(draft, view)) {
    return false
  }
  return fileIds.some((fileId) => draft.file_record_ids.has(fileId))
}

function toggleAssignmentMergeView(draft: AssignmentDraft, view: MergeView) {
  const fileIds = getAssignableMergeViewFileIds(view)
  if (fileIds.length === 0) {
    return
  }
  const shouldRemove = isAssignmentMergeViewChecked(draft, view)
  assignmentDrafts.value = assignmentDrafts.value.map((item) => {
    if (item.assignee_id !== draft.assignee_id || item.workflow_step_id !== draft.workflow_step_id) {
      return item
    }
    const nextFileIds = new Set(item.file_record_ids)
    const nextFileRanges = new Map(item.file_ranges)
    for (const fileId of fileIds) {
      if (shouldRemove) {
        nextFileIds.delete(fileId)
        nextFileRanges.delete(fileId)
      } else {
        nextFileIds.add(fileId)
      }
    }
    return {
      ...item,
      file_record_ids: nextFileIds,
      file_ranges: nextFileRanges,
    }
  })
}

function getAssignmentMergeViewMeta(view: MergeView) {
  return [
    `${getAssignableMergeViewFileIds(view).length} 个文件`,
    view.creator_name ? `创建人 ${view.creator_name}` : '',
  ].filter(Boolean).join(' · ')
}

function getCheckedAssignmentMergeViewIds(draft: AssignmentDraft) {
  return assignmentMergeViews.value
    .filter((view) => isAssignmentMergeViewChecked(draft, view))
    .map((view) => view.id)
}

function updateFilteredAssignmentFiles(userId: string, checked: boolean) {
  const draft = getAssignmentDraft(userId)
  if (!draft) {
    return
  }
  const filteredFileIds = getFilteredAssignmentFiles(draft).map((file) => file.id)
  if (filteredFileIds.length === 0) {
    return
  }

  assignmentDrafts.value = assignmentDrafts.value.map((item) => {
    if (item.assignee_id !== userId || item.workflow_step_id !== draft.workflow_step_id) {
      return item
    }
    const nextFileIds = new Set(item.file_record_ids)
    const nextFileRanges = new Map(item.file_ranges)
    for (const fileId of filteredFileIds) {
      if (checked) {
        nextFileIds.add(fileId)
      } else {
        nextFileIds.delete(fileId)
        nextFileRanges.delete(fileId)
      }
    }
    return {
      ...item,
      file_record_ids: nextFileIds,
      file_ranges: nextFileRanges,
    }
  })
}

function selectFilteredAssignmentFiles(userId: string) {
  updateFilteredAssignmentFiles(userId, true)
}

function clearFilteredAssignmentFiles(userId: string) {
  updateFilteredAssignmentFiles(userId, false)
}

function getAssignmentRangeInputValue(
  draft: AssignmentDraft,
  fileRecordId: string,
  field: AssignmentFileRangeField,
) {
  const value = draft.file_ranges.get(fileRecordId)?.[field]
  return value ?? ''
}

function getAssignmentInputValue(event: Event) {
  return event.target instanceof HTMLInputElement ? event.target.value : ''
}

function parseAssignmentRangeInput(value: string) {
  const trimmed = value.trim()
  if (!trimmed) {
    return null
  }
  const numericValue = Number(trimmed)
  return Number.isFinite(numericValue) ? numericValue : null
}

function updateAssignmentFileRange(
  userId: string,
  fileRecordId: string,
  field: AssignmentFileRangeField,
  value: string,
) {
  const workflowStepId = activeAssignmentWorkflowStepId.value
  assignmentDrafts.value = assignmentDrafts.value.map((draft) => {
    if (draft.assignee_id !== userId || draft.workflow_step_id !== workflowStepId) {
      return draft
    }
    const nextFileIds = new Set(draft.file_record_ids)
    const nextFileRanges = new Map(draft.file_ranges)
    const currentRange = nextFileRanges.get(fileRecordId) ?? { range_start: null, range_end: null }
    const nextRange = {
      ...currentRange,
      [field]: parseAssignmentRangeInput(value),
    }
    nextFileIds.add(fileRecordId)
    if (nextRange.range_start === null && nextRange.range_end === null) {
      nextFileRanges.delete(fileRecordId)
    } else {
      nextFileRanges.set(fileRecordId, nextRange)
    }
    return {
      ...draft,
      file_record_ids: nextFileIds,
      file_ranges: nextFileRanges,
    }
  })
}

function getAssignmentFileSegmentCount(fileRecordId: string) {
  return Number(projectFileById.value.get(fileRecordId)?.total_segments || 0)
}

function getAssignmentFileLabel(fileRecordId: string) {
  return projectFileById.value.get(fileRecordId)?.filename || fileRecordId
}

function validateAssignmentRanges() {
  for (const draft of assignmentDrafts.value) {
    for (const fileRecordId of draft.file_record_ids) {
      const range = draft.file_ranges.get(fileRecordId)
      if (!range || (range.range_start === null && range.range_end === null)) {
        continue
      }
      const fileLabel = getAssignmentFileLabel(fileRecordId)
      if (range.range_start === null || range.range_end === null) {
        toast.error(`${fileLabel} 的句段范围需要同时填写起始段和结束段。`)
        return false
      }
      if (
        !Number.isInteger(range.range_start)
        || !Number.isInteger(range.range_end)
        || range.range_start < 1
        || range.range_end < 1
      ) {
        toast.error(`${fileLabel} 的句段范围必须是大于 0 的整数。`)
        return false
      }
      if (range.range_start > range.range_end) {
        toast.error(`${fileLabel} 的起始段不能大于结束段。`)
        return false
      }
      const segmentCount = getAssignmentFileSegmentCount(fileRecordId)
      if (segmentCount > 0 && range.range_end > segmentCount) {
        toast.error(`${fileLabel} 的结束段不能超过 ${segmentCount}。`)
        return false
      }
    }
  }
  return true
}

function buildAssignmentFilePayload(draft: AssignmentDraft) {
  const file_record_ids: string[] = []
  const file_ranges: Array<{ file_record_id: string; range_start: number; range_end: number }> = []
  for (const fileRecordId of draft.file_record_ids) {
    const range = draft.file_ranges.get(fileRecordId)
    if (range && range.range_start !== null && range.range_end !== null) {
      file_ranges.push({
        file_record_id: fileRecordId,
        range_start: range.range_start,
        range_end: range.range_end,
      })
    } else {
      file_record_ids.push(fileRecordId)
    }
  }
  return { file_record_ids, file_ranges }
}

function getAssignmentEventActionLabel(action: string) {
  const labels: Record<string, string> = {
    project_assigned: '项目指派',
    project_unassigned: '取消项目指派',
    file_permission_granted: '文件授权',
    file_permission_revoked: '取消文件授权',
  }
  return labels[action] || action
}

function resetAssignmentFilters() {
  assignmentUserSearch.value = ''
  assignmentUserTypeFilter.value = 'all'
  assignmentUserStateFilter.value = 'all'
  assignmentFileSearch.value = ''
  assignmentFileStateFilter.value = 'all'
  hideAssignmentTooltip()
}

function resetAssignmentDialogState() {
  assignmentDrafts.value = []
  resetAssignmentFilters()
}

function updateAssignmentTooltipPosition(event: MouseEvent) {
  const maxWidth = Math.min(340, Math.max(220, window.innerWidth - 32))
  const left = Math.min(event.clientX + 14, window.innerWidth - maxWidth - 12)
  const top = Math.min(event.clientY + 16, window.innerHeight - 80)
  assignmentTooltipStyle.value = {
    left: `${Math.max(12, left)}px`,
    top: `${Math.max(12, top)}px`,
    maxWidth: `${maxWidth}px`,
  }
}

function showAssignmentTooltip(event: MouseEvent, text: string | null | undefined) {
  const value = String(text || '').trim()
  if (!value) {
    hideAssignmentTooltip()
    return
  }
  assignmentTooltipText.value = value
  updateAssignmentTooltipPosition(event)
}

function hideAssignmentTooltip() {
  assignmentTooltipText.value = ''
  assignmentTooltipStyle.value = {}
}

async function openAssignmentDialog(_row?: ProjectFileItem | null) {
  if (!canAssignProject.value) {
    return
  }
  closeActionMenu()
  resetAssignmentFilters()
  ensureActiveAssignmentWorkflowStep()
  showAssignmentDialog.value = true
  await Promise.all([loadAssignableUsers(), loadProjectAssignments(), loadMergeViews()])
}

function closeAssignmentDialog() {
  if (savingAssignment.value) {
    return
  }
  showAssignmentDialog.value = false
  resetAssignmentDialogState()
}

async function saveAssignment() {
  if (!project.value || !canAssignProject.value) {
    return
  }
  if (!validateAssignmentRanges()) {
    return
  }
  savingAssignment.value = true
  try {
    await http.patch(`/projects/${project.value.id}/assignments`, {
      assignments: assignmentDrafts.value.map((draft) => {
        const filePayload = buildAssignmentFilePayload(draft)
        return {
          assignee_id: draft.assignee_id,
          workflow_step_id: draft.workflow_step_id,
          file_record_ids: filePayload.file_record_ids,
          file_ranges: filePayload.file_ranges,
          merge_view_ids: getCheckedAssignmentMergeViewIds(draft),
        }
      }),
    })
    toast.success('项目指派已更新。')
    showAssignmentDialog.value = false
    resetAssignmentDialogState()
    await loadProject()
    await loadAssignmentEvents()
  } catch (error) {
    toast.error(getErrorMessage(error, '项目指派保存失败。'))
  } finally {
    savingAssignment.value = false
  }
}

async function closeTermExtractionDialog() {
  showTermExtractionDialog.value = false
  if (termExtractionNeedsReload.value) {
    termExtractionNeedsReload.value = false
    await loadProject()
  }
}

function handleTermExtractionDone() {
  termExtractionNeedsReload.value = true
}

function closePreTranslateDialog() {
  if (loading.value) {
    return
  }
  showPreTranslateDialog.value = false
}

async function handlePreTranslateDone() {
  showPreTranslateDialog.value = false
  selectedFileIds.value = new Set<string>()
  await loadProject()
  preTranslateProgressByFileId.value = {}
  activePretranslationTaskIdByFileId.value = {}
}

function handlePreTranslateProgress(payload: PreTranslateProgressPayload) {
  preTranslateProgressByFileId.value = {
    ...preTranslateProgressByFileId.value,
    [payload.fileId]: {
      progress: Math.max(0, Math.min(100, Math.round(payload.progress))),
      status: payload.status,
      running: payload.running,
    },
  }
}

function buildActivePretranslationStatus(task: ActivePretranslationTaskStatus) {
  const parts: string[] = []
  if (task.message) {
    parts.push(task.message)
  }
  if (task.provider || task.model) {
    parts.push([task.provider, task.model].filter(Boolean).join(' / '))
  }
  if (task.total_segments > 0 || task.processed_segments > 0) {
    const total = Math.max(task.total_segments, task.processed_segments)
    parts.push(`${task.processed_segments}/${total}`)
  }
  if (task.unique_segments > 0 && task.deduplicated_segments > 0) {
    parts.push(`唯一 ${task.unique_segments}，去重 ${task.deduplicated_segments}`)
  }
  if (task.updated_segments > 0 || task.error_segments > 0) {
    parts.push(`成功 ${task.updated_segments}，失败 ${task.error_segments}`)
  }
  if (task.error && task.status === 'failed') {
    parts.push(task.error)
  }
  return parts.filter(Boolean).join(' · ') || t('projectDetail.preTranslate.progress.running')
}

function clearActivePretranslationPollTimer() {
  if (activePretranslationPollTimer !== null) {
    window.clearInterval(activePretranslationPollTimer)
    activePretranslationPollTimer = null
  }
}

function ensureActivePretranslationPolling() {
  if (activePretranslationPollTimer !== null) {
    return
  }
  activePretranslationPollTimer = window.setInterval(() => {
    void loadActivePretranslationTasks()
  }, ACTIVE_PRETRANSLATION_POLL_INTERVAL_MS)
}

async function loadActivePretranslationTasks() {
  if (!project.value?.id) {
    clearActivePretranslationPollTimer()
    activePretranslationTaskIdByFileId.value = {}
    return
  }
  try {
    const { data } = await http.get<ActivePretranslationTasksResponse>(
      `/projects/${project.value.id}/pretranslation-tasks/active`,
    )
    const hasRunningProgress = Object.values(preTranslateProgressByFileId.value).some((state) => state.running)
    const hadActiveTasks = Object.keys(activePretranslationTaskIdByFileId.value).length > 0
    if (!data.tasks.length) {
      clearActivePretranslationPollTimer()
      activePretranslationTaskIdByFileId.value = {}
      if (hasRunningProgress || hadActiveTasks) {
        preTranslateProgressByFileId.value = {}
        await loadProject()
      }
      return
    }

    const nextTaskIdByFileId: Record<string, string> = {}
    for (const task of data.tasks) {
      nextTaskIdByFileId[task.file_record_id] = task.id
      handlePreTranslateProgress({
        fileId: task.file_record_id,
        progress: task.progress,
        status: buildActivePretranslationStatus(task),
        running: ACTIVE_PRETRANSLATION_STATUSES.has(task.status),
      })
    }
    activePretranslationTaskIdByFileId.value = nextTaskIdByFileId
    ensureActivePretranslationPolling()
  } catch {
    clearActivePretranslationPollTimer()
    activePretranslationTaskIdByFileId.value = {}
  }
}

function getFileDisplayProgress(row: ProjectRow) {
  return Number(row.progress || 0)
}

function getFileDisplayProgressStatus(row: ProjectRow) {
  return String(row.status || '')
}

function getFileDisplayProgressMessage(row: ProjectRow) {
  return row.active_operation && row.active_operation !== 'pre_translate'
    ? String(row.active_operation_message || '')
    : ''
}

function getFileWorkflowProgress(row: ProjectRow) {
  return (row.workflow_progress || []) as WorkflowProgress[]
}

function getFilePretranslationProgress(row: ProjectRow) {
  const progress = preTranslateProgressByFileId.value[String(row.id)]?.progress
  return typeof progress === 'number'
    ? progress
    : Number(row.pretranslation_progress || 0)
}

function getFilePretranslationProgressStatus(row: ProjectRow) {
  const state = preTranslateProgressByFileId.value[String(row.id)]
  return (state?.running && state.progress < 100) ? 'processing' : String(row.status || '')
}

function getFilePretranslationProgressMessage(row: ProjectRow) {
  const state = preTranslateProgressByFileId.value[String(row.id)]
  if (state?.running || getActivePretranslationTaskId(row)) {
    return state?.status || (row.active_operation === 'pre_translate' ? row.active_operation_message : '') || ''
  }
  if (state?.progress >= 100) {
    return state.status || ''
  }
  return ''
}

function canCancelFilePretranslation(row: ProjectRow) {
  const state = preTranslateProgressByFileId.value[String(row.id)]
  return Boolean(getActivePretranslationTaskId(row) && state?.running !== false)
}

function getActivePretranslationTaskId(row: ProjectRow) {
  return activePretranslationTaskIdByFileId.value[String(row.id)] || ''
}

function setPretranslationTaskCanceling(taskId: string, canceling: boolean) {
  const next = new Set(cancelingPretranslationTaskIds.value)
  if (canceling) {
    next.add(taskId)
  } else {
    next.delete(taskId)
  }
  cancelingPretranslationTaskIds.value = next
}

function isFilePretranslationCanceling(row: ProjectRow) {
  const taskId = getActivePretranslationTaskId(row)
  return taskId ? cancelingPretranslationTaskIds.value.has(taskId) : false
}

async function cancelFilePretranslation(row: ProjectRow) {
  const taskId = getActivePretranslationTaskId(row)
  if (!taskId) {
    return
  }
  setPretranslationTaskCanceling(taskId, true)
  handlePreTranslateProgress({
    fileId: String(row.id),
    progress: getFilePretranslationProgress(row),
    status: '正在停止预翻译任务。',
    running: true,
  })
  try {
    await http.post(`/pretranslation-tasks/${taskId}/cancel`)
    await loadActivePretranslationTasks()
  } catch (error) {
    toast.error(getErrorMessage(error, '预翻译停止失败。'))
  } finally {
    setPretranslationTaskCanceling(taskId, false)
  }
}

function openProjectIssueDialog() {
  if (!project.value || !canOpenProjectIssueDialog.value) {
    return
  }
  issueDialogTarget.value = {
    fileRecordId: null,
    label: project.value.filename || t('projectDetail.titleFallback'),
  }
  showIssueDialog.value = true
}

function openFileIssueDialog(row: ProjectRow) {
  if (!project.value) {
    return
  }
  closeActionMenu()
  issueDialogTarget.value = {
    fileRecordId: String(row.id),
    label: t('issueMarker.list.fileScope', { name: String(row.filename || '') }),
  }
  showIssueDialog.value = true
}

async function handleIssueSaved(_marker: IssueMarker) {
  showIssueDialog.value = false
  issueDialogTarget.value = null
  toast.success(t('issueMarker.messages.saved'))
  await loadProject()
}

function getIssueCategoryLabel(category: string) {
  return t(`issueMarker.categories.${category}` as any)
}

function getIssueSeverityLabel(severity: string) {
  return t(`issueMarker.severity.${severity}` as any)
}

function getIssueStatusLabel(status: string) {
  return t(`issueMarker.status.${status}` as any)
}

async function setIssueStatus(marker: IssueMarker, status: IssueStatus) {
  updatingIssueId.value = marker.id
  try {
    await http.patch(`/issue-markers/${marker.id}`, { status })
    toast.success(t('issueMarker.messages.updated'))
    await loadProject()
  } catch (error) {
    toast.show({
      tone: 'error',
      title: t('issueMarker.errors.save'),
      message: getErrorMessage(error, ''),
    })
  } finally {
    updatingIssueId.value = null
  }
}

function resetFileSelectionRangeDefaults() {
  const fallbackEnd = Math.max(filteredTableRows.value.length, 1)
  const start = currentPageFileRangeStart.value || 1
  const end = currentPageFileRangeEnd.value || fallbackEnd
  fileSelectionRangeStart.value = String(start)
  fileSelectionRangeEnd.value = String(end)
  fileSelectionRangeError.value = ''
}

function closeFileSelectionMenu() {
  showFileSelectionMenu.value = false
  fileSelectionRangeError.value = ''
}

function toggleFileSelectionMenu() {
  if (filteredTableRows.value.length === 0) {
    return
  }
  if (showFileSelectionMenu.value) {
    closeFileSelectionMenu()
    return
  }
  closeActionMenu()
  showProjectExportMenu.value = false
  resetFileSelectionRangeDefaults()
  showFileSelectionMenu.value = true
}

function selectCurrentFilePageFromMenu() {
  const nextIds = new Set(selectedFileIds.value)
  for (const row of pagedRows.value) {
    nextIds.add(row.id)
  }
  selectedFileIds.value = nextIds
  closeFileSelectionMenu()
}

function selectAllFilteredFiles() {
  selectedFileIds.value = new Set(filteredTableRows.value.map((row) => row.id))
  closeFileSelectionMenu()
}

function clearSelectedProjectFiles() {
  selectedFileIds.value = new Set<string>()
  closeFileSelectionMenu()
}

function selectFileRangeFromMenu() {
  const total = filteredTableRows.value.length
  if (total === 0) {
    fileSelectionRangeError.value = '当前没有可选择的文件。'
    return
  }

  const rawStart = Number.parseInt(fileSelectionRangeStart.value, 10)
  const rawEnd = Number.parseInt(fileSelectionRangeEnd.value, 10)
  if (!Number.isFinite(rawStart) || !Number.isFinite(rawEnd)) {
    fileSelectionRangeError.value = '请输入有效的起止序号。'
    return
  }

  const start = Math.min(Math.max(Math.min(rawStart, rawEnd), 1), total)
  const end = Math.min(Math.max(Math.max(rawStart, rawEnd), 1), total)
  fileSelectionRangeStart.value = String(start)
  fileSelectionRangeEnd.value = String(end)
  selectedFileIds.value = new Set(filteredTableRows.value.slice(start - 1, end).map((row) => row.id))
  closeFileSelectionMenu()
}

function closeActionMenu() {
  openActionMenuId.value = null
  actionMenuStyle.value = {}
}

function toggleActionMenu(ev: MouseEvent, id: string) {
  if (openActionMenuId.value === id) {
    closeActionMenu()
    return
  }
  const btn = ev.currentTarget as HTMLElement
  const r = btn.getBoundingClientRect()
  openActionMenuId.value = id
  actionMenuStyle.value = {
    position: 'fixed',
    top: `${Math.round(r.bottom + 6)}px`,
    left: `${Math.round(r.right)}px`,
    transform: 'translateX(-100%)',
    zIndex: '3000',
  }
}

function isEventFromFloatingActionMenu(ev: MouseEvent) {
  return ev.composedPath().some(
    (n) => n instanceof HTMLElement && n.classList.contains('pd-action-menu__dropdown--floating'),
  )
}

function stopActionMenuEventBubble(ev: MouseEvent) {
  ev.stopPropagation()
}

function handleDocumentClick(ev: MouseEvent) {
  const target = ev.target as HTMLElement
  if (!target.closest('.upload-target-select')) {
    closeUploadTargetMenu()
  }
  if (!target.closest('.pd-export-dropdown')) {
    showProjectExportMenu.value = false
  }
  if (!target.closest('.pd-file-selection')) {
    closeFileSelectionMenu()
  }
  if (isEventFromFloatingActionMenu(ev)) {
    return
  }
  closeActionMenu()
}

function handleDocumentScroll() {
  closeUploadTargetMenu()
  showProjectExportMenu.value = false
  closeFileSelectionMenu()
  if (openActionMenuId.value) {
    closeActionMenu()
  }
}

function goBack() {
  void router.push(backRoute.value)
}

function switchProjectTab(tab: ProjectTab) {
  activeTab.value = tab
  if (tab === 'settings') {
    basicCollapsed.value = true
  }
  if (tab === 'views') {
    void loadMergeViews()
  }
  if (tab === 'stats' && statisticsSelectedFileIds.value.size === 0 && selectedFileIds.value.size > 0) {
    statisticsSelectedFileIds.value = new Set(selectedFileIds.value)
  }
  if (tab === 'stats' && canManageProject.value && statisticsReports.value.length === 0) {
    void loadDocumentStatisticsReports()
  }
}

function getProjectSettingsSectionHash(section: ProjectSettingsSection) {
  return `#project-settings-${section}`
}

function switchProjectSettingsSection(section: ProjectSettingsSection) {
  activeTab.value = 'settings'
  activeProjectSettingsSection.value = section
  basicCollapsed.value = true
  const hash = getProjectSettingsSectionHash(section)
  if (route.hash !== hash) {
    void router.replace({
      path: route.path,
      query: route.query,
      hash,
    })
  }
}

function syncProjectSettingsHash() {
  if (!route.hash.startsWith('#project-settings-')) {
    return
  }
  activeTab.value = 'settings'
  activeProjectSettingsSection.value = getProjectSettingsSectionFromHash(route.hash)
  basicCollapsed.value = true
}

function updateStatisticsSelectedFileIds(ids: Set<string>) {
  statisticsSelectedFileIds.value = new Set(ids)
}

async function generateDocumentStatisticsTable() {
  if (!project.value || !canGenerateStatistics.value) {
    return
  }
  statisticsLoading.value = true
  pageError.value = ''
  const fileIds = Array.from(statisticsSelectedFileIds.value)

  try {
    const { data } = await http.post<ProjectDocumentStatisticsResponse>(
      `/projects/${project.value.id}/document-statistics`,
      { file_ids: fileIds },
    )
    const updatedFiles = new Map(data.files.map((file) => [file.id, file]))
    project.value = {
      ...project.value,
      files: project.value.files.map((file) => updatedFiles.get(file.id) ?? file),
    }
    if (data.report) {
      mergeDocumentStatisticsReports([data.report])
      selectDocumentStatisticsReport(data.report.id)
    }
    statisticsResultFileIds.value = new Set(fileIds)
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.statistics'))
  } finally {
    statisticsLoading.value = false
  }
}

async function loadDocumentStatisticsReports() {
  if (!project.value || !canManageProject.value || statisticsReportsLoading.value) {
    return
  }
  statisticsReportsLoading.value = true
  try {
    const { data } = await http.get<DocumentStatisticsReportsResponse>(
      `/projects/${project.value.id}/document-statistics-reports`,
      { params: { limit: 30, include_items: true } },
    )
    statisticsReports.value = sortDocumentStatisticsReports(data.items)
    if (activeStatisticsReportId.value && statisticsReports.value.some((report) => report.id === activeStatisticsReportId.value)) {
      selectDocumentStatisticsReport(activeStatisticsReportId.value, false)
    } else if (statisticsReports.value.length > 0) {
      selectDocumentStatisticsReport(statisticsReports.value[0].id, false)
    } else {
      activeStatisticsReportId.value = ''
      statisticsResultFileIds.value = new Set<string>()
    }
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.statisticsReports'))
  } finally {
    statisticsReportsLoading.value = false
  }
}

function clearDocumentStatisticsTable() {
  statisticsSelectedFileIds.value = new Set<string>()
  statisticsResultFileIds.value = new Set<string>()
  activeStatisticsReportId.value = ''
}

function openWorkbench(row: ProjectRow) {
  if (!canEnterWorkbench(row)) {
    return
  }

  closeActionMenu()
  const rowId = String(row.id)
  const resolved = router.resolve({
    name: 'workbench-focus',
    params: { id: rowId },
    query: {
      from: 'project',
      pid: props.id,
      ...(cameFromTasks.value ? { parent: 'tasks' } : {}),
    },
  })
  window.open(resolved.href, '_blank', 'noopener,noreferrer')
}

async function loadMergeViews() {
  if (!project.value || loadingMergeViews.value) {
    return
  }
  loadingMergeViews.value = true
  try {
    const data = await listProjectMergeViews(project.value.id)
    mergeViews.value = data.items
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.mergeViews.errors.load'))
  } finally {
    loadingMergeViews.value = false
  }
}

function openMergeView(view: MergeView) {
  const resolved = router.resolve({
    name: 'merge-view-focus',
    params: { viewId: view.id },
    query: {
      from: 'project',
      pid: props.id,
      ...(cameFromTasks.value ? { parent: 'tasks' } : {}),
    },
  })
  window.open(resolved.href, '_blank', 'noopener,noreferrer')
}

function openCreateMergeViewDialog() {
  if (!canOpenMergeViewDialog.value) {
    toast.warn(mergeOpenButtonTitle.value || t('projectDetail.mergeViews.selectAtLeastTwo'))
    return
  }
  mergeViewDialogMode.value = 'create'
  activeMergeView.value = null
  mergeViewName.value = buildDefaultMergeViewName()
  mergeViewDialogError.value = ''
  showMergeViewDialog.value = true
}

function openRenameMergeViewDialog(view: MergeView) {
  if (!canManageMergeView(view)) {
    return
  }
  mergeViewDialogMode.value = 'rename'
  activeMergeView.value = view
  mergeViewName.value = view.name
  mergeViewDialogError.value = ''
  showMergeViewDialog.value = true
}

function closeMergeViewDialog() {
  if (savingMergeView.value) {
    return
  }
  showMergeViewDialog.value = false
  activeMergeView.value = null
  mergeViewDialogError.value = ''
}

async function submitMergeViewDialog() {
  if (!project.value) {
    return
  }
  const name = mergeViewName.value.trim()
  if (!name) {
    mergeViewDialogError.value = t('projectDetail.mergeViews.errors.nameRequired')
    return
  }

  savingMergeView.value = true
  mergeViewDialogError.value = ''
  try {
    if (mergeViewDialogMode.value === 'create') {
      const created = await createProjectMergeView(project.value.id, {
        name,
        file_ids: selectedMergeViewFiles.value.map((file) => file.id),
      })
      mergeViews.value = [created, ...mergeViews.value.filter((view) => view.id !== created.id)]
      showMergeViewDialog.value = false
      toast.success(t('projectDetail.mergeViews.messages.created'))
      openMergeView(created)
      return
    }

    if (!activeMergeView.value) {
      return
    }
    const updated = await updateMergeView(activeMergeView.value.id, { name })
    mergeViews.value = mergeViews.value.map((view) => (view.id === updated.id ? updated : view))
    showMergeViewDialog.value = false
    activeMergeView.value = null
    toast.success(t('projectDetail.mergeViews.messages.renamed'))
  } catch (error) {
    mergeViewDialogError.value = getErrorMessage(
      error,
      mergeViewDialogMode.value === 'create'
        ? t('projectDetail.mergeViews.errors.create')
        : t('projectDetail.mergeViews.errors.rename'),
    )
  } finally {
    savingMergeView.value = false
  }
}

async function deleteSavedMergeView(view: MergeView) {
  if (!canManageMergeView(view) || mergeViewActionId.value) {
    return
  }
  const confirmed = await confirm({
    title: t('projectDetail.mergeViews.deleteTitle'),
    message: t('projectDetail.mergeViews.deleteConfirm', { name: view.name }),
    confirmText: t('common.actions.delete'),
    danger: true,
  })
  if (!confirmed) {
    return
  }

  mergeViewActionId.value = view.id
  try {
    await deleteMergeView(view.id)
    mergeViews.value = mergeViews.value.filter((item) => item.id !== view.id)
    toast.success(t('projectDetail.mergeViews.messages.deleted'))
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.mergeViews.errors.delete'))
  } finally {
    mergeViewActionId.value = ''
  }
}

async function loadProject(options: { preserveFilePagination?: boolean } = {}) {
  loading.value = true
  pageError.value = ''

  try {
    const { data } = await http.get<ProjectDetail>(`/projects/${props.id}`)
    project.value = data
    ensureActiveAssignmentWorkflowStep()
    syncSettingsForm(data)
    guidelinesText.value = data.translation_guidelines || ''
    if (options.preserveFilePagination) {
      applyFilePaginationFromRouteQuery()
    } else {
      currentPage.value = 1
    }
    selectedFileIds.value = new Set<string>()
    statisticsSelectedFileIds.value = new Set<string>()
    statisticsResultFileIds.value = new Set<string>()
    statisticsReports.value = []
    activeStatisticsReportId.value = ''
    if (activeTab.value === 'views') {
      void loadMergeViews()
    }
    if (canAssignProject.value) {
      void loadAssignmentEvents()
    } else {
      assignmentEvents.value = []
    }
    if (data.can_manage) {
      void loadProjectTranslationMemorySettings()
      void loadProjectTermBaseSettings()
      void loadProjectQualityQASettings()
      if (activeTab.value === 'stats') {
        void loadDocumentStatisticsReports()
      }
    } else {
      translationMemorySettings.value = null
      termBaseSettings.value = null
      qualityQASettings.value = null
    }
    void loadActivePretranslationTasks()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.load'))
  } finally {
    loading.value = false
  }
}

async function loadProjectTermBaseSettings() {
  if (!project.value?.can_manage) {
    termBaseSettings.value = null
    return
  }
  loadingTermBaseSettings.value = true
  termBaseSettingsError.value = ''
  try {
    const { data } = await http.get<ProjectTermBaseSettingsResponse>(
      `/projects/${project.value.id}/term-base-settings`,
    )
    preserveTermBaseSettingsDisplayOrder(data, termBaseSettings.value)
    termBaseSettings.value = data
  } catch (error) {
    termBaseSettingsError.value = getErrorMessage(error, '术语库设置加载失败。')
  } finally {
    loadingTermBaseSettings.value = false
  }
}

async function loadProjectTranslationMemorySettings() {
  if (!project.value?.can_manage) {
    translationMemorySettings.value = null
    return
  }
  loadingTranslationMemorySettings.value = true
  translationMemorySettingsError.value = ''
  try {
    const { data } = await http.get<ProjectTranslationMemorySettingsResponse>(
      `/projects/${project.value.id}/translation-memory-settings`,
    )
    preserveTranslationMemorySettingsDisplayOrder(data, translationMemorySettings.value)
    translationMemorySettings.value = data
  } catch (error) {
    translationMemorySettingsError.value = getErrorMessage(error, '记忆库设置加载失败。')
  } finally {
    loadingTranslationMemorySettings.value = false
  }
}

function syncQualityQADraftFromSettings(data: QualityQASettingsResponse) {
  for (const rule of qualityQARules) {
    const ruleSetting = data.settings.rules?.[rule.key]
    qualityQADraft.rules[rule.key] = typeof ruleSetting?.enabled === 'boolean'
      ? ruleSetting.enabled
      : (rule.key === 'spelling_grammar' ? data.settings.spelling_grammar.enabled : rule.defaultEnabled)
  }
}

function buildQualityQARulesPayload() {
  return qualityQARules.reduce((payload, rule) => {
    payload[rule.key] = {
      enabled: qualityQADraft.rules[rule.key],
    }
    return payload
  }, {} as Record<QualityQARuleKey, { enabled: boolean }>)
}

function toggleAllQualityQARules(event: Event) {
  const checked = (event.target as HTMLInputElement | null)?.checked ?? false
  for (const rule of qualityQARules) {
    qualityQADraft.rules[rule.key] = checked
  }
}

async function loadProjectQualityQASettings() {
  if (!project.value?.can_manage) {
    qualityQASettings.value = null
    return
  }
  loadingQualityQASettings.value = true
  qualityQASettingsError.value = ''
  try {
    const { data } = await http.get<QualityQASettingsResponse>(
      `/projects/${project.value.id}/quality-qa-settings`,
    )
    qualityQASettings.value = data
    syncQualityQADraftFromSettings(data)
  } catch (error) {
    qualityQASettingsError.value = getErrorMessage(error, '质量保证设置加载失败。')
  } finally {
    loadingQualityQASettings.value = false
  }
}

async function saveProjectQualityQASettings() {
  if (!project.value?.can_manage || savingQualityQASettings.value) {
    return
  }
  savingQualityQASettings.value = true
  qualityQASettingsError.value = ''
  try {
    const { data } = await http.patch<QualityQASettingsResponse>(
      `/projects/${project.value.id}/quality-qa-settings`,
      {
        rules: buildQualityQARulesPayload(),
        spelling_grammar: {
          enabled: qualityQADraft.rules.spelling_grammar,
        },
      },
    )
    qualityQASettings.value = data
    syncQualityQADraftFromSettings(data)
    toast.success('质量保证设置已保存')
  } catch (error) {
    qualityQASettingsError.value = getErrorMessage(error, '质量保证设置保存失败。')
  } finally {
    savingQualityQASettings.value = false
  }
}

async function setProjectSyncForProject(enabled: boolean): Promise<boolean> {
  if (!project.value || projectSyncToggleLoading.value || !canManageProject.value) {
    return false
  }

  const disabled = !enabled
  if (disabled) {
    const accepted = await confirm({
      title: '关闭项目同步',
      message: '将关闭当前项目全部文件的重复句段自动同步，并清除由项目同步生成的译文。人工、AI 和记忆库译文不会被清除。',
      confirmText: '关闭并清除',
      cancelText: t('common.actions.cancel'),
      danger: true,
    })
    if (!accepted) {
      return false
    }
  }

  projectSyncToggleLoading.value = true
  pageError.value = ''
  try {
    const { data } = await http.patch<ProjectSyncDisableResult>(
      `/projects/${project.value.id}/segments/project-sync`,
      { disabled },
    )
    await loadProject()
    if (disabled) {
      if (data.updated_count > 0) {
        toast.success({
          title: '项目同步已关闭',
          message: `已处理 ${data.updated_count} 个句段，关闭 ${data.disabled_count} 个同步开关，清除 ${data.cleared_count} 条项目同步译文。`,
        })
      } else {
        toast.info('当前项目没有需要关闭的项目同步句段。')
      }
    } else if (data.updated_count > 0) {
      toast.success({
        title: '项目同步已开启',
        message: `已恢复 ${data.updated_count} 个句段的重复句段自动同步。`,
      })
    } else {
      toast.info('当前项目同步已处于开启状态。')
    }
    return true
  } catch (error) {
    pageError.value = getErrorMessage(error, disabled ? '关闭项目同步失败。' : '开启项目同步失败。')
    toast.error({
      title: disabled ? '关闭项目同步失败' : '开启项目同步失败',
      message: pageError.value,
    })
    return false
  } finally {
    projectSyncToggleLoading.value = false
  }
}

async function handleProjectSyncToggle(event: Event) {
  const input = event.target as HTMLInputElement | null
  if (!input) {
    return
  }

  await setProjectSyncForProject(input.checked)
  input.checked = projectSyncAllEnabled.value
  input.indeterminate = projectSyncMixed.value
}


function translationMemorySettingGroupKey(group: ProjectTranslationMemorySettingGroup) {
  return `${group.source_language}->${group.target_language}`
}

function getTranslationMemorySettingPairLabel(group: ProjectTranslationMemorySettingGroup) {
  return formatLanguagePair(group.source_language, group.target_language)
}

function normalizeTMMatchThreshold(value: unknown) {
  const numericValue = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numericValue)) {
    return 0.8
  }
  return Math.min(1, Math.max(0.5, Math.round(numericValue * 100) / 100))
}

function getGroupTMMatchThreshold(group: ProjectTranslationMemorySettingGroup) {
  if (group.files.length === 0) {
    return 0.8
  }
  const firstThreshold = normalizeTMMatchThreshold(group.files[0].tm_match_threshold)
  return group.files.every((file) => normalizeTMMatchThreshold(file.tm_match_threshold) === firstThreshold)
    ? firstThreshold
    : firstThreshold
}

function setGroupTMMatchThreshold(group: ProjectTranslationMemorySettingGroup, event: Event) {
  const threshold = normalizeTMMatchThreshold((event.target as HTMLInputElement).value)
  for (const file of group.files) {
    file.tm_match_threshold = threshold
  }
}

function setFileTMMatchThreshold(file: ProjectTranslationMemorySettingFile, event: Event) {
  file.tm_match_threshold = normalizeTMMatchThreshold((event.target as HTMLInputElement).value)
}

function tmCollectionRowKey(group: ProjectTranslationMemorySettingGroup, collectionId: string) {
  return `${translationMemorySettingGroupKey(group)}:${collectionId}`
}

function getTMCollectionBoundFiles(group: ProjectTranslationMemorySettingGroup, collectionId: string) {
  return group.files.filter((file) => file.collection_ids.includes(collectionId))
}

function getTMCollectionBoundSummary(group: ProjectTranslationMemorySettingGroup, collectionId: string) {
  const count = getTMCollectionBoundFiles(group, collectionId).length
  return `${count}/${group.files.length} 个文件`
}

function normalizeResourceSettingsSearchText(value: unknown) {
  return String(value ?? '').trim().toLowerCase()
}

function getResourceSettingsSearchKeywords(value: string) {
  return normalizeResourceSettingsSearchText(value).split(/\s+/).filter(Boolean)
}

function getTMCollectionSearchText(
  group: ProjectTranslationMemorySettingGroup,
  collection: ProjectTranslationMemorySettingCollection,
) {
  return [
    collection.name,
    collection.description,
    collection.entry_count,
    formatLanguagePair(collection.source_language, collection.target_language),
    getLanguageLabel(collection.source_language),
    getLanguageLabel(collection.target_language),
    isTMCollectionEnabled(group, collection.id) ? '已启用 enabled' : '未启用 disabled',
    isTMCollectionWritable(group, collection.id) ? '写入 writable' : '',
    getTMCollectionBoundSummary(group, collection.id),
  ].map(normalizeResourceSettingsSearchText).join(' ')
}

function getFilteredTMCollections(group: ProjectTranslationMemorySettingGroup) {
  const keywords = getResourceSettingsSearchKeywords(tmSettingsSearchQuery.value)
  if (keywords.length === 0) {
    return group.collections
  }
  return group.collections.filter((collection) => {
    const searchText = getTMCollectionSearchText(group, collection)
    return keywords.every((keyword) => searchText.includes(keyword))
  })
}

function getFilteredTMSettingsCollectionCount() {
  return translationMemorySettings.value?.groups.reduce(
    (total, group) => total + getFilteredTMCollections(group).length,
    0,
  ) ?? 0
}

function getTMSettingsCollectionCount() {
  return translationMemorySettings.value?.groups.reduce(
    (total, group) => total + group.collections.length,
    0,
  ) ?? 0
}

function isTMCollectionEnabled(group: ProjectTranslationMemorySettingGroup, collectionId: string) {
  return getTMCollectionBoundFiles(group, collectionId).length > 0
}

function sortTMCollectionsByEnabled(
  group: ProjectTranslationMemorySettingGroup,
  priorityCollectionId = '',
) {
  group.collections = group.collections
    .map((collection, index) => ({ collection, index }))
    .sort((left, right) => {
      const leftEnabled = isTMCollectionEnabled(group, left.collection.id)
      const rightEnabled = isTMCollectionEnabled(group, right.collection.id)
      if (leftEnabled !== rightEnabled) {
        return leftEnabled ? -1 : 1
      }
      if (priorityCollectionId && leftEnabled && rightEnabled) {
        if (left.collection.id === priorityCollectionId) return -1
        if (right.collection.id === priorityCollectionId) return 1
      }
      return left.index - right.index
    })
    .map(({ collection }) => collection)
}

function sortTranslationMemorySettings(settings: ProjectTranslationMemorySettingsResponse) {
  for (const group of settings.groups) {
    sortTMCollectionsByEnabled(group)
  }
}

function preserveTranslationMemorySettingsDisplayOrder(
  settings: ProjectTranslationMemorySettingsResponse,
  previousSettings: ProjectTranslationMemorySettingsResponse | null,
) {
  if (!previousSettings) {
    sortTranslationMemorySettings(settings)
    return
  }

  const previousGroupByKey = new Map(
    previousSettings.groups.map((group) => [translationMemorySettingGroupKey(group), group]),
  )
  for (const group of settings.groups) {
    const previousGroup = previousGroupByKey.get(translationMemorySettingGroupKey(group))
    if (!previousGroup) {
      sortTMCollectionsByEnabled(group)
      continue
    }
    const displayOrderById = new Map(previousGroup.collections.map((collection, index) => [collection.id, index]))
    group.collections = group.collections
      .map((collection, index) => ({ collection, index }))
      .sort((left, right) => {
        const leftOrder = displayOrderById.get(left.collection.id)
        const rightOrder = displayOrderById.get(right.collection.id)
        if (typeof leftOrder === 'number' && typeof rightOrder === 'number') {
          return leftOrder - rightOrder
        }
        if (typeof leftOrder === 'number') {
          return -1
        }
        if (typeof rightOrder === 'number') {
          return 1
        }
        return left.index - right.index
      })
      .map(({ collection }) => collection)
  }
}

function isTMCollectionWritable(group: ProjectTranslationMemorySettingGroup, collectionId: string) {
  return group.files.some((file) => file.collection_id === collectionId)
}

function toggleTMCollectionEnabled(
  group: ProjectTranslationMemorySettingGroup,
  collectionId: string,
  event: Event,
) {
  const checked = (event.target as HTMLInputElement).checked
  for (const file of group.files) {
    if (checked && !file.collection_ids.includes(collectionId)) {
      file.collection_ids = [...file.collection_ids, collectionId]
    }
    if (!checked) {
      file.collection_ids = file.collection_ids.filter((id) => id !== collectionId)
      if (file.collection_id === collectionId) {
        file.collection_id = file.collection_ids[0] || null
      }
    }
  }
  if (checked) {
    scheduleTMCollectionToTop(group, collectionId)
  }
}

function toggleTMCollectionWritable(
  group: ProjectTranslationMemorySettingGroup,
  collectionId: string,
  event: Event,
) {
  const checked = (event.target as HTMLInputElement).checked
  for (const file of group.files) {
    if (checked) {
      if (!file.collection_ids.includes(collectionId)) {
        file.collection_ids = [...file.collection_ids, collectionId]
      }
      file.collection_id = collectionId
    } else if (file.collection_id === collectionId) {
      file.collection_id = file.collection_ids.find((id) => id !== collectionId) || null
    }
  }
  if (checked) {
    scheduleTMCollectionToTop(group, collectionId)
  }
}

function toggleTMCollectionDetails(group: ProjectTranslationMemorySettingGroup, collectionId: string) {
  const key = tmCollectionRowKey(group, collectionId)
  expandedTMCollectionKey.value = expandedTMCollectionKey.value === key ? '' : key
}

function isTMCollectionDetailsOpen(group: ProjectTranslationMemorySettingGroup, collectionId: string) {
  return expandedTMCollectionKey.value === tmCollectionRowKey(group, collectionId)
}

function isFileTMCollectionBound(file: ProjectTranslationMemorySettingFile, collectionId: string) {
  return file.collection_ids.includes(collectionId)
}

function toggleFileTMCollection(
  file: ProjectTranslationMemorySettingFile,
  collectionId: string,
  event: Event,
) {
  const checked = (event.target as HTMLInputElement).checked
  if (checked && !file.collection_ids.includes(collectionId)) {
    file.collection_ids = [...file.collection_ids, collectionId]
  }
  if (!checked) {
    file.collection_ids = file.collection_ids.filter((id) => id !== collectionId)
    if (file.collection_id === collectionId) {
      file.collection_id = file.collection_ids[0] || null
    }
  }
  if (checked && !file.collection_id) {
    file.collection_id = collectionId
  }
  if (checked) {
    const group = translationMemorySettings.value?.groups.find((item) => (
      item.files.some((candidate) => candidate.id === file.id)
    ))
    if (group) {
      scheduleTMCollectionToTop(group, collectionId)
    }
  }
}

function setFilePrimaryTMCollection(file: ProjectTranslationMemorySettingFile, event: Event) {
  const collectionId = (event.target as HTMLSelectElement).value || null
  file.collection_id = collectionId
  if (collectionId && !file.collection_ids.includes(collectionId)) {
    file.collection_ids = [...file.collection_ids, collectionId]
  }
}

function isTMCollectionBoundForAll(group: ProjectTranslationMemorySettingGroup, collectionId: string) {
  return group.files.length > 0 && group.files.every((file) => file.collection_ids.includes(collectionId))
}

function setTMCollectionBindingForAll(
  group: ProjectTranslationMemorySettingGroup,
  collectionId: string,
  enabled: boolean,
) {
  for (const file of group.files) {
    if (enabled && !file.collection_ids.includes(collectionId)) {
      file.collection_ids = [...file.collection_ids, collectionId]
    }
    if (!enabled) {
      file.collection_ids = file.collection_ids.filter((id) => id !== collectionId)
      if (file.collection_id === collectionId) {
        file.collection_id = file.collection_ids[0] || null
      }
    }
    if (enabled && !file.collection_id) {
      file.collection_id = collectionId
    }
  }
  if (enabled) {
    scheduleTMCollectionToTop(group, collectionId)
  }
}

function toggleTMCollectionBindingForAll(
  group: ProjectTranslationMemorySettingGroup,
  collectionId: string,
) {
  setTMCollectionBindingForAll(
    group,
    collectionId,
    !isTMCollectionBoundForAll(group, collectionId),
  )
}

function toggleGroupTMCollection(
  group: ProjectTranslationMemorySettingGroup,
  collectionId: string,
  event: Event,
) {
  const checked = (event.target as HTMLInputElement).checked
  setTMCollectionBindingForAll(group, collectionId, checked)
}

function getGroupPrimaryTMCollectionId(group: ProjectTranslationMemorySettingGroup) {
  if (group.files.length === 0) {
    return ''
  }
  const firstCollectionId = group.files[0].collection_id || ''
  return group.files.every((file) => (file.collection_id || '') === firstCollectionId)
    ? firstCollectionId
    : ''
}

function setGroupPrimaryTMCollection(group: ProjectTranslationMemorySettingGroup, event: Event) {
  const collectionId = (event.target as HTMLSelectElement).value || null
  for (const file of group.files) {
    file.collection_id = collectionId
    if (collectionId && !file.collection_ids.includes(collectionId)) {
      file.collection_ids = [...file.collection_ids, collectionId]
    }
  }
  if (collectionId) {
    scheduleTMCollectionToTop(group, collectionId)
  }
}

function buildTranslationMemorySettingsPayload() {
  return {
    auto_tm_enabled: translationMemorySettings.value?.auto_tm_enabled ?? true,
    settings: (translationMemorySettings.value?.groups || []).map((group) => ({
      source_language: group.source_language,
      target_language: group.target_language,
      files: group.files.map((file) => ({
        file_record_id: file.id,
        collection_ids: file.collection_ids,
        primary_collection_id: file.collection_id,
        tm_match_threshold: normalizeTMMatchThreshold(file.tm_match_threshold),
      })),
    })),
  }
}

async function saveProjectTranslationMemorySettings(showSuccessToast = true) {
  if (!project.value || savingTranslationMemorySettings.value || !canManageProject.value) {
    return
  }
  savingTranslationMemorySettings.value = true
  translationMemorySettingsError.value = ''
  try {
    const { data } = await http.patch<ProjectTranslationMemorySettingsResponse>(
      `/projects/${project.value.id}/translation-memory-settings`,
      buildTranslationMemorySettingsPayload(),
    )
    preserveTranslationMemorySettingsDisplayOrder(data, translationMemorySettings.value)
    translationMemorySettings.value = data
    if (showSuccessToast) {
      toast.show({
        tone: 'success',
        title: '记忆库设置已保存',
        message: data.initial_match_queued_count
          ? `已将 ${data.initial_match_queued_count} 个文件加入后台匹配队列。`
          : data.initial_match_updated_count
          ? `已同步更新 ${data.initial_match_updated_count} 个句段。`
          : '',
      })
    }
    await loadProject()
  } catch (error) {
    translationMemorySettingsError.value = getErrorMessage(error, '记忆库设置保存失败。')
    toast.show({
      tone: 'error',
      title: '记忆库设置保存失败',
      message: translationMemorySettingsError.value,
    })
    if (!showSuccessToast) {
      throw error
    }
  } finally {
    savingTranslationMemorySettings.value = false
  }
}

function moveTMCollectionToTop(group: ProjectTranslationMemorySettingGroup, collectionId: string) {
  const index = group.collections.findIndex((collection) => collection.id === collectionId)
  if (index <= 0) {
    return
  }
  const [collection] = group.collections.splice(index, 1)
  group.collections.unshift(collection)
}

function moveTermBaseRowToTop(group: ProjectTermBaseSettingGroup, termBaseId: string) {
  const index = group.term_bases.findIndex((row) => row.id === termBaseId)
  if (index <= 0) {
    return
  }
  const [row] = group.term_bases.splice(index, 1)
  group.term_bases.unshift(row)
  normalizeTermBaseQAPriority(group)
}

function flushPendingResourceTopMoves() {
  resourceTopMoveTimer = null
  const tmSettings = translationMemorySettings.value
  if (tmSettings && pendingTMCollectionTopMoves.size > 0) {
    const groupByKey = new Map(tmSettings.groups.map((group) => [translationMemorySettingGroupKey(group), group]))
    for (const [groupKey, collectionIds] of pendingTMCollectionTopMoves) {
      const group = groupByKey.get(groupKey)
      if (!group) {
        continue
      }
      for (const collectionId of collectionIds) {
        moveTMCollectionToTop(group, collectionId)
      }
    }
  }

  const termSettings = termBaseSettings.value
  if (termSettings && pendingTermBaseTopMoves.size > 0) {
    const groupByKey = new Map(termSettings.groups.map((group) => [termBaseSettingGroupKey(group), group]))
    for (const [groupKey, termBaseIds] of pendingTermBaseTopMoves) {
      const group = groupByKey.get(groupKey)
      if (!group) {
        continue
      }
      for (const termBaseId of termBaseIds) {
        moveTermBaseRowToTop(group, termBaseId)
      }
    }
  }

  pendingTMCollectionTopMoves.clear()
  pendingTermBaseTopMoves.clear()
}

function scheduleResourceTopMove() {
  if (resourceTopMoveTimer !== null) {
    window.clearTimeout(resourceTopMoveTimer)
  }
  resourceTopMoveTimer = window.setTimeout(flushPendingResourceTopMoves, 120)
}

function clearPendingResourceTopMoves() {
  if (resourceTopMoveTimer !== null) {
    window.clearTimeout(resourceTopMoveTimer)
    resourceTopMoveTimer = null
  }
  pendingTMCollectionTopMoves.clear()
  pendingTermBaseTopMoves.clear()
}

function scheduleTMCollectionToTop(group: ProjectTranslationMemorySettingGroup, collectionId: string) {
  const groupKey = translationMemorySettingGroupKey(group)
  const pendingIds = pendingTMCollectionTopMoves.get(groupKey) ?? new Set<string>()
  pendingIds.add(collectionId)
  pendingTMCollectionTopMoves.set(groupKey, pendingIds)
  scheduleResourceTopMove()
}

function scheduleTermBaseToTop(group: ProjectTermBaseSettingGroup, termBaseId: string) {
  const groupKey = termBaseSettingGroupKey(group)
  const pendingIds = pendingTermBaseTopMoves.get(groupKey) ?? new Set<string>()
  pendingIds.add(termBaseId)
  pendingTermBaseTopMoves.set(groupKey, pendingIds)
  scheduleResourceTopMove()
}

function findProjectResourceCreateTMGroup() {
  return translationMemorySettings.value?.groups.find((group) => (
    translationMemorySettingGroupKey(group) === projectResourceCreateGroupKey.value
  )) ?? null
}

function findProjectResourceCreateTermGroup() {
  return termBaseSettings.value?.groups.find((group) => (
    termBaseSettingGroupKey(group) === projectResourceCreateGroupKey.value
  )) ?? null
}

function openProjectResourceCreateDialog(
  kind: ProjectResourceCreateKind,
  group: ProjectTranslationMemorySettingGroup | ProjectTermBaseSettingGroup,
) {
  if (!project.value || projectResourceCreateSubmitting.value) {
    return
  }
  const pairLabel = formatLanguagePair(group.source_language, group.target_language)
  const resourceLabel = kind === 'tm' ? '记忆库' : '术语库'
  projectResourceCreateKind.value = kind
  projectResourceCreateGroupKey.value = kind === 'tm'
    ? translationMemorySettingGroupKey(group as ProjectTranslationMemorySettingGroup)
    : termBaseSettingGroupKey(group as ProjectTermBaseSettingGroup)
  projectResourceCreateForm.name = `${project.value.name || project.value.filename || '项目'} ${pairLabel} ${resourceLabel}`
  projectResourceCreateForm.description = ''
  projectResourceCreateForm.sourceLanguage = group.source_language
  projectResourceCreateForm.targetLanguage = group.target_language
  projectResourceCreateError.value = ''
  showProjectResourceCreateDialog.value = true
}

function closeProjectResourceCreateDialog() {
  if (projectResourceCreateSubmitting.value) {
    return
  }
  showProjectResourceCreateDialog.value = false
  projectResourceCreateError.value = ''
}

function getProjectResourceLanguageSearchText(resource: ProjectResourceLanguageAsset) {
  return [
    resource.name,
    resource.description,
    resource.entry_count,
    formatLanguagePair(resource.source_language, resource.target_language),
    getLanguageLabel(resource.source_language),
    getLanguageLabel(resource.target_language),
    isProjectResourceLanguageTargetMatch(resource) ? '当前语言对 已匹配 matched' : '可复制 language pair mismatch',
  ].map(normalizeResourceSettingsSearchText).join(' ')
}

function isProjectResourceLanguageTargetMatch(resource: ProjectResourceLanguageAsset) {
  return resource.source_language === projectResourceLanguageTarget.sourceLanguage
    && resource.target_language === projectResourceLanguageTarget.targetLanguage
}

function getProjectResourceLanguageEndpoint(resourceId: string) {
  return projectResourceLanguageKind.value === 'tm'
    ? `/translation-memory/collections/${resourceId}/copy-language-pair`
    : `/term-bases/${resourceId}/copy-language-pair`
}

function buildProjectResourceLanguageCopyName(resource: ProjectResourceLanguageAsset) {
  return `${resource.name} ${projectResourceLanguageTarget.pairLabel}`
}

function findProjectResourceLanguageTMGroup() {
  return translationMemorySettings.value?.groups.find((group) => (
    group.source_language === projectResourceLanguageTarget.sourceLanguage
    && group.target_language === projectResourceLanguageTarget.targetLanguage
  )) ?? null
}

function findProjectResourceLanguageTermGroup() {
  return termBaseSettings.value?.groups.find((group) => (
    group.source_language === projectResourceLanguageTarget.sourceLanguage
    && group.target_language === projectResourceLanguageTarget.targetLanguage
  )) ?? null
}

async function loadProjectResourceLanguageResources() {
  projectResourceLanguageLoading.value = true
  projectResourceLanguageError.value = ''
  try {
    const endpoint = projectResourceLanguageKind.value === 'tm'
      ? '/translation-memory/collections'
      : '/term-bases'
    const { data } = await http.get<ProjectResourceLanguageAsset[]>(endpoint)
    projectResourceLanguageResources.value = data
    const selectedStillExists = data.some((resource) => resource.id === projectResourceLanguageSelectedId.value)
    if (!selectedStillExists) {
      projectResourceLanguageSelectedId.value = data.find((resource) => !isProjectResourceLanguageTargetMatch(resource))?.id
        ?? ''
    }
  } catch (error) {
    projectResourceLanguageResources.value = []
    projectResourceLanguageSelectedId.value = ''
    projectResourceLanguageError.value = getErrorMessage(error, `${projectResourceLanguageAssetLabel.value}列表加载失败。`)
  } finally {
    projectResourceLanguageLoading.value = false
  }
}

function openProjectResourceLanguageDialog(
  kind: ProjectResourceCreateKind,
  group: ProjectTranslationMemorySettingGroup | ProjectTermBaseSettingGroup,
) {
  if (projectResourceLanguageSubmitting.value) {
    return
  }
  projectResourceLanguageKind.value = kind
  projectResourceLanguageTarget.sourceLanguage = group.source_language
  projectResourceLanguageTarget.targetLanguage = group.target_language
  projectResourceLanguageTarget.pairLabel = formatLanguagePair(group.source_language, group.target_language)
  projectResourceLanguageSearchQuery.value = ''
  projectResourceLanguageSelectedId.value = ''
  projectResourceLanguageError.value = ''
  showProjectResourceLanguageDialog.value = true
  void loadProjectResourceLanguageResources()
}

function closeProjectResourceLanguageDialog() {
  if (projectResourceLanguageSubmitting.value || projectResourceLanguageLoading.value) {
    return
  }
  showProjectResourceLanguageDialog.value = false
  projectResourceLanguageError.value = ''
}

async function submitProjectResourceLanguageDialog() {
  const resource = selectedProjectResourceLanguageResource.value
  if (!resource) {
    projectResourceLanguageError.value = `请先选择要复制的${projectResourceLanguageAssetLabel.value}。`
    return
  }
  if (isProjectResourceLanguageTargetMatch(resource)) {
    projectResourceLanguageError.value = `该${projectResourceLanguageAssetLabel.value}已经是当前分组语言对，可直接在列表中启用。`
    return
  }
  if (!projectResourceLanguageTarget.sourceLanguage || !projectResourceLanguageTarget.targetLanguage) {
    projectResourceLanguageError.value = '当前项目分组缺少语言对，无法复制。'
    return
  }

  const accepted = await confirm({
    title: projectResourceLanguageTitle.value,
    message: `将从“${resource.name}”复制一个 ${projectResourceLanguageTarget.pairLabel} 的新${projectResourceLanguageAssetLabel.value}，并复制其中 ${resource.entry_count} ${projectResourceLanguageEntryLabel.value}。原库仍保留 ${formatLanguagePair(resource.source_language, resource.target_language)}，不会影响其它项目。`,
    confirmText: '复制并启用',
  })
  if (!accepted) {
    return
  }

  projectResourceLanguageSubmitting.value = true
  projectResourceLanguageError.value = ''
  try {
    const { data } = await http.post<ProjectResourceLanguageAsset>(getProjectResourceLanguageEndpoint(resource.id), {
      name: buildProjectResourceLanguageCopyName(resource),
      description: resource.description || null,
      source_language: projectResourceLanguageTarget.sourceLanguage,
      target_language: projectResourceLanguageTarget.targetLanguage,
    })
    if (projectResourceLanguageKind.value === 'tm') {
      await loadProjectTranslationMemorySettings()
      const nextGroup = findProjectResourceLanguageTMGroup()
      if (nextGroup?.collections.some((collection) => collection.id === data.id)) {
        for (const file of nextGroup.files) {
          if (!file.collection_ids.includes(data.id)) {
            file.collection_ids = [...file.collection_ids, data.id]
          }
          if (!file.collection_id) {
            file.collection_id = data.id
          }
        }
        moveTMCollectionToTop(nextGroup, data.id)
        tmSettingsSearchQuery.value = ''
        await saveProjectTranslationMemorySettings(false)
      }
    } else {
      await loadProjectTermBaseSettings()
      const nextGroup = findProjectResourceLanguageTermGroup()
      const createdRow = nextGroup?.term_bases.find((row) => row.id === data.id)
      if (nextGroup && createdRow) {
        createdRow.enabled = true
        createdRow.writable = true
        createdRow.qa = false
        moveTermBaseRowToTop(nextGroup, data.id)
        termBaseSettingsSearchQuery.value = ''
        await saveProjectTermBaseSettings(false)
      }
    }
    toast.success(`已复制并启用${projectResourceLanguageAssetLabel.value}：${data.name || resource.name}`)
    showProjectResourceLanguageDialog.value = false
  } catch (error) {
    projectResourceLanguageError.value = getErrorMessage(error, `${projectResourceLanguageAssetLabel.value}复制失败。`)
    toast.show({
      tone: 'error',
      title: `${projectResourceLanguageAssetLabel.value}复制失败`,
      message: projectResourceLanguageError.value,
    })
  } finally {
    projectResourceLanguageSubmitting.value = false
  }
}

function createTranslationMemoryForGroup(group: ProjectTranslationMemorySettingGroup) {
  openProjectResourceCreateDialog('tm', group)
}

async function submitProjectResourceCreateDialog() {
  const name = projectResourceCreateForm.name.trim()
  if (!name) {
    projectResourceCreateError.value = `${projectResourceCreateNameLabel.value}不能为空。`
    return
  }

  const sourceLanguage = projectResourceCreateForm.sourceLanguage
  const targetLanguage = projectResourceCreateForm.targetLanguage
  if (!sourceLanguage || !targetLanguage) {
    projectResourceCreateError.value = '缺少当前语言对，无法创建资源库。'
    return
  }

  projectResourceCreateSubmitting.value = true
  projectResourceCreateError.value = ''
  if (projectResourceCreateKind.value === 'tm') {
    creatingTranslationMemoryPair.value = projectResourceCreateGroupKey.value
  } else {
    creatingTermBasePair.value = projectResourceCreateGroupKey.value
  }

  try {
    if (projectResourceCreateKind.value === 'tm') {
      const { data } = await http.post<{ id: string; name?: string }>('/translation-memory/collections', {
        name,
        description: projectResourceCreateForm.description.trim() || null,
        source_language: sourceLanguage,
        target_language: targetLanguage,
      })
      await loadProjectTranslationMemorySettings()
      const nextGroup = findProjectResourceCreateTMGroup()
      if (nextGroup?.collections.some((collection) => collection.id === data.id)) {
        for (const file of nextGroup.files) {
          if (!file.collection_ids.includes(data.id)) {
            file.collection_ids = [...file.collection_ids, data.id]
          }
          if (!file.collection_id) {
            file.collection_id = data.id
          }
        }
        moveTMCollectionToTop(nextGroup, data.id)
        tmSettingsSearchQuery.value = ''
        await saveProjectTranslationMemorySettings(false)
      }
      toast.success(`已创建并启用记忆库：${data.name || name}`)
    } else {
      const { data } = await http.post<{ id: string; name?: string }>('/term-bases', {
        name,
        description: projectResourceCreateForm.description.trim() || null,
        source_language: sourceLanguage,
        target_language: targetLanguage,
      })
      await loadProjectTermBaseSettings()
      const nextGroup = findProjectResourceCreateTermGroup()
      const createdRow = nextGroup?.term_bases.find((row) => row.id === data.id)
      if (nextGroup && createdRow) {
        createdRow.enabled = true
        createdRow.writable = true
        createdRow.qa = false
        moveTermBaseRowToTop(nextGroup, data.id)
        termBaseSettingsSearchQuery.value = ''
        await saveProjectTermBaseSettings(false)
      }
      toast.success(`已创建并启用术语库：${data.name || name}`)
    }
    showProjectResourceCreateDialog.value = false
  } catch (error) {
    projectResourceCreateError.value = getErrorMessage(
      error,
      projectResourceCreateKind.value === 'tm' ? '记忆库创建失败。' : '术语库创建失败。',
    )
    toast.show({
      tone: 'error',
      title: projectResourceCreateKind.value === 'tm' ? '记忆库创建失败' : '术语库创建失败',
      message: projectResourceCreateError.value,
    })
  } finally {
    projectResourceCreateSubmitting.value = false
    creatingTranslationMemoryPair.value = ''
    creatingTermBasePair.value = ''
  }
}

function openTMIncrementalImport(
  group: ProjectTranslationMemorySettingGroup,
  collection: ProjectTranslationMemorySettingCollection,
) {
  tmImportDialogContext.value = {
    collectionId: collection.id,
    collectionName: collection.name,
    sourceLanguage: collection.source_language || group.source_language,
    targetLanguage: collection.target_language || group.target_language,
  }
  showTMImportDialog.value = true
}

async function handleTMIncrementalImported() {
  showTMImportDialog.value = false
  toast.show({
    tone: 'success',
    title: '增量导入完成',
    message: '翻译记忆库已更新，正在刷新项目设置。',
  })
  await loadProjectTranslationMemorySettings()
  await loadProject()
}

function termBaseSettingGroupKey(group: ProjectTermBaseSettingGroup) {
  return `${group.source_language}->${group.target_language}`
}

function getTermBaseSettingPairLabel(group: ProjectTermBaseSettingGroup) {
  return formatLanguagePair(group.source_language, group.target_language)
}

function getTermBaseSearchText(
  group: ProjectTermBaseSettingGroup,
  row: ProjectTermBaseSettingRow,
) {
  return [
    row.name,
    row.description,
    row.entry_count,
    getTermBaseSettingPairLabel(group),
    getLanguageLabel(row.source_language),
    getLanguageLabel(row.target_language),
    row.enabled ? '已启用 enabled' : '未启用 disabled',
    row.writable ? '写入 writable' : '',
    row.qa ? 'qa 质量检查' : '',
  ].map(normalizeResourceSettingsSearchText).join(' ')
}

function getFilteredTermBaseRows(group: ProjectTermBaseSettingGroup) {
  const keywords = getResourceSettingsSearchKeywords(termBaseSettingsSearchQuery.value)
  if (keywords.length === 0) {
    return group.term_bases
  }
  return group.term_bases.filter((row) => {
    const searchText = getTermBaseSearchText(group, row)
    return keywords.every((keyword) => searchText.includes(keyword))
  })
}

function getFilteredTermBaseSettingsRowCount() {
  return termBaseSettings.value?.groups.reduce(
    (total, group) => total + getFilteredTermBaseRows(group).length,
    0,
  ) ?? 0
}

function getTermBaseSettingsRowCount() {
  return termBaseSettings.value?.groups.reduce(
    (total, group) => total + group.term_bases.length,
    0,
  ) ?? 0
}

function sortTermBaseSettings(settings: ProjectTermBaseSettingsResponse) {
  for (const group of settings.groups) {
    sortTermBaseSettingGroup(group)
  }
}

function sortTermBaseSettingGroup(group: ProjectTermBaseSettingGroup) {
  group.term_bases = [...group.term_bases].sort((left, right) => {
    if (left.qa !== right.qa) {
      return left.qa ? -1 : 1
    }
    if (left.qa && right.qa) {
      return (left.qa_priority || 9999) - (right.qa_priority || 9999)
    }
    if (left.enabled !== right.enabled) {
      return left.enabled ? -1 : 1
    }
    return left.name.localeCompare(right.name)
  })
  normalizeTermBaseQAPriority(group)
}

function getOrderedTermBaseQARows(group: ProjectTermBaseSettingGroup) {
  const displayOrderById = new Map(group.term_bases.map((row, index) => [row.id, index]))
  return group.term_bases
    .filter((row) => row.qa)
    .sort((left, right) => {
      const leftPriority = typeof left.qa_priority === 'number' ? left.qa_priority : Number.MAX_SAFE_INTEGER
      const rightPriority = typeof right.qa_priority === 'number' ? right.qa_priority : Number.MAX_SAFE_INTEGER
      if (leftPriority !== rightPriority) {
        return leftPriority - rightPriority
      }
      return (displayOrderById.get(left.id) ?? 0) - (displayOrderById.get(right.id) ?? 0)
    })
}

function normalizeTermBaseQAPriority(group: ProjectTermBaseSettingGroup) {
  const orderedQARows = getOrderedTermBaseQARows(group)
  for (const row of group.term_bases) {
    if (!row.qa) {
      row.qa_priority = null
    }
  }
  let priority = 1
  for (const row of orderedQARows) {
    row.qa_priority = priority
    priority += 1
  }
}

function preserveTermBaseSettingsDisplayOrder(
  settings: ProjectTermBaseSettingsResponse,
  previousSettings: ProjectTermBaseSettingsResponse | null,
) {
  if (!previousSettings) {
    sortTermBaseSettings(settings)
    return
  }

  const previousGroupByKey = new Map(
    previousSettings.groups.map((group) => [termBaseSettingGroupKey(group), group]),
  )
  for (const group of settings.groups) {
    const previousGroup = previousGroupByKey.get(termBaseSettingGroupKey(group))
    if (!previousGroup) {
      sortTermBaseSettingGroup(group)
      continue
    }
    const displayOrderById = new Map(previousGroup.term_bases.map((row, index) => [row.id, index]))
    group.term_bases = group.term_bases
      .map((row, index) => ({ row, index }))
      .sort((left, right) => {
        const leftOrder = displayOrderById.get(left.row.id)
        const rightOrder = displayOrderById.get(right.row.id)
        if (typeof leftOrder === 'number' && typeof rightOrder === 'number') {
          return leftOrder - rightOrder
        }
        if (typeof leftOrder === 'number') {
          return -1
        }
        if (typeof rightOrder === 'number') {
          return 1
        }
        return left.index - right.index
      })
      .map(({ row }) => row)
    normalizeTermBaseQAPriority(group)
  }
}

function toggleTermBaseSetting(
  row: ProjectTermBaseSettingRow,
  group: ProjectTermBaseSettingGroup,
  field: 'enabled' | 'writable' | 'qa',
  event: Event,
) {
  const checked = (event.target as HTMLInputElement).checked
  row[field] = checked
  if ((field === 'writable' || field === 'qa') && checked) {
    row.enabled = true
  }
  if (field === 'enabled' && !checked) {
    row.writable = false
    row.qa = false
  }
  if (field === 'qa' && checked) {
    row.enabled = true
  }
  normalizeTermBaseQAPriority(group)
  if (checked) {
    scheduleTermBaseToTop(group, row.id)
  }
}

function moveTermBaseQAPriority(
  group: ProjectTermBaseSettingGroup,
  row: ProjectTermBaseSettingRow,
  direction: -1 | 1,
) {
  if (!row.qa) {
    return
  }
  const orderedQARows = getOrderedTermBaseQARows(group)
  const currentIndex = orderedQARows.findIndex((item) => item.id === row.id)
  if (currentIndex < 0) {
    return
  }
  const targetIndex = currentIndex + direction
  if (targetIndex < 0 || targetIndex >= orderedQARows.length) {
    return
  }
  const [movedRow] = orderedQARows.splice(currentIndex, 1)
  orderedQARows.splice(targetIndex, 0, movedRow)
  orderedQARows.forEach((item, index) => {
    item.qa_priority = index + 1
  })
  normalizeTermBaseQAPriority(group)
}

function openTermIncrementalImport(
  group: ProjectTermBaseSettingGroup,
  row: ProjectTermBaseSettingRow,
) {
  termImportDialogContext.value = {
    termBaseId: row.id,
    termBaseName: row.name,
    sourceLanguage: row.source_language || group.source_language,
    targetLanguage: row.target_language || group.target_language,
  }
  showTermImportDialog.value = true
}

async function handleTermIncrementalImported() {
  showTermImportDialog.value = false
  toast.show({
    tone: 'success',
    title: '增量导入完成',
    message: '术语库已更新，正在刷新项目设置。',
  })
  await loadProjectTermBaseSettings()
  await loadProject()
}

function buildTermBaseSettingsPayload() {
  return {
    settings: (termBaseSettings.value?.groups || []).map((group) => ({
      source_language: group.source_language,
      target_language: group.target_language,
      enabled_term_base_ids: group.term_bases.filter((row) => row.enabled).map((row) => row.id),
      writable_term_base_ids: group.term_bases.filter((row) => row.enabled && row.writable).map((row) => row.id),
      qa_term_base_ids: getOrderedTermBaseQARows(group).filter((row) => row.enabled).map((row) => row.id),
    })),
  }
}

async function saveProjectTermBaseSettings(showSuccessToast = true) {
  if (!project.value || savingTermBaseSettings.value || !canManageProject.value) {
    return
  }
  savingTermBaseSettings.value = true
  termBaseSettingsError.value = ''
  try {
    const { data } = await http.patch<ProjectTermBaseSettingsResponse>(
      `/projects/${project.value.id}/term-base-settings`,
      buildTermBaseSettingsPayload(),
    )
    preserveTermBaseSettingsDisplayOrder(data, termBaseSettings.value)
    termBaseSettings.value = data
    if (showSuccessToast) {
      toast.show({
        tone: 'success',
        title: '术语库设置已保存',
        message: '',
      })
    }
    await loadProject()
  } catch (error) {
    termBaseSettingsError.value = getErrorMessage(error, '术语库设置保存失败。')
    toast.show({
      tone: 'error',
      title: '术语库设置保存失败',
      message: termBaseSettingsError.value,
    })
    if (!showSuccessToast) {
      throw error
    }
  } finally {
    savingTermBaseSettings.value = false
  }
}

function createTermBaseForGroup(group: ProjectTermBaseSettingGroup) {
  openProjectResourceCreateDialog('term', group)
}

async function generateProjectTermQAReport() {
  if (!project.value || generatingTermQAReport.value) {
    return
  }
  generatingTermQAReport.value = true
  try {
    if (termBaseSettings.value && !savingTermBaseSettings.value) {
      await saveProjectTermBaseSettings(false)
    }
    const { data } = await http.post<TermQAReport>(`/projects/${project.value.id}/term-qa-reports`, {
      file_ids: [],
    })
    termQAReport.value = data
    toast.show({
      tone: data.issue_count > 0 ? 'warn' : 'success',
      title: '术语QA报告已生成',
      message: `发现 ${data.issue_count} 条术语问题。`,
    })
  } catch (error) {
    toast.show({
      tone: 'error',
      title: '术语QA报告生成失败',
      message: getErrorMessage(error, '术语QA报告生成失败。'),
    })
  } finally {
    generatingTermQAReport.value = false
  }
}

async function downloadTermQAReport(report: TermQAReport | null) {
  if (!report || downloadingTermQAReport.value) {
    return
  }
  downloadingTermQAReport.value = true
  try {
    const response = await http.get(`/term-qa-reports/${report.id}/export-xlsx`, {
      responseType: 'blob',
    })
    downloadBlob(
      response.data,
      resolveDownloadFilename(response.headers['content-disposition'], `term-qa-report-${report.id}.xlsx`),
    )
  } catch (error) {
    toast.show({
      tone: 'error',
      title: '术语QA报告导出失败',
      message: getErrorMessage(error, '术语QA报告导出失败。'),
    })
  } finally {
    downloadingTermQAReport.value = false
  }
}

async function loadUploadCapabilities() {
  loadingUploadCapabilities.value = true
  try {
    const { data } = await http.get<UploadCapabilitiesResponse>('/file-records/upload-capabilities')
    uploadCapabilities.value = data.formats
    uploadLimits.value = {
      max_files_per_batch: data.limits?.max_files_per_batch ?? 50,
      max_total_size_mb: data.limits?.max_total_size_mb ?? 500,
      max_expanded_files: data.limits?.max_expanded_files ?? 100,
    }
    uploadFileAccept.value = data.accept || supportedTaskFileAccept
  } catch (error) {
    console.error('Failed to load upload capabilities:', error)
    uploadCapabilities.value = []
    uploadFileAccept.value = supportedTaskFileAccept
  } finally {
    loadingUploadCapabilities.value = false
  }
}

async function saveProjectSettings() {
  if (!project.value || savingSettings.value || !canManageProject.value) {
    return
  }

  const name = settingsForm.name.trim()
  if (!name) {
    settingsError.value = t('projectDetail.settings.nameRequired')
    return
  }

  savingSettings.value = true
  settingsError.value = ''
  try {
    const { data } = await http.patch<Partial<ProjectDetail>>(`/projects/${project.value.id}`, {
      name,
      deadline: settingsForm.deadline || null,
      access_level: settingsForm.access_level,
    })
    const updatedProject: ProjectDetail = {
      ...project.value,
      name: data.name ?? name,
      filename: data.filename ?? data.name ?? name,
      deadline: data.deadline ?? (settingsForm.deadline || null),
      access_level: data.access_level ?? settingsForm.access_level,
      updated_at: data.updated_at ?? project.value.updated_at,
    }
    project.value = updatedProject
    syncSettingsForm(updatedProject)
    toast.show({
      tone: 'success',
      title: t('projectDetail.settings.basicSaved'),
      message: '',
    })
  } catch (error) {
    settingsError.value = getErrorMessage(error, t('projectDetail.settings.basicSaveFailed'))
    toast.show({
      tone: 'error',
      title: t('projectDetail.settings.basicSaveFailed'),
      message: settingsError.value,
    })
  } finally {
    savingSettings.value = false
  }
}

async function saveGuidelines() {
  if (!project.value || savingGuidelines.value || !canManageProject.value) {
    return
  }
  savingGuidelines.value = true
  try {
    await http.patch(`/projects/${project.value.id}`, {
      translation_guidelines: guidelinesText.value,
    })
    project.value.translation_guidelines = guidelinesText.value
    toast.show({
      tone: 'success',
      title: t('projectDetail.settings.guidelinesSaved'),
      message: '',
    })
  } catch (error) {
    toast.show({
      tone: 'error',
      title: t('projectDetail.settings.guidelinesSaveFailed'),
      message: getErrorMessage(error, ''),
    })
  } finally {
    savingGuidelines.value = false
  }
}

async function detectSourceLanguage() {
  if (!canUploadProjectFiles.value) {
    return
  }

  if (isProjectLanguagePairBound.value) {
    languageDetectTone.value = 'info'
    languageDetectMessage.value = uploadLanguageBoundMessage.value
    return
  }

  if (selectedFiles.value.length === 0) {
    languageDetectTone.value = 'warning'
    languageDetectMessage.value = '请先选择要识别的文件。'
    return
  }

  detectingLanguage.value = true
  languageDetectTone.value = 'info'
  languageDetectMessage.value = '正在读取文件内容并识别源语言...'

  try {
    const formData = new FormData()
    formData.append('file', selectedFiles.value[0])

    const { data } = await http.post<LanguageDetectResponse>(
      `/projects/${props.id}/detect-source-language`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )

    if (data.language) {
      const matchesTargetLanguage = uploadTargetLanguages.value.includes(data.language)
      uploadSourceLanguage.value = data.language
      if (matchesTargetLanguage) {
        uploadTargetLanguages.value = uploadTargetLanguages.value.filter((code) => code !== data.language)
      }
      languageDetectTone.value = 'success'
      const confidence = data.confidence > 0 ? `，置信度 ${Math.round(data.confidence * 100)}%` : ''
      const nextStep = matchesTargetLanguage ? '请重新选择目标语言。' : '可手动修改。'
      languageDetectMessage.value = `已识别为 ${data.label || data.language}${confidence}，${nextStep}`
      return
    }

    languageDetectTone.value = data.supported ? 'warning' : 'error'
    languageDetectMessage.value = data.message || '未能识别源语言，请手动选择。'
  } catch (error) {
    languageDetectTone.value = 'error'
    languageDetectMessage.value = getErrorMessage(error, '识别源语言失败，请手动选择。')
  } finally {
    detectingLanguage.value = false
  }
}

async function uploadSourceDocument() {
  if (!canUploadProjectFiles.value) {
    return
  }

  if (selectedFiles.value.length === 0) {
    uploadMessage.value = t('projectDetail.errors.selectFile')
    return
  }

  const resolvedSourceLanguage = projectBoundLanguagePair.value?.source || uploadSourceLanguage.value
  const resolvedTargetLanguages = projectBoundLanguagePair.value?.target
    ? [projectBoundLanguagePair.value.target]
    : uploadTargetLanguages.value
  if (isProjectLanguagePairBound.value) {
    uploadSourceLanguage.value = resolvedSourceLanguage
    uploadTargetLanguages.value = [...resolvedTargetLanguages]
  }

  if (!resolvedSourceLanguage || resolvedTargetLanguages.length === 0) {
    uploadMessage.value = t('projectDetail.errors.selectLanguagePair')
    return
  }

  if (resolvedTargetLanguages.includes(resolvedSourceLanguage)) {
    uploadMessage.value = t('projectList.errors.sameLanguage')
    return
  }

  const validationError = validateSelectedUploadFiles(selectedFiles.value)
  if (validationError) {
    uploadMessage.value = validationError
    return
  }

  if (uploadGenerationValidationError.value) {
    uploadMessage.value = uploadGenerationValidationError.value
    return
  }

  uploadMessage.value = ''
  pageError.value = ''
  uploading.value = true
  uploadPercent.value = 0

  try {
    const formData = new FormData()
    selectedFiles.value.forEach((file) => {
      formData.append('files', file)
    })
    formData.append('threshold', '0.6')
    formData.append('source_language', resolvedSourceLanguage)
    resolvedTargetLanguages.forEach((language) => {
      formData.append('target_languages', language)
    })
    formData.append('document_parse_mode', documentParseMode.value)
    formData.append('document_parse_options', JSON.stringify(documentParseOptions.value))

    const { data } = await http.post<unknown | ImportTaskAccepted>(`/projects/${props.id}/source-document`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        uploadPercent.value = total > 0 ? Math.min(40, Math.round((loaded / total) * 40)) : 0
      },
    })
    if (isImportTaskAccepted(data)) {
      await waitForImportTask(data.task_id, (status) => {
        uploadPercent.value = Math.min(100, 40 + Math.round(status.progress * 0.6))
      })
    }

    await loadProject()
    showUploadModal.value = false
    resetUploadForm()
    selectedFileIds.value = new Set<string>()
    toast.success(t('projectDetail.messages.uploaded'))
  } catch (error) {
    uploadMessage.value = getErrorMessage(error, t('projectDetail.errors.upload'))
  } finally {
    uploading.value = false
    uploadPercent.value = 0
  }
}

function clearExportPollTimer() {
  if (exportPollTimer !== null) {
    window.clearTimeout(exportPollTimer)
    exportPollTimer = null
  }
}

function waitForExportPoll(ms: number) {
  clearExportPollTimer()
  return new Promise<void>((resolve) => {
    exportPollTimer = window.setTimeout(() => {
      exportPollTimer = null
      resolve()
    }, ms)
  })
}

function isProjectFileExporting(row: ProjectRow | null, exportType = '') {
  if (!row || exportingFileId.value !== String(row.id)) {
    return false
  }
  return !exportType || exportingFileType.value === exportType
}

function getProjectFileExportLabel(row: ProjectRow | null, exportType = 'original') {
  if (!isProjectFileExporting(row, exportType)) {
    return exportType === 'source'
      ? t('projectDetail.files.actions.exportSource')
      : t('projectDetail.files.actions.exportTarget')
  }
  return `导出中 ${exportFileProgress.value}%`
}

function getProjectFileExportFallbackName(filename: string, exportType: string) {
  return exportType === 'source' ? filename : buildTranslatedTaskFilename(filename)
}

function getProjectFileExportSuccessMessage(exportType: string, count: number) {
  if (count > 1) {
    return exportType === 'source'
      ? `已开始下载 ${count} 个源文件。`
      : `已开始下载 ${count} 个导出文件。`
  }
  return exportType === 'source'
    ? '源文件已开始下载。'
    : '导出完成，文件已开始下载。'
}

async function waitForFileExportTask(task: FileExportTask) {
  let currentTask = task
  while (true) {
    exportFileProgress.value = currentTask.progress
    exportFileMessage.value = currentTask.message || `导出处理中：${currentTask.progress}%`

    if (currentTask.status === 'completed') {
      return currentTask
    }
    if (currentTask.status === 'failed') {
      throw new Error(currentTask.error || currentTask.message || '导出失败。')
    }

    await waitForExportPoll(1200)
    const { data } = await http.get<FileExportTask>(`/file-records/export-tasks/${currentTask.task_id}`)
    currentTask = data
  }
}

async function waitForProjectFileZipExportTask(task: FileExportTask) {
  let currentTask = task
  while (true) {
    exportFileProgress.value = currentTask.progress
    exportFileMessage.value = currentTask.message || `压缩包导出处理中：${currentTask.progress}%`

    if (currentTask.status === 'completed') {
      return currentTask
    }
    if (currentTask.status === 'failed') {
      throw new Error(currentTask.error || currentTask.message || '压缩包导出失败。')
    }

    await waitForExportPoll(1200)
    const { data } = await http.get<FileExportTask>(`/projects/file-export-zip-tasks/${currentTask.task_id}`)
    currentTask = data
  }
}

async function loadProjectExportOptionsForSelection() {
  const rows = [...selectedProjectFiles.value]
  projectExportOptions.value = []
  if (rows.length === 0) {
    return
  }

  loadingProjectExportOptions.value = true
  try {
    const responses = await Promise.all(
      rows.map((row) => http.get<{ export_options: FileExportOption[] }>(`/file-records/${String(row.id)}/export-options`)),
    )
    const optionLists = responses.map((response) => response.data.export_options || [])
    const firstOptions = optionLists[0] || []
    const commonIds = new Set(firstOptions.map((option) => option.id))
    for (const options of optionLists.slice(1)) {
      const ids = new Set(options.map((option) => option.id))
      for (const id of Array.from(commonIds)) {
        if (!ids.has(id)) {
          commonIds.delete(id)
        }
      }
    }
    projectExportOptions.value = firstOptions.filter((option) => commonIds.has(option.id))
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.exportOptions'))
    projectExportOptions.value = []
  } finally {
    loadingProjectExportOptions.value = false
  }
}

async function toggleProjectExportMenu() {
  if (!canOpenProjectExportMenu.value) {
    return
  }
  if (showProjectExportMenu.value) {
    showProjectExportMenu.value = false
    return
  }
  await loadProjectExportOptionsForSelection()
  showProjectExportMenu.value = true
}

async function downloadProjectFileExport(row: ProjectRow, exportType: string) {
  const rowId = String(row.id)
  const filename = String(row.filename || 'export')
  const { data: task } = await http.post<FileExportTask>(
    `/file-records/${rowId}/exports`,
    null,
    { params: { type: exportType } },
  )
  const completedTask = await waitForFileExportTask(task)
  const response = await http.get(`/file-records/export-tasks/${completedTask.task_id}/download`, {
    responseType: 'blob',
  })
  const downloadName = resolveDownloadFilename(
    response.headers['content-disposition'],
    getProjectFileExportFallbackName(filename, exportType),
  )
  downloadBlob(response.data, downloadName)
}

function getProjectFileZipExportFallbackName() {
  const projectName = String(project.value?.name || project.value?.filename || '项目')
  return `${projectName}-目标文件.zip`
}

async function downloadProjectFileZipExport(rows: ProjectRow[]) {
  const { data: task } = await http.post<FileExportTask>(
    `/projects/${props.id}/file-export-zip-tasks`,
    { file_ids: rows.map((row) => String(row.id)) },
  )
  const completedTask = await waitForProjectFileZipExportTask(task)
  const response = await http.get(`/projects/file-export-zip-tasks/${completedTask.task_id}/download`, {
    responseType: 'blob',
  })
  const downloadName = resolveDownloadFilename(
    response.headers['content-disposition'],
    getProjectFileZipExportFallbackName(),
  )
  downloadBlob(response.data, downloadName)
}

async function exportProjectFile(row: ProjectRow, exportType = 'original') {
  if (exportingFileId.value) {
    return
  }

  closeActionMenu()
  showProjectExportMenu.value = false
  pageError.value = ''
  exportingFileId.value = String(row.id)
  exportingFileType.value = exportType
  exportFileProgress.value = 0
  exportFileMessage.value = '导出任务提交中。'

  try {
    await downloadProjectFileExport(row, exportType)
    toast.success(getProjectFileExportSuccessMessage(exportType, 1))
  } catch (error) {
    pageError.value = getErrorMessage(
      error,
      exportType === 'source' ? t('projectDetail.errors.exportSource') : t('projectDetail.errors.export'),
    )
  } finally {
    clearExportPollTimer()
    exportingFileId.value = ''
    exportingFileType.value = ''
    exportFileProgress.value = 0
    exportFileMessage.value = ''
  }
}

async function exportSelectedProjectFilesAsZip() {
  if (!canExportSelectedProjectFilesAsZip.value || exportingFileId.value) {
    return
  }

  closeActionMenu()
  showProjectExportMenu.value = false
  pageError.value = ''
  const rows = [...selectedProjectFiles.value]
  exportingFileId.value = '__project_zip__'
  exportingFileType.value = 'zip'
  exportFileProgress.value = 0
  exportFileMessage.value = '压缩包导出任务提交中。'

  try {
    await downloadProjectFileZipExport(rows)
    toast.success(`已开始下载包含 ${rows.length} 个目标文件的压缩包。`)
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.exportZip'))
  } finally {
    clearExportPollTimer()
    exportingFileId.value = ''
    exportingFileType.value = ''
    exportFileProgress.value = 0
    exportFileMessage.value = ''
  }
}

async function exportSelectedProjectFiles(exportType: string) {
  if (selectedProjectFiles.value.length === 0 || exportingFileId.value) {
    return
  }

  closeActionMenu()
  showProjectExportMenu.value = false
  pageError.value = ''
  const rows = [...selectedProjectFiles.value]

  try {
    for (let index = 0; index < rows.length; index += 1) {
      const current = rows[index]
      exportingFileId.value = String(current.id)
      exportingFileType.value = exportType
      exportFileProgress.value = 0
      exportFileMessage.value = `导出 ${index + 1}/${rows.length} 提交中。`
      await downloadProjectFileExport(current, exportType)
    }
    toast.success(getProjectFileExportSuccessMessage(exportType, rows.length))
  } catch (error) {
    pageError.value = getErrorMessage(
      error,
      exportType === 'source' ? t('projectDetail.errors.exportSource') : t('projectDetail.errors.export'),
    )
  } finally {
    clearExportPollTimer()
    exportingFileId.value = ''
    exportingFileType.value = ''
    exportFileProgress.value = 0
    exportFileMessage.value = ''
  }
}

async function deleteCurrentProject() {
  if (!project.value || !canManageProject.value) {
    return
  }

  const filename = project.value.filename || t('projectDetail.titleFallback')
  const confirmed = await confirm({
    title: t('projectDetail.files.actions.delete'),
    message: t('projectDetail.messages.deleteProjectConfirm', { name: filename }),
    confirmText: t('common.actions.delete'),
    danger: true,
  })

  if (!confirmed) {
    return
  }

  deleting.value = true
  pageError.value = ''

  try {
    await http.delete(`/projects/${props.id}`)
    toast.success(t('projectDetail.messages.projectDeleted', { name: filename }))
    await router.push({ name: 'projects' })
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.delete'))
  } finally {
    deleting.value = false
  }
}

async function deleteProjectFiles(rows: ProjectRow[]) {
  closeActionMenu()
  if (!canManageProject.value || rows.length === 0) {
    return
  }

  const fileCount = rows.length
  const firstFile = rows[0]
  const firstFilename = String(firstFile.filename || t('projectDetail.titleFallback'))

  const confirmed = await confirm({
    title: t('projectDetail.files.actions.delete'),
    message: fileCount === 1
      ? t('projectDetail.messages.deleteFileConfirm', { name: firstFilename })
      : t('projectDetail.messages.deleteFilesConfirm', { count: fileCount }),
    confirmText: t('common.actions.delete'),
    danger: true,
  })

  if (!confirmed) {
    return
  }

  deleting.value = true
  pageError.value = ''

  try {
    await Promise.all(rows.map((row) => http.delete(`/file-records/${String(row.id)}`)))
    const deletedIds = new Set(rows.map((row) => String(row.id)))
    selectedFileIds.value = new Set(
      Array.from(selectedFileIds.value).filter((id) => !deletedIds.has(id)),
    )
    toast.success(fileCount === 1
      ? t('projectDetail.messages.fileDeleted', { name: firstFilename })
      : t('projectDetail.messages.filesDeleted', { count: fileCount }))
    await loadProject()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.delete'))
  } finally {
    deleting.value = false
  }
}

async function deleteProjectFile(row: ProjectRow) {
  await deleteProjectFiles([row])
}

async function deleteSelectedProjectFiles() {
  if (!canDeleteSelectedProjectFiles.value) {
    return
  }

  await deleteProjectFiles(selectedProjectFiles.value)
}

async function duplicateSelectedTemplate() {
  if (!canDuplicateTemplate.value || !canManageProject.value) {
    return
  }

  const sourceFile = selectedProjectFiles.value[0]
  if (!sourceFile) {
    return
  }

  duplicating.value = true
  pageError.value = ''

  try {
    const { data } = await http.post<ProjectFileItem>(`/file-records/${sourceFile.id}/duplicate`)
    await loadProject()
    selectedFileIds.value = new Set([data.id])
    toast.success(t('projectDetail.messages.duplicated', { name: data.filename }))
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.duplicate'))
  } finally {
    duplicating.value = false
  }
}

async function createEnglishVariantCopy() {
  if (!canCreateEnglishVariantCopy.value || !project.value) {
    return
  }
  const sourceFile = selectedProjectFiles.value[0]
  if (!sourceFile) {
    return
  }

  creatingEnglishVariantCopy.value = true
  pageError.value = ''
  try {
    const { data } = await http.post<EnglishVariantCopyResponse>(
      `/projects/${project.value.id}/file-records/${sourceFile.id}/english-variant-copy`,
    )
    await loadProject()
    selectedFileIds.value = new Set([data.file.id])
    toast.success(t('projectDetail.messages.englishVariantCopyCreated', {
      name: data.file.filename,
      changed: data.summary.changed_segments,
      replacements: data.summary.replacement_count,
    }))
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.englishVariantCopy'))
  } finally {
    creatingEnglishVariantCopy.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
  window.addEventListener('scroll', handleDocumentScroll, { passive: true })
  window.addEventListener('resize', handleDocumentScroll)
  syncProjectSettingsHash()
  void (async () => {
    await loadProject({ preserveFilePagination: true })
    syncProjectSettingsHash()
    if (route.query.assign === '1' && canAssignProject.value) {
      await openAssignmentDialog()
    }
  })()
  void loadUploadCapabilities()
})

watch(uploadSourceLanguage, (sourceLanguage) => {
  if (isProjectLanguagePairBound.value || !sourceLanguage) {
    return
  }
  if (uploadTargetLanguages.value.includes(sourceLanguage)) {
    uploadTargetLanguages.value = uploadTargetLanguages.value.filter((code) => code !== sourceLanguage)
    uploadMessage.value = uploadFileValidationError.value || uploadGenerationValidationError.value
  }
})

watch(() => route.hash, () => {
  syncProjectSettingsHash()
})

watch(() => [route.query[FILE_PAGE_QUERY_KEY], route.query[FILE_PAGE_SIZE_QUERY_KEY]], () => {
  applyFilePaginationFromRouteQuery()
})

watch([fileSearchQuery, fileStatusFilter, fileLanguagePairFilter, fileAssigneeFilter], () => {
  currentPage.value = 1
  if (selectedFileIds.value.size > 0) {
    const visibleFileIds = new Set(filteredTableRows.value.map((file) => file.id))
    selectedFileIds.value = new Set(Array.from(selectedFileIds.value).filter((id) => visibleFileIds.has(id)))
  }
  closeFileSelectionMenu()
  closeActionMenu()
})

watch([filteredTableRows, pageSize], () => {
  const totalPages = Math.max(1, Math.ceil(filteredTableRows.value.length / pageSize.value))
  if (currentPage.value > totalPages) {
    currentPage.value = totalPages
  }
  if (showFileSelectionMenu.value) {
    resetFileSelectionRangeDefaults()
  }
})

watch([currentPage, pageSize], () => {
  syncFilePaginationToRouteQuery()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
  window.removeEventListener('scroll', handleDocumentScroll)
  window.removeEventListener('resize', handleDocumentScroll)
  clearExportPollTimer()
  clearActivePretranslationPollTimer()
  clearPendingResourceTopMoves()
})

</script>

<template>
  <div v-if="showUploadModal" class="upload-page" data-testid="project-upload-page">
    <header class="upload-page__topbar">
      <button class="upload-page__back" type="button" :disabled="uploading" @click="closeUploadDialog">
        <ArrowLeft :size="15" />
        返回
      </button>
      <span class="upload-page__divider" aria-hidden="true" />
      <strong>上传文件</strong>
    </header>

    <main class="upload-page__main">
      <section class="upload-page__workspace">
        <div
          class="upload-dropzone"
          @dragover.prevent
          @drop.prevent="onFileDrop"
        >
          <label class="button button--primary upload-dropzone__button">
            <input
              :key="uploadInputKey"
              class="sr-only"
              data-testid="project-upload-file-input"
              type="file"
              multiple
              :accept="uploadFileAccept"
              aria-label="上传文件"
              @change="onFileChange"
            />
            <Upload :size="16" />
            上传文件
          </label>
          <p>或拖放文件进行翻译</p>
        </div>

        <p class="upload-supported">
          后端当前支持：
          <span>{{ uploadSupportedSummary }}</span>
        </p>

        <section class="upload-language-panel">
          <div class="upload-language-panel__head">
            <div>
              <div class="section-title section-title--tight">语言设置</div>
              <p class="panel-subtitle">{{ uploadLanguageDescription }}</p>
            </div>
            <button
              v-if="!isProjectLanguagePairBound"
              class="button upload-detect-button"
              type="button"
              :disabled="!canDetectSourceLanguage"
              @click="detectSourceLanguage"
            >
              <Loader2 v-if="detectingLanguage" class="lucide-spin" :size="14" />
              <Sparkles v-else :size="14" />
              {{ detectingLanguage ? '识别中' : '识别源语言' }}
            </button>
          </div>

          <div class="upload-language-grid">
            <label class="field">
              <span class="field__label">{{ t('projectList.form.sourceLanguage') }} <span class="field__required">*</span></span>
              <select
                v-model="uploadSourceLanguage"
                class="field__control"
                data-testid="project-upload-source-language"
                :disabled="isProjectLanguagePairBound || uploading"
              >
                <option value="" disabled>{{ t('projectList.form.sourcePlaceholder') }}</option>
                <option
                  v-for="lang in languageOptions"
                  :key="lang.code"
                  :value="lang.code"
                >
                  {{ lang.label }}
                </option>
              </select>
            </label>

            <label v-if="isProjectLanguagePairBound" class="field">
              <span class="field__label">{{ t('projectList.form.targetLanguage') }} <span class="field__required">*</span></span>
              <select
                :value="effectiveUploadTargetLanguages[0] || ''"
                class="field__control"
                data-testid="project-upload-target-language"
                disabled
              >
                <option value="" disabled>{{ t('projectList.form.targetPlaceholder') }}</option>
                <option
                  v-for="lang in languageOptions"
                  :key="lang.code"
                  :value="lang.code"
                  :disabled="lang.code === uploadSourceLanguage"
                >
                  {{ lang.label }}
                </option>
              </select>
            </label>

            <div v-else class="field upload-target-field">
              <span class="field__label">
                {{ t('projectDetail.uploadLanguage.targetLanguages') }}
                <span class="field__required">*</span>
              </span>
              <div
                class="upload-target-select"
                :class="{ 'is-open': uploadTargetMenuOpen }"
                data-testid="project-upload-target-languages"
              >
                <button
                  class="upload-target-select__trigger"
                  data-testid="project-upload-target-trigger"
                  type="button"
                  :disabled="uploading"
                  :aria-expanded="uploadTargetMenuOpen"
                  @click.stop="toggleUploadTargetMenu"
                >
                  <span v-if="uploadTargetLanguages.length === 0" class="upload-target-select__placeholder">
                    {{ t('projectDetail.uploadLanguage.targetPlaceholder') }}
                  </span>
                  <span v-else class="upload-target-select__value">
                    {{ t('projectDetail.uploadLanguage.selectedTargets', { count: uploadTargetLanguages.length }) }}
                  </span>
                  <ChevronDown :size="16" :class="{ 'is-rotated': uploadTargetMenuOpen }" />
                </button>

                <div
                  v-if="uploadTargetMenuOpen"
                  class="upload-target-select__popover"
                  @click.stop
                  @keydown.esc="closeUploadTargetMenu"
                >
                  <label class="upload-target-select__search">
                    <Search :size="14" />
                    <input
                      v-model="uploadTargetLanguageSearch"
                      data-testid="project-upload-target-search"
                      type="search"
                      :placeholder="t('projectDetail.uploadLanguage.searchTarget')"
                      :disabled="uploading"
                    />
                  </label>
                  <div class="upload-target-select__options">
                    <label
                      v-for="lang in filteredUploadTargetLanguageOptions"
                      :key="lang.code"
                      class="upload-target-option"
                      :class="{ 'is-selected': uploadTargetLanguages.includes(lang.code) }"
                    >
                      <input
                        type="checkbox"
                        :data-testid="`project-upload-target-${lang.code}`"
                        :checked="uploadTargetLanguages.includes(lang.code)"
                        :disabled="uploading"
                        @change="toggleUploadTargetLanguage(lang.code)"
                      />
                      <span>{{ lang.label }}</span>
                      <small>{{ lang.code }}</small>
                    </label>
                    <p v-if="filteredUploadTargetLanguageOptions.length === 0" class="upload-target-select__empty">
                      {{ t('projectDetail.uploadLanguage.noTargetResult') }}
                    </p>
                  </div>
                  <div class="upload-target-select__footer">
                    <span>{{ t('projectDetail.uploadLanguage.selectedTargets', { count: uploadTargetLanguages.length }) }}</span>
                    <button class="button button--primary" type="button" @click="closeUploadTargetMenu">
                      {{ t('common.actions.confirm') }}
                    </button>
                  </div>
                </div>
              </div>

              <div v-if="uploadTargetLanguages.length" class="upload-target-select__chips">
                <button
                  v-for="languageCode in uploadTargetLanguages"
                  :key="languageCode"
                  class="upload-target-chip"
                  type="button"
                  :disabled="uploading"
                  :aria-label="t('projectDetail.uploadLanguage.removeTarget', { language: getLanguageLabel(languageCode) })"
                  @click="removeUploadTargetLanguage(languageCode)"
                >
                  <span>{{ getLanguageLabel(languageCode) }}</span>
                  <X :size="12" />
                </button>
              </div>
            </div>
          </div>

          <p v-if="isProjectLanguagePairBound" class="upload-bound-language">
            {{ uploadLanguageBoundMessage }}
          </p>

          <p
            v-if="languageDetectMessage"
            class="upload-detect-message"
            :class="`upload-detect-message--${languageDetectTone}`"
          >
            {{ languageDetectMessage }}
          </p>

          <p
            v-if="selectedFiles.length && effectiveUploadTargetLanguages.length"
            class="upload-task-estimate"
            :class="{ 'is-error': Boolean(uploadGenerationValidationError) }"
            data-testid="project-upload-task-estimate"
          >
            {{ t('projectDetail.uploadLanguage.taskEstimate', {
              files: selectedFiles.length,
              languages: effectiveUploadTargetLanguages.length,
              count: generatedUploadTaskCount,
            }) }}
            <span v-if="uploadGenerationValidationError">{{ uploadGenerationValidationError }}</span>
          </p>

          <div v-if="selectedFiles.length" class="upload-file-list-wrap">
            <div class="upload-file-list__head">
              <span class="upload-file-list__summary">已选 {{ selectedFiles.length }} 个文件</span>
              <button
                type="button"
                class="upload-file-list__clear"
                data-testid="project-upload-clear-files"
                :disabled="uploading"
                @click="clearSelectedUploadFiles"
              >
                <X :size="14" />
                取消选中
              </button>
            </div>
            <div class="upload-file-list">
              <div
                v-for="(file, index) in selectedFiles"
                :key="`${file.name}-${file.size}-${file.lastModified}-${index}`"
                class="upload-file-list__item"
              >
                <FileText :size="15" />
                <span>{{ file.name }}</span>
              </div>
            </div>
          </div>

          <div v-if="uploading" class="upload-page__progress">
            <div class="progress-bar">
              <div class="progress-bar__track">
                <div
                  class="progress-bar__fill"
                  :class="{ 'is-complete': isProgressComplete(uploadPercent) }"
                  :style="{ width: `${uploadPercent}%` }"
                />
              </div>
              <span class="progress-bar__text">{{ uploadPercent }}%</span>
            </div>
          </div>

          <p v-if="uploadMessage" class="form-message is-error">{{ uploadMessage }}</p>

          <div class="upload-page__actions">
            <button class="button" type="button" :disabled="uploading" @click="closeUploadDialog">
              {{ t('common.actions.cancel') }}
            </button>
            <button
              class="button button--primary"
              data-testid="project-upload-submit"
              type="button"
              :disabled="!canSubmitSourceUpload"
              @click="uploadSourceDocument"
            >
              <Loader2 v-if="uploading" class="lucide-spin" :size="14" />
              <Upload v-else :size="14" />
              {{ uploading
                ? t('projectDetail.messages.uploading', { percent: uploadPercent })
                : t('projectDetail.messages.startUploadCount', { count: generatedUploadTaskCount }) }}
            </button>
          </div>
        </section>
      </section>

      <DocumentParseSettings
        v-model="documentParseMode"
        v-model:parse-options="documentParseOptions"
        :capabilities="uploadCapabilities"
        :selected-files="selectedFiles"
        :loading="loadingUploadCapabilities"
        variant="panel"
      />
    </main>
  </div>

  <div v-else class="content-stack pd-layout workbench-page">
    <section class="panel pd-hero">
      <div class="pd-hero__main">
        <div class="pd-hero__left">
          <button
            class="button workbench-action workbench-action--back workbench-toolbar__icon-btn pd-hero__back"
            type="button"
            :title="backLabel"
            :aria-label="backLabel"
            @click="goBack"
          >
            <ArrowLeft :size="16" />
          </button>
          <div class="pd-hero__copy">
            <div class="section-title section-title--tight">
              {{ t('projectDetail.hero.title', { name: project?.filename || t('projectDetail.titleFallback') }) }}
            </div>
            <p class="panel-subtitle">{{ t('projectDetail.description') }}</p>
          </div>
        </div>

        <div class="pd-hero__progress">
          <span class="pd-hero__progress-label">{{ t('projectDetail.totals.progressLabel') }}</span>
          <WorkflowProgressSummary
            class="pd-hero__progress-bar"
            :progress="project?.progress ?? 0"
            :status="project?.status || ''"
            :workflow-progress="projectWorkflowProgress"
            :label="t('common.progress.total')"
            :detail-title="t('common.progress.workflowDetail')"
          />
        </div>
      </div>
    </section>

    <nav class="pd-tabs" :aria-label="t('pages.projectDetail.title')">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="pd-tabs__item"
        :class="{ 'is-active': activeTab === tab.key }"
        type="button"
        :disabled="tab.disabled"
        :title="tab.disabled ? t('projectDetail.common.comingSoon') : undefined"
        @click="!tab.disabled && switchProjectTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <p v-if="pageError" class="form-message is-error">{{ pageError }}</p>

    <section v-if="loading && !project" class="panel">
      <div class="empty-state">
        <Loader2 class="lucide-spin" :size="32" />
        {{ t('projectDetail.loading') }}
      </div>
    </section>

    <template v-else-if="project">
      <section class="panel pd-base-panel" :class="{ 'is-collapsed': basicCollapsed }">
        <div class="pd-panel-head">
          <div class="pd-panel-head__copy">
            <div class="section-title section-title--tight">{{ t('projectDetail.base.title') }}</div>
            <p class="panel-subtitle">{{ t('projectDetail.base.description') }}</p>
            <div class="pd-base-summary" aria-label="项目摘要">
              <span>{{ formatStatus(project.status) }}</span>
              <span>{{ projectLanguagePairLabel }}</span>
              <span>{{ project.total_segments }} 句段</span>
              <span>{{ tableRows.length }} 个文件</span>
            </div>
          </div>
          <button
            class="button pd-panel-toggle"
            type="button"
            :aria-expanded="!basicCollapsed"
            @click="toggleBasicCollapsed"
          >
            {{ basicCollapsed ? t('projectDetail.base.expand') : t('projectDetail.base.collapse') }}
            <ChevronDown v-if="basicCollapsed" :size="16" />
            <ChevronUp v-else :size="16" />
          </button>
        </div>

        <div
          class="pd-basic-collapse"
          :class="{ 'is-collapsed': basicCollapsed }"
          :aria-hidden="basicCollapsed"
        >
          <div class="pd-basic-collapse__inner">
            <div class="pd-basic-grid">
              <label class="pd-field">
                <span class="pd-field__label">{{ t('projectDetail.base.status') }}</span>
                <span class="pd-field__value">
                  <span class="project-status" :class="getStatusClass(project.status)">
                    {{ formatStatus(project.status) }}
                  </span>
                </span>
              </label>
              <label class="pd-field">
                <span class="pd-field__label">{{ t('projectDetail.base.languagePair') }}</span>
                <span class="pd-field__value">{{ projectLanguagePairLabel }}</span>
              </label>
              <label class="pd-field">
                <span class="pd-field__label">{{ t('projectDetail.base.workflow') }}</span>
                <span class="pd-field__value">{{ projectWorkflowLabel }}</span>
              </label>
              <label class="pd-field">
                <span class="pd-field__label">{{ t('projectDetail.base.createdAt') }}</span>
                <span class="pd-field__value">{{ formatDateText(project.created_at) }}</span>
              </label>
              <label class="pd-field">
                <span class="pd-field__label">{{ t('projectDetail.base.totalWords') }}</span>
                <span class="pd-field__value">{{ project.total_segments }}</span>
              </label>
              <label class="pd-field">
                <span class="pd-field__label">{{ t('projectDetail.base.deadline') }}</span>
                <span class="pd-field__value">{{ formatDateText(project.deadline) }}</span>
              </label>
              <label class="pd-field">
                <span class="pd-field__label">{{ t('projectDetail.base.domain') }}</span>
                <span class="pd-field__value">{{ getPlaceholder() }}</span>
              </label>
              <label class="pd-field">
                <span class="pd-field__label">{{ t('projectDetail.base.fileCount') }}</span>
                <span class="pd-field__value">{{ tableRows.length }}</span>
              </label>
              <label class="pd-field">
                <span class="pd-field__label">{{ t('issueMarker.list.title') }}</span>
                <span class="pd-field__value">
                  {{ t('issueMarker.list.openCount', { count: openIssueCount }) }}
                </span>
              </label>
              <label class="pd-field">
                <span class="pd-field__label">{{ t('projectDetail.base.creator') }}</span>
                <span class="pd-field__value">{{ project.creator || getPlaceholder() }}</span>
              </label>
              <label class="pd-field">
                <span class="pd-field__label">{{ t('projectDetail.base.pm') }}</span>
                <span class="pd-field__value">{{ getPlaceholder() }}</span>
              </label>
            </div>
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'settings'" class="panel pd-settings-panel">
        <div class="pd-settings-layout">
          <nav class="pd-settings-rail" aria-label="项目设置分区">
            <button
              class="pd-settings-rail__item"
              :class="{ 'is-active': activeProjectSettingsSection === 'basic' }"
              type="button"
              @click="switchProjectSettingsSection('basic')"
            >
              <Settings2 :size="15" />
              <span>基础信息</span>
            </button>
            <button
              class="pd-settings-rail__item"
              :class="{ 'is-active': activeProjectSettingsSection === 'guidelines' }"
              type="button"
              @click="switchProjectSettingsSection('guidelines')"
            >
              <FileText :size="15" />
              <span>翻译要求</span>
            </button>
            <button
              class="pd-settings-rail__item"
              :class="{ 'is-active': activeProjectSettingsSection === 'translation-memory' }"
              type="button"
              @click="switchProjectSettingsSection('translation-memory')"
            >
              <BookOpen :size="15" />
              <span>翻译记忆库</span>
            </button>
            <button
              class="pd-settings-rail__item"
              :class="{ 'is-active': activeProjectSettingsSection === 'terms' }"
              type="button"
              @click="switchProjectSettingsSection('terms')"
            >
              <BookOpen :size="15" />
              <span>术语库</span>
            </button>
            <button
              class="pd-settings-rail__item"
              :class="{ 'is-active': activeProjectSettingsSection === 'automation' }"
              type="button"
              @click="switchProjectSettingsSection('automation')"
            >
              <Sparkles :size="15" />
              <span>自动应用与锁定</span>
            </button>
            <button
              class="pd-settings-rail__item"
              :class="{ 'is-active': activeProjectSettingsSection === 'quality-qa' }"
              type="button"
              @click="switchProjectSettingsSection('quality-qa')"
            >
              <ShieldCheck :size="15" />
              <span>质量保证</span>
            </button>
            <button
              class="pd-settings-rail__item"
              :class="{ 'is-active': activeProjectSettingsSection === 'term-qa' }"
              type="button"
              @click="switchProjectSettingsSection('term-qa')"
            >
              <ShieldCheck :size="15" />
              <span>术语 QA</span>
            </button>
          </nav>

          <div class="pd-settings-main">
            <div class="pd-settings-overview">
              <div class="pd-settings-overview__copy">
                <div class="section-title section-title--tight">{{ t('projectDetail.tabs.settings') }}</div>
                <p class="panel-subtitle">{{ t('projectDetail.settings.basicDescription') }}</p>
              </div>
            </div>

            <section v-show="activeProjectSettingsSection === 'basic'" id="project-settings-basic" class="pd-settings-section">
              <header class="pd-settings-section-head">
                <div class="pd-settings-section-head__copy">
                  <span class="pd-settings-section-icon">
                    <Settings2 :size="17" />
                  </span>
                  <div>
                    <div class="section-title section-title--tight">{{ t('projectDetail.settings.basicTitle') }}</div>
                    <p class="panel-subtitle">{{ t('projectDetail.settings.languageLockedHint') }}</p>
                  </div>
                </div>
                <button
                  class="button button--primary pd-settings-save"
                  data-testid="project-settings-save"
                  type="button"
                  :disabled="savingSettings"
                  @click="saveProjectSettings"
                >
                  <Loader2 v-if="savingSettings" class="lucide-spin" :size="14" />
                  <Settings2 v-else :size="14" />
                  {{ savingSettings ? t('common.actions.saving') : t('projectDetail.settings.saveBasic') }}
                </button>
              </header>

              <div class="pd-settings-section-body">
                <div class="pd-settings-list">
                  <label class="field pd-settings-row pd-settings-row--name">
                    <span class="field__label">{{ t('projectDetail.settings.nameLabel') }} <span class="field__required">*</span></span>
                    <input
                      v-model="settingsForm.name"
                      class="field__control pd-settings-control pd-settings-control--name"
                      data-testid="project-settings-name"
                      type="text"
                      maxlength="200"
                      :placeholder="t('projectDetail.settings.namePlaceholder')"
                    />
                  </label>

                  <label class="field pd-settings-row">
                    <span class="field__label">{{ t('projectDetail.settings.deadlineLabel') }}</span>
                    <input
                      v-model="settingsForm.deadline"
                      class="field__control pd-settings-control"
                      data-testid="project-settings-deadline"
                      type="datetime-local"
                    />
                  </label>

                  <label class="field pd-settings-row">
                    <span class="field__label">{{ t('projectDetail.settings.accessLevelLabel') }}</span>
                    <select
                      v-model="settingsForm.access_level"
                      class="field__control pd-settings-control"
                      data-testid="project-settings-access-level"
                    >
                      <option v-for="option in accessOptions" :key="option.value" :value="option.value">
                        {{ option.label }}
                      </option>
                    </select>
                  </label>
                </div>

                <div class="pd-readonly-grid">
                  <label class="pd-readonly-field">
                    <span class="field__label">{{ t('projectDetail.base.languagePair') }}</span>
                    <span class="pd-readonly-value">{{ projectLanguagePairLabel }}</span>
                  </label>
                  <label class="pd-readonly-field">
                    <span class="field__label">{{ t('projectDetail.base.createdAt') }}</span>
                    <span class="pd-readonly-value">{{ formatDateText(project.created_at) }}</span>
                  </label>
                </div>

                <p v-if="settingsError" class="form-message is-error">{{ settingsError }}</p>
              </div>
            </section>

            <section v-show="activeProjectSettingsSection === 'guidelines'" id="project-settings-guidelines" class="pd-settings-section">
              <header class="pd-settings-section-head">
                <div class="pd-settings-section-head__copy">
                  <span class="pd-settings-section-icon">
                    <FileText :size="17" />
                  </span>
                  <div>
                    <div class="section-title section-title--tight">{{ t('projectDetail.settings.guidelinesTitle') }}</div>
                    <p class="panel-subtitle">{{ t('projectDetail.settings.guidelinesDescription') }}</p>
                  </div>
                </div>
                <button
                  class="button button--primary pd-settings-save"
                  type="button"
                  :disabled="savingGuidelines"
                  @click="saveGuidelines"
                >
                  <Loader2 v-if="savingGuidelines" class="lucide-spin" :size="14" />
                  <Settings2 v-else :size="14" />
                  {{ savingGuidelines ? t('common.actions.saving') : t('common.actions.save') }}
                </button>
              </header>

              <div class="pd-settings-section-body">
                <label class="field field--full">
                  <span class="field__label">{{ t('projectDetail.settings.guidelinesLabel') }}</span>
                  <textarea
                    v-model="guidelinesText"
                    class="field__control pd-guidelines-editor"
                    rows="8"
                    :placeholder="t('projectDetail.settings.guidelinesPlaceholder')"
                  />
                </label>
                <p class="hint-text">{{ t('projectDetail.settings.guidelinesHint') }}</p>
              </div>
            </section>

            <section v-show="activeProjectSettingsSection === 'translation-memory'" id="project-settings-translation-memory" class="pd-settings-section pd-settings-section--resource">
              <header class="pd-settings-section-head">
                <div class="pd-settings-section-head__copy">
                  <span class="pd-settings-section-icon">
                    <BookOpen :size="17" />
                  </span>
                  <div>
                    <div class="section-title section-title--tight">翻译记忆库</div>
                    <p class="panel-subtitle">启用后参与匹配；勾选写入后作为主写入库，确认句段会实时写入更新。</p>
                  </div>
                </div>
                <button
                  class="button button--primary pd-settings-save pd-settings-save--compact"
                  type="button"
                  :disabled="savingTranslationMemorySettings || loadingTranslationMemorySettings"
                  :title="savingTranslationMemorySettings ? '保存中' : '保存记忆库设置'"
                  :aria-label="savingTranslationMemorySettings ? '保存中' : '保存记忆库设置'"
                  @click="saveProjectTranslationMemorySettings()"
                >
                  <Loader2 v-if="savingTranslationMemorySettings" class="lucide-spin" :size="14" />
                  <Settings2 v-else :size="14" />
                  {{ savingTranslationMemorySettings ? '保存中' : '保存' }}
                </button>
              </header>

              <div class="pd-settings-section-body tm-settings">
                <StateView
                  v-if="loadingTranslationMemorySettings"
                  kind="loading"
                  title="正在加载记忆库设置"
                  message="正在读取项目文件语言对和可用记忆库。"
                />
                <p v-else-if="translationMemorySettingsError" class="form-message is-error">{{ translationMemorySettingsError }}</p>
                <div v-else-if="!translationMemorySettings || translationMemorySettings.groups.length === 0" class="empty-state">
                  当前项目还没有可配置语言对的文件。
                </div>
                <div v-else class="tm-settings__groups">
                  <div class="tm-settings__auto-sync">
                    <label class="term-settings__toggle tm-settings__auto-sync-switch">
                      <input
                        v-model="translationMemorySettings.auto_tm_enabled"
                        type="checkbox"
                        aria-label="自动写入确认句段到主写入记忆库"
                      >
                      <span aria-hidden="true" />
                    </label>
                    <div class="tm-settings__auto-sync-copy">
                      <strong>自动写入确认句段到主写入记忆库</strong>
                      <span>开启后，已确认且有译文的句段会进入后台队列，写入当前文件的主写入记忆库。</span>
                    </div>
                  </div>
                  <div class="resource-settings-search">
                    <label class="resource-settings-search__field">
                      <Search :size="14" />
                      <input
                        v-model="tmSettingsSearchQuery"
                        type="search"
                        placeholder="搜索记忆库名称、说明、语言或条目数"
                      >
                    </label>
                    <span class="resource-settings-search__summary">
                      显示 {{ getFilteredTMSettingsCollectionCount() }} / {{ getTMSettingsCollectionCount() }} 个记忆库
                    </span>
                    <button
                      v-if="tmSettingsSearchQuery"
                      class="resource-settings-search__clear"
                      type="button"
                      title="清空搜索"
                      @click="tmSettingsSearchQuery = ''"
                    >
                      <X :size="14" />
                    </button>
                  </div>
                  <section
                    v-for="group in translationMemorySettings.groups"
                    :key="translationMemorySettingGroupKey(group)"
                    class="tm-settings__panel"
                  >
                    <div class="tm-settings__panel-head">
                      <div>
                        <strong>{{ getTranslationMemorySettingPairLabel(group) }}</strong>
                        <span>
                          {{ group.file_count }} 个文件 ·
                          <template v-if="tmSettingsSearchQuery">
                            显示 {{ getFilteredTMCollections(group).length }} / {{ group.collections.length }} 个记忆库
                          </template>
                          <template v-else>{{ group.collections.length }} 个记忆库</template>
                        </span>
                      </div>
                      <div class="tm-settings__panel-actions">
                        <label class="tm-settings__threshold">
                          <span>匹配率阈值</span>
                          <input
                            type="range"
                            min="0.5"
                            max="1"
                            step="0.01"
                            :value="getGroupTMMatchThreshold(group)"
                            @input="setGroupTMMatchThreshold(group, $event)"
                          >
                          <input
                            type="number"
                            min="0.5"
                            max="1"
                            step="0.01"
                            :value="getGroupTMMatchThreshold(group)"
                            @change="setGroupTMMatchThreshold(group, $event)"
                          >
                        </label>
                        <button
                          class="button term-settings__create"
                          type="button"
                          :disabled="Boolean(creatingTranslationMemoryPair)"
                          :title="`创建 ${getTranslationMemorySettingPairLabel(group)} 记忆库`"
                          :aria-label="`创建 ${getTranslationMemorySettingPairLabel(group)} 记忆库`"
                          @click="createTranslationMemoryForGroup(group)"
                        >
                          <Loader2 v-if="creatingTranslationMemoryPair === translationMemorySettingGroupKey(group)" class="lucide-spin" :size="14" />
                          <Plus v-else :size="14" />
                          新建
                        </button>
                        <button
                          class="button term-settings__create"
                          type="button"
                          :disabled="projectResourceLanguageLoading || projectResourceLanguageSubmitting"
                          :title="`复制已有记忆库为 ${getTranslationMemorySettingPairLabel(group)}`"
                          :aria-label="`复制已有记忆库为 ${getTranslationMemorySettingPairLabel(group)}`"
                          @click="openProjectResourceLanguageDialog('tm', group)"
                        >
                          <Settings2 :size="14" />
                          复制语言对
                        </button>
                      </div>
                    </div>

                    <div v-if="group.collections.length === 0" class="empty-state">
                      当前语言对暂无记忆库。
                    </div>
                    <div v-else class="tm-settings__table-wrap">
                      <table class="tm-settings__full-table">
                        <colgroup>
                          <col class="tm-settings__full-col-index">
                          <col class="tm-settings__full-col-toggle">
                          <col class="tm-settings__full-col-write">
                          <col class="tm-settings__full-col-name">
                          <col class="tm-settings__full-col-status">
                          <col class="tm-settings__full-col-lang">
                          <col class="tm-settings__full-col-lang">
                          <col class="tm-settings__full-col-count">
                          <col class="tm-settings__full-col-files">
                          <col class="tm-settings__full-col-action">
                        </colgroup>
                        <thead>
                          <tr>
                            <th>序号</th>
                            <th>启用</th>
                            <th title="勾选后，该记忆库会作为主写入库，确认句段将实时写入更新。">写入</th>
                            <th>记忆库名称</th>
                            <th>状态</th>
                            <th>源语言</th>
                            <th>目标语言</th>
                            <th>条目数</th>
                            <th>绑定文件</th>
                            <th>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-if="getFilteredTMCollections(group).length === 0">
                            <td colspan="10">没有匹配的记忆库。</td>
                          </tr>
                          <template v-for="(collection, collectionIndex) in getFilteredTMCollections(group)" :key="collection.id">
                            <tr>
                              <td>{{ collectionIndex + 1 }}</td>
                              <td>
                                <label class="term-settings__toggle">
                                  <input
                                    type="checkbox"
                                    :checked="isTMCollectionEnabled(group, collection.id)"
                                    :aria-label="`启用 ${collection.name}`"
                                    @change="toggleTMCollectionEnabled(group, collection.id, $event)"
                                  />
                                  <span aria-hidden="true" />
                                </label>
                              </td>
                              <td>
                                <input
                                  class="tm-settings__checkbox"
                                  type="checkbox"
                                  :checked="isTMCollectionWritable(group, collection.id)"
                                  :aria-label="`实时写入 ${collection.name}`"
                                  title="勾选后实时写入更新"
                                  @change="toggleTMCollectionWritable(group, collection.id, $event)"
                                >
                              </td>
                              <td>
                                <span class="term-settings__name">{{ collection.name }}</span>
                                <span v-if="collection.description" class="term-settings__meta">{{ collection.description }}</span>
                              </td>
                              <td>正常</td>
                              <td>{{ getLanguageLabel(collection.source_language) }}</td>
                              <td>{{ getLanguageLabel(collection.target_language) }}</td>
                              <td>{{ collection.entry_count }}</td>
                              <td>{{ getTMCollectionBoundSummary(group, collection.id) }}</td>
                              <td>
                                <button
                                  class="button tm-settings__file-button pd-icon-action"
                                  type="button"
                                  :title="`增量导入 ${collection.name}`"
                                  :aria-label="`增量导入 ${collection.name}`"
                                  @click="openTMIncrementalImport(group, collection)"
                                >
                                  <Upload :size="14" />
                                </button>
                                <button
                                  class="button tm-settings__file-button pd-icon-action"
                                  type="button"
                                  :title="isTMCollectionDetailsOpen(group, collection.id) ? `收起 ${collection.name} 文件设置` : `展开 ${collection.name} 文件设置`"
                                  :aria-label="isTMCollectionDetailsOpen(group, collection.id) ? `收起 ${collection.name} 文件设置` : `展开 ${collection.name} 文件设置`"
                                  @click="toggleTMCollectionDetails(group, collection.id)"
                                >
                                  <ChevronUp v-if="isTMCollectionDetailsOpen(group, collection.id)" :size="14" />
                                  <Settings2 v-else :size="14" />
                                </button>
                              </td>
                            </tr>
                            <tr v-if="isTMCollectionDetailsOpen(group, collection.id)" class="tm-settings__detail-row">
                              <td colspan="10">
                                <div class="tm-settings__file-panel">
                                  <div class="tm-settings__file-panel-head">
                                    <div>
                                      <strong>文件设置</strong>
                                      <span>为当前记忆库配置各文件的启用、主写入和匹配阈值。</span>
                                    </div>
                                    <div class="tm-settings__file-panel-actions">
                                      <span class="tm-settings__file-summary">{{ getTMCollectionBoundSummary(group, collection.id) }}</span>
                                      <button
                                        class="button tm-settings__batch-button"
                                        type="button"
                                        :class="{ 'is-active': isTMCollectionBoundForAll(group, collection.id) }"
                                        :title="isTMCollectionBoundForAll(group, collection.id) ? '取消当前记忆库在全部文件中的启用' : '为当前记忆库批量启用全部文件'"
                                        :aria-label="isTMCollectionBoundForAll(group, collection.id) ? '取消当前记忆库在全部文件中的启用' : '为当前记忆库批量启用全部文件'"
                                        @click="toggleTMCollectionBindingForAll(group, collection.id)"
                                      >
                                        <Check v-if="!isTMCollectionBoundForAll(group, collection.id)" :size="13" />
                                        <X v-else :size="13" />
                                      </button>
                                    </div>
                                  </div>
                                  <div class="tm-settings__file-header" aria-hidden="true">
                                    <span>文件</span>
                                    <span>启用</span>
                                    <span>主写入</span>
                                    <span>阈值</span>
                                  </div>
                                  <div
                                    v-for="file in group.files"
                                    :key="file.id"
                                    class="tm-settings__file-item"
                                  >
                                    <span class="tm-settings__file-name">{{ file.filename }}</span>
                                    <label class="tm-settings__file-check">
                                      <input
                                        type="checkbox"
                                        :checked="isFileTMCollectionBound(file, collection.id)"
                                        @change="toggleFileTMCollection(file, collection.id, $event)"
                                      >
                                      <span>启用</span>
                                    </label>
                                    <label class="tm-settings__file-check">
                                      <input
                                        type="radio"
                                        :name="`primary-${file.id}`"
                                        :value="collection.id"
                                        :disabled="!isFileTMCollectionBound(file, collection.id)"
                                        :checked="file.collection_id === collection.id"
                                        @change="setFilePrimaryTMCollection(file, $event)"
                                      >
                                      <span>主写入</span>
                                    </label>
                                    <label class="tm-settings__file-threshold">
                                      <span>阈值</span>
                                      <input
                                        type="number"
                                        min="0.5"
                                        max="1"
                                        step="0.01"
                                        :value="normalizeTMMatchThreshold(file.tm_match_threshold)"
                                        @change="setFileTMMatchThreshold(file, $event)"
                                      >
                                    </label>
                                  </div>
                                </div>
                              </td>
                            </tr>
                          </template>
                        </tbody>
                      </table>
                    </div>
                  </section>
                </div>
              </div>
            </section>

            <section v-show="activeProjectSettingsSection === 'terms'" id="project-settings-terms" class="pd-settings-section pd-settings-section--resource">
              <header class="pd-settings-section-head">
                <div class="pd-settings-section-head__copy">
                  <span class="pd-settings-section-icon">
                    <BookOpen :size="17" />
                  </span>
                  <div>
                    <div class="section-title section-title--tight">术语库设置</div>
                    <p class="panel-subtitle">按文件语言对启用术语提醒、控制写入入口，并指定术语 QA 标准库。</p>
                  </div>
                </div>
                <button
                  class="button button--primary pd-settings-save pd-settings-save--compact"
                  type="button"
                  :disabled="savingTermBaseSettings || loadingTermBaseSettings"
                  :title="savingTermBaseSettings ? '保存中' : '保存术语库设置'"
                  :aria-label="savingTermBaseSettings ? '保存中' : '保存术语库设置'"
                  @click="saveProjectTermBaseSettings()"
                >
                  <Loader2 v-if="savingTermBaseSettings" class="lucide-spin" :size="14" />
                  <Settings2 v-else :size="14" />
                  {{ savingTermBaseSettings ? '保存中' : '保存' }}
                </button>
              </header>

              <div class="pd-settings-section-body term-settings">
                <StateView
                  v-if="loadingTermBaseSettings"
                  kind="loading"
                  title="正在加载术语库设置"
                  message="正在读取项目文件语言对和可用术语库。"
                />
                <p v-else-if="termBaseSettingsError" class="form-message is-error">{{ termBaseSettingsError }}</p>
                <div v-else-if="!termBaseSettings || termBaseSettings.groups.length === 0" class="empty-state">
                  当前项目还没有可配置语言对的文件。
                </div>
                <div v-else class="term-settings__groups">
                  <div class="resource-settings-search">
                    <label class="resource-settings-search__field">
                      <Search :size="14" />
                      <input
                        v-model="termBaseSettingsSearchQuery"
                        type="search"
                        placeholder="搜索术语库名称、说明、语言、条目数或 QA"
                      >
                    </label>
                    <span class="resource-settings-search__summary">
                      显示 {{ getFilteredTermBaseSettingsRowCount() }} / {{ getTermBaseSettingsRowCount() }} 个术语库
                    </span>
                    <button
                      v-if="termBaseSettingsSearchQuery"
                      class="resource-settings-search__clear"
                      type="button"
                      title="清空搜索"
                      @click="termBaseSettingsSearchQuery = ''"
                    >
                      <X :size="14" />
                    </button>
                  </div>
                  <section
                    v-for="group in termBaseSettings.groups"
                    :key="termBaseSettingGroupKey(group)"
                    class="term-settings__group"
                  >
                    <div class="term-settings__group-head">
                      <span class="term-settings__group-summary">
                        {{ getTermBaseSettingPairLabel(group) }} · {{ group.file_count }} 个文件 ·
                        <template v-if="termBaseSettingsSearchQuery">
                          显示 {{ getFilteredTermBaseRows(group).length }} / {{ group.term_bases.length }} 个术语库
                        </template>
                        <template v-else>{{ group.term_bases.length }} 个术语库</template>
                      </span>
                      <button
                        class="button term-settings__create"
                        type="button"
                        :disabled="Boolean(creatingTermBasePair)"
                        :title="`创建 ${getTermBaseSettingPairLabel(group)} 术语库`"
                        :aria-label="`创建 ${getTermBaseSettingPairLabel(group)} 术语库`"
                        @click="createTermBaseForGroup(group)"
                      >
                        <Loader2 v-if="creatingTermBasePair === termBaseSettingGroupKey(group)" class="lucide-spin" :size="14" />
                        <Plus v-else :size="14" />
                        新建
                      </button>
                      <button
                        class="button term-settings__create"
                        type="button"
                        :disabled="projectResourceLanguageLoading || projectResourceLanguageSubmitting"
                        :title="`复制已有术语库为 ${getTermBaseSettingPairLabel(group)}`"
                        :aria-label="`复制已有术语库为 ${getTermBaseSettingPairLabel(group)}`"
                        @click="openProjectResourceLanguageDialog('term', group)"
                      >
                        <Settings2 :size="14" />
                        复制语言对
                      </button>
                    </div>

                    <div class="term-settings__table-wrap">
                      <table class="term-settings__table">
                        <thead>
                          <tr>
                            <th>术语库</th>
                            <th>启用</th>
                            <th>
                              写入
                              <span class="term-settings__tip" title="允许在本项目相关入口中编辑、增加和删除该术语库中的术语。">?</span>
                            </th>
                            <th>
                              QA
                              <span class="term-settings__tip" title="用于 QA 的术语库是质量检查的标准术语库。勾选后，系统会用该库检查译文术语一致性。">?</span>
                            </th>
                            <th>QA优先级</th>
                            <th>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-if="group.term_bases.length === 0">
                            <td colspan="6">当前语言对暂无术语库。</td>
                          </tr>
                          <tr v-else-if="getFilteredTermBaseRows(group).length === 0">
                            <td colspan="6">没有匹配的术语库。</td>
                          </tr>
                          <tr v-for="row in getFilteredTermBaseRows(group)" :key="row.id">
                            <td>
                              <span class="term-settings__name">{{ row.name }}</span>
                              <span class="term-settings__meta">{{ row.entry_count }} 条术语</span>
                            </td>
                            <td>
                              <label class="term-settings__toggle">
                                <input
                                  type="checkbox"
                                  :checked="row.enabled"
                                  :aria-label="`启用 ${row.name}`"
                                  @change="toggleTermBaseSetting(row, group, 'enabled', $event)"
                                />
                                <span aria-hidden="true" />
                              </label>
                            </td>
                            <td>
                              <label class="term-settings__toggle">
                                <input
                                  type="checkbox"
                                  :checked="row.writable"
                                  :aria-label="`写入 ${row.name}`"
                                  @change="toggleTermBaseSetting(row, group, 'writable', $event)"
                                />
                                <span aria-hidden="true" />
                              </label>
                            </td>
                            <td>
                              <label class="term-settings__toggle">
                                <input
                                  type="checkbox"
                                  :checked="row.qa"
                                  :aria-label="`术语库 QA ${row.name}`"
                                  @change="toggleTermBaseSetting(row, group, 'qa', $event)"
                                />
                                <span aria-hidden="true" />
                              </label>
                            </td>
                            <td>
                              <div class="term-settings__priority">
                                <strong v-if="row.qa">{{ row.qa_priority }}</strong>
                                <span v-else>未启用</span>
                                <button
                                  class="button button--icon"
                                  type="button"
                                  :disabled="!row.qa || row.qa_priority === 1"
                                  title="提高优先级"
                                  aria-label="提高优先级"
                                  @click="moveTermBaseQAPriority(group, row, -1)"
                                >
                                  <ArrowUp :size="14" />
                                </button>
                                <button
                                  class="button button--icon"
                                  type="button"
                                  :disabled="!row.qa || row.qa_priority === group.term_bases.filter((item) => item.qa).length"
                                  title="降低优先级"
                                  aria-label="降低优先级"
                                  @click="moveTermBaseQAPriority(group, row, 1)"
                                >
                                  <ArrowDown :size="14" />
                                </button>
                              </div>
                            </td>
                            <td>
                              <button
                                class="button tm-settings__file-button pd-icon-action"
                                type="button"
                                :title="`增量导入 ${row.name}`"
                                :aria-label="`增量导入 ${row.name}`"
                                @click="openTermIncrementalImport(group, row)"
                              >
                                <Upload :size="14" />
                              </button>
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </section>
                </div>
              </div>
            </section>

            <section v-show="activeProjectSettingsSection === 'automation'" id="project-settings-automation" class="pd-settings-section">
              <header class="pd-settings-section-head">
                <div class="pd-settings-section-head__copy">
                  <span class="pd-settings-section-icon">
                    <Sparkles :size="17" />
                  </span>
                  <div>
                    <div class="section-title section-title--tight">自动应用与锁定</div>
                    <p class="panel-subtitle">管理重复句段同步、内置自动本地化，以及后续可接入的锁定规则。</p>
                  </div>
                </div>
              </header>

              <div class="pd-settings-section-body automation-settings">
                <section class="automation-settings__group">
                  <h3>自动应用</h3>
                  <label
                    class="automation-settings__check is-connected"
                    :class="{ 'is-busy': projectSyncToggleLoading }"
                    :title="projectSyncToggleTitle"
                  >
                    <input
                      type="checkbox"
                      :checked="projectSyncAllEnabled"
                      :indeterminate.prop="projectSyncMixed"
                      :disabled="projectSyncToggleDisabled"
                      @change="handleProjectSyncToggle"
                    />
                    <span>自动同步重复句段</span>
                    <small v-if="projectSyncStatusLabel">{{ projectSyncStatusLabel }}</small>
                  </label>
                  <label class="automation-settings__check is-placeholder" title="内置自动本地化占位，后续接入项目级保存。">
                    <input type="checkbox" checked disabled />
                    <span>自动替换</span>
                    <CircleHelp :size="13" />
                  </label>
                  <div class="automation-settings__children">
                    <label
                      v-for="option in autoLocalizationOptions"
                      :key="option"
                      class="automation-settings__check is-placeholder"
                    >
                      <input type="checkbox" checked disabled />
                      <span>{{ option }}</span>
                    </label>
                  </div>
                  <label class="automation-settings__check is-placeholder" title="沿用导入和导出中的自动编号/排序处理，占位展示。">
                    <input type="checkbox" checked disabled />
                    <span>自动排序</span>
                  </label>
                </section>

                <section class="automation-settings__group">
                  <h3>锁定</h3>
                  <div class="automation-settings__lock-grid">
                    <label
                      v-for="option in lockPlaceholderOptions"
                      :key="option"
                      class="automation-settings__check is-placeholder"
                    >
                      <input type="checkbox" disabled />
                      <span>{{ option }}</span>
                    </label>
                  </div>
                </section>
              </div>
            </section>

            <section v-show="activeProjectSettingsSection === 'quality-qa'" id="project-settings-quality-qa" class="pd-settings-section">
              <header class="pd-settings-section-head">
                <div class="pd-settings-section-head__copy">
                  <span class="pd-settings-section-icon">
                    <ShieldCheck :size="17" />
                  </span>
                  <div>
                    <div class="section-title section-title--tight">质量保证</div>
                    <p class="panel-subtitle">启用拼写/语法 QA 后，译文保存会后台检查，工作台会用红色波浪线提示问题。</p>
                  </div>
                </div>
                <button
                  class="button button--primary pd-settings-save"
                  type="button"
                  :disabled="savingQualityQASettings || loadingQualityQASettings"
                  @click="saveProjectQualityQASettings"
                >
                  <Loader2 v-if="savingQualityQASettings" class="lucide-spin" :size="14" />
                  <Settings2 v-else :size="14" />
                  {{ savingQualityQASettings ? '保存中' : '保存质量设置' }}
                </button>
              </header>

              <div class="pd-settings-section-body quality-qa-settings">
                <StateView
                  v-if="loadingQualityQASettings"
                  kind="loading"
                  title="正在加载质量保证设置"
                  message="正在读取 LanguageTool 配置和项目语言支持情况。"
                />
                <p v-else-if="qualityQASettingsError" class="form-message is-error">{{ qualityQASettingsError }}</p>
                <div v-else class="quality-qa-settings__content">
                  <div class="quality-qa-settings__rule-table-wrap">
                    <table class="quality-qa-settings__rule-table">
                      <thead>
                        <tr>
                          <th>序号</th>
                          <th class="quality-qa-settings__check-cell">
                            <label class="quality-qa-settings__rule-check" title="全选/取消全选">
                              <input
                                type="checkbox"
                                :checked="allQualityQARulesEnabled"
                                :indeterminate.prop="partiallyEnabledQualityQARules"
                                :disabled="savingQualityQASettings || loadingQualityQASettings"
                                aria-label="全选质量保证规则"
                                @change="toggleAllQualityQARules"
                              />
                            </label>
                          </th>
                          <th>规则</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr
                          v-for="(rule, index) in qualityQARules"
                          :key="rule.key"
                          :data-rule-key="rule.key"
                          :data-testid="`quality-qa-rule-${rule.key}`"
                        >
                          <td>{{ index + 1 }}</td>
                          <td class="quality-qa-settings__check-cell">
                            <label class="quality-qa-settings__rule-check">
                              <input
                                v-model="qualityQADraft.rules[rule.key]"
                                type="checkbox"
                                :data-testid="`quality-qa-rule-toggle-${rule.key}`"
                                :disabled="savingQualityQASettings || loadingQualityQASettings"
                                :aria-label="`启用${rule.label}`"
                              />
                            </label>
                          </td>
                          <td>{{ rule.label }}</td>
                        </tr>
                        <tr
                          v-for="(rule, index) in qualityQAPlaceholderRules"
                          :key="rule.key"
                          class="is-placeholder"
                          :data-rule-key="rule.key"
                          :data-testid="`quality-qa-rule-${rule.key}`"
                          title="占位展示，后端暂未接入。"
                        >
                          <td>{{ qualityQARules.length + index + 1 }}</td>
                          <td class="quality-qa-settings__check-cell">
                            <label class="quality-qa-settings__rule-check">
                              <input
                                type="checkbox"
                                disabled
                                :data-testid="`quality-qa-rule-toggle-${rule.key}`"
                                :aria-label="`占位${rule.label}`"
                              />
                            </label>
                          </td>
                          <td>
                            <span class="quality-qa-settings__rule-text">
                              <template v-if="typeof rule.percent === 'number'">
                                <span>{{ rule.label }}</span>
                                <input
                                  class="quality-qa-settings__inline-number"
                                  type="number"
                                  :value="rule.percent"
                                  disabled
                                />
                                <span>{{ rule.suffix }}</span>
                              </template>
                              <template v-else>{{ rule.label }}</template>
                            </span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>


                  <div class="quality-qa-settings__status-grid">
                    <div class="quality-qa-settings__status-item">
                      <span>规则状态</span>
                      <strong :class="qualityQARuleStatusClass">{{ qualityQARuleStatusText }}</strong>
                      <small>{{ qualityQARuleStatusHint }}</small>
                    </div>
                    <div class="quality-qa-settings__status-item">
                      <span>LanguageTool</span>
                      <strong :class="qualityQASettings?.languagetool_configured ? 'is-ok' : 'is-warn'">
                        {{ qualityQASettings?.languagetool_configured ? '已配置' : '未配置' }}
                      </strong>
                    </div>
                    <div class="quality-qa-settings__status-item">
                      <span>检查方式</span>
                      <strong>后台自动检查</strong>
                    </div>
                  </div>

                  <div class="quality-qa-settings__language-list">
                    <div class="quality-qa-settings__language-head">
                      <strong>项目目标语言</strong>
                      <span>{{ qualityQASettings?.target_languages.length || 0 }} 种</span>
                    </div>
                    <div v-if="!qualityQASettings || qualityQASettings.target_languages.length === 0" class="empty-state">
                      当前项目还没有可检查的目标语言文件。
                    </div>
                    <div v-else class="quality-qa-settings__language-grid">
                      <span
                        v-for="item in qualityQASettings.target_languages"
                        :key="item.language"
                        class="quality-qa-settings__language-chip"
                        :class="{ 'is-supported': item.supported, 'is-unsupported': !item.supported }"
                      >
                        <strong>{{ getLanguageLabel(item.language) }}</strong>
                        <small>{{ item.file_count }} 个文件 · {{ item.supported ? item.languagetool_code : '暂不支持' }}</small>
                      </span>
                    </div>
                  </div>

                  <p v-if="spellingGrammarQAEnabled && !qualityQASettings?.languagetool_configured" class="form-message is-error">
                    已启用检查，但后端尚未配置 LANGUAGETOOL_BASE_URL，保存译文不会被阻塞，QA 检查会自动跳过。
                  </p>
                </div>
              </div>
            </section>
            <section v-show="activeProjectSettingsSection === 'term-qa'" id="project-settings-term-qa" class="pd-settings-section">
              <header class="pd-settings-section-head">
                <div class="pd-settings-section-head__copy">
                  <span class="pd-settings-section-icon">
                    <ShieldCheck :size="17" />
                  </span>
                  <div>
                    <div class="section-title section-title--tight">术语 QA 报告</div>
                    <p class="panel-subtitle">检查原文命中的 QA 术语是否在译文中使用了对应译文。</p>
                  </div>
                </div>
                <div class="pd-settings-actions term-qa-report__actions">
                  <button
                    class="button button--primary"
                    type="button"
                    :disabled="generatingTermQAReport"
                    @click="generateProjectTermQAReport"
                  >
                    <Loader2 v-if="generatingTermQAReport" class="lucide-spin" :size="14" />
                    <ShieldCheck v-else :size="14" />
                    {{ generatingTermQAReport ? '生成中' : '生成术语 QA 报告' }}
                  </button>
                  <button
                    class="button"
                    type="button"
                    :disabled="!termQAReport || downloadingTermQAReport"
                    @click="downloadTermQAReport(termQAReport)"
                  >
                    <Loader2 v-if="downloadingTermQAReport" class="lucide-spin" :size="14" />
                    <Download v-else :size="14" />
                    导出 XLSX
                  </button>
                </div>
              </header>

              <div class="pd-settings-section-body term-qa-report">
                <div v-if="termQAReport" class="term-qa-report__summary">
              <span>检查文件：{{ termQAReport.total_files }}</span>
              <span>检查句段：{{ termQAReport.checked_segments }}</span>
              <span>总问题数：{{ termQAReport.issue_count }}</span>
              <span>待处理：{{ termQAReport.active_issue_count }}</span>
              <span>已忽略：{{ termQAReport.ignored_count }}</span>
            </div>

            <div v-if="termQAReport && termQAReport.items.length === 0" class="empty-state">
              未发现术语不一致问题。
            </div>
            <div v-else-if="termQAReport" class="term-qa-report__table-wrap">
              <table class="term-qa-report__table">
                <thead>
                  <tr>
                    <th>文件</th>
                    <th>句段</th>
                    <th>原文术语</th>
                    <th>期望译文</th>
                    <th>当前译文</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in termQAReport.items.slice(0, 50)" :key="item.id">
                    <td>{{ item.file_name }}</td>
                    <td>{{ item.sentence_id }}</td>
                    <td>{{ item.source_term }}</td>
                    <td>{{ item.expected_target_term }}</td>
                    <td>{{ item.target_text || getPlaceholder() }}</td>
                    <td>{{ item.ignored ? '已忽略' : '待处理' }}</td>
                  </tr>
                </tbody>
              </table>
              <p v-if="termQAReport.items.length > 50" class="hint-text">
                已显示前 50 条，完整报告请导出 XLSX。
              </p>
            </div>
              </div>
            </section>
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'issues'" class="panel">
        <div class="pd-panel-head">
          <div class="pd-panel-head__copy">
            <div class="section-title section-title--tight">{{ t('issueMarker.list.title') }}</div>
            <p class="panel-subtitle">
              {{ t('issueMarker.list.description') }}
            </p>
          </div>
          <button
            class="button button--primary"
            type="button"
            :disabled="!canOpenProjectIssueDialog"
            @click="openProjectIssueDialog"
          >
            <Flag :size="14" />
            {{ t('issueMarker.actions.open') }}
          </button>
        </div>

        <div class="issue-summary">
          <span>{{ t('issueMarker.list.openCount', { count: openIssueCount }) }}</span>
          <span>{{ t('issueMarker.list.totalCount', { count: issueMarkers.length }) }}</span>
        </div>

        <div v-if="issueMarkers.length === 0" class="empty-state issue-empty">
          {{ t('issueMarker.list.empty') }}
        </div>

        <div v-else class="issue-list">
          <article
            v-for="marker in issueMarkers"
            :key="marker.id"
            class="issue-item"
            :class="`issue-item--${marker.status}`"
          >
            <div class="issue-item__main">
              <div class="issue-item__head">
                <span class="issue-status" :class="`issue-status--${marker.status}`">
                  {{ getIssueStatusLabel(marker.status) }}
                </span>
                <strong>{{ marker.title }}</strong>
              </div>
              <p class="issue-item__description">{{ marker.description }}</p>
              <div class="issue-item__meta">
                <span>{{ marker.file_record_name ? t('issueMarker.list.fileScope', { name: marker.file_record_name }) : t('issueMarker.list.projectScope') }}</span>
                <span>{{ getIssueCategoryLabel(marker.category) }}</span>
                <span>{{ getIssueSeverityLabel(marker.severity) }}</span>
                <span>{{ t('issueMarker.list.reporter') }}：{{ marker.reporter_name || getPlaceholder() }}</span>
                <span>{{ t('issueMarker.list.createdAt') }}：{{ formatDateText(marker.created_at) }}</span>
              </div>
            </div>
            <button
              class="button issue-item__action"
              type="button"
              :disabled="updatingIssueId === marker.id"
              @click="setIssueStatus(marker, marker.status === 'open' ? 'resolved' : 'open')"
            >
              <Loader2 v-if="updatingIssueId === marker.id" class="lucide-spin" :size="14" />
              <Check v-else-if="marker.status === 'open'" :size="14" />
              <RotateCcw v-else :size="14" />
              {{ marker.status === 'open' ? t('issueMarker.actions.resolve') : t('issueMarker.actions.reopen') }}
            </button>
          </article>
        </div>
      </section>

      <section v-if="activeTab === 'assignments'" class="panel">
        <div class="pd-panel-head">
          <div class="pd-panel-head__copy">
            <div class="section-title section-title--tight">指派记录</div>
            <p class="panel-subtitle">查看当前项目的指派、授权和取消记录。</p>
          </div>
          <button class="button" type="button" :disabled="assignmentEventsLoading" @click="loadAssignmentEvents">
            <Loader2 v-if="assignmentEventsLoading" class="lucide-spin" :size="14" />
            <RotateCcw v-else :size="14" />
            刷新
          </button>
        </div>

        <div v-if="assignmentEventsLoading" class="empty-state issue-empty">
          正在加载指派记录...
        </div>
        <div v-else-if="assignmentEvents.length === 0" class="empty-state issue-empty">
          暂无指派记录
        </div>
        <div v-else class="assignment-event-list">
          <article v-for="event in assignmentEvents" :key="event.id" class="assignment-event-item">
            <div class="assignment-event-item__main">
              <strong>{{ getAssignmentEventActionLabel(event.action) }}</strong>
              <span>
                {{ event.file_record_name || '项目级' }} ·
                {{ getAssigneeDisplayName(event.assignee) || '--' }}
              </span>
            </div>
            <div class="assignment-event-item__meta">
              <span>操作人：{{ getAssigneeDisplayName(event.actor) || '--' }}</span>
              <span>{{ formatDateText(event.created_at) }}</span>
            </div>
          </article>
        </div>
      </section>

      <section v-if="activeTab === 'views'" class="panel">
        <div class="pd-panel-head">
          <div class="pd-panel-head__copy">
            <div class="section-title section-title--tight">{{ t('projectDetail.mergeViews.title') }}</div>
            <p class="panel-subtitle">{{ t('projectDetail.mergeViews.description') }}</p>
          </div>
          <button class="button" type="button" :disabled="loadingMergeViews" @click="loadMergeViews">
            <Loader2 v-if="loadingMergeViews" class="lucide-spin" :size="14" />
            <RotateCcw v-else :size="14" />
            {{ t('common.actions.refresh') }}
          </button>
        </div>

        <div v-if="loadingMergeViews" class="empty-state issue-empty">
          {{ t('projectDetail.mergeViews.loading') }}
        </div>
        <div v-else-if="mergeViews.length === 0" class="empty-state issue-empty">
          {{ t('projectDetail.mergeViews.empty') }}
        </div>
        <div v-else class="pd-merge-view-list">
          <article v-for="view in mergeViews" :key="view.id" class="pd-merge-view-item">
            <div class="pd-merge-view-item__main">
              <div class="pd-merge-view-item__head">
                <strong>{{ view.name }}</strong>
                <span>{{ getMergeViewMetaText(view) }}</span>
              </div>
              <div class="pd-merge-view-item__files">
                <span
                  v-for="fileName in getMergeViewFileNames(view)"
                  :key="`${view.id}-${fileName}`"
                  :title="fileName"
                >
                  {{ fileName }}
                </span>
              </div>
            </div>
            <div class="pd-merge-view-item__actions">
              <button
                class="button"
                type="button"
                :disabled="view.can_open === false"
                @click="openMergeView(view)"
              >
                <FolderOpen :size="14" />
                {{ t('projectDetail.mergeViews.open') }}
              </button>
              <button
                v-if="canManageMergeView(view)"
                class="button"
                type="button"
                :disabled="mergeViewActionId === view.id"
                @click="openRenameMergeViewDialog(view)"
              >
                {{ t('projectDetail.mergeViews.rename') }}
              </button>
              <button
                v-if="canManageMergeView(view)"
                class="button button--danger"
                type="button"
                :disabled="mergeViewActionId === view.id"
                @click="deleteSavedMergeView(view)"
              >
                <Loader2 v-if="mergeViewActionId === view.id" class="lucide-spin" :size="14" />
                <Trash2 v-else :size="14" />
                {{ t('projectDetail.mergeViews.delete') }}
              </button>
            </div>
          </article>
        </div>
      </section>

      <section v-if="activeTab === 'files'" class="panel pd-files-panel">
        <div class="pd-panel-head">
          <div class="pd-panel-head__copy">
            <div class="section-title section-title--tight pd-files-title">
              <span>{{ t('projectDetail.files.title') }}</span>
              <span class="pd-files-count">
                显示 {{ filteredTableRows.length }} / {{ tableRows.length }} 个文件
                <template v-if="selectedFileIds.size > 0"> · 已选 {{ selectedFileIds.size }}</template>
              </span>
            </div>
            <p class="panel-subtitle">{{ t('projectDetail.files.description') }}</p>
          </div>
        </div>

        <div class="table-toolbar pd-toolbar">
          <div class="table-toolbar__left pd-toolbar__left">
            <button
              v-if="canUploadProjectFiles"
              class="button button--primary pd-toolbar-primary"
              data-testid="project-upload-open"
              type="button"
              :disabled="!canOpenUploadModal"
              :title="uploadButtonTitle || t('projectDetail.files.actions.upload')"
              :aria-label="t('projectDetail.files.actions.upload')"
              @click="openUploadDialog"
            >
              <Upload :size="14" />
              {{ t('projectDetail.files.actions.upload') }}
            </button>
            <div class="pd-file-selection" @click.stop>
              <button
                class="button pd-file-selection__trigger"
                type="button"
                :disabled="filteredTableRows.length === 0"
                :title="filteredTableRows.length === 0 ? '当前没有可选择的文件' : '选择文件范围或全部文件'"
                aria-haspopup="menu"
                :aria-expanded="showFileSelectionMenu"
                @click="toggleFileSelectionMenu"
              >
                <Check :size="14" />
                <span>选择</span>
                <strong v-if="selectedFileIds.size > 0">{{ selectedFileIds.size }}</strong>
                <ChevronDown :size="12" />
              </button>

              <div v-if="showFileSelectionMenu" class="pd-file-selection__menu" role="menu">
                <div class="pd-file-selection__head">
                  <strong>批量选择</strong>
                  <span>已选 {{ selectedFileIds.size }} 个</span>
                </div>

                <button
                  class="pd-file-selection__item"
                  type="button"
                  role="menuitem"
                  :disabled="pagedRows.length === 0"
                  @click="selectCurrentFilePageFromMenu"
                >
                  <span>选中当前页</span>
                  <small>
                    第 {{ currentPageFileRangeStart || 0 }}-{{ currentPageFileRangeEnd || 0 }} 项 ·
                    已选 {{ selectedCurrentPageFileCount }} / {{ pagedRows.length }}
                  </small>
                </button>

                <button
                  class="pd-file-selection__item"
                  type="button"
                  role="menuitem"
                  :class="{ 'is-active': allFilteredFilesSelected }"
                  :disabled="filteredTableRows.length === 0"
                  @click="selectAllFilteredFiles"
                >
                  <span>{{ fileSelectionAllLabel }}</span>
                  <small>
                    跨分页生效 · 已选 {{ selectedFilteredFileCount }} / {{ filteredTableRows.length }}
                  </small>
                </button>

                <div class="pd-file-selection__range" role="group" aria-label="按序号范围选择">
                  <div class="pd-file-selection__range-head">
                    <strong>选中范围</strong>
                    <span>{{ fileSelectionRangeHint }}</span>
                  </div>
                  <div class="pd-file-selection__range-controls">
                    <label>
                      <span>从</span>
                      <input
                        v-model="fileSelectionRangeStart"
                        type="number"
                        min="1"
                        :max="Math.max(filteredTableRows.length, 1)"
                        inputmode="numeric"
                        @keydown.enter.prevent="selectFileRangeFromMenu"
                      >
                    </label>
                    <label>
                      <span>到</span>
                      <input
                        v-model="fileSelectionRangeEnd"
                        type="number"
                        min="1"
                        :max="Math.max(filteredTableRows.length, 1)"
                        inputmode="numeric"
                        @keydown.enter.prevent="selectFileRangeFromMenu"
                      >
                    </label>
                    <button
                      class="button pd-file-selection__range-submit"
                      type="button"
                      :disabled="filteredTableRows.length === 0"
                      @click="selectFileRangeFromMenu"
                    >
                      选中
                    </button>
                  </div>
                  <p v-if="fileSelectionRangeError" class="pd-file-selection__error">
                    {{ fileSelectionRangeError }}
                  </p>
                </div>

                <button
                  v-if="selectedFileIds.size > 0"
                  class="pd-file-selection__clear"
                  type="button"
                  role="menuitem"
                  @click="clearSelectedProjectFiles"
                >
                  清空选择
                </button>
              </div>
            </div>
            <div class="pd-toolbar-action-strip" aria-label="文件批量操作">
              <div class="pd-toolbar-action-group pd-toolbar-action-group--featured" aria-label="智能处理">
                <button
                  class="button pd-toolbar-action-button pd-toolbar-action-button--labeled pd-toolbar-action-button--accent"
                  type="button"
                  :disabled="!canOpenPreTranslate"
                  :title="preTranslateButtonTitle || t('projectDetail.preTranslate.button')"
                  :aria-label="t('projectDetail.preTranslate.button')"
                  @click="openPreTranslateDialog"
                >
                  <Sparkles :size="15" />
                  <span>{{ t('projectDetail.preTranslate.button') }}</span>
                </button>
                <button
                  class="button pd-toolbar-action-button pd-toolbar-action-button--labeled"
                  type="button"
                  :disabled="!canOpenTermExtraction"
                  :title="termExtractionButtonTitle || t('projectDetail.termExtraction.button')"
                  :aria-label="t('projectDetail.termExtraction.button')"
                  @click="openTermExtractionDialog"
                >
                  <BookOpen :size="15" />
                  <span>{{ t('projectDetail.termExtraction.button') }}</span>
                </button>
              </div>

              <div class="pd-toolbar-action-group" aria-label="文件操作">
                <button
                  v-if="canManageProject && !authStore.isExternalTranslator"
                  class="button pd-toolbar-action-button pd-toolbar-action-button--labeled pd-toolbar-action-button--variant-copy"
                  :class="{
                    'pd-toolbar-action-button--british-copy': englishVariantCopyDirection !== 'to-american',
                    'pd-toolbar-action-button--american-copy': englishVariantCopyDirection === 'to-american',
                  }"
                  data-testid="create-english-variant-copy"
                  type="button"
                  :disabled="!canCreateEnglishVariantCopy"
                  :title="englishVariantCopyDisabledReason || englishVariantCopyLabel"
                  :aria-label="englishVariantCopyLabel"
                  @click="createEnglishVariantCopy"
                >
                  <Loader2 v-if="creatingEnglishVariantCopy" class="lucide-spin" :size="15" />
                  <ReplaceAll v-else :size="15" />
                  <span>{{ englishVariantCopyShortLabel }}</span>
                </button>
                <button
                  v-if="canManageProject"
                  class="button pd-toolbar-action-button pd-toolbar-action-button--labeled pd-toolbar-action-button--template-copy"
                  data-testid="duplicate-file-template"
                  type="button"
                  :disabled="!canDuplicateTemplate"
                  :title="duplicateTemplateButtonTitle || t('projectDetail.files.actions.duplicateTemplate')"
                  :aria-label="t('projectDetail.files.actions.duplicateTemplate')"
                  @click="duplicateSelectedTemplate"
                >
                  <Loader2 v-if="duplicating" class="lucide-spin" :size="15" />
                  <Copy v-else :size="15" />
                  <span>{{ t('projectDetail.files.actions.duplicateTemplateShort') }}</span>
                </button>
                <button
                  class="button pd-toolbar-action-button pd-toolbar-action-button--labeled"
                  type="button"
                  :disabled="!canOpenProjectIssueDialog"
                  :title="t('issueMarker.actions.open')"
                  :aria-label="t('issueMarker.actions.open')"
                  @click="openProjectIssueDialog"
                >
                  <Flag :size="15" />
                  <span>{{ t('issueMarker.actions.open') }}</span>
                </button>
                <button
                  v-if="canAssignProject"
                  class="button pd-toolbar-action-button pd-toolbar-action-button--labeled"
                  type="button"
                  :disabled="!canAssignSelectedFile"
                  :title="t('projectDetail.files.actions.assign')"
                  :aria-label="t('projectDetail.files.actions.assign')"
                  @click="openAssignmentDialog()"
                >
                  <Users :size="15" />
                  <span>{{ t('projectDetail.files.actions.assign') }}</span>
                </button>
                <div class="pd-export-dropdown">
                  <button
                    class="button pd-toolbar-action-button pd-toolbar-action-button--labeled pd-toolbar-export-button"
                    data-testid="project-file-export-selected"
                    type="button"
                    :disabled="!canOpenProjectExportMenu"
                    :title="projectExportButtonTitle || (exportingFileId ? `导出中 ${exportFileProgress}%` : t('projectDetail.files.actions.export'))"
                    :aria-label="t('projectDetail.files.actions.export')"
                    aria-haspopup="menu"
                    :aria-expanded="showProjectExportMenu"
                    @click.stop="toggleProjectExportMenu"
                  >
                    <Loader2 v-if="loadingProjectExportOptions || exportingFileId" class="lucide-spin" :size="15" />
                    <Download v-else :size="15" />
                    <span>{{ t('projectDetail.files.actions.export') }}</span>
                    <ChevronDown :size="12" />
                  </button>
                  <div v-if="showProjectExportMenu" class="pd-export-menu" role="menu" @click.stop>
                    <div v-if="loadingProjectExportOptions" class="pd-export-menu__loading">
                      {{ t('projectDetail.files.actions.exportLoading') }}
                    </div>
                    <div v-else-if="projectExportOptions.length === 0" class="pd-export-menu__loading">
                      {{ t('projectDetail.files.actions.exportNoOptions') }}
                    </div>
                    <template v-else>
                      <div
                        v-for="group in groupedProjectExportOptions"
                        :key="group.id"
                        class="pd-export-menu__group"
                      >
                        <div class="pd-export-menu__group-title">{{ group.label }}</div>
                        <button
                          v-for="option in group.options"
                          :key="option.id"
                          class="pd-export-menu__item"
                          type="button"
                          :disabled="Boolean(exportingFileId)"
                          @click="exportSelectedProjectFiles(option.id)"
                        >
                          <span class="pd-export-menu__item-head">
                            <span class="pd-export-menu__item-name">{{ option.name }}</span>
                            <span
                              v-if="getExportOptionExtensionLabel(option)"
                              class="pd-export-menu__item-ext"
                            >
                              {{ getExportOptionExtensionLabel(option) }}
                            </span>
                          </span>
                          <span class="pd-export-menu__item-desc">{{ option.description }}</span>
                        </button>
                      </div>
                      <button
                        v-if="canExportSelectedProjectFilesAsZip"
                        class="pd-export-menu__item"
                        type="button"
                        :disabled="Boolean(exportingFileId)"
                        @click="exportSelectedProjectFilesAsZip"
                      >
                        <span class="pd-export-menu__item-head">
                          <span class="pd-export-menu__item-name">{{ t('projectDetail.files.actions.exportZip') }}</span>
                          <span class="pd-export-menu__item-ext">ZIP</span>
                        </span>
                        <span class="pd-export-menu__item-desc">{{ t('projectDetail.files.actions.exportZipDescription') }}</span>
                      </button>
                    </template>
                  </div>
                </div>
                <button
                  class="button pd-toolbar-action-button pd-toolbar-action-button--labeled"
                  type="button"
                  :disabled="!canOpenMergeViewDialog"
                  :title="mergeOpenButtonTitle || t('projectDetail.files.actions.mergeOpen')"
                  :aria-label="t('projectDetail.files.actions.mergeOpen')"
                  @click="openCreateMergeViewDialog"
                >
                  <FolderOpen :size="15" />
                  <span>{{ t('projectDetail.files.actions.mergeOpen') }}</span>
                </button>
              </div>

              <div v-if="canManageProject" class="pd-toolbar-action-group pd-toolbar-action-group--reserved" aria-label="待开放操作">
                <button
                  class="button pd-toolbar-action-button"
                  type="button"
                  disabled
                  :title="`${t('projectDetail.files.actions.link')}：${t('projectDetail.common.comingSoon')}`"
                  :aria-label="t('projectDetail.files.actions.link')"
                >
                  <Link :size="15" />
                </button>
                <button
                  class="button pd-toolbar-action-button"
                  type="button"
                  disabled
                  :title="`${t('projectDetail.files.actions.glossary')}：${t('projectDetail.common.comingSoon')}`"
                  :aria-label="t('projectDetail.files.actions.glossary')"
                >
                  <BookOpen :size="15" />
                </button>
                <button
                  class="button pd-toolbar-action-button"
                  type="button"
                  disabled
                  :title="`${t('projectDetail.files.actions.modifyTaskType')}：${t('projectDetail.common.comingSoon')}`"
                  :aria-label="t('projectDetail.files.actions.modifyTaskType')"
                >
                  <Clock3 :size="15" />
                </button>
              </div>

              <div class="pd-toolbar-action-group pd-toolbar-action-group--utility" aria-label="视图设置">
                <button
                  class="button pd-toolbar-action-button"
                  type="button"
                  disabled
                  :title="`${t('projectDetail.files.actions.columns')}：${t('projectDetail.common.comingSoon')}`"
                  :aria-label="t('projectDetail.files.actions.columns')"
                >
                  <Settings2 :size="14" />
                </button>
              </div>

              <div v-if="canManageProject" class="pd-toolbar-action-group pd-toolbar-action-group--danger" aria-label="危险操作">
                <button
                  class="button pd-toolbar-action-button pd-toolbar-action-button--labeled pd-toolbar-action-button--danger"
                  data-testid="project-file-delete-selected"
                  type="button"
                  :disabled="!canDeleteSelectedProjectFiles"
                  :title="deleteSelectedFilesButtonTitle || t('projectDetail.files.actions.deleteSelected')"
                  :aria-label="t('projectDetail.files.actions.deleteSelected')"
                  @click="deleteSelectedProjectFiles"
                >
                  <Trash2 :size="15" />
                  <span>{{ t('projectDetail.files.actions.delete') }}</span>
                </button>
              </div>
            </div>
          </div>

          <div class="table-toolbar__right pd-toolbar__right">
            <div class="pd-file-filters" role="search" aria-label="文件筛选">
              <span class="pd-file-filters__lead" title="筛选文件" aria-hidden="true">
                <Filter :size="14" />
              </span>
              <label class="pd-file-filter pd-file-filter--search">
                <Search class="pd-file-filter__search-icon" :size="14" aria-hidden="true" />
                <input
                  v-model="fileSearchQuery"
                  class="pd-file-filter__input"
                  type="search"
                  placeholder="搜索文件名、语言、负责人"
                  aria-label="搜索文件"
                  @keydown.esc="fileSearchQuery = ''"
                />
                <button
                  v-if="fileSearchQuery"
                  class="pd-file-filter__clear"
                  type="button"
                  title="清空搜索"
                  aria-label="清空搜索"
                  @click="fileSearchQuery = ''"
                >
                  <X :size="13" />
                </button>
              </label>
              <select
                v-model="fileStatusFilter"
                class="pd-file-filter__select pd-file-filter__select--status"
                aria-label="状态筛选"
              >
                <option value="all">全部状态</option>
                <option v-for="option in fileStatusFilterOptions" :key="option.value" :value="option.value">
                  {{ option.label }}（{{ option.count }}）
                </option>
              </select>
              <select
                v-model="fileLanguagePairFilter"
                class="pd-file-filter__select pd-file-filter__select--language"
                aria-label="语言对筛选"
              >
                <option value="all">全部语言对</option>
                <option v-for="option in fileLanguagePairFilterOptions" :key="option.value" :value="option.value">
                  {{ option.label }}（{{ option.count }}）
                </option>
              </select>
              <select
                v-model="fileAssigneeFilter"
                class="pd-file-filter__select pd-file-filter__select--assignee"
                aria-label="负责人筛选"
              >
                <option value="all">全部负责人</option>
                <option v-for="option in fileAssigneeFilterOptions" :key="option.value" :value="option.value">
                  {{ option.label }}（{{ option.count }}）
                </option>
              </select>
              <button
                v-if="hasFileFilters"
                class="button pd-file-filter__reset"
                type="button"
                @click="resetFileFilters"
              >
                <RotateCcw :size="14" />
                清空
              </button>
            </div>
          </div>
        </div>

        <DataTable
          class="pd-file-table"
          test-id="project-file-table"
          row-test-id-prefix="project-file-row"
          :columns="columns"
          :data="pagedRows"
          :loading="loading"
          :selectable="true"
          :selected-ids="selectedFileIds"
          :sort-key="fileSortKey"
          :sort-order="fileSortOrder"
          :show-index="true"
          :index-offset="indexOffset"
          :empty-text="fileTableEmptyText"
          :row-class="getProjectFileRowClass"
          @sort="handleFileSort"
          @select="selectedFileIds = $event"
        >
          <template #filename="{ row }">
            <div class="pd-file-cell">
              <FileText class="pd-file-cell__icon" :size="18" />
              <div class="pd-file-cell__content">
                <div class="pd-file-cell__title-row">
                  <button
                    v-if="canEnterWorkbench(row)"
                    class="pd-link-button"
                    data-testid="project-file-open-workbench"
                    type="button"
                    :title="row.filename"
                    @click="openWorkbench(row)"
                  >
                    {{ row.filename }}
                  </button>
                  <span v-else class="pd-file-cell__title" :title="row.filename">{{ row.filename }}</span>
                  <span
                    v-if="getDerivedFileKind(row)"
                    class="pd-file-kind-badge"
                    :class="`pd-file-kind-badge--${getDerivedFileKind(row)}`"
                  >
                    {{ getDerivedFileKindLabel(row) }}
                  </span>
                </div>
                <span class="pd-file-cell__meta" :title="getFileMetaText(row)">{{ getFileMetaText(row) }}</span>
              </div>
            </div>
          </template>

          <template #progress="{ row }">
            <div class="pd-file-progress" :title="getFileDisplayProgressMessage(row) || undefined">
              <WorkflowProgressSummary
                compact
                :progress="getFileDisplayProgress(row)"
                :status="getFileDisplayProgressStatus(row)"
                :workflow-progress="getFileWorkflowProgress(row)"
                :label="t('common.progress.total')"
                :detail-title="t('common.progress.workflowDetail')"
              />
              <span v-if="getFileDisplayProgressMessage(row)" class="pd-file-progress__status">
                {{ getFileDisplayProgressMessage(row) }}
              </span>
            </div>
          </template>

          <template #pretranslation_progress="{ row }">
            <div class="pd-file-progress" :title="getFilePretranslationProgressMessage(row) || undefined">
              <div class="progress-bar">
                <div class="progress-bar__track">
                  <div
                    class="progress-bar__fill"
                    :class="{ 'is-complete': isProgressComplete(getFilePretranslationProgress(row)) }"
                    :style="getProgressStyle(getFilePretranslationProgress(row), getFilePretranslationProgressStatus(row))"
                  />
                </div>
                <span class="progress-bar__text">{{ getFilePretranslationProgress(row) }}%</span>
              </div>
              <span v-if="getFilePretranslationProgressMessage(row)" class="pd-file-progress__status">
                {{ getFilePretranslationProgressMessage(row) }}
              </span>
              <button
                v-if="canCancelFilePretranslation(row)"
                class="pd-file-progress__cancel"
                type="button"
                :disabled="isFilePretranslationCanceling(row)"
                @click.stop="cancelFilePretranslation(row)"
              >
                <Loader2 v-if="isFilePretranslationCanceling(row)" class="lucide-spin" :size="12" />
                <X v-else :size="12" />
                停止
              </button>
            </div>
          </template>

          <template #taskManage="{ row }">
            <div class="pd-task-cell">
              <span class="pd-assignee" :class="{ 'is-empty': !(row.assignees?.length || row.assignee) }">
                {{ getAssigneeLabel(row) }}
              </span>
              <div v-if="canAssignProject" class="pd-task-links">
                <button
                  class="pd-inline-link"
                  type="button"
                  @click.stop="openAssignmentDialog(row as ProjectFileItem)"
                >
                  {{ t('projectDetail.files.task.assign') }}
                </button>
              </div>
            </div>
          </template>

          <template #status="{ row }">
            <span class="project-status" :class="getStatusClass(row.status)">
              {{ formatStatus(row.status) }}
            </span>
          </template>

          <template #open_issue_count="{ row }">
            <button
              class="issue-badge"
              :class="{ 'is-active': Number(row.open_issue_count || 0) > 0 }"
              type="button"
              :title="t('issueMarker.actions.open')"
              @click="openFileIssueDialog(row)"
            >
              <Flag :size="13" />
              {{ Number(row.open_issue_count || 0) > 0 ? row.open_issue_count : t('common.none') }}
            </button>
          </template>

          <template #actions="{ row }">
            <div class="pd-row-actions" @click.stop>
              <div class="pd-action-menu">
                <button
                  class="data-table__actions-btn"
                  type="button"
                  :title="t('projectDetail.files.columns.actions')"
                  :aria-label="t('projectDetail.files.columns.actions')"
                  @click.stop="toggleActionMenu($event, row.id)"
                >
                  <MoreHorizontal :size="16" />
                </button>
              </div>
            </div>
          </template>
        </DataTable>

        <Pagination
          :total="filteredTableRows.length"
          :page="currentPage"
          :page-size="pageSize"
          :page-sizes="FILE_PAGE_SIZES"
          @update:page="setFilePage"
          @update:page-size="setFilePageSize"
        />
      </section>

      <section v-if="activeTab === 'stats'" class="panel">
        <div class="pd-panel-head">
          <div class="pd-panel-head__copy">
            <div class="section-title section-title--tight">{{ t('projectDetail.stats.title') }}</div>
            <p class="panel-subtitle">{{ t('projectDetail.stats.description') }}</p>
          </div>
        </div>

        <div class="table-toolbar pd-toolbar">
          <div class="table-toolbar__left pd-toolbar__left">
            <button
              class="button button--primary"
              data-testid="project-statistics-generate"
              type="button"
              :disabled="!canGenerateStatistics"
              :title="statisticsSelectedFileIds.size === 0 ? t('projectDetail.stats.selectFileFirst') : undefined"
              @click="generateDocumentStatisticsTable"
            >
              <Loader2 v-if="statisticsLoading" class="lucide-spin" :size="14" />
              <Check v-else :size="14" />
              {{ statisticsLoading ? t('projectDetail.stats.generating') : t('projectDetail.stats.generate') }}
            </button>
            <button
              class="button"
              type="button"
              :disabled="statisticsLoading || (statisticsSelectedFileIds.size === 0 && statisticsResultFileIds.size === 0 && !activeStatisticsReportId)"
              @click="clearDocumentStatisticsTable"
            >
              <RotateCcw :size="14" />
              {{ t('projectDetail.stats.clear') }}
            </button>
          </div>
          <div class="table-toolbar__right pd-toolbar__right">
            <label class="pd-statistics-report-picker">
              <span>{{ t('projectDetail.stats.historyLabel') }}</span>
              <select
                v-model="activeStatisticsReportId"
                class="field__control pd-statistics-report-picker__select"
                :disabled="statisticsReportsLoading || statisticsReports.length === 0"
                @change="selectDocumentStatisticsReport(activeStatisticsReportId)"
              >
                <option value="">{{ t('projectDetail.stats.historyPlaceholder') }}</option>
                <option v-for="report in statisticsReports" :key="report.id" :value="report.id">
                  {{ formatStatisticsReportOption(report) }}
                </option>
              </select>
            </label>
            <button
              class="button"
              type="button"
              :disabled="statisticsReportsLoading"
              @click="loadDocumentStatisticsReports"
            >
              <Loader2 v-if="statisticsReportsLoading" class="lucide-spin" :size="14" />
              <RotateCcw v-else :size="14" />
              {{ statisticsReportsLoading ? t('projectDetail.stats.loadingReports') : t('projectDetail.stats.refreshReports') }}
            </button>
            <span class="pd-statistics-selection">
              {{ t('projectDetail.stats.selectedCount', { count: statisticsSelectedFiles.length }) }}
            </span>
          </div>
        </div>

        <DataTable
          class="pd-file-table pd-statistics-file-table"
          test-id="project-statistics-file-table"
          row-test-id-prefix="project-statistics-file-row"
          :columns="statisticsFileColumns"
          :data="tableRows"
          :loading="loading || statisticsLoading || statisticsReportsLoading"
          :selectable="true"
          :selected-ids="statisticsSelectedFileIds"
          :show-index="true"
          :empty-text="t('projectDetail.files.empty')"
          @select="updateStatisticsSelectedFileIds"
        >
          <template #filename="{ row }">
            <div class="pd-file-cell">
              <FileText class="pd-file-cell__icon" :size="18" />
              <div class="pd-file-cell__content">
                <span class="pd-file-cell__title" :title="row.filename">{{ row.filename }}</span>
                <span class="pd-file-cell__meta">{{ formatBytes(row.file_size_bytes) }}</span>
              </div>
            </div>
          </template>

          <template #source_language="{ row }">
            <span>{{ row.source_language ? getLanguageLabel(row.source_language) : getPlaceholder() }}</span>
          </template>

          <template #target_language="{ row }">
            <span>{{ row.target_language ? getLanguageLabel(row.target_language) : getPlaceholder() }}</span>
          </template>

          <template #status="{ row }">
            <span class="project-status" :class="getStatusClass(row.status)">
              {{ formatStatus(row.status) }}
            </span>
          </template>

          <template #statistics_status="{ row }">
            <span class="project-status" :class="getStatisticsStatusClass(getStatisticsForFile(row))">
              {{ getStatisticsSourceLabel(getStatisticsForFile(row)) }}
            </span>
          </template>
        </DataTable>

        <div v-if="statisticsResultRows.length > 0" class="pd-statistics-result">
          <div v-if="activeStatisticsReport" class="pd-statistics-report-meta">
            <span>{{ t('projectDetail.stats.reportCreatedAt') }}：{{ formatDateText(activeStatisticsReport.created_at) }}</span>
            <span>{{ t('projectDetail.stats.reportCreator') }}：{{ activeStatisticsReport.created_by_name || getPlaceholder() }}</span>
          </div>
          <div class="pd-statistics-summary">
            <div class="pd-statistics-summary__item">
              <span>{{ t('projectDetail.stats.summary.files') }}</span>
              <strong>{{ formatStatisticNumber(activeStatisticsReport?.total_files ?? statisticsResultRows.length) }}</strong>
            </div>
            <div class="pd-statistics-summary__item">
              <span>{{ t('projectDetail.stats.summary.available') }}</span>
              <strong>{{ formatStatisticNumber(statisticsAvailableCount) }}</strong>
            </div>
            <div class="pd-statistics-summary__item">
              <span>{{ t('projectDetail.stats.columns.words') }}</span>
              <strong>{{ formatStatisticNumber(statisticsTotals.words) }}</strong>
            </div>
            <div class="pd-statistics-summary__item">
              <span>{{ t('projectDetail.stats.columns.charactersWithSpaces') }}</span>
              <strong>{{ formatStatisticNumber(statisticsTotals.characters_with_spaces) }}</strong>
            </div>
            <div class="pd-statistics-summary__item">
              <span>图片数</span>
              <strong>{{ formatStatisticNumber(statisticsTotals.image_count) }}</strong>
            </div>
          </div>

          <div v-if="statisticsMatchAnalysisRows.length > 0" class="pd-statistics-match-analysis">
            <div class="pd-statistics-subhead">
              <div class="section-title section-title--tight">{{ t('projectDetail.stats.matchAnalysis.summaryTitle') }}</div>
              <span>
                统计阈值 {{ formatStatisticPercent((statisticsMatchAnalysis?.threshold ?? 0.5) * 100) }}
                · 已选记忆库 {{ statisticsMatchAnalysis?.collection_ids.length ?? 0 }} 个
                · 字数按 Word-like OpenXML 口径统计
              </span>
            </div>
            <div class="pd-statistics-grid-wrap pd-statistics-grid-wrap--match">
              <table class="pd-statistics-grid pd-statistics-match-grid">
                <thead>
                  <tr>
                    <th>{{ t('projectDetail.stats.matchAnalysis.columns.category') }}</th>
                    <th>{{ t('projectDetail.stats.matchAnalysis.columns.percent') }}</th>
                    <th>{{ t('projectDetail.stats.matchAnalysis.columns.segments') }}</th>
                    <th>{{ t('projectDetail.stats.matchAnalysis.columns.words') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="row in statisticsMatchAnalysisRows"
                    :key="row.key"
                    :class="{ 'is-total': row.is_total }"
                  >
                    <td>{{ row.label }}</td>
                    <td>{{ formatStatisticPercent(row.percent) }}</td>
                    <td>{{ formatStatisticNumber(row.segment_count) }}</td>
                    <td>{{ formatStatisticNumber(row.word_count) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div v-if="statisticsFileMatchAnalysisBlocks.length > 1" class="pd-statistics-match-analysis">
            <div class="pd-statistics-subhead">
              <div class="section-title section-title--tight">{{ t('projectDetail.stats.matchAnalysis.fileTitle') }}</div>
              <span>{{ t('projectDetail.stats.matchAnalysis.fileHint') }}</span>
            </div>
            <div class="pd-statistics-file-match-list">
              <section
                v-for="block in statisticsFileMatchAnalysisBlocks"
                :key="block.id"
                class="pd-statistics-file-match-block"
              >
                <div class="pd-statistics-file-match-head">
                  <strong :title="block.file_name">{{ block.file_name }}</strong>
                  <span>
                    {{ t('projectDetail.stats.matchAnalysis.fileMeta', {
                      words: formatStatisticNumber(block.analysis.total_words),
                      segments: formatStatisticNumber(block.analysis.total_segments),
                    }) }}
                  </span>
                </div>
                <div class="pd-statistics-grid-wrap pd-statistics-grid-wrap--match">
                  <table class="pd-statistics-grid pd-statistics-match-grid">
                    <thead>
                      <tr>
                        <th>{{ t('projectDetail.stats.matchAnalysis.columns.category') }}</th>
                        <th>{{ t('projectDetail.stats.matchAnalysis.columns.percent') }}</th>
                        <th>{{ t('projectDetail.stats.matchAnalysis.columns.segments') }}</th>
                        <th>{{ t('projectDetail.stats.matchAnalysis.columns.words') }}</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="row in block.rows"
                        :key="`${block.id}-${row.key}`"
                        :class="{ 'is-total': row.is_total }"
                      >
                        <td>{{ row.label }}</td>
                        <td>{{ formatStatisticPercent(row.percent) }}</td>
                        <td>{{ formatStatisticNumber(row.segment_count) }}</td>
                        <td>{{ formatStatisticNumber(row.word_count) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          </div>

          <div class="pd-statistics-grid-wrap">
            <table class="pd-statistics-grid">
              <thead>
                <tr>
                  <th>{{ t('projectDetail.stats.columns.file') }}</th>
                  <th>{{ t('projectDetail.stats.columns.source') }}</th>
                  <th>{{ t('projectDetail.stats.columns.pages') }}</th>
                  <th>{{ t('projectDetail.stats.columns.words') }}</th>
                  <th>{{ t('projectDetail.stats.columns.nonAsianWords') }}</th>
                  <th>{{ t('projectDetail.stats.columns.asianCharacters') }}</th>
                  <th>{{ t('projectDetail.stats.columns.characters') }}</th>
                  <th>{{ t('projectDetail.stats.columns.charactersWithSpaces') }}</th>
                  <th>{{ t('projectDetail.stats.columns.paragraphs') }}</th>
                  <th>{{ t('projectDetail.stats.columns.lines') }}</th>
                  <th>图片数</th>
                  <th>去重图片数</th>
                  <th>图表数</th>
                  <th>SmartArt 数</th>
                  <th>{{ t('projectDetail.stats.columns.license') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in statisticsResultRows" :key="row.id">
                  <td>
                    <span class="pd-statistics-file-name" :title="row.file_name">{{ row.file_name }}</span>
                  </td>
                  <td>{{ getStatisticsSourceLabel(row.statistics) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'pages')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'words')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'non_asian_words')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'asian_characters')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'characters')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'characters_with_spaces')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'paragraphs')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'lines')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'image_count')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'unique_image_count')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'chart_count')) }}</td>
                  <td>{{ formatStatisticNumber(getStatisticNumber(row.statistics, 'smartart_count')) }}</td>
                  <td>{{ getStatisticsLicenseLabel(row.statistics) }}</td>
                </tr>
              </tbody>
              <tfoot>
                <tr>
                  <th>{{ t('projectDetail.stats.total') }}</th>
                  <td>{{ getPlaceholder() }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.pages) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.words) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.non_asian_words) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.asian_characters) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.characters) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.characters_with_spaces) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.paragraphs) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.lines) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.image_count) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.unique_image_count) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.chart_count) }}</td>
                  <td>{{ formatStatisticNumber(statisticsTotals.smartart_count) }}</td>
                  <td>{{ getPlaceholder() }}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>

        <div v-else class="empty-state pd-statistics-empty">
          {{ t('projectDetail.stats.emptyResult') }}
        </div>
      </section>
    </template>

    <Teleport to="body">
      <div
        v-if="openActionMenuId && actionMenuRow"
        class="pd-action-menu__dropdown pd-action-menu__dropdown--floating"
        :style="actionMenuStyle"
        role="menu"
        @click="stopActionMenuEventBubble"
      >
        <button
          type="button"
          :disabled="!canEnterWorkbench(actionMenuRow)"
          :title="!canEnterWorkbench(actionMenuRow) ? getFileDetailHint(actionMenuRow) : undefined"
          @click="openWorkbench(actionMenuRow)"
        >
          {{ t('projectDetail.enterWorkbench') }}
        </button>
        <button
          v-if="canAssignProject"
          type="button"
          @click="openAssignmentDialog(actionMenuRow)"
        >
          {{ t('projectDetail.files.task.assign') }}
        </button>
        <button
          type="button"
          :disabled="!actionMenuRow.has_source_document || Boolean(exportingFileId)"
          :title="isProjectFileExporting(actionMenuRow, 'original') ? exportFileMessage : (!actionMenuRow.has_source_document ? t('projectDetail.common.uploadRequired') : undefined)"
          @click="exportProjectFile(actionMenuRow, 'original')"
        >
          {{ getProjectFileExportLabel(actionMenuRow, 'original') }}
        </button>
        <button
          type="button"
          :disabled="!actionMenuRow.has_source_document || Boolean(exportingFileId)"
          :title="isProjectFileExporting(actionMenuRow, 'source') ? exportFileMessage : (!actionMenuRow.has_source_document ? t('projectDetail.common.uploadRequired') : undefined)"
          @click="exportProjectFile(actionMenuRow, 'source')"
        >
          {{ getProjectFileExportLabel(actionMenuRow, 'source') }}
        </button>
        <button
          type="button"
          @click="openFileIssueDialog(actionMenuRow)"
        >
          {{ t('issueMarker.actions.open') }}
        </button>
        <button
          v-if="canManageProject"
          class="is-danger"
          type="button"
          :disabled="deleting"
          @click="deleteProjectFile(actionMenuRow)"
        >
          {{ t('projectDetail.files.actions.delete') }}
        </button>
      </div>
    </Teleport>

    <Modal
      :open="showMergeViewDialog"
      :title="mergeViewDialogMode === 'create' ? t('projectDetail.mergeViews.dialogCreateTitle') : t('projectDetail.mergeViews.dialogRenameTitle')"
      width="min(560px, calc(100vw - 32px))"
      :close-on-overlay="!savingMergeView"
      :close-on-esc="!savingMergeView"
      @close="closeMergeViewDialog"
    >
      <div class="pd-merge-view-dialog">
        <label class="field">
          <span class="field__label">{{ t('projectDetail.mergeViews.nameLabel') }} <span class="field__required">*</span></span>
          <input
            v-model="mergeViewName"
            class="field__control"
            type="text"
            maxlength="200"
            :disabled="savingMergeView"
            :placeholder="t('projectDetail.mergeViews.namePlaceholder')"
            @keydown.enter.prevent="submitMergeViewDialog"
          />
        </label>

        <div v-if="mergeViewDialogMode === 'create'" class="pd-merge-view-selected">
          <span>{{ t('projectDetail.mergeViews.selectedFiles', { count: selectedMergeViewFiles.length }) }}</span>
          <div>
            <span v-for="file in selectedMergeViewFiles" :key="file.id" :title="file.filename">
              {{ file.filename }}
            </span>
          </div>
        </div>

        <div
          v-if="mergeViewDialogMode === 'create' && selectedMergeViewLanguagePairs.length > 0"
          class="pd-merge-view-language"
          :class="{ 'is-warning': selectedMergeViewHasMixedLanguagePairs }"
        >
          <strong>{{ selectedMergeViewHasMixedLanguagePairs ? '检测到混合语言对' : '语言对一致' }}</strong>
          <div>
            <span
              v-for="pair in selectedMergeViewLanguagePairs"
              :key="`${pair.source_language || 'unset'}-${pair.target_language || 'unset'}`"
            >
              {{ formatLanguagePairSummary(pair) }}
            </span>
          </div>
          <p v-if="selectedMergeViewHasMixedLanguagePairs">
            合并视图允许混合语言对一起审阅；后续 AI、TM、术语库等批处理会按当前文件或语言对隔离，避免资源串用。
          </p>
        </div>

        <p v-if="mergeViewDialogError" class="form-message is-error">{{ mergeViewDialogError }}</p>
      </div>

      <template #footer>
        <button class="button" type="button" :disabled="savingMergeView" @click="closeMergeViewDialog">
          {{ t('common.actions.cancel') }}
        </button>
        <button class="button button--primary" type="button" :disabled="savingMergeView" @click="submitMergeViewDialog">
          <Loader2 v-if="savingMergeView" class="lucide-spin" :size="14" />
          <FolderOpen v-else-if="mergeViewDialogMode === 'create'" :size="14" />
          <Check v-else :size="14" />
          {{ savingMergeView ? t('common.actions.saving') : (mergeViewDialogMode === 'create' ? t('projectDetail.mergeViews.createAndOpen') : t('common.actions.save')) }}
        </button>
      </template>
    </Modal>

    <Modal
      :open="showProjectResourceCreateDialog"
      :title="projectResourceCreateTitle"
      :description="projectResourceCreateDescription"
      width="min(620px, calc(100vw - 32px))"
      :close-on-overlay="!projectResourceCreateSubmitting"
      :close-on-esc="!projectResourceCreateSubmitting"
      @close="closeProjectResourceCreateDialog"
    >
      <div class="upload-form form-grid-2 pd-resource-create-dialog">
        <label class="field">
          <span class="field__label">{{ projectResourceCreateNameLabel }}</span>
          <input
            v-model="projectResourceCreateForm.name"
            class="field__control"
            type="text"
            :placeholder="projectResourceCreateNamePlaceholder"
            :disabled="projectResourceCreateSubmitting"
            @keydown.enter.prevent="submitProjectResourceCreateDialog"
          />
        </label>

        <label class="field">
          <span class="field__label">说明</span>
          <input
            v-model="projectResourceCreateForm.description"
            class="field__control"
            type="text"
            placeholder="可选"
            :disabled="projectResourceCreateSubmitting"
            @keydown.enter.prevent="submitProjectResourceCreateDialog"
          />
        </label>

        <label class="field">
          <span class="field__label">源语言</span>
          <select v-model="projectResourceCreateForm.sourceLanguage" class="field__control" disabled>
            <option v-for="option in languageOptions" :key="option.code" :value="option.code">
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="field">
          <span class="field__label">目标语言</span>
          <select v-model="projectResourceCreateForm.targetLanguage" class="field__control" disabled>
            <option v-for="option in languageOptions" :key="option.code" :value="option.code">
              {{ option.label }}
            </option>
          </select>
        </label>
      </div>

      <p class="hint-text pd-resource-create-dialog__hint">
        语言对来自当前项目设置分组。创建成功后会自动启用，并排到当前列表顶部。
      </p>
      <p v-if="projectResourceCreateError" class="form-message is-error">{{ projectResourceCreateError }}</p>

      <template #footer>
        <button
          class="button"
          type="button"
          :disabled="projectResourceCreateSubmitting"
          @click="closeProjectResourceCreateDialog"
        >
          取消
        </button>
        <button
          class="button button--primary"
          type="button"
          :disabled="projectResourceCreateSubmitting || !projectResourceCreateForm.name.trim()"
          @click="submitProjectResourceCreateDialog"
        >
          <Loader2 v-if="projectResourceCreateSubmitting" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ projectResourceCreateSubmitText }}
        </button>
      </template>
    </Modal>

    <Modal
      :open="showProjectResourceLanguageDialog"
      :title="projectResourceLanguageTitle"
      :description="projectResourceLanguageDescription"
      width="min(720px, calc(100vw - 32px))"
      :close-on-overlay="!projectResourceLanguageSubmitting && !projectResourceLanguageLoading"
      :close-on-esc="!projectResourceLanguageSubmitting && !projectResourceLanguageLoading"
      @close="closeProjectResourceLanguageDialog"
    >
      <div class="project-resource-language-dialog">
        <div class="project-resource-language-target">
          <span>新库语言对</span>
          <strong>{{ projectResourceLanguageTarget.pairLabel }}</strong>
        </div>

        <label class="resource-settings-search__field project-resource-language-search">
          <Search :size="14" />
          <input
            v-model="projectResourceLanguageSearchQuery"
            type="search"
            :placeholder="`搜索${projectResourceLanguageAssetLabel}名称、说明或语言对`"
            :disabled="projectResourceLanguageLoading || projectResourceLanguageSubmitting"
          >
        </label>

        <StateView
          v-if="projectResourceLanguageLoading"
          kind="loading"
          :title="`正在加载${projectResourceLanguageAssetLabel}`"
          message="正在读取语言资产列表。"
        />
        <div v-else-if="projectResourceLanguageResources.length === 0" class="empty-state">
          当前还没有可复制的{{ projectResourceLanguageAssetLabel }}。
        </div>
        <div v-else class="project-resource-language-list">
          <label
            v-for="resource in filteredProjectResourceLanguageResources"
            :key="resource.id"
            class="project-resource-language-item"
            :class="{
              'is-selected': projectResourceLanguageSelectedId === resource.id,
              'is-current': isProjectResourceLanguageTargetMatch(resource),
            }"
          >
            <input
              v-model="projectResourceLanguageSelectedId"
              type="radio"
              name="project-resource-language-resource"
              :value="resource.id"
              :disabled="projectResourceLanguageSubmitting || isProjectResourceLanguageTargetMatch(resource)"
            >
            <span class="project-resource-language-item__body">
              <strong>{{ resource.name }}</strong>
              <span>{{ resource.description || '无说明' }}</span>
            </span>
            <span class="project-resource-language-item__meta">
              <small>{{ formatLanguagePair(resource.source_language, resource.target_language) }}</small>
              <small>{{ resource.entry_count }} {{ projectResourceLanguageEntryLabel }}</small>
            </span>
            <span v-if="isProjectResourceLanguageTargetMatch(resource)" class="tag">已匹配</span>
          </label>
          <div v-if="filteredProjectResourceLanguageResources.length === 0" class="empty-state">
            没有符合搜索条件的{{ projectResourceLanguageAssetLabel }}。
          </div>
        </div>

        <p class="hint-text">
          选择一个已有{{ projectResourceLanguageAssetLabel }}后复制，系统会创建新库并把新库条目的语言对写为当前项目分组，原库保持不变。
        </p>
        <p v-if="projectResourceLanguageError" class="form-message is-error">{{ projectResourceLanguageError }}</p>
      </div>

      <template #footer>
        <button
          class="button"
          type="button"
          :disabled="projectResourceLanguageSubmitting || projectResourceLanguageLoading"
          @click="closeProjectResourceLanguageDialog"
        >
          取消
        </button>
        <button
          class="button button--primary"
          type="button"
          :disabled="projectResourceLanguageSubmitting || projectResourceLanguageLoading || !projectResourceLanguageSelectedId"
          @click="submitProjectResourceLanguageDialog"
        >
          <Loader2 v-if="projectResourceLanguageSubmitting" class="lucide-spin" :size="14" />
          <Settings2 v-else :size="14" />
          {{ projectResourceLanguageSubmitting ? '复制中...' : '复制并启用' }}
        </button>
      </template>
    </Modal>

    <Modal
      :open="showAssignmentDialog"
      title="分配任务"
      width="min(980px, calc(100vw - 32px))"
      :close-on-overlay="!savingAssignment"
      :close-on-esc="!savingAssignment"
      @close="closeAssignmentDialog"
    >
      <div class="pd-assignment-dialog pd-assignment-dialog--project">
        <aside class="pd-assignment-panel pd-assignment-users">
          <div class="pd-assignment-panel__head">
            <div>
              <strong>译者</strong>
              <span>{{ filteredAssignableUsers.length }} / {{ assignableUsers.length }}</span>
            </div>
          </div>

          <label class="pd-assignment-search">
            <Search :size="14" />
            <input
              v-model="assignmentUserSearch"
              type="search"
              placeholder="搜索昵称、用户名或译者类型"
              :disabled="loadingAssignableUsers || loadingAssignments || savingAssignment"
            />
            <button
              v-if="assignmentUserSearch"
              class="pd-assignment-clear"
              type="button"
              aria-label="清空搜索"
              :disabled="loadingAssignableUsers || loadingAssignments || savingAssignment"
              @mouseenter="showAssignmentTooltip($event, '清空搜索')"
              @mousemove="updateAssignmentTooltipPosition"
              @mouseleave="hideAssignmentTooltip"
              @click="assignmentUserSearch = ''"
            >
              <X :size="13" />
            </button>
          </label>

          <div class="pd-assignment-filter-row">
            <select
              v-model="assignmentUserTypeFilter"
              class="pd-assignment-filter-select"
              aria-label="译者类型筛选"
              :disabled="loadingAssignableUsers || loadingAssignments || savingAssignment"
            >
              <option value="all">全部译者类型</option>
              <option value="internal">内部译者</option>
              <option value="external">外部译者</option>
            </select>
            <select
              v-model="assignmentUserStateFilter"
              class="pd-assignment-filter-select"
              aria-label="译者选择状态筛选"
              :disabled="loadingAssignableUsers || loadingAssignments || savingAssignment"
            >
              <option value="all">全部选择状态</option>
              <option value="selected">已选择</option>
              <option value="unselected">未选择</option>
            </select>
          </div>

          <p v-if="loadingAssignableUsers || loadingAssignments" class="hint-text pd-assignment-state">
            正在加载指派信息...
          </p>
          <p v-else-if="assignableUsers.length === 0" class="form-message is-error pd-assignment-state">
            暂无可分配的启用译者账号。
          </p>
          <p v-else-if="filteredAssignableUsers.length === 0" class="pd-assignment-state">
            没有符合条件的译者
          </p>
          <div v-else class="pd-assignment-user-list">
            <button
              v-for="user in filteredAssignableUsers"
              :key="user.id"
              class="pd-assignment-user"
              :class="{ 'is-active': isUserInAssignmentDraft(user.id) }"
              type="button"
              :disabled="savingAssignment"
              @mouseenter="showAssignmentTooltip($event, getAssigneeTooltip(user))"
              @mousemove="updateAssignmentTooltipPosition"
              @mouseleave="hideAssignmentTooltip"
              @click="toggleAssignmentUser(user)"
            >
              <span>{{ getAssigneeDisplayName(user) }}</span>
              <small>{{ getAssigneeSecondaryLabel(user) }}</small>
            </button>
          </div>
        </aside>

        <section class="pd-assignment-panel pd-assignment-files">
          <div class="pd-assignment-panel__head pd-assignment-panel__head--files">
            <div>
              <strong>文件 / 视图授权</strong>
              <span>已选择 {{ assignmentDrafts.length }} 位译者</span>
            </div>
            <span>{{ tableRows.length }} 个文件 · {{ assignmentMergeViews.length }} 个视图</span>
          </div>

          <div v-if="projectWorkflowSteps.length > 0" class="pd-assignment-workflow-tabs">
            <button
              v-for="step in projectWorkflowSteps"
              :key="step.id"
              class="pd-assignment-workflow-tab"
              :class="{ 'is-active': step.id === activeAssignmentWorkflowStepId }"
              type="button"
              :disabled="savingAssignment"
              @click="activeAssignmentWorkflowStepId = step.id"
            >
              {{ step.name }}
            </button>
          </div>

          <div class="pd-assignment-file-toolbar">
            <label class="pd-assignment-search pd-assignment-search--files">
              <Search :size="14" />
              <input
                v-model="assignmentFileSearch"
                type="search"
                placeholder="搜索文件名"
                :disabled="savingAssignment || activeAssignmentDrafts.length === 0"
              />
              <button
                v-if="assignmentFileSearch"
                class="pd-assignment-clear"
                type="button"
                aria-label="清空搜索"
                :disabled="savingAssignment"
                @mouseenter="showAssignmentTooltip($event, '清空搜索')"
                @mousemove="updateAssignmentTooltipPosition"
                @mouseleave="hideAssignmentTooltip"
                @click="assignmentFileSearch = ''"
              >
                <X :size="13" />
              </button>
            </label>
            <select
              v-model="assignmentFileStateFilter"
              class="pd-assignment-filter-select"
              aria-label="文件授权状态筛选"
              :disabled="savingAssignment || activeAssignmentDrafts.length === 0"
            >
              <option value="all">全部文件</option>
              <option value="checked">已授权</option>
              <option value="unchecked">未授权</option>
            </select>
          </div>

          <div v-if="activeAssignmentDrafts.length === 0" class="empty-state pd-assignment-empty">
            请选择至少一位译者。
          </div>
          <div v-else class="pd-assignment-file-groups">
            <section
              v-for="draft in activeAssignmentDrafts"
              :key="`${draft.workflow_step_id}-${draft.assignee_id}`"
              class="pd-assignment-file-group"
            >
              <div class="pd-assignment-file-group__head">
                <div class="pd-assignment-file-group__title">
                  <strong>{{ getAssignmentUserName(draft.assignee_id) }}</strong>
                  <span>
                    已授权 {{ draft.file_record_ids.size }} 个文件 · 当前筛选 {{ getFilteredAssignmentFiles(draft).length }} 个
                  </span>
                </div>
                <div class="pd-assignment-file-group__actions">
                  <button
                    class="button pd-assignment-file-action"
                    type="button"
                    :disabled="savingAssignment || getFilteredAssignmentFiles(draft).length === 0"
                    @click="selectFilteredAssignmentFiles(draft.assignee_id)"
                  >
                    <Check :size="13" />
                    全选筛选结果
                  </button>
                  <button
                    class="button pd-assignment-file-action"
                    type="button"
                    :disabled="savingAssignment || getFilteredAssignmentFiles(draft).length === 0"
                    @click="clearFilteredAssignmentFiles(draft.assignee_id)"
                  >
                    <X :size="13" />
                    清空筛选结果
                  </button>
                </div>
              </div>

              <div v-if="assignmentMergeViews.length > 0" class="pd-assignment-view-list">
                <div class="pd-assignment-view-list__head">
                  <strong>按视图授权</strong>
                  <span>勾选后自动选择视图内文件</span>
                </div>
                <label
                  v-for="view in assignmentMergeViews"
                  :key="`${draft.assignee_id}-${draft.workflow_step_id}-${view.id}`"
                  class="pd-assignment-view-option"
                  :class="{ 'is-partial': isAssignmentMergeViewPartial(draft, view) }"
                >
                  <input
                    type="checkbox"
                    :checked="isAssignmentMergeViewChecked(draft, view)"
                    :disabled="savingAssignment"
                    @change="toggleAssignmentMergeView(draft, view)"
                  />
                  <span>
                    <strong>{{ view.name }}</strong>
                    <small>{{ getAssignmentMergeViewMeta(view) }}</small>
                  </span>
                </label>
              </div>

              <p v-if="tableRows.length === 0" class="hint-text pd-assignment-mini-empty">
                当前项目暂无文件，译者会先获得项目可见性。
              </p>
              <p v-else-if="getFilteredAssignmentFiles(draft).length === 0" class="pd-assignment-mini-empty">
                没有符合条件的文件
              </p>
              <div v-else class="pd-assignment-file-list">
                <div
                  v-for="file in getFilteredAssignmentFiles(draft)"
                  :key="`${draft.assignee_id}-${file.id}`"
                  class="pd-assignment-file-option"
                >
                  <label class="pd-assignment-file-check">
                    <input
                      type="checkbox"
                      :checked="isFileCheckedForUser(draft.assignee_id, file.id)"
                      :disabled="savingAssignment"
                      @change="toggleAssignmentFile(draft.assignee_id, file.id)"
                    />
                    <span
                      @mouseenter="showAssignmentTooltip($event, file.filename)"
                      @mousemove="updateAssignmentTooltipPosition"
                      @mouseleave="hideAssignmentTooltip"
                    >
                      {{ file.filename }}
                    </span>
                  </label>
                  <div class="pd-assignment-range-controls">
                    <small>{{ getAssignmentFileSegmentCount(file.id) }} 段</small>
                    <input
                      type="number"
                      min="1"
                      inputmode="numeric"
                      placeholder="起始"
                      :value="getAssignmentRangeInputValue(draft, file.id, 'range_start')"
                      :disabled="savingAssignment || !isFileCheckedForUser(draft.assignee_id, file.id)"
                      @input="updateAssignmentFileRange(draft.assignee_id, file.id, 'range_start', getAssignmentInputValue($event))"
                    />
                    <span>-</span>
                    <input
                      type="number"
                      min="1"
                      inputmode="numeric"
                      placeholder="结束"
                      :value="getAssignmentRangeInputValue(draft, file.id, 'range_end')"
                      :disabled="savingAssignment || !isFileCheckedForUser(draft.assignee_id, file.id)"
                      @input="updateAssignmentFileRange(draft.assignee_id, file.id, 'range_end', getAssignmentInputValue($event))"
                    />
                  </div>
                </div>
              </div>
            </section>
          </div>
        </section>
      </div>
      <div
        v-if="assignmentTooltipText"
        class="pd-assignment-tooltip"
        :style="assignmentTooltipStyle"
        role="tooltip"
      >
        {{ assignmentTooltipText }}
      </div>

      <template #footer>
        <button class="button" type="button" :disabled="savingAssignment" @click="closeAssignmentDialog">
          {{ t('common.actions.cancel') }}
        </button>
        <button
          class="button button--primary"
          type="button"
          :disabled="savingAssignment || loadingAssignableUsers || loadingAssignments"
          @click="saveAssignment"
        >
          <Loader2 v-if="savingAssignment" class="lucide-spin" :size="14" />
          <Users v-else :size="14" />
          {{ savingAssignment ? t('common.actions.saving') : '保存分配' }}
        </button>
      </template>
    </Modal>

    <PreTranslateDialog
      :open="showPreTranslateDialog"
      :project-id="project?.id ?? null"
      :files="selectedProjectFiles"
      :source-language="project?.source_language ?? null"
      :target-language="project?.target_language ?? null"
      :translation-guidelines="project?.translation_guidelines ?? ''"
      @close="closePreTranslateDialog"
      @done="handlePreTranslateDone"
      @progress="handlePreTranslateProgress"
    />
    <TermExtractionDialog
      :open="showTermExtractionDialog"
      :file="selectedTermExtractionFile"
      :project-source-language="project?.source_language ?? null"
      :project-target-language="project?.target_language ?? null"
      @close="closeTermExtractionDialog"
      @done="handleTermExtractionDone"
    />
    <IssueMarkerDialog
      :open="showIssueDialog"
      :project-id="project?.id ?? null"
      :file-record-id="issueDialogTarget?.fileRecordId ?? null"
      :context-label="issueDialogTarget?.label ?? ''"
      @close="showIssueDialog = false"
      @saved="handleIssueSaved"
    />
    <ResourceImportDialog
      v-if="canManageProject"
      :open="showTMImportDialog"
      mode="tm"
      initial-tab="tm"
      title="增量导入翻译记忆库"
      :context-label="tmImportDialogContext.collectionName"
      :source-language="tmImportDialogContext.sourceLanguage"
      :target-language="tmImportDialogContext.targetLanguage"
      :fixed-tm-collection-id="tmImportDialogContext.collectionId"
      @close="showTMImportDialog = false"
      @imported="handleTMIncrementalImported"
    />
    <ResourceImportDialog
      v-if="canManageProject"
      :open="showTermImportDialog"
      mode="term"
      initial-tab="term"
      title="增量导入术语库"
      :context-label="termImportDialogContext.termBaseName"
      :source-language="termImportDialogContext.sourceLanguage"
      :target-language="termImportDialogContext.targetLanguage"
      :fixed-term-base-id="termImportDialogContext.termBaseId"
      @close="showTermImportDialog = false"
      @imported="handleTermIncrementalImported"
    />
  </div>
</template>

<style scoped>
.upload-page {
  min-height: calc(100vh - 56px);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  background: #ffffff;
}

.upload-page__topbar {
  display: flex;
  align-items: center;
  gap: 16px;
  height: 56px;
  padding: 0 16px;
  border-bottom: 1px solid #dbe3e1;
  color: var(--text-primary);
}

.upload-page__topbar strong {
  font-size: 16px;
  font-weight: 600;
}

.upload-page__back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-secondary);
  box-shadow: none;
}

.upload-page__back:hover:not(:disabled) {
  color: var(--brand-700);
}

.upload-page__divider {
  width: 1px;
  height: 18px;
  background: #dbe3e1;
}

.upload-page__main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 38%);
  min-height: 0;
}

.upload-page__workspace {
  display: grid;
  align-content: start;
  gap: 22px;
  padding: 14px 16px 28px;
}

.upload-dropzone {
  min-height: 130px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 14px;
  border: 1px dashed #ced8d5;
  border-radius: 6px;
  background: #fbfbfb;
  color: var(--text-secondary);
}

.upload-dropzone__button {
  min-width: 118px;
  box-shadow: none;
}

.upload-dropzone p,
.upload-supported {
  margin: 0;
  color: var(--text-secondary);
  font-size: 14px;
}

.upload-supported {
  text-align: center;
  font-size: 12px;
}

.upload-supported span {
  color: var(--text-muted);
}

.upload-supported button {
  padding: 0;
  border: 0;
  background: transparent;
  color: #1680db;
  box-shadow: none;
}

.upload-language-panel {
  width: min(760px, 100%);
  display: grid;
  gap: 16px;
  padding: 18px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--surface-panel);
  box-shadow: 0 6px 18px rgba(27, 55, 48, 0.04);
}

.upload-language-panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.upload-language-panel__head > div {
  min-width: 0;
}

.upload-detect-button {
  flex: 0 0 auto;
  min-height: 34px;
  padding: 0 12px;
}

.upload-language-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: 14px;
}

.upload-language-grid > .field {
  align-content: start;
  align-self: start;
  min-width: 0;
}

.upload-language-grid .field__control {
  align-self: start;
  height: 42px;
}

.upload-target-field {
  min-width: 0;
}

.upload-target-select {
  position: relative;
  min-width: 0;
}

.upload-target-select__trigger {
  width: 100%;
  min-height: 42px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 11px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--control-bg);
  color: var(--text-primary);
  text-align: left;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
}

.upload-target-select__trigger:hover:not(:disabled),
.upload-target-select.is-open .upload-target-select__trigger {
  border-color: var(--brand-700);
  background: var(--surface-panel);
}

.upload-target-select.is-open .upload-target-select__trigger {
  box-shadow: var(--focus-ring);
}

.upload-target-select__trigger svg {
  flex: 0 0 auto;
  color: var(--text-secondary);
  transition: transform 0.16s ease;
}

.upload-target-select__trigger svg.is-rotated {
  transform: rotate(180deg);
}

.upload-target-select__value {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-target-select__popover {
  position: absolute;
  z-index: 80;
  top: calc(100% + 7px);
  left: 0;
  width: 100%;
  min-width: 300px;
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: 0 16px 36px rgba(20, 45, 39, 0.18);
}

.upload-target-select__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-height: 26px;
  max-height: 64px;
  overflow-y: auto;
}

.upload-target-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  min-height: 26px;
  padding: 3px 8px;
  border: 1px solid color-mix(in srgb, var(--state-info) 28%, var(--line-soft));
  border-radius: 999px;
  background: var(--state-info-bg);
  color: var(--state-info);
  font-size: 12px;
  cursor: pointer;
}

.upload-target-chip:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.upload-target-chip span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-target-select__placeholder {
  overflow: hidden;
  color: var(--text-tertiary, #8a9491);
  font-size: 13px;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-target-select__search {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 32px;
  padding: 0 9px;
  border: 1px solid var(--line-soft);
  border-radius: 5px;
  color: var(--text-secondary);
  background: var(--surface-panel);
}

.upload-target-select__search input {
  min-width: 0;
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
}

.upload-target-select__options {
  display: grid;
  max-height: 230px;
  overflow-y: auto;
  padding: 2px 0;
}

.upload-target-option {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 5px 7px;
  border-radius: 5px;
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
}

.upload-target-option:hover {
  background: var(--surface-hover, #f3f7f6);
}

.upload-target-option.is-selected {
  background: color-mix(in srgb, var(--state-info-bg) 78%, transparent);
  color: var(--brand-800, var(--brand-700));
}

.upload-target-option input {
  margin: 0;
  accent-color: var(--brand-700);
}

.upload-target-option small {
  color: var(--text-secondary);
  font-size: 11px;
}

.upload-target-select__empty {
  margin: 0;
  padding: 12px 8px;
  color: var(--text-secondary);
  font-size: 12px;
  text-align: center;
}

.upload-target-select__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-top: 9px;
  border-top: 1px solid var(--line-soft);
  color: var(--text-secondary);
  font-size: 12px;
}

.upload-target-select__footer .button {
  min-height: 30px;
  padding: 5px 12px;
  font-size: 12px;
}

.upload-task-estimate {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin: -2px 0 0;
  padding: 9px 10px;
  border: 1px solid color-mix(in srgb, var(--state-info) 25%, transparent);
  border-radius: 6px;
  background: var(--state-info-bg);
  color: var(--state-info);
  font-size: 12px;
  line-height: 1.5;
}

.upload-task-estimate.is-error {
  border-color: color-mix(in srgb, var(--state-danger, #dc2626) 30%, transparent);
  background: var(--state-danger-bg, #fef2f2);
  color: var(--state-danger, #dc2626);
}

.upload-bound-language {
  margin: -2px 0 0;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--state-info) 28%, transparent);
  border-radius: 6px;
  background: var(--state-info-bg);
  color: var(--state-info);
  font-size: 12px;
  line-height: 1.5;
}

.upload-detect-message {
  margin: -2px 0 0;
  font-size: 13px;
  line-height: 1.5;
}

.upload-detect-message--info {
  color: var(--text-secondary);
}

.upload-detect-message--success {
  color: #047857;
}

.upload-detect-message--warning {
  color: #b45309;
}

.upload-detect-message--error {
  color: var(--danger, #dc2626);
}

.upload-file-list-wrap {
  display: grid;
  gap: 8px;
}

.upload-file-list__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.upload-file-list__summary {
  color: var(--text-secondary);
  font-size: 13px;
}

.upload-file-list__clear {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 28px;
  padding: 4px 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--state-danger, #dc2626);
  font-size: 12px;
  cursor: pointer;
}

.upload-file-list__clear:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--state-danger, #dc2626) 30%, transparent);
  background: var(--state-danger-bg, #fef2f2);
}

.upload-file-list__clear:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.upload-file-list {
  display: grid;
  gap: 8px;
  max-height: min(280px, 40vh);
  overflow-y: auto;
}

.upload-file-list__item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-1);
  color: var(--text-secondary);
  font-size: 13px;
}

.upload-file-list__item span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-page__progress {
  display: grid;
  gap: 8px;
}

.upload-page__actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid var(--line-soft);
}

.doc-settings {
  min-height: 100%;
  padding: 18px 20px 28px;
  border-left: 1px solid #dbe3e1;
  background: #ffffff;
}

.doc-settings h2 {
  margin: 0 0 24px;
  color: var(--text-primary);
  font-size: 16px;
  font-weight: 600;
}

.doc-settings__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(140px, 1fr));
  gap: 26px 44px;
}

.doc-setting-card {
  display: grid;
  align-content: start;
  gap: 11px;
  min-width: 0;
}

.doc-setting-card--excel {
  grid-row: span 2;
}

.doc-setting-card--mini {
  align-self: end;
}

.doc-type-icon,
.doc-file-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  margin-bottom: 10px;
  border-radius: 4px;
  color: #ffffff;
  font-weight: 700;
}

.doc-setting-card--word .doc-type-icon {
  background: #2b5aa8;
}

.doc-setting-card--ppt .doc-type-icon {
  background: #d94521;
}

.doc-setting-card--excel .doc-type-icon {
  background: #1b7f49;
}

.doc-file-icon {
  width: 38px;
  height: 38px;
  background: #5fa2f3;
  font-size: 12px;
}

.doc-file-icon--purple {
  background: #9b6ee8;
}

.doc-setting-card label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.3;
}

.doc-setting-card label:not(.is-muted) {
  color: #1976d2;
}

.doc-setting-card label.is-muted {
  color: #7d8d91;
}

.doc-setting-card input[type="checkbox"] {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: #4596f6;
}

.doc-color-swatches {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-left: 26px;
}

.doc-color-swatches span {
  width: 18px;
  height: 18px;
  border: 2px solid #ffffff;
  outline: 1px solid #d7dde0;
}

.pd-layout {
  align-content: start;
  align-items: start;
  gap: 8px;
  padding: 8px 12px 14px;
}

.pd-hero {
  padding: 6px 10px;
  min-height: 50px;
  display: grid;
  align-items: center;
}

.pd-hero__main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.pd-hero__left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.pd-hero__back {
  flex: 0 0 auto;
  align-self: center;
  margin-top: 0;
}

.workbench-toolbar__icon-btn {
  min-width: 34px;
  min-height: 30px;
  padding: 4px 8px;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(37, 61, 70, 0.08);
}

.workbench-action--back.workbench-toolbar__icon-btn {
  min-width: 34px;
  min-height: 30px;
  padding-inline: 8px;
}

.workbench-action {
  --action-bg: linear-gradient(180deg, #f4f7f8, #e8eef1);
  --action-border: #ccd9de;
  --action-color: #2d4651;
  --action-shadow: rgba(37, 61, 70, 0.08);
  --action-hover-shadow: rgba(37, 61, 70, 0.12);

  border-color: var(--action-border);
  background: var(--action-bg);
  color: var(--action-color);
  font-weight: 600;
  box-shadow: 0 3px 8px var(--action-shadow);
  transition:
    border-color 160ms ease,
    background 160ms ease,
    color 160ms ease,
    box-shadow 160ms ease,
    transform 160ms ease;
}

.workbench-action:not(:disabled):hover {
  border-color: color-mix(in srgb, var(--action-border) 82%, #17313b);
  box-shadow: 0 4px 12px var(--action-hover-shadow);
  transform: translateY(-1px);
}

.workbench-action:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--action-border) 36%, transparent);
  outline-offset: 2px;
}

.workbench-action--back {
  --action-bg: linear-gradient(180deg, #f3f7f8, #e7eef1);
  --action-border: #cbd9df;
  --action-color: #2d4651;
  --action-shadow: rgba(45, 70, 81, 0.08);
  --action-hover-shadow: rgba(45, 70, 81, 0.14);
}

.pd-hero__copy {
  display: grid;
  gap: 2px;
}

.pd-hero__copy .section-title {
  margin-bottom: 0;
  font-size: 15px;
  line-height: 1.25;
}

.pd-hero__copy .panel-subtitle {
  font-size: 12px;
  line-height: 1.25;
}

.pd-hero__progress {
  width: min(230px, 100%);
  display: grid;
  gap: 3px;
}

.pd-hero__progress-label {
  font-size: 12px;
  color: var(--text-muted);
}

.pd-hero__progress-bar {
  width: 100%;
}

.pd-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  width: fit-content;
  max-width: 100%;
  padding: 3px;
  overflow-x: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--surface-1) 88%, var(--azure-050)), var(--surface-muted));
}

.pd-tabs__item {
  position: relative;
  flex: 0 0 auto;
  min-height: 30px;
  padding: 4px 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
  box-shadow: none;
  transition:
    border-color var(--motion-base) var(--ease-standard),
    background var(--motion-base) var(--ease-standard),
    color var(--motion-base) var(--ease-standard),
    box-shadow var(--motion-base) var(--ease-standard);
}

.pd-tabs__item::after {
  display: none;
}

.pd-tabs__item:hover:not(:disabled) {
  background: color-mix(in srgb, var(--surface-panel) 78%, var(--brand-050));
  color: var(--brand-700);
}

.pd-tabs__item.is-active {
  border-color: color-mix(in srgb, var(--brand-700) 34%, var(--line-soft));
  background: var(--surface-panel);
  color: var(--brand-700);
  box-shadow:
    0 8px 18px rgba(17, 49, 42, 0.07),
    inset 0 -2px 0 color-mix(in srgb, var(--brand-700) 72%, transparent);
}

.pd-tabs__item.is-active::after {
  display: none;
}

.pd-tabs__item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pd-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.pd-panel-head__copy {
  display: grid;
  gap: 2px;
}

.pd-panel-head .section-title {
  margin-bottom: 0;
  font-size: 15px;
}

.pd-panel-head .panel-subtitle {
  font-size: 12px;
  line-height: 1.35;
}

.pd-panel-toggle {
  flex-shrink: 0;
  width: 76px;
  min-height: 32px;
  padding: 6px 10px;
  font-size: 13px;
}

.pd-panel-toggle:not(:disabled):hover,
.pd-panel-toggle:not(:disabled):active {
  transform: none;
}

.pd-base-panel {
  padding: 10px 18px;
  overflow-anchor: none;
}

.pd-base-panel .pd-panel-head {
  align-items: center;
  margin-bottom: 6px;
}

.pd-base-panel.is-collapsed .pd-panel-head {
  margin-bottom: 0;
}

.pd-base-panel.is-collapsed .pd-panel-head__copy {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.pd-base-panel.is-collapsed .pd-panel-head .section-title {
  flex: 0 0 auto;
}

.pd-base-panel.is-collapsed .pd-panel-head .panel-subtitle {
  display: none;
}

.pd-base-panel.is-collapsed .pd-base-summary {
  margin-top: 0;
}

.pd-base-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 4px;
}

.pd-base-summary span {
  min-height: 20px;
  padding: 1px 6px;
  border: 1px solid color-mix(in srgb, var(--brand-700) 14%, var(--line-soft));
  border-radius: 6px;
  background: color-mix(in srgb, var(--brand-050) 42%, var(--surface-panel));
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.35;
  white-space: nowrap;
}

.pd-files-panel {
  padding: 12px 18px 14px;
}

.pd-files-panel .pd-panel-head {
  margin-bottom: 4px;
}

.pd-files-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.pd-files-count {
  min-height: 20px;
  padding: 1px 7px;
  border: 1px solid color-mix(in srgb, var(--brand-700) 12%, var(--line-soft));
  border-radius: 6px;
  background: var(--surface-muted);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  line-height: 1.35;
}

.pd-files-panel .pd-toolbar {
  gap: 6px 8px;
  padding: 2px 0 8px;
  justify-content: space-between;
}

.pd-files-panel .pd-toolbar__right {
  flex: 1 1 100%;
  width: 100%;
  justify-content: flex-start;
}

.pd-files-panel .pd-file-filters {
  flex: 1 1 auto;
  justify-content: flex-start;
}

@media (min-width: 1540px) {
  .pd-files-panel .pd-toolbar {
    align-items: center;
    flex-wrap: nowrap;
  }

  .pd-files-panel .pd-toolbar__left {
    flex: 0 0 auto;
  }

  .pd-files-panel .pd-toolbar__right {
    flex: 1 1 auto;
    width: auto;
  }

  .pd-files-panel .pd-file-filters {
    flex-wrap: nowrap;
    justify-content: flex-end;
  }

  .pd-files-panel .pd-file-filter--search {
    flex: 0 1 250px;
    width: 250px;
  }

  .pd-files-panel .pd-file-filter__select--status {
    width: 122px;
  }

  .pd-files-panel .pd-file-filter__select--language {
    width: 148px;
  }

  .pd-files-panel .pd-file-filter__select--assignee {
    width: 132px;
  }
}

.pd-basic-collapse {
  display: grid;
  grid-template-rows: 1fr;
  opacity: 1;
  overflow-anchor: none;
  transition:
    grid-template-rows var(--motion-slow) var(--ease-emphasized),
    opacity var(--motion-base) var(--ease-standard),
    margin-top var(--motion-base) var(--ease-standard);
}

.pd-basic-collapse.is-collapsed {
  grid-template-rows: 0fr;
  margin-top: -4px;
  opacity: 0;
  pointer-events: none;
}

.pd-basic-collapse__inner {
  min-height: 0;
  overflow: hidden;
}

.pd-basic-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
  gap: 6px 18px;
  padding-top: 2px;
}

.pd-field {
  display: grid;
  min-width: 0;
  gap: 2px;
  padding-bottom: 7px;
  border-bottom: 1px solid color-mix(in srgb, var(--line-soft) 68%, transparent);
}

.pd-field__label {
  font-size: 11px;
  color: var(--text-muted);
}

.pd-field__value {
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.3;
  word-break: break-word;
}

.pd-toolbar {
  align-items: flex-start;
  gap: 8px 10px;
  padding: 4px 0 8px;
  flex-wrap: wrap;
}

.pd-toolbar__left,
.pd-toolbar__right {
  min-width: 0;
  flex-wrap: wrap;
}

.pd-toolbar__left {
  gap: 8px;
}

.pd-toolbar__right {
  flex: 1 1 620px;
  justify-content: flex-end;
}

.pd-toolbar-primary {
  min-height: 32px;
  padding: 5px 12px;
  border-radius: 7px;
  font-weight: 700;
  white-space: nowrap;
}

.pd-file-selection {
  position: relative;
  display: inline-flex;
  flex: 0 0 auto;
  min-width: 0;
}

.pd-file-selection__trigger {
  min-height: 32px;
  padding: 5px 9px;
  gap: 4px;
  border-radius: 7px;
  border-color: color-mix(in srgb, var(--brand-700) 22%, var(--line-soft));
  background: var(--surface-panel);
  color: var(--brand-700);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.pd-file-selection__trigger strong {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 19px;
  height: 19px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--brand-700);
  color: #fff;
  font-size: 11px;
  line-height: 1;
}

.pd-file-selection__trigger .lucide:last-child {
  width: 12px;
  height: 12px;
  opacity: 0.75;
}

.pd-file-selection__menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 3200;
  display: grid;
  gap: 7px;
  width: min(328px, calc(100vw - 32px));
  padding: 9px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: var(--shadow-popover);
}

.pd-file-selection__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 2px 2px 4px;
}

.pd-file-selection__head strong,
.pd-file-selection__range-head strong {
  color: var(--text-primary);
  font-size: 13px;
}

.pd-file-selection__head span,
.pd-file-selection__range-head span,
.pd-file-selection__item small {
  color: var(--text-muted);
  font-size: 12px;
}

.pd-file-selection__item,
.pd-file-selection__clear {
  width: 100%;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--text-primary);
  text-align: left;
  cursor: pointer;
}

.pd-file-selection__item {
  display: grid;
  gap: 3px;
  padding: 8px 9px;
}

.pd-file-selection__item span {
  font-size: 13px;
  font-weight: 700;
}

.pd-file-selection__item:hover:not(:disabled),
.pd-file-selection__item.is-active {
  background: color-mix(in srgb, var(--brand-050) 72%, var(--surface-muted));
}

.pd-file-selection__item:disabled {
  color: var(--text-muted);
  cursor: not-allowed;
  opacity: 0.58;
}

.pd-file-selection__range {
  display: grid;
  gap: 8px;
  padding: 9px;
  border: 1px solid color-mix(in srgb, var(--line-soft) 86%, var(--brand-100));
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-muted) 54%, var(--surface-panel));
}

.pd-file-selection__range-head {
  display: grid;
  gap: 2px;
}

.pd-file-selection__range-controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  align-items: end;
  gap: 7px;
}

.pd-file-selection__range-controls label {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.pd-file-selection__range-controls label span {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 650;
}

.pd-file-selection__range-controls input {
  width: 100%;
  height: 30px;
  min-width: 0;
  padding: 0 8px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--control-bg);
  color: var(--text-primary);
  font-size: 13px;
}

.pd-file-selection__range-controls input:focus {
  outline: none;
  border-color: var(--brand-700);
  box-shadow: var(--focus-ring);
}

.pd-file-selection__range-submit {
  height: 30px;
  min-height: 30px;
  padding: 0 10px;
}

.pd-file-selection__error {
  margin: -2px 0 0;
  color: var(--state-danger);
  font-size: 12px;
}

.pd-file-selection__clear {
  padding: 7px 9px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 700;
}

.pd-file-selection__clear:hover {
  background: var(--surface-muted);
  color: var(--state-danger);
}

.pd-toolbar-action-strip {
  display: inline-flex;
  align-items: center;
  flex: 0 1 auto;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 100%;
  min-width: 0;
}

.pd-toolbar-action-group {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 2px;
  min-width: 0;
}

/* Keep the danger action slightly set apart with a light divider */
.pd-toolbar-action-group--danger {
  margin-left: 4px;
  padding-left: 8px;
  border-left: 1px solid color-mix(in srgb, var(--line-soft) 60%, transparent);
}

/* Hide not-yet-available placeholders to keep the bar clean and minimal */
.pd-toolbar-action-group--reserved,
.pd-toolbar-action-group--utility {
  display: none;
}

.pd-toolbar-action-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 32px;
  min-width: 32px;
  height: 32px;
  min-height: 32px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1;
  box-shadow: none;
  white-space: nowrap;
}

.pd-toolbar-action-button .lucide {
  flex: 0 0 auto;
  color: currentColor;
}

.pd-toolbar-action-button:not(:disabled):hover {
  background: color-mix(in srgb, var(--brand-050) 66%, var(--surface-muted));
  color: var(--brand-700);
}

.pd-toolbar-action-button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* Secondary actions are icon-only; the label is kept for accessibility */
.pd-toolbar-action-button--labeled span {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

/* Featured "smart" actions keep their text label as compact pills */
.pd-toolbar-action-group--featured .pd-toolbar-action-button--labeled {
  width: auto;
  min-width: 0;
  padding: 0 12px;
}

.pd-toolbar-action-group--featured .pd-toolbar-action-button--labeled span {
  position: static;
  width: auto;
  height: auto;
  overflow: visible;
  clip: auto;
}

.pd-toolbar-action-button--accent {
  background: color-mix(in srgb, var(--brand-100) 46%, var(--surface-panel));
  color: var(--brand-700);
}

.pd-toolbar-action-button--accent:not(:disabled):hover {
  background: color-mix(in srgb, var(--brand-100) 72%, var(--surface-panel));
  color: var(--brand-700);
}

/* Subtle icon tint keeps the copy actions recognisable without a colour block */
.pd-toolbar-action-button--british-copy .lucide {
  color: #2563eb;
}

.pd-toolbar-action-button--american-copy .lucide {
  color: #7c3aed;
}

.pd-toolbar-action-button--template-copy .lucide {
  color: #b45309;
}

.pd-toolbar-export-button {
  width: auto;
  min-width: 0;
  gap: 3px;
  padding: 0 8px;
}

.pd-toolbar-export-button .lucide:last-child {
  width: 12px;
  height: 12px;
  opacity: 0.7;
}

.pd-toolbar-action-button--danger {
  color: var(--state-danger);
}

.pd-toolbar-action-button--danger .lucide {
  color: var(--state-danger);
}

.pd-toolbar-action-button--danger:not(:disabled):hover {
  background: var(--state-danger-bg);
  color: var(--state-danger);
}

.pd-file-filters {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  max-width: 100%;
  min-width: 0;
  flex-wrap: wrap;
}

.pd-file-filters__lead {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-muted);
  color: var(--text-muted);
}

.pd-file-filter {
  position: relative;
  display: inline-flex;
  align-items: center;
  min-width: 0;
}

.pd-file-filter--search {
  flex: 1 1 220px;
  width: min(300px, 32vw);
  max-width: 320px;
}

.pd-file-filter__input,
.pd-file-filter__select {
  min-height: 32px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--control-bg);
  color: var(--text-primary);
  font-size: 13px;
  transition:
    border-color var(--motion-base) var(--ease-standard),
    background var(--motion-base) var(--ease-standard),
    box-shadow var(--motion-base) var(--ease-standard);
}

.pd-file-filter__input {
  width: 100%;
  padding: 5px 32px 5px 32px;
}

.pd-file-filter__select {
  height: 32px;
  padding: 0 28px 0 10px;
}

.pd-file-filter__select--status {
  width: 122px;
}

.pd-file-filter__select--language {
  width: 154px;
}

.pd-file-filter__select--assignee {
  width: 136px;
}

@media (max-width: 1480px) {
  .pd-files-panel .pd-file-filters__lead {
    display: none;
  }

  .pd-files-panel .pd-file-filter--search {
    flex-basis: 220px;
    width: 220px;
  }

  .pd-files-panel .pd-file-filter__select--status {
    width: 116px;
  }

  .pd-files-panel .pd-file-filter__select--language {
    width: 142px;
  }

  .pd-files-panel .pd-file-filter__select--assignee {
    width: 126px;
  }
}

.pd-file-filter__input::placeholder {
  color: var(--text-placeholder);
}

.pd-file-filter__input:hover,
.pd-file-filter__select:hover {
  border-color: color-mix(in srgb, var(--brand-700) 58%, var(--line-strong));
  background: var(--surface-panel);
}

.pd-file-filter__input:focus,
.pd-file-filter__select:focus {
  outline: none;
  border-color: var(--brand-700);
  box-shadow: var(--focus-ring);
}

.pd-file-filter__search-icon {
  position: absolute;
  left: 10px;
  color: var(--text-muted);
  pointer-events: none;
}

.pd-file-filter__clear {
  position: absolute;
  right: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  box-shadow: none;
}

.pd-file-filter__clear:hover {
  background: var(--surface-muted);
  color: var(--brand-700);
}

.pd-file-filter__reset {
  min-height: 32px;
  padding: 4px 10px;
  font-size: 13px;
}

.pd-file-filter__summary {
  color: var(--text-muted);
  font-size: 12px;
  white-space: nowrap;
}

.pd-merge-view-list {
  display: grid;
  gap: 10px;
}

.pd-merge-view-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  padding: 14px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.pd-merge-view-item__main {
  min-width: 0;
  display: grid;
  gap: 9px;
}

.pd-merge-view-item__head {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.pd-merge-view-item__head strong,
.pd-merge-view-item__head span,
.pd-merge-view-item__files span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-merge-view-item__head strong {
  color: var(--text-primary);
  font-size: 14px;
}

.pd-merge-view-item__head span {
  color: var(--text-muted);
  font-size: 12px;
}

.pd-merge-view-item__files {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
}

.pd-merge-view-item__files span,
.pd-merge-view-selected span {
  max-width: 220px;
  padding: 3px 7px;
  border: 1px solid color-mix(in srgb, var(--brand-700) 18%, var(--line-soft));
  border-radius: 6px;
  background: color-mix(in srgb, var(--brand-050) 48%, var(--surface-panel));
  color: var(--text-secondary);
  font-size: 12px;
}

.pd-merge-view-item__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.pd-merge-view-dialog {
  display: grid;
  gap: 14px;
}

.pd-merge-view-selected {
  display: grid;
  gap: 8px;
}

.pd-merge-view-selected > span {
  max-width: none;
  width: fit-content;
  background: var(--surface-muted);
}

.pd-merge-view-selected > div {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.pd-merge-view-language {
  display: grid;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--brand-700) 16%, var(--line-soft));
  border-radius: 8px;
  background: color-mix(in srgb, var(--brand-050) 48%, var(--surface-panel));
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.pd-merge-view-language.is-warning {
  border-color: rgba(194, 120, 3, 0.3);
  background: var(--state-warning-bg);
}

.pd-merge-view-language strong {
  color: var(--text-primary);
}

.pd-merge-view-language > div {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pd-merge-view-language span {
  max-width: 100%;
  padding: 3px 7px;
  border: 1px solid color-mix(in srgb, var(--brand-700) 14%, var(--line-soft));
  border-radius: 6px;
  background: var(--surface-panel);
  overflow-wrap: anywhere;
}

.pd-merge-view-language p {
  margin: 0;
}

.pd-export-dropdown {
  position: relative;
  display: inline-flex;
}

.pd-export-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 30;
  min-width: 300px;
  max-width: min(360px, calc(100vw - 24px));
  max-height: min(520px, calc(100vh - 120px));
  padding: 8px;
  overflow-y: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-0);
  box-shadow: var(--shadow-medium);
}

.pd-export-menu__loading,
.pd-export-menu__item {
  width: 100%;
  padding: 9px 12px;
  text-align: left;
}

.pd-export-menu__loading {
  color: var(--text-secondary);
  font-size: 13px;
}

.pd-export-menu__group + .pd-export-menu__group,
.pd-export-menu__group + .pd-export-menu__item {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--line-soft);
}

.pd-export-menu__group-title {
  padding: 2px 0 6px;
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 700;
}

.pd-export-menu__item {
  display: grid;
  gap: 4px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-primary);
  cursor: pointer;
}

.pd-export-menu__item:hover:not(:disabled) {
  background: var(--surface-muted);
}

.pd-export-menu__item:disabled {
  color: var(--text-muted);
  cursor: not-allowed;
}

.pd-export-menu__item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.pd-export-menu__item-name {
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.pd-export-menu__item-ext {
  flex: 0 0 auto;
  padding: 2px 6px;
  border: 1px solid var(--line-soft);
  border-radius: 4px;
  background: var(--surface-muted);
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 700;
}

.pd-export-menu__item-desc {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.35;
}

.pd-file-table :deep(.data-table) {
  table-layout: fixed;
  min-width: 1080px;
}

.pd-file-table :deep(.data-table tbody tr.pd-file-row--template-copy) {
  background: color-mix(in srgb, #d97706 4%, var(--surface-panel));
  box-shadow: inset 3px 0 0 color-mix(in srgb, #d97706 72%, transparent);
}

.pd-file-table :deep(.data-table tbody tr.pd-file-row--british-copy) {
  background: color-mix(in srgb, #2563eb 4%, var(--surface-panel));
  box-shadow: inset 3px 0 0 color-mix(in srgb, #2563eb 68%, transparent);
}

.pd-file-table :deep(.data-table tbody tr.pd-file-row--american-copy) {
  background: color-mix(in srgb, #7c3aed 4%, var(--surface-panel));
  box-shadow: inset 3px 0 0 color-mix(in srgb, #7c3aed 68%, transparent);
}

.pd-file-table :deep(.data-table tbody tr.pd-file-row--template-copy:hover) {
  background: color-mix(in srgb, #d97706 7%, var(--surface-panel));
}

.pd-file-table :deep(.data-table tbody tr.pd-file-row--british-copy:hover) {
  background: color-mix(in srgb, #2563eb 7%, var(--surface-panel));
}

.pd-file-table :deep(.data-table tbody tr.pd-file-row--american-copy:hover) {
  background: color-mix(in srgb, #7c3aed 7%, var(--surface-panel));
}

.pd-file-table :deep(.data-table tbody tr.pd-file-row--template-copy.is-selected) {
  background: color-mix(in srgb, #d97706 5%, var(--teal-050));
  box-shadow: inset 3px 0 0 #d97706;
}

.pd-file-table :deep(.data-table tbody tr.pd-file-row--british-copy.is-selected) {
  background: color-mix(in srgb, #2563eb 5%, var(--teal-050));
  box-shadow: inset 3px 0 0 #2563eb;
}

.pd-file-table :deep(.data-table tbody tr.pd-file-row--american-copy.is-selected) {
  background: color-mix(in srgb, #7c3aed 5%, var(--teal-050));
  box-shadow: inset 3px 0 0 #7c3aed;
}

.pd-statistics-file-table :deep(.data-table) {
  min-width: 980px;
}

.pd-statistics-selection {
  color: var(--text-muted);
  font-size: 13px;
}

.pd-statistics-report-picker {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--text-muted);
  font-size: 13px;
}

.pd-statistics-report-picker span {
  flex: 0 0 auto;
}

.pd-statistics-report-picker__select {
  width: min(360px, 52vw);
  min-width: 220px;
  height: 34px;
  padding: 0 10px;
}

.pd-statistics-report-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  color: var(--text-muted);
  font-size: 13px;
}

.pd-statistics-result {
  display: grid;
  gap: 14px;
  margin-top: 16px;
}

.pd-statistics-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.pd-statistics-summary__item {
  min-width: 0;
  display: grid;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-muted);
}

.pd-statistics-summary__item span {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-statistics-summary__item strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 18px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-statistics-match-analysis {
  display: grid;
  gap: 10px;
}

.pd-statistics-subhead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.pd-statistics-subhead > span {
  flex: 0 0 auto;
  color: var(--text-muted);
  font-size: 13px;
}

.pd-statistics-file-match-list {
  display: grid;
  gap: 14px;
}

.pd-statistics-file-match-block {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.pd-statistics-file-match-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  padding: 7px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-muted);
}

.pd-statistics-file-match-head strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-statistics-file-match-head span {
  flex: 0 0 auto;
  color: var(--text-muted);
  font-size: 12px;
}

.pd-statistics-grid-wrap {
  overflow-x: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.pd-statistics-grid-wrap--match {
  max-width: 720px;
}

.pd-statistics-grid {
  width: 100%;
  min-width: 1120px;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 13px;
}

.pd-statistics-match-grid {
  min-width: 560px;
}

.pd-statistics-grid th,
.pd-statistics-grid td {
  padding: 9px 10px;
  border-right: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
  color: var(--text-secondary);
  text-align: right;
  vertical-align: middle;
}

.pd-statistics-grid th:first-child,
.pd-statistics-grid td:first-child,
.pd-statistics-grid th:nth-child(2),
.pd-statistics-grid td:nth-child(2),
.pd-statistics-grid th:last-child,
.pd-statistics-grid td:last-child {
  text-align: left;
}

.pd-statistics-grid th:last-child,
.pd-statistics-grid td:last-child {
  border-right: 0;
}

.pd-statistics-match-grid th:first-child,
.pd-statistics-match-grid td:first-child {
  text-align: left;
}

.pd-statistics-match-grid th:nth-child(n + 2),
.pd-statistics-match-grid td:nth-child(n + 2),
.pd-statistics-match-grid th:last-child,
.pd-statistics-match-grid td:last-child {
  text-align: right;
}

.pd-statistics-grid thead th,
.pd-statistics-grid tfoot th,
.pd-statistics-grid tfoot td {
  background: var(--surface-muted);
  color: var(--text-primary);
  font-weight: 600;
}

.pd-statistics-grid tbody tr:last-child td {
  border-bottom: 0;
}

.pd-statistics-match-grid tbody tr.is-total td {
  background: var(--surface-muted);
  color: var(--text-primary);
  font-weight: 600;
}

.pd-statistics-grid tfoot th,
.pd-statistics-grid tfoot td {
  border-bottom: 0;
}

.pd-statistics-file-name {
  display: block;
  overflow: hidden;
  color: var(--text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-statistics-empty {
  margin-top: 16px;
  min-height: 120px;
}

.pd-file-cell {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
  max-width: 100%;
}

.pd-file-cell__icon {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--brand-700);
}

.pd-file-cell__content {
  display: grid;
  gap: 4px;
  min-width: 0;
  width: 100%;
  overflow: hidden;
}

.pd-file-cell__title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
  white-space: nowrap;
}

.pd-link-button {
  display: block;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--brand-700);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  box-shadow: none;
}

.pd-link-button:hover {
  color: var(--brand-600);
}

.pd-file-cell__title {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-file-kind-badge {
  flex: 0 0 auto;
  padding: 2px 6px;
  border: 1px solid transparent;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  line-height: 1.2;
}

.pd-file-kind-badge--template-copy {
  border-color: color-mix(in srgb, #d97706 18%, transparent);
  background: color-mix(in srgb, #d97706 6%, transparent);
  color: #9a5b00;
}

.pd-file-kind-badge--british-copy {
  border-color: color-mix(in srgb, #2563eb 18%, transparent);
  background: color-mix(in srgb, #2563eb 6%, transparent);
  color: #1d4ed8;
}

.pd-file-kind-badge--american-copy {
  border-color: color-mix(in srgb, #7c3aed 18%, transparent);
  background: color-mix(in srgb, #7c3aed 6%, transparent);
  color: #6d28d9;
}

.pd-file-cell__meta {
  display: block;
  overflow: hidden;
  font-size: 12px;
  color: var(--text-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-file-progress {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.pd-file-progress__status {
  display: block;
  overflow: hidden;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-file-progress__cancel {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  justify-self: flex-start;
  gap: 4px;
  min-width: 52px;
  min-height: 24px;
  padding: 3px 8px;
  border: 1px solid color-mix(in srgb, var(--state-danger, #dc2626) 26%, transparent);
  border-radius: 6px;
  background: var(--state-danger-bg, #fef2f2);
  color: var(--state-danger, #dc2626);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}

.pd-file-progress__cancel:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--state-danger, #dc2626) 42%, transparent);
  background: color-mix(in srgb, var(--state-danger-bg, #fef2f2) 70%, #fff);
}

.pd-file-progress__cancel:disabled {
  cursor: wait;
  opacity: 0.75;
}

.pd-task-cell {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.pd-assignee {
  display: block;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-assignee.is-empty {
  color: var(--text-muted);
  font-weight: 500;
}

.pd-task-links {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
}

.issue-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 24px;
  padding: 3px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 999px;
  background: var(--surface-muted);
  color: var(--text-muted);
  font-size: 12px;
  box-shadow: none;
}

.issue-badge.is-active {
  border-color: color-mix(in srgb, var(--state-warning) 45%, var(--line-soft));
  background: var(--state-warning-bg);
  color: var(--state-warning);
}

.issue-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
  color: var(--text-muted);
  font-size: 13px;
}

.issue-summary span {
  padding: 4px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 999px;
  background: var(--surface-muted);
}

.issue-empty {
  min-height: 180px;
}

.issue-list {
  display: grid;
  gap: 10px;
}

.issue-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.issue-item--resolved {
  opacity: 0.78;
}

.issue-item__main {
  min-width: 0;
  display: grid;
  gap: 8px;
}

.issue-item__head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.issue-item__head strong {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--text-primary);
  font-size: 14px;
}

.issue-status {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 12px;
}

.issue-status--open {
  color: var(--state-warning);
  background: var(--state-warning-bg);
}

.issue-status--resolved {
  color: var(--state-success);
  background: var(--state-success-bg);
}

.issue-item__description {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.issue-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  color: var(--text-muted);
  font-size: 12px;
}

.issue-item__action {
  flex: 0 0 auto;
  min-height: 32px;
  padding: 6px 10px;
}

.pd-inline-link {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--brand-700);
  font-size: 13px;
  box-shadow: none;
}

.pd-inline-link:hover:not(:disabled) {
  color: var(--brand-600);
}

.pd-inline-link:disabled {
  color: var(--text-muted);
  opacity: 0.6;
  cursor: not-allowed;
}

.pd-row-actions {
  display: flex;
  justify-content: center;
  position: relative;
}

.pd-action-menu {
  position: relative;
}

.pd-action-menu__dropdown {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 20;
  min-width: 148px;
  display: grid;
  gap: 4px;
  padding: 8px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--surface-panel);
  box-shadow: var(--shadow-soft);
}

.pd-action-menu__dropdown--floating {
  position: fixed;
  right: auto;
  top: auto;
  margin: 0;
}

.pd-action-menu__dropdown button {
  justify-content: flex-start;
  min-height: 34px;
  padding: 6px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  box-shadow: none;
}

.pd-action-menu__dropdown button:hover:not(:disabled) {
  background: var(--surface-muted);
  color: var(--text-primary);
}

.pd-action-menu__dropdown button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.pd-action-menu__dropdown button.is-danger {
  color: var(--state-danger);
}

.pd-action-menu__dropdown button.is-danger:hover:not(:disabled) {
  background: var(--state-danger-bg);
}

.pd-upload-progress {
  margin-top: 12px;
}

.pd-assignment-dialog {
  display: grid;
  gap: 12px;
}

.pd-assignment-dialog--project {
  grid-template-columns: minmax(220px, 280px) minmax(0, 1fr);
  align-items: stretch;
  gap: 16px;
  min-height: min(560px, calc(100vh - 220px));
}

.pd-assignment-users,
.pd-assignment-files {
  min-height: 360px;
  max-height: min(600px, calc(100vh - 220px));
  overflow: hidden;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.pd-assignment-users {
  display: grid;
  grid-template-rows: auto auto auto minmax(0, 1fr);
  gap: 8px;
  padding: 10px;
}

.pd-assignment-panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  color: var(--text-muted);
  font-size: 12px;
}

.pd-assignment-panel__head > div {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.pd-assignment-panel__head strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-assignment-panel__head span {
  color: var(--text-muted);
  font-size: 12px;
}

.pd-assignment-panel__head--files {
  align-items: center;
}

.pd-assignment-search {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 0 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
  color: var(--text-muted);
  font-size: 13px;
}

.pd-assignment-search:focus-within {
  border-color: color-mix(in srgb, var(--brand-700) 45%, var(--line-strong));
  box-shadow: var(--focus-ring);
}

.pd-assignment-search input {
  min-width: 0;
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text-primary);
}

.pd-assignment-search input::placeholder {
  color: var(--text-placeholder);
}

.pd-assignment-search input:disabled {
  cursor: not-allowed;
}

.pd-assignment-clear {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  box-shadow: none;
}

.pd-assignment-clear:hover:not(:disabled) {
  background: var(--surface-muted);
  color: var(--text-primary);
}

.pd-assignment-filter-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.pd-assignment-filter-select {
  min-width: 0;
  width: 100%;
  min-height: 36px;
  padding: 0 28px 0 10px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-muted);
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.35;
}

.pd-assignment-filter-select:focus {
  outline: none;
  border-color: var(--brand-700);
  background: var(--surface-panel);
  box-shadow: var(--focus-ring);
}

.pd-assignment-state {
  display: grid;
  place-items: center;
  min-height: 90px;
  margin: 0;
  padding: 12px;
  border: 1px dashed var(--line-soft);
  border-radius: 8px;
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
}

.pd-assignment-user-list {
  min-height: 0;
  display: grid;
  grid-auto-rows: minmax(64px, auto);
  align-content: start;
  gap: 6px;
  overflow: auto;
  padding-right: 2px;
}

.pd-assignment-user {
  position: relative;
  display: grid;
  align-content: center;
  gap: 4px;
  justify-items: start;
  min-height: 64px;
  width: 100%;
  padding: 10px 36px 10px 12px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  box-shadow: none;
}

.pd-assignment-user:hover:not(:disabled) {
  border-color: var(--line-soft);
  background: var(--surface-muted);
  color: var(--text-primary);
}

.pd-assignment-user.is-active {
  border-color: color-mix(in srgb, var(--brand-700) 58%, var(--line-strong));
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--brand-700) 12%, transparent), transparent 34%),
    color-mix(in srgb, var(--brand-100) 74%, var(--surface-panel));
  color: var(--text-primary);
  box-shadow:
    inset 4px 0 0 var(--brand-700),
    0 0 0 1px color-mix(in srgb, var(--brand-700) 14%, transparent);
}

.pd-assignment-user.is-active::after {
  content: "✓";
  position: absolute;
  top: 10px;
  right: 10px;
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: var(--brand-700);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
}

.pd-assignment-user.is-active:hover:not(:disabled) {
  border-color: var(--brand-700);
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--brand-700) 16%, transparent), transparent 36%),
    color-mix(in srgb, var(--brand-100) 86%, var(--surface-panel));
}

.pd-assignment-user span {
  max-width: 100%;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
  overflow-wrap: anywhere;
  white-space: normal;
}

.pd-assignment-user small {
  max-width: 100%;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.35;
  overflow-wrap: anywhere;
  white-space: normal;
}

.pd-assignment-files {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: 10px;
  padding: 12px;
}

.pd-assignment-file-toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(132px, 160px);
  gap: 8px;
  align-items: center;
}

.pd-assignment-workflow-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pd-assignment-workflow-tab {
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-panel);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}

.pd-assignment-workflow-tab.is-active {
  border-color: var(--brand-600);
  color: var(--brand-700);
  background: color-mix(in srgb, var(--brand-100) 82%, var(--surface-panel));
}

.pd-assignment-file-groups {
  min-height: 0;
  display: grid;
  align-content: start;
  gap: 12px;
  overflow: auto;
  padding-right: 2px;
}

.pd-assignment-empty {
  min-height: 220px;
}

.pd-assignment-file-group {
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
}

.pd-assignment-file-group__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  color: var(--text-muted);
  font-size: 12px;
}

.pd-assignment-file-group__title {
  min-width: 160px;
  display: grid;
  flex: 1 1 180px;
  gap: 2px;
}

.pd-assignment-file-group__title strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-assignment-file-group__title span {
  overflow: hidden;
  color: var(--text-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-assignment-file-group__actions {
  display: flex;
  flex: 0 1 auto;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.pd-assignment-file-action {
  min-height: 30px;
  padding: 5px 8px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.2;
  box-shadow: none;
}

.pd-assignment-view-list {
  display: grid;
  gap: 6px;
  padding: 8px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-muted) 54%, var(--surface-panel));
}

.pd-assignment-view-list__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--text-muted);
  font-size: 12px;
}

.pd-assignment-view-list__head strong {
  color: var(--text-primary);
  font-size: 13px;
}

.pd-assignment-view-option {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-height: 38px;
  padding: 7px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.4;
}

.pd-assignment-view-option:hover {
  border-color: var(--line-soft);
  background: var(--surface-panel);
}

.pd-assignment-view-option.is-partial {
  border-color: color-mix(in srgb, var(--state-warning) 42%, var(--line-soft));
  background: color-mix(in srgb, var(--state-warning-bg) 62%, var(--surface-panel));
}

.pd-assignment-view-option > span {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.pd-assignment-view-option strong,
.pd-assignment-view-option small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-assignment-view-option strong {
  color: var(--text-primary);
}

.pd-assignment-view-option small {
  color: var(--text-muted);
  font-size: 12px;
}

.pd-assignment-file-list {
  display: grid;
  gap: 4px;
  max-height: 240px;
  overflow: auto;
  padding: 2px;
}

.pd-assignment-file-option {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  min-height: 30px;
  padding: 6px 8px;
  border-radius: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.45;
}

.pd-assignment-file-option:hover {
  background: var(--surface-muted);
}

.pd-assignment-file-check {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}

.pd-assignment-file-check span {
  min-width: 0;
  flex: 1;
  overflow-wrap: anywhere;
  white-space: normal;
}

.pd-assignment-range-controls {
  display: grid;
  grid-template-columns: auto 68px auto 68px;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  font-size: 12px;
}

.pd-assignment-range-controls input {
  width: 68px;
  min-width: 0;
  padding: 4px 6px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-panel);
  color: var(--text-primary);
  font: inherit;
}

.pd-assignment-range-controls input:focus {
  border-color: var(--brand-500);
  outline: none;
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--brand-500) 18%, transparent);
}

.pd-assignment-range-controls input:disabled {
  background: var(--surface-muted);
  color: var(--text-muted);
}

.pd-assignment-mini-empty {
  display: grid;
  place-items: center;
  min-height: 58px;
  margin: 0;
  padding: 10px;
  border: 1px dashed var(--line-soft);
  border-radius: 8px;
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
}

.pd-assignment-tooltip {
  position: fixed;
  z-index: 10000;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--brand-700) 54%, var(--line-strong));
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-panel) 96%, var(--brand-050));
  color: var(--text-primary);
  box-shadow: 0 10px 28px rgba(17, 49, 42, 0.16);
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  pointer-events: none;
  white-space: normal;
}

.assignment-event-list {
  display: grid;
  gap: 10px;
}

.assignment-event-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.assignment-event-item__main,
.assignment-event-item__meta {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.assignment-event-item__main strong {
  color: var(--text-primary);
  font-size: 13px;
}

.assignment-event-item__main span,
.assignment-event-item__meta {
  color: var(--text-muted);
  font-size: 12px;
}

.pd-upload-dialog {
  display: grid;
  gap: 16px;
}

.pd-upload-picker {
  display: grid;
  grid-template-columns: auto minmax(220px, 1fr) minmax(180px, 0.8fr);
  gap: 14px;
  align-items: center;
  padding: 14px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
}

.pd-upload-picker__icon {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--brand-050);
  color: var(--brand-700);
}

.pd-upload-picker__field {
  min-width: 0;
}

.pd-upload-picker__summary {
  display: grid;
  gap: 4px;
  min-width: 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.45;
}

.pd-upload-picker__summary strong {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.pd-upload-picker__summary span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-upload-grid {
  align-items: start;
}

.project-status {
  display: inline-flex;
  align-items: center;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 1;
}

.project-status--info {
  color: var(--state-info);
  background: var(--state-info-bg);
}

.project-status--success {
  color: var(--state-success);
  background: var(--state-success-bg);
}

.project-status--warning {
  color: var(--state-warning);
  background: var(--state-warning-bg);
}

.project-status--danger {
  color: var(--state-danger);
  background: var(--state-danger-bg);
}

.project-status--default {
  color: var(--text-secondary);
  background: var(--surface-muted);
}

.field--full {
  grid-column: 1 / -1;
}

.field__required {
  color: var(--state-danger);
}

.pd-settings-panel {
  padding: 0;
  overflow: visible;
}

.pd-settings-layout {
  display: grid;
  grid-template-columns: 168px minmax(0, 1fr);
  align-items: start;
  min-height: 0;
}

.pd-settings-rail {
  position: sticky;
  top: 72px;
  align-self: stretch;
  display: grid;
  align-content: start;
  grid-auto-rows: min-content;
  gap: 3px;
  min-height: 0;
  padding: 10px 0;
  border-right: 1px solid var(--line-soft);
  background: var(--surface-panel);
}

.pd-settings-rail__item {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0;
  width: 100%;
  min-width: 0;
  min-height: 34px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-right: 2px solid transparent;
  border-radius: 0;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  text-align: right;
  text-decoration: none;
  transition:
    border-color var(--motion-base) var(--ease-standard),
    background var(--motion-base) var(--ease-standard),
    color var(--motion-base) var(--ease-standard);
}

.pd-settings-rail__item:hover {
  border-color: transparent;
  border-right-color: color-mix(in srgb, var(--brand-700) 38%, transparent);
  background: color-mix(in srgb, var(--brand-700) 7%, var(--surface-panel));
  color: var(--brand-700);
}

.pd-settings-rail__item.is-active {
  border-color: transparent;
  border-right-color: var(--brand-700);
  background: var(--brand-050);
  color: var(--brand-700);
}

.pd-settings-rail__item svg {
  display: none;
}

.pd-settings-rail__item span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-settings-main {
  min-width: 0;
  display: grid;
  align-content: start;
}

.pd-settings-overview {
  display: grid;
  align-items: center;
  gap: 6px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--surface-panel);
}

.pd-settings-overview__copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.pd-settings-overview .section-title {
  margin-bottom: 0;
  font-size: 15px;
}

.pd-settings-section {
  scroll-margin-top: 16px;
  display: grid;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line-soft);
}

.pd-settings-section:last-child {
  border-bottom: 0;
}

.pd-settings-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.pd-settings-section-head__copy {
  min-width: 0;
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 8px;
}

.pd-settings-section-head .section-title {
  margin-bottom: 0;
  font-size: 14px;
}

.pd-settings-section-head .panel-subtitle {
  font-size: 12px;
  line-height: 1.4;
}

.pd-settings-section-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 1px solid color-mix(in srgb, var(--brand-700) 22%, var(--line-soft));
  border-radius: 8px;
  background: var(--brand-050);
  color: var(--brand-700);
}

.pd-settings-section:nth-of-type(2) .pd-settings-section-icon {
  border-color: color-mix(in srgb, var(--state-info) 22%, var(--line-soft));
  background: var(--state-info-bg);
  color: var(--state-info);
}

.pd-settings-section:nth-of-type(3) .pd-settings-section-icon {
  border-color: color-mix(in srgb, var(--state-warning) 24%, var(--line-soft));
  background: var(--state-warning-bg);
  color: var(--state-warning);
}

.pd-settings-section:nth-of-type(4) .pd-settings-section-icon {
  border-color: color-mix(in srgb, var(--state-success) 24%, var(--line-soft));
  background: var(--state-success-bg);
  color: var(--state-success);
}

.pd-settings-section-body {
  display: grid;
  gap: 10px;
}

.pd-settings-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.pd-settings-row {
  display: grid;
  grid-template-columns: 1fr;
  gap: 6px;
}

.pd-settings-row--name {
  grid-column: 1 / -1;
}

.pd-settings-control,
.pd-settings-control--name {
  width: 100%;
  min-height: 34px;
  max-width: none;
  padding: 6px 10px;
}

.pd-readonly-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.pd-readonly-field {
  min-width: 0;
  display: grid;
  align-content: start;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-muted) 72%, var(--surface-panel));
}

.pd-readonly-value {
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pd-settings-divider {
  display: none;
}

.pd-guidelines-editor {
  resize: vertical;
  min-height: 150px;
  max-height: 420px;
  font-size: 13px;
  line-height: 1.6;
  font-family: inherit;
}

.pd-settings-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-start;
}

.pd-settings-panel .button {
  min-height: 32px;
  padding: 5px 10px;
  gap: 5px;
  font-size: 13px;
}

.pd-settings-panel .button .lucide {
  width: 14px;
  height: 14px;
}

.pd-settings-save {
  flex: 0 0 auto;
  min-height: 32px;
  padding: 5px 10px;
  font-size: 13px;
}

.pd-settings-save--compact {
  min-width: 76px;
  justify-content: center;
}

.pd-settings-section--resource {
  gap: 8px;
}

.pd-icon-action {
  display: inline-grid;
  place-items: center;
  width: 30px;
  min-width: 30px;
  height: 30px;
  min-height: 30px;
  padding: 0;
  border-radius: 6px;
}

.pd-settings-panel .pd-icon-action {
  min-height: 30px;
  padding: 0;
}

.pd-icon-action .lucide {
  width: 14px;
  height: 14px;
}

.term-settings,
.term-settings__groups,
.term-qa-report {
  display: grid;
  gap: 10px;
}

.resource-settings-block {
  display: grid;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-muted) 38%, var(--surface-panel));
}

.resource-settings-block__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.resource-settings-block__head > div {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.resource-settings-block__head strong {
  color: var(--text-primary);
  font-size: 14px;
}

.resource-settings-block__head span {
  color: var(--text-muted);
  font-size: 12px;
}

.resource-settings-search {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 7px 9px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-muted) 46%, var(--surface-panel));
}

.resource-settings-search__field {
  position: relative;
  display: flex;
  align-items: center;
  flex: 1 1 280px;
  min-width: min(100%, 240px);
}

.resource-settings-search__field svg {
  position: absolute;
  left: 10px;
  color: var(--text-muted);
  pointer-events: none;
}

.resource-settings-search__field input {
  width: 100%;
  height: 30px;
  padding: 0 10px 0 32px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--surface-panel);
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
}

.resource-settings-search__field input:focus {
  border-color: var(--brand-700);
  outline: none;
  box-shadow: var(--focus-ring);
}

.resource-settings-search__summary {
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.resource-settings-search__clear {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-panel);
  color: var(--text-muted);
  cursor: pointer;
}

.resource-settings-search__clear:hover {
  border-color: var(--line-strong);
  color: var(--brand-700);
}

.project-resource-language-dialog {
  display: grid;
  gap: 12px;
}

.project-resource-language-target {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--brand-050) 62%, var(--surface-panel));
  color: var(--text-muted);
  font-size: 13px;
}

.project-resource-language-target strong {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--brand-700);
  font-weight: 700;
}

.project-resource-language-search {
  flex-basis: 100%;
  min-width: 0;
}

.project-resource-language-list {
  display: grid;
  gap: 8px;
  max-height: 360px;
  overflow: auto;
  padding-right: 2px;
}

.project-resource-language-item {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) minmax(150px, auto) auto;
  align-items: center;
  gap: 10px;
  min-height: 58px;
  padding: 9px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  cursor: pointer;
  transition:
    border-color var(--motion-base) var(--ease-standard),
    background var(--motion-base) var(--ease-standard);
}

.project-resource-language-item:hover {
  border-color: color-mix(in srgb, var(--brand-700) 28%, var(--line-soft));
  background: color-mix(in srgb, var(--brand-050) 42%, var(--surface-panel));
}

.project-resource-language-item.is-selected {
  border-color: color-mix(in srgb, var(--brand-700) 48%, var(--line-soft));
  background: var(--brand-050);
}

.project-resource-language-item.is-current {
  cursor: not-allowed;
  opacity: 0.72;
}

.project-resource-language-item input {
  width: 16px;
  height: 16px;
}

.project-resource-language-item__body {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.project-resource-language-item__body strong,
.project-resource-language-item__body span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-resource-language-item__body strong {
  color: var(--text-primary);
  font-size: 13px;
}

.project-resource-language-item__body span,
.project-resource-language-item__meta small {
  color: var(--text-muted);
  font-size: 12px;
}

.project-resource-language-item__meta {
  display: grid;
  justify-items: end;
  gap: 2px;
  white-space: nowrap;
}

.tm-settings__bulk {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(190px, 260px);
  gap: 10px;
  align-items: end;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-soft);
  background: color-mix(in srgb, var(--surface-muted) 42%, var(--surface-panel));
}

.tm-settings__bulk-bindings {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--text-muted);
  font-size: 12px;
}

.tm-settings__bulk-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 220px;
  color: var(--text-secondary);
  font-size: 12px;
}

.tm-settings__bulk-item span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tm-settings__primary-select {
  display: grid;
  gap: 4px;
  color: var(--text-muted);
  font-size: 12px;
}

.tm-settings__groups {
  display: grid;
  gap: 9px;
}

.tm-settings__auto-sync {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
}

.tm-settings__auto-sync-switch {
  flex: 0 0 auto;
}

.tm-settings__auto-sync-copy {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.tm-settings__auto-sync-copy strong {
  color: var(--text-primary);
  font-size: 13px;
}

.tm-settings__auto-sync-copy span {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.35;
}

.tm-settings__panel {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.tm-settings__panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line-soft);
  background: color-mix(in srgb, var(--surface-muted) 62%, var(--surface-panel));
}

.tm-settings__panel-head > div:first-child {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.tm-settings__panel-head strong {
  color: var(--text-primary);
  font-size: 13px;
}

.tm-settings__panel-head span {
  color: var(--text-muted);
  font-size: 12px;
}

.tm-settings__panel-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
}

.tm-settings__threshold {
  display: grid;
  grid-template-columns: auto 104px 56px;
  align-items: center;
  gap: 6px;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
}

.tm-settings__threshold input[type='range'] {
  width: 104px;
  accent-color: var(--brand-700);
}

.tm-settings__threshold input[type='number'],
.tm-settings__file-threshold input {
  width: 56px;
  height: 28px;
  padding: 0 7px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--surface-panel);
  color: var(--text-primary);
  font: inherit;
}

.tm-settings__table-wrap {
  overflow-x: auto;
  background: var(--surface-panel);
}

.tm-settings__full-table {
  width: 100%;
  min-width: 820px;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 12px;
}

.tm-settings__full-table th,
.tm-settings__full-table td {
  overflow: hidden;
  padding: 7px 8px;
  border-bottom: 1px solid var(--line-soft);
  color: var(--text-secondary);
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}

.tm-settings__full-table th {
  color: var(--text-muted);
  font-weight: 700;
  background: color-mix(in srgb, var(--surface-muted) 82%, var(--surface-panel));
}

.tm-settings__full-table th:nth-child(4),
.tm-settings__full-table td:nth-child(4) {
  text-align: left;
}

.tm-settings__full-table td:last-child {
  display: flex;
  flex-wrap: nowrap;
  justify-content: center;
  gap: 4px;
  overflow: visible;
  text-overflow: clip;
  white-space: normal;
}

.tm-settings__full-table tr:last-child td {
  border-bottom: 0;
}

.tm-settings__full-col-index {
  width: 44px;
}

.tm-settings__full-col-toggle,
.tm-settings__full-col-write {
  width: 54px;
}

.tm-settings__full-col-name {
  width: auto;
}

.tm-settings__full-col-status {
  width: 56px;
}

.tm-settings__full-col-lang {
  width: 94px;
}

.tm-settings__full-col-count {
  width: 70px;
}

.tm-settings__full-col-files {
  width: 80px;
}

.tm-settings__full-col-action {
  width: 74px;
}

.tm-settings__checkbox {
  width: 14px;
  height: 14px;
  accent-color: var(--brand-700);
}

.tm-settings__file-primary {
  min-width: 150px;
}

.tm-settings__file-button {
  min-width: 30px;
  justify-content: center;
}

.tm-settings__full-table .tm-settings__detail-row td,
.tm-settings__full-table .tm-settings__detail-row td:last-child {
  display: table-cell;
  overflow: visible;
  padding: 8px 10px 10px;
  background: color-mix(in srgb, var(--surface-muted) 68%, var(--surface-panel));
  text-overflow: clip;
  white-space: normal;
}

.tm-settings__file-panel {
  display: grid;
  gap: 6px;
  padding: 9px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: inset 3px 0 0 var(--brand-700);
  text-align: left;
}

.tm-settings__file-panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding-bottom: 2px;
}

.tm-settings__file-panel-head > div:first-child {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.tm-settings__file-panel-head strong {
  color: var(--text-primary);
  font-size: 13px;
}

.tm-settings__file-panel-head span {
  color: var(--text-muted);
  font-size: 12px;
}

.tm-settings__file-summary {
  flex: 0 0 auto;
  white-space: nowrap;
}

.tm-settings__file-panel-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
}

.tm-settings__file-header,
.tm-settings__file-item {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) 82px 92px 96px;
  gap: 8px;
  align-items: center;
}

.tm-settings__batch-button {
  width: 28px;
  min-width: 28px;
  min-height: 28px;
  justify-content: center;
  gap: 0;
  padding: 0;
  border-color: var(--line-soft);
  background: var(--surface-panel);
  color: var(--text-secondary);
  box-shadow: none;
  font-size: 12px;
  font-weight: 600;
}

.pd-settings-panel .tm-settings__batch-button {
  min-height: 28px;
  padding: 0;
}

.tm-settings__batch-button:not(:disabled):hover {
  border-color: var(--line-strong);
  background: var(--surface-muted);
  color: var(--brand-700);
  box-shadow: none;
}

.tm-settings__batch-button.is-active {
  border-color: color-mix(in srgb, var(--brand-700) 34%, var(--line-soft));
  background: color-mix(in srgb, var(--brand-050) 70%, var(--surface-panel));
  color: var(--brand-700);
}

.tm-settings__batch-button .lucide {
  width: 13px;
  height: 13px;
}

.tm-settings__file-header {
  min-height: 28px;
  padding: 0 10px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--surface-muted) 80%, var(--surface-panel));
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 700;
  text-align: center;
}

.tm-settings__file-header span:first-child {
  text-align: left;
}

.tm-settings__file-item {
  min-height: 34px;
  padding: 6px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-panel) 96%, var(--surface-muted));
  transition:
    border-color var(--motion-base) var(--ease-standard),
    background var(--motion-base) var(--ease-standard);
}

.tm-settings__file-item:hover {
  border-color: var(--line-strong);
  background: var(--surface-panel);
}

.tm-settings__file-item:last-child {
  border-bottom: 1px solid var(--line-soft);
}

.tm-settings__file-name {
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tm-settings__file-check {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  color: var(--text-secondary);
  font-size: 12px;
}

.tm-settings__file-check input {
  accent-color: var(--brand-700);
}

.tm-settings__file-threshold {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.pd-resource-create-dialog__hint {
  margin: 10px 0 0;
}

.term-settings__group {
  display: grid;
  overflow: hidden;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.term-settings__group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  padding: 7px 10px;
  border-bottom: 1px solid var(--line-soft);
  background: color-mix(in srgb, var(--surface-muted) 64%, var(--surface-panel));
}

.term-settings__group-summary {
  min-width: 0;
  overflow: hidden;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.term-settings__table-wrap,
.term-qa-report__table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.term-settings__table-wrap {
  border: 0;
  border-radius: 0;
}

.term-settings__table,
.term-qa-report__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.term-settings__table {
  min-width: 660px;
  table-layout: fixed;
}

.term-qa-report__table {
  min-width: 720px;
}

.term-settings__table th,
.term-settings__table td,
.term-qa-report__table th,
.term-qa-report__table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--line-soft);
  color: var(--text-secondary);
  text-align: left;
  vertical-align: middle;
}

.term-settings__table th,
.term-qa-report__table th {
  color: var(--text-muted);
  font-weight: 700;
  background: color-mix(in srgb, var(--surface-muted) 82%, var(--surface-panel));
}

.term-settings__table th:first-child,
.term-settings__table td:first-child {
  text-align: left;
}

.term-settings__table th:not(:first-child),
.term-settings__table td:not(:first-child) {
  text-align: center;
}

.term-settings__table th:first-child {
  width: 38%;
}

.term-settings__table th:nth-child(5),
.term-settings__table td:nth-child(5) {
  width: 110px;
}

.term-settings__table th:last-child,
.term-settings__table td:last-child {
  width: 68px;
}

.term-settings__priority {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 102px;
}

.term-settings__priority strong,
.term-settings__priority span {
  min-width: 30px;
  color: var(--text-secondary);
  font-size: 12px;
}

.term-settings__priority .button--icon {
  width: 24px;
  min-width: 24px;
  height: 24px;
  min-height: 24px;
  padding: 0;
  justify-content: center;
}

.term-settings__name,
.term-settings__meta {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.term-settings__name {
  color: var(--text-primary);
  font-weight: 600;
}

.term-settings__meta {
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 12px;
}

.term-settings__table tr:last-child td,
.term-qa-report__table tr:last-child td {
  border-bottom: 0;
}

.term-settings__toggle {
  position: relative;
  display: inline-grid;
  place-items: center;
  width: 32px;
  height: 18px;
  cursor: pointer;
}

.term-settings__toggle input {
  position: absolute;
  inset: 0;
  margin: 0;
  opacity: 0;
  cursor: pointer;
}

.term-settings__toggle span {
  position: relative;
  width: 32px;
  height: 18px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: color-mix(in srgb, var(--surface-muted) 78%, var(--surface-panel));
  transition:
    border-color var(--motion-base) var(--ease-standard),
    background var(--motion-base) var(--ease-standard);
}

.term-settings__toggle span::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--text-muted);
  transition:
    background var(--motion-base) var(--ease-standard),
    transform var(--motion-base) var(--ease-standard);
}

.term-settings__toggle input:checked + span {
  border-color: var(--brand-700);
  background: color-mix(in srgb, var(--brand-700) 82%, var(--brand-500));
}

.term-settings__toggle input:checked + span::after {
  background: #ffffff;
  transform: translateX(14px);
}

.term-settings__toggle input:focus-visible + span {
  box-shadow: var(--focus-ring);
}

.term-settings__tip {
  display: inline-grid;
  place-items: center;
  width: 15px;
  height: 15px;
  margin-left: 3px;
  border-radius: 50%;
  color: var(--brand-700);
  background: color-mix(in srgb, var(--brand-700) 12%, transparent);
  font-size: 11px;
  cursor: help;
}

.automation-settings {
  max-width: 680px;
  gap: 18px;
}

.automation-settings__group {
  display: grid;
  gap: 8px;
}

.automation-settings__group h3 {
  margin: 0 0 2px;
  color: var(--text-primary);
  font-size: 18px;
  line-height: 1.25;
}

.automation-settings__check {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  min-height: 24px;
  color: var(--brand-700);
  font-size: 14px;
  line-height: 1.3;
}

.automation-settings__check.is-connected {
  cursor: pointer;
}

.automation-settings__check.is-connected.is-busy {
  opacity: 0.72;
  pointer-events: none;
}

.automation-settings__check input {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: var(--brand-700);
}

.automation-settings__check input:not(:disabled) {
  cursor: pointer;
}

.automation-settings__check small {
  color: var(--text-muted);
  font-size: 12px;
}

.automation-settings__check.is-placeholder {
  opacity: 0.84;
}

.automation-settings__check.is-placeholder input {
  cursor: not-allowed;
}

.automation-settings__check svg {
  color: var(--brand-700);
}

.automation-settings__children,
.automation-settings__lock-grid {
  display: grid;
  gap: 7px;
  padding-left: 24px;
}

.quality-qa-settings__content {
  display: grid;
  gap: 16px;
}

.quality-qa-settings__status-item small,
.quality-qa-settings__language-chip small,
.quality-qa-settings__language-head span {
  color: var(--text-muted);
}

.quality-qa-settings__rule-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border-muted);
  border-radius: 8px;
  background: var(--surface-primary);
}

.quality-qa-settings__rule-table {
  width: 100%;
  min-width: 560px;
  border-collapse: collapse;
  table-layout: fixed;
}

.quality-qa-settings__rule-table th,
.quality-qa-settings__rule-table td {
  height: 49px;
  padding: 0 14px;
  border-bottom: 1px solid var(--border-muted);
  color: var(--text-primary);
  font-size: 14px;
  text-align: left;
  vertical-align: middle;
}

.quality-qa-settings__rule-table th {
  background: var(--surface-muted);
  font-weight: 700;
}

.quality-qa-settings__rule-table th:first-child,
.quality-qa-settings__rule-table td:first-child {
  width: 58px;
  text-align: center;
}

.quality-qa-settings__rule-table th:nth-child(2),
.quality-qa-settings__rule-table td:nth-child(2) {
  width: 48px;
}

.quality-qa-settings__rule-table tbody tr:last-child td {
  border-bottom: 0;
}

.quality-qa-settings__rule-table tbody tr:hover {
  background: color-mix(in srgb, var(--brand-050) 48%, transparent);
}

.quality-qa-settings__rule-table tbody tr.is-placeholder td {
  color: var(--text-secondary);
}

.quality-qa-settings__check-cell {
  text-align: center;
}

.quality-qa-settings__rule-check {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
}

.quality-qa-settings__rule-check input {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: var(--brand-700);
}

.quality-qa-settings__rule-check input:not(:disabled) {
  cursor: pointer;
}

.quality-qa-settings__rule-check input:disabled {
  cursor: not-allowed;
}

.quality-qa-settings__rule-text {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.quality-qa-settings__inline-number {
  width: 40px;
  height: 24px;
  padding: 0 4px;
  border: 1px solid var(--border-muted);
  border-radius: 3px;
  background: var(--surface-muted);
  color: var(--text-primary);
  font: inherit;
  text-align: center;
}

.quality-qa-settings__status-grid,
.quality-qa-settings__language-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}

.quality-qa-settings__status-item,
.quality-qa-settings__language-chip {
  display: grid;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--border-muted);
  border-radius: 8px;
  background: var(--surface-primary);
}

.quality-qa-settings__status-item span {
  color: var(--text-muted);
  font-size: 13px;
}

.quality-qa-settings__status-item .is-ok,
.quality-qa-settings__language-chip.is-supported strong {
  color: #227f58;
}

.quality-qa-settings__status-item .is-warn,
.quality-qa-settings__language-chip.is-unsupported strong {
  color: #b54708;
}

.quality-qa-settings__language-list {
  display: grid;
  gap: 10px;
}

.quality-qa-settings__language-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.term-qa-report__actions {
  justify-content: flex-end;
}

.term-qa-report__summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  gap: 8px;
}

.term-qa-report__summary span {
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-muted) 72%, var(--surface-panel));
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

@media (max-width: 960px) {
  .upload-page__main {
    grid-template-columns: 1fr;
  }

  .doc-settings {
    border-left: 0;
    border-top: 1px solid #dbe3e1;
  }

  .pd-basic-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .pd-settings-layout {
    grid-template-columns: 1fr;
  }

  .pd-settings-rail {
    position: static;
    display: flex;
    overflow-x: auto;
    min-height: 0;
    padding: 8px;
    border-right: 0;
    border-bottom: 1px solid var(--line-soft);
  }

  .pd-settings-rail__item {
    flex: 0 0 auto;
    justify-content: center;
    min-height: 34px;
    padding: 0 12px;
    border-radius: 6px;
  }

  .pd-readonly-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .pd-statistics-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .pd-toolbar__right {
    width: 100%;
    justify-content: flex-start;
  }

  .pd-file-filters {
    flex: 1 1 100%;
    justify-content: flex-start;
  }

  .pd-file-filter--search {
    width: auto;
    max-width: none;
  }

}

@media (max-width: 720px) {
  .upload-page__topbar {
    padding: 0 14px;
  }

  .upload-page__workspace,
  .doc-settings {
    padding: 14px;
  }

  .upload-language-panel__head {
    flex-direction: column;
    align-items: stretch;
  }

  .upload-detect-button {
    justify-content: center;
  }

  .upload-language-grid,
  .doc-settings__grid {
    grid-template-columns: 1fr;
  }

  .upload-target-select__popover {
    min-width: 0;
  }

  .pd-settings-list,
  .pd-readonly-grid {
    grid-template-columns: 1fr;
  }

  .pd-settings-section-head {
    flex-direction: column;
    align-items: stretch;
  }

  .pd-settings-section {
    padding: 16px;
  }

  .pd-settings-section-head__copy {
    grid-template-columns: 34px minmax(0, 1fr);
  }

  .pd-settings-actions,
  .term-qa-report__actions {
    width: 100%;
    justify-content: flex-start;
  }

  .pd-settings-actions .button {
    flex: 1 1 140px;
    justify-content: center;
  }

  .pd-file-filters__lead {
    display: none;
  }

  .pd-toolbar {
    display: grid;
    grid-template-columns: minmax(0, calc(100vw - 72px));
    align-items: flex-start;
    justify-content: flex-start;
  }

  .pd-toolbar__left,
  .pd-toolbar__right {
    flex: 1 1 100%;
    width: 100%;
    max-width: calc(100vw - 72px);
  }

  .pd-file-selection,
  .pd-toolbar-action-strip,
  .pd-file-filters {
    width: calc(100vw - 72px);
    max-width: calc(100vw - 72px);
  }

  .pd-file-selection__trigger {
    width: 100%;
    justify-content: center;
  }

  .pd-file-selection__menu {
    width: calc(100vw - 72px);
    max-width: calc(100vw - 72px);
  }

  .pd-file-filter--search,
  .pd-file-filter__select,
  .pd-file-filter__summary,
  .pd-file-filter__reset {
    width: 100%;
  }

  .pd-file-filter__summary {
    white-space: normal;
  }

  .tm-settings__bulk {
    grid-template-columns: 1fr;
  }

  .tm-settings__file-item {
    grid-template-columns: 1fr;
    gap: 6px;
    align-items: start;
    padding: 10px;
  }

  .tm-settings__file-panel-actions {
    flex-wrap: wrap;
    justify-content: flex-start;
  }

  .tm-settings__batch-button {
    flex: 0 1 auto;
  }

  .tm-settings__file-header {
    display: none;
  }

  .tm-settings__file-panel-head {
    display: grid;
  }

  .tm-settings__file-check {
    justify-content: flex-start;
  }

  .tm-settings__file-threshold {
    justify-content: flex-start;
  }

  .resource-settings-block__head {
    align-items: stretch;
  }

  .pd-settings-save {
    flex: 0 0 auto;
    width: 100%;
    justify-content: center;
  }

  .pd-settings-row,
  .pd-settings-row--name {
    grid-template-columns: 1fr;
    align-items: stretch;
  }

  .pd-assignment-dialog--project {
    grid-template-columns: 1fr;
    min-height: 0;
  }

  .pd-assignment-users,
  .pd-assignment-files {
    min-height: 0;
    max-height: none;
  }

  .pd-assignment-users {
    max-height: 340px;
  }

  .pd-assignment-files {
    max-height: 520px;
  }

  .pd-assignment-filter-row,
  .pd-assignment-file-toolbar {
    grid-template-columns: 1fr;
  }

  .pd-assignment-file-group__actions {
    width: 100%;
    justify-content: flex-start;
  }

  .pd-assignment-file-action {
    flex: 1 1 140px;
  }

  .pd-assignment-file-option {
    grid-template-columns: 1fr;
    align-items: stretch;
  }

  .pd-assignment-range-controls {
    grid-template-columns: auto minmax(0, 1fr) auto minmax(0, 1fr);
  }

  .pd-assignment-range-controls input {
    width: 100%;
  }

  .pd-settings-control,
  .pd-settings-control--name {
    max-width: none;
  }

  .term-settings__table {
    min-width: 0;
  }

  .term-settings__table th,
  .term-settings__table td {
    padding: 8px 6px;
  }

  .term-settings__table th:first-child {
    width: 52%;
  }

  .term-settings__table th:not(:first-child),
  .term-settings__table td:not(:first-child) {
    width: 16%;
  }

  .term-settings__name,
  .term-settings__meta {
    white-space: normal;
    overflow-wrap: anywhere;
  }

  .term-settings__tip {
    display: none;
  }

  .pd-statistics-summary {
    grid-template-columns: 1fr;
  }

  .upload-page__actions {
    flex-direction: column;
  }

  .pd-hero__main,
  .pd-panel-head {
    flex-direction: column;
    align-items: stretch;
  }

  .pd-hero__progress {
    width: 100%;
  }

  .pd-basic-grid {
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .pd-tabs {
    overflow-x: auto;
  }

  .pd-upload-picker {
    grid-template-columns: 1fr;
  }

  .issue-item {
    flex-direction: column;
  }

  .issue-item__action {
    width: 100%;
    justify-content: center;
  }
}
</style>
