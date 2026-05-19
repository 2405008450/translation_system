<script setup lang="ts">
import axios from 'axios'
import {
  ArrowRight,
  Download,
  Flag,
  Loader2,
  MoreHorizontal,
  Search,
  Settings2,
  Trash2,
  Upload,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import DataTable from '../components/DataTable.vue'
import DocumentParseSettings from '../components/DocumentParseSettings.vue'
import IssueMarkerDialog from '../components/IssueMarkerDialog.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import Pagination from '../components/Pagination.vue'
import ResourceImportDialog from '../components/ResourceImportDialog.vue'
import { useConfirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'
import { formatLanguagePair, languageOptions } from '../constants/languages'
import { getFileStatusMeta } from '../constants/status'
import { supportedTaskFileAccept } from '../constants/taskFiles'
import { useTaskStore } from '../stores/task'
import type {
  DocumentParseMode,
  DocumentParseOptions,
  IssueMarker,
  TermBase,
  TMCollection,
  UploadCapabilitiesResponse,
  UploadCapability,
} from '../types/api'
import { getProgressStyle, isProgressComplete } from '../utils/progress'

interface ProjectRow {
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
  source_language: string | null
  target_language: string | null
  creator: string | null
  deadline: string | null
  access_level: string | null
  created_at: string
  updated_at: string
}

interface ProjectListResponse {
  items: ProjectRow[]
  total: number
  skip: number
  limit: number
}

type MainTab = 'tasks' | 'performance'
type SubTab = 'all' | 'incomplete'
type ResourceImportTab = 'tm' | 'term'

const NO_TM_COLLECTION_ID = '__NO_TM_COLLECTION__'
const DEFAULT_DOCUMENT_PARSE_OPTIONS: DocumentParseOptions = {
  include_headers_footers: true,
  include_footnotes_endnotes: true,
  include_comments: true,
  clean_format: false,
}

const taskStore = useTaskStore()
const confirm = useConfirm()
const toast = useToast()
const router = useRouter()
const { t } = useI18n()

const mainTab = ref<MainTab>('tasks')
const subTab = ref<SubTab>('all')
const selectedFile = ref<File | null>(null)
const threshold = ref(0.6)
const pageError = ref('')
const tmCollections = ref<TMCollection[]>([])
const loadingCollections = ref(false)
const selectedCollectionIds = ref<string[]>([NO_TM_COLLECTION_ID])
const termBases = ref<TermBase[]>([])
const loadingTermBases = ref(false)
const selectedTermBaseId = ref('')
const uploadSourceLanguage = ref('')
const uploadTargetLanguage = ref('')
const documentParseMode = ref<DocumentParseMode>('full')
const documentParseOptions = ref<DocumentParseOptions>({ ...DEFAULT_DOCUMENT_PARSE_OPTIONS })
const uploadCapabilities = ref<UploadCapability[]>([])
const uploadFileAccept = ref(supportedTaskFileAccept)
const loadingUploadCapabilities = ref(false)
const showUploadForm = ref(false)
const currentPage = ref(1)
const pageSize = ref(50)
const selectedIds = ref(new Set<string>())
const sortKey = ref('')
const sortOrder = ref<'asc' | 'desc'>('desc')
const searchQuery = ref('')
const projects = ref<ProjectRow[]>([])
const projectsLoading = ref(false)
let searchTimer: ReturnType<typeof setTimeout> | null = null
const showImportDialog = ref(false)
const showIssueDialog = ref(false)
const importDialogInitialTab = ref<ResourceImportTab>('tm')
const openActionMenuId = ref<string | null>(null)
const issueTarget = ref<ProjectRow | null>(null)
const importDialogContext = ref<{
  label: string
  sourceLanguage: string | null
  targetLanguage: string | null
}>({
  label: t('taskList.mainTab'),
  sourceLanguage: null,
  targetLanguage: null,
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

const selectedUploadCollectionIds = computed(() => (
  selectedCollectionIds.value.filter((collectionId) => collectionId !== NO_TM_COLLECTION_ID)
))

const selectedCollectionIdsModel = computed<string[]>({
  get: () => selectedCollectionIds.value,
  set: (collectionIds) => {
    selectedCollectionIds.value = normalizeSelectedCollectionIds(collectionIds)
  },
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

const columns = computed<DataTableColumn[]>(() => ([
  { key: 'filename', label: t('taskList.columns.filename'), sortable: true },
  { key: 'status', label: t('taskList.columns.status'), width: '110px' },
  { key: 'progress', label: t('projectList.status.progress'), width: '180px' },
  { key: 'file_count', label: t('projectDetail.base.fileCount'), width: '110px', align: 'right' },
  { key: 'open_issue_count', label: t('issueMarker.list.title'), width: '120px' },
  { key: 'created_at', label: t('taskList.columns.createdAt'), width: '160px', sortable: true },
  { key: 'updated_at', label: t('taskList.columns.updatedAt'), width: '160px', sortable: true },
]))

function openImportDialog(
  task?: Partial<{ filename: string; source_language: string | null; target_language: string | null }> | null,
  tab: ResourceImportTab = 'tm',
) {
  importDialogInitialTab.value = tab
  importDialogContext.value = {
    label: task ? t('workbench.importContext', { name: task.filename }) : t('taskList.mainTab'),
    sourceLanguage: task?.source_language ?? null,
    targetLanguage: task?.target_language ?? null,
  }
  showImportDialog.value = true
}

function formatDate(value: string) {
  const date = new Date(value)
  return {
    date: date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }),
    time: date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }),
  }
}

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

async function loadProjects() {
  pageError.value = ''
  projectsLoading.value = true
  try {
    const { data } = await http.get<ProjectListResponse>('/projects', {
      params: {
        skip: 0,
        limit: 200,
        search: searchQuery.value.trim(),
      },
    })
    projects.value = data.items
  } catch (error) {
    pageError.value = getErrorMessage(error, t('taskList.errors.load'))
  } finally {
    projectsLoading.value = false
  }
}

async function loadTMCollections() {
  loadingCollections.value = true
  try {
    const { data } = await http.get<TMCollection[]>('/translation-memory/collections')
    tmCollections.value = data
  } catch (error) {
    pageError.value = getErrorMessage(error, t('taskList.errors.collectionsLoad'))
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
    // 术语库加载失败不阻止上传
    console.error('Failed to load term bases:', error)
  } finally {
    loadingTermBases.value = false
  }
}

async function loadUploadCapabilities() {
  loadingUploadCapabilities.value = true
  try {
    const { data } = await http.get<UploadCapabilitiesResponse>('/file-records/upload-capabilities')
    uploadCapabilities.value = data.formats
    uploadFileAccept.value = data.accept || supportedTaskFileAccept
  } catch (error) {
    console.error('Failed to load upload capabilities:', error)
    uploadCapabilities.value = []
    uploadFileAccept.value = supportedTaskFileAccept
  } finally {
    loadingUploadCapabilities.value = false
  }
}

function onFileChange(event: Event) {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

function normalizeSelectedCollectionIds(collectionIds: string[]) {
  const availableIds = new Set(availableTMCollections.value.map((collection) => collection.id))
  const wantsNoCollection = collectionIds.includes(NO_TM_COLLECTION_ID)
  const hadNoCollection = selectedCollectionIds.value.includes(NO_TM_COLLECTION_ID)

  if (wantsNoCollection && (!hadNoCollection || collectionIds.length === 1)) {
    return [NO_TM_COLLECTION_ID]
  }

  const normalizedIds = collectionIds.filter((collectionId) => (
    collectionId !== NO_TM_COLLECTION_ID && availableIds.has(collectionId)
  ))
  return normalizedIds.length > 0 ? normalizedIds : [NO_TM_COLLECTION_ID]
}

async function uploadFile() {
  if (!selectedFile.value) {
    pageError.value = t('taskList.errors.selectFile')
    return
  }
  if (!uploadSourceLanguage.value || !uploadTargetLanguage.value) {
    pageError.value = t('taskList.errors.selectLanguagePair')
    return
  }
  if (uploadSourceLanguage.value === uploadTargetLanguage.value) {
    pageError.value = t('projectList.errors.sameLanguage')
    return
  }

  pageError.value = ''
  try {
    const result = await taskStore.uploadTask(
      selectedFile.value,
      threshold.value,
      selectedUploadCollectionIds.value,
      selectedTermBaseId.value || null,
      uploadSourceLanguage.value,
      uploadTargetLanguage.value,
      documentParseMode.value,
      documentParseOptions.value,
    )
    selectedFile.value = null
    const fileInput = document.getElementById('upload-file') as HTMLInputElement | null
    if (fileInput) {
      fileInput.value = ''
    }
    toast.success(t('taskList.messages.uploaded'))
    await router.push({ name: 'workbench', params: { id: result.id } })
  } catch (error) {
    pageError.value = getErrorMessage(error, t('taskList.errors.upload'))
  }
}

async function removeProject(projectId: string, name: string) {
  const confirmed = await confirm({
    title: t('taskList.messages.deleting'),
    message: t('taskList.messages.deleteConfirm', { name }),
    confirmText: t('common.actions.delete'),
    danger: true,
  })

  if (!confirmed) {
    return
  }

  pageError.value = ''
  try {
    await http.delete(`/projects/${projectId}`)
    selectedIds.value.delete(projectId)
    toast.success(t('taskList.messages.deleted', { name }))
    await loadProjects()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('taskList.errors.delete'))
  }
}

function getStatusClass(status: string) {
  const meta = getFileStatusMeta(status)
  return `project-status--${meta.tone}`
}

const filteredProjects = computed(() => {
  let rows = [...projects.value]

  if (subTab.value === 'incomplete') {
    rows = rows.filter((row) => row.status !== 'completed' && row.status !== 'translated')
  }

  if (sortKey.value) {
    const key = sortKey.value as keyof ProjectRow
    const direction = sortOrder.value === 'asc' ? 1 : -1
    rows.sort((left, right) => {
      const leftVal = left[key]
      const rightVal = right[key]
      if (key === 'progress' || key === 'file_count') {
        return ((Number(leftVal) || 0) - (Number(rightVal) || 0)) * direction
      }
      return String(leftVal ?? '').localeCompare(String(rightVal ?? '')) * direction
    })
  }

  return rows
})

const totalCount = computed(() => filteredProjects.value.length)
const pagedProjects = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredProjects.value.slice(start, start + pageSize.value)
})
const indexOffset = computed(() => (currentPage.value - 1) * pageSize.value)

