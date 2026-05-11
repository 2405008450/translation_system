<script setup lang="ts">
import axios from 'axios'
import { GitMerge, Loader2, Pencil, Plus, Search, Trash2 } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import DataTable from '../components/DataTable.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import Modal from '../components/base/Modal.vue'
import Pagination from '../components/Pagination.vue'
import { useConfirm } from '../composables/useConfirm'
import { formatLanguagePair, languageOptions } from '../constants/languages'
import type { TMCollection } from '../types/api'

interface TMCollectionMergeSummary {
  collection: TMCollection
  source_count: number
  created_rows: number
  updated_rows: number
  skipped_rows: number
  merged_rows: number
}

const router = useRouter()
const confirm = useConfirm()
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
const showCreateDialog = ref(false)
const showMergeDialog = ref(false)
const mergeName = ref('')
const mergeDescription = ref('')
const mergeMessage = ref('')
const mergeSubmitting = ref(false)
const deletingCollections = ref(false)

const selectedCollectionId = ref('')

const columns: DataTableColumn[] = [
  { key: 'name', label: '名称', sortable: true },
  { key: 'language_pair', label: '语言对', sortable: true },
  { key: 'description', label: '说明' },
  { key: 'entry_count', label: '条目数量', width: '100px', sortable: true, align: 'right' },
  { key: 'created_at', label: '创建时间', width: '160px', sortable: true },
  { key: 'updated_at', label: '更新时间', width: '160px', sortable: true },
]

const selectedCollections = computed<TMCollection[]>(() => (
  Array.from(selectedIds.value)
    .map((id) => tmCollections.value.find((item) => item.id === id))
    .filter((item): item is TMCollection => Boolean(item))
))

const selectedCollectionEntryCount = computed(() => (
  selectedCollections.value.reduce((total, collection) => total + collection.entry_count, 0)
))

const mergeLanguagePairLabel = computed(() => {
  const first = selectedCollections.value[0]
  return first ? formatLanguagePair(first.source_language, first.target_language) : ''
})

const canMergeSelectedCollections = computed(() => !getSelectedCollectionsMergeError())

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
  if (!sourceLanguage || !targetLanguage) {
    throw new Error('请先选择源语言和目标语言。')
  }
  if (sourceLanguage === targetLanguage) {
    throw new Error('源语言和目标语言不能相同。')
  }
}

async function navigateToCollectionDetail(collectionId: string) {
  const resolvedId = String(collectionId || '').trim()
  if (!resolvedId) {
    throw new Error('创建成功但未返回记忆库 ID。')
  }

  const target = router.resolve({ name: 'tm-edit', params: { id: resolvedId } })

  try {
    const navigationResult = await router.push(target)
    if (navigationResult) {
      window.location.assign(target.href)
    }
  } catch {
    window.location.assign(target.href)
  }
}

async function loadCollections() {
  loadingCollections.value = true
  try {
    const { data } = await http.get<TMCollection[]>('/translation-memory/collections')
    tmCollections.value = data
    const availableIds = new Set(data.map((item) => item.id))
    selectedIds.value = new Set(Array.from(selectedIds.value).filter((id) => availableIds.has(id)))
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
    showCreateDialog.value = false
    await navigateToCollectionDetail(collection.id)
    collectionMessage.value = `已创建记忆库：${collection.name}`
  } catch (error) {
    collectionMessage.value = getErrorMessage(error, '记忆库创建失败。')
  } finally {
    collectionSubmitting.value = false
  }
}

function openCreateDialog() {
  collectionMessage.value = ''
  showCreateDialog.value = true
}

function closeCreateDialog() {
  if (collectionSubmitting.value) {
    return
  }
  showCreateDialog.value = false
}

function getSelectedCollectionsMergeError() {
  const collections = selectedCollections.value
  if (collections.length < 2) {
    return '请至少选择两个记忆库进行合并。'
  }
  if (collections.some((collection) => !collection.source_language || !collection.target_language)) {
    return '选中的记忆库缺少语言对，无法合并。'
  }
  const first = collections[0]
  const hasMismatch = collections.some((collection) => (
    collection.source_language !== first.source_language
    || collection.target_language !== first.target_language
  ))
  return hasMismatch ? '只能合并语言对一致的记忆库。' : ''
}

