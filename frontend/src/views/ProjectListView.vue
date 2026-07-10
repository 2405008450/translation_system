<script setup lang="ts">
import axios from 'axios'
import {
  Copy,
  Database,
  FileText,
  Filter,
  Flag,
  FolderOpen,
  Loader2,
  Plus,
  Search,
  Settings2,
  Trash2,
  Users,
  X,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import Modal from '../components/base/Modal.vue'
import DataTable from '../components/DataTable.vue'
import IssueMarkerDialog from '../components/IssueMarkerDialog.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import Pagination from '../components/Pagination.vue'
import RowActionMenu from '../components/RowActionMenu.vue'
import WorkflowProgressSummary from '../components/WorkflowProgressSummary.vue'
import { useConfirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'
import { formatLanguagePair, languageOptions } from '../constants/languages'
import { getFileStatusMeta } from '../constants/status'
import type { IssueMarker, User, WorkflowProgress, WorkflowStep, WorkflowTemplate } from '../types/api'
import { useAuthStore } from '../stores/auth'

interface ProjectItem {
  id: string
  name: string
  filename: string
  status: string
  progress: number
  file_count: number
  issue_count: number
  open_issue_count: number
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
  deadline: string | null
  access_level: string | null
  assigned_users?: User[]
  can_manage?: boolean
  can_write?: boolean
  created_at: string
  updated_at: string
}

interface ProjectListResponse {
  items: ProjectItem[]
  total: number
  skip: number
  limit: number
}

interface ProjectCreateResponse {
  id: string
}

interface ProjectFileItem {
  id: string
  filename: string
  source_language: string | null
  target_language: string | null
}

interface ProjectDetailResponse extends ProjectItem {
  translation_guidelines: string
  files: ProjectFileItem[]
}

interface EditableWorkflowStep {
  step_key: string
  name: string
  step_type: string
}

type ProjectDeadlineScope = '' | 'overdue' | 'due_soon' | 'no_deadline'
type ProjectFileCountScope = '' | 'has_files' | 'no_files'

interface ProjectFilters {
  status: string
  access_level: string
  source_language: string
  target_language: string
  creator: string
  deadline_scope: ProjectDeadlineScope
  file_count_scope: ProjectFileCountScope
  created_from: string
  created_to: string
  deadline_from: string
  deadline_to: string
}

interface SelectOption {
  value: string
  label: string
}

const UNSET_FILTER_VALUE = '__unset__'
const FILTER_FETCH_LIMIT = 200

const projectLanguageLabels: Record<string, string> = {
  'zh-CN': '中文（简体）',
  'zh-TW': '中文（繁体）',
  'zh-HK': '中文（香港）',
  'zh-MO': '中文（澳门）',
  'en-US': '英语（美国）',
  'en-GB': '英语（英国）',
  'ja-JP': '日语',
  'ko-KR': '韩语',
  'fr-FR': '法语',
  'de-DE': '德语',
  'es-ES': '西班牙语（欧洲）',
  'es-419': '西班牙语（拉美）',
  'pt-BR': '葡萄牙语（巴西）',
  'it-IT': '意大利语',
  'ru-RU': '俄语',
  'ar-SA': '阿拉伯语',
  'th-TH': '泰语',
  'vi-VN': '越南语',
}

const confirm = useConfirm()
const toast = useToast()
const router = useRouter()
const authStore = useAuthStore()
const { t } = useI18n()

const loading = ref(false)
const projects = ref<ProjectItem[]>([])
const totalCount = ref(0)
const currentPage = ref(1)
const pageSize = ref(10)
const searchQuery = ref('')
const sortKey = ref('')
const sortOrder = ref<'asc' | 'desc'>('desc')
const pageError = ref('')
const selectedProjectIds = ref(new Set<string>())

const showCreateDialog = ref(false)
const creating = ref(false)
const formError = ref('')
const showIssueDialog = ref(false)
const issueTarget = ref<ProjectItem | null>(null)
const showDuplicateDialog = ref(false)
const loadingDuplicateDialog = ref(false)
const duplicatingProject = ref(false)
const duplicateError = ref('')
const duplicateTemplates = ref<ProjectItem[]>([])
const duplicateTemplateProjectId = ref('')
const duplicateTemplateDetail = ref<ProjectDetailResponse | null>(null)
const workflowTemplates = ref<WorkflowTemplate[]>([])
const loadingWorkflowTemplates = ref(false)
const showFilterDialog = ref(false)
const loadingFilterOptions = ref(false)
const filterProjectPool = ref<ProjectItem[]>([])
const filterProjectPoolSearch = ref<string | null>(null)

const defaultForm = () => ({
  name: '',
  source_language: '',
  target_language: '',
  deadline: '',
  access_level: 'team' as 'team' | 'private' | 'public',
  workflow_template_id: '',
  workflow_steps: [] as EditableWorkflowStep[],
})

const form = reactive(defaultForm())

const defaultDuplicateForm = () => ({
  name: '',
  deadline: '',
  access_level: 'team' as 'team' | 'private' | 'public',
})

const duplicateForm = reactive(defaultDuplicateForm())

function defaultProjectFilters(): ProjectFilters {
  return {
    status: '',
    access_level: '',
    source_language: '',
    target_language: '',
    creator: '',
    deadline_scope: '',
    file_count_scope: '',
    created_from: '',
    created_to: '',
    deadline_from: '',
    deadline_to: '',
  }
}

const projectFilters = reactive<ProjectFilters>(defaultProjectFilters())
const filterDraft = reactive<ProjectFilters>(defaultProjectFilters())

const accessOptions = computed(() => ([
  { value: 'team', label: t('projectList.form.team') },
  { value: 'private', label: t('projectList.form.private') },
  { value: 'public', label: t('projectList.form.public') },
]))
const filterOptionSource = computed(() => (
  filterProjectPool.value.length > 0 ? filterProjectPool.value : projects.value
))
const deadlineScopeOptions: SelectOption[] = [
  { value: '', label: '全部截止期限' },
  { value: 'overdue', label: '已逾期' },
  { value: 'due_soon', label: '7 天内到期' },
  { value: 'no_deadline', label: '未设置截止时间' },
]
const fileCountScopeOptions: SelectOption[] = [
  { value: '', label: '全部文件数量' },
  { value: 'has_files', label: '已有文件' },
  { value: 'no_files', label: '暂无文件' },
]
const activeProjectFilterCount = computed(() => getActiveProjectFilterCount(projectFilters))
const hasActiveProjectFilters = computed(() => activeProjectFilterCount.value > 0)
const statusFilterOptions = computed<SelectOption[]>(() => ([
  { value: '', label: '全部状态' },
  ...createOptionList(
    filterOptionSource.value,
    (project) => project.status,
    (status) => getFileStatusMeta(status).label,
  ),
]))
const accessFilterOptions = computed<SelectOption[]>(() => ([
  { value: '', label: '全部访问权限' },
  ...accessOptions.value,
]))
const sourceLanguageFilterOptions = computed<SelectOption[]>(() => ([
  { value: '', label: '全部源语言' },
  ...createNullableOptionList(
    filterOptionSource.value,
    (project) => project.source_language,
    getProjectLanguageLabel,
  ),
]))
const targetLanguageFilterOptions = computed<SelectOption[]>(() => ([
  { value: '', label: '全部目标语言' },
  ...createNullableOptionList(
    filterOptionSource.value,
    (project) => project.target_language,
    getProjectLanguageLabel,
  ),
]))
const creatorFilterOptions = computed<SelectOption[]>(() => ([
  { value: '', label: '全部创建人' },
  ...createNullableOptionList(
    filterOptionSource.value,
    (project) => project.creator,
    (creator) => creator,
  ),
]))
const projectFilterTags = computed(() => {
  const tags: string[] = []
  if (projectFilters.status) {
    tags.push(`状态：${getFileStatusMeta(projectFilters.status).label}`)
  }
  if (projectFilters.access_level) {
    tags.push(`权限：${getAccessLabel(projectFilters.access_level)}`)
  }
  if (projectFilters.source_language) {
    tags.push(`源语言：${getNullableFilterLabel(projectFilters.source_language, getProjectLanguageLabel)}`)
  }
  if (projectFilters.target_language) {
    tags.push(`目标语言：${getNullableFilterLabel(projectFilters.target_language, getProjectLanguageLabel)}`)
  }
  if (projectFilters.creator) {
    tags.push(`创建人：${getNullableFilterLabel(projectFilters.creator, (value) => value)}`)
  }
  if (projectFilters.deadline_scope) {
    tags.push(getOptionLabel(deadlineScopeOptions, projectFilters.deadline_scope))
  }
  if (projectFilters.file_count_scope) {
    tags.push(getOptionLabel(fileCountScopeOptions, projectFilters.file_count_scope))
  }
  const createdRange = formatDateRangeLabel(projectFilters.created_from, projectFilters.created_to)
  if (createdRange) {
    tags.push(`创建时间：${createdRange}`)
  }
  const deadlineRange = formatDateRangeLabel(projectFilters.deadline_from, projectFilters.deadline_to)
  if (deadlineRange) {
    tags.push(`截止时间：${deadlineRange}`)
  }
  return tags
})
const canManageProjects = computed(() => authStore.isBusinessManager)
const canCreateProjects = computed(() => authStore.isBusinessManager)
const canAssignProjects = computed(() => authStore.isBusinessManager)
const isCreateLanguagePairPartiallySelected = computed(() => (
  Boolean(form.source_language) !== Boolean(form.target_language)
))
const isCreateLanguagePairDuplicated = computed(() => (
  Boolean(form.source_language)
  && Boolean(form.target_language)
  && form.source_language === form.target_language
))
const canSubmitCreate = computed(() => (
  canCreateProjects.value
  && !creating.value
  && Boolean(form.name.trim())
  && Boolean(form.workflow_template_id)
  && form.workflow_steps.length > 0
  && form.workflow_steps.every((step) => step.name.trim())
  && !isCreateLanguagePairPartiallySelected.value
  && !isCreateLanguagePairDuplicated.value
))

const columns = computed<DataTableColumn[]>(() => ([
  { key: 'filename', label: t('common.actions.details'), width: '320px', sortable: true },
  { key: 'status', label: t('projectList.status.current'), width: '90px', align: 'center' },
  { key: 'progress', label: t('projectList.status.confirmedProgress'), width: '140px', align: 'center' },
  { key: 'file_count', label: t('projectDetail.base.fileCount'), width: '70px', align: 'center' },
  { key: 'open_issue_count', label: t('issueMarker.list.title'), width: '80px', align: 'center' },
]))

const indexOffset = computed(() => (currentPage.value - 1) * pageSize.value)
const selectedProjects = computed(() => (
  projects.value.filter((project) => selectedProjectIds.value.has(project.id))
))
const duplicateButtonTitle = computed(() => {
  if (selectedProjectIds.value.size > 1) {
    return t('projectList.duplicate.selectOne')
  }
  if (selectedProjectIds.value.size === 1) {
    return t('projectList.duplicate.useSelected')
  }
  return t('projectList.duplicate.selectHint')
})
const canOpenDuplicateDialog = computed(() => (
  canCreateProjects.value
  && selectedProjectIds.value.size <= 1
  && !loadingDuplicateDialog.value
  && !duplicatingProject.value
))
const selectedDuplicateTemplate = computed(() => (
  duplicateTemplates.value.find((project) => project.id === duplicateTemplateProjectId.value) || null
))
const duplicateTemplateFileCount = computed(() => duplicateTemplateDetail.value?.files.length ?? 0)

function getProjectLanguageLabel(code: string) {
  return projectLanguageLabels[code] || code
}

function getNullableFilterLabel(value: string, labelGetter: (value: string) => string) {
  return value === UNSET_FILTER_VALUE ? '未设置' : labelGetter(value)
}

function getOptionLabel(options: SelectOption[], value: string) {
  return options.find((option) => option.value === value)?.label || value
}

function createOptionList(
  items: ProjectItem[],
  valueGetter: (project: ProjectItem) => string | null | undefined,
  labelGetter: (value: string) => string,
): SelectOption[] {
  const values = Array.from(new Set(
    items.map((item) => (valueGetter(item) || '').trim()).filter(Boolean),
  ))
  return values
    .map((value) => ({ value, label: labelGetter(value) }))
    .sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'))
}

function createNullableOptionList(
  items: ProjectItem[],
  valueGetter: (project: ProjectItem) => string | null | undefined,
  labelGetter: (value: string) => string,
): SelectOption[] {
  const values = new Set<string>()
  let hasUnset = false
  items.forEach((item) => {
    const value = (valueGetter(item) || '').trim()
    if (value) {
      values.add(value)
    } else {
      hasUnset = true
    }
  })
  const options = Array.from(values)
    .map((value) => ({ value, label: labelGetter(value) }))
    .sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'))
  return hasUnset ? [{ value: UNSET_FILTER_VALUE, label: '未设置' }, ...options] : options
}

function getActiveProjectFilterCount(filters: ProjectFilters) {
  const directCount = [
    filters.status,
    filters.access_level,
    filters.source_language,
    filters.target_language,
    filters.creator,
    filters.deadline_scope,
    filters.file_count_scope,
  ].filter(Boolean).length
  const rangeCount = [
    filters.created_from || filters.created_to,
    filters.deadline_from || filters.deadline_to,
  ].filter(Boolean).length
  return directCount + rangeCount
}

function copyProjectFilters(source: ProjectFilters, target: ProjectFilters) {
  Object.assign(target, {
    status: source.status,
    access_level: source.access_level,
    source_language: source.source_language,
    target_language: source.target_language,
    creator: source.creator,
    deadline_scope: source.deadline_scope,
    file_count_scope: source.file_count_scope,
    created_from: source.created_from,
    created_to: source.created_to,
    deadline_from: source.deadline_from,
    deadline_to: source.deadline_to,
  })
}

function resetProjectFilters(target: ProjectFilters) {
  copyProjectFilters(defaultProjectFilters(), target)
}

function invalidateProjectFilterPool() {
  filterProjectPool.value = []
  filterProjectPoolSearch.value = null
}

function formatDateRangeLabel(from: string, to: string) {
  if (from && to) {
    return `${from} 至 ${to}`
  }
  if (from) {
    return `${from} 之后`
  }
  if (to) {
    return `${to} 之前`
  }
  return ''
}

function parseDateForFilter(value: string | null | undefined, endOfDay = false) {
  if (!value) {
    return null
  }
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value)
    ? `${value}T${endOfDay ? '23:59:59.999' : '00:00:00'}`
    : value)
  return Number.isNaN(date.getTime()) ? null : date
}

