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
import type { TMCollection, TMImportSummary } from '../types/api'

type TabKey = 'collections' | 'import'

const router = useRouter()
const confirm = useConfirm()
const activeTab = ref<TabKey>('collections')
const searchQuery = ref('')
const currentPage = ref(1)
const pageSize = ref(50)
const selectedIds = ref(new Set<string>())
const sortKey = ref('')
const sortOrder = ref<'asc' | 'desc'>('asc')

const tmCollections = ref<TMCollection[]>([])
const loadingCollections = ref(false)
const collectionMessage = ref('')
const collectionSubmitting = ref(false)
const newCollectionName = ref('')
const newCollectionDescription = ref('')
const newCollectionSourceLanguage = ref('')
const newCollectionTargetLanguage = ref('')
const showCreateForm = ref(false)

const selectedTMFile = ref<File | null>(null)
const tmImporting = ref(false)
const tmImportMessage = ref('')
const tmImportSummary = ref<TMImportSummary | null>(null)
const selectedCollectionId = ref('')
const importSourceLanguage = ref('')
const importTargetLanguage = ref('')
const tmImportSummaryResolved = computed<TMImportSummary>(() => tmImportSummary.value ?? {
  filename: '',
  created_rows: 0,
  updated_rows: 0,
  skipped_empty_rows: 0,
  skipped_header_rows: 0,
  imported_rows: 0,
  collection_id: null,
  collection_name: null,
  source_language: '',
  target_language: '',
})

const columns: DataTableColumn[] = [
  { key: 'name', label: '名称', sortable: true },
  { key: 'language_pair', label: '语言对', sortable: true },
  { key: 'description', label: '说明' },
  { key: 'entry_count', label: '条目数量', width: '100px', sortable: true, align: 'right' },
  { key: 'created_at', label: '创建时间', width: '160px', sortable: true },
  { key: 'updated_at', label: '更新时间', width: '160px', sortable: true },
]