function openMergeDialog() {
  const error = getSelectedCollectionsMergeError()
  if (error) {
    collectionMessage.value = error
    return
  }
  const first = selectedCollections.value[0]
  mergeName.value = `${first.name}等${selectedCollections.value.length}个记忆库合并`
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

async function mergeSelectedCollections() {
  const error = getSelectedCollectionsMergeError()
  if (error) {
    mergeMessage.value = error
    return
  }
  if (!mergeName.value.trim()) {
    mergeMessage.value = '合并后的记忆库名称不能为空。'
    return
  }

  mergeSubmitting.value = true
  mergeMessage.value = ''
  try {
    const { data } = await http.post<TMCollectionMergeSummary>('/translation-memory/collections/merge', {
      source_collection_ids: selectedCollections.value.map((collection) => collection.id),
      name: mergeName.value,
      description: mergeDescription.value,
    })
    showMergeDialog.value = false
    await loadCollections()
    selectedIds.value = new Set([data.collection.id])
    selectedCollectionId.value = data.collection.id
    collectionMessage.value = `已合并 ${data.source_count} 个记忆库，生成“${data.collection.name}”：新增 ${data.created_rows} 条，覆盖 ${data.updated_rows} 条。`
  } catch (error) {
    mergeMessage.value = getErrorMessage(error, '记忆库合并失败。')
  } finally {
    mergeSubmitting.value = false
  }
}

async function deleteCollection(collection: any) {
  const confirmed = await confirm({
    title: '删除记忆库',
    message: `确定删除记忆库“${collection.name}”吗？其中 ${collection.entry_count} 条 TM 记录也会一起删除。`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }
  collectionMessage.value = ''
  deletingCollections.value = true
  try {
    await http.delete(`/translation-memory/collections/${collection.id}`)
    if (selectedCollectionId.value === collection.id) {
      selectedCollectionId.value = ''
    }
    selectedIds.value = new Set(Array.from(selectedIds.value).filter((id) => id !== collection.id))
    await loadCollections()
    collectionMessage.value = '记忆库已删除。'
  } catch (error) {
    collectionMessage.value = getErrorMessage(error, '记忆库删除失败。')
  } finally {
    deletingCollections.value = false
  }
}

async function deleteSelectedCollections() {
  const collections = selectedCollections.value
  if (collections.length === 0) {
    return
  }
  const confirmed = await confirm({
    title: '删除选中的记忆库',
    message: `确定删除选中的 ${collections.length} 个记忆库吗？其中 ${selectedCollectionEntryCount.value} 条 TM 记录也会一起删除。`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }

  collectionMessage.value = ''
  deletingCollections.value = true
  try {
    for (const collection of collections) {
      await http.delete(`/translation-memory/collections/${collection.id}`)
    }
    if (collections.some((collection) => collection.id === selectedCollectionId.value)) {
      selectedCollectionId.value = ''
    }
    selectedIds.value = new Set()
    await loadCollections()
    collectionMessage.value = `已删除 ${collections.length} 个记忆库。`
  } catch (error) {
    collectionMessage.value = getErrorMessage(error, '记忆库删除失败。')
    await loadCollections()
  } finally {
    deletingCollections.value = false
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

onMounted(() => {
  void loadCollections()
})
</script>

<template>
  <div>
    <div class="table-page">
      <div class="table-page__header">
        <h2 class="table-page__title">记忆库集合</h2>
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
          <button class="button button--primary" type="button" @click="openCreateDialog">
            <Plus :size="14" /> 创建
          </button>
          <button class="button" type="button" :disabled="loadingCollections" @click="loadCollections">
            {{ loadingCollections ? '刷新中...' : '刷新' }}
          </button>
          <span style="font-size: 13px; color: var(--ink-500);">
            已选择：{{ selectedIds.size }}　总数：{{ totalCount }}
          </span>
        </div>
      </div>

        <div v-if="selectedIds.size > 0" class="resource-bulk-bar">
          <span>已选择 {{ selectedIds.size }} 个记忆库，包含 {{ selectedCollectionEntryCount }} 条 TM 记录</span>
          <div class="resource-bulk-bar__actions">
            <button
              class="button"
              type="button"
              :disabled="!canMergeSelectedCollections || mergeSubmitting"
              :title="canMergeSelectedCollections ? '合并选中的记忆库' : getSelectedCollectionsMergeError()"
              @click="openMergeDialog"
            >
              <GitMerge :size="14" />
              合并
            </button>
            <button
              class="button button--danger"
              type="button"
              :disabled="deletingCollections"
              @click="deleteSelectedCollections"
            >
              <Trash2 :size="14" />
              删除
            </button>
          </div>
        </div>

        <p v-if="collectionMessage" class="form-message table-page__message">{{ collectionMessage }}</p>

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
              <button
                class="text-link tm-link"
                type="button"
                @click="navigateToCollectionDetail(row.id)"
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
              <div style="display: flex; gap: 4px; justify-content: center;">
                <button
                  class="data-table__actions-btn"
                  type="button"
                  title="查看详情"
                  @click="navigateToCollectionDetail(row.id)"
                >
                  <Pencil :size="14" />
                </button>
                <button
                  class="data-table__actions-btn"
                  type="button"
                  title="删除"
                  :disabled="deletingCollections"
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
    </div>

    <Modal
      :open="showCreateDialog"
      title="创建记忆库"
      description="填写名称、说明和语言对后创建新的记忆库。"
      width="min(620px, calc(100vw - 32px))"
      @close="closeCreateDialog"
    >
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
      </div>

      <p v-if="collectionMessage" class="form-message resource-modal-message">{{ collectionMessage }}</p>

      <template #footer>
        <button class="button" type="button" :disabled="collectionSubmitting" @click="closeCreateDialog">取消</button>
        <button
          class="button button--primary"
          type="button"
          :disabled="collectionSubmitting"
          @click="createCollectionFromForm"
        >
          <Loader2 v-if="collectionSubmitting" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ collectionSubmitting ? '创建中...' : '创建记忆库' }}
        </button>
      </template>
    </Modal>

    <Modal
      :open="showMergeDialog"
      title="合并记忆库"
      :description="`将 ${selectedCollections.length} 个同语言对记忆库合并为一个新库。`"
      width="min(680px, calc(100vw - 32px))"
      @close="closeMergeDialog"
    >
      <div class="resource-merge-summary">
        <span class="tag">语言对：{{ mergeLanguagePairLabel }}</span>
        <span class="tag">来源：{{ selectedCollections.length }} 个</span>
        <span class="tag">TM 记录：{{ selectedCollectionEntryCount }} 条</span>
      </div>

      <div class="resource-merge-list">
        <div v-for="collection in selectedCollections" :key="collection.id" class="resource-merge-item">
          <strong>{{ collection.name }}</strong>
          <span>{{ formatLanguagePair(collection.source_language, collection.target_language) }} / {{ collection.entry_count }} 条</span>
        </div>
      </div>

      <div class="upload-form form-grid-2 resource-merge-form">
        <label class="field">
          <span class="field__label">合并后名称</span>
          <input
            v-model="mergeName"
            class="field__control"
            type="text"
            placeholder="例如：产品资料中英合并记忆库"
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
          @click="mergeSelectedCollections"
        >
          <Loader2 v-if="mergeSubmitting" class="lucide-spin" :size="14" />
          <GitMerge v-else :size="14" />
          {{ mergeSubmitting ? '合并中...' : '合并记忆库' }}
        </button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.tm-link {
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
  color: var(--brand-700);
  font-weight: 500;
}

.tm-link:hover {
  color: var(--brand-600);
}

.resource-bulk-bar {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin: 0 20px 12px;
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--brand-050);
  color: var(--text-secondary);
  font-size: 13px;
}

.resource-bulk-bar__actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.table-page__message {
  margin: 0 20px 12px;
}

.resource-modal-message {
  margin-top: 12px;
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
  .resource-bulk-bar,
  .resource-merge-item {
    align-items: flex-start;
    flex-direction: column;
  }

  .resource-bulk-bar__actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
