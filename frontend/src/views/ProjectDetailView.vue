<script setup lang="ts">
import axios from 'axios'
import {
  ArrowLeft,
  BookOpen,
  ChevronDown,
  ChevronUp,
  Clock3,
  Download,
  FileText,
  Filter,
  FolderOpen,
  Link,
  Loader2,
  MoreHorizontal,
  Settings2,
  Sparkles,
  Trash2,
  Upload,
  Users,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import Modal from '../components/base/Modal.vue'
import DataTable from '../components/DataTable.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import PreTranslateDialog from '../components/PreTranslateDialog.vue'
import Pagination from '../components/Pagination.vue'
import { useConfirm } from '../composables/useConfirm'
import { usePageHeader } from '../composables/usePageHeader'
import { useToast } from '../composables/useToast'
import { formatLanguagePair, getLanguageLabel, languageOptions } from '../constants/languages'
import { getFileStatusMeta } from '../constants/status'
import { buildTranslatedTaskFilename, supportedTaskFileAccept } from '../constants/taskFiles'
import type { TermBase, TMCollection } from '../types/api'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'
import { getProgressStyle } from '../utils/progress'

const props = defineProps<{
  id: string
}>()

type ProjectTab = 'files' | 'settings' | 'stats' | 'summary' | 'quote'
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
  collection_id: string | null
  term_base_id: string | null
}

type ProjectRow = ProjectFileItem | Record<string, any>

const confirm = useConfirm()
const router = useRouter()
const toast = useToast()
const { t } = useI18n()

const loading = ref(false)
const deleting = ref(false)
const uploading = ref(false)
const uploadPercent = ref(0)
const loadingCollections = ref(false)
const loadingTermBases = ref(false)
const project = ref<ProjectDetail | null>(null)
const pageError = ref('')
const uploadMessage = ref('')
const selectedFiles = ref<File[]>([])
const threshold = ref(0.6)
const tmCollections = ref<TMCollection[]>([])
const termBases = ref<TermBase[]>([])
const selectedCollectionIds = ref<string[]>([])
const selectedTermBaseId = ref('')
const uploadSourceLanguage = ref('')
const uploadTargetLanguage = ref('')
const documentParseMode = ref<DocumentParseMode>('full')
const basicCollapsed = ref(false)
const activeTab = ref<ProjectTab>('files')
const showUploadModal = ref(false)
const showPreTranslateDialog = ref(false)
const uploadInputKey = ref(0)
const openActionMenuId = ref<string | null>(null)
const currentPage = ref(1)
const pageSize = ref(10)
const selectedFileIds = ref(new Set<string>())
const guidelinesText = ref('')
const savingGuidelines = ref(false)

const tabs = computed(() => ([
  { key: 'files' as const, label: t('projectDetail.tabs.files'), disabled: false },
  { key: 'settings' as const, label: t('projectDetail.tabs.settings'), disabled: false },
  { key: 'stats' as const, label: t('projectDetail.tabs.stats'), disabled: true },
  { key: 'summary' as const, label: t('projectDetail.tabs.summary'), disabled: true },
  { key: 'quote' as const, label: t('projectDetail.tabs.quote'), disabled: true },
]))

const tableRows = computed<ProjectFileItem[]>(() => project.value?.files ?? [])
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
  { key: 'created_at', label: t('projectDetail.files.columns.createdAt'), width: '170px' },
  { key: 'source_language', label: t('projectList.form.sourceLanguage'), width: '130px' },
  { key: 'target_language', label: t('projectDetail.files.columns.targetLang'), width: '130px' },
  { key: 'file_size_bytes', label: t('projectDetail.files.columns.size'), width: '120px', align: 'right' },
]))

const canOpenUploadModal = computed(() => Boolean(project.value))

const uploadButtonTitle = computed(() => {
  if (loadingCollections.value) {
    return t('projectDetail.loading')
  }
  return ''
})

const availableTMCollections = computed(() => {
  if (!uploadSourceLanguage.value || !uploadTargetLanguage.value) {
    return tmCollections.value
  }

  return tmCollections.value.filter((collection) => (
    (!collection.source_language || collection.source_language === uploadSourceLanguage.value)
    && (!collection.target_language || collection.target_language === uploadTargetLanguage.value)
  ))
})

const availableTermBases = computed(() => {
  if (!uploadSourceLanguage.value || !uploadTargetLanguage.value) {
    return termBases.value
  }

  return termBases.value.filter((termBase) => (
    termBase.source_language === uploadSourceLanguage.value
    && termBase.target_language === uploadTargetLanguage.value
  ))
})

