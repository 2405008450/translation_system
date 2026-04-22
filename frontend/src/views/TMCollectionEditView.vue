<script setup lang="ts">
import axios from 'axios'
import { ArrowLeft, Download, Loader2, Pencil, Plus, RefreshCw, Save, Search, Trash2, Upload } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import DataTable from '../components/DataTable.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import ResourceImportDialog from '../components/ResourceImportDialog.vue'
import Modal from '../components/base/Modal.vue'
import Pagination from '../components/Pagination.vue'
import { useConfirm } from '../composables/useConfirm'
import { formatLanguagePair, languageOptions } from '../constants/languages'
import type { PaginatedResponse, TMCollection, TMEntryRecord } from '../types/api'
import { downloadBlob } from '../utils/download'

const props = defineProps<{
  id: string
}>()

const router = useRouter()
const confirm = useConfirm()

const collection = ref<TMCollection | null>(null)
const loadingCollection = ref(false)
const loadingEntries = ref(false)
const savingCollection = ref(false)
const savingEntry = ref(false)
const creatingEntry = ref(false)
const exportingEntries = ref(false)
const deletingEntryId = ref('')
const pageError = ref('')
const collectionMessage = ref('')
const entryMessage = ref('')

const formName = ref('')
const formDescription = ref('')
const formSourceLanguage = ref('')
const formTargetLanguage = ref('')

const entrySearch = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const totalEntries = ref(0)
const entries = ref<TMEntryRecord[]>([])
const editingEntryId = ref('')
const editingSourceText = ref('')
const editingTargetText = ref('')

const showCreateEntryDialog = ref(false)
const showImportDialog = ref(false)
const newSourceText = ref('')
const newTargetText = ref('')

const entryColumns: DataTableColumn[] = [
  { key: 'source_text', label: '原文' },
  { key: 'target_text', label: '译文' },
  { key: 'updated_at', label: '更新时间', width: '160px' },
]

const entryCountLabel = computed(() => `${collection.value?.entry_count ?? totalEntries.value} 条记录`)
const editingEntry = computed(() => (
  entries.value.find((item) => item.id === editingEntryId.value) ?? null
))

