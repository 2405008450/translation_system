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
  TableProperties,
  Trash2,
  Upload,
  X,
} from 'lucide-vue-next'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import Modal from '../components/base/Modal.vue'
import Pagination from '../components/Pagination.vue'
import ResourceImportDialog from '../components/ResourceImportDialog.vue'
import { useConfirm } from '../composables/useConfirm'
import { usePageHeader } from '../composables/usePageHeader'
import { getLanguageLabel } from '../constants/languages'
import { useAuthStore } from '../stores/auth'
import type { PaginatedResponse, TermBase, TermEntryRecord, TMCollection, TMEntryRecord } from '../types/api'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'

type ResourceMode = 'tm' | 'term'
type ExportFormat = 'xlsx' | 'tmx' | 'tbx'
type ResourceRecord = TMCollection | TermBase
type EntryRecord = TMEntryRecord | TermEntryRecord

interface ResourceExportTask {
  task_id: string
  resource_type: ResourceMode
  resource_id: string
  format: ExportFormat
  status: 'queued' | 'running' | 'completed' | 'failed' | 'canceling' | 'canceled'
  progress: number
  message: string
  result: {
    filename: string
    size_bytes: number
    total_entries: number
  } | null
  error: string | null
  created_at: string
  updated_at: string
}

const props = defineProps<{
  id: string
  mode: ResourceMode
}>()

const router = useRouter()
const confirm = useConfirm()
const authStore = useAuthStore()

const resource = ref<ResourceRecord | null>(null)
const entries = ref<EntryRecord[]>([])
const loadingResource = ref(false)
const loadingEntries = ref(false)
const creatingEntry = ref(false)
const updatingEntryId = ref('')
const deletingEntryId = ref('')
const exportingEntries = ref(false)
const pageError = ref('')
const entryMessage = ref('')

const searchText = ref('')
const matchCase = ref(false)
const currentPage = ref(1)
const pageSize = ref(50)
const totalEntries = ref(0)
const showAddDialog = ref(false)
const showImportDialog = ref(false)
const showFieldMenu = ref(false)
const showExportMenu = ref(false)
const showIndexColumn = ref(true)
const showSourceColumn = ref(true)
const showTargetColumn = ref(true)
const showCreatorColumn = ref(true)
const showCreatedAtColumn = ref(true)
const showLastModifiedByColumn = ref(true)
const showUpdatedAtColumn = ref(true)
const exportProgress = ref(0)
const currentExportTaskId = ref('')
const newSourceText = ref('')
const newTargetText = ref('')
const editingEntryId = ref('')
const editSourceText = ref('')
const editTargetText = ref('')
let searchTimer: number | null = null
let exportPollTimer: number | null = null
let disposed = false