function matchesNullableFilter(value: string | null | undefined, filterValue: string) {
  if (!filterValue) {
    return true
  }
  if (filterValue === UNSET_FILTER_VALUE) {
    return !(value || '').trim()
  }
  return (value || '').trim() === filterValue
}

function matchesDateRange(value: string | null | undefined, from: string, to: string) {
  if (!from && !to) {
    return true
  }
  const date = parseDateForFilter(value)
  if (!date) {
    return false
  }
  const start = parseDateForFilter(from)
  const end = parseDateForFilter(to, true)
  if (start && date < start) {
    return false
  }
  if (end && date > end) {
    return false
  }
  return true
}

function matchesDeadlineScope(project: ProjectItem, scope: ProjectDeadlineScope) {
  if (!scope) {
    return true
  }
  if (scope === 'no_deadline') {
    return !project.deadline
  }
  const deadline = parseDateForFilter(project.deadline)
  if (!deadline) {
    return false
  }
  const now = new Date()
  if (scope === 'overdue') {
    return deadline < now
  }
  const dueSoonEnd = new Date(now)
  dueSoonEnd.setDate(now.getDate() + 7)
  return deadline >= now && deadline <= dueSoonEnd
}

function matchesFileCountScope(project: ProjectItem, scope: ProjectFileCountScope) {
  if (!scope) {
    return true
  }
  const fileCount = Number(project.file_count || 0)
  return scope === 'has_files' ? fileCount > 0 : fileCount === 0
}