function handleSort(key: string, order: 'asc' | 'desc') {
  sortKey.value = key
  sortOrder.value = order
}

function handleSelect(ids: Set<string>) {
  selectedIds.value = ids
}

function toggleActionMenu(id: string) {
  openActionMenuId.value = openActionMenuId.value === id ? null : id
}

function closeActionMenu() {
  openActionMenuId.value = null
}

function handleDocumentClick() {
  closeActionMenu()
}

function goToAssets() {
  void router.push({ name: 'tm' })
}

function openProjectDetail(row: ProjectRow) {
  closeActionMenu()
  void router.push({
    name: 'project-detail',
    params: { id: row.id },
    query: { from: 'tasks' },
  })
}

function openIssueDialog(row: ProjectRow) {
  closeActionMenu()
  issueTarget.value = row
  showIssueDialog.value = true
}

async function handleIssueSaved(_marker: IssueMarker) {
  showIssueDialog.value = false
  issueTarget.value = null
  toast.success(t('issueMarker.messages.saved'))
  await loadProjects()
}

watch(searchQuery, () => {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
  searchTimer = setTimeout(() => {
    currentPage.value = 1
    void loadProjects()
  }, 300)
})

watch(subTab, () => {
  currentPage.value = 1
})

watch([uploadSourceLanguage, uploadTargetLanguage], () => {
  selectedCollectionIds.value = normalizeSelectedCollectionIds(selectedCollectionIds.value)
  if (
    selectedTermBaseId.value
    && !availableTermBases.value.some((termBase) => termBase.id === selectedTermBaseId.value)
  ) {
    selectedTermBaseId.value = ''
  }
})

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
  void loadProjects()
  void loadTMCollections()
  void loadTermBases()
  void loadUploadCapabilities()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
})
</script>

