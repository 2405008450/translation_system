<script setup lang="ts">
import axios from 'axios'
import { FileCode2, FileSpreadsheet, GitMerge, Loader2, Pencil, Plus, Search, Trash2, X } from 'lucide-vue-next'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import DataTable from '../components/DataTable.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import Modal from '../components/base/Modal.vue'
import Pagination from '../components/Pagination.vue'
import RowActionMenu from '../components/RowActionMenu.vue'
import { useConfirm } from '../composables/useConfirm'
import { canonicalizeLanguagePair, formatLanguagePair, getLanguageLabel, languageOptions } from '../constants/languages'
import { useAuthStore } from '../stores/auth'
import type { TermBase } from '../types/api'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'

type ExportFormat = 'xlsx' | 'tmx' | 'tbx'

interface ResourceExportTask {
  task_id: string
  resource_type: 'term'
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
}

interface TermBaseMergeSummary {
  term_base: TermBase
  source_count: number
  created_rows: number
  updated_rows: number
  skipped_rows: number
  merged_rows: number
}

const router = useRouter()
const confirm = useConfirm()
const authStore = useAuthStore()
const searchQuery = ref('')
const filterSourceLanguage = ref('')
const filterTargetLanguage = ref('')
const currentPage = ref(1)
const pageSize = ref(50)
const selectedIds = ref(new Set<string>())
const sortKey = ref('')
const sortOrder = ref<'asc' | 'desc'>('asc')

const termBases = ref<TermBase[]>([])
const loadingBases = ref(false)
const baseMessage = ref('')
const baseSubmitting = ref(false)
const newBaseName = ref('')
const newBaseDescription = ref('')
const newBaseSourceLanguage = ref('')
const newBaseTargetLanguage = ref('')
const showCreateDialog = ref(false)
const showMergeDialog = ref(false)
const mergeName = ref('')
const mergeDescription = ref('')
const mergeMessage = ref('')
const mergeSubmitting = ref(false)
const deletingBases = ref(false)
const exportingKey = ref('')
const currentExportTaskId = ref('')

const selectedTermBaseId = ref('')
let exportPollTimer: number | null = null
let disposed = false

const canManageResources = computed(() => authStore.isAdmin)
const canCreateResources = computed(() => authStore.isAdmin || authStore.isInternalTranslator)

function normalizeResourceSearchText(value: unknown) {
  return String(value ?? '').trim().toLowerCase()
}

function getResourceSearchKeywords() {
  return normalizeResourceSearchText(searchQuery.value).split(/\s+/).filter(Boolean)
}

function getTermBaseSearchText(termBase: TermBase) {
  return [
    termBase.name,
    termBase.description,
    termBase.source_language,
    termBase.target_language,
    getLanguageLabel(termBase.source_language),
    getLanguageLabel(termBase.target_language),
    formatLanguagePair(termBase.source_language, termBase.target_language),
    termBase.entry_count,
    termBase.created_at,
    termBase.updated_at,
  ].map(normalizeResourceSearchText).join(' ')
}

const columns: DataTableColumn[] = [
  { key: 'name', label: '名称', sortable: true },
  { key: 'language_pair', label: '语言对', sortable: true },
  { key: 'description', label: '说明' },
  { key: 'entry_count', label: '术语数量', width: '100px', sortable: true, align: 'right' },
  { key: 'created_at', label: '创建时间', width: '160px', sortable: true },
  { key: 'updated_at', label: '更新时间', width: '160px', sortable: true },
]

const selectedTermBases = computed<TermBase[]>(() => (
  Array.from(selectedIds.value)
    .map((id) => termBases.value.find((item) => item.id === id))
    .filter((item): item is TermBase => Boolean(item))
))

const selectedTermBaseEntryCount = computed(() => (
  selectedTermBases.value.reduce((total, termBase) => total + termBase.entry_count, 0)
))

const mergeLanguagePairLabel = computed(() => {
  const first = selectedTermBases.value[0]
  if (!first) {
    return ''
  }
  const pair = canonicalizeLanguagePair(first.source_language, first.target_language)
  return pair
    ? formatLanguagePair(pair.source, pair.target)
    : formatLanguagePair(first.source_language, first.target_language)
})

const canMergeSelectedTermBases = computed(() => !getSelectedTermBasesMergeError())
const hasLanguageFilter = computed(() => Boolean(filterSourceLanguage.value || filterTargetLanguage.value))

