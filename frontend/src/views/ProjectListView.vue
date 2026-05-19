<script setup lang="ts">
import axios from 'axios'
import {
  Copy,
  Database,
  Filter,
  Flag,
  Loader2,
  MoreHorizontal,
  Plus,
  Search,
  Settings2,
  Trash2,
} from 'lucide-vue-next'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import Modal from '../components/base/Modal.vue'
import DataTable from '../components/DataTable.vue'
import IssueMarkerDialog from '../components/IssueMarkerDialog.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import Pagination from '../components/Pagination.vue'
import { useConfirm } from '../composables/useConfirm'
import { useToast } from '../composables/useToast'
import { getFileStatusMeta } from '../constants/status'
import { getProgressStyle, isProgressComplete } from '../utils/progress'
import type { IssueMarker } from '../types/api'

interface ProjectItem {
  id: string
  name: string
  filename: string
  status: string
  progress: number
  file_count: number
  issue_count: number
  open_issue_count: number
  total_segments: number
  translated_segments: number
  source_language: string | null
  target_language: string | null
  creator: string | null
  deadline: string | null
  access_level: string | null
  created_at: string
  updated_at: string
}

interface ProjectListResponse {
  items: ProjectItem[]
  total: number
  skip: number
  limit: number
}

interface ProjectCreateResponse {
  id: string
}

const confirm = useConfirm()
const toast = useToast()
const router = useRouter()
const { t } = useI18n()

const loading = ref(false)
const projects = ref<ProjectItem[]>([])
const totalCount = ref(0)
const currentPage = ref(1)
const pageSize = ref(10)
const searchQuery = ref('')
const selectedIds = ref(new Set<string>())
const sortKey = ref('')
const sortOrder = ref<'asc' | 'desc'>('desc')
const pageError = ref('')

const showCreateDialog = ref(false)
const creating = ref(false)
const formError = ref('')
const showIssueDialog = ref(false)
const issueTarget = ref<ProjectItem | null>(null)

const defaultForm = () => ({
  name: '',
  deadline: '',
  access_level: 'team' as 'team' | 'private' | 'public',
})

const form = reactive(defaultForm())

const accessOptions = computed(() => ([
  { value: 'team', label: t('projectList.form.team') },
  { value: 'private', label: t('projectList.form.private') },
  { value: 'public', label: t('projectList.form.public') },
]))

const columns = computed<DataTableColumn[]>(() => ([
  { key: 'filename', label: t('common.actions.details'), sortable: true },
  { key: 'status', label: t('projectList.status.current'), width: '110px' },
  { key: 'progress', label: t('projectList.status.progress'), width: '180px' },
  { key: 'file_count', label: t('projectDetail.base.fileCount'), width: '110px', align: 'right' },
  { key: 'open_issue_count', label: t('issueMarker.list.title'), width: '120px' },
  { key: 'access_level', label: t('projectList.status.access'), width: '110px' },
  { key: 'creator', label: t('projectList.status.creator'), width: '130px' },
  { key: 'created_at', label: t('projectList.summaries.createdAt'), width: '120px', sortable: true },
  { key: 'deadline', label: t('projectList.form.deadline'), width: '120px', sortable: true },
]))

const indexOffset = computed(() => (currentPage.value - 1) * pageSize.value)

async function loadProjects() {
  loading.value = true
  pageError.value = ''
  try {
    const skip = (currentPage.value - 1) * pageSize.value
    const { data } = await http.get<ProjectListResponse>('/projects', {
      params: { skip, limit: pageSize.value, search: searchQuery.value.trim() },
    })
    projects.value = data.items
    totalCount.value = data.total
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || t('projectList.errors.load'))
      return
    }
    pageError.value = error instanceof Error ? error.message : t('projectList.errors.load')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, defaultForm())
  formError.value = ''
}

function openCreateDialog() {
  resetForm()
  showCreateDialog.value = true
}