const copy = computed(() => {
  if (props.mode === 'tm') {
    return {
      backRoute: 'tm',
      backLabel: '返回记忆库',
      titlePrefix: '记忆库名称：',
      fallbackTitle: '记忆库详情',
      detailTitle: '记忆库详情',
      description: '维护记忆库信息、双语条目和导入导出操作。',
      assetLabel: '记忆库',
      entryName: '记录',
      entryCountLabel: '条目数',
      addTitle: '添加记忆库条目',
      addDescription: '填写原文和译文后，会写入当前记忆库。',
      sourceLabel: '原文',
      targetLabel: '译文',
      searchPlaceholder: '输入记忆库内容搜索',
      emptyText: '暂无数据',
      importTab: 'tm' as const,
      importTitle: '导入记忆库',
      exportFilenameSuffix: 'tm',
      detailEndpoint: `/translation-memory/collections/${props.id}`,
      entriesEndpoint: `/translation-memory/collections/${props.id}/entries`,
      createEndpoint: `/translation-memory/collections/${props.id}/entries`,
      createExportEndpoint: `/translation-memory/collections/${props.id}/exports`,
      exportTaskEndpoint: (taskId: string) => `/translation-memory/export-tasks/${taskId}`,
      exportCancelEndpoint: (taskId: string) => `/translation-memory/export-tasks/${taskId}/cancel`,
      exportDownloadEndpoint: (taskId: string) => `/translation-memory/export-tasks/${taskId}/download`,
      updateEntryEndpoint: (entryId: string) => `/translation-memory/entries/${entryId}`,
      deleteEntryEndpoint: (entryId: string) => `/translation-memory/entries/${entryId}`,
    }
  }

  return {
    backRoute: 'term-base',
    backLabel: '返回术语库',
    titlePrefix: '术语库名称：',
    fallbackTitle: '术语库详情',
    detailTitle: '术语库详情',
    description: '维护术语库信息、术语条目和导入导出操作。',
    assetLabel: '术语库',
    entryName: '术语',
    entryCountLabel: '条目数',
    addTitle: '添加术语条目',
    addDescription: '填写术语原文和译文后，会写入当前术语库。',
    sourceLabel: '术语原文',
    targetLabel: '术语译文',
    searchPlaceholder: '输入术语库内容搜索',
    emptyText: '暂无数据',
    importTab: 'term' as const,
    importTitle: '导入术语库',
    exportFilenameSuffix: 'term-base',
    detailEndpoint: `/term-bases/${props.id}`,
    entriesEndpoint: `/term-bases/${props.id}/entries`,
    createEndpoint: `/term-bases/${props.id}/entries`,
    createExportEndpoint: `/term-bases/${props.id}/exports`,
    exportTaskEndpoint: (taskId: string) => `/term-bases/export-tasks/${taskId}`,
    exportCancelEndpoint: (taskId: string) => `/term-bases/export-tasks/${taskId}/cancel`,
    exportDownloadEndpoint: (taskId: string) => `/term-bases/export-tasks/${taskId}/download`,
    updateEntryEndpoint: (entryId: string) => `/term-entries/${entryId}`,
    deleteEntryEndpoint: (entryId: string) => `/term-entries/${entryId}`,
  }
})

const sourceLanguageCode = computed(() => resource.value?.source_language || '')
const targetLanguageCode = computed(() => resource.value?.target_language || '')
const sourceLanguageLabel = computed(() => getDetailLanguageLabel(sourceLanguageCode.value))
const targetLanguageLabel = computed(() => getDetailLanguageLabel(targetLanguageCode.value))
const pageTitle = computed(() => resource.value?.name || copy.value.fallbackTitle)
const indexOffset = computed(() => (currentPage.value - 1) * pageSize.value)
const canManageResources = computed(() => authStore.isAdmin)
const tableColumnCount = computed(() => (
  Number(showIndexColumn.value) + Number(showSourceColumn.value) + Number(showTargetColumn.value)
  + Number(showCreatorColumn.value) + Number(showCreatedAtColumn.value)
  + Number(showLastModifiedByColumn.value) + Number(showUpdatedAtColumn.value)
  + Number(canManageResources.value)
))
const entryCount = computed(() => resource.value?.entry_count ?? totalEntries.value)
const entryCountText = computed(() => `${entryCount.value}`)
const lastImportTime = computed(() => entries.value[0]?.updated_at ? formatDate(entries.value[0].updated_at) : '-')
const exportButtonText = computed(() => (
  exportingEntries.value ? `导出中 ${exportProgress.value}%` : '导出'
))

const metaColumns = computed(() => [
  [
    { label: '源语言', value: sourceLanguageLabel.value },
    { label: '创建人', value: '项目专员-' },
    { label: '关联项目', value: '-' },
  ],
  [
    { label: '目标语言', value: targetLanguageLabel.value },
    { label: '最近导入时间', value: lastImportTime.value },
    { label: copy.value.entryCountLabel, value: entryCountText.value },
  ],
  [
    { label: '创建时间', value: resource.value?.created_at ? formatDate(resource.value.created_at) : '-' },
    { label: '访问权限', value: '团队内可见' },
    { label: '分类标签', value: resource.value?.description || '-' },
  ],
])

usePageHeader(() => ({
  title: pageTitle.value,
  description: copy.value.description,
  breadcrumbs: [
    { label: '语言资产' },
    { label: copy.value.assetLabel, to: { name: copy.value.backRoute } },
    { label: pageTitle.value },
  ],
}))

function getDetailLanguageLabel(code: string | null | undefined) {
  if (code === 'zh-CN') {
    return '中文（中国）'
  }
  if (code === 'en-US') {
    return '英语（美国）'
  }
  return getLanguageLabel(code)
}

