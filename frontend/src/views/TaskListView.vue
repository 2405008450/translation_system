<script setup lang="ts">
import axios from 'axios'
import {
  ArrowRight,
  Download,
  FolderOpen,
  MoreHorizontal,
  Search,
  Settings2,
  Upload,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import { listVisibleMergeViews } from '../api/mergeViews'
import DataTable from '../components/DataTable.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import Pagination from '../components/Pagination.vue'
import ResourceImportDialog from '../components/ResourceImportDialog.vue'
import WorkflowProgressSummary from '../components/WorkflowProgressSummary.vue'
import { useConfirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'
import { getFileStatusMeta } from '../constants/status'
import { useAuthStore } from '../stores/auth'
import type { MergeView, WorkflowProgress } from '../types/api'
import { getProgressStyle, isProgressComplete } from '../utils/progress'

interface ProjectRow {
  id: string
  project_id: string | null
  project_name: string | null
  filename: string
  status: string
  progress: number
  file_count?: number
  total_segments: number
  translated_segments: number
  confirmed_segments?: number
  pretranslated_segments?: number
  pretranslation_progress?: number
  workflow_progress?: WorkflowProgress[]
  source_language: string | null
  target_language: string | null
  creator: string | null
  deadline: string | null
  access_level: string | null
  can_manage?: boolean
  can_write?: boolean
  created_at: string
  updated_at: string
}

type MainTab = 'tasks' | 'views' | 'performance'
type SubTab = 'all' | 'incomplete'
type ResourceImportTab = 'tm' | 'term'

const confirm = useConfirm()
const toast = useToast()
const router = useRouter()
const authStore = useAuthStore()
const { t } = useI18n()

const mainTab = ref<MainTab>('tasks')
const subTab = ref<SubTab>('all')
const pageError = ref('')
const currentPage = ref(1)
const pageSize = ref(50)
const selectedIds = ref(new Set<string>())
const sortKey = ref('')
const sortOrder = ref<'asc' | 'desc'>('desc')
const searchQuery = ref('')
const projects = ref<ProjectRow[]>([])
const projectsLoading = ref(false)
const visibleMergeViews = ref<MergeView[]>([])
const mergeViewsLoading = ref(false)
const mergeViewError = ref('')
let searchTimer: ReturnType<typeof setTimeout> | null = null
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
  { key: 'filename', label: t('taskList.columns.filename'), width: '330px', sortable: true },
  { key: 'status', label: t('taskList.columns.status'), width: '88px' },
  { key: 'progress', label: t('projectDetail.files.columns.progress'), width: '150px' },
  { key: 'pretranslation_progress', label: t('projectDetail.files.columns.pretranslationProgress'), width: '150px' },
  { key: 'updated_at', label: t('taskList.columns.updatedAt'), width: '132px', sortable: true },
]))

function compactFilename(filename: string, maxLength = 54) {
  if (filename.length <= maxLength) {
    return filename
  }

  const dotIndex = filename.lastIndexOf('.')
  const extension = dotIndex > 0 && filename.length - dotIndex <= 12
    ? filename.slice(dotIndex)
    : ''
  const body = extension ? filename.slice(0, -extension.length) : filename
  const headLength = Math.max(18, Math.floor((maxLength - extension.length - 3) * 0.55))
  const tailLength = Math.max(10, maxLength - extension.length - headLength - 3)

  return `${body.slice(0, headLength)}...${body.slice(-tailLength)}${extension}`
}

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

function getTaskMetaText(row: ProjectRow) {
  return [
    row.project_name || t('common.notSet'),
    `${t('projectList.status.totalSegments')} ${Number(row.total_segments || 0)}`,
  ].join(' · ')
}

function getMergeViewMetaText(view: MergeView) {
  return [
    view.project_name || '未命名项目',
    `${Number(view.file_count || 0)} 个文件`,
    view.creator_name ? `创建人 ${view.creator_name}` : '',
  ].filter(Boolean).join(' · ')
}

