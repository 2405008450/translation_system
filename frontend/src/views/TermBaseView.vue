<script setup lang="ts">
import axios from 'axios'
import { Loader2, Pencil, Plus, Search, Trash2, Upload } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import DataTable from '../components/DataTable.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import Pagination from '../components/Pagination.vue'
import ResourceImportPanel from '../components/ResourceImportPanel.vue'
import { useConfirm } from '../composables/useConfirm'
import { formatLanguagePair, languageOptions } from '../constants/languages'
import type { TermBase, TermImportSummary } from '../types/api'

type TabKey = 'bases' | 'import'

const router = useRouter()
const confirm = useConfirm()
const activeTab = ref<TabKey>('bases')
const searchQuery = ref('')
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
const showCreateForm = ref(false)

const selectedTermFile = ref<File | null>(null)
const termImporting = ref(false)
const termImportMessage = ref('')
const termImportSummary = ref<TermImportSummary | null>(null)
const selectedTermBaseId = ref('')
const importSourceLanguage = ref('')
const importTargetLanguage = ref('')
const termImportSummaryResolved = computed<TermImportSummary>(() => termImportSummary.value ?? {
  filename: '',
  created_rows: 0,
  updated_rows: 0,
  skipped_empty_rows: 0,
  skipped_header_rows: 0,
  imported_rows: 0,
  term_base_id: '',
  term_base_name: '',
  source_language: '',
  target_language: '',
})

const columns: DataTableColumn[] = [
  { key: 'name', label: '名称', sortable: true },
  { key: 'language_pair', label: '语言对', sortable: true },
  { key: 'description', label: '说明' },
  { key: 'entry_count', label: '术语数量', width: '100px', sortable: true, align: 'right' },
  { key: 'created_at', label: '创建时间', width: '160px', sortable: true },
  { key: 'updated_at', label: '更新时间', width: '160px', sortable: true },
]

const selectedTermBase = computed(() => (
  termBases.value.find((item) => item.id === selectedTermBaseId.value) ?? null
))

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

function fileBaseName(file: File) {
  return file.name.replace(/\.[^.]+$/, '')
}

function ensureLanguagePair(sourceLanguage: string, targetLanguage: string) {
  if (!sourceLanguage || !targetLanguage) {
    throw new Error('请先选择源语言和目标语言。')
  }
  if (sourceLanguage === targetLanguage) {
    throw new Error('源语言和目标语言不能相同。')
  }
}

function onTermFileChange(event: Event) {
  selectedTermFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  if (selectedTermFile.value && !selectedTermBaseId.value && !newBaseName.value.trim()) {
    newBaseName.value = fileBaseName(selectedTermFile.value)
  }
}

async function loadTermBases() {
  loadingBases.value = true
  try {
    const { data } = await http.get<TermBase[]>('/term-bases')
    termBases.value = data
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
    showCreateForm.value = false
    baseMessage.value = `已创建术语库：${termBase.name}`
  } catch (error) {
    baseMessage.value = getErrorMessage(error, '术语库创建失败。')
  } finally {
    baseSubmitting.value = false
  }
}

async function ensureImportTermBase() {
  if (selectedTermBaseId.value) {
    return selectedTermBaseId.value
  }
  const fallbackName = selectedTermFile.value ? fileBaseName(selectedTermFile.value) : ''
  const termBase = await createTermBase(
    newBaseName.value || fallbackName,
    newBaseDescription.value,
    importSourceLanguage.value,
    importTargetLanguage.value,
  )
  selectedTermBaseId.value = termBase.id
  return termBase.id
}