function formatDate(value: string) {
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

function getExportFormatLabel(format: ExportFormat) {
  return format === 'xlsx' ? 'Excel' : (format === 'tmx' ? 'TMX' : 'TBX')
}

function getExportFilename(format: ExportFormat) {
  if (!resource.value) {
    return `export.${format}`
  }
  return `${resource.value.name}-${copy.value.exportFilenameSuffix}.${format}`
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

function resetAddForm() {
  newSourceText.value = ''
  newTargetText.value = ''
}

function resetEditForm() {
  editingEntryId.value = ''
  editSourceText.value = ''
  editTargetText.value = ''
}

function openAddDialog() {
  if (!canManageResources.value) {
    entryMessage.value = `当前账号只能查看和导出${copy.value.assetLabel}。`
    return
  }
  resetAddForm()
  entryMessage.value = ''
  showAddDialog.value = true
}

function goBack() {
  void router.push({ name: copy.value.backRoute })
}

async function loadResource() {
  loadingResource.value = true
  try {
    const { data } = await http.get<ResourceRecord>(copy.value.detailEndpoint)
    resource.value = data
    pageError.value = ''
  } catch (error) {
    resource.value = null
    pageError.value = getErrorMessage(error, `${copy.value.fallbackTitle}加载失败。`)
  } finally {
    loadingResource.value = false
  }
}

async function loadEntries() {
  loadingEntries.value = true
  try {
    const { data } = await http.get<PaginatedResponse<EntryRecord>>(copy.value.entriesEndpoint, {
      params: {
        skip: (currentPage.value - 1) * pageSize.value,
        limit: pageSize.value,
        search: searchText.value.trim() || undefined,
        case_sensitive: matchCase.value || undefined,
      },
    })
    entries.value = data.items
    totalEntries.value = data.total
    entryMessage.value = ''
  } catch (error) {
    entryMessage.value = getErrorMessage(error, `${copy.value.entryName}加载失败。`)
  } finally {
    loadingEntries.value = false
  }
}

async function reloadPage() {
  await Promise.all([loadResource(), loadEntries()])
}

async function createEntry() {
  if (!resource.value || !canManageResources.value) {
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
    await http.post<EntryRecord>(copy.value.createEndpoint, {
      source_text: sourceText,
      target_text: targetText,
    })
    showAddDialog.value = false
    resetAddForm()
    currentPage.value = 1
    await reloadPage()
    entryMessage.value = `${copy.value.entryName}已添加。`
  } catch (error) {
    entryMessage.value = getErrorMessage(error, `${copy.value.entryName}添加失败。`)
  } finally {
    creatingEntry.value = false
  }
}

function startEditEntry(entry: EntryRecord) {
  if (!canManageResources.value) {
    entryMessage.value = `当前账号只能查看和导出${copy.value.assetLabel}。`
    return
  }
  editingEntryId.value = entry.id
  editSourceText.value = entry.source_text
  editTargetText.value = entry.target_text
  entryMessage.value = ''
}

function isEditingEntry(entry: EntryRecord) {
  return editingEntryId.value === entry.id
}

async function updateEntry(entry: EntryRecord) {
  if (!canManageResources.value) {
    entryMessage.value = `当前账号只能查看和导出${copy.value.assetLabel}。`
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
    await http.put<EntryRecord>(copy.value.updateEntryEndpoint(entry.id), {
      source_text: sourceText,
      target_text: targetText,
    })
    resetEditForm()
    await reloadPage()
    entryMessage.value = `${copy.value.entryName}已更新。`
  } catch (error) {
    entryMessage.value = getErrorMessage(error, `${copy.value.entryName}更新失败。`)
  } finally {
    updatingEntryId.value = ''
  }
}

async function deleteEntry(entry: EntryRecord) {
  if (!canManageResources.value) {
    entryMessage.value = `当前账号只能查看和导出${copy.value.assetLabel}。`
    return
  }
  const confirmed = await confirm({
    title: `删除${copy.value.entryName}`,
    message: `确定删除这条${copy.value.entryName}吗？此操作不可恢复。`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }

  deletingEntryId.value = entry.id
  entryMessage.value = ''
  try {
    await http.delete(copy.value.deleteEntryEndpoint(entry.id))
    if (editingEntryId.value === entry.id) {
      resetEditForm()
    }
    const remainingTotal = Math.max(totalEntries.value - 1, 0)
    const maxPage = Math.max(Math.ceil(remainingTotal / pageSize.value), 1)
    if (currentPage.value > maxPage) {
      currentPage.value = maxPage
    }
    await reloadPage()
    entryMessage.value = `${copy.value.entryName}已删除。`
  } catch (error) {
    entryMessage.value = getErrorMessage(error, `${copy.value.entryName}删除失败。`)
  } finally {
    deletingEntryId.value = ''
  }
}

async function waitForExportTask(task: ResourceExportTask) {
  let currentTask = task
  currentExportTaskId.value = task.task_id
  while (!disposed) {
    exportProgress.value = currentTask.progress
    entryMessage.value = currentTask.message || '导出任务处理中。'

    if (currentTask.status === 'completed') {
      return currentTask
    }
    if (currentTask.status === 'failed') {
      throw new Error(currentTask.error || currentTask.message || '导出失败。')
    }
    if (currentTask.status === 'canceled') {
      throw new DOMException(currentTask.message || '导出已取消。', 'AbortError')
    }

    await waitForExportPoll(1200)
    const { data } = await http.get<ResourceExportTask>(copy.value.exportTaskEndpoint(currentTask.task_id))
    currentTask = data
  }
  throw new Error('导出任务已取消。')
}

async function cancelCurrentExport() {
  if (!currentExportTaskId.value) {
    return
  }
  entryMessage.value = '正在停止导出...'
  await http.post(copy.value.exportCancelEndpoint(currentExportTaskId.value)).catch(() => undefined)
}

async function exportEntries(format: ExportFormat) {
  if (!resource.value) {
    return
  }

  showExportMenu.value = false
  exportingEntries.value = true
  exportProgress.value = 0
  entryMessage.value = ''
  try {
    const formatLabel = getExportFormatLabel(format)
    const { data: task } = await http.post<ResourceExportTask>(copy.value.createExportEndpoint, null, {
      params: { format },
    })
    entryMessage.value = `${formatLabel} 导出任务已提交。`
    const completedTask = await waitForExportTask(task)
    const response = await http.get(copy.value.exportDownloadEndpoint(completedTask.task_id), {
      responseType: 'blob',
    })
    const filename = resolveDownloadFilename(
      response.headers['content-disposition'],
      getExportFilename(format),
    )
    downloadBlob(response.data, filename)
    entryMessage.value = `${copy.value.entryName}已导出为 ${formatLabel}。`
  } catch (error) {
    entryMessage.value = error instanceof DOMException && error.name === 'AbortError'
      ? `${copy.value.entryName}导出已停止。`
      : getErrorMessage(error, `${copy.value.entryName}导出失败。`)
  } finally {
    clearExportPollTimer()
    exportingEntries.value = false
    exportProgress.value = 0
    currentExportTaskId.value = ''
  }
}

async function handleImported(payload: { tab: 'tm' | 'glossary' | 'term' }) {
  if (payload.tab !== copy.value.importTab) {
    return
  }
  showImportDialog.value = false
  currentPage.value = 1
  await reloadPage()
  entryMessage.value = '导入完成，列表已刷新。'
}

watch([currentPage, pageSize, matchCase], () => {
  void loadEntries()
})

watch(searchText, () => {
  if (searchTimer) {
    window.clearTimeout(searchTimer)
  }
  searchTimer = window.setTimeout(() => {
    currentPage.value = 1
    void loadEntries()
  }, 250)
})

watch(() => [props.id, props.mode] as const, () => {
  currentPage.value = 1
  showAddDialog.value = false
  showImportDialog.value = false
  showFieldMenu.value = false
  showExportMenu.value = false
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
  <div class="resource-detail-page">
    <section class="resource-detail-topbar">
      <div class="resource-detail-topbar__identity">
        <button class="resource-detail-back" type="button" @click="goBack">
          <ArrowLeft :size="14" />
          {{ copy.backLabel }}
        </button>
        <span class="resource-detail-topbar__divider" aria-hidden="true" />
        <strong class="resource-detail-topbar__title">
          {{ copy.titlePrefix }}{{ resource?.name || copy.fallbackTitle }}
        </strong>
      </div>

      <div class="resource-detail-topbar__actions">
        <button
          v-if="canManageResources"
          class="resource-detail-button resource-detail-button--primary"
          type="button"
          :disabled="!resource"
          @click="showImportDialog = true"
        >
          <Upload :size="14" />
          导入
        </button>
        <div class="resource-detail-export">
          <button
            class="resource-detail-button"
            type="button"
            :disabled="!resource || exportingEntries"
            @click="showExportMenu = !showExportMenu"
          >
            <Loader2 v-if="exportingEntries" class="lucide-spin" :size="14" />
            <Download v-else :size="14" />
            {{ exportButtonText }}
            <ChevronDown :size="14" />
          </button>
          <div v-if="showExportMenu && !exportingEntries" class="resource-detail-export__menu">
            <button type="button" @click="exportEntries('xlsx')">
              <FileSpreadsheet :size="14" />
              Excel
            </button>
            <button type="button" @click="exportEntries('tmx')">
              <FileCode2 :size="14" />
              TMX
            </button>
            <button v-if="props.mode === 'term'" type="button" @click="exportEntries('tbx')">
              <FileCode2 :size="14" />
              TBX
            </button>
          </div>
        </div>
        <button
          v-if="exportingEntries"
          class="resource-detail-button resource-detail-button--danger"
          type="button"
          @click="cancelCurrentExport"
        >
          <X :size="14" />
          停止导出
        </button>
        <button class="resource-detail-icon-button" type="button" title="更多">
          <ChevronDown :size="16" />
        </button>
      </div>
    </section>

    <p v-if="pageError" class="form-message is-error resource-detail-message">{{ pageError }}</p>

    <section v-if="loadingResource && !resource" class="resource-detail-loading">
      <Loader2 class="lucide-spin" :size="26" />
      页面加载中...
    </section>

    <template v-else-if="resource">
      <section class="resource-detail-meta">
        <div
          v-for="(column, columnIndex) in metaColumns"
          :key="columnIndex"
          class="resource-detail-meta__column"
        >
          <div v-for="item in column" :key="item.label" class="resource-detail-meta__item">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>
      </section>

      <section class="resource-detail-content">
        <h2>{{ copy.detailTitle }}</h2>

        <div class="resource-detail-toolbar">
          <div class="resource-detail-toolbar__left">
            <button
              v-if="canManageResources"
              class="resource-detail-button resource-detail-button--primary"
              type="button"
              @click="openAddDialog"
            >
              <Plus :size="14" />
              添加
            </button>

            <div class="resource-detail-search">
              <Search :size="14" />
              <input
                v-model="searchText"
                type="text"
                :placeholder="copy.searchPlaceholder"
              />
            </div>

            <label class="resource-detail-checkbox">
              <input v-model="matchCase" type="checkbox" />
              匹配大小写
            </label>
          </div>

          <div class="resource-detail-fields">
            <button class="resource-detail-fields__trigger" type="button" @click="showFieldMenu = !showFieldMenu">
              <TableProperties :size="16" />
              显示字段
            </button>
            <div v-if="showFieldMenu" class="resource-detail-fields__menu">
              <label>
                <input v-model="showIndexColumn" type="checkbox" />
                序号
              </label>
              <label>
                <input v-model="showSourceColumn" type="checkbox" />
                {{ sourceLanguageLabel }}
              </label>
              <label>
                <input v-model="showTargetColumn" type="checkbox" />
                {{ targetLanguageLabel }}
              </label>
              <label>
                <input v-model="showCreatorColumn" type="checkbox" />
                创建人
              </label>
              <label>
                <input v-model="showCreatedAtColumn" type="checkbox" />
                创建时间
              </label>
              <label>
                <input v-model="showLastModifiedByColumn" type="checkbox" />
                最后修改人
              </label>
              <label>
                <input v-model="showUpdatedAtColumn" type="checkbox" />
                最新修改时间
              </label>
            </div>
          </div>
        </div>

        <p
          v-if="entryMessage"
          class="form-message resource-detail-message"
          :class="{ 'is-error': entryMessage.includes('失败') || entryMessage.includes('不能为空') || entryMessage.includes('已存在') }"
        >
          {{ entryMessage }}
        </p>

        <div class="resource-detail-table-wrap">
          <table class="resource-detail-table">
            <thead>
              <tr>
                <th v-if="showIndexColumn" class="resource-detail-table__index">序号</th>
                <th v-if="showSourceColumn">{{ sourceLanguageLabel }}</th>
                <th v-if="showTargetColumn">{{ targetLanguageLabel }}</th>
                <th v-if="showCreatorColumn" class="resource-detail-table__meta">创建人</th>
                <th v-if="showCreatedAtColumn" class="resource-detail-table__datetime">创建时间</th>
                <th v-if="showLastModifiedByColumn" class="resource-detail-table__meta">最后修改人</th>
                <th v-if="showUpdatedAtColumn" class="resource-detail-table__datetime">最新修改时间</th>
                <th v-if="canManageResources" class="resource-detail-table__actions">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="loadingEntries">
                <td :colspan="Math.max(tableColumnCount, 1)" class="resource-detail-table__empty">
                  <Loader2 class="lucide-spin" :size="18" />
                  加载中...
                </td>
              </tr>
              <tr v-else-if="entries.length === 0">
                <td :colspan="Math.max(tableColumnCount, 1)" class="resource-detail-table__empty">
                  {{ copy.emptyText }}
                </td>
              </tr>
              <tr v-for="(entry, entryIndex) in entries" v-else :key="entry.id">
                <td v-if="showIndexColumn" class="resource-detail-table__index">
                  {{ indexOffset + entryIndex + 1 }}
                </td>
                <td v-if="showSourceColumn">
                  <textarea
                    v-if="isEditingEntry(entry)"
                    v-model="editSourceText"
                    class="resource-detail-table__textarea"
                    rows="3"
                    :aria-label="`编辑${copy.sourceLabel}`"
                  />
                  <span v-else>{{ entry.source_text }}</span>
                </td>
                <td v-if="showTargetColumn">
                  <textarea
                    v-if="isEditingEntry(entry)"
                    v-model="editTargetText"
                    class="resource-detail-table__textarea"
                    rows="3"
                    :aria-label="`编辑${copy.targetLabel}`"
                  />
                  <span v-else>{{ entry.target_text }}</span>
                </td>
                <td v-if="showCreatorColumn" class="resource-detail-table__meta">
                  {{ entry.creator_name || '-' }}
                </td>
                <td v-if="showCreatedAtColumn" class="resource-detail-table__datetime">
                  {{ formatDate(entry.created_at) }}
                </td>
                <td v-if="showLastModifiedByColumn" class="resource-detail-table__meta">
                  {{ entry.last_modified_by_name || '-' }}
                </td>
                <td v-if="showUpdatedAtColumn" class="resource-detail-table__datetime">
                  {{ formatDate(entry.updated_at) }}
                </td>
                <td v-if="canManageResources" class="resource-detail-table__actions">
                  <div v-if="isEditingEntry(entry)" class="resource-detail-row-actions">
                    <button
                      class="resource-detail-action-button"
                      type="button"
                      title="保存"
                      :disabled="updatingEntryId === entry.id"
                      @click="updateEntry(entry)"
                    >
                      <Loader2 v-if="updatingEntryId === entry.id" class="lucide-spin" :size="14" />
                      <Save v-else :size="14" />
                    </button>
                    <button
                      class="resource-detail-action-button"
                      type="button"
                      title="取消"
                      :disabled="updatingEntryId === entry.id"
                      @click="resetEditForm"
                    >
                      <X :size="14" />
                    </button>
                  </div>
                  <div v-else class="resource-detail-row-actions">
                    <button
                      class="resource-detail-action-button"
                      type="button"
                      title="编辑"
                      :disabled="Boolean(editingEntryId) || deletingEntryId === entry.id"
                      @click="startEditEntry(entry)"
                    >
                      <Pencil :size="14" />
                    </button>
                    <button
                      class="resource-detail-action-button resource-detail-action-button--danger"
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
      :title="copy.addTitle"
      :description="copy.addDescription"
      width="min(760px, calc(100vw - 32px))"
      @close="showAddDialog = false"
    >
      <div class="resource-detail-add-form">
        <label class="field">
          <span class="field__label">{{ copy.sourceLabel }}</span>
          <textarea
            v-model="newSourceText"
            class="field__control field__control--multi"
            rows="6"
            :placeholder="`请输入${copy.sourceLabel}`"
          />
        </label>

        <label class="field">
          <span class="field__label">{{ copy.targetLabel }}</span>
          <textarea
            v-model="newTargetText"
            class="field__control field__control--multi"
            rows="6"
            :placeholder="`请输入${copy.targetLabel}`"
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

    <ResourceImportDialog
      v-if="canManageResources"
      :open="showImportDialog"
      :mode="copy.importTab"
      :initial-tab="copy.importTab"
      :title="copy.importTitle"
      :context-label="resource?.name || copy.fallbackTitle"
      :source-language="resource?.source_language || null"
      :target-language="resource?.target_language || null"
      :fixed-tm-collection-id="props.mode === 'tm' ? props.id : ''"
      :fixed-term-base-id="props.mode === 'term' ? props.id : ''"
      @close="showImportDialog = false"
      @imported="handleImported"
    />
  </div>
</template>

<style scoped>
.resource-detail-page {
  min-height: calc(100vh - 96px);
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  color: var(--text-primary);
  overflow: hidden;
}

.resource-detail-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 58px;
  padding: 0 20px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--surface-0);
}

.resource-detail-topbar__identity,
.resource-detail-topbar__actions,
.resource-detail-toolbar,
.resource-detail-toolbar__left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.resource-detail-topbar__divider {
  width: 1px;
  height: 24px;
  background: var(--line-soft);
}

.resource-detail-topbar__title {
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-detail-back,
.resource-detail-button,
.resource-detail-icon-button,
.resource-detail-fields__trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--button-bg);
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1;
  box-shadow: none;
}

.resource-detail-back {
  min-height: 32px;
  padding: 0;
  border-color: transparent;
  background: transparent;
  color: var(--text-secondary);
}

.resource-detail-button {
  min-height: 32px;
  padding: 0 14px;
}

.resource-detail-button--primary {
  border-color: var(--brand-700);
  background: var(--brand-700);
  color: #ffffff;
}

.resource-detail-button--danger {
  border-color: var(--state-danger);
  background: color-mix(in srgb, var(--state-danger) 9%, var(--button-bg));
  color: var(--state-danger);
}

.resource-detail-back:hover,
.resource-detail-button:hover:not(:disabled),
.resource-detail-icon-button:hover,
.resource-detail-fields__trigger:hover {
  border-color: var(--brand-700);
  background: var(--brand-050);
  color: var(--brand-700);
}

.resource-detail-button--primary:hover:not(:disabled) {
  border-color: #0b6b5b;
  background: #0b6b5b;
  color: #ffffff;
}

.resource-detail-button--danger:hover:not(:disabled) {
  border-color: var(--state-danger);
  background: color-mix(in srgb, var(--state-danger) 14%, var(--button-bg));
  color: var(--state-danger);
}

.resource-detail-button:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.resource-detail-export {
  position: relative;
}

.resource-detail-export__menu {
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

.resource-detail-export__menu button {
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

.resource-detail-export__menu button:hover {
  background: var(--brand-050);
  color: var(--brand-700);
}

.resource-detail-icon-button {
  width: 30px;
  height: 28px;
  padding: 0;
  border-color: transparent;
  background: transparent;
}

.resource-detail-meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 28px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--surface-1);
}

.resource-detail-meta__column {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.resource-detail-meta__item {
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr);
  gap: 14px;
  min-height: 16px;
  align-items: center;
  color: var(--text-primary);
  font-size: 13px;
}

.resource-detail-meta__item span {
  color: var(--text-muted);
}

.resource-detail-meta__item strong {
  min-width: 0;
  overflow-wrap: anywhere;
  font-weight: 400;
}

.resource-detail-content {
  padding: 16px 20px 22px;
}

.resource-detail-content h2 {
  margin: 0 0 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line-soft);
  color: var(--text-primary);
  font-size: 16px;
  font-weight: 600;
}

.resource-detail-toolbar {
  justify-content: space-between;
  margin-bottom: 10px;
}

.resource-detail-search {
  display: flex;
  align-items: center;
  position: relative;
  width: 220px;
  min-width: 160px;
  color: #98a2b3;
}

.resource-detail-search svg {
  position: absolute;
  left: 12px;
  pointer-events: none;
}

.resource-detail-search input {
  width: 100%;
  height: 28px;
  padding: 0 10px 0 32px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--control-bg);
  color: var(--text-primary);
  font-size: 13px;
}