function projectMatchesFilters(project: ProjectItem, filters: ProjectFilters) {
  return matchesNullableFilter(project.status, filters.status)
    && matchesNullableFilter(project.access_level, filters.access_level)
    && matchesNullableFilter(project.source_language, filters.source_language)
    && matchesNullableFilter(project.target_language, filters.target_language)
    && matchesNullableFilter(project.creator, filters.creator)
    && matchesDeadlineScope(project, filters.deadline_scope)
    && matchesFileCountScope(project, filters.file_count_scope)
    && matchesDateRange(project.created_at, filters.created_from, filters.created_to)
    && matchesDateRange(project.deadline, filters.deadline_from, filters.deadline_to)
}

function getProjectSortValue(project: ProjectItem, key: string) {
  if (key === 'filename') {
    return (project.filename || project.name || '').toLowerCase()
  }
  if (key === 'status') {
    return getFileStatusMeta(project.status).label
  }
  if (key === 'progress') {
    return Number(project.progress || 0)
  }
  if (key === 'file_count') {
    return Number(project.file_count || 0)
  }
  if (key === 'open_issue_count') {
    return Number(project.open_issue_count || 0)
  }
  return String((project as unknown as Record<string, unknown>)[key] ?? '')
}

function sortProjectRows(items: ProjectItem[]) {
  if (!sortKey.value) {
    return items
  }
  return [...items].sort((left, right) => {
    const leftValue = getProjectSortValue(left, sortKey.value)
    const rightValue = getProjectSortValue(right, sortKey.value)
    const result = typeof leftValue === 'number' && typeof rightValue === 'number'
      ? leftValue - rightValue
      : String(leftValue).localeCompare(String(rightValue), 'zh-CN')
    return sortOrder.value === 'asc' ? result : -result
  })
}

function normalizeWorkflowStepKey(value: string, index: number) {
  const normalized = value.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
  return normalized || (index === 0 ? 'translate' : `step_${index + 1}`)
}

function cloneWorkflowSteps(template: WorkflowTemplate): EditableWorkflowStep[] {
  return template.steps.map((step, index) => ({
    step_key: index === 0 ? 'translate' : normalizeWorkflowStepKey(step.step_key || '', index),
    name: index === 0 ? '翻译' : step.name,
    step_type: index === 0 ? 'translation' : (step.step_type || 'custom'),
  }))
}

async function loadWorkflowTemplates() {
  if (workflowTemplates.value.length > 0 || loadingWorkflowTemplates.value) {
    return
  }
  loadingWorkflowTemplates.value = true
  try {
    const { data } = await http.get<{ items: WorkflowTemplate[] }>('/workflow-templates')
    workflowTemplates.value = data.items || []
  } catch (error) {
    formError.value = getProjectListError(error, '工作流模板加载失败')
  } finally {
    loadingWorkflowTemplates.value = false
  }
}

function handleWorkflowTemplateChange() {
  const template = workflowTemplates.value.find((item) => item.id === form.workflow_template_id)
  form.workflow_steps = template ? cloneWorkflowSteps(template) : []
}

function addWorkflowStep() {
  const index = form.workflow_steps.length
  form.workflow_steps.push({
    step_key: `step_${index + 1}`,
    name: `阶段 ${index + 1}`,
    step_type: 'custom',
  })
}

function removeWorkflowStep(index: number) {
  if (index <= 0) {
    return
  }
  form.workflow_steps.splice(index, 1)
}

function getWorkflowProgressItems(row: ProjectItem) {
  return row.workflow_progress || []
}

function isProjectAssignedToCurrentUser(project: ProjectItem) {
  const currentUserId = authStore.user?.id
  if (!authStore.isExternalTranslator || !currentUserId) {
    return true
  }
  return (project.assigned_users || []).some((user) => user.id === currentUserId)
}

function getProjectListError(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

async function loadFilterProjectPool(force = false) {
  const normalizedSearch = searchQuery.value.trim()
  if (!force && filterProjectPoolSearch.value === normalizedSearch) {
    return filterProjectPool.value
  }

  loadingFilterOptions.value = true
  const items: ProjectItem[] = []
  let skip = 0
  let total = 0

  try {
    do {
      const { data } = await http.get<ProjectListResponse>('/projects', {
        params: { skip, limit: FILTER_FETCH_LIMIT, search: normalizedSearch },
      })
      items.push(...data.items.filter(isProjectAssignedToCurrentUser))
      total = data.total
      const limit = Math.max(data.limit || FILTER_FETCH_LIMIT, 1)
      skip += limit
      if (data.items.length === 0) {
        break
      }
    } while (skip < total)

    filterProjectPool.value = items
    filterProjectPoolSearch.value = normalizedSearch
    return items
  } finally {
    loadingFilterOptions.value = false
  }
}

async function loadProjects() {
  loading.value = true
  pageError.value = ''
  try {
    if (hasActiveProjectFilters.value) {
      const candidateProjects = await loadFilterProjectPool()
      const filteredProjects = sortProjectRows(
        candidateProjects.filter((project) => projectMatchesFilters(project, projectFilters)),
      )
      const start = (currentPage.value - 1) * pageSize.value
      projects.value = filteredProjects.slice(start, start + pageSize.value)
      totalCount.value = filteredProjects.length
      syncSelectedProjectsToVisibleRows()
      return
    }

    const skip = authStore.isExternalTranslator ? 0 : (currentPage.value - 1) * pageSize.value
    const limit = authStore.isExternalTranslator ? 200 : pageSize.value
    const { data } = await http.get<ProjectListResponse>('/projects', {
      params: { skip, limit, search: searchQuery.value.trim() },
    })
    const visibleItems = data.items.filter(isProjectAssignedToCurrentUser)
    if (authStore.isExternalTranslator) {
      const start = (currentPage.value - 1) * pageSize.value
      projects.value = visibleItems.slice(start, start + pageSize.value)
      totalCount.value = visibleItems.length
      syncSelectedProjectsToVisibleRows()
      return
    }
    projects.value = visibleItems
    totalCount.value = data.total
    syncSelectedProjectsToVisibleRows()
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || t('projectList.errors.load'))
      return
    }
    pageError.value = error instanceof Error ? error.message : t('projectList.errors.load')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, defaultForm())
  formError.value = ''
}

