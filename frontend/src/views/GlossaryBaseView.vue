<script setup lang="ts">
import axios from 'axios'
import { FileCode2, FileSpreadsheet, Loader2, Pencil, Plus, Search, Trash2, X } from 'lucide-vue-next'
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
import type { GlossaryBase } from '../types/api'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'

type ExportFormat = 'xlsx' | 'tmx'

interface ResourceExportTask {
  task_id: string
  resource_type: 'glossary'
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

const glossaryBases = ref<GlossaryBase[]>([])
const loadingBases = ref(false)
const baseMessage = ref('')
const baseSubmitting = ref(false)
const deletingBases = ref(false)
const exportingKey = ref('')
const currentExportTaskId = ref('')

const newBaseName = ref('')
const newBaseDescription = ref('')
const newBaseSourceLanguage = ref('')
const newBaseTargetLanguage = ref('')
const showCreateDialog = ref(false)

let exportPollTimer: number | null = null
let disposed = false

const canManageResources = computed(() => authStore.isBusinessManager)
const canCreateResources = computed(() => authStore.isBusinessManager)

function normalizeResourceSearchText(value: unknown) {
  return String(value ?? '').trim().toLowerCase()
}

function getResourceSearchKeywords() {
  return normalizeResourceSearchText(searchQuery.value).split(/\s+/).filter(Boolean)
}

function getGlossaryBaseSearchText(glossaryBase: GlossaryBase) {
  return [
    glossaryBase.name,
    glossaryBase.description,
    glossaryBase.source_language,
    glossaryBase.target_language,
    getLanguageLabel(glossaryBase.source_language),
    getLanguageLabel(glossaryBase.target_language),
    formatLanguagePair(glossaryBase.source_language, glossaryBase.target_language),
    glossaryBase.entry_count,
    glossaryBase.created_at,
    glossaryBase.updated_at,
  ].map(normalizeResourceSearchText).join(' ')
}

const columns: DataTableColumn[] = [
  { key: 'name', label: '名称', sortable: true },
  { key: 'language_pair', label: '语言对', sortable: true },
  { key: 'description', label: '说明' },
  { key: 'entry_count', label: '词条数量', width: '100px', sortable: true, align: 'right' },
  { key: 'created_at', label: '创建时间', width: '160px', sortable: true },
  { key: 'updated_at', label: '更新时间', width: '160px', sortable: true },
]

const selectedGlossaryBases = computed<GlossaryBase[]>(() => (
  Array.from(selectedIds.value)
    .map((id) => glossaryBases.value.find((item) => item.id === id))
    .filter((item): item is GlossaryBase => Boolean(item))
))

const selectedEntryCount = computed(() => (
  selectedGlossaryBases.value.reduce((total, base) => total + base.entry_count, 0)
))

const hasLanguageFilter = computed(() => Boolean(filterSourceLanguage.value || filterTargetLanguage.value))

const filteredBases = computed(() => {
  let data = glossaryBases.value
  if (filterSourceLanguage.value) {
    data = data.filter((item) => item.source_language === filterSourceLanguage.value)
  }
  if (filterTargetLanguage.value) {
    data = data.filter((item) => item.target_language === filterTargetLanguage.value)
  }
  const keywords = getResourceSearchKeywords()
  if (keywords.length > 0) {
    data = data.filter((item) => {
      const searchText = getGlossaryBaseSearchText(item)
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
      const key = sortKey.value as keyof GlossaryBase
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

function ensureLanguagePair(sourceLanguage: string, targetLanguage: string) {
  const pair = canonicalizeLanguagePair(sourceLanguage, targetLanguage)
  if (!pair) {
    throw new Error('请先选择源语言和目标语言。')
  }
  if (pair.source === pair.target) {
    throw new Error('源语言和目标语言不能相同。')
  }
  return pair
}

function getExportKey(glossaryBaseId: string, format: ExportFormat) {
  return `${glossaryBaseId}:${format}`
}

function isExporting(glossaryBaseId: string, format: ExportFormat) {
  return exportingKey.value === getExportKey(glossaryBaseId, format)
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
    const { data } = await http.get<ResourceExportTask>(`/glossary-bases/export-tasks/${currentTask.task_id}`)
    currentTask = data
  }
  throw new Error('导出任务已取消。')
}

async function cancelCurrentExport() {
  if (!currentExportTaskId.value) {
    return
  }
  baseMessage.value = '正在停止导出...'
  await http.post(`/glossary-bases/export-tasks/${currentExportTaskId.value}/cancel`).catch(() => undefined)
}

async function loadGlossaryBases() {
  loadingBases.value = true
  try {
    const { data } = await http.get<GlossaryBase[]>('/glossary-bases')
    glossaryBases.value = data
    const availableIds = new Set(data.map((item) => item.id))
    selectedIds.value = new Set(Array.from(selectedIds.value).filter((id) => availableIds.has(id)))
    baseMessage.value = ''
  } catch (error) {
    baseMessage.value = getErrorMessage(error, '词汇表列表加载失败。')
  } finally {
    loadingBases.value = false
  }
}

async function createGlossaryBase() {
  if (!canCreateResources.value) {
    baseMessage.value = '当前账号只能查看和导出词汇表。'
    return
  }
  const name = newBaseName.value.trim()
  if (!name) {
    baseMessage.value = '词汇表名称不能为空。'
    return
  }

  baseSubmitting.value = true
  baseMessage.value = ''
  try {
    const pair = ensureLanguagePair(newBaseSourceLanguage.value, newBaseTargetLanguage.value)
    const { data } = await http.post<GlossaryBase>('/glossary-bases', {
      name,
      description: newBaseDescription.value.trim() || null,
      source_language: pair.source,
      target_language: pair.target,
    })
    showCreateDialog.value = false
    newBaseName.value = ''
    newBaseDescription.value = ''
    newBaseSourceLanguage.value = ''
    newBaseTargetLanguage.value = ''
    await loadGlossaryBases()
    await router.push({ name: 'glossary-edit', params: { id: data.id } })
  } catch (error) {
    baseMessage.value = getErrorMessage(error, '词汇表创建失败。')
  } finally {
    baseSubmitting.value = false
  }
}

function openCreateDialog() {
  if (!canCreateResources.value) {
    baseMessage.value = '当前账号只能查看和导出词汇表。'
    return
  }
  baseMessage.value = ''
  showCreateDialog.value = true
}

function closeCreateDialog() {
  if (!baseSubmitting.value) {
    showCreateDialog.value = false
  }
}

async function exportGlossaryBase(glossaryBase: GlossaryBase | Record<string, any>, format: ExportFormat) {
  if (exportingKey.value) {
    return
  }
  const currentBase = glossaryBase as GlossaryBase
  const formatLabel = format === 'xlsx' ? 'Excel' : 'TMX'
  exportingKey.value = getExportKey(currentBase.id, format)
  baseMessage.value = ''
  try {
    const { data: task } = await http.post<ResourceExportTask>(
      `/glossary-bases/${currentBase.id}/exports`,
      null,
      { params: { format } },
    )
    baseMessage.value = `${currentBase.name} 的 ${formatLabel} 导出任务已提交。`
    const completedTask = await waitForExportTask(task)
    const response = await http.get(`/glossary-bases/export-tasks/${completedTask.task_id}/download`, {
      responseType: 'blob',
    })
    const filename = resolveDownloadFilename(
      response.headers['content-disposition'],
      `${currentBase.name}-glossary.${format}`,
    )
    downloadBlob(response.data, filename)
    baseMessage.value = `${currentBase.name} 已导出为 ${formatLabel}。`
  } catch (error) {
    baseMessage.value = error instanceof DOMException && error.name === 'AbortError'
      ? `${currentBase.name} 导出已停止。`
      : getErrorMessage(error, `${currentBase.name} 导出失败。`)
  } finally {
    clearExportPollTimer()
    exportingKey.value = ''
    currentExportTaskId.value = ''
  }
}

async function deleteGlossaryBase(glossaryBase: GlossaryBase | Record<string, any>) {
  if (!canManageResources.value) {
    baseMessage.value = '当前账号只能查看和导出词汇表。'
    return
  }
  const currentBase = glossaryBase as GlossaryBase
  const confirmed = await confirm({
    title: '删除词汇表',
    message: `确定删除词汇表“${currentBase.name}”吗？其中 ${currentBase.entry_count} 条词条也会一起删除。`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }
  baseMessage.value = ''
  deletingBases.value = true
  try {
    await http.delete(`/glossary-bases/${currentBase.id}`)
    selectedIds.value = new Set(Array.from(selectedIds.value).filter((id) => id !== currentBase.id))
    await loadGlossaryBases()
    baseMessage.value = '词汇表已删除。'
  } catch (error) {
    baseMessage.value = getErrorMessage(error, '词汇表删除失败。')
  } finally {
    deletingBases.value = false
  }
}

async function deleteSelectedGlossaryBases() {
  if (!canManageResources.value) {
    baseMessage.value = '当前账号只能查看和导出词汇表。'
    return
  }
  const bases = selectedGlossaryBases.value
  if (bases.length === 0) {
    return
  }
  const confirmed = await confirm({
    title: '删除选中的词汇表',
    message: `确定删除选中的 ${bases.length} 个词汇表吗？其中 ${selectedEntryCount.value} 条词条也会一起删除。`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }

  baseMessage.value = ''
  deletingBases.value = true
  try {
    for (const base of bases) {
      await http.delete(`/glossary-bases/${base.id}`)
    }
    selectedIds.value = new Set()
    await loadGlossaryBases()
    baseMessage.value = `已删除 ${bases.length} 个词汇表。`
  } catch (error) {
    baseMessage.value = getErrorMessage(error, '词汇表删除失败。')
    await loadGlossaryBases()
  } finally {
    deletingBases.value = false
  }
}

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
  void loadGlossaryBases()
})

onUnmounted(() => {
  disposed = true
  clearExportPollTimer()
})
</script>

<template>
  <div class="glossary-base-page">
    <div class="table-page">
      <div class="table-page__header">
        <h2 class="table-page__title">词汇表集合</h2>
      </div>

      <div class="table-toolbar glossary-toolbar">
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
            class="button button--danger"
            type="button"
            :disabled="selectedIds.size === 0 || deletingBases"
            :title="selectedIds.size === 0 ? '请先勾选要删除的词汇表' : '删除选中的词汇表'"
            @click="deleteSelectedGlossaryBases"
          >
            <Trash2 :size="14" />
            删除
          </button>
          <button class="button" type="button" :disabled="loadingBases" @click="loadGlossaryBases">
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
          <span class="glossary-toolbar__summary">
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
          empty-text="当前还没有词汇表"
          @sort="handleSort"
          @select="handleSelect"
        >
          <template #name="{ row }">
            <button
              class="text-link glossary-base-link"
              type="button"
              @click="router.push({ name: 'glossary-edit', params: { id: row.id } })"
            >
              {{ row.name }}
            </button>
          </template>

          <template #language_pair="{ row }">
            <span>{{ formatLanguagePair(row.source_language, row.target_language) }}</span>
          </template>

          <template #description="{ row }">
            <span class="glossary-muted">{{ row.description || '无说明' }}</span>
          </template>

          <template #entry_count="{ row }">
            <span class="glossary-count">{{ row.entry_count }}</span>
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
                    @click="router.push({ name: 'glossary-edit', params: { id: row.id } }); close()"
                  >
                    <Pencil :size="14" />
                    查看详情
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    title="导出 Excel"
                    :disabled="Boolean(exportingKey)"
                    @click="close(); exportGlossaryBase(row, 'xlsx')"
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
                    @click="close(); exportGlossaryBase(row, 'tmx')"
                  >
                    <Loader2 v-if="isExporting(row.id, 'tmx')" class="lucide-spin" :size="13" />
                    <FileCode2 v-else :size="13" />
                    导出 TMX
                  </button>
                  <button
                    v-if="canManageResources"
                    class="is-danger"
                    type="button"
                    role="menuitem"
                    :disabled="deletingBases"
                    @click="close(); deleteGlossaryBase(row)"
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
      title="创建词汇表"
      description="词汇表用于 AI 预翻译时按原文检索注入，不参与译后 QA 术语检查。"
      width="min(620px, calc(100vw - 32px))"
      @close="closeCreateDialog"
    >
      <div class="upload-form form-grid-2">
        <label class="field">
          <span class="field__label">词汇表名称</span>
          <input
            v-model="newBaseName"
            class="field__control"
            type="text"
            placeholder="例如：产品手册 AI 预翻译词汇表"
          />
        </label>

        <label class="field">
          <span class="field__label">说明</span>
          <input
            v-model="newBaseDescription"
            class="field__control"
            type="text"
            placeholder="可选，用于区分业务线或适用场景"
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
          @click="createGlossaryBase"
        >
          <Loader2 v-if="baseSubmitting" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ baseSubmitting ? '创建中...' : '创建词汇表' }}
        </button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.glossary-base-link {
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
  color: var(--brand-700);
  font-weight: 500;
}

.glossary-base-link:hover {
  color: var(--brand-600);
}

.glossary-toolbar {
  padding: 8px 20px;
}

.glossary-toolbar__summary,
.glossary-muted {
  color: var(--text-muted);
  font-size: 13px;
}

.glossary-count {
  font-weight: 500;
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

.glossary-base-page :deep(.data-table__actions) {
  width: 64px;
  min-width: 64px;
}
</style>