.resource-detail-search input::placeholder {
  color: #98a2b3;
}

.resource-detail-search input:focus {
  outline: none;
  border-color: var(--brand-700);
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.08);
}

.resource-detail-checkbox {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  white-space: nowrap;
}

.resource-detail-checkbox input,
.resource-detail-fields__menu input {
  width: 14px;
  height: 14px;
  accent-color: var(--brand-700);
}

.resource-detail-fields {
  position: relative;
}

.resource-detail-fields__trigger {
  min-height: 28px;
  padding: 0;
  border-color: transparent;
  color: var(--text-secondary);
}

.resource-detail-fields__menu {
  display: grid;
  gap: 8px;
  position: absolute;
  z-index: 5;
  top: calc(100% + 8px);
  right: 0;
  min-width: 150px;
  padding: 10px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-panel);
  box-shadow: var(--shadow-soft);
}

.resource-detail-fields__menu label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  white-space: nowrap;
}

.resource-detail-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-0);
}

.resource-detail-table {
  width: 100%;
  min-width: 1120px;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 13px;
}

.resource-detail-table th {
  height: 36px;
  padding: 0 10px;
  border-right: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
  background: var(--brand-050);
  color: var(--text-secondary);
  font-weight: 600;
  text-align: left;
}

.resource-detail-table th:last-child,
.resource-detail-table td:last-child {
  border-right: none;
}