function openCreateDialog() {
  if (!canCreateProjects.value) {
    return
  }
  resetForm()
  showCreateDialog.value = true
  void loadWorkflowTemplates()
}

async function createProject() {
  if (!canCreateProjects.value) {
    return
  }
  if (!form.name.trim()) {
    formError.value = t('projectList.errors.requiredName')
    return
  }
  if (!form.workflow_template_id || form.workflow_steps.length === 0) {
    formError.value = '请选择工作流模板'
    return
  }
  if (isCreateLanguagePairPartiallySelected.value) {
    formError.value = form.source_language
      ? t('projectList.errors.requiredTarget')
      : t('projectList.errors.requiredSource')
    return
  }
  if (isCreateLanguagePairDuplicated.value) {
    formError.value = t('projectList.errors.sameLanguage')
    return
  }
  const workflowSteps = form.workflow_steps.map((step, index) => ({
    step_key: index === 0 ? 'translate' : normalizeWorkflowStepKey(step.step_key, index),
    name: index === 0 ? '翻译' : step.name.trim(),
    step_type: index === 0 ? 'translation' : (step.step_type || 'custom'),
  }))

  creating.value = true
  formError.value = ''
  try {
    const { data } = await http.post<ProjectCreateResponse>('/projects', {
      name: form.name.trim(),
      source_language: form.source_language || null,
      target_language: form.target_language || null,
      deadline: form.deadline || null,
      access_level: form.access_level,
      workflow_template_id: form.workflow_template_id,
      workflow_steps: workflowSteps,
    })
    showCreateDialog.value = false
    resetForm()
    toast.success(t('projectList.messages.created'))
    await router.push({
      name: 'project-detail',
      params: { id: data.id },
    })
  } catch (error) {
    if (axios.isAxiosError(error)) {
      formError.value = String(error.response?.data?.detail || t('projectList.errors.create'))
      return
    }
    formError.value = error instanceof Error ? error.message : t('projectList.errors.create')
  } finally {
    creating.value = false
  }
}

function syncSelectedProjectsToVisibleRows() {
  const visibleIds = new Set(projects.value.map((project) => project.id))
  selectedProjectIds.value = new Set(
    Array.from(selectedProjectIds.value).filter((id) => visibleIds.has(id)),
  )
}

function handleProjectSelection(ids: Set<string>) {
  selectedProjectIds.value = new Set(ids)
}

function reloadProjectsFromFirstPage() {
  if (currentPage.value === 1) {
    void loadProjects()
    return
  }
  currentPage.value = 1
}

function openFilterDialog() {
  const nextOpen = !showFilterDialog.value
  showFilterDialog.value = nextOpen
  if (!nextOpen) {
    return
  }
  copyProjectFilters(projectFilters, filterDraft)
  void loadFilterProjectPool().catch((error) => {
    pageError.value = getProjectListError(error, t('projectList.errors.load'))
  })
}

function resetFilterDraft() {
  resetProjectFilters(filterDraft)
}

function applyProjectFilters() {
  copyProjectFilters(filterDraft, projectFilters)
  showFilterDialog.value = false
  reloadProjectsFromFirstPage()
}

function clearProjectFilters() {
  const hadFilters = hasActiveProjectFilters.value
  resetProjectFilters(projectFilters)
  resetProjectFilters(filterDraft)
  showFilterDialog.value = false
  if (hadFilters) {
    reloadProjectsFromFirstPage()
  }
}

function closeProjectFilterPanel() {
  showFilterDialog.value = false
}

function handleProjectFilterDocumentClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null
  if (
    !showFilterDialog.value
    || !target
    || target.closest('.project-filter-popover')
    || target.closest('.project-filter-button')
  ) {
    return
  }
  closeProjectFilterPanel()
}

function resetDuplicateDialog() {
  Object.assign(duplicateForm, defaultDuplicateForm())
  duplicateError.value = ''
  duplicateTemplateProjectId.value = ''
  duplicateTemplateDetail.value = null
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

function buildDuplicateProjectName(name: string) {
  const suffix = ' - 副本'
  const baseName = (name || t('projectDetail.titleFallback')).trim()
  return `${baseName.slice(0, Math.max(1, 200 - suffix.length))}${suffix}`
}

function normalizeAccessLevel(level: string | null | undefined): 'team' | 'private' | 'public' {
  return level === 'private' || level === 'public' || level === 'team' ? level : 'team'
}

function syncDuplicateFormFromTemplate(detail: ProjectDetailResponse) {
  duplicateForm.name = buildDuplicateProjectName(detail.name || detail.filename)
  duplicateForm.deadline = formatDateTimeLocalValue(detail.deadline)
  duplicateForm.access_level = normalizeAccessLevel(detail.access_level)
}

async function loadDuplicateTemplates() {
  const { data } = await http.get<ProjectListResponse>('/projects', {
    params: { skip: 0, limit: 500, search: '' },
  })
  duplicateTemplates.value = data.items.filter(isProjectAssignedToCurrentUser)
}

async function loadDuplicateTemplateDetail(projectId: string) {
  if (!projectId) {
    duplicateTemplateDetail.value = null
    return
  }
  loadingDuplicateDialog.value = true
  duplicateError.value = ''
  duplicateTemplateDetail.value = null
  try {
    const { data } = await http.get<ProjectDetailResponse>(`/projects/${projectId}`)
    duplicateTemplateDetail.value = data
    syncDuplicateFormFromTemplate(data)
  } catch (error) {
    duplicateError.value = getProjectListError(error, t('projectList.errors.duplicateTemplateLoad'))
  } finally {
    loadingDuplicateDialog.value = false
  }
}

async function openDuplicateDialog() {
  if (!canOpenDuplicateDialog.value) {
    return
  }
  const preferredTemplateId = selectedProjects.value.length === 1 ? selectedProjects.value[0].id : ''
  resetDuplicateDialog()
  showDuplicateDialog.value = true
  loadingDuplicateDialog.value = true
  try {
    await loadDuplicateTemplates()
    const preferredProject = duplicateTemplates.value.find((project) => project.id === preferredTemplateId)
    const templateProject = preferredProject || duplicateTemplates.value[0]
    if (templateProject) {
      duplicateTemplateProjectId.value = templateProject.id
      await loadDuplicateTemplateDetail(templateProject.id)
    }
  } catch (error) {
    duplicateError.value = getProjectListError(error, t('projectList.errors.duplicateTemplateLoad'))
  } finally {
    loadingDuplicateDialog.value = false
  }
}

function closeDuplicateDialog() {
  if (duplicatingProject.value) {
    return
  }
  showDuplicateDialog.value = false
  resetDuplicateDialog()
}

async function handleDuplicateTemplateChange(event: Event) {
  const projectId = (event.target as HTMLSelectElement).value
  duplicateTemplateProjectId.value = projectId
  await loadDuplicateTemplateDetail(projectId)
}

async function duplicateProjectFromTemplate() {
  if (!duplicateTemplateProjectId.value) {
    duplicateError.value = t('projectList.errors.duplicateTemplateRequired')
    return
  }
  if (!duplicateForm.name.trim()) {
    duplicateError.value = t('projectList.errors.requiredName')
    return
  }

  duplicatingProject.value = true
  duplicateError.value = ''
  try {
    const { data } = await http.post<ProjectDetailResponse>(`/projects/${duplicateTemplateProjectId.value}/duplicate`, {
      name: duplicateForm.name.trim(),
      deadline: duplicateForm.deadline || null,
      access_level: duplicateForm.access_level,
    })
    showDuplicateDialog.value = false
    resetDuplicateDialog()
    invalidateProjectFilterPool()
    toast.success(t('projectList.messages.duplicated', { name: data.name || data.filename }))
    await loadProjects()
    await router.push({ name: 'project-detail', params: { id: data.id } })
  } catch (error) {
    duplicateError.value = getProjectListError(error, t('projectList.errors.duplicate'))
  } finally {
    duplicatingProject.value = false
  }
}

async function deleteProjectIds(ids: string[], successMessage: string) {
  pageError.value = ''
  try {
    await Promise.all(ids.map((id) => http.delete(`/projects/${id}`)))
    invalidateProjectFilterPool()
    await loadProjects()
    toast.success(successMessage)
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || t('projectList.errors.delete'))
      return
    }
    pageError.value = error instanceof Error ? error.message : t('projectList.errors.delete')
  }
}