<template>
  <div>
    <div v-if="showUploadForm" class="upload-panel">
      <div class="section-title">{{ t('taskList.uploadSection') }}</div>
      <div class="upload-form upload-form--inline upload-form--task">
        <label class="field">
          <span class="field__label">任务文件</span>
          <input
            id="upload-file"
            class="field__control"
            data-testid="task-upload-file-input"
            type="file"
            :accept="uploadFileAccept"
            @change="onFileChange"
          />
        </label>

        <label class="field field--compact">
          <span class="field__label">{{ t('projectList.form.sourceLanguage') }}</span>
          <select v-model="uploadSourceLanguage" class="field__control" data-testid="task-upload-source-language">
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

        <label class="field field--compact">
          <span class="field__label">{{ t('projectList.form.targetLanguage') }}</span>
          <select v-model="uploadTargetLanguage" class="field__control" data-testid="task-upload-target-language">
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
          <span class="field__label">{{ t('taskList.fields.threshold') }}</span>
          <input
            v-model.number="threshold"
            class="field__control"
            type="number"
            step="0.05"
            min="0"
            max="1"
          />
        </label>

        <DocumentParseSettings
          v-model="documentParseMode"
          v-model:parse-options="documentParseOptions"
          :capabilities="uploadCapabilities"
          :selected-files="selectedFile ? [selectedFile] : []"
          :loading="loadingUploadCapabilities"
          variant="inline"
        />

        <label class="field field--collections">
          <span class="field__label">{{ t('taskList.fields.collections') }}</span>
          <select
            v-model="selectedCollectionIdsModel"
            class="field__control field__control--multi"
            multiple
            :disabled="loadingCollections"
          >
            <option :value="NO_TM_COLLECTION_ID">
              {{ t('taskList.fields.noCollection') }}
            </option>
            <option v-for="collection in availableTMCollections" :key="collection.id" :value="collection.id">
              {{ collection.name }}（{{ formatLanguagePair(collection.source_language, collection.target_language) }} / {{ collection.entry_count }} 条）
            </option>
          </select>
          <span class="hint-text">
            {{ availableTMCollections.length ? t('taskList.hints.collections') : t('taskList.hints.noCollections') }}
          </span>
        </label>

        <label class="field field--compact">
          <span class="field__label">{{ t('taskList.fields.termBase') }}</span>
          <select
            v-model="selectedTermBaseId"
            class="field__control"
            :disabled="loadingTermBases || availableTermBases.length === 0"
          >
            <option value="">{{ t('taskList.hints.noTermBase') }}</option>
            <option v-for="termBase in availableTermBases" :key="termBase.id" :value="termBase.id">
              {{ termBase.name }}（{{ formatLanguagePair(termBase.source_language, termBase.target_language) }} / {{ termBase.entry_count }} 条）
            </option>
          </select>
        </label>

        <button
          class="button button--primary"
          data-testid="task-upload-submit"
          type="button"
          :disabled="taskStore.uploading.active || !selectedFile || !uploadSourceLanguage || !uploadTargetLanguage"
          @click="uploadFile"
        >
          <Loader2 v-if="taskStore.uploading.active" class="lucide-spin" />
          <Upload v-else />
          {{ taskStore.uploading.active ? t('taskList.messages.uploading', { percent: taskStore.uploading.percent }) : t('taskList.toolbar.uploadTask') }}
        </button>
      </div>

      <div v-if="taskStore.uploading.active" class="task-upload-progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div
              class="progress-bar__fill"
              :class="{ 'is-complete': isProgressComplete(taskStore.uploading.percent) }"
              :style="{ width: `${taskStore.uploading.percent}%` }"
            />
          </div>
          <span class="progress-bar__text">{{ taskStore.uploading.percent }}%</span>
        </div>
        <span class="hint-text">{{ t('taskList.messages.uploadingFile', { name: taskStore.uploading.fileName }) }}</span>
      </div>

      <p v-if="pageError" class="form-message is-error task-page__message">{{ pageError }}</p>
    </div>

    <div class="table-page">
      <div class="table-page__header">
        <div class="tab-bar" style="border-bottom: none;">
          <button
            class="tab-item"
            :class="{ 'is-active': mainTab === 'tasks' }"
            type="button"
            @click="mainTab = 'tasks'"
          >
            {{ t('taskList.mainTab') }}
          </button>
          <button
            class="tab-item"
            :class="{ 'is-active': mainTab === 'performance' }"
            type="button"
            disabled
            :title="t('taskList.performanceSoon')"
          >
            {{ t('taskList.performance') }}
          </button>
        </div>
      </div>

      <template v-if="mainTab === 'tasks'">
        <div class="task-subtabs">
          <div class="tab-bar">
            <button
              class="tab-item"
              :class="{ 'is-active': subTab === 'all' }"
              type="button"
              @click="subTab = 'all'; currentPage = 1"
            >
              {{ t('taskList.all') }}
            </button>
            <button
              class="tab-item"
              :class="{ 'is-active': subTab === 'incomplete' }"
              type="button"
              @click="subTab = 'incomplete'; currentPage = 1"
            >
              {{ t('taskList.incomplete') }}
            </button>
            <div class="tab-bar__meta">
              <span>{{ t('taskList.count', { count: totalCount }) }}</span>
              <span v-if="selectedIds.size > 0">{{ t('taskList.selected', { count: selectedIds.size }) }}</span>
            </div>
          </div>
        </div>

        <div class="table-toolbar task-table-toolbar">
          <div class="table-toolbar__left">
            <div class="table-page__search">
              <Search :size="14" class="table-page__search-icon" />
              <input
                v-model="searchQuery"
                class="table-page__search-input"
                type="text"
                :placeholder="t('taskList.searchPlaceholder')"
              />
            </div>
          </div>
          <div class="table-toolbar__right">
            <button class="button" data-testid="task-upload-toggle" type="button" @click="showUploadForm = !showUploadForm">
              <Upload :size="14" />
              {{ showUploadForm ? t('taskList.toolbar.collapseUpload') : t('taskList.toolbar.uploadTask') }}
            </button>
            <button
              class="button"
              type="button"
              @click="goToAssets"
            >
              <Upload :size="14" />
              {{ t('taskList.toolbar.importAssets') }}
            </button>
            <button class="button" type="button" disabled :title="t('common.comingSoon')">
              <Download :size="14" />
              {{ t('taskList.toolbar.export') }}
            </button>
            <button class="button" type="button" disabled :title="t('common.comingSoon')">
              <Settings2 :size="14" />
              {{ t('taskList.toolbar.columns') }}
            </button>
            <button class="button" type="button" disabled :title="t('common.comingSoon')">
              <MoreHorizontal :size="14" />
              {{ t('taskList.toolbar.cardMode') }}
            </button>
          </div>
        </div>

        <p v-if="pageError && !showUploadForm" class="form-message is-error task-page__message">{{ pageError }}</p>

        <div class="table-page__body">
          <DataTable
            test-id="task-table"
            row-test-id-prefix="task-row"
            :columns="columns"
            :data="pagedProjects"
            :loading="projectsLoading"
            :selectable="true"
            :selected-ids="selectedIds"
            :sort-key="sortKey"
            :sort-order="sortOrder"
            :show-index="true"
            :index-offset="indexOffset"
            :empty-text="t('taskList.empty')"
            @sort="handleSort"
            @select="handleSelect"
          >
            <template #filename="{ row }">
              <button
                class="text-link project-link"
                type="button"
                @click="openProjectDetail(row as ProjectRow)"
              >
                {{ row.filename }}
              </button>
            </template>

            <template #status="{ row }">
              <span class="project-status" :class="getStatusClass(row.status)">
                {{ getFileStatusMeta(row.status).label }}
              </span>
            </template>

            <template #progress="{ row }">
              <div class="progress-bar">
                <div class="progress-bar__track">
                  <div
                    class="progress-bar__fill"
                    :class="{ 'is-complete': isProgressComplete(row.progress) }"
                    :style="getProgressStyle(row.progress, row.status)"
                  />
                </div>
                <span class="progress-bar__text">{{ row.progress }}%</span>
              </div>
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
                @click="openIssueDialog(row as ProjectRow)"
              >
                <Flag :size="13" />
                {{ Number(row.open_issue_count || 0) > 0 ? row.open_issue_count : t('common.none') }}
              </button>
            </template>

            <template #created_at="{ row }">
              <div class="date-cell">
                {{ formatDate(row.created_at).date }}<br>{{ formatDate(row.created_at).time }}
              </div>
            </template>

            <template #updated_at="{ row }">
              <div class="date-cell">
                {{ formatDate(row.updated_at).date }}<br>{{ formatDate(row.updated_at).time }}
              </div>
            </template>

            <template #actions="{ row }">
              <div class="task-row-actions" @click.stop>
                <button
                  class="data-table__actions-btn"
                  type="button"
                  :title="t('taskList.actions.continue')"
                  :aria-label="t('taskList.actions.continue')"
                  @click="openProjectDetail(row as ProjectRow)"
                >
                  <ArrowRight :size="16" />
                </button>
                <button
                  class="data-table__actions-btn"
                  type="button"
                  :title="t('issueMarker.actions.open')"
                  :aria-label="t('issueMarker.actions.open')"
                  @click="openIssueDialog(row as ProjectRow)"
                >
                  <Flag :size="14" />
                </button>
                <div class="task-action-menu">
                  <button
                    class="data-table__actions-btn"
                    type="button"
                    :title="t('taskList.actions.more')"
                    :aria-label="t('taskList.actions.more')"
                    @click.stop="toggleActionMenu(row.id)"
                  >
                    <MoreHorizontal :size="16" />
                  </button>
                  <div v-if="openActionMenuId === row.id" class="task-action-menu__dropdown">
                    <button type="button" @click="openProjectDetail(row as ProjectRow)">
                      {{ t('taskList.actions.details') }}
                    </button>
                    <button
                      type="button"
                      @click="openImportDialog(row); closeActionMenu()"
                    >
                      {{ t('taskList.actions.importResources') }}
                    </button>
                    <button type="button" @click="openIssueDialog(row as ProjectRow)">
                      {{ t('issueMarker.actions.open') }}
                    </button>
                    <button
                      class="is-danger"
                      type="button"
                      @click="removeProject(row.id, row.filename); closeActionMenu()"
                    >
                      {{ t('taskList.actions.delete') }}
                    </button>
                  </div>
                </div>
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
      </template>

      <template v-else>
        <div class="empty-state" style="padding: 60px 20px;">
          {{ t('taskList.performanceSoon') }}
        </div>
      </template>
    </div>

    <ResourceImportDialog
      :open="showImportDialog"
      :initial-tab="importDialogInitialTab"
      :context-label="importDialogContext.label"
      :source-language="importDialogContext.sourceLanguage"
      :target-language="importDialogContext.targetLanguage"
      @close="showImportDialog = false"
    />
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
.upload-panel {
  display: grid;
  gap: 14px;
  padding: 16px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: none;
}