function formatDate(value: string) {
  const d = new Date(value)
  const date = d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
  const time = d.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
  return { date, time }
}

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

function getExportKey(termBaseId: string, format: ExportFormat) {
  return `${termBaseId}:${format}`
}

function isExporting(termBaseId: string, format: ExportFormat) {
  return exportingKey.value === getExportKey(termBaseId, format)
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

async function waitForExportTask(task: ResourceExportTask) {
  let currentTask = task
  currentExportTaskId.value = task.task_id
  while (!disposed) {
    baseMessage.value = currentTask.message || `导出处理中：${currentTask.progress}%`

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
    const { data } = await http.get<ResourceExportTask>(`/term-bases/export-tasks/${currentTask.task_id}`)
    currentTask = data
  }
  throw new Error('导出任务已取消。')
}

async function cancelCurrentExport() {
  if (!currentExportTaskId.value) {
    return
  }
  baseMessage.value = '正在停止导出...'
  await http.post(`/term-bases/export-tasks/${currentExportTaskId.value}/cancel`).catch(() => undefined)
}

async function exportTermBase(termBase: TermBase | Record<string, any>, format: ExportFormat) {
  if (exportingKey.value) {
    return
  }
  const currentTermBase = termBase as TermBase
  const formatLabel = format === 'xlsx' ? 'Excel' : (format === 'tmx' ? 'TMX' : 'TBX')
  exportingKey.value = getExportKey(currentTermBase.id, format)
  baseMessage.value = ''
  try {
    const { data: task } = await http.post<ResourceExportTask>(
      `/term-bases/${currentTermBase.id}/exports`,
      null,
      { params: { format } },
    )
    baseMessage.value = `${currentTermBase.name} 的 ${formatLabel} 导出任务已提交。`
    const completedTask = await waitForExportTask(task)
    const response = await http.get(`/term-bases/export-tasks/${completedTask.task_id}/download`, {
      responseType: 'blob',
    })
    const filename = resolveDownloadFilename(
      response.headers['content-disposition'],
      `${currentTermBase.name}-term-base.${format}`,
    )
    downloadBlob(response.data, filename)
    baseMessage.value = `${currentTermBase.name} 已导出为 ${formatLabel}。`
  } catch (error) {
    baseMessage.value = error instanceof DOMException && error.name === 'AbortError'
      ? `${currentTermBase.name} 导出已停止。`
      : getErrorMessage(error, `${currentTermBase.name} 导出失败。`)
  } finally {
    clearExportPollTimer()
    exportingKey.value = ''
    currentExportTaskId.value = ''
  }
}

function ensureLanguagePair(sourceLanguage: string, targetLanguage: string) {
  if (!sourceLanguage || !targetLanguage) {
    throw new Error('请先选择源语言和目标语言。')
  }
  if (sourceLanguage === targetLanguage) {
    throw new Error('源语言和目标语言不能相同。')
  }
}

async function loadTermBases() {
  loadingBases.value = true
  try {
    const { data } = await http.get<TermBase[]>('/term-bases')
    termBases.value = data
    const availableIds = new Set(data.map((item) => item.id))
    selectedIds.value = new Set(Array.from(selectedIds.value).filter((id) => availableIds.has(id)))
    if (selectedTermBaseId.value && !data.some((item) => item.id === selectedTermBaseId.value)) {
      selectedTermBaseId.value = ''
    }
  } catch (error) {
    baseMessage.value = getErrorMessage(error, '术语库列表加载失败。')
  } finally {
    loadingBases.value = false
  }
}

async function createTermBase(
  name: string,
  description = '',
  sourceLanguage = '',
  targetLanguage = '',
) {
  const cleanedName = name.trim()
  if (!cleanedName) {
    throw new Error('术语库名称不能为空。')
  }
  ensureLanguagePair(sourceLanguage, targetLanguage)
  const { data } = await http.post<TermBase>('/term-bases', {
    name: cleanedName,
    description: description.trim() || null,
    source_language: sourceLanguage,
    target_language: targetLanguage,
  })
  await loadTermBases()
  return data
}

async function createTermBaseFromForm() {
  if (!canCreateResources.value) {
    baseMessage.value = '当前账号只能查看和导出术语库。'
    return
  }
  baseMessage.value = ''
  baseSubmitting.value = true
  try {
    const termBase = await createTermBase(
      newBaseName.value,
      newBaseDescription.value,
      newBaseSourceLanguage.value,
      newBaseTargetLanguage.value,
    )
    selectedTermBaseId.value = termBase.id
    newBaseName.value = ''
    newBaseDescription.value = ''
    newBaseSourceLanguage.value = ''
    newBaseTargetLanguage.value = ''
    showCreateDialog.value = false
    await router.push({ name: 'term-base-edit', params: { id: termBase.id } })
    baseMessage.value = `已创建术语库：${termBase.name}`
  } catch (error) {
    baseMessage.value = getErrorMessage(error, '术语库创建失败。')
  } finally {
    baseSubmitting.value = false
  }
}

function openCreateDialog() {
  if (!canCreateResources.value) {
    baseMessage.value = '当前账号只能查看和导出术语库。'
    return
  }
  baseMessage.value = ''
  showCreateDialog.value = true
}

function closeCreateDialog() {
  if (baseSubmitting.value) {
    return
  }
  showCreateDialog.value = false
}

function getSelectedTermBasesMergeError() {
  const bases = selectedTermBases.value
  if (bases.length < 2) {
    return '请至少选择两个术语库进行合并。'
  }
  const pairs = bases.map((b) => canonicalizeLanguagePair(b.source_language, b.target_language))
  if (pairs.some((p) => p === null)) {
    return '选中的术语库缺少有效语言对，无法合并。'
  }
  const first = pairs[0]!
  const hasMismatch = pairs.some(
    (p) => p!.source !== first.source || p!.target !== first.target,
  )
  return hasMismatch ? '只能合并语言对完全一致的术语库。' : ''
}

function openMergeDialog() {
  if (!canManageResources.value) {
    baseMessage.value = '当前账号只能查看和导出术语库。'
    return
  }
  const error = getSelectedTermBasesMergeError()
  if (error) {
    baseMessage.value = error
    return
  }
  const first = selectedTermBases.value[0]
  mergeName.value = `${first.name}等${selectedTermBases.value.length}个术语库合并`
  mergeDescription.value = ''
  mergeMessage.value = ''
  showMergeDialog.value = true
}

function closeMergeDialog() {
  if (mergeSubmitting.value) {
    return
  }
  showMergeDialog.value = false
}

async function mergeSelectedTermBases() {
  if (!canManageResources.value) {
    mergeMessage.value = '当前账号只能查看和导出术语库。'
    return
  }
  const error = getSelectedTermBasesMergeError()
  if (error) {
    mergeMessage.value = error
    return
  }
  if (!mergeName.value.trim()) {
    mergeMessage.value = '合并后的术语库名称不能为空。'
    return
  }

  mergeSubmitting.value = true
  mergeMessage.value = ''
  try {
    const { data } = await http.post<TermBaseMergeSummary>('/term-bases/merge', {
      source_term_base_ids: selectedTermBases.value.map((termBase) => termBase.id),
      name: mergeName.value,
      description: mergeDescription.value,
    })
    showMergeDialog.value = false
    await loadTermBases()
    selectedIds.value = new Set([data.term_base.id])
    selectedTermBaseId.value = data.term_base.id
    baseMessage.value = `已合并 ${data.source_count} 个术语库，生成“${data.term_base.name}”：新增 ${data.created_rows} 条，覆盖 ${data.updated_rows} 条。`
  } catch (error) {
    mergeMessage.value = getErrorMessage(error, '术语库合并失败。')
  } finally {
    mergeSubmitting.value = false
  }
}

async function deleteTermBase(termBase: any) {
  if (!canManageResources.value) {
    baseMessage.value = '当前账号只能查看和导出术语库。'
    return
  }
  const confirmed = await confirm({
    title: '删除术语库',
    message: `确定删除术语库“${termBase.name}”吗？其中 ${termBase.entry_count} 条术语也会一起删除。`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }
  baseMessage.value = ''
  deletingBases.value = true
  try {
    await http.delete(`/term-bases/${termBase.id}`)
    if (selectedTermBaseId.value === termBase.id) {
      selectedTermBaseId.value = ''
    }
    selectedIds.value = new Set(Array.from(selectedIds.value).filter((id) => id !== termBase.id))
    await loadTermBases()
    baseMessage.value = '术语库已删除。'
  } catch (error) {
    baseMessage.value = getErrorMessage(error, '术语库删除失败。')
  } finally {
    deletingBases.value = false
  }
}

async function deleteSelectedTermBases() {
  if (!canManageResources.value) {
    baseMessage.value = '当前账号只能查看和导出术语库。'
    return
  }
  const bases = selectedTermBases.value
  if (bases.length === 0) {
    return
  }
  const confirmed = await confirm({
    title: '删除选中的术语库',
    message: `确定删除选中的 ${bases.length} 个术语库吗？其中 ${selectedTermBaseEntryCount.value} 条术语也会一起删除。`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }

  baseMessage.value = ''
  deletingBases.value = true
  try {
    for (const termBase of bases) {
      await http.delete(`/term-bases/${termBase.id}`)
    }
    if (bases.some((termBase) => termBase.id === selectedTermBaseId.value)) {
      selectedTermBaseId.value = ''
    }
    selectedIds.value = new Set()
    await loadTermBases()
    baseMessage.value = `已删除 ${bases.length} 个术语库。`
  } catch (error) {
    baseMessage.value = getErrorMessage(error, '术语库删除失败。')
    await loadTermBases()
  } finally {
    deletingBases.value = false
  }
}

const filteredBases = computed(() => {
  let data = termBases.value
  if (filterSourceLanguage.value) {
    data = data.filter((item) => item.source_language === filterSourceLanguage.value)
  }
  if (filterTargetLanguage.value) {
    data = data.filter((item) => item.target_language === filterTargetLanguage.value)
  }
  const keywords = getResourceSearchKeywords()
  if (keywords.length > 0) {
    data = data.filter((item) => {
      const searchText = getTermBaseSearchText(item)
      return keywords.every((keyword) => searchText.includes(keyword))
    })
  }
  if (sortKey.value) {
    const dir = sortOrder.value === 'asc' ? 1 : -1
    if (sortKey.value === 'language_pair') {
      data = [...data].sort((a, b) => (
        formatLanguagePair(a.source_language, a.target_language)
          .localeCompare(formatLanguagePair(b.source_language, b.target_language)) * dir
      ))
    } else {
      const key = sortKey.value as keyof TermBase
      data = [...data].sort((a, b) => {
        const va = a[key]
        const vb = b[key]
        if (typeof va === 'number' && typeof vb === 'number') {
          return (va - vb) * dir
        }
        return String(va ?? '').localeCompare(String(vb ?? '')) * dir
      })
    }
  }
  return data
})

const totalCount = computed(() => filteredBases.value.length)
const pagedData = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredBases.value.slice(start, start + pageSize.value)
})
const indexOffset = computed(() => (currentPage.value - 1) * pageSize.value)

function handleSort(key: string, order: 'asc' | 'desc') {
  sortKey.value = key
  sortOrder.value = order
}

function handleSelect(ids: Set<string>) {
  selectedIds.value = ids
}

function clearLanguageFilter() {
  filterSourceLanguage.value = ''
  filterTargetLanguage.value = ''
}

watch([searchQuery, filterSourceLanguage, filterTargetLanguage], () => {
  currentPage.value = 1
})

onMounted(() => {
  disposed = false
  void loadTermBases()
})

onUnmounted(() => {
  disposed = true
  clearExportPollTimer()
})
</script>

<template>
  <div class="term-base-page">
    <div class="table-page">
      <div class="table-page__header">
        <h2 class="table-page__title">术语库集合</h2>
      </div>

      <div class="table-toolbar" style="padding: 8px 20px;">
        <div class="table-toolbar__left">
          <div class="table-page__search">
            <Search :size="14" class="table-page__search-icon" />
            <input
              v-model="searchQuery"
              class="table-page__search-input"
              type="text"
              placeholder="搜索名称、说明或语言对..."
            />
          </div>
          <div class="resource-language-filter" aria-label="语言对筛选">
            <select v-model="filterSourceLanguage" class="resource-language-filter__select" title="筛选源语言">
              <option value="">源语言：全部</option>
              <option v-for="option in languageOptions" :key="option.code" :value="option.code">
                {{ option.label }}
              </option>
            </select>
            <span class="resource-language-filter__arrow">→</span>
            <select v-model="filterTargetLanguage" class="resource-language-filter__select" title="筛选目标语言">
              <option value="">目标语言：全部</option>
              <option v-for="option in languageOptions" :key="option.code" :value="option.code">
                {{ option.label }}
              </option>
            </select>
            <button
              v-if="hasLanguageFilter"
              class="resource-language-filter__clear"
              type="button"
              title="清空语言对筛选"
              @click="clearLanguageFilter"
            >
              <X :size="14" />
            </button>
          </div>
          <button v-if="canCreateResources" class="button button--primary" type="button" @click="openCreateDialog">
            <Plus :size="14" /> 创建
          </button>
          <button
            v-if="canManageResources"
            class="button"
            type="button"
            :disabled="!canMergeSelectedTermBases || mergeSubmitting"
            :title="canMergeSelectedTermBases ? '合并选中的术语库' : getSelectedTermBasesMergeError()"
            @click="openMergeDialog"
          >
            <GitMerge :size="14" />
            合并
          </button>
          <button
            v-if="canManageResources"
            class="button button--danger"
            type="button"
            :disabled="selectedIds.size === 0 || deletingBases"
            :title="selectedIds.size === 0 ? '请先选择要删除的术语库' : '删除选中的术语库'"
            @click="deleteSelectedTermBases"
          >
            <Trash2 :size="14" />
            删除
          </button>
          <button class="button" type="button" :disabled="loadingBases" @click="loadTermBases">
            {{ loadingBases ? '刷新中...' : '刷新' }}
          </button>
          <button
            v-if="exportingKey"
            class="button button--danger"
            type="button"
            @click="cancelCurrentExport"
          >
            <X :size="14" />
            停止导出
          </button>
          <span style="font-size: 13px; color: var(--ink-500);">
            已选择：{{ selectedIds.size }}　总数：{{ totalCount }}
          </span>
        </div>
      </div>

        <p v-if="baseMessage" class="form-message table-page__message">{{ baseMessage }}</p>

        <div class="table-page__body">
          <DataTable
            :columns="columns"
            :data="pagedData"
            :loading="loadingBases"
            :selectable="canManageResources"
            :selected-ids="selectedIds"
            :sort-key="sortKey"
            :sort-order="sortOrder"
            :show-index="true"
            :index-offset="indexOffset"
            empty-text="当前还没有术语库"
            @sort="handleSort"
            @select="handleSelect"
          >
            <template #name="{ row }">
              <button
                class="text-link term-base-link"
                type="button"
                @click="router.push({ name: 'term-base-edit', params: { id: row.id } })"
              >
                {{ row.name }}
              </button>
            </template>

            <template #language_pair="{ row }">
              <span>{{ formatLanguagePair(row.source_language, row.target_language) }}</span>
            </template>

            <template #description="{ row }">
              <span style="color: var(--ink-500);">{{ row.description || '无说明' }}</span>
            </template>

            <template #entry_count="{ row }">
              <span style="font-weight: 500;">{{ row.entry_count }}</span>
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
              <div class="resource-row-actions">
                <RowActionMenu title="更多操作">
                  <template #default="{ close }">
                    <button
                      type="button"
                      role="menuitem"
                      @click="router.push({ name: 'term-base-edit', params: { id: row.id } }); close()"
                    >
                      <Pencil :size="14" />
                      查看详情
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      title="导出 Excel"
                      :disabled="Boolean(exportingKey)"
                      @click="close(); exportTermBase(row, 'xlsx')"
                    >
                      <Loader2 v-if="isExporting(row.id, 'xlsx')" class="lucide-spin" :size="13" />
                      <FileSpreadsheet v-else :size="13" />
                      导出 Excel
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      title="导出 TMX"
                      :disabled="Boolean(exportingKey)"
                      @click="close(); exportTermBase(row, 'tmx')"
                    >
                      <Loader2 v-if="isExporting(row.id, 'tmx')" class="lucide-spin" :size="13" />
                      <FileCode2 v-else :size="13" />
                      导出 TMX
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      title="导出 TBX"
                      :disabled="Boolean(exportingKey)"
                      @click="close(); exportTermBase(row, 'tbx')"
                    >
                      <Loader2 v-if="isExporting(row.id, 'tbx')" class="lucide-spin" :size="13" />
                      <FileCode2 v-else :size="13" />
                      导出 TBX
                    </button>
                    <button
                      v-if="canManageResources"
                      class="is-danger"
                      type="button"
                      role="menuitem"
                      :disabled="deletingBases"
                      @click="close(); deleteTermBase(row)"
                    >
                      <Trash2 :size="14" />
                      删除
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
    </div>

    <Modal
      :open="showCreateDialog"
      title="创建术语库"
      description="填写名称、说明和语言对后创建新的术语库。"
      width="min(620px, calc(100vw - 32px))"
      @close="closeCreateDialog"
    >
      <div class="upload-form form-grid-2">
        <label class="field">
          <span class="field__label">术语库名称</span>
          <input
            v-model="newBaseName"
            class="field__control"
            type="text"
            placeholder="例如：医疗器械中英术语库"
          />
        </label>

        <label class="field">
          <span class="field__label">说明</span>
          <input
            v-model="newBaseDescription"
            class="field__control"
            type="text"
            placeholder="可选"
          />
        </label>

        <label class="field">
          <span class="field__label">源语言</span>
          <select v-model="newBaseSourceLanguage" class="field__control">
            <option value="">请选择</option>
            <option v-for="option in languageOptions" :key="option.code" :value="option.code">
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="field">
          <span class="field__label">目标语言</span>
          <select v-model="newBaseTargetLanguage" class="field__control">
            <option value="">请选择</option>
            <option v-for="option in languageOptions" :key="option.code" :value="option.code">
              {{ option.label }}
            </option>
          </select>
        </label>
      </div>

      <p v-if="baseMessage" class="form-message resource-modal-message">{{ baseMessage }}</p>

      <template #footer>
        <button class="button" type="button" :disabled="baseSubmitting" @click="closeCreateDialog">取消</button>
        <button
          class="button button--primary"
          type="button"
          :disabled="baseSubmitting"
          @click="createTermBaseFromForm"
        >
          <Loader2 v-if="baseSubmitting" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ baseSubmitting ? '创建中...' : '创建术语库' }}
        </button>
      </template>
    </Modal>

    <Modal
      :open="showMergeDialog"
      title="合并术语库"
      :description="`将 ${selectedTermBases.length} 个同语言对术语库合并为一个新库。`"
      width="min(680px, calc(100vw - 32px))"
      @close="closeMergeDialog"
    >
      <div class="resource-merge-summary">
        <span class="tag">语言对：{{ mergeLanguagePairLabel }}</span>
        <span class="tag">来源：{{ selectedTermBases.length }} 个</span>
        <span class="tag">术语：{{ selectedTermBaseEntryCount }} 条</span>
      </div>

      <div class="resource-merge-list">
        <div v-for="termBase in selectedTermBases" :key="termBase.id" class="resource-merge-item">
          <strong>{{ termBase.name }}</strong>
          <span>{{ formatLanguagePair(termBase.source_language, termBase.target_language) }} / {{ termBase.entry_count }} 条</span>
        </div>
      </div>

      <div class="upload-form form-grid-2 resource-merge-form">
        <label class="field">
          <span class="field__label">合并后名称</span>
          <input
            v-model="mergeName"
            class="field__control"
            type="text"
            placeholder="例如：产品资料中英合并术语库"
          />
        </label>

        <label class="field">
          <span class="field__label">说明</span>
          <input
            v-model="mergeDescription"
            class="field__control"
            type="text"
            placeholder="可选"
          />
        </label>
      </div>

      <p v-if="mergeMessage" class="form-message is-error resource-modal-message">{{ mergeMessage }}</p>

      <template #footer>
        <button class="button" type="button" :disabled="mergeSubmitting" @click="closeMergeDialog">取消</button>
        <button
          class="button button--primary"
          type="button"
          :disabled="mergeSubmitting"
          @click="mergeSelectedTermBases"
        >
          <Loader2 v-if="mergeSubmitting" class="lucide-spin" :size="14" />
          <GitMerge v-else :size="14" />
          {{ mergeSubmitting ? '合并中...' : '合并术语库' }}
        </button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.term-base-link {
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
  color: var(--brand-700);
  font-weight: 500;
}

.term-base-link:hover {
  color: var(--brand-600);
}

.table-page__message {
  margin: 0 20px 12px;
}

.resource-row-actions {
  display: flex;
  justify-content: center;
}

.resource-modal-message {
  margin-top: 12px;
}

.term-base-page :deep(.data-table__actions) {
  width: 64px;
  min-width: 64px;
}

.resource-merge-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}

.resource-merge-list {
  display: grid;
  gap: 8px;
  max-height: 180px;
  overflow: auto;
  margin-bottom: 16px;
}

.resource-merge-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-muted);
}

.resource-merge-item span {
  color: var(--text-muted);
  font-size: 13px;
  white-space: nowrap;
}

.resource-merge-form {
  margin-top: 0;
}

@media (max-width: 720px) {
  .resource-merge-item {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