async function deleteRow(row: ProjectItem) {
  const confirmed = await confirm({
    title: t('projectList.actions.delete'),
    message: t('projectList.messages.deleteOneConfirm', { name: row.filename }),
    confirmText: t('common.actions.delete'),
    danger: true,
  })

  if (!confirmed) {
    return
  }

  await deleteProjectIds([row.id], t('projectList.messages.deletedOne', { name: row.filename }))
}

function formatDate(value: string | null) {
  if (!value) {
    return { date: '--', time: '' }
  }
  const date = new Date(value)
  return {
    date: date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }),
    time: date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }),
  }
}

function getAccessLabel(level: string | null) {
  const labels: Record<string, string> = {
    team: t('projectList.form.team'),
    private: t('projectList.form.private'),
    public: t('projectList.form.public'),
  }
  return labels[level || 'team'] || t('projectList.form.team')
}

function getProjectMetaText(row: ProjectItem) {
  const created = formatDate(row.created_at)
  const deadline = formatDate(row.deadline)
  return [
    getAccessLabel(row.access_level),
    row.creator || '--',
    `${t('projectList.summaries.createdAt')} ${created.date}`,
    `${t('projectList.form.deadline')} ${deadline.date}`,
  ].join(' · ')
}

function getStatusClass(status: string) {
  const meta = getFileStatusMeta(status)
  return `project-status--${meta.tone}`
}

function getProjectLinkClass(status: string) {
  const meta = getFileStatusMeta(status)
  return `project-link--${meta.tone}`
}

function handleSort(key: string, order: 'asc' | 'desc') {
  sortKey.value = key
  sortOrder.value = order
  if (hasActiveProjectFilters.value) {
    void loadProjects()
  }
}

function goToAssets() {
  void router.push({ name: 'tm' })
}

function openProjectDetail(row: ProjectItem) {
  void router.push({ name: 'project-detail', params: { id: row.id } })
}

function openProjectAssignment(row: ProjectItem) {
  void router.push({ name: 'project-detail', params: { id: row.id }, query: { assign: '1' } })
}

function openIssueDialog(row: ProjectItem) {
  issueTarget.value = row
  showIssueDialog.value = true
}

async function handleIssueSaved(_marker: IssueMarker) {
  showIssueDialog.value = false
  issueTarget.value = null
  toast.success(t('issueMarker.messages.saved'))
  await loadProjects()
}

let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(searchQuery, () => {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
  searchTimer = setTimeout(() => {
    invalidateProjectFilterPool()
    reloadProjectsFromFirstPage()
  }, 300)
})

watch([currentPage, pageSize], () => {
  void loadProjects()
})

onMounted(() => {
  document.addEventListener('click', handleProjectFilterDocumentClick)
  void loadProjects()
  void loadWorkflowTemplates()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleProjectFilterDocumentClick)
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
})
</script>