.upload-panel .section-title {
  margin-bottom: 0;
  font-size: 15px;
}

.upload-form--task {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  align-items: start;
  margin-top: 0;
}

.upload-form--task > .field:first-child,
.upload-form--task > .field--collections {
  grid-column: span 2;
}

.upload-form--task > .button {
  align-self: end;
  min-height: 42px;
}

.task-subtabs {
  padding: 0 20px;
}

.task-table-toolbar {
  padding: 8px 20px;
}

.task-page__message {
  margin: 0 20px 12px;
}

.task-upload-progress {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.project-link {
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
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

.task-row-actions {
  display: flex;
  gap: 4px;
  justify-content: center;
  position: relative;
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

.task-action-menu {
  position: relative;
}

.task-action-menu__dropdown {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 20;
  min-width: 132px;
  display: grid;
  gap: 4px;
  padding: 8px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--surface-panel);
  box-shadow: var(--shadow-soft);
}

.task-action-menu__dropdown button {
  justify-content: flex-start;
  min-height: 34px;
  padding: 6px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  box-shadow: none;
}

.task-action-menu__dropdown button:hover {
  background: var(--surface-muted);
  color: var(--text-primary);
}

.task-action-menu__dropdown button.is-danger {
  color: var(--state-danger);
}

.task-action-menu__dropdown button.is-danger:hover {
  background: var(--state-danger-bg);
}

@media (max-width: 1100px) {
  .upload-form--task {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .upload-form--task {
    grid-template-columns: 1fr;
  }

  .upload-form--task > .field:first-child,
  .upload-form--task > .field--collections {
    grid-column: auto;
  }
}
</style>
