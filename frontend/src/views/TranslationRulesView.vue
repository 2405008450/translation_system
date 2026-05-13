<script setup lang="ts">
import axios from 'axios'
import {
  FileText,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  Search,
  Trash2,
  Upload,
} from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { http } from '../api/http'
import DataTable from '../components/DataTable.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import { useConfirm } from '../composables/useConfirm'
import { usePageHeader } from '../composables/usePageHeader'
import { useToast } from '../composables/useToast'
import type { GuidelineTemplateDetail, GuidelineTemplateSummary } from '../types/api'

const confirm = useConfirm()
const toast = useToast()

const loading = ref(false)
const saving = ref(false)
const deleting = ref(false)
const importing = ref(false)
const templates = ref<GuidelineTemplateSummary[]>([])
const selectedTemplate = ref<GuidelineTemplateDetail | null>(null)
const selectedTemplateId = ref('')
const editorContent = ref('')
const searchQuery = ref('')
const pageMessage = ref('')
const ruleFileInputRef = ref<HTMLInputElement | null>(null)

const columns: DataTableColumn[] = [
  { key: 'name', label: '规则名称', sortable: true },
  { key: 'content_preview', label: '内容预览' },
  { key: 'updated_at', label: '更新时间', width: '170px', sortable: true },
  { key: 'size_bytes', label: '大小', width: '90px', align: 'right', sortable: true },
]

const filteredTemplates = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  if (!keyword) {
    return templates.value
  }
  return templates.value.filter((template) => (
    template.name.toLowerCase().includes(keyword)
    || template.filename.toLowerCase().includes(keyword)
    || template.content_preview.toLowerCase().includes(keyword)
  ))
})

const selectedHasChanges = computed(() => (
  selectedTemplate.value ? editorContent.value !== selectedTemplate.value.content : false
))

const editorTitle = computed(() => selectedTemplate.value?.name || '选择翻译规则')

usePageHeader(() => ({
  title: '翻译规则',
  description: '维护可复用的 Markdown 翻译规则，供预翻译和工作台 AI 修正时选择。',
  breadcrumbs: [
    { label: '语言资产' },
    { label: '翻译规则' },
  ],
}))

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '--'
  }
  return date.toLocaleString('zh-CN', { hour12: false })
}

function formatSize(bytes: number) {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  return `${(bytes / 1024).toFixed(1)} KB`
}

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

async function loadTemplates() {
  loading.value = true
  pageMessage.value = ''
  try {
    const { data } = await http.get<GuidelineTemplateSummary[]>('/guideline-templates')
    templates.value = data
    if (selectedTemplateId.value && !data.some((template) => template.id === selectedTemplateId.value)) {
      selectedTemplate.value = null
      selectedTemplateId.value = ''
      editorContent.value = ''
    }
  } catch (error) {
    pageMessage.value = getErrorMessage(error, '翻译规则加载失败。')
  } finally {
    loading.value = false
  }
}

async function selectTemplate(template: GuidelineTemplateSummary) {
  if (selectedHasChanges.value) {
    const confirmed = await confirm({
      title: '切换翻译规则',
      message: '当前规则有未保存修改，切换后会丢失这些修改。是否继续？',
      confirmText: '继续切换',
    })
    if (!confirmed) {
      return
    }
  }

  selectedTemplateId.value = template.id
  pageMessage.value = ''
  try {
    const { data } = await http.get<GuidelineTemplateDetail>(`/guideline-templates/${template.id}`)
    selectedTemplate.value = data
    editorContent.value = data.content
  } catch (error) {
    pageMessage.value = getErrorMessage(error, '翻译规则详情加载失败。')
  }
}

