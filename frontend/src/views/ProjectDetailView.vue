<script setup lang="ts">
import axios from 'axios'
import {
  ArrowLeft,
  BookOpen,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  Download,
  FileText,
  Filter,
  Flag,
  FolderOpen,
  Link,
  Loader2,
  MoreHorizontal,
  Settings2,
  Sparkles,
  Trash2,
  Upload,
  Users,
  RotateCcw,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import {
  isImportTaskAccepted,
  waitForImportTask,
  type ImportTaskAccepted,
} from '../api/importTasks'
import { http } from '../api/http'
import DataTable from '../components/DataTable.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import IssueMarkerDialog from '../components/IssueMarkerDialog.vue'
import PreTranslateDialog from '../components/PreTranslateDialog.vue'
import Pagination from '../components/Pagination.vue'
import { useConfirm } from '../composables/useConfirm'
import { usePageHeader } from '../composables/usePageHeader'
import { useToast } from '../composables/useToast'
import { getLanguageLabel, languageOptions } from '../constants/languages'
import { getFileStatusMeta } from '../constants/status'
import { buildTranslatedTaskFilename, supportedTaskFileAccept } from '../constants/taskFiles'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'
import { getProgressStyle } from '../utils/progress'
import type { IssueMarker, IssueStatus } from '../types/api'

const props = defineProps<{
  id: string
}>()

type ProjectTab = 'files' | 'issues' | 'settings' | 'stats' | 'summary' | 'quote'
type DocumentParseMode = 'full' | 'body_only'

interface ProjectDetail {
  id: string
  name: string
  filename: string
  status: string
  progress: number
  file_count: number
  total_segments: number
  translated_segments: number
  source_language: string | null
  target_language: string | null
  creator: string | null
  deadline: string | null
  access_level: string | null
  translation_guidelines: string
  term_base_id: string | null
  created_at: string
  updated_at: string
  has_source_document: boolean
  file_size_bytes: number | null
  issue_count: number
  open_issue_count: number
  issue_markers: IssueMarker[]
  files: ProjectFileItem[]
}

interface ProjectFileItem {
  id: string
  project_id: string | null
  filename: string
  status: string
  document_parse_mode: DocumentParseMode
  progress: number
  total_segments: number
  translated_segments: number
  source_language: string | null
  target_language: string | null
  creator: string | null
  deadline: string | null
  access_level: string | null
  created_at: string
  updated_at: string
  has_source_document: boolean
  file_size_bytes: number | null
  issue_count: number
  open_issue_count: number
  collection_id: string | null
  term_base_id: string | null
}

type LanguageDetectTone = 'info' | 'success' | 'warning' | 'error'

interface LanguageDetectResponse {
  language: string | null
  label: string | null
  confidence: number
  supported: boolean
  sample_length: number
  message: string
}

type ProjectRow = ProjectFileItem | Record<string, any>

const confirm = useConfirm()
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { t } = useI18n()

const loading = ref(false)
const deleting = ref(false)
const uploading = ref(false)
const uploadPercent = ref(0)
const project = ref<ProjectDetail | null>(null)
const pageError = ref('')
const uploadMessage = ref('')
const detectingLanguage = ref(false)
const languageDetectMessage = ref('')
const languageDetectTone = ref<LanguageDetectTone>('info')
const selectedFiles = ref<File[]>([])
const uploadSourceLanguage = ref('')
const uploadTargetLanguage = ref('')
const basicCollapsed = ref(false)
const activeTab = ref<ProjectTab>('files')
const showUploadModal = ref(false)
const showPreTranslateDialog = ref(false)
const showIssueDialog = ref(false)
const uploadInputKey = ref(0)
const openActionMenuId = ref<string | null>(null)
const actionMenuStyle = ref<Record<string, string>>({})
const currentPage = ref(1)
const pageSize = ref(10)
const selectedFileIds = ref(new Set<string>())
const guidelinesText = ref('')
const savingGuidelines = ref(false)
const issueDialogTarget = ref<{
  fileRecordId: string | null
  label: string
} | null>(null)
const updatingIssueId = ref<string | null>(null)

const tabs = computed(() => ([
  { key: 'files' as const, label: t('projectDetail.tabs.files'), disabled: false },
  {
    key: 'issues' as const,
    label: `${t('projectDetail.tabs.issues')}${openIssueCount.value > 0 ? ` (${openIssueCount.value})` : ''}`,
    disabled: false,
  },
  { key: 'settings' as const, label: t('projectDetail.tabs.settings'), disabled: false },
  { key: 'stats' as const, label: t('projectDetail.tabs.stats'), disabled: true },
  { key: 'summary' as const, label: t('projectDetail.tabs.summary'), disabled: true },
  { key: 'quote' as const, label: t('projectDetail.tabs.quote'), disabled: true },
]))

const tableRows = computed<ProjectFileItem[]>(() => project.value?.files ?? [])
const issueMarkers = computed<IssueMarker[]>(() => project.value?.issue_markers ?? [])
const openIssueCount = computed(() => issueMarkers.value.filter((marker) => marker.status === 'open').length)
const actionMenuRow = computed<ProjectFileItem | null>(() => {
  const id = openActionMenuId.value
  if (!id) return null
  return tableRows.value.find((r) => r.id === id) ?? null
})
const pagedRows = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return tableRows.value.slice(start, start + pageSize.value)
})
const indexOffset = computed(() => (currentPage.value - 1) * pageSize.value)
const selectedProjectFiles = computed(() => (
  tableRows.value.filter((row) => selectedFileIds.value.has(row.id))
))
const preTranslateButtonTitle = computed(() => (
  selectedFileIds.value.size === 0 ? t('projectDetail.preTranslate.selectFileFirst') : ''
))

