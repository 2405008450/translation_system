<script setup lang="ts">
import axios from 'axios'
import { Upload, Loader2, ArrowRight, Trash2, ChevronUp } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import { useAuthStore } from '../stores/auth'
import { useTaskStore } from '../stores/task'
import type { TMCollection } from '../types/api'

const authStore = useAuthStore()
const taskStore = useTaskStore()
const router = useRouter()

const selectedFile = ref<File | null>(null)
const threshold = ref(0.6)
const pageError = ref('')
const tmCollections = ref<TMCollection[]>([])
const loadingCollections = ref(false)
const selectedCollectionIds = ref<string[]>([])
const showFormatDetails = ref(false)

// 支持的文件格式分类（共30种格式）
const formatCategories = {
  office: {
    label: '办公文档',
    formats: ['docx', 'txt', 'pdf', 'pptx', 'xlsx'],
  },
  localization: {
    label: '本地化文件',
    formats: ['properties', 'po', 'pot', 'strings', 'yaml', 'yml', 'json', 'php'],
  },
  web: {
    label: '网页/排版',
    formats: ['html', 'htm', 'md', 'markdown', 'csv', 'srt'],
  },
  technical: {
    label: '技术写作',
    formats: ['dita', 'ditamap', 'xml', 'svg'],
  },
  bilingual: {
    label: '双语文件',
    formats: ['sdlxliff', 'txml', 'xliff', 'xlf', 'tmx'],
  },
  engineering: {
    label: '工程/设计',
    formats: ['dxf', 'idml', 'mif'],
  },
  archive: {
    label: '压缩包',
    formats: ['zip', 'rar'],
  },
}

// 所有支持的扩展名（用于 accept 属性）
const allExtensions = Object.values(formatCategories)
  .flatMap(cat => cat.formats)
  .map(ext => `.${ext}`)
  .join(',')

const commonFormatsText = 'docx, txt, pdf, html, json, yaml 等 30 种格式'

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function loadTasks() {
  pageError.value = ''
  try {
    await taskStore.fetchTasks()
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '任务列表加载失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '任务列表加载失败。'
  }
}

async function loadTMCollections() {
  loadingCollections.value = true
  try {
    const { data } = await http.get<TMCollection[]>('/tm/collections')
    tmCollections.value = data
    if (selectedCollectionIds.value.length === 0 && data.length > 0) {
      selectedCollectionIds.value = [data[0].id]
    }
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || 'TM 记忆库加载失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : 'TM 记忆库加载失败。'
  } finally {
    loadingCollections.value = false
  }
}

