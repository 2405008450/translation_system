<script setup lang="ts">
import axios from 'axios'
import {
  ArrowLeft,
  ChevronDown,
  Download,
  FileCode2,
  FileSpreadsheet,
  Loader2,
  Pencil,
  Plus,
  Search,
  Save,
  Trash2,
  Upload,
  X,
} from 'lucide-vue-next'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import Modal from '../components/base/Modal.vue'
import Pagination from '../components/Pagination.vue'
import { useConfirm } from '../composables/useConfirm'
import { usePageHeader } from '../composables/usePageHeader'
import { formatLanguagePair, languageOptions } from '../constants/languages'
import { useAuthStore } from '../stores/auth'
import type { GlossaryBase, GlossaryEntryRecord, GlossaryImportSummary, PaginatedResponse } from '../types/api'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'
import { refreshGlobalNotifications } from '../utils/notifications'

type ExportFormat = 'xlsx' | 'tmx'

interface ResourceExportTask {
  task_id: string
  resource_type: 'glossary'
  resource_id: string
  format: ExportFormat
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  message: string
  result: {
    filename: string
    size_bytes: number
    total_entries: number
  } | null
  error: string | null
}

const props = defineProps<{
  id: string
}>()

const router = useRouter()
const confirm = useConfirm()
const authStore = useAuthStore()

const glossaryBase = ref<GlossaryBase | null>(null)
const entries = ref<GlossaryEntryRecord[]>([])
const loadingBase = ref(false)
const loadingEntries = ref(false)
const creatingEntry = ref(false)
const updatingEntryId = ref('')
const deletingEntryId = ref('')
const updatingBase = ref(false)
const importing = ref(false)
const exportingEntries = ref(false)
const exportProgress = ref(0)
const pageError = ref('')
const entryMessage = ref('')
const importMessage = ref('')
const importSummary = ref<GlossaryImportSummary | null>(null)

const searchText = ref('')
const matchCase = ref(false)
const currentPage = ref(1)
const pageSize = ref(50)
const totalEntries = ref(0)
const showAddDialog = ref(false)
const showBaseEditDialog = ref(false)
const showImportDialog = ref(false)
const showExportMenu = ref(false)

const newSourceText = ref('')
const newTargetText = ref('')
const newNote = ref('')
const editingEntryId = ref('')
const editSourceText = ref('')
const editTargetText = ref('')
const editNote = ref('')
const entrySortKey = ref('updated_at')
const entrySortOrder = ref<'asc' | 'desc'>('desc')

const editBaseName = ref('')
const editBaseDescription = ref('')
const editBaseSourceLanguage = ref('')
const editBaseTargetLanguage = ref('')

const importFileInput = ref<HTMLInputElement | null>(null)
const selectedImportFile = ref<File | null>(null)
const importUploadPercent = ref(0)

let searchTimer: number | null = null
let exportPollTimer: number | null = null
let disposed = false

const canManageResources = computed(() => authStore.isAdmin)
const pageTitle = computed(() => glossaryBase.value?.name || '词汇表详情')
const languagePairLabel = computed(() => (
  glossaryBase.value
    ? formatLanguagePair(glossaryBase.value.source_language, glossaryBase.value.target_language)
    : '-'
))
const indexOffset = computed(() => (currentPage.value - 1) * pageSize.value)
const tableColumnCount = computed(() => 8 + Number(canManageResources.value))
const exportButtonText = computed(() => (
  exportingEntries.value ? `导出中 ${exportProgress.value}%` : '导出'
))

usePageHeader(() => ({
  title: pageTitle.value,
  description: '维护 AI 预翻译专用词汇表。命中词条只在 LLM 调用时注入，不写入句段展示。',
  breadcrumbs: [
    { label: '语言资产' },
    { label: '词汇表', to: { name: 'glossary' } },
    { label: pageTitle.value },
  ],
}))