async function saveSelectedTemplate() {
  if (!selectedTemplate.value || saving.value) {
    return
  }
  saving.value = true
  pageMessage.value = ''
  try {
    const { data } = await http.put<GuidelineTemplateDetail>(
      `/guideline-templates/${selectedTemplate.value.id}`,
      { content: editorContent.value },
    )
    selectedTemplate.value = data
    editorContent.value = data.content
    await loadTemplates()
    toast.success('翻译规则已保存。')
  } catch (error) {
    pageMessage.value = getErrorMessage(error, '翻译规则保存失败。')
  } finally {
    saving.value = false
  }
}

async function deleteSelectedTemplate() {
  if (!selectedTemplate.value || deleting.value) {
    return
  }

  const name = selectedTemplate.value.name
  const id = selectedTemplate.value.id
  const confirmed = await confirm({
    title: '删除翻译规则',
    message: `确定删除“${name}”吗？删除后预翻译和工作台将不能再选择这条规则。`,
    confirmText: '删除',
    danger: true,
  })
  if (!confirmed) {
    return
  }

  deleting.value = true
  pageMessage.value = ''
  try {
    await http.delete(`/guideline-templates/${id}`)
    selectedTemplate.value = null
    selectedTemplateId.value = ''
    editorContent.value = ''
    await loadTemplates()
    toast.success('翻译规则已删除。')
  } catch (error) {
    pageMessage.value = getErrorMessage(error, '翻译规则删除失败。')
  } finally {
    deleting.value = false
  }
}

function openImportPicker() {
  ruleFileInputRef.value?.click()
}