async function createProject() {
  if (!form.name.trim()) {
    formError.value = t('projectList.errors.requiredName')
    return
  }

  creating.value = true
  formError.value = ''
  try {
    const { data } = await http.post<ProjectCreateResponse>('/projects', {
      name: form.name.trim(),
      deadline: form.deadline || null,
      access_level: form.access_level,
    })
    showCreateDialog.value = false
    resetForm()
    toast.success(t('projectList.messages.created'))
    await router.push({
      name: 'project-detail',
      params: { id: data.id },
    })
  } catch (error) {
    if (axios.isAxiosError(error)) {
      formError.value = String(error.response?.data?.detail || t('projectList.errors.create'))
      return
    }
    formError.value = error instanceof Error ? error.message : t('projectList.errors.create')
  } finally {
    creating.value = false
  }
}

async function deleteProjectIds(ids: string[], successMessage: string) {
  pageError.value = ''
  try {
    await Promise.all(ids.map((id) => http.delete(`/projects/${id}`)))
    selectedIds.value = new Set()
    await loadProjects()
    toast.success(successMessage)
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || t('projectList.errors.delete'))
      return
    }
    pageError.value = error instanceof Error ? error.message : t('projectList.errors.delete')
  }
}

async function deleteSelected() {
  if (selectedIds.value.size === 0) {
    return
  }

  const confirmed = await confirm({
    title: t('projectList.actions.delete'),
    message: t('projectList.messages.deleteSelectedConfirm', { count: selectedIds.value.size }),
    confirmText: t('common.actions.delete'),
    danger: true,
  })

  if (!confirmed) {
    return
  }

  await deleteProjectIds(Array.from(selectedIds.value), t('projectList.messages.deletedMany'))
}

async function deleteRow(row: ProjectItem) {
  const confirmed = await confirm({
    title: t('projectList.actions.delete'),
    message: t('projectList.messages.deleteOneConfirm', { name: row.filename }),
    confirmText: t('common.actions.delete'),
    danger: true,
  })

  if (!confirmed) {
    return
  }

  await deleteProjectIds([row.id], t('projectList.messages.deletedOne', { name: row.filename }))
}

function formatDate(value: string | null) {
  if (!value) {
    return { date: '--', time: '' }
  }
  const date = new Date(value)
  return {
    date: date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }),
    time: date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }),
  }
}

function getAccessLabel(level: string | null) {
  const labels: Record<string, string> = {
    team: t('projectList.form.team'),
    private: t('projectList.form.private'),
    public: t('projectList.form.public'),
  }
  return labels[level || 'team'] || t('projectList.form.team')
}

function getStatusClass(status: string) {
  const meta = getFileStatusMeta(status)
  return `project-status--${meta.tone}`
}

function handleSort(key: string, order: 'asc' | 'desc') {
  sortKey.value = key
  sortOrder.value = order
}

function handleSelect(ids: Set<string>) {
  selectedIds.value = ids
}

function goToAssets() {
  void router.push({ name: 'tm' })
}

function openIssueDialog(row: ProjectItem) {
  issueTarget.value = row
  showIssueDialog.value = true
}

async function handleIssueSaved(_marker: IssueMarker) {
  showIssueDialog.value = false
  issueTarget.value = null
  toast.success(t('issueMarker.messages.saved'))
  await loadProjects()
}

let searchTimer: ReturnType<typeof setTimeout> | null = null

watch(searchQuery, () => {
  if (searchTimer) {
    clearTimeout(searchTimer)
  }
  searchTimer = setTimeout(() => {
    currentPage.value = 1
    void loadProjects()
  }, 300)
})

watch([currentPage, pageSize], () => {
  void loadProjects()
})

onMounted(() => {
  void loadProjects()
})
</script>