<template>
  <div class="table-page">
    <div class="table-toolbar table-toolbar--page">
      <div class="table-toolbar__left">
        <div class="table-page__search">
          <Search :size="14" class="table-page__search-icon" />
          <input
            v-model="searchQuery"
            class="table-page__search-input"
            type="text"
            :placeholder="t('projectList.searchPlaceholder')"
          />
        </div>
        <span class="table-toolbar__summary">{{ t('projectList.total', { total: totalCount }) }}</span>
        <span
          v-if="canManageProjects && selectedProjectIds.size > 0"
          class="table-toolbar__selected"
          :class="{ 'is-warning': selectedProjectIds.size > 1 }"
        >
          {{ t('projectList.selectedSummary', { count: selectedProjectIds.size }) }}
        </span>
      </div>
      <div class="table-toolbar__right">
        <button
          v-if="canCreateProjects"
          class="button button--primary"
          data-testid="project-create-button"
          type="button"
          @click="openCreateDialog"
        >
          <Plus :size="14" />
          {{ t('projectList.create') }}
        </button>
        <button
          v-if="canManageProjects"
          class="button"
          type="button"
          :title="t('projectList.importAssetsTitle')"
          @click="goToAssets"
        >
          <Database :size="14" />
          {{ t('projectList.importAssets') }}
        </button>
        <button
          v-if="canCreateProjects"
          class="button"
          type="button"
          :title="duplicateButtonTitle"
          :disabled="!canOpenDuplicateDialog"
          @click="openDuplicateDialog"
        >
          <Copy :size="14" />
          {{ t('projectList.fromTemplate') }}
        </button>
        <div class="project-filter-popover-wrap" @keydown.esc.stop="closeProjectFilterPanel">
          <button
            class="button project-filter-button"
            :class="{ 'is-active': showFilterDialog || hasActiveProjectFilters }"
            data-testid="project-filter-button"
            type="button"
            :title="hasActiveProjectFilters ? '已启用项目筛选' : '筛选项目'"
            :aria-expanded="showFilterDialog"
            aria-controls="project-filter-popover"
            @click.stop="openFilterDialog"
          >
            <Filter :size="14" />
            {{ t('projectList.filter') }}
            <span v-if="activeProjectFilterCount > 0" class="project-filter-button__count">
              {{ activeProjectFilterCount }}
            </span>
          </button>

          <Transition name="project-filter-popover">
            <section
              v-if="showFilterDialog"
              id="project-filter-popover"
              class="project-filter-dialog project-filter-popover"
              data-testid="project-filter-dialog"
              role="dialog"
              aria-label="项目筛选"
              @click.stop
            >
              <div class="project-filter-dialog__header">
                <div class="project-filter-dialog__title">
                  <span>项目筛选</span>
                  <span v-if="activeProjectFilterCount > 0" class="project-filter-dialog__count">
                    {{ activeProjectFilterCount }}
                  </span>
                </div>
                <div class="project-filter-dialog__header-actions">
                  <button
                    class="project-filter-dialog__clear"
                    type="button"
                    :disabled="!hasActiveProjectFilters"
                    @click="clearProjectFilters"
                  >
                    清空
                  </button>
                  <button
                    class="project-filter-dialog__close"
                    type="button"
                    title="关闭"
                    aria-label="关闭项目筛选"
                    @click="closeProjectFilterPanel"
                  >
                    <X :size="14" />
                  </button>
                </div>
              </div>

              <div v-if="loadingFilterOptions" class="project-filter-dialog__loading">
                <Loader2 class="lucide-spin" :size="16" />
                <span>正在读取可筛选项目...</span>
              </div>

              <div class="project-filter-dialog__grid">
                <label class="project-filter-dialog__field">
                  <span>项目状态</span>
                  <select v-model="filterDraft.status" class="field__control">
                    <option v-for="option in statusFilterOptions" :key="option.value" :value="option.value">
                      {{ option.label }}
                    </option>
                  </select>
                </label>

                <label class="project-filter-dialog__field">
                  <span>访问权限</span>
                  <select v-model="filterDraft.access_level" class="field__control">
                    <option v-for="option in accessFilterOptions" :key="option.value" :value="option.value">
                      {{ option.label }}
                    </option>
                  </select>
                </label>

                <label class="project-filter-dialog__field">
                  <span>源语言</span>
                  <select v-model="filterDraft.source_language" class="field__control">
                    <option v-for="option in sourceLanguageFilterOptions" :key="option.value" :value="option.value">
                      {{ option.label }}
                    </option>
                  </select>
                </label>

                <label class="project-filter-dialog__field">
                  <span>目标语言</span>
                  <select v-model="filterDraft.target_language" class="field__control">
                    <option v-for="option in targetLanguageFilterOptions" :key="option.value" :value="option.value">
                      {{ option.label }}
                    </option>
                  </select>
                </label>

                <label class="project-filter-dialog__field">
                  <span>创建人</span>
                  <select v-model="filterDraft.creator" class="field__control">
                    <option v-for="option in creatorFilterOptions" :key="option.value" :value="option.value">
                      {{ option.label }}
                    </option>
                  </select>
                </label>

                <label class="project-filter-dialog__field">
                  <span>截止期限</span>
                  <select v-model="filterDraft.deadline_scope" class="field__control">
                    <option v-for="option in deadlineScopeOptions" :key="option.value" :value="option.value">
                      {{ option.label }}
                    </option>
                  </select>
                </label>

                <label class="project-filter-dialog__field">
                  <span>文件数量</span>
                  <select v-model="filterDraft.file_count_scope" class="field__control">
                    <option v-for="option in fileCountScopeOptions" :key="option.value" :value="option.value">
                      {{ option.label }}
                    </option>
                  </select>
                </label>
              </div>

              <section class="project-filter-dialog__section">
                <div class="project-filter-dialog__section-title">时间范围</div>
                <div class="project-filter-dialog__grid">
                  <label class="project-filter-dialog__field">
                    <span>创建时间从</span>
                    <input v-model="filterDraft.created_from" class="field__control" type="date" />
                  </label>
                  <label class="project-filter-dialog__field">
                    <span>创建时间至</span>
                    <input v-model="filterDraft.created_to" class="field__control" type="date" />
                  </label>
                  <label class="project-filter-dialog__field">
                    <span>截止时间从</span>
                    <input v-model="filterDraft.deadline_from" class="field__control" type="date" />
                  </label>
                  <label class="project-filter-dialog__field">
                    <span>截止时间至</span>
                    <input v-model="filterDraft.deadline_to" class="field__control" type="date" />
                  </label>
                </div>
              </section>

              <p class="project-filter-dialog__hint">
                只使用项目名称搜索结果内的基础信息。
              </p>

              <div class="project-filter-dialog__footer">
                <button class="button" type="button" @click="resetFilterDraft">
                  <X :size="14" />
                  重置
                </button>
                <button class="button button--primary" type="button" @click="applyProjectFilters">
                  <Filter :size="14" />
                  应用筛选
                </button>
              </div>
            </section>
          </Transition>
        </div>
        <button class="button" type="button" disabled :title="t('common.comingSoon')">
          <Settings2 :size="14" />
          {{ t('projectList.columns') }}
        </button>
      </div>
    </div>

    <div v-if="hasActiveProjectFilters" class="project-filter-summary" aria-live="polite">
      <span class="project-filter-summary__label">已筛选</span>
      <span v-for="tag in projectFilterTags" :key="tag" class="project-filter-summary__tag">
        {{ tag }}
      </span>
      <button class="project-filter-summary__clear" type="button" @click="clearProjectFilters">
        <X :size="13" />
        清空
      </button>
    </div>

    <p v-if="pageError" class="form-message is-error table-page__message">{{ pageError }}</p>

    <div class="table-page__body">
      <DataTable
        class="project-table"
        test-id="project-table"
        row-test-id-prefix="project-row"
        :columns="columns"
        :data="projects"
        :loading="loading"
        :selectable="canManageProjects"
        :selected-ids="selectedProjectIds"
        :sort-key="sortKey"
        :sort-order="sortOrder"
        :show-index="true"
        :index-offset="indexOffset"
        :empty-text="t('projectList.empty')"
        @sort="handleSort"
        @select="handleProjectSelection"
      >
        <template #filename="{ row }">
          <div class="project-main-cell">
            <button
              class="text-link project-link"
              :class="getProjectLinkClass(row.status)"
              type="button"
              :title="row.filename"
              @click="router.push({ name: 'project-detail', params: { id: row.id } })"
            >
              {{ row.filename }}
            </button>
            <span class="project-main-cell__meta" :title="getProjectMetaText(row as ProjectItem)">
              {{ getProjectMetaText(row as ProjectItem) }}
            </span>
          </div>
        </template>

        <template #status="{ row }">
          <span class="project-status" :class="getStatusClass(row.status)">
            {{ getFileStatusMeta(row.status).label }}
          </span>
        </template>

        <template #progress="{ row }">
          <WorkflowProgressSummary
            compact
            :progress="row.progress"
            :status="row.status"
            :workflow-progress="getWorkflowProgressItems(row as ProjectItem)"
            :label="t('common.progress.total')"
            :detail-title="t('common.progress.workflowDetail')"
          />
        </template>

        <template #file_count="{ row }">
          <span>{{ row.file_count }}</span>
        </template>

        <template #open_issue_count="{ row }">
          <button
            class="issue-badge"
            :class="{ 'is-active': Number(row.open_issue_count || 0) > 0 }"
            type="button"
            :title="t('issueMarker.actions.open')"
            @click="openIssueDialog(row as ProjectItem)"
          >
            <Flag :size="13" />
            {{ Number(row.open_issue_count || 0) > 0 ? row.open_issue_count : t('common.none') }}
          </button>
        </template>

        <template #actions="{ row }">
          <div class="project-row-actions">
            <RowActionMenu title="更多操作" menu-label="项目操作" :min-width="156">
              <template #default="{ close }">
                <button
                  type="button"
                  role="menuitem"
                  @click="openProjectDetail(row as ProjectItem); close()"
                >
                  <FolderOpen :size="14" />
                  查看详情
                </button>
                <button
                  v-if="canAssignProjects"
                  type="button"
                  role="menuitem"
                  @click="openProjectAssignment(row as ProjectItem); close()"
                >
                  <Users :size="14" />
                  分配任务
                </button>
                <button
                  v-if="canManageProjects"
                  type="button"
                  role="menuitem"
                  @click="openIssueDialog(row as ProjectItem); close()"
                >
                  <Flag :size="14" />
                  问题标记
                </button>
                <button
                  v-if="canManageProjects"
                  class="is-danger"
                  type="button"
                  role="menuitem"
                  @click="close(); deleteRow(row as ProjectItem)"
                >
                  <Trash2 :size="14" />
                  删除项目
                </button>
              </template>
            </RowActionMenu>
          </div>
        </template>
      </DataTable>

      <Pagination
        :total="totalCount"
        :page="currentPage"
        :page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        @update:page="currentPage = $event"
        @update:page-size="pageSize = $event"
      />
    </div>

    <Modal
      :open="showCreateDialog"
      :title="t('projectList.createDialogTitle')"
      :description="t('projectList.createDialogDescription')"
      width="min(560px, calc(100vw - 32px))"
      @close="showCreateDialog = false"
    >
      <div class="form-grid" data-testid="project-create-dialog">
        <label class="field field--full">
          <span class="field__label">{{ t('projectList.form.name') }} <span class="field__required">*</span></span>
          <input
            v-model="form.name"
            class="field__control"
            data-testid="project-create-name"
            type="text"
            :placeholder="t('projectList.form.namePlaceholder')"
            maxlength="200"
          />
        </label>

        <label class="field">
          <span class="field__label">{{ t('projectList.form.deadline') }}</span>
          <input
            v-model="form.deadline"
            class="field__control"
            type="datetime-local"
          />
        </label>

        <label class="field">
          <span class="field__label">{{ t('projectList.form.accessLevel') }}</span>
          <select v-model="form.access_level" class="field__control">
            <option v-for="option in accessOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>

        <div class="field field--full project-language-binding">
          <span class="field__label">{{ t('projectList.form.languagePairBinding') }}</span>
          <p class="project-language-binding__hint">
            {{ t('projectList.form.languagePairBindingHint') }}
          </p>
          <div class="project-language-binding__grid">
            <label class="field">
              <span class="field__label">{{ t('projectList.form.sourceLanguage') }}</span>
              <select
                v-model="form.source_language"
                class="field__control"
                data-testid="project-create-source-language"
              >
                <option value="">{{ t('projectList.form.noLanguagePairBinding') }}</option>
                <option
                  v-for="lang in languageOptions"
                  :key="lang.code"
                  :value="lang.code"
                  :disabled="lang.code === form.target_language"
                >
                  {{ lang.label }}
                </option>
              </select>
            </label>

            <label class="field">
              <span class="field__label">{{ t('projectList.form.targetLanguage') }}</span>
              <select
                v-model="form.target_language"
                class="field__control"
                data-testid="project-create-target-language"
              >
                <option value="">{{ t('projectList.form.noLanguagePairBinding') }}</option>
                <option
                  v-for="lang in languageOptions"
                  :key="lang.code"
                  :value="lang.code"
                  :disabled="lang.code === form.source_language"
                >
                  {{ lang.label }}
                </option>
              </select>
            </label>
          </div>
        </div>

        <label class="field field--full">
          <span class="field__label">工作流模板 <span class="field__required">*</span></span>
          <select
            v-model="form.workflow_template_id"
            class="field__control"
            data-testid="project-create-workflow-template"
            :disabled="loadingWorkflowTemplates"
            @change="handleWorkflowTemplateChange"
          >
            <option value="" disabled>{{ loadingWorkflowTemplates ? '模板加载中...' : '请选择工作流模板' }}</option>
            <option v-for="template in workflowTemplates" :key="template.id" :value="template.id">
              {{ template.name }}
            </option>
          </select>
        </label>

        <div v-if="form.workflow_steps.length > 0" class="workflow-editor field--full">
          <div
            v-for="(step, index) in form.workflow_steps"
            :key="`${step.step_key}-${index}`"
            class="workflow-editor__row"
          >
            <span class="workflow-editor__order">{{ index + 1 }}</span>
            <input
              v-model="step.name"
              class="field__control"
              type="text"
              :disabled="index === 0"
              maxlength="80"
            />
            <button
              v-if="index > 0"
              class="button button--ghost workflow-editor__remove"
              type="button"
              @click="removeWorkflowStep(index)"
            >
              删除
            </button>
          </div>
          <button class="button button--ghost workflow-editor__add" type="button" @click="addWorkflowStep">
            <Plus :size="14" />
            添加阶段
          </button>
        </div>
      </div>

      <p class="form-hint">
        {{ t('projectList.form.hint') }}
      </p>

      <p v-if="formError" class="form-message is-error">{{ formError }}</p>

      <template #footer>
        <button class="button" type="button" @click="showCreateDialog = false">{{ t('common.actions.cancel') }}</button>
        <button
          class="button button--primary"
          data-testid="project-create-submit"
          type="button"
          :disabled="!canSubmitCreate"
          @click="createProject"
        >
          <Loader2 v-if="creating" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ creating ? t('projectList.actions.creating') : t('projectList.actions.createSubmit') }}
        </button>
      </template>
    </Modal>

    <Modal
      :open="showDuplicateDialog"
      :title="t('projectList.duplicateDialogTitle')"
      :description="t('projectList.duplicateDialogDescription')"
      width="min(640px, calc(100vw - 32px))"
      @close="closeDuplicateDialog"
    >
      <div class="duplicate-dialog" data-testid="project-duplicate-dialog">
        <label class="field field--full">
          <span class="field__label">{{ t('projectList.duplicate.templateProject') }}</span>
          <select
            class="field__control"
            data-testid="project-duplicate-template"
            :value="duplicateTemplateProjectId"
            :disabled="loadingDuplicateDialog || duplicatingProject"
            @change="handleDuplicateTemplateChange"
          >
            <option value="" disabled>{{ t('projectList.duplicate.templatePlaceholder') }}</option>
            <option v-for="item in duplicateTemplates" :key="item.id" :value="item.id">
              {{ item.filename }}
            </option>
          </select>
        </label>

        <p v-if="duplicateTemplates.length === 0 && !loadingDuplicateDialog" class="form-hint">
          {{ t('projectList.duplicate.emptyTemplates') }}
        </p>

        <div v-if="loadingDuplicateDialog" class="duplicate-dialog__loading">
          <Loader2 class="lucide-spin" :size="16" />
          <span>{{ t('common.loading') }}</span>
        </div>

        <template v-if="duplicateTemplateDetail">
          <section class="duplicate-dialog__section">
            <div class="duplicate-dialog__section-title">
              <FileText :size="15" />
              <span>{{ t('projectList.duplicate.basicInfo') }}</span>
            </div>
            <div class="duplicate-dialog__summary">
              <span>{{ t('projectList.duplicate.sourceProject') }}：{{ selectedDuplicateTemplate?.filename || duplicateTemplateDetail.filename }}</span>
              <span>{{ t('projectList.duplicate.templateFileCount') }}：{{ duplicateTemplateFileCount }}</span>
              <span>{{ t('projectList.duplicate.languagePair') }}：{{ formatLanguagePair(duplicateTemplateDetail.source_language, duplicateTemplateDetail.target_language) }}</span>
            </div>
            <p class="form-hint">{{ t('projectList.duplicate.basicOnlyHint') }}</p>
            <div class="form-grid duplicate-dialog__form">
              <label class="field field--full">
                <span class="field__label">{{ t('projectList.form.name') }} <span class="field__required">*</span></span>
                <input
                  v-model="duplicateForm.name"
                  class="field__control"
                  data-testid="project-duplicate-name"
                  type="text"
                  maxlength="200"
                  :disabled="duplicatingProject"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('projectList.form.deadline') }}</span>
                <input
                  v-model="duplicateForm.deadline"
                  class="field__control"
                  type="datetime-local"
                  :disabled="duplicatingProject"
                />
              </label>
              <label class="field">
                <span class="field__label">{{ t('projectList.form.accessLevel') }}</span>
                <select v-model="duplicateForm.access_level" class="field__control" :disabled="duplicatingProject">
                  <option v-for="option in accessOptions" :key="option.value" :value="option.value">
                    {{ option.label }}
                  </option>
                </select>
              </label>
            </div>
          </section>
        </template>

        <p v-if="duplicateError" class="form-message is-error">{{ duplicateError }}</p>
      </div>

      <template #footer>
        <button class="button" type="button" :disabled="duplicatingProject" @click="closeDuplicateDialog">
          {{ t('common.actions.cancel') }}
        </button>
        <button
          class="button button--primary"
          data-testid="project-duplicate-submit"
          type="button"
          :disabled="duplicatingProject || loadingDuplicateDialog || !duplicateTemplateProjectId || !duplicateForm.name.trim()"
          @click="duplicateProjectFromTemplate"
        >
          <Loader2 v-if="duplicatingProject" class="lucide-spin" :size="14" />
          <Copy v-else :size="14" />
          {{ duplicatingProject ? t('projectList.actions.duplicating') : t('projectList.actions.duplicateSubmit') }}
        </button>
      </template>
    </Modal>

    <IssueMarkerDialog
      :open="showIssueDialog"
      :project-id="issueTarget?.id ?? null"
      :context-label="issueTarget?.filename ?? ''"
      @close="showIssueDialog = false"
      @saved="handleIssueSaved"
    />
  </div>