function onFileChange(event: Event) {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

async function uploadFile() {
  if (!selectedFile.value) {
    pageError.value = '请先选择要上传的 DOCX 文件。'
    return
  }
  if (selectedCollectionIds.value.length === 0) {
    pageError.value = '请选择本次任务要使用的 TM 记忆库。'
    return
  }

  pageError.value = ''
  try {
    const result = await taskStore.uploadTask(
      selectedFile.value,
      threshold.value,
      selectedCollectionIds.value,
    )
    selectedFile.value = null
    const fileInput = document.getElementById('upload-file') as HTMLInputElement | null
    if (fileInput) {
      fileInput.value = ''
    }
    await router.push({ name: 'workbench', params: { id: result.id } })
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '文档上传失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '文档上传失败。'
  }
}

async function removeTask(fileRecordId: string) {
  if (!window.confirm('确定删除这个任务吗？删除后无法恢复。')) {
    return
  }

  pageError.value = ''
  try {
    await taskStore.deleteTask(fileRecordId)
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '任务删除失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '任务删除失败。'
  }
}

onMounted(() => {
  void loadTasks()
  void loadTMCollections()
})
</script>

<template>
  <div class="content-stack">
    <section class="panel">
      <div class="section-title">上传新任务</div>
      <div class="upload-form upload-form--inline upload-form--task">
        <label class="field">
          <span class="field__label">
            上传文件
            <span class="format-hint">
              <span class="format-hint__summary">
                支持的文件类型：{{ commonFormatsText }}
                <button type="button" class="format-hint__toggle" @click.prevent="showFormatDetails = !showFormatDetails">
                  {{ showFormatDetails ? '收起' : '更多' }}
                  <ChevronUp :class="['format-hint__icon', { 'is-expanded': showFormatDetails }]" :size="14" />
                </button>
              </span>
              <Transition name="format-expand">
                <div v-if="showFormatDetails" class="format-hint__details">
                  <div v-for="(category, key) in formatCategories" :key="key" class="format-category">
                    <span class="format-category__label">{{ category.label }}：</span>
                    <span class="format-category__list">{{ category.formats.join(', ') }}</span>
                  </div>
                  <div class="format-note">
                    <span class="format-note__icon">ℹ️</span>
                    <span>RAR 压缩包导出时将转换为 ZIP 格式</span>
                  </div>
                </div>
              </Transition>
            </span>
          </span>
          <input id="upload-file" class="field__control" type="file" :accept="allExtensions" @change="onFileChange" />
        </label>

        <label class="field field--compact">
          <span class="field__label">模糊匹配阈值</span>
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
          <span class="field__label">匹配记忆库</span>
          <select
            v-model="selectedCollectionIds"
            class="field__control field__control--multi"
            multiple
            :disabled="loadingCollections || tmCollections.length === 0"
          >
            <option v-for="collection in tmCollections" :key="collection.id" :value="collection.id">
              {{ collection.name }}（{{ collection.entry_count }} 条）
            </option>
          </select>
          <span class="hint-text">
            {{ tmCollections.length ? '可多选，任务只会匹配选中的记忆库' : '请先在 TM 记忆库导入 Excel' }}
          </span>
        </label>

        <button
          class="button button--primary"
          type="button"
          :disabled="taskStore.uploading"
          @click="uploadFile"
        >
          <Loader2 v-if="taskStore.uploading" class="lucide-spin" />
          <Upload v-else />
          {{ taskStore.uploading ? '上传中...' : '上传并进入工作台' }}
        </button>
      </div>
    </section>

    <p v-if="pageError" class="form-message is-error">{{ pageError }}</p>

    <section class="panel">
      <div class="section-title">任务列表</div>
      <div v-if="taskStore.loading" class="empty-state">
        <Loader2 class="lucide-spin" :size="32" />
        任务列表加载中...
      </div>
      <div v-else-if="taskStore.tasks.length === 0" class="empty-state">当前还没有翻译任务</div>
      <div v-else class="task-list">
        <article v-for="task in taskStore.tasks" :key="task.id" class="task-item">
          <div class="task-item__main">
            <h2>{{ task.filename }}</h2>
            <div class="task-meta">
              <span>状态：{{ task.status }}</span>
              <span>创建：{{ formatTime(task.created_at) }}</span>
              <span>更新：{{ formatTime(task.updated_at) }}</span>
            </div>
          </div>

          <div class="task-item__actions">
            <button
              class="button button--primary"
              type="button"
              @click="router.push({ name: 'workbench', params: { id: task.id } })"
            >
              <ArrowRight /> 继续翻译
            </button>
            <button
              v-if="authStore.isAdmin"
              class="button"
              type="button"
              @click="removeTask(task.id)"
            >
              <Trash2 /> 删除
            </button>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.field__label {
  display: flex;
  align-items: baseline;
  gap: 1rem;
  flex-wrap: wrap;
}

.format-hint {
  font-size: 0.8125rem;
  font-weight: normal;
  color: var(--color-text-muted, #6b7280);
  position: relative;
}

.format-hint__summary {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}

.format-hint__toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.125rem;
  padding: 0;
  border: none;
  background: none;
  color: var(--color-primary, #3b82f6);
  font-size: inherit;
  cursor: pointer;
  text-decoration: none;
}

.format-hint__toggle:hover {
  text-decoration: underline;
}

.format-hint__icon {
  transition: transform 0.2s ease;
  transform: rotate(180deg);
}

.format-hint__icon.is-expanded {
  transform: rotate(0deg);
}

.format-hint__details {
  position: absolute;
  bottom: 100%;
  left: 0;
  margin-bottom: 0.5rem;
  padding: 0.75rem 1rem;
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 0.375rem;
  box-shadow: 0 -4px 6px -1px rgb(0 0 0 / 0.1), 0 -2px 4px -2px rgb(0 0 0 / 0.1);
  z-index: 10;
  white-space: nowrap;
}

.format-category {
  display: flex;
  gap: 0.5rem;
  padding: 0.25rem 0;
  line-height: 1.5;
}

.format-category__label {
  color: var(--color-text-muted, #6b7280);
  white-space: nowrap;
}

.format-category__list {
  color: var(--color-text, #374151);
}

.format-note {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--color-border, #e5e7eb);
  font-size: 0.75rem;
  color: var(--color-text-muted, #6b7280);
}

.format-note__icon {
  font-size: 0.875rem;
}

/* 展开动画 */
.format-expand-enter-active,
.format-expand-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.format-expand-enter-from,
.format-expand-leave-to {
  opacity: 0;
  transform: translateY(0.5rem);
}
</style>