.resource-detail-table td {
  min-height: 44px;
  padding: 12px 10px;
  border-right: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
  color: var(--text-primary);
  vertical-align: top;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.resource-detail-table__index {
  width: 48px;
  text-align: center !important;
}

.resource-detail-table__meta {
  width: 112px;
}

.resource-detail-table__datetime {
  width: 152px;
}

.resource-detail-table__actions {
  width: 96px;
  text-align: center !important;
  vertical-align: middle !important;
}

.resource-detail-table__empty {
  height: 60px;
  color: var(--text-muted);
  text-align: center !important;
  vertical-align: middle !important;
}

.resource-detail-table__empty svg {
  margin-right: 8px;
  vertical-align: middle;
}

.resource-detail-table__textarea {
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

.resource-detail-table__textarea:focus {
  outline: none;
  border-color: var(--brand-700);
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.08);
}

.resource-detail-row-actions {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.resource-detail-action-button {
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

.resource-detail-action-button:not(:disabled):hover {
  border-color: var(--line-strong);
  background: var(--brand-050);
  color: var(--brand-700);
}

.resource-detail-action-button--danger {
  color: var(--state-danger);
}

.resource-detail-action-button--danger:not(:disabled):hover {
  border-color: rgba(194, 59, 63, 0.24);
  background: var(--state-danger-bg);
  color: var(--state-danger);
}

.resource-detail-action-button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.resource-detail-add-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(220px, 1fr));
  gap: 16px;
}

.resource-detail-message {
  margin: 0 0 10px;
}

.resource-detail-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 240px;
  color: var(--text-muted);
}

@media (max-width: 900px) {
  .resource-detail-topbar,
  .resource-detail-toolbar,
  .resource-detail-topbar__actions {
    align-items: stretch;
    flex-direction: column;
  }

  .resource-detail-topbar {
    padding: 12px 16px;
  }

  .resource-detail-meta {
    grid-template-columns: 1fr;
    gap: 10px;
    padding: 14px 16px;
  }

  .resource-detail-content {
    padding: 14px 16px 24px;
  }

  .resource-detail-toolbar__left {
    align-items: stretch;
    flex-direction: column;
  }

  .resource-detail-search {
    width: 100%;
  }

  .resource-detail-export,
  .resource-detail-export .resource-detail-button {
    width: 100%;
  }

  .resource-detail-add-form {
    grid-template-columns: 1fr;
  }
}
</style>