</template>

<style scoped>
.table-toolbar--page {
  padding: 18px 20px 10px;
}

.table-toolbar__summary {
  color: var(--text-muted);
  font-size: 13px;
}

.table-toolbar__selected {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--state-info-bg);
  color: var(--state-info);
  font-size: 12px;
  white-space: nowrap;
}

.table-toolbar__selected.is-warning {
  background: var(--state-warning-bg);
  color: var(--state-warning);
}

.table-page__message {
  margin: 0 20px 8px;
}

.project-filter-popover-wrap {
  position: relative;
  display: inline-flex;
}

.project-filter-button.is-active {
  border-color: color-mix(in srgb, var(--state-info) 45%, var(--line-soft));
  background: var(--state-info-bg);
  color: var(--state-info);
}

.project-filter-button__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--state-info);
  color: var(--surface-panel);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.project-filter-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0 20px 10px;
  color: var(--text-secondary);
  font-size: 12px;
}

.project-filter-summary__label {
  color: var(--text-muted);
}

.project-filter-summary__tag {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 3px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 999px;
  background: var(--surface-muted);
  color: var(--text-secondary);
  white-space: nowrap;
}

.project-filter-summary__clear {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 24px;
  padding: 3px 8px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: var(--state-danger);
  font-size: 12px;
  box-shadow: none;
}