<template>
  <div class="table-page">
    <div class="table-toolbar table-toolbar--page">
      <div class="table-toolbar__left">
        <div class="table-page__search">
          <Search :size="14" class="table-page__search-icon" />
          <input
            v-model="searchQuery"
            class="table-page__search-input"
            type="text"
            :placeholder="t('projectList.searchPlaceholder')"
          />
        </div>
        <span class="table-toolbar__summary">{{ t('projectList.total', { total: totalCount }) }}</span>
      </div>
      <div class="table-toolbar__right">
        <button class="button button--primary" data-testid="project-create-button" type="button" @click="openCreateDialog">
          <Plus :size="14" />
          {{ t('projectList.create') }}
        </button>
        <button
          class="button"
          type="button"
          :title="t('projectList.importAssetsTitle')"
          @click="goToAssets"
        >
          <Database :size="14" />
          {{ t('projectList.importAssets') }}
        </button>
        <button class="button" type="button" disabled :title="t('common.comingSoon')">
          <Copy :size="14" />
          {{ t('projectList.fromTemplate') }}
        </button>
        <button class="button" type="button" disabled :title="t('common.comingSoon')">
          <Filter :size="14" />
          {{ t('projectList.filter') }}
        </button>
        <button class="button" type="button" disabled :title="t('common.comingSoon')">
          <Settings2 :size="14" />
          {{ t('projectList.columns') }}
        </button>
      </div>
    </div>

    <div v-if="selectedIds.size > 0" class="table-bulk-bar">
      <span>{{ t('projectList.selectedSummary', { count: selectedIds.size }) }}</span>
      <button class="button button--danger" type="button" @click="deleteSelected">
        <Trash2 :size="14" />
        {{ t('projectList.deleteSelected') }}
      </button>
    </div>

    <p v-if="pageError" class="form-message is-error table-page__message">{{ pageError }}</p>

    <div class="table-page__body">
      <DataTable
        test-id="project-table"
        row-test-id-prefix="project-row"
        :columns="columns"
        :data="projects"
        :loading="loading"
        :selectable="true"
        :selected-ids="selectedIds"
        :sort-key="sortKey"
        :sort-order="sortOrder"
        :show-index="true"
        :index-offset="indexOffset"
        :empty-text="t('projectList.empty')"
        @sort="handleSort"
        @select="handleSelect"
      >
        <template #filename="{ row }">
          <button
            class="text-link project-link"
            type="button"
            @click="router.push({ name: 'project-detail', params: { id: row.id } })"
          >
            {{ row.filename }}
          </button>
        </template>

        <template #status="{ row }">
          <span class="project-status" :class="getStatusClass(row.status)">
            {{ getFileStatusMeta(row.status).label }}
          </span>
        </template>

        <template #progress="{ row }">
          <div class="progress-bar">
            <div class="progress-bar__track">
              <div
                class="progress-bar__fill"
                :class="{ 'is-complete': isProgressComplete(row.progress) }"
                :style="getProgressStyle(row.progress, row.status)"
              />
            </div>
            <span class="progress-bar__text">{{ row.progress }}%</span>
          </div>
        </template>

        <template #file_count="{ row }">
          <span>{{ row.file_count }}</span>
        </template>

        <template #open_issue_count="{ row }">
          <button
            class="issue-badge"
            :class="{ 'is-active': Number(row.open_issue_count || 0) > 0 }"
            type="button"
            :title="t('issueMarker.actions.open')"
            @click="openIssueDialog(row as ProjectItem)"
          >
            <Flag :size="13" />
            {{ Number(row.open_issue_count || 0) > 0 ? row.open_issue_count : t('common.none') }}
          </button>
        </template>

        <template #access_level="{ row }">
          <span>{{ getAccessLabel(row.access_level) }}</span>
        </template>

        <template #creator="{ row }">
          <span>{{ row.creator || '--' }}</span>
        </template>

        <template #created_at="{ row }">
          <div class="date-cell">
            {{ formatDate(row.created_at).date }}<br>{{ formatDate(row.created_at).time }}
          </div>
        </template>

        <template #deadline="{ row }">
          <div class="date-cell">
            {{ formatDate(row.deadline).date }}<br>{{ formatDate(row.deadline).time }}
          </div>
        </template>

        <template #actions="{ row }">
          <div class="project-row-actions">
            <button
              class="data-table__actions-btn"
              type="button"
              :title="t('issueMarker.actions.open')"
              :aria-label="t('issueMarker.actions.open')"
              @click="openIssueDialog(row as ProjectItem)"
            >
              <Flag :size="14" />
            </button>
            <button
              class="data-table__actions-btn"
              type="button"
              :title="t('projectList.actions.view')"
              :aria-label="t('projectList.actions.view')"
              @click="router.push({ name: 'project-detail', params: { id: row.id } })"
            >
              <MoreHorizontal :size="16" />
            </button>
            <button
              class="data-table__actions-btn"
              type="button"
              :title="t('projectList.actions.delete')"
              :aria-label="t('projectList.actions.delete')"
              @click="deleteRow(row as ProjectItem)"
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

    <Modal
      :open="showCreateDialog"
      :title="t('projectList.createDialogTitle')"
      :description="t('projectList.createDialogDescription')"
      width="min(560px, calc(100vw - 32px))"
      @close="showCreateDialog = false"
    >
      <div class="form-grid" data-testid="project-create-dialog">
        <label class="field field--full">
          <span class="field__label">{{ t('projectList.form.name') }} <span class="field__required">*</span></span>
          <input
            v-model="form.name"
            class="field__control"
            data-testid="project-create-name"
            type="text"
            :placeholder="t('projectList.form.namePlaceholder')"
            maxlength="200"
          />
        </label>

        <label class="field">
          <span class="field__label">{{ t('projectList.form.deadline') }}</span>
          <input
            v-model="form.deadline"
            class="field__control"
            type="datetime-local"
          />
        </label>

        <label class="field">
          <span class="field__label">{{ t('projectList.form.accessLevel') }}</span>
          <select v-model="form.access_level" class="field__control">
            <option v-for="option in accessOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
      </div>

      <p class="form-hint">
        {{ t('projectList.form.hint') }}
      </p>

      <p v-if="formError" class="form-message is-error">{{ formError }}</p>

      <template #footer>
        <button class="button" type="button" @click="showCreateDialog = false">{{ t('common.actions.cancel') }}</button>
        <button
          class="button button--primary"
          data-testid="project-create-submit"
          type="button"
          :disabled="creating"
          @click="createProject"
        >
          <Loader2 v-if="creating" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ creating ? t('projectList.actions.creating') : t('projectList.actions.createSubmit') }}
        </button>
      </template>
    </Modal>

    <IssueMarkerDialog
      :open="showIssueDialog"
      :project-id="issueTarget?.id ?? null"
      :context-label="issueTarget?.filename ?? ''"
      @close="showIssueDialog = false"
      @saved="handleIssueSaved"
    />
  </div>
</template>

<style scoped>
.table-toolbar--page {
  padding: 18px 20px 10px;
}

.table-toolbar__summary {
  color: var(--text-muted);
  font-size: 13px;
}

.table-bulk-bar {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin: 0 20px 12px;
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--brand-050);
}

.table-page__message {
  margin: 0 20px 8px;
}

.project-link {
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
}

.project-row-actions {
  display: flex;
  gap: 4px;
  justify-content: center;
}

.issue-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 24px;
  padding: 3px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 999px;
  background: var(--surface-muted);
  color: var(--text-muted);
  font-size: 12px;
  box-shadow: none;
}

.issue-badge.is-active {
  border-color: color-mix(in srgb, var(--state-warning) 45%, var(--line-soft));
  background: var(--state-warning-bg);
  color: var(--state-warning);
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

.field--full {
  grid-column: 1 / -1;
}

.field__required {
  color: var(--state-danger);
}

.form-hint {
  margin: 12px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}

@media (max-width: 720px) {
  .table-bulk-bar {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