const columns = computed<DataTableColumn[]>(() => ([
  { key: 'filename', label: t('projectDetail.files.columns.details'), width: '280px' },
  { key: 'progress', label: t('projectDetail.files.columns.progress'), width: '180px' },
  { key: 'taskManage', label: t('projectDetail.files.columns.task'), width: '150px' },
  { key: 'status', label: t('projectDetail.files.columns.status'), width: '120px' },
  { key: 'open_issue_count', label: t('issueMarker.list.title'), width: '120px' },
  { key: 'created_at', label: t('projectDetail.files.columns.createdAt'), width: '170px' },
  { key: 'source_language', label: t('projectList.form.sourceLanguage'), width: '130px' },
  { key: 'target_language', label: t('projectDetail.files.columns.targetLang'), width: '130px' },
  { key: 'file_size_bytes', label: t('projectDetail.files.columns.size'), width: '120px', align: 'right' },
]))

const canOpenUploadModal = computed(() => Boolean(project.value))
const canOpenIssueDialog = computed(() => Boolean(project.value))

const uploadButtonTitle = computed(() => '')

const uploadFilePreview = computed(() => selectedFiles.value.slice(0, 3).map((file) => file.name))
const canDetectSourceLanguage = computed(() => (
  selectedFiles.value.length > 0 && !uploading.value && !detectingLanguage.value
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

function canEnterWorkbench(row: ProjectRow) {
  return Number(row.total_segments ?? 0) > 0
}

function getFileDetailHint(row: ProjectRow) {
  if (canEnterWorkbench(row)) {
    return t('projectDetail.files.openHint')
  }
  if (row.has_source_document) {
    return t('projectDetail.files.processingHint')
  }
  return t('projectDetail.common.uploadRequired')
}

function clearLanguageDetectState() {
  languageDetectMessage.value = ''
  languageDetectTone.value = 'info'
}

function updateSelectedFiles(files: File[]) {
  selectedFiles.value = files
  clearLanguageDetectState()
}

function onFileChange(event: Event) {
  updateSelectedFiles(Array.from((event.target as HTMLInputElement).files ?? []))
}

function onFileDrop(event: DragEvent) {
  updateSelectedFiles(Array.from(event.dataTransfer?.files ?? []))
}

function resetUploadForm() {
  selectedFiles.value = []
  uploadMessage.value = ''
  clearLanguageDetectState()
  uploadPercent.value = 0
  uploadInputKey.value += 1
}

function openUploadDialog() {
  if (!canOpenUploadModal.value) {
    return
  }

  resetUploadForm()
  uploadSourceLanguage.value = project.value?.source_language || ''
  uploadTargetLanguage.value = project.value?.target_language || ''
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
  if (selectedFileIds.value.size === 0) {
    return
  }
  showPreTranslateDialog.value = true
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
}

function openProjectIssueDialog() {
  if (!project.value) {
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
  if (isEventFromFloatingActionMenu(ev)) {
    return
  }
  closeActionMenu()
}

function handleDocumentScroll() {
  if (openActionMenuId.value) {
    closeActionMenu()
  }
}

function goBack() {
  void router.push(backRoute.value)
}

function openWorkbench(row: ProjectRow) {
  if (!canEnterWorkbench(row)) {
    return
  }

  closeActionMenu()
  const rowId = String(row.id)
  void router.push({
    name: 'workbench',
    params: { id: rowId },
    query: {
      from: 'project',
      pid: props.id,
      ...(cameFromTasks.value ? { parent: 'tasks' } : {}),
    },
  })
}

async function loadProject() {
  loading.value = true
  pageError.value = ''

  try {
    const { data } = await http.get<ProjectDetail>(`/projects/${props.id}`)
    project.value = data
    guidelinesText.value = data.translation_guidelines || ''
    currentPage.value = 1
    selectedFileIds.value = new Set<string>()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.load'))
  } finally {
    loading.value = false
  }
}

async function saveGuidelines() {
  if (!project.value || savingGuidelines.value) {
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
      const matchesTargetLanguage = uploadTargetLanguage.value === data.language
      uploadSourceLanguage.value = data.language
      if (matchesTargetLanguage) {
        uploadTargetLanguage.value = ''
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
  if (selectedFiles.value.length === 0) {
    uploadMessage.value = t('projectDetail.errors.selectFile')
    return
  }

  if (!uploadSourceLanguage.value || !uploadTargetLanguage.value) {
    uploadMessage.value = t('projectDetail.errors.selectLanguagePair')
    return
  }

  if (uploadSourceLanguage.value === uploadTargetLanguage.value) {
    uploadMessage.value = t('projectList.errors.sameLanguage')
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
    formData.append('source_language', uploadSourceLanguage.value)
    formData.append('target_language', uploadTargetLanguage.value)
    formData.append('document_parse_mode', 'full')

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

async function exportProjectFile(row: ProjectRow) {
  if (!row.has_source_document) {
    return
  }

  closeActionMenu()
  pageError.value = ''
  const rowId = String(row.id)
  const filename = String(row.filename || 'translated.txt')

  try {
    const response = await http.get(`/file-records/${rowId}/export`, {
      responseType: 'blob',
    })
    const downloadName = resolveDownloadFilename(
      response.headers['content-disposition'],
      buildTranslatedTaskFilename(filename),
    )
    downloadBlob(response.data, downloadName)
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.export'))
  }
}

async function deleteCurrentProject() {
  if (!project.value) {
    return
  }

  const filename = project.value.filename || t('projectDetail.titleFallback')
  const confirmed = await confirm({
    title: t('projectDetail.files.actions.delete'),
    message: t('projectDetail.messages.deleteConfirm', { name: filename }),
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
    toast.success(t('projectDetail.messages.deleted', { name: filename }))
    await router.push({ name: 'projects' })
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.delete'))
  } finally {
    deleting.value = false
  }
}

async function deleteProjectFile(row: ProjectRow) {
  closeActionMenu()

  const rowId = String(row.id)
  const filename = String(row.filename || t('projectDetail.titleFallback'))

  const confirmed = await confirm({
    title: t('projectDetail.files.actions.delete'),
    message: t('projectDetail.messages.deleteConfirm', { name: filename }),
    confirmText: t('common.actions.delete'),
    danger: true,
  })

  if (!confirmed) {
    return
  }

  deleting.value = true
  pageError.value = ''

  try {
    await http.delete(`/file-records/${rowId}`)
    toast.success(t('projectDetail.messages.deleted', { name: filename }))
    selectedFileIds.value.delete(rowId)
    selectedFileIds.value = new Set(selectedFileIds.value)
    await loadProject()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.delete'))
  } finally {
    deleting.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
  window.addEventListener('scroll', handleDocumentScroll, { passive: true })
  window.addEventListener('resize', handleDocumentScroll)
  void loadProject()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
  window.removeEventListener('scroll', handleDocumentScroll)
  window.removeEventListener('resize', handleDocumentScroll)
})

</script>

<template>
  <div v-if="showUploadModal" class="upload-page">
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
              type="file"
              multiple
              :accept="supportedTaskFileAccept"
              aria-label="上传文件"
              @change="onFileChange"
            />
            <Upload :size="16" />
            上传文件
          </label>
          <p>或拖放文件进行翻译</p>
        </div>

        <p class="upload-supported">
          支持的文件类型：
          <span>doc/docx</span>、<span>xls/xlsx</span>、<span>ppt/pptx</span>、<span>sdlxliff</span>、<span>dwg</span>、<span>dxf</span>、<span>pdf</span>
          <button type="button">更多</button>
        </p>

        <section class="upload-language-panel">
          <div class="upload-language-panel__head">
            <div>
              <div class="section-title section-title--tight">语言设置</div>
              <p class="panel-subtitle">可先识别第一个文件的源语言，再手动调整源语言和目标语言。</p>
            </div>
            <button
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
              <select v-model="uploadSourceLanguage" class="field__control">
                <option value="" disabled>{{ t('projectList.form.sourcePlaceholder') }}</option>
                <option
                  v-for="lang in languageOptions"
                  :key="lang.code"
                  :value="lang.code"
                  :disabled="lang.code === uploadTargetLanguage"
                >
                  {{ lang.label }}
                </option>
              </select>
            </label>

            <label class="field">
              <span class="field__label">{{ t('projectList.form.targetLanguage') }} <span class="field__required">*</span></span>
              <select v-model="uploadTargetLanguage" class="field__control">
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
          </div>

          <p
            v-if="languageDetectMessage"
            class="upload-detect-message"
            :class="`upload-detect-message--${languageDetectTone}`"
          >
            {{ languageDetectMessage }}
          </p>

          <div v-if="selectedFiles.length" class="upload-file-list">
            <div v-for="fileName in uploadFilePreview" :key="fileName" class="upload-file-list__item">
              <FileText :size="15" />
              <span>{{ fileName }}</span>
            </div>
            <div v-if="selectedFiles.length > uploadFilePreview.length" class="upload-file-list__item">
              <FileText :size="15" />
              <span>还有 {{ selectedFiles.length - uploadFilePreview.length }} 个文件</span>
            </div>
          </div>

          <div v-if="uploading" class="upload-page__progress">
            <div class="progress-bar">
              <div class="progress-bar__track">
                <div class="progress-bar__fill" :style="{ width: `${uploadPercent}%` }" />
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
              type="button"
              :disabled="uploading || selectedFiles.length === 0 || !uploadSourceLanguage || !uploadTargetLanguage"
              @click="uploadSourceDocument"
            >
              <Loader2 v-if="uploading" class="lucide-spin" :size="14" />
              <Upload v-else :size="14" />
              {{ uploading ? t('projectDetail.messages.uploading', { percent: uploadPercent }) : t('projectDetail.messages.startUpload') }}
            </button>
          </div>
        </section>
      </section>

      <aside class="doc-settings">
        <h2>文档设置</h2>

        <div class="doc-settings__grid">
          <section class="doc-setting-card doc-setting-card--word">
            <div class="doc-type-icon">W</div>
            <label><input type="checkbox" checked /> 全选</label>
            <label><input type="checkbox" checked /> 翻译页眉页脚</label>
            <label><input type="checkbox" checked /> 翻译批注</label>
            <label class="is-muted"><input type="checkbox" /> 翻译隐藏内容</label>
            <label class="is-muted"><input type="checkbox" /> 翻译文档属性</label>
            <label class="is-muted"><input type="checkbox" /> 清洗格式</label>
          </section>

          <section class="doc-setting-card doc-setting-card--ppt">
            <div class="doc-type-icon">P</div>
            <label><input type="checkbox" checked /> 全选</label>
            <label><input type="checkbox" checked /> 翻译批注</label>
            <label><input type="checkbox" checked /> 翻译备注</label>
            <label class="is-muted"><input type="checkbox" /> 翻译文档属性</label>
          </section>

          <section class="doc-setting-card doc-setting-card--excel">
            <div class="doc-type-icon">X</div>
            <label><input type="checkbox" checked /> 全选</label>
            <label><input type="checkbox" checked /> 翻译批注</label>
            <label><input type="checkbox" checked /> 翻译图形文本</label>
            <label class="is-muted"><input type="checkbox" /> 翻译工作表名</label>
            <label class="is-muted"><input type="checkbox" /> 翻译隐藏内容</label>
            <label class="is-muted"><input type="checkbox" /> 翻译文档属性</label>
            <label class="is-muted"><input type="checkbox" /> 跳过应用所选背景色的单元格</label>
            <div class="doc-color-swatches" aria-hidden="true">
              <span v-for="color in ['#d7191c', '#f44336', '#ffc107', '#ffeb3b', '#8bc34a', '#4caf50', '#00bcd4', '#2196f3', '#0d47a1', '#7b1fa2']" :key="color" :style="{ background: color }" />
            </div>
          </section>

          <section class="doc-setting-card doc-setting-card--mini">
            <div class="doc-file-icon">MD</div>
            <label><input type="checkbox" checked /> 全选</label>
            <label><input type="checkbox" checked /> 翻译代码块</label>
            <label class="is-muted"><input type="checkbox" /> 提取链接</label>
          </section>

          <section class="doc-setting-card doc-setting-card--mini">
            <div class="doc-file-icon doc-file-icon--purple">DAT</div>
            <label><input type="checkbox" checked /> 翻译代码块</label>
          </section>
        </div>
      </aside>
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
          <div class="progress-bar pd-hero__progress-bar">
            <div class="progress-bar__track">
              <div class="progress-bar__fill" :style="getProgressStyle(project?.progress ?? 0)" />
            </div>
            <span class="progress-bar__text">{{ project?.progress ?? 0 }}%</span>
          </div>
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
        @click="activeTab = tab.key"
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
      <section class="panel">
        <div class="pd-panel-head">
          <div class="pd-panel-head__copy">
            <div class="section-title section-title--tight">{{ t('projectDetail.base.title') }}</div>
            <p class="panel-subtitle">{{ t('projectDetail.base.description') }}</p>
          </div>
          <button class="button pd-panel-toggle" type="button" @click="basicCollapsed = !basicCollapsed">
            {{ basicCollapsed ? t('projectDetail.base.expand') : t('projectDetail.base.collapse') }}
            <ChevronDown v-if="basicCollapsed" :size="16" />
            <ChevronUp v-else :size="16" />
          </button>
        </div>

        <div v-if="!basicCollapsed" class="pd-basic-grid">
          <label class="pd-field">
            <span class="pd-field__label">{{ t('projectDetail.base.status') }}</span>
            <span class="pd-field__value">
              <span class="project-status" :class="getStatusClass(project.status)">
                {{ formatStatus(project.status) }}
              </span>
            </span>
          </label>
          <label class="pd-field">
            <span class="pd-field__label">{{ t('projectDetail.base.workflow') }}</span>
            <span class="pd-field__value">{{ t('projectDetail.base.workflowValue') }}</span>
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
      </section>

      <section v-if="activeTab === 'settings'" class="panel">
        <div class="pd-panel-head">
          <div class="pd-panel-head__copy">
            <div class="section-title section-title--tight">{{ t('projectDetail.settings.guidelinesTitle') }}</div>
            <p class="panel-subtitle">{{ t('projectDetail.settings.guidelinesDescription') }}</p>
          </div>
        </div>

        <div class="pd-settings-form">
          <label class="field field--full">
            <span class="field__label">{{ t('projectDetail.settings.guidelinesLabel') }}</span>
            <textarea
              v-model="guidelinesText"
              class="field__control pd-guidelines-editor"
              rows="10"
              :placeholder="t('projectDetail.settings.guidelinesPlaceholder')"
            />
          </label>
          <p class="hint-text">{{ t('projectDetail.settings.guidelinesHint') }}</p>
          <div class="pd-settings-actions">
            <button
              class="button button--primary"
              type="button"
              :disabled="savingGuidelines"
              @click="saveGuidelines"
            >
              <Loader2 v-if="savingGuidelines" class="lucide-spin" :size="14" />
              <Settings2 v-else :size="14" />
              {{ savingGuidelines ? t('common.actions.saving') : t('common.actions.save') }}
            </button>
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
            :disabled="!canOpenIssueDialog"
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

      <section v-if="activeTab === 'files'" class="panel">
        <div class="pd-panel-head">
          <div class="pd-panel-head__copy">
            <div class="section-title section-title--tight">{{ t('projectDetail.files.title') }}</div>
            <p class="panel-subtitle">{{ t('projectDetail.files.description') }}</p>
          </div>
        </div>

        <div class="table-toolbar pd-toolbar">
          <div class="table-toolbar__left pd-toolbar__left">
            <button
              class="button"
              type="button"
              :disabled="selectedFileIds.size === 0"
              :title="preTranslateButtonTitle || undefined"
              @click="openPreTranslateDialog"
            >
              <Sparkles :size="14" />
              {{ t('projectDetail.preTranslate.button') }}
            </button>
            <button
              class="button button--primary"
              type="button"
              :disabled="!canOpenUploadModal"
              :title="uploadButtonTitle || undefined"
              @click="openUploadDialog"
            >
              <Upload :size="14" />
              {{ t('projectDetail.files.actions.upload') }}
            </button>
            <button
              class="button"
              type="button"
              :disabled="!canOpenIssueDialog"
              @click="openProjectIssueDialog"
            >
              <Flag :size="14" />
              {{ t('issueMarker.actions.open') }}
            </button>
            <button class="button" type="button" disabled :title="t('projectDetail.common.comingSoon')">
              <Link :size="14" />
              {{ t('projectDetail.files.actions.link') }}
            </button>
            <button class="button" type="button" disabled :title="t('projectDetail.common.comingSoon')">
              <BookOpen :size="14" />
              {{ t('projectDetail.files.actions.glossary') }}
            </button>
            <button class="button" type="button" disabled :title="t('projectDetail.common.comingSoon')">
              <Users :size="14" />
              {{ t('projectDetail.files.actions.assign') }}
            </button>
            <button
              class="button"
              type="button"
              disabled
              :title="t('projectDetail.common.comingSoon')"
            >
              <Download :size="14" />
              {{ t('projectDetail.files.actions.export') }}
            </button>
            <button class="button" type="button" disabled :title="t('projectDetail.common.comingSoon')">
              <Clock3 :size="14" />
              {{ t('projectDetail.files.actions.modifyTaskType') }}
            </button>
            <button class="button" type="button" disabled :title="t('projectDetail.common.comingSoon')">
              <FolderOpen :size="14" />
              {{ t('projectDetail.files.actions.mergeOpen') }}
            </button>
            <button
              class="button button--danger"
              type="button"
              :disabled="deleting"
              @click="deleteCurrentProject"
            >
              <Trash2 :size="14" />
              {{ t('projectDetail.files.actions.delete') }}
            </button>
          </div>

          <div class="table-toolbar__right pd-toolbar__right">
            <button class="button" type="button" disabled :title="t('projectDetail.common.comingSoon')">
              <Filter :size="14" />
              {{ t('projectDetail.files.actions.filter') }}
            </button>
            <button class="button" type="button" disabled :title="t('projectDetail.common.comingSoon')">
              <Settings2 :size="14" />
              {{ t('projectDetail.files.actions.columns') }}
            </button>
          </div>
        </div>

        <DataTable
          :columns="columns"
          :data="pagedRows"
          :loading="loading"
          :selectable="true"
          :selected-ids="selectedFileIds"
          :show-index="true"
          :index-offset="indexOffset"
          :empty-text="t('projectDetail.files.empty')"
          @select="selectedFileIds = $event"
        >
          <template #filename="{ row }">
            <div class="pd-file-cell">
              <FileText class="pd-file-cell__icon" :size="18" />
              <div class="pd-file-cell__content">
                <button
                  v-if="canEnterWorkbench(row)"
                  class="pd-link-button"
                  type="button"
                  @click="openWorkbench(row)"
                >
                  {{ row.filename }}
                </button>
                <span v-else class="pd-file-cell__title">{{ row.filename }}</span>
                <span class="pd-file-cell__meta">{{ getFileDetailHint(row) }}</span>
              </div>
            </div>
          </template>

          <template #progress="{ row }">
            <div class="progress-bar">
              <div class="progress-bar__track">
                <div class="progress-bar__fill" :style="getProgressStyle(row.progress)" />
              </div>
              <span class="progress-bar__text">{{ row.progress }}%</span>
            </div>
          </template>

          <template #taskManage>
            <div class="pd-task-links">
              <button class="pd-inline-link" type="button" disabled :title="t('projectDetail.common.comingSoon')">
                {{ t('projectDetail.files.task.assign') }}
              </button>
              <button class="pd-inline-link" type="button" disabled :title="t('projectDetail.common.comingSoon')">
                {{ t('projectDetail.files.task.detail') }}
              </button>
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

          <template #created_at="{ row }">
            <div class="date-cell">
              {{ formatDateParts(row.created_at).date }}<br>{{ formatDateParts(row.created_at).time }}
            </div>
          </template>

          <template #source_language="{ row }">
            <span>{{ row.source_language ? getLanguageLabel(row.source_language) : getPlaceholder() }}</span>
          </template>

          <template #target_language="{ row }">
            <span>{{ row.target_language ? getLanguageLabel(row.target_language) : getPlaceholder() }}</span>
          </template>

          <template #file_size_bytes="{ row }">
            <span>{{ formatBytes(row.file_size_bytes) }}</span>
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
          :total="tableRows.length"
          :page="currentPage"
          :page-size="pageSize"
          :page-sizes="[10]"
          @update:page="currentPage = $event"
          @update:page-size="pageSize = $event"
        />
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
          type="button"
          :disabled="!actionMenuRow.has_source_document"
          :title="!actionMenuRow.has_source_document ? t('projectDetail.common.uploadRequired') : undefined"
          @click="exportProjectFile(actionMenuRow)"
        >
          {{ t('projectDetail.files.actions.export') }}
        </button>
        <button
          type="button"
          @click="openFileIssueDialog(actionMenuRow)"
        >
          {{ t('issueMarker.actions.open') }}
        </button>
        <button
          class="is-danger"
          type="button"
          :disabled="deleting"
          @click="deleteProjectFile(actionMenuRow)"
        >
          {{ t('projectDetail.files.actions.delete') }}
        </button>
      </div>
    </Teleport>

    <PreTranslateDialog
      :open="showPreTranslateDialog"
      :files="selectedProjectFiles"
      :source-language="project?.source_language ?? null"
      :target-language="project?.target_language ?? null"
      :translation-guidelines="project?.translation_guidelines ?? ''"
      @close="closePreTranslateDialog"
      @done="handlePreTranslateDone"
    />
    <IssueMarkerDialog
      :open="showIssueDialog"
      :project-id="project?.id ?? null"
      :file-record-id="issueDialogTarget?.fileRecordId ?? null"
      :context-label="issueDialogTarget?.label ?? ''"
      @close="showIssueDialog = false"
      @saved="handleIssueSaved"
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
  width: min(720px, 100%);
  display: grid;
  gap: 14px;
  padding: 16px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
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
  gap: 14px;
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

.upload-file-list {
  display: grid;
  gap: 8px;
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
  gap: 16px;
}

.pd-hero {
  padding: 8px 12px;
  min-height: var(--route-top-panel-min-height, 90px);
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
  align-self: flex-start;
  margin-top: 2px;
}

.workbench-toolbar__icon-btn {
  min-width: 34px;
  min-height: 30px;
  padding: 4px 8px;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(37, 61, 70, 0.08);
}

.workbench-action--back.workbench-toolbar__icon-btn {
  min-width: 46px;
  min-height: 32px;
  padding-inline: 12px;
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
  font-size: 16px;
}

.pd-hero__copy .panel-subtitle {
  font-size: 12px;
  line-height: 1.35;
}

.pd-hero__progress {
  width: min(260px, 100%);
  display: grid;
  gap: 4px;
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
  gap: 12px;
  padding: 0 6px;
  border-bottom: 1px solid var(--line-soft);
}

.pd-tabs__item {
  position: relative;
  padding: 12px 8px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 14px;
  font-weight: 500;
  box-shadow: none;
}

.pd-tabs__item::after {
  content: '';
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 0;
  height: 2px;
  border-radius: 999px;
  background: transparent;
}

.pd-tabs__item.is-active {
  color: var(--text-primary);
}

.pd-tabs__item.is-active::after {
  background: var(--brand-700);
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
  min-height: 32px;
  padding: 6px 10px;
  font-size: 13px;
}

.pd-basic-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 20px;
}

.pd-field {
  display: grid;
  gap: 4px;
}

.pd-field__label {
  font-size: 11px;
  color: var(--text-muted);
}

.pd-field__value {
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.35;
  word-break: break-word;
}

.pd-toolbar {
  padding: 8px 0 16px;
  flex-wrap: wrap;
}

.pd-toolbar__left,
.pd-toolbar__right {
  flex-wrap: wrap;
}

.pd-file-cell {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.pd-file-cell__icon {
  margin-top: 2px;
  color: var(--brand-700);
}

.pd-file-cell__content {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.pd-link-button {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--brand-700);
  text-align: left;
  box-shadow: none;
}

.pd-link-button:hover {
  color: var(--brand-600);
}

.pd-file-cell__title {
  color: var(--text-primary);
  font-weight: 500;
}

.pd-file-cell__meta {
  font-size: 12px;
  color: var(--text-muted);
}

.pd-task-links {
  display: grid;
  justify-items: start;
  gap: 4px;
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
  color: var(--text-secondary);
  font-size: 13px;
  box-shadow: none;
}

.pd-inline-link:disabled {
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

.pd-settings-form {
  display: grid;
  gap: 12px;
  padding: 0 16px 16px;
}

.pd-guidelines-editor {
  resize: vertical;
  min-height: 120px;
  max-height: 400px;
  font-size: 13px;
  line-height: 1.6;
  font-family: inherit;
}

.pd-settings-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
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