const selectedCollection = computed(() => (
  tmCollections.value.find((item) => item.id === selectedCollectionId.value) ?? null
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

function onTMFileChange(event: Event) {
  selectedTMFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  if (selectedTMFile.value && !selectedCollectionId.value && !newCollectionName.value.trim()) {
    newCollectionName.value = fileBaseName(selectedTMFile.value)
  }
}

async function loadCollections() {
  loadingCollections.value = true
  try {
    const { data } = await http.get<TMCollection[]>('/translation-memory/collections')
    tmCollections.value = data
    if (selectedCollectionId.value && !data.some((item) => item.id === selectedCollectionId.value)) {
      selectedCollectionId.value = ''
    }
  } catch (error) {
    collectionMessage.value = getErrorMessage(error, '记忆库列表加载失败。')
  } finally {
    loadingCollections.value = false
  }
}

async function createCollection(
  name: string,
  description = '',
  sourceLanguage = '',
  targetLanguage = '',
) {
  const cleanedName = name.trim()
  if (!cleanedName) {
    throw new Error('记忆库名称不能为空。')
  }
  ensureLanguagePair(sourceLanguage, targetLanguage)
  const { data } = await http.post<TMCollection>('/translation-memory/collections', {
    name: cleanedName,
    description: description.trim() || null,
    source_language: sourceLanguage,
    target_language: targetLanguage,
  })
  await loadCollections()
  return data
}

async function createCollectionFromForm() {
  collectionMessage.value = ''
  collectionSubmitting.value = true
  try {
    const collection = await createCollection(
      newCollectionName.value,
      newCollectionDescription.value,
      newCollectionSourceLanguage.value,
      newCollectionTargetLanguage.value,
    )
    selectedCollectionId.value = collection.id
    newCollectionName.value = ''
    newCollectionDescription.value = ''
    newCollectionSourceLanguage.value = ''
    newCollectionTargetLanguage.value = ''
    showCreateForm.value = false
    collectionMessage.value = `已创建记忆库：${collection.name}`
  } catch (error) {
    collectionMessage.value = getErrorMessage(error, '记忆库创建失败。')
  } finally {
    collectionSubmitting.value = false
  }
}

async function ensureImportCollection() {
  if (selectedCollectionId.value) {
    return selectedCollectionId.value
  }
  const fallbackName = selectedTMFile.value ? fileBaseName(selectedTMFile.value) : ''
  const collection = await createCollection(
    newCollectionName.value || fallbackName,
    newCollectionDescription.value,
    importSourceLanguage.value,
    importTargetLanguage.value,
  )
  selectedCollectionId.value = collection.id
  return collection.id
}

async function deleteCollection(collection: any) {
  if (collection.entry_count > 0) {
    collectionMessage.value = '已有 TM 记录的记忆库不能直接删除。'
    return
  }
  const confirmed = await confirm({
    title: '删除记忆库',
    message: `确定删除记忆库“${collection.name}”吗？`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }
  collectionMessage.value = ''
  try {
    await http.delete(`/translation-memory/collections/${collection.id}`)
    if (selectedCollectionId.value === collection.id) {
      selectedCollectionId.value = ''
    }
    await loadCollections()
    collectionMessage.value = '记忆库已删除。'
  } catch (error) {
    collectionMessage.value = getErrorMessage(error, '记忆库删除失败。')
  }
}

async function uploadTMWorkbook() {
  if (!selectedTMFile.value) {
    tmImportMessage.value = '请先选择要导入的 Excel 文件。'
    return
  }

  try {
    ensureLanguagePair(importSourceLanguage.value, importTargetLanguage.value)
  } catch (error) {
    tmImportMessage.value = error instanceof Error ? error.message : '请先选择语言对。'
    tmImportSummary.value = null
    return
  }

  tmImportMessage.value = ''
  tmImportSummary.value = null
  tmImporting.value = true
  try {
    const collectionId = await ensureImportCollection()
    const formData = new FormData()
    formData.append('file', selectedTMFile.value)
    formData.append('collection_id', collectionId)
    formData.append('source_language', importSourceLanguage.value)
    formData.append('target_language', importTargetLanguage.value)

    const { data } = await http.post<TMImportSummary>('/translation-memory/import-xlsx', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    tmImportSummary.value = data
    tmImportMessage.value = `导入完成：${data.filename}`
    selectedTMFile.value = null
    const fileInput = document.getElementById('tm-upload-file') as HTMLInputElement | null
    if (fileInput) {
      fileInput.value = ''
    }
    await loadCollections()
  } catch (error) {
    tmImportMessage.value = getErrorMessage(error, 'TM 记忆库导入失败。')
  } finally {
    tmImporting.value = false
  }
}

const filteredCollections = computed(() => {
  let data = tmCollections.value
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
      const key = sortKey.value as keyof TMCollection
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

const totalCount = computed(() => filteredCollections.value.length)
const pagedData = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredCollections.value.slice(start, start + pageSize.value)
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

watch(selectedCollection, (collection) => {
  if (!collection) {
    return
  }
  if (collection.source_language) {
    importSourceLanguage.value = collection.source_language
  }
  if (collection.target_language) {
    importTargetLanguage.value = collection.target_language
  }
})

onMounted(() => {
  void loadCollections()
})
</script>

<template>
  <div>
    <div v-if="showCreateForm" class="upload-panel">
      <div class="section-title">创建记忆库</div>
      <div class="upload-form form-grid-2">
        <label class="field">
          <span class="field__label">记忆库名称</span>
          <input
            v-model="newCollectionName"
            class="field__control"
            type="text"
            placeholder="例如：技术文档中英记忆库"
          />
        </label>

        <label class="field">
          <span class="field__label">说明</span>
          <input
            v-model="newCollectionDescription"
            class="field__control"
            type="text"
            placeholder="可选"
          />
        </label>

        <label class="field">
          <span class="field__label">源语言</span>
          <select v-model="newCollectionSourceLanguage" class="field__control">
            <option value="">请选择</option>
            <option v-for="option in languageOptions" :key="option.code" :value="option.code">
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="field">
          <span class="field__label">目标语言</span>
          <select v-model="newCollectionTargetLanguage" class="field__control">
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
            :disabled="collectionSubmitting"
            @click="createCollectionFromForm"
          >
            <Loader2 v-if="collectionSubmitting" class="lucide-spin" />
            <span v-else>创建记忆库</span>
          </button>
        </div>
      </div>
      <p v-if="collectionMessage" class="form-message" style="margin-top: 8px;">{{ collectionMessage }}</p>
    </div>

    <div class="table-page">
      <div class="table-page__header">
        <div class="tab-bar" style="border-bottom: none;">
          <button
            class="tab-item"
            :class="{ 'is-active': activeTab === 'collections' }"
            @click="activeTab = 'collections'"
          >记忆库集合</button>
          <button
            class="tab-item"
            :class="{ 'is-active': activeTab === 'import' }"
            @click="activeTab = 'import'"
          >导入 TM</button>
        </div>
      </div>

      <template v-if="activeTab === 'collections'">
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
            <button class="button" type="button" :disabled="loadingCollections" @click="loadCollections">
              {{ loadingCollections ? '刷新中...' : '刷新' }}
            </button>
          </div>
        </div>

        <div class="table-page__body">
          <DataTable
            :columns="columns"
            :data="pagedData"
            :loading="loadingCollections"
            :selectable="true"
            :selected-ids="selectedIds"
            :sort-key="sortKey"
            :sort-order="sortOrder"
            :show-index="true"
            :index-offset="indexOffset"
            empty-text="当前还没有记忆库"
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
                  @click="router.push({ name: 'tm-edit', params: { id: row.id } })"
                >
                  <Pencil :size="14" />
                </button>
                <button
                  class="data-table__actions-btn"
                  type="button"
                  title="删除"
                  :disabled="row.entry_count > 0"
                  @click="deleteCollection(row)"
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
              Excel 约定：第一列为源文，第二列为译文。导入时必须明确选择语言对，系统会把语言标签同步写入记忆库和 TM 条目。
            </p>

            <div class="upload-form form-grid-2" style="margin-top: 0;">
              <label class="field">
                <span class="field__label">目标记忆库</span>
                <select v-model="selectedCollectionId" class="field__control">
                  <option value="">新建记忆库</option>
                  <option v-for="collection in tmCollections" :key="collection.id" :value="collection.id">
                    {{ collection.name }}（{{ formatLanguagePair(collection.source_language, collection.target_language) }} / {{ collection.entry_count }} 条）
                  </option>
                </select>
              </label>

              <label class="field">
                <span class="field__label">Excel 文件</span>
                <input
                  id="tm-upload-file"
                  class="field__control"
                  type="file"
                  accept=".xlsx"
                  @change="onTMFileChange"
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
                  :disabled="tmImporting"
                  @click="uploadTMWorkbook"
                >
                  <Loader2 v-if="tmImporting" class="lucide-spin" />
                  <Upload v-else :size="14" />
                  {{ tmImporting ? '导入中...' : '导入记忆库' }}
                </button>
              </div>
            </div>

            <p v-if="tmImportMessage" class="form-message" :class="{ 'is-error': !tmImportSummary }" style="margin-top: 12px;">
              {{ tmImportMessage }}
            </p>
          </div>

          <div v-if="tmImportSummary" style="margin-top: 16px;">
            <div class="section-title">导入结果</div>
            <div class="summary-grid summary-grid--wide">
              <div class="summary-item">
                <strong>{{ tmImportSummaryResolved.collection_name }}</strong>
                <span>目标记忆库</span>
              </div>
              <div class="summary-item">
                <strong>{{ formatLanguagePair(tmImportSummaryResolved.source_language, tmImportSummaryResolved.target_language) }}</strong>
                <span>语言对</span>
              </div>
              <div class="summary-item">
                <strong>{{ tmImportSummaryResolved.imported_rows }}</strong>
                <span>写入总行数</span>
              </div>
              <div class="summary-item">
                <strong>{{ tmImportSummaryResolved.created_rows }}</strong>
                <span>新增</span>
              </div>
              <div class="summary-item">
                <strong>{{ tmImportSummaryResolved.updated_rows }}</strong>
                <span>更新</span>
              </div>
              <div class="summary-item">
                <strong>{{ tmImportSummaryResolved.skipped_header_rows }}</strong>
                <span>跳过表头</span>
              </div>
              <div class="summary-item">
                <strong>{{ tmImportSummaryResolved.skipped_empty_rows }}</strong>
                <span>跳过空行</span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="table-page__body">
          <ResourceImportPanel mode="tm" @imported="loadCollections" />
        </div>
      </template>
    </div>
  </div>
</template>