async function importTemplate(event: Event) {
  const fileInput = event.target as HTMLInputElement
  const file = fileInput.files?.[0]
  fileInput.value = ''
  if (!file) {
    return
  }

  importing.value = true
  pageMessage.value = ''
  try {
    const formData = new FormData()
    formData.append('file', file)
    const { data } = await http.post<GuidelineTemplateDetail>('/guideline-templates/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    await loadTemplates()
    await selectTemplate(data)
    toast.success('翻译规则已导入。')
  } catch (error) {
    pageMessage.value = getErrorMessage(error, '翻译规则导入失败。')
  } finally {
    importing.value = false
  }
}

onMounted(() => {
  void loadTemplates()
})
</script>

<template>
  <div class="translation-rules-page">
    <section class="table-page translation-rules-list">
      <div class="table-toolbar translation-rules-toolbar">
        <div class="table-toolbar__left">
          <div class="table-page__search">
            <Search :size="14" class="table-page__search-icon" />
            <input
              v-model="searchQuery"
              class="table-page__search-input"
              type="text"
              placeholder="搜索规则名称、文件名或内容..."
            />
          </div>
          <span class="table-toolbar__summary">总数：{{ filteredTemplates.length }}</span>
        </div>

        <div class="table-toolbar__right">
          <input
            ref="ruleFileInputRef"
            class="sr-only"
            type="file"
            accept=".md,.markdown,.txt"
            @change="importTemplate"
          />
          <button class="button button--primary" type="button" :disabled="importing" @click="openImportPicker">
            <Loader2 v-if="importing" class="lucide-spin" :size="14" />
            <Upload v-else :size="14" />
            {{ importing ? '导入中...' : '导入规则' }}
          </button>
          <button class="button" type="button" :disabled="loading" @click="loadTemplates">
            <Loader2 v-if="loading" class="lucide-spin" :size="14" />
            <RefreshCw v-else :size="14" />
            {{ loading ? '刷新中...' : '刷新' }}
          </button>
        </div>
      </div>

      <p v-if="pageMessage" class="form-message is-error translation-rules-message">{{ pageMessage }}</p>

      <div class="table-page__body">
        <DataTable
          :columns="columns"
          :data="filteredTemplates"
          :loading="loading"
          :show-index="true"
          empty-text="当前还没有翻译规则"
        >
          <template #name="{ row }">
            <button
              class="text-link translation-rule-link"
              :class="{ 'is-active': row.id === selectedTemplateId }"
              type="button"
              @click="selectTemplate(row as GuidelineTemplateSummary)"
            >
              <FileText :size="14" />
              <span>{{ row.name }}</span>
            </button>
            <div class="translation-rule-filename">{{ row.filename }}</div>
          </template>

          <template #content_preview="{ row }">
            <span class="translation-rule-preview">{{ row.content_preview || '--' }}</span>
          </template>

          <template #updated_at="{ row }">
            <span>{{ formatDate(row.updated_at) }}</span>
          </template>

          <template #size_bytes="{ row }">
            <span>{{ formatSize(Number(row.size_bytes || 0)) }}</span>
          </template>

          <template #actions="{ row }">
            <button
              class="data-table__actions-btn"
              type="button"
              title="编辑"
              @click="selectTemplate(row as GuidelineTemplateSummary)"
            >
              <Pencil :size="14" />
            </button>
          </template>
        </DataTable>
      </div>
    </section>

    <section class="panel translation-rule-editor">
      <div class="translation-rule-editor__head">
        <div>
          <div class="section-title section-title--tight">{{ editorTitle }}</div>
          <p class="panel-subtitle">
            {{ selectedTemplate ? selectedTemplate.filename : '从左侧选择一条规则后进行微调。' }}
          </p>
        </div>
        <div class="translation-rule-editor__actions">
          <button
            class="button"
            type="button"
            :disabled="!selectedTemplate || saving || !selectedHasChanges"
            @click="saveSelectedTemplate"
          >
            <Loader2 v-if="saving" class="lucide-spin" :size="14" />
            <Save v-else :size="14" />
            {{ saving ? '保存中...' : '保存' }}
          </button>
          <button
            class="button button--danger"
            type="button"
            :disabled="!selectedTemplate || deleting"
            @click="deleteSelectedTemplate"
          >
            <Loader2 v-if="deleting" class="lucide-spin" :size="14" />
            <Trash2 v-else :size="14" />
            删除
          </button>
        </div>
      </div>

      <textarea
        v-model="editorContent"
        class="field__control translation-rule-editor__textarea"
        :disabled="!selectedTemplate"
        placeholder="支持 Markdown。首个标题会作为规则名称显示，例如：# 医疗器械翻译规则"
      />

      <p class="hint-text">
        建议使用一级标题作为规则名称；保存后预翻译和工作台会读取最新内容。
      </p>
    </section>
  </div>
</template>

<style scoped>
.translation-rules-page {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(360px, 0.9fr);
  gap: 16px;
  align-items: start;
}

.translation-rules-toolbar {
  padding: 16px 20px 10px;
  flex-wrap: wrap;
}

.translation-rules-message {
  margin: 0 20px 12px;
}

.translation-rule-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
  font-weight: 600;
}

.translation-rule-link span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.translation-rule-link.is-active {
  color: var(--brand-700);
}

.translation-rule-filename {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 12px;
}

.translation-rule-preview {
  display: -webkit-box;
  max-width: 420px;
  overflow: hidden;
  color: var(--text-secondary);
  line-height: 1.45;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  white-space: normal;
}

.translation-rule-editor {
  position: sticky;
  top: 72px;
  display: grid;
  gap: 14px;
}

.translation-rule-editor__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.translation-rule-editor__actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.translation-rule-editor__textarea {
  min-height: calc(100vh - 250px);
  resize: vertical;
  font-family: "Consolas", "Microsoft YaHei", monospace;
  line-height: 1.65;
}

@media (max-width: 1120px) {
  .translation-rules-page {
    grid-template-columns: 1fr;
  }

  .translation-rule-editor {
    position: static;
  }

  .translation-rule-editor__textarea {
    min-height: 420px;
  }
}

@media (max-width: 720px) {
  .translation-rule-editor__head {
    flex-direction: column;
  }

  .translation-rule-editor__actions {
    width: 100%;
    justify-content: stretch;
  }

  .translation-rule-editor__actions .button {
    flex: 1 1 0;
  }
}
</style>