const documentParseModeHint = computed(() => (
  documentParseMode.value === 'body_only'
    ? t('documentParsing.hints.bodyOnly')
    : t('documentParsing.hints.full')
))

usePageHeader(() => ({
  title: project.value?.filename || t('projectDetail.titleFallback'),
  description: t('projectDetail.description'),
  breadcrumbs: [
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

function onFileChange(event: Event) {
  selectedFiles.value = Array.from((event.target as HTMLInputElement).files ?? [])
}

function resetUploadForm() {
  selectedFiles.value = []
  uploadMessage.value = ''
  uploadPercent.value = 0
  documentParseMode.value = 'full'
  uploadInputKey.value += 1
}

function openUploadDialog() {
  if (!canOpenUploadModal.value) {
    return
  }

  resetUploadForm()
  uploadSourceLanguage.value = project.value?.source_language || ''
  uploadTargetLanguage.value = project.value?.target_language || ''
  selectedCollectionIds.value = selectedCollectionIds.value.filter((collectionId) => (
    availableTMCollections.value.some((collection) => collection.id === collectionId)
  ))
  selectedTermBaseId.value = availableTermBases.value.some((termBase) => termBase.id === project.value?.term_base_id)
    ? project.value?.term_base_id || ''
    : ''
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

function closeActionMenu() {
  openActionMenuId.value = null
}

function toggleActionMenu(id: string) {
  openActionMenuId.value = openActionMenuId.value === id ? null : id
}

function handleDocumentClick() {
  closeActionMenu()
}

function goBack() {
  void router.push({ name: 'projects' })
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
    query: { from: 'project', pid: props.id },
  })
}

async function loadProject() {
  loading.value = true
  pageError.value = ''

  try {
    const { data } = await http.get<ProjectDetail>(`/projects/${props.id}`)
    project.value = data
    selectedTermBaseId.value = data.term_base_id || ''
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

async function loadTMCollections() {
  loadingCollections.value = true

  try {
    const { data } = await http.get<TMCollection[]>('/translation-memory/collections')
    tmCollections.value = data
  } catch (error) {
    pageError.value = getErrorMessage(error, t('projectDetail.errors.collectionsLoad'))
  } finally {
    loadingCollections.value = false
  }
}

async function loadTermBases() {
  loadingTermBases.value = true

  try {
    const { data } = await http.get<TermBase[]>('/term-bases')
    termBases.value = data
  } catch (error) {
    console.error('Failed to load term bases:', error)
  } finally {
    loadingTermBases.value = false
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
    formData.append('threshold', String(threshold.value))
    formData.append('source_language', uploadSourceLanguage.value)
    formData.append('target_language', uploadTargetLanguage.value)
    formData.append('document_parse_mode', documentParseMode.value)
    selectedCollectionIds.value.forEach((collectionId) => {
      formData.append('collection_ids', collectionId)
    })
    if (selectedTermBaseId.value) {
      formData.append('term_base_id', selectedTermBaseId.value)
    }

    await http.post(`/projects/${props.id}/source-document`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        uploadPercent.value = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
      },
    })

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
  void loadProject()
  void loadTMCollections()
  void loadTermBases()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})

watch([uploadSourceLanguage, uploadTargetLanguage], () => {
  selectedCollectionIds.value = selectedCollectionIds.value.filter((collectionId) => (
    availableTMCollections.value.some((collection) => collection.id === collectionId)
  ))
  if (
    selectedTermBaseId.value
    && !availableTermBases.value.some((termBase) => termBase.id === selectedTermBaseId.value)
  ) {
    selectedTermBaseId.value = ''
  }
})
</script>

<template>
  <div class="content-stack pd-layout">
    <section class="panel pd-hero">
      <div class="pd-hero__main">
        <div class="pd-hero__left">
          <button class="button" type="button" @click="goBack">
            <ArrowLeft :size="16" />
            {{ t('projectDetail.back') }}
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
              class="button button--primary"
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
              class="button"
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
                  @click.stop="toggleActionMenu(row.id)"
                >
                  <MoreHorizontal :size="16" />
                </button>

                <div v-if="openActionMenuId === row.id" class="pd-action-menu__dropdown">
                  <button
                    type="button"
                    :disabled="!canEnterWorkbench(row)"
                    :title="!canEnterWorkbench(row) ? getFileDetailHint(row) : undefined"
                    @click="openWorkbench(row)"
                  >
                    {{ t('projectDetail.enterWorkbench') }}
                  </button>
                  <button
                    type="button"
                    :disabled="!row.has_source_document"
                    :title="!row.has_source_document ? t('projectDetail.common.uploadRequired') : undefined"
                    @click="exportProjectFile(row)"
                  >
                    {{ t('projectDetail.files.actions.export') }}
                  </button>
                  <button
                    class="is-danger"
                    type="button"
                    :disabled="deleting"
                    @click="deleteProjectFile(row)"
                  >
                    {{ t('projectDetail.files.actions.delete') }}
                  </button>
                </div>
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

    <Modal
      :open="showUploadModal"
      :title="t('projectDetail.uploadDialog.title')"
      description="支持 DOCX、TXT、CSV、HTML、Markdown、JSON、YAML、PO、SRT、SDLXLIFF、ZIP 等任务文件。"
      width="min(680px, calc(100vw - 32px))"
      @close="closeUploadDialog"
    >
      <div class="form-grid-2">
        <label class="field">
          <span class="field__label">源文件</span>
          <input
            :key="uploadInputKey"
            class="field__control"
            type="file"
            multiple
            :accept="supportedTaskFileAccept"
            aria-label="源文件"
            @change="onFileChange"
          />
        </label>

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

        <label class="field field--compact">
          <span class="field__label">{{ t('projectDetail.fields.threshold') }}</span>
          <input
            v-model.number="threshold"
            class="field__control"
            type="number"
            step="0.05"
            min="0"
            max="1"
            :aria-label="t('projectDetail.fields.threshold')"
          />
        </label>

        <label class="field field--full">
          <span class="field__label">{{ t('documentParsing.label') }}</span>
          <select v-model="documentParseMode" class="field__control">
            <option value="full">{{ t('documentParsing.modes.full') }}</option>
            <option value="body_only">{{ t('documentParsing.modes.bodyOnly') }}</option>
          </select>
          <span class="hint-text">{{ documentParseModeHint }}</span>
        </label>

        <label class="field field--full">
          <span class="field__label">{{ t('taskList.fields.termBase') }}</span>
          <select
            v-model="selectedTermBaseId"
            class="field__control"
            :disabled="loadingTermBases"
          >
            <option value="">{{ t('taskList.hints.noTermBase') }}</option>
            <option
              v-for="termBase in availableTermBases"
              :key="termBase.id"
              :value="termBase.id"
            >
              {{ termBase.name }}（{{ formatLanguagePair(termBase.source_language, termBase.target_language) }} / {{ termBase.entry_count }} 条）
            </option>
          </select>
        </label>

        <label class="field field--full">
          <span class="field__label">{{ t('projectDetail.fields.collections') }}</span>
          <select
            v-model="selectedCollectionIds"
            class="field__control field__control--multi"
            multiple
            :disabled="loadingCollections || availableTMCollections.length === 0"
            :aria-label="t('projectDetail.fields.collections')"
          >
            <option v-for="collection in availableTMCollections" :key="collection.id" :value="collection.id">
              {{ collection.name }}（{{ formatLanguagePair(collection.source_language, collection.target_language) }} / {{ collection.entry_count }} 条）
            </option>
          </select>
          <span class="hint-text">
            {{ availableTMCollections.length ? t('projectDetail.hints.collections') : t('projectDetail.hints.noCollections') }}
          </span>
        </label>
      </div>

      <div v-if="uploading" class="pd-upload-progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div class="progress-bar__fill" :style="{ width: `${uploadPercent}%` }" />
          </div>
          <span class="progress-bar__text">{{ uploadPercent }}%</span>
        </div>
      </div>

      <p v-if="uploadMessage" class="form-message is-error">{{ uploadMessage }}</p>

      <template #footer>
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
      </template>
    </Modal>

    <PreTranslateDialog
      :open="showPreTranslateDialog"
      :files="selectedProjectFiles"
      :source-language="project?.source_language ?? null"
      :target-language="project?.target_language ?? null"
      :translation-guidelines="project?.translation_guidelines ?? ''"
      @close="closePreTranslateDialog"
      @done="handlePreTranslateDone"
    />
  </div>
</template>

<style scoped>
.pd-layout {
  gap: 16px;
}

.pd-hero {
  padding: 14px 16px;
}

.pd-hero__main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.pd-hero__left {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
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
  .pd-basic-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .pd-hero__main,
  .pd-hero__left,
  .pd-panel-head {
    flex-direction: column;
    align-items: stretch;
  }

  .pd-basic-grid {
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .pd-tabs {
    overflow-x: auto;
  }
}
</style>