function formatDate(value: string) {
  const date = new Date(value)
  return date.toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
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

function resetCollectionForm(item: TMCollection) {
  formName.value = item.name
  formDescription.value = item.description || ''
  formSourceLanguage.value = item.source_language || ''
  formTargetLanguage.value = item.target_language || ''
}

function startEditing(entry: TMEntryRecord | Record<string, any>) {
  editingEntryId.value = entry.id
  editingSourceText.value = entry.source_text
  editingTargetText.value = entry.target_text
  entryMessage.value = ''
}

function normalizeEntry(entry: TMEntryRecord | Record<string, any>): TMEntryRecord {
  return entry as TMEntryRecord
}

function cancelEditing() {
  editingEntryId.value = ''
  editingSourceText.value = ''
  editingTargetText.value = ''
  entryMessage.value = ''
}

function resetCreateEntryForm() {
  newSourceText.value = ''
  newTargetText.value = ''
}

async function loadCollection() {
  loadingCollection.value = true
  try {
    const { data } = await http.get<TMCollection>(`/translation-memory/collections/${props.id}`)
    collection.value = data
    resetCollectionForm(data)
    pageError.value = ''
  } catch (error) {
    collection.value = null
    pageError.value = getErrorMessage(error, '记忆库详情加载失败。')
  } finally {
    loadingCollection.value = false
  }
}

async function loadEntries() {
  loadingEntries.value = true
  try {
    const { data } = await http.get<PaginatedResponse<TMEntryRecord>>(
      `/translation-memory/collections/${props.id}/entries`,
      {
        params: {
          skip: (currentPage.value - 1) * pageSize.value,
          limit: pageSize.value,
          search: entrySearch.value.trim() || undefined,
        },
      },
    )
    entries.value = data.items
    totalEntries.value = data.total
    if (editingEntryId.value) {
      const current = data.items.find((item) => item.id === editingEntryId.value)
      if (current) {
        editingSourceText.value = current.source_text
        editingTargetText.value = current.target_text
      }
    }
  } catch (error) {
    entryMessage.value = getErrorMessage(error, 'TM 条目加载失败。')
  } finally {
    loadingEntries.value = false
  }
}

async function reloadPage() {
  await Promise.all([loadCollection(), loadEntries()])
}

async function saveCollection() {
  if (!collection.value) {
    return
  }

  collectionMessage.value = ''
  savingCollection.value = true
  try {
    ensureLanguagePair(formSourceLanguage.value, formTargetLanguage.value)
    const payload = {
      name: formName.value.trim(),
      description: formDescription.value.trim() || null,
      source_language: formSourceLanguage.value,
      target_language: formTargetLanguage.value,
    }
    const { data } = await http.put<TMCollection>(
      `/translation-memory/collections/${collection.value.id}`,
      payload,
    )
    collection.value = data
    resetCollectionForm(data)
    collectionMessage.value = '记忆库信息已更新。'
    await loadEntries()
  } catch (error) {
    collectionMessage.value = getErrorMessage(error, '记忆库保存失败。')
  } finally {
    savingCollection.value = false
  }
}

async function createEntry() {
  if (!collection.value) {
    return
  }

  entryMessage.value = ''
  creatingEntry.value = true
  try {
    const { data } = await http.post<TMEntryRecord>(
      `/translation-memory/collections/${collection.value.id}/entries`,
      {
        source_text: newSourceText.value,
        target_text: newTargetText.value,
      },
    )
    showCreateEntryDialog.value = false
    resetCreateEntryForm()
    currentPage.value = 1
    await reloadPage()
    const created = entries.value.find((item) => item.id === data.id)
    if (created) {
      startEditing(created)
    }
    entryMessage.value = 'TM 条目已新增。'
  } catch (error) {
    entryMessage.value = getErrorMessage(error, 'TM 条目新增失败。')
  } finally {
    creatingEntry.value = false
  }
}

async function saveEntry() {
  if (!editingEntryId.value) {
    return
  }

  entryMessage.value = ''
  savingEntry.value = true
  try {
    const { data } = await http.put<TMEntryRecord>(
      `/translation-memory/entries/${editingEntryId.value}`,
      {
        source_text: editingSourceText.value,
        target_text: editingTargetText.value,
      },
    )
    entryMessage.value = 'TM 条目已更新。'
    await loadEntries()
    const current = entries.value.find((item) => item.id === data.id)
    if (current) {
      startEditing(current)
    } else {
      cancelEditing()
    }
  } catch (error) {
    entryMessage.value = getErrorMessage(error, 'TM 条目保存失败。')
  } finally {
    savingEntry.value = false
  }
}

async function deleteEntry(rawEntry: TMEntryRecord | Record<string, any>) {
  const entry = normalizeEntry(rawEntry)
  const confirmed = await confirm({
    title: '删除 TM 条目',
    message: `确定删除该条 TM 记录吗？`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }

  deletingEntryId.value = entry.id
  entryMessage.value = ''
  try {
    await http.delete(`/translation-memory/entries/${entry.id}`)
    if (editingEntryId.value === entry.id) {
      cancelEditing()
    }
    if (entries.value.length === 1 && currentPage.value > 1) {
      currentPage.value -= 1
    }
    await reloadPage()
    entryMessage.value = 'TM 条目已删除。'
  } catch (error) {
    entryMessage.value = getErrorMessage(error, 'TM 条目删除失败。')
  } finally {
    deletingEntryId.value = ''
  }
}

async function exportEntries() {
  if (!collection.value) {
    return
  }

  exportingEntries.value = true
  entryMessage.value = ''
  try {
    const response = await http.get(`/translation-memory/collections/${collection.value.id}/export-xlsx`, {
      responseType: 'blob',
    })
    downloadBlob(response.data, `${collection.value.name}-tm.xlsx`)
    entryMessage.value = 'TM 条目已导出为 XLSX。'
  } catch (error) {
    entryMessage.value = getErrorMessage(error, 'TM 条目导出失败。')
  } finally {
    exportingEntries.value = false
  }
}

function goBack() {
  void router.push({ name: 'tm' })
}

function openCreateEntryDialog() {
  resetCreateEntryForm()
  showCreateEntryDialog.value = true
}

function runEntrySearch() {
  if (currentPage.value !== 1) {
    currentPage.value = 1
    return
  }
  void loadEntries()
}

async function handleImported(payload: { tab: 'tm' | 'term' }) {
  if (payload.tab !== 'tm') {
    return
  }
  showImportDialog.value = false
  await reloadPage()
  entryMessage.value = '导入完成，列表已刷新。'
}

watch([currentPage, pageSize], () => {
  void loadEntries()
})

watch(() => props.id, () => {
  currentPage.value = 1
  cancelEditing()
  void reloadPage()
})

onMounted(() => {
  void reloadPage()
})
</script>

<template>
  <div class="content-stack">
    <section class="panel panel--header">
      <div class="panel-header">
        <div>
          <button class="button" type="button" @click="goBack">
            <ArrowLeft :size="14" />
            返回记忆库管理
          </button>
          <div class="section-title section-title--tight" style="margin-top: 14px;">
            {{ collection?.name || '记忆库详情' }}
          </div>
          <p class="panel-subtitle">像任务详情页一样集中管理基础信息、TM 条目、导入和导出。</p>
        </div>
        <div class="summary-grid" style="min-width: min(420px, 100%);">
          <div class="summary-item">
            <strong style="font-size: 18px; line-height: 1.4; overflow-wrap: anywhere;">
              {{ formatLanguagePair(collection?.source_language, collection?.target_language) }}
            </strong>
            <span>当前语言对</span>
          </div>
          <div class="summary-item">
            <strong>{{ entryCountLabel }}</strong>
            <span>记忆条目</span>
          </div>
        </div>
      </div>
    </section>

    <p v-if="pageError" class="form-message is-error">{{ pageError }}</p>

    <section v-if="loadingCollection && !collection" class="panel">
      <div class="empty-state">
        <Loader2 class="lucide-spin" :size="28" />
        页面加载中...
      </div>
    </section>

    <template v-else-if="collection">
      <section class="panel">
        <div class="panel-header panel-header--compact">
          <div>
            <div class="section-title section-title--tight">基础信息</div>
            <p class="panel-subtitle">修改名称、说明和语言对后，条目语言标签会一起同步。</p>
          </div>
          <span class="tag">最近更新：{{ formatDate(collection.updated_at) }}</span>
        </div>

        <div class="upload-form form-grid-2" style="margin-top: 0;">
          <label class="field">
            <span class="field__label">记忆库名称</span>
            <input v-model="formName" class="field__control" type="text" placeholder="请输入记忆库名称" />
          </label>

          <label class="field">
            <span class="field__label">说明</span>
            <input v-model="formDescription" class="field__control" type="text" placeholder="可选说明" />
          </label>

          <label class="field">
            <span class="field__label">源语言</span>
            <select v-model="formSourceLanguage" class="field__control">
              <option value="">请选择</option>
              <option v-for="option in languageOptions" :key="option.code" :value="option.code">
                {{ option.label }}
              </option>
            </select>
          </label>

          <label class="field">
            <span class="field__label">目标语言</span>
            <select v-model="formTargetLanguage" class="field__control">
              <option value="">请选择</option>
              <option v-for="option in languageOptions" :key="option.code" :value="option.code">
                {{ option.label }}
              </option>
            </select>
          </label>

          <div class="field-actions">
            <button class="button button--primary" type="button" :disabled="savingCollection" @click="saveCollection">
              <Loader2 v-if="savingCollection" class="lucide-spin" />
              <Save v-else :size="14" />
              {{ savingCollection ? '保存中...' : '保存基础信息' }}
            </button>
          </div>
        </div>

        <p v-if="collectionMessage" class="form-message" :class="{ 'is-error': collectionMessage.includes('失败') || collectionMessage.includes('不能为空') }">
          {{ collectionMessage }}
        </p>
      </section>

      <section class="panel panel--stretch">
        <div class="panel-header panel-header--compact">
          <div>
            <div class="section-title section-title--tight">TM 条目</div>
            <p class="panel-subtitle">支持手动新增、导入 Excel、导出 XLSX，以及逐条编辑和删除。</p>
          </div>
        </div>

        <div class="table-toolbar" style="padding: 0 0 12px;">
          <div class="table-toolbar__left">
            <div class="table-page__search">
              <Search :size="14" class="table-page__search-icon" />
              <input
                v-model="entrySearch"
                class="table-page__search-input"
                type="text"
                placeholder="搜索原文或译文"
                @keyup.enter="runEntrySearch"
              />
            </div>
            <span style="font-size: 13px; color: var(--ink-500);">{{ entryCountLabel }}</span>
          </div>
          <div class="table-toolbar__right">
            <button class="button button--primary" type="button" @click="openCreateEntryDialog">
              <Plus :size="14" />
              手动新增
            </button>
            <button class="button" type="button" @click="showImportDialog = true">
              <Upload :size="14" />
              导入
            </button>
            <button class="button" type="button" :disabled="exportingEntries" @click="exportEntries">
              <Loader2 v-if="exportingEntries" class="lucide-spin" :size="14" />
              <Download v-else :size="14" />
              导出 XLSX
            </button>
            <button class="button" type="button" @click="runEntrySearch">搜索</button>
            <button class="button" type="button" :disabled="loadingEntries" @click="loadEntries">
              <RefreshCw :size="14" />
              刷新
            </button>
          </div>
        </div>

        <div v-if="editingEntry" class="upload-panel" style="margin-top: 6px;">
          <div class="section-title" style="margin-bottom: 10px;">编辑中的条目</div>
          <div class="upload-form form-grid-2" style="margin-top: 0;">
            <label class="field">
              <span class="field__label">原文</span>
              <textarea
                v-model="editingSourceText"
                class="field__control field__control--multi"
                rows="5"
                placeholder="请输入原文"
              />
            </label>

            <label class="field">
              <span class="field__label">译文</span>
              <textarea
                v-model="editingTargetText"
                class="field__control field__control--multi"
                rows="5"
                placeholder="请输入译文"
              />
            </label>

            <div class="field-actions" style="display: flex; gap: 8px; flex-wrap: wrap;">
              <button class="button button--primary" type="button" :disabled="savingEntry" @click="saveEntry">
                <Loader2 v-if="savingEntry" class="lucide-spin" />
                <Save v-else :size="14" />
                {{ savingEntry ? '保存中...' : '保存条目' }}
              </button>
              <button class="button" type="button" @click="cancelEditing">取消编辑</button>
            </div>
          </div>
        </div>
        <div v-else class="empty-state" style="padding: 28px 16px; border: 1px dashed #d7dde4; border-radius: 8px; margin-top: 6px;">
          <Pencil :size="18" />
          请选择一条 TM 记录开始编辑
        </div>

        <p v-if="entryMessage" class="form-message" :class="{ 'is-error': entryMessage.includes('失败') || entryMessage.includes('不能为空') || entryMessage.includes('已存在') }">
          {{ entryMessage }}
        </p>

        <DataTable
          :columns="entryColumns"
          :data="entries"
          :loading="loadingEntries"
          empty-text="当前记忆库还没有条目"
        >
          <template #source_text="{ row }">
            <div style="white-space: pre-wrap; line-height: 1.5; overflow-wrap: anywhere;">{{ row.source_text }}</div>
          </template>

          <template #target_text="{ row }">
            <div style="white-space: pre-wrap; line-height: 1.5; overflow-wrap: anywhere;">{{ row.target_text }}</div>
          </template>

          <template #updated_at="{ row }">
            <div class="date-cell">{{ formatDate(row.updated_at) }}</div>
          </template>

          <template #actions="{ row }">
            <div class="asset-entry-actions">
              <button class="data-table__actions-btn" type="button" title="编辑条目" @click="startEditing(row)">
                <Pencil :size="14" />
              </button>
              <button
                class="data-table__actions-btn"
                type="button"
                title="删除条目"
                :disabled="deletingEntryId === row.id"
                @click="deleteEntry(row)"
              >
                <Trash2 :size="14" />
              </button>
            </div>
          </template>
        </DataTable>

        <Pagination
          :total="totalEntries"
          :page="currentPage"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          @update:page="currentPage = $event"
          @update:page-size="pageSize = $event"
        />
      </section>
    </template>

    <Modal
      :open="showCreateEntryDialog"
      title="手动新增 TM 条目"
      description="填写原文和译文后会直接写入当前记忆库。"
      width="min(760px, calc(100vw - 32px))"
      @close="showCreateEntryDialog = false"
    >
      <div class="form-grid-2">
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
      </div>

      <template #footer>
        <button class="button" type="button" @click="showCreateEntryDialog = false">取消</button>
        <button class="button button--primary" type="button" :disabled="creatingEntry" @click="createEntry">
          <Loader2 v-if="creatingEntry" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ creatingEntry ? '新增中...' : '确认新增' }}
        </button>
      </template>
    </Modal>

    <ResourceImportDialog
      :open="showImportDialog"
      initial-tab="tm"
      :context-label="collection?.name || '当前记忆库'"
      :source-language="collection?.source_language || null"
      :target-language="collection?.target_language || null"
      :fixed-tm-collection-id="collection?.id || ''"
      @close="showImportDialog = false"
      @imported="handleImported"
    />
  </div>
</template>

<style scoped>
.asset-entry-actions {
  display: flex;
  gap: 4px;
  justify-content: center;
}
</style>