.project-filter-summary__clear:hover {
  border-color: color-mix(in srgb, var(--state-danger) 30%, transparent);
  background: var(--state-danger-bg);
}

.project-filter-popover {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 1800;
  width: min(560px, calc(100vw - 32px));
  max-height: min(680px, calc(100vh - 152px));
  padding: 12px 14px 14px;
  overflow-y: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: 0 18px 42px rgba(20, 45, 55, 0.18);
}

.project-filter-popover-enter-active,
.project-filter-popover-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.project-filter-popover-enter-from,
.project-filter-popover-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.project-filter-dialog {
  display: grid;
  gap: 12px;
  color: var(--text-primary);
}

.project-filter-dialog__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 32px;
}

.project-filter-dialog__title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 14px;
  font-weight: 700;
}

.project-filter-dialog__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--state-info-bg);
  color: var(--state-info);
  font-size: 11px;
  font-weight: 700;
}

.project-filter-dialog__header-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}

.project-filter-dialog__clear {
  border: 0;
  background: transparent;
  color: var(--state-info);
  font-size: 13px;
  cursor: pointer;
}

.project-filter-dialog__clear:disabled {
  color: var(--text-muted);
  cursor: not-allowed;
}

.project-filter-dialog__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 1px solid var(--line-soft);
  border-radius: 4px;
  background: var(--surface-panel);
  color: var(--text-secondary);
  cursor: pointer;
}

.project-filter-dialog__close:hover {
  background: var(--surface-muted);
  color: var(--text-primary);
}

.project-filter-dialog__loading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: 13px;
}

.project-filter-dialog__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 10px;
}

.project-filter-dialog__field {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.project-filter-dialog__field > span {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.project-filter-dialog__field .field__control {
  min-width: 0;
  height: 31px;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 13px;
  box-shadow: none;
}

.project-filter-dialog__section {
  display: grid;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
}

.project-filter-dialog__section-title {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 700;
}

.project-filter-dialog__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
}

.project-filter-dialog__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
}

.project-filter-dialog__footer .button {
  min-height: 30px;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 13px;
  box-shadow: none;
}

.project-table :deep(.data-table) {
  table-layout: fixed;
}

.project-link {
  display: block;
  max-width: 100%;
  overflow: hidden;
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-main-cell {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.project-main-cell__meta {
  display: block;
  overflow: hidden;
  color: var(--text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-link--info {
  color: var(--state-info);
}

.project-link--success {
  color: var(--state-success);
}

.project-link--warning {
  color: var(--state-warning);
}

.project-link--danger {
  color: var(--state-danger);
}

.project-link--default {
  color: var(--text-secondary);
}

.project-row-actions {
  display: flex;
  gap: 4px;
  justify-content: center;
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

.form-hint {
  margin: 12px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}

.project-language-binding {
  display: grid;
  gap: 8px;
}

.project-language-binding__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.5;
}

.project-language-binding__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.workflow-editor {
  display: grid;
  gap: 8px;
}

.workflow-editor__row {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
}

.workflow-editor__order {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 999px;
  background: var(--surface-muted);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 700;
}

.workflow-editor__remove {
  padding-inline: 10px;
}

.workflow-editor__add {
  justify-self: start;
}

.duplicate-dialog {
  display: grid;
  gap: 16px;
}

.duplicate-dialog__loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: 13px;
}

.duplicate-dialog__section {
  display: grid;
  gap: 12px;
  padding-top: 4px;
}

.duplicate-dialog__section-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 700;
}

.duplicate-dialog__summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 13px;
}

.duplicate-dialog__summary span {
  padding: 4px 8px;
  border-radius: 6px;
  background: var(--surface-muted);
}

.duplicate-dialog__form {
  margin-top: 2px;
}

.duplicate-dialog__empty {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}

@media (max-width: 760px) {
  .project-filter-popover {
    position: fixed;
    top: 88px;
    right: 12px;
    left: 12px;
    width: auto;
    max-height: calc(100vh - 112px);
  }

  .project-filter-dialog__grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .project-language-binding__grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .project-filter-dialog__footer {
    justify-content: stretch;
  }

  .project-filter-dialog__footer .button {
    flex: 1 1 0;
  }
}

</style>