function formatDate(value: string | null | undefined) {
  if (!value) {
    return '-'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '-'
  }
  const pad = (input: number) => String(input).padStart(2, '0')
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`,
  ].join(' ')
}

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

function fileBaseName(file: File) {
  return file.name.replace(/\.[^.]+$/, '')
}

function resetAddForm() {
  newSourceText.value = ''
  newTargetText.value = ''
  newNote.value = ''
}

function resetEditForm() {
  editingEntryId.value = ''
  editSourceText.value = ''
  editTargetText.value = ''
  editNote.value = ''
}

function openAddDialog() {
  if (!canManageResources.value) {
    entryMessage.value = '当前账号只能查看和导出词汇表。'
    return
  }
  resetAddForm()
  entryMessage.value = ''
  showAddDialog.value = true
}

function openBaseEditDialog() {
  if (!glossaryBase.value || !canManageResources.value) {
    entryMessage.value = '当前账号只能查看和导出词汇表。'
    return
  }
  editBaseName.value = glossaryBase.value.name
  editBaseDescription.value = glossaryBase.value.description || ''
  editBaseSourceLanguage.value = glossaryBase.value.source_language
  editBaseTargetLanguage.value = glossaryBase.value.target_language
  entryMessage.value = ''
  showBaseEditDialog.value = true
}

function closeBaseEditDialog() {
  if (!updatingBase.value) {
    showBaseEditDialog.value = false
  }
}

function goBack() {
  void router.push({ name: 'glossary' })
}

function getExportFormatLabel(format: ExportFormat) {
  return format === 'xlsx' ? 'Excel' : 'TMX'
}

function getExportFilename(format: ExportFormat) {
  if (!glossaryBase.value) {
    return `glossary.${format}`
  }
  return `${glossaryBase.value.name}-glossary.${format}`
}

function clearExportPollTimer() {
  if (exportPollTimer) {
    window.clearTimeout(exportPollTimer)
    exportPollTimer = null
  }
}

function waitForExportPoll(ms: number) {
  return new Promise<void>((resolve) => {
    clearExportPollTimer()
    exportPollTimer = window.setTimeout(() => {
      exportPollTimer = null
      resolve()
    }, ms)
  })
}

async function loadBase() {
  loadingBase.value = true
  try {
    const { data } = await http.get<GlossaryBase>(`/glossary-bases/${props.id}`)
    glossaryBase.value = data
    pageError.value = ''
  } catch (error) {
    glossaryBase.value = null
    pageError.value = getErrorMessage(error, '词汇表详情加载失败。')
  } finally {
    loadingBase.value = false
  }
}

async function loadEntries() {
  loadingEntries.value = true
  try {
    const { data } = await http.get<PaginatedResponse<GlossaryEntryRecord>>(
      `/glossary-bases/${props.id}/entries`,
      {
        params: {
          skip: (currentPage.value - 1) * pageSize.value,
          limit: pageSize.value,
          search: searchText.value.trim() || undefined,
          case_sensitive: matchCase.value || undefined,
          sort_by: entrySortKey.value || undefined,
          sort_order: entrySortOrder.value,
        },
      },
    )
    entries.value = data.items
    totalEntries.value = data.total
    entryMessage.value = ''
  } catch (error) {
    entryMessage.value = getErrorMessage(error, '词条加载失败。')
  } finally {
    loadingEntries.value = false
  }
}

async function reloadPage() {
  await Promise.all([loadBase(), loadEntries()])
}

async function updateBaseInfo() {
  if (!glossaryBase.value || !canManageResources.value) {
    return
  }
  const name = editBaseName.value.trim()
  if (!name) {
    entryMessage.value = '词汇表名称不能为空。'
    return
  }
  if (!editBaseSourceLanguage.value || !editBaseTargetLanguage.value) {
    entryMessage.value = '请先选择源语言和目标语言。'
    return
  }
  if (editBaseSourceLanguage.value === editBaseTargetLanguage.value) {
    entryMessage.value = '源语言和目标语言不能相同。'
    return
  }

  updatingBase.value = true
  entryMessage.value = ''
  try {
    await http.put<GlossaryBase>(`/glossary-bases/${props.id}`, {
      name,
      description: editBaseDescription.value.trim() || null,
      source_language: editBaseSourceLanguage.value,
      target_language: editBaseTargetLanguage.value,
    })
    showBaseEditDialog.value = false
    await reloadPage()
    entryMessage.value = '词汇表信息已更新。'
  } catch (error) {
    entryMessage.value = getErrorMessage(error, '词汇表信息更新失败。')
  } finally {
    updatingBase.value = false
  }
}

async function createEntry() {
  if (!glossaryBase.value || !canManageResources.value) {
    return
  }
  const sourceText = newSourceText.value.trim()
  const targetText = newTargetText.value.trim()
  if (!sourceText || !targetText) {
    entryMessage.value = '原文和译文不能为空。'
    return
  }

  creatingEntry.value = true
  entryMessage.value = ''
  try {
    await http.post<GlossaryEntryRecord>(`/glossary-bases/${props.id}/entries`, {
      source_text: sourceText,
      target_text: targetText,
      note: newNote.value.trim() || null,
    })
    showAddDialog.value = false
    resetAddForm()
    currentPage.value = 1
    await reloadPage()
    entryMessage.value = '词条已添加。'
  } catch (error) {
    entryMessage.value = getErrorMessage(error, '词条添加失败。')
  } finally {
    creatingEntry.value = false
  }
}

function startEditEntry(entry: GlossaryEntryRecord) {
  if (!canManageResources.value) {
    entryMessage.value = '当前账号只能查看和导出词汇表。'
    return
  }
  editingEntryId.value = entry.id
  editSourceText.value = entry.source_text
  editTargetText.value = entry.target_text
  editNote.value = entry.note || ''
  entryMessage.value = ''
}

function isEditingEntry(entry: GlossaryEntryRecord) {
  return editingEntryId.value === entry.id
}

async function updateEntry(entry: GlossaryEntryRecord) {
  if (!canManageResources.value) {
    entryMessage.value = '当前账号只能查看和导出词汇表。'
    return
  }
  const sourceText = editSourceText.value.trim()
  const targetText = editTargetText.value.trim()
  if (!sourceText || !targetText) {
    entryMessage.value = '原文和译文不能为空。'
    return
  }

  updatingEntryId.value = entry.id
  entryMessage.value = ''
  try {
    await http.put<GlossaryEntryRecord>(`/glossary-entries/${entry.id}`, {
      source_text: sourceText,
      target_text: targetText,
      note: editNote.value.trim() || null,
    })
    resetEditForm()
    await reloadPage()
    entryMessage.value = '词条已更新。'
  } catch (error) {
    entryMessage.value = getErrorMessage(error, '词条更新失败。')
  } finally {
    updatingEntryId.value = ''
  }
}

async function deleteEntry(entry: GlossaryEntryRecord) {
  if (!canManageResources.value) {
    entryMessage.value = '当前账号只能查看和导出词汇表。'
    return
  }
  const confirmed = await confirm({
    title: '删除词条',
    message: '确定删除这条词汇表词条吗？此操作不可恢复。',
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }

  deletingEntryId.value = entry.id
  entryMessage.value = ''
  try {
    await http.delete(`/glossary-entries/${entry.id}`)
    if (editingEntryId.value === entry.id) {
      resetEditForm()
    }
    const remainingTotal = Math.max(totalEntries.value - 1, 0)
    const maxPage = Math.max(Math.ceil(remainingTotal / pageSize.value), 1)
    if (currentPage.value > maxPage) {
      currentPage.value = maxPage
    }
    await reloadPage()
    entryMessage.value = '词条已删除。'
  } catch (error) {
    entryMessage.value = getErrorMessage(error, '词条删除失败。')
  } finally {
    deletingEntryId.value = ''
  }
}

function onImportFileChange(event: Event) {
  selectedImportFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  if (selectedImportFile.value && !glossaryBase.value?.name) {
    entryMessage.value = fileBaseName(selectedImportFile.value)
  }
}

async function uploadGlossaryWorkbook() {
  if (!glossaryBase.value) {
    return
  }
  if (!selectedImportFile.value) {
    importMessage.value = '请先选择要导入的 Excel 文件。'
    importSummary.value = null
    return
  }

  importing.value = true
  importUploadPercent.value = 0
  importMessage.value = ''
  importSummary.value = null

  try {
    const formData = new FormData()
    formData.append('file', selectedImportFile.value)
    formData.append('glossary_base_id', glossaryBase.value.id)
    formData.append('source_language', glossaryBase.value.source_language)
    formData.append('target_language', glossaryBase.value.target_language)

    const { data } = await http.post<GlossaryImportSummary>('/glossary-bases/import-xlsx', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        importUploadPercent.value = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
      },
    })
    importSummary.value = data
    importMessage.value = `导入完成：${data.filename}`
    refreshGlobalNotifications()
    selectedImportFile.value = null
    if (importFileInput.value) {
      importFileInput.value.value = ''
    }
    currentPage.value = 1
    await reloadPage()
  } catch (error) {
    importMessage.value = getErrorMessage(error, '词汇表导入失败。')
  } finally {
    importing.value = false
    importUploadPercent.value = 0
  }
}

async function waitForExportTask(task: ResourceExportTask) {
  let currentTask = task
  while (!disposed) {
    exportProgress.value = currentTask.progress
    entryMessage.value = currentTask.message || '导出任务处理中。'

    if (currentTask.status === 'completed') {
      return currentTask
    }
    if (currentTask.status === 'failed') {
      throw new Error(currentTask.error || currentTask.message || '导出失败。')
    }

    await waitForExportPoll(1200)
    const { data } = await http.get<ResourceExportTask>(`/glossary-bases/export-tasks/${currentTask.task_id}`)
    currentTask = data
  }
  throw new Error('导出任务已取消。')
}

async function exportEntries(format: ExportFormat) {
  if (!glossaryBase.value) {
    return
  }

  showExportMenu.value = false
  exportingEntries.value = true
  exportProgress.value = 0
  entryMessage.value = ''
  try {
    const formatLabel = getExportFormatLabel(format)
    const { data: task } = await http.post<ResourceExportTask>(
      `/glossary-bases/${glossaryBase.value.id}/exports`,
      null,
      { params: { format } },
    )
    entryMessage.value = `${formatLabel} 导出任务已提交。`
    const completedTask = await waitForExportTask(task)
    const response = await http.get(`/glossary-bases/export-tasks/${completedTask.task_id}/download`, {
      responseType: 'blob',
    })
    const filename = resolveDownloadFilename(
      response.headers['content-disposition'],
      getExportFilename(format),
    )
    downloadBlob(response.data, filename)
    entryMessage.value = `词汇表已导出为 ${formatLabel}。`
  } catch (error) {
    entryMessage.value = getErrorMessage(error, '词汇表导出失败。')
  } finally {
    clearExportPollTimer()
    exportingEntries.value = false
    exportProgress.value = 0
  }
}

function scheduleEntriesReload() {
  if (searchTimer) {
    window.clearTimeout(searchTimer)
  }
  searchTimer = window.setTimeout(() => {
    currentPage.value = 1
    void loadEntries()
  }, 250)
}

function toggleEntrySort(key: string) {
  if (entrySortKey.value === key) {
    entrySortOrder.value = entrySortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    entrySortKey.value = key
    entrySortOrder.value = 'asc'
  }
  currentPage.value = 1
  void loadEntries()
}

function getEntrySortArrow(key: string) {
  if (entrySortKey.value !== key) {
    return '↕'
  }
  return entrySortOrder.value === 'asc' ? '↑' : '↓'
}

watch([currentPage, pageSize, matchCase], () => {
  void loadEntries()
})

watch(searchText, () => {
  scheduleEntriesReload()
})

watch(() => props.id, () => {
  currentPage.value = 1
  showAddDialog.value = false
  showBaseEditDialog.value = false
  showImportDialog.value = false
  entrySortKey.value = 'updated_at'
  entrySortOrder.value = 'desc'
  resetAddForm()
  resetEditForm()
  void reloadPage()
})

onMounted(() => {
  disposed = false
  void reloadPage()
})

onUnmounted(() => {
  disposed = true
  if (searchTimer) {
    window.clearTimeout(searchTimer)
  }
  clearExportPollTimer()
})
</script>

<template>
  <div class="glossary-detail-page">
    <section class="glossary-detail-topbar">
      <div class="glossary-detail-topbar__identity">
        <button class="glossary-detail-back" type="button" @click="goBack">
          <ArrowLeft :size="14" />
          返回词汇表
        </button>
        <span class="glossary-detail-topbar__divider" aria-hidden="true" />
        <strong class="glossary-detail-topbar__title">
          词汇表名称：{{ glossaryBase?.name || '词汇表详情' }}
        </strong>
      </div>

      <div class="glossary-detail-topbar__actions">
        <button
          v-if="canManageResources"
          class="glossary-detail-button"
          type="button"
          :disabled="!glossaryBase"
          @click="openBaseEditDialog"
        >
          <Pencil :size="14" />
          编辑信息
        </button>
        <button
          v-if="canManageResources"
          class="glossary-detail-button glossary-detail-button--primary"
          type="button"
          :disabled="!glossaryBase"
          @click="showImportDialog = true"
        >
          <Upload :size="14" />
          导入
        </button>
        <div class="glossary-detail-export">
          <button
            class="glossary-detail-button"
            type="button"
            :disabled="!glossaryBase || exportingEntries"
            @click="showExportMenu = !showExportMenu"
          >
            <Loader2 v-if="exportingEntries" class="lucide-spin" :size="14" />
            <Download v-else :size="14" />
            {{ exportButtonText }}
            <ChevronDown :size="14" />
          </button>
          <div v-if="showExportMenu && !exportingEntries" class="glossary-detail-export__menu">
            <button type="button" @click="exportEntries('xlsx')">
              <FileSpreadsheet :size="14" />
              Excel
            </button>
            <button type="button" @click="exportEntries('tmx')">
              <FileCode2 :size="14" />
              TMX
            </button>
          </div>
        </div>
      </div>
    </section>

    <p v-if="pageError" class="form-message is-error glossary-detail-message">{{ pageError }}</p>

    <section v-if="loadingBase && !glossaryBase" class="glossary-detail-loading">
      <Loader2 class="lucide-spin" :size="26" />
      页面加载中...
    </section>

    <template v-else-if="glossaryBase">
      <section class="glossary-detail-meta">
        <div class="glossary-detail-meta__item">
          <span>语言对</span>
          <strong>{{ languagePairLabel }}</strong>
        </div>
        <div class="glossary-detail-meta__item">
          <span>词条数量</span>
          <strong>{{ glossaryBase.entry_count }}</strong>
        </div>
        <div class="glossary-detail-meta__item">
          <span>创建时间</span>
          <strong>{{ formatDate(glossaryBase.created_at) }}</strong>
        </div>
        <div class="glossary-detail-meta__item">
          <span>更新时间</span>
          <strong>{{ formatDate(glossaryBase.updated_at) }}</strong>
        </div>
        <div class="glossary-detail-meta__item glossary-detail-meta__item--wide">
          <span>说明</span>
          <strong>{{ glossaryBase.description || '-' }}</strong>
        </div>
      </section>

      <section class="glossary-detail-content">
        <div class="glossary-detail-toolbar">
          <div class="glossary-detail-toolbar__left">
            <button
              v-if="canManageResources"
              class="glossary-detail-button glossary-detail-button--primary"
              type="button"
              @click="openAddDialog"
            >
              <Plus :size="14" />
              新增词条
            </button>

            <div class="glossary-detail-search">
              <Search :size="14" />
              <input
                v-model="searchText"
                type="text"
                placeholder="搜索原文、译文或备注"
              />
            </div>

            <label class="glossary-detail-checkbox">
              <input v-model="matchCase" type="checkbox" />
              匹配大小写
            </label>
          </div>
        </div>

        <p
          v-if="entryMessage"
          class="form-message glossary-detail-message"
          :class="{ 'is-error': entryMessage.includes('失败') || entryMessage.includes('不能为空') }"
        >
          {{ entryMessage }}
        </p>

        <div class="glossary-detail-table-wrap">
          <table class="glossary-detail-table">
            <thead>
              <tr>
                <th class="glossary-detail-table__index">序号</th>
                <th>
                  <button class="glossary-detail-th-button" type="button" @click="toggleEntrySort('source_text')">
                    <span>原文</span>
                    <span>{{ getEntrySortArrow('source_text') }}</span>
                  </button>
                </th>
                <th>
                  <button class="glossary-detail-th-button" type="button" @click="toggleEntrySort('target_text')">
                    <span>译文</span>
                    <span>{{ getEntrySortArrow('target_text') }}</span>
                  </button>
                </th>
                <th>
                  <button class="glossary-detail-th-button" type="button" @click="toggleEntrySort('note')">
                    <span>备注</span>
                    <span>{{ getEntrySortArrow('note') }}</span>
                  </button>
                </th>
                <th class="glossary-detail-table__meta">
                  <button class="glossary-detail-th-button" type="button" @click="toggleEntrySort('creator_name')">
                    <span>创建人</span>
                    <span>{{ getEntrySortArrow('creator_name') }}</span>
                  </button>
                </th>
                <th class="glossary-detail-table__datetime">
                  <button class="glossary-detail-th-button" type="button" @click="toggleEntrySort('created_at')">
                    <span>创建时间</span>
                    <span>{{ getEntrySortArrow('created_at') }}</span>
                  </button>
                </th>
                <th class="glossary-detail-table__meta">
                  <button class="glossary-detail-th-button" type="button" @click="toggleEntrySort('last_modified_by_name')">
                    <span>最后修改人</span>
                    <span>{{ getEntrySortArrow('last_modified_by_name') }}</span>
                  </button>
                </th>
                <th class="glossary-detail-table__datetime">
                  <button class="glossary-detail-th-button" type="button" @click="toggleEntrySort('updated_at')">
                    <span>最新修改时间</span>
                    <span>{{ getEntrySortArrow('updated_at') }}</span>
                  </button>
                </th>
                <th v-if="canManageResources" class="glossary-detail-table__actions">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="loadingEntries">
                <td :colspan="tableColumnCount" class="glossary-detail-table__empty">
                  <Loader2 class="lucide-spin" :size="18" />
                  加载中...
                </td>
              </tr>
              <tr v-else-if="entries.length === 0">
                <td :colspan="tableColumnCount" class="glossary-detail-table__empty">
                  暂无词条
                </td>
              </tr>
              <tr v-for="(entry, entryIndex) in entries" v-else :key="entry.id">
                <td class="glossary-detail-table__index">
                  {{ indexOffset + entryIndex + 1 }}
                </td>
                <td>
                  <textarea
                    v-if="isEditingEntry(entry)"
                    v-model="editSourceText"
                    class="glossary-detail-table__textarea"
                    rows="3"
                    aria-label="编辑原文"
                  />
                  <span v-else>{{ entry.source_text }}</span>
                </td>
                <td>
                  <textarea
                    v-if="isEditingEntry(entry)"
                    v-model="editTargetText"
                    class="glossary-detail-table__textarea"
                    rows="3"
                    aria-label="编辑译文"
                  />
                  <span v-else>{{ entry.target_text }}</span>
                </td>
                <td>
                  <textarea
                    v-if="isEditingEntry(entry)"
                    v-model="editNote"
                    class="glossary-detail-table__textarea"
                    rows="3"
                    aria-label="编辑备注"
                  />
                  <span v-else class="glossary-detail-note">{{ entry.note || '-' }}</span>
                </td>
                <td class="glossary-detail-table__meta">
                  {{ entry.creator_name || '-' }}
                </td>
                <td class="glossary-detail-table__datetime">
                  {{ formatDate(entry.created_at) }}
                </td>
                <td class="glossary-detail-table__meta">
                  {{ entry.last_modified_by_name || '-' }}
                </td>
                <td class="glossary-detail-table__datetime">
                  {{ formatDate(entry.updated_at) }}
                </td>
                <td v-if="canManageResources" class="glossary-detail-table__actions">
                  <div v-if="isEditingEntry(entry)" class="glossary-detail-row-actions">
                    <button
                      class="glossary-detail-action-button"
                      type="button"
                      title="保存"
                      :disabled="updatingEntryId === entry.id"
                      @click="updateEntry(entry)"
                    >
                      <Loader2 v-if="updatingEntryId === entry.id" class="lucide-spin" :size="14" />
                      <Save v-else :size="14" />
                    </button>
                    <button
                      class="glossary-detail-action-button"
                      type="button"
                      title="取消"
                      :disabled="updatingEntryId === entry.id"
                      @click="resetEditForm"
                    >
                      <X :size="14" />
                    </button>
                  </div>
                  <div v-else class="glossary-detail-row-actions">
                    <button
                      class="glossary-detail-action-button"
                      type="button"
                      title="编辑"
                      :disabled="Boolean(editingEntryId) || deletingEntryId === entry.id"
                      @click="startEditEntry(entry)"
                    >
                      <Pencil :size="14" />
                    </button>
                    <button
                      class="glossary-detail-action-button glossary-detail-action-button--danger"
                      type="button"
                      title="删除"
                      :disabled="Boolean(editingEntryId) || deletingEntryId === entry.id"
                      @click="deleteEntry(entry)"
                    >
                      <Loader2 v-if="deletingEntryId === entry.id" class="lucide-spin" :size="14" />
                      <Trash2 v-else :size="14" />
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <Pagination
          v-if="totalEntries > pageSize"
          :total="totalEntries"
          :page="currentPage"
          :page-size="pageSize"
          :page-sizes="[20, 50, 100, 200]"
          @update:page="currentPage = $event"
          @update:page-size="pageSize = $event"
        />
      </section>
    </template>

    <Modal
      v-if="canManageResources"
      :open="showAddDialog"
      title="新增词汇表词条"
      description="备注用于限定使用场景，AI 预翻译会用它判断是否适用。"
      width="min(860px, calc(100vw - 32px))"
      @close="showAddDialog = false"
    >
      <div class="glossary-detail-add-form">
        <label class="field">
          <span class="field__label">原文</span>
          <textarea
            v-model="newSourceText"
            class="field__control field__control--multi"
            rows="6"
            placeholder="请输入原文"
          />
        </label>

        <label class="field">
          <span class="field__label">译文</span>
          <textarea
            v-model="newTargetText"
            class="field__control field__control--multi"
            rows="6"
            placeholder="请输入译文"
          />
        </label>

        <label class="field field--full">
          <span class="field__label">备注</span>
          <textarea
            v-model="newNote"
            class="field__control field__control--multi"
            rows="4"
            placeholder="可选，例如：仅用于法律合同场景"
          />
        </label>
      </div>

      <template #footer>
        <button class="button" type="button" :disabled="creatingEntry" @click="showAddDialog = false">
          取消
        </button>
        <button class="button button--primary" type="button" :disabled="creatingEntry" @click="createEntry">
          <Loader2 v-if="creatingEntry" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ creatingEntry ? '添加中...' : '确认添加' }}
        </button>
      </template>
    </Modal>

    <Modal
      v-if="canManageResources"
      :open="showBaseEditDialog"
      title="编辑词汇表信息"
      description="修改语言对会同步更新当前词汇表下的词条语言对。"
      width="min(640px, calc(100vw - 32px))"
      @close="closeBaseEditDialog"
    >
      <div class="upload-form form-grid-2">
        <label class="field">
          <span class="field__label">词汇表名称</span>
          <input v-model="editBaseName" class="field__control" type="text" />
        </label>
        <label class="field">
          <span class="field__label">说明</span>
          <input v-model="editBaseDescription" class="field__control" type="text" placeholder="可选" />
        </label>
        <label class="field">
          <span class="field__label">源语言</span>
          <select v-model="editBaseSourceLanguage" class="field__control">
            <option value="">请选择</option>
            <option v-for="option in languageOptions" :key="option.code" :value="option.code">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="field">
          <span class="field__label">目标语言</span>
          <select v-model="editBaseTargetLanguage" class="field__control">
            <option value="">请选择</option>
            <option v-for="option in languageOptions" :key="option.code" :value="option.code">
              {{ option.label }}
            </option>
          </select>
        </label>
      </div>

      <template #footer>
        <button class="button" type="button" :disabled="updatingBase" @click="closeBaseEditDialog">取消</button>
        <button class="button button--primary" type="button" :disabled="updatingBase" @click="updateBaseInfo">
          <Loader2 v-if="updatingBase" class="lucide-spin" :size="14" />
          <Save v-else :size="14" />
          {{ updatingBase ? '保存中...' : '保存' }}
        </button>
      </template>
    </Modal>

    <Modal
      v-if="canManageResources"
      :open="showImportDialog"
      title="导入词汇表"
      description="支持三列 Excel：原文、译文、备注。备注列也可命名为说明、补充解释、note、comment 或 context。"
      width="min(760px, calc(100vw - 32px))"
      @close="showImportDialog = false"
    >
      <div class="glossary-import-form">
        <label class="field">
          <span class="field__label">Excel 文件</span>
          <input
            ref="importFileInput"
            class="field__control"
            type="file"
            accept=".xlsx"
            @change="onImportFileChange"
          />
        </label>
        <div class="glossary-import-target">
          <span class="tag">{{ glossaryBase?.name }}</span>
          <span class="tag">{{ languagePairLabel }}</span>
        </div>
      </div>

      <div v-if="importing" class="glossary-import-progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div class="progress-bar__fill" :style="{ width: `${importUploadPercent}%` }" />
          </div>
          <span class="progress-bar__text">{{ importUploadPercent }}%</span>
        </div>
      </div>

      <p
        v-if="importMessage"
        class="form-message glossary-detail-message"
        :class="{ 'is-error': !importSummary }"
      >
        {{ importMessage }}
      </p>

      <div v-if="importSummary" class="glossary-import-summary">
        <div class="section-title">导入结果</div>
        <div class="summary-grid summary-grid--wide">
          <div class="summary-item">
            <strong>{{ importSummary.glossary_base_name }}</strong>
            <span>目标词汇表</span>
          </div>
          <div class="summary-item">
            <strong>{{ formatLanguagePair(importSummary.source_language, importSummary.target_language) }}</strong>
            <span>语言对</span>
          </div>
          <div class="summary-item">
            <strong>{{ importSummary.imported_rows }}</strong>
            <span>写入总行数</span>
          </div>
          <div class="summary-item">
            <strong>{{ importSummary.created_rows }}</strong>
            <span>新增</span>
          </div>
          <div class="summary-item">
            <strong>{{ importSummary.updated_rows }}</strong>
            <span>更新</span>
          </div>
          <div class="summary-item">
            <strong>{{ importSummary.skipped_header_rows }}</strong>
            <span>跳过表头</span>
          </div>
          <div class="summary-item">
            <strong>{{ importSummary.skipped_empty_rows }}</strong>
            <span>跳过空行</span>
          </div>
        </div>
      </div>

      <template #footer>
        <button class="button" type="button" :disabled="importing" @click="showImportDialog = false">关闭</button>
        <button class="button button--primary" type="button" :disabled="importing" @click="uploadGlossaryWorkbook">
          <Loader2 v-if="importing" class="lucide-spin" :size="14" />
          <Upload v-else :size="14" />
          {{ importing ? `导入中... ${importUploadPercent}%` : '导入词汇表' }}
        </button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.glossary-detail-page {
  min-height: calc(100vh - 96px);
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  color: var(--text-primary);
  overflow: hidden;
}

.glossary-detail-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 58px;
  padding: 0 20px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--surface-0);
}

.glossary-detail-topbar__identity,
.glossary-detail-topbar__actions,
.glossary-detail-toolbar,
.glossary-detail-toolbar__left,
.glossary-detail-row-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.glossary-detail-topbar__divider {
  width: 1px;
  height: 24px;
  background: var(--line-soft);
}

.glossary-detail-topbar__title {
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.glossary-detail-back,
.glossary-detail-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 32px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--button-bg);
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1;
  box-shadow: none;
}

.glossary-detail-back {
  padding: 0;
  border-color: transparent;
  background: transparent;
  color: var(--text-secondary);
}

.glossary-detail-button {
  padding: 0 14px;
}

.glossary-detail-button--primary {
  border-color: var(--brand-700);
  background: var(--brand-700);
  color: #ffffff;
}

.glossary-detail-back:hover,
.glossary-detail-button:hover:not(:disabled) {
  border-color: var(--brand-700);
  background: var(--brand-050);
  color: var(--brand-700);
}

.glossary-detail-button--primary:hover:not(:disabled) {
  border-color: #0b6b5b;
  background: #0b6b5b;
  color: #ffffff;
}

.glossary-detail-button:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.glossary-detail-export {
  position: relative;
}

.glossary-detail-export__menu {
  display: grid;
  gap: 4px;
  position: absolute;
  z-index: 6;
  top: calc(100% + 8px);
  right: 0;
  min-width: 132px;
  padding: 6px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-panel);
  box-shadow: var(--shadow-soft);
}

.glossary-detail-export__menu button {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  text-align: left;
}

.glossary-detail-meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--surface-1);
}

.glossary-detail-meta__item {
  display: grid;
  gap: 5px;
  min-width: 0;
  color: var(--text-primary);
  font-size: 13px;
}

.glossary-detail-meta__item--wide {
  grid-column: 1 / -1;
}

.glossary-detail-meta__item span {
  color: var(--text-muted);
}

.glossary-detail-meta__item strong {
  min-width: 0;
  overflow-wrap: anywhere;
  font-weight: 500;
}

.glossary-detail-content {
  padding: 16px 20px 22px;
}

.glossary-detail-toolbar {
  justify-content: space-between;
  margin-bottom: 10px;
}

.glossary-detail-search {
  display: flex;
  align-items: center;
  position: relative;
  width: 260px;
  min-width: 180px;
  color: #98a2b3;
}

.glossary-detail-search svg {
  position: absolute;
  left: 12px;
  pointer-events: none;
}

.glossary-detail-search input {
  width: 100%;
  height: 32px;
  padding: 0 10px 0 32px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--control-bg);
  color: var(--text-primary);
  font-size: 13px;
}

.glossary-detail-checkbox {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  white-space: nowrap;
}

.glossary-detail-checkbox input {
  width: 14px;
  height: 14px;
  accent-color: var(--brand-700);
}

.glossary-detail-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-0);
}

.glossary-detail-table {
  width: 100%;
  min-width: 1240px;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 13px;
}

.glossary-detail-table th {
  height: 36px;
  padding: 0 10px;
  border-right: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
  background: var(--brand-050);
  color: var(--text-secondary);
  font-weight: 600;
  text-align: left;
  vertical-align: middle;
}

.glossary-detail-th-button {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  width: 100%;
  min-height: 18px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  line-height: 1.2;
  text-align: left;
}

.glossary-detail-th-button span:first-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.glossary-detail-table th:last-child,
.glossary-detail-table td:last-child {
  border-right: none;
}

.glossary-detail-table td {
  min-height: 44px;
  padding: 12px 10px;
  border-right: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
  color: var(--text-primary);
  vertical-align: top;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.glossary-detail-table__index {
  width: 56px;
  text-align: center !important;
}

.glossary-detail-table__meta {
  width: 112px;
}

.glossary-detail-table__datetime {
  width: 152px;
}

.glossary-detail-table__actions {
  width: 96px;
  text-align: center !important;
  vertical-align: middle !important;
}

.glossary-detail-table__empty {
  height: 60px;
  color: var(--text-muted);
  text-align: center !important;
  vertical-align: middle !important;
}

.glossary-detail-table__empty svg {
  margin-right: 8px;
  vertical-align: middle;
}

.glossary-detail-table__textarea {
  width: 100%;
  min-height: 74px;
  padding: 8px 10px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--control-bg);
  color: var(--text-primary);
  font: inherit;
  line-height: 1.5;
  resize: vertical;
}

.glossary-detail-note {
  color: var(--text-secondary);
}

.glossary-detail-row-actions {
  justify-content: center;
  gap: 4px;
}

.glossary-detail-action-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
}

.glossary-detail-action-button:not(:disabled):hover {
  border-color: var(--line-strong);
  background: var(--brand-050);
  color: var(--brand-700);
}

.glossary-detail-action-button--danger {
  color: var(--state-danger);
}

.glossary-detail-action-button--danger:not(:disabled):hover {
  border-color: rgba(194, 59, 63, 0.24);
  background: var(--state-danger-bg);
  color: var(--state-danger);
}

.glossary-detail-action-button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.glossary-detail-add-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
  gap: 16px;
}

.glossary-detail-add-form .field--full {
  grid-column: 1 / -1;
}

.glossary-import-form,
.glossary-import-summary {
  display: grid;
  gap: 12px;
}

.glossary-import-target {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.glossary-import-progress {
  margin-top: 14px;
}

.glossary-detail-message {
  margin: 0 0 10px;
}

.glossary-detail-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 240px;
  color: var(--text-muted);
}

@media (max-width: 900px) {
  .glossary-detail-topbar,
  .glossary-detail-toolbar,
  .glossary-detail-topbar__actions {
    align-items: stretch;
    flex-direction: column;
  }

  .glossary-detail-topbar {
    padding: 12px 16px;
  }

  .glossary-detail-meta {
    grid-template-columns: 1fr;
    padding: 14px 16px;
  }

  .glossary-detail-content {
    padding: 14px 16px 24px;
  }

  .glossary-detail-toolbar__left {
    align-items: stretch;
    flex-direction: column;
  }

  .glossary-detail-search,
  .glossary-detail-export,
  .glossary-detail-export .glossary-detail-button {
    width: 100%;
  }

  .glossary-detail-add-form {
    grid-template-columns: 1fr;
  }
}
</style>
