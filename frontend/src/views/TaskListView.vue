<script setup lang="ts">
import axios from 'axios'
import {
  ArrowRight,
  Download,
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
import type { DataTableColumn } from '../components/DataTable.vue'
import Pagination from '../components/Pagination.vue'
import ResourceImportDialog from '../components/ResourceImportDialog.vue'
import { useConfirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'
import { formatLanguagePair } from '../constants/languages'
import { getFileStatusMeta } from '../constants/status'
import { supportedTaskFileAccept } from '../constants/taskFiles'
import { useTaskStore } from '../stores/task'
import type { TermBase, TMCollection } from '../types/api'

type MainTab = 'tasks' | 'performance'
type SubTab = 'all' | 'incomplete'
type ResourceImportTab = 'tm' | 'term'

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
const selectedCollectionIds = ref<string[]>([])
const termBases = ref<TermBase[]>([])
const loadingTermBases = ref(false)
const selectedTermBaseId = ref<string | null>(null)
const showUploadForm = ref(false)
const currentPage = ref(1)
const pageSize = ref(50)
const selectedIds = ref(new Set<string>())
const sortKey = ref('')
const sortOrder = ref<'asc' | 'desc'>('asc')
const searchQuery = ref('')
const showImportDialog = ref(false)
const importDialogInitialTab = ref<ResourceImportTab>('tm')
const openActionMenuId = ref<string | null>(null)
const importDialogContext = ref<{
  label: string
  sourceLanguage: string | null
  targetLanguage: string | null
}>({
  label: t('taskList.mainTab'),
  sourceLanguage: null,
  targetLanguage: null,
})

const columns = computed<DataTableColumn[]>(() => ([
  { key: 'filename', label: t('taskList.columns.filename'), sortable: true },
  { key: 'status', label: t('taskList.columns.status'), width: '110px' },
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

async function loadTasks() {
  pageError.value = ''
  try {
    await taskStore.fetchTasks()
  } catch (error) {
    pageError.value = getErrorMessage(error, t('taskList.errors.load'))
  }
}

async function loadTMCollections() {
  loadingCollections.value = true
  try {
    const { data } = await http.get<TMCollection[]>('/translation-memory/collections')
    tmCollections.value = data
    if (selectedCollectionIds.value.length === 0 && data.length > 0) {
      selectedCollectionIds.value = [data[0].id]
    }
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

function onFileChange(event: Event) {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

async function uploadFile() {
  if (!selectedFile.value) {
    pageError.value = '请选择要上传的文件。'
    return
  }
  if (selectedCollectionIds.value.length === 0) {
    pageError.value = t('taskList.errors.selectCollection')
    return
  }

  pageError.value = ''
  try {
    const result = await taskStore.uploadTask(
      selectedFile.value,
      threshold.value,
      selectedCollectionIds.value,
      selectedTermBaseId.value,
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

async function removeTask(fileRecordId: string, filename: string) {
  const confirmed = await confirm({
    title: t('taskList.messages.deleting'),
    message: t('taskList.messages.deleteConfirm', { name: filename }),
    confirmText: t('common.actions.delete'),
    danger: true,
  })

  if (!confirmed) {
    return
  }

  pageError.value = ''
  try {
    await taskStore.deleteTask(fileRecordId)
    toast.success(t('taskList.messages.deleted', { name: filename }))
  } catch (error) {
    pageError.value = getErrorMessage(error, t('taskList.errors.delete'))
  }
}

const filteredTasks = computed(() => {
  let tasks = taskStore.tasks

  if (subTab.value === 'incomplete') {
    tasks = tasks.filter((task) => task.status !== 'completed' && task.status !== 'translated')
  }

  if (searchQuery.value.trim()) {
    const query = searchQuery.value.trim().toLowerCase()
    tasks = tasks.filter((task) => task.filename.toLowerCase().includes(query))
  }

  if (sortKey.value) {
    const key = sortKey.value as keyof typeof tasks[0]
    const direction = sortOrder.value === 'asc' ? 1 : -1
    tasks = [...tasks].sort((left, right) => String(left[key]).localeCompare(String(right[key])) * direction)
  }

  return tasks
})

const totalCount = computed(() => filteredTasks.value.length)
const pagedTasks = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredTasks.value.slice(start, start + pageSize.value)
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

watch(searchQuery, () => {
  currentPage.value = 1
})

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
  void loadTasks()
  void loadTMCollections()
  void loadTermBases()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<template>
  <div>
    <div v-if="showUploadForm" class="upload-panel">
      <div class="section-title">{{ t('taskList.uploadSection') }}</div>
      <div class="upload-form upload-form--inline upload-form--task">
        <label class="field">
          <span class="field__label">任务文件</span>
          <input id="upload-file" class="field__control" type="file" :accept="supportedTaskFileAccept" @change="onFileChange" />
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

        <label class="field field--collections">
          <span class="field__label">{{ t('taskList.fields.collections') }}</span>
          <select
            v-model="selectedCollectionIds"
            class="field__control field__control--multi"
            multiple
            :disabled="loadingCollections || tmCollections.length === 0"
          >
            <option v-for="collection in tmCollections" :key="collection.id" :value="collection.id">
              {{ collection.name }}（{{ formatLanguagePair(collection.source_language, collection.target_language) }} / {{ collection.entry_count }} 条）
            </option>
          </select>
          <span class="hint-text">
            {{ tmCollections.length ? t('taskList.hints.collections') : t('taskList.hints.noCollections') }}
          </span>
        </label>

        <label class="field field--compact">
          <span class="field__label">{{ t('taskList.fields.termBase') }}</span>
          <select
            v-model="selectedTermBaseId"
            class="field__control"
            :disabled="loadingTermBases || termBases.length === 0"
          >
            <option :value="null">{{ t('taskList.hints.noTermBase') }}</option>
            <option v-for="termBase in termBases" :key="termBase.id" :value="termBase.id">
              {{ termBase.name }}（{{ formatLanguagePair(termBase.source_language, termBase.target_language) }} / {{ termBase.entry_count }} 条）
            </option>
          </select>
        </label>

        <button
          class="button button--primary"
          type="button"
          :disabled="taskStore.uploading.active || selectedCollectionIds.length === 0 || tmCollections.length === 0"
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
            <div class="progress-bar__fill" :style="{ width: `${taskStore.uploading.percent}%` }" />
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
            <button class="button" type="button" @click="showUploadForm = !showUploadForm">
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
            :columns="columns"
            :data="pagedTasks"
            :loading="taskStore.loading"
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
                class="text-link task-link"
                type="button"
                @click="router.push({ name: 'workbench', params: { id: row.id } })"
              >
                {{ row.filename }}
              </button>
            </template>

            <template #status="{ row }">
              <span class="project-status" :class="`project-status--${getFileStatusMeta(row.status).tone}`">
                {{ getFileStatusMeta(row.status).label }}
              </span>
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
                  @click="router.push({ name: 'workbench', params: { id: row.id } })"
                >
                  <ArrowRight :size="16" />
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
                    <button type="button" @click="router.push({ name: 'workbench', params: { id: row.id } }); closeActionMenu()">
                      {{ t('taskList.actions.details') }}
                    </button>
                    <button
                      type="button"
                      @click="openImportDialog(row); closeActionMenu()"
                    >
                      {{ t('taskList.actions.importResources') }}
                    </button>
                    <button
                      class="is-danger"
                      type="button"
                      @click="removeTask(row.id, row.filename); closeActionMenu()"
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
  </div>
</template>

<style scoped>
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

.task-link {
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
</style>