async function deleteTermBase(termBase: any) {
  if (termBase.entry_count > 0) {
    baseMessage.value = '已有术语条目的术语库不能直接删除。'
    return
  }
  const confirmed = await confirm({
    title: '删除术语库',
    message: `确定删除术语库“${termBase.name}”吗？`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }
  baseMessage.value = ''
  try {
    await http.delete(`/term-bases/${termBase.id}`)
    if (selectedTermBaseId.value === termBase.id) {
      selectedTermBaseId.value = ''
    }
    await loadTermBases()
    baseMessage.value = '术语库已删除。'
  } catch (error) {
    baseMessage.value = getErrorMessage(error, '术语库删除失败。')
  }
}

async function uploadTermWorkbook() {
  if (!selectedTermFile.value) {
    termImportMessage.value = '请先选择要导入的 Excel 文件。'
    return
  }

  try {
    ensureLanguagePair(importSourceLanguage.value, importTargetLanguage.value)
  } catch (error) {
    termImportMessage.value = error instanceof Error ? error.message : '请先选择语言对。'
    termImportSummary.value = null
    return
  }

  termImportMessage.value = ''
  termImportSummary.value = null
  termImporting.value = true
  try {
    const termBaseId = await ensureImportTermBase()
    const formData = new FormData()
    formData.append('file', selectedTermFile.value)
    formData.append('term_base_id', termBaseId)
    formData.append('source_language', importSourceLanguage.value)
    formData.append('target_language', importTargetLanguage.value)

    const { data } = await http.post<TermImportSummary>('/term-bases/import-xlsx', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    termImportSummary.value = data
    termImportMessage.value = `导入完成：${data.filename}`
    selectedTermFile.value = null
    const fileInput = document.getElementById('term-upload-file') as HTMLInputElement | null
    if (fileInput) {
      fileInput.value = ''
    }
    await loadTermBases()
  } catch (error) {
    termImportMessage.value = getErrorMessage(error, '术语库导入失败。')
  } finally {
    termImporting.value = false
  }
}

const filteredBases = computed(() => {
  let data = termBases.value
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    data = data.filter((item) => (
      item.name.toLowerCase().includes(q)
      || (item.description || '').toLowerCase().includes(q)
      || formatLanguagePair(item.source_language, item.target_language).toLowerCase().includes(q)
    ))
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

watch(searchQuery, () => {
  currentPage.value = 1
})

watch(selectedTermBase, (termBase) => {
  if (!termBase) {
    return
  }
  importSourceLanguage.value = termBase.source_language
  importTargetLanguage.value = termBase.target_language
})

onMounted(() => {
  void loadTermBases()
})
</script>

<template>
  <div>
    <div v-if="showCreateForm" class="upload-panel">
      <div class="section-title">创建术语库</div>
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

        <div class="field-actions">
          <button
            class="button button--primary"
            type="button"
            :disabled="baseSubmitting"
            @click="createTermBaseFromForm"
          >
            <Loader2 v-if="baseSubmitting" class="lucide-spin" />
            <span v-else>创建术语库</span>
          </button>
        </div>
      </div>
      <p v-if="baseMessage" class="form-message" style="margin-top: 8px;">{{ baseMessage }}</p>
    </div>

    <div class="table-page">
      <div class="table-page__header">
        <div class="tab-bar" style="border-bottom: none;">
          <button
            class="tab-item"
            :class="{ 'is-active': activeTab === 'bases' }"
            @click="activeTab = 'bases'"
          >术语库集合</button>
          <button
            class="tab-item"
            :class="{ 'is-active': activeTab === 'import' }"
            @click="activeTab = 'import'"
          >导入术语</button>
        </div>
      </div>

      <template v-if="activeTab === 'bases'">
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
            <span style="font-size: 13px; color: var(--ink-500);">
              已选择：{{ selectedIds.size }}　总数：{{ totalCount }}
            </span>
          </div>
          <div class="table-toolbar__right">
            <button class="button" type="button" @click="showCreateForm = !showCreateForm">
              <Plus :size="14" /> {{ showCreateForm ? '收起' : '创建' }}
            </button>
            <button class="button" type="button" :disabled="loadingBases" @click="loadTermBases">
              {{ loadingBases ? '刷新中...' : '刷新' }}
            </button>
          </div>
        </div>

        <div class="table-page__body">
          <DataTable
            :columns="columns"
            :data="pagedData"
            :loading="loadingBases"
            :selectable="true"
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
              <strong style="font-weight: 500; color: var(--brand-700);">{{ row.name }}</strong>
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
              <div style="display: flex; gap: 4px; justify-content: center;">
                <button
                  class="data-table__actions-btn"
                  type="button"
                  title="编辑"
                  @click="router.push({ name: 'term-base-edit', params: { id: row.id } })"
                >
                  <Pencil :size="14" />
                </button>
                <button
                  class="data-table__actions-btn"
                  type="button"
                  title="删除"
                  :disabled="row.entry_count > 0"
                  @click="deleteTermBase(row)"
                >
                  <Trash2 :size="14" />
                </button>
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

      <template v-else-if="false">
        <div class="table-page__body">
          <div class="upload-panel" style="border: none; box-shadow: none; margin: 0; padding: 0;">
            <p class="hint-text" style="margin-bottom: 12px;">
              Excel 约定：第一列为源术语，第二列为目标术语。导入时必须明确选择语言对，系统会把语言标签同步写入术语库和术语条目。
            </p>

            <div class="upload-form form-grid-2" style="margin-top: 0;">
              <label class="field">
                <span class="field__label">目标术语库</span>
                <select v-model="selectedTermBaseId" class="field__control">
                  <option value="">新建术语库</option>
                  <option v-for="termBase in termBases" :key="termBase.id" :value="termBase.id">
                    {{ termBase.name }}（{{ formatLanguagePair(termBase.source_language, termBase.target_language) }} / {{ termBase.entry_count }} 条）
                  </option>
                </select>
              </label>

              <label class="field">
                <span class="field__label">Excel 文件</span>
                <input
                  id="term-upload-file"
                  class="field__control"
                  type="file"
                  accept=".xlsx"
                  @change="onTermFileChange"
                />
              </label>

              <label class="field">
                <span class="field__label">源语言</span>
                <select v-model="importSourceLanguage" class="field__control">
                  <option value="">请选择</option>
                  <option v-for="option in languageOptions" :key="option.code" :value="option.code">
                    {{ option.label }}
                  </option>
                </select>
              </label>

              <label class="field">
                <span class="field__label">目标语言</span>
                <select v-model="importTargetLanguage" class="field__control">
                  <option value="">请选择</option>
                  <option v-for="option in languageOptions" :key="option.code" :value="option.code">
                    {{ option.label }}
                  </option>
                </select>
              </label>

              <div class="field-actions">
                <button
                  class="button button--primary"
                  type="button"
                  :disabled="termImporting"
                  @click="uploadTermWorkbook"
                >
                  <Loader2 v-if="termImporting" class="lucide-spin" />
                  <Upload v-else :size="14" />
                  {{ termImporting ? '导入中...' : '导入术语库' }}
                </button>
              </div>
            </div>

            <p v-if="termImportMessage" class="form-message" :class="{ 'is-error': !termImportSummary }" style="margin-top: 12px;">
              {{ termImportMessage }}
            </p>
          </div>

          <div v-if="termImportSummary" style="margin-top: 16px;">
            <div class="section-title">导入结果</div>
            <div class="summary-grid summary-grid--wide">
              <div class="summary-item">
                <strong>{{ termImportSummaryResolved.term_base_name }}</strong>
                <span>目标术语库</span>
              </div>
              <div class="summary-item">
                <strong>{{ formatLanguagePair(termImportSummaryResolved.source_language, termImportSummaryResolved.target_language) }}</strong>
                <span>语言对</span>
              </div>
              <div class="summary-item">
                <strong>{{ termImportSummaryResolved.imported_rows }}</strong>
                <span>写入总行数</span>
              </div>
              <div class="summary-item">
                <strong>{{ termImportSummaryResolved.created_rows }}</strong>
                <span>新增</span>
              </div>
              <div class="summary-item">
                <strong>{{ termImportSummaryResolved.updated_rows }}</strong>
                <span>更新</span>
              </div>
              <div class="summary-item">
                <strong>{{ termImportSummaryResolved.skipped_header_rows }}</strong>
                <span>跳过表头</span>
              </div>
              <div class="summary-item">
                <strong>{{ termImportSummaryResolved.skipped_empty_rows }}</strong>
                <span>跳过空行</span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="table-page__body">
          <ResourceImportPanel mode="term" @imported="loadTermBases" />
        </div>
      </template>
    </div>
  </div>
</template>