function getMergeViewFileText(view: MergeView) {
  const count = Number(view.file_count || view.file_ids?.length || 0)
  return count > 0 ? `可打开 ${count} 个文件` : '暂无可访问文件'
}

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

async function loadMergeViews() {
  mergeViewError.value = ''
  mergeViewsLoading.value = true
  try {
    const data = await listVisibleMergeViews()
    visibleMergeViews.value = data.items
  } catch (error) {
    mergeViewError.value = getErrorMessage(error, '合并视图加载失败。')
  } finally {
    mergeViewsLoading.value = false
  }
}

async function loadProjects() {
  pageError.value = ''
  projectsLoading.value = true
  try {
    const { data } = await http.get<ProjectRow[]>('/file-records', {
      params: {
        skip: 0,
        limit: 200,
      },
    })
    const keyword = searchQuery.value.trim().toLowerCase()
    projects.value = keyword
      ? data.filter((item) => (
          (item.project_name || '').toLowerCase().includes(keyword)
          || item.filename.toLowerCase().includes(keyword)
        ))
      : data
  } catch (error) {
    pageError.value = getErrorMessage(error, t('taskList.errors.load'))
  } finally {
    projectsLoading.value = false
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
    await http.delete(`/file-records/${projectId}`)
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

function getFileDisplayProgress(row: ProjectRow) {
  return Number(row.progress || 0)
}

function getFileDisplayProgressStatus(row: ProjectRow) {
  return String(row.status || '')
}

function getFileWorkflowProgress(row: ProjectRow) {
  return row.workflow_progress || []
}

function getFilePretranslationProgress(row: ProjectRow) {
  return Number(row.pretranslation_progress || 0)
}

function getFilePretranslationProgressStatus(row: ProjectRow) {
  return String(row.status || '')
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
      if (key === 'progress' || key === 'pretranslation_progress' || key === 'file_count' || key === 'total_segments') {
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
    name: 'workbench-focus',
    params: { id: row.id },
    query: {
      from: 'project',
      ...(row.project_id ? { pid: row.project_id, parent: 'tasks' } : {}),
    },
  })
}

function openMergeView(view: MergeView) {
  void router.push({
    name: 'merge-view-focus',
    params: { viewId: view.id },
    query: {
      from: 'tasks',
      ...(view.project_id ? { pid: view.project_id } : {}),
    },
  })
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

watch(mainTab, (tab) => {
  if (tab === 'views' && visibleMergeViews.value.length === 0 && !mergeViewsLoading.value) {
    void loadMergeViews()
  }
})

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
  void loadProjects()
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
            :class="{ 'is-active': mainTab === 'views' }"
            type="button"
            @click="mainTab = 'views'"
          >
            合并视图
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
            <button
              v-if="authStore.isAdmin"
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

        <p v-if="pageError" class="form-message is-error task-page__message">{{ pageError }}</p>

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
              <div class="task-main-cell">
                <button
                  class="text-link project-link"
                  type="button"
                  :title="row.filename"
                  @click="openProjectDetail(row as ProjectRow)"
                >
                  {{ compactFilename(row.filename) }}
                </button>
                <span class="task-main-cell__meta" :title="getTaskMetaText(row as ProjectRow)">
                  {{ getTaskMetaText(row as ProjectRow) }}
                </span>
              </div>
            </template>

            <template #status="{ row }">
              <span class="project-status" :class="getStatusClass(row.status)">
                {{ getFileStatusMeta(row.status).label }}
              </span>
            </template>

            <template #progress="{ row }">
              <div class="task-file-progress">
                <WorkflowProgressSummary
                  compact
                  :progress="getFileDisplayProgress(row as ProjectRow)"
                  :status="getFileDisplayProgressStatus(row as ProjectRow)"
                  :workflow-progress="getFileWorkflowProgress(row as ProjectRow)"
                  :label="t('common.progress.total')"
                  :detail-title="t('common.progress.workflowDetail')"
                />
              </div>
            </template>

            <template #pretranslation_progress="{ row }">
              <div class="task-file-progress">
                <div class="progress-bar">
                  <div class="progress-bar__track">
                    <div
                      class="progress-bar__fill"
                      :class="{ 'is-complete': isProgressComplete(getFilePretranslationProgress(row as ProjectRow)) }"
                      :style="getProgressStyle(getFilePretranslationProgress(row as ProjectRow), getFilePretranslationProgressStatus(row as ProjectRow))"
                    />
                  </div>
                  <span class="progress-bar__text">{{ getFilePretranslationProgress(row as ProjectRow) }}%</span>
                </div>
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
                      v-if="authStore.isAdmin"
                      type="button"
                      @click="openImportDialog(row); closeActionMenu()"
                    >
                      {{ t('taskList.actions.importResources') }}
                    </button>
                    <button
                      v-if="row.can_manage"
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

      <template v-else-if="mainTab === 'views'">
        <div class="task-merge-views">
          <div class="task-merge-views__head">
            <div>
              <strong>合并视图</strong>
              <span>显示已授权且可重新打开的多文件工作台</span>
            </div>
            <button class="button" type="button" :disabled="mergeViewsLoading" @click="loadMergeViews">
              <FolderOpen :size="14" />
              刷新
            </button>
          </div>

          <p v-if="mergeViewError" class="form-message is-error task-page__message">{{ mergeViewError }}</p>

          <div v-if="mergeViewsLoading" class="empty-state task-merge-views__empty">
            正在加载合并视图...
          </div>
          <div v-else-if="visibleMergeViews.length === 0" class="empty-state task-merge-views__empty">
            暂无可打开的合并视图
          </div>
          <div v-else class="task-merge-view-list">
            <article v-for="view in visibleMergeViews" :key="view.id" class="task-merge-view-card">
              <div class="task-merge-view-card__main">
                <strong>{{ view.name }}</strong>
                <span>{{ getMergeViewMetaText(view) }}</span>
                <small>{{ getMergeViewFileText(view) }}</small>
              </div>
              <button
                class="button button--primary"
                type="button"
                :disabled="view.can_open === false"
                @click="openMergeView(view)"
              >
                <ArrowRight :size="14" />
                打开
              </button>
            </article>
          </div>
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

.task-merge-views {
  display: grid;
  gap: 12px;
  padding: 16px 20px 20px;
}

.task-merge-views__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.task-merge-views__head > div {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.task-merge-views__head strong {
  color: var(--text-primary);
  font-size: 15px;
}

.task-merge-views__head span {
  color: var(--text-muted);
  font-size: 13px;
}

.task-merge-views__empty {
  min-height: 220px;
}

.task-merge-view-list {
  display: grid;
  gap: 10px;
}

.task-merge-view-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.task-merge-view-card__main {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.task-merge-view-card__main strong,
.task-merge-view-card__main span,
.task-merge-view-card__main small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-merge-view-card__main strong {
  color: var(--text-primary);
  font-size: 14px;
}

.task-merge-view-card__main span,
.task-merge-view-card__main small {
  color: var(--text-muted);
  font-size: 12px;
}

.project-link {
  display: inline-block;
  max-width: 100%;
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
  overflow: hidden;
  text-align: left;
  text-overflow: ellipsis;
  vertical-align: middle;
  white-space: nowrap;
}

.task-main-cell,
.task-file-progress {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.task-main-cell__meta {
  display: block;
  overflow: hidden;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.table-page__body :deep(.data-table) {
  table-layout: fixed;
  min-width: 960px;
}

.table-page__body :deep(.data-table th),
.table-page__body :deep(.data-table td) {
  padding-right: 10px;
  padding-left: 10px;
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

@media (max-width: 720px) {
  .task-merge-view-card {
    align-items: stretch;
    flex-direction: column;
  }

  .task-merge-view-card .button {
    width: 100%;
    justify-content: center;
  }
}

</style>
