<script setup lang="ts">
import axios from 'axios'
import { onMounted, ref } from 'vue'

import { http } from '../api/http'
import type { TMCollection, TMImportSummary } from '../types/api'

const selectedTMFile = ref<File | null>(null)
const tmImporting = ref(false)
const tmImportMessage = ref('')
const tmImportSummary = ref<TMImportSummary | null>(null)
const tmCollections = ref<TMCollection[]>([])
const loadingCollections = ref(false)
const collectionMessage = ref('')
const collectionSubmitting = ref(false)
const selectedCollectionId = ref('')
const newCollectionName = ref('')
const newCollectionDescription = ref('')

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
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

function onTMFileChange(event: Event) {
  selectedTMFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  if (selectedTMFile.value && !selectedCollectionId.value && !newCollectionName.value.trim()) {
    newCollectionName.value = fileBaseName(selectedTMFile.value)
  }
}

async function loadCollections() {
  loadingCollections.value = true
  try {
    const { data } = await http.get<TMCollection[]>('/tm/collections')
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

async function createCollection(name: string, description = '') {
  const cleanedName = name.trim()
  if (!cleanedName) {
    throw new Error('记忆库名称不能为空。')
  }

  const { data } = await http.post<TMCollection>('/tm/collections', {
    name: cleanedName,
    description: description.trim() || null,
  })
  await loadCollections()
  return data
}

async function createCollectionFromForm() {
  collectionMessage.value = ''
  collectionSubmitting.value = true
  try {
    const collection = await createCollection(newCollectionName.value, newCollectionDescription.value)
    selectedCollectionId.value = collection.id
    newCollectionName.value = ''
    newCollectionDescription.value = ''
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
  const collection = await createCollection(newCollectionName.value || fallbackName, newCollectionDescription.value)
  selectedCollectionId.value = collection.id
  newCollectionName.value = ''
  newCollectionDescription.value = ''
  return collection.id
}

async function deleteCollection(collection: TMCollection) {
  if (collection.entry_count > 0) {
    collectionMessage.value = '已有 TM 记录的记忆库不能直接删除。'
    return
  }
  if (!window.confirm(`确定删除记忆库“${collection.name}”吗？`)) {
    return
  }

  collectionMessage.value = ''
  try {
    await http.delete(`/tm/collections/${collection.id}`)
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

  tmImportMessage.value = ''
  tmImportSummary.value = null
  tmImporting.value = true

  try {
    const collectionId = await ensureImportCollection()
    const formData = new FormData()
    formData.append('file', selectedTMFile.value)
    if (collectionId) {
      formData.append('collection_id', collectionId)
    }
    const { data } = await http.post<TMImportSummary>('/tm/import-xlsx', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })

    tmImportSummary.value = data
    tmImportMessage.value = `导入完成：${data.filename}`
    selectedTMFile.value = null
    selectedCollectionId.value = ''
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

onMounted(() => {
  void loadCollections()
})
</script>

<template>
  <div class="content-stack">
    <section class="panel">
      <div class="panel-header">
        <div>
          <div class="section-title section-title--tight">记忆库集合</div>
          <p class="hint-text">每次导入都可以生成独立记忆库，任务匹配时只读取选中的集合。</p>
        </div>
        <button class="button" type="button" :disabled="loadingCollections" @click="loadCollections">
          {{ loadingCollections ? '刷新中...' : '刷新列表' }}
        </button>
      </div>

      <div class="upload-form form-grid-2">
        <label class="field">
          <span class="field__label">新记忆库名称</span>
          <input v-model="newCollectionName" class="field__control" type="text" placeholder="例如：技术中英术语库" />
        </label>

        <label class="field">
          <span class="field__label">说明</span>
          <input v-model="newCollectionDescription" class="field__control" type="text" placeholder="可选" />
        </label>

        <div class="field-actions">
          <button
            class="button button--primary"
            type="button"
            :disabled="collectionSubmitting"
            @click="createCollectionFromForm"
          >
            {{ collectionSubmitting ? '创建中...' : '创建记忆库' }}
          </button>
        </div>
      </div>

      <p v-if="collectionMessage" class="form-message">{{ collectionMessage }}</p>

      <div v-if="tmCollections.length" class="collection-list">
        <article v-for="collection in tmCollections" :key="collection.id" class="collection-item">
          <div>
            <strong>{{ collection.name }}</strong>
            <span>{{ collection.description || '无说明' }}</span>
            <small>记录：{{ collection.entry_count }} 条 · 创建：{{ formatTime(collection.created_at) }}</small>
          </div>
          <button
            class="button"
            type="button"
            :disabled="collection.entry_count > 0"
            @click="deleteCollection(collection)"
          >
            删除
          </button>
        </article>
      </div>
      <div v-else class="empty-state">当前还没有记忆库</div>
    </section>

    <section class="panel">
      <div class="section-title">导入 TM 记忆库</div>
      <div class="upload-form">
        <p class="hint-text">
          Excel 约定：第一列原文，第二列译文，首行可以保留表头。重复原文会按现有记录更新。
        </p>

        <label class="field">
          <span class="field__label">目标记忆库</span>
          <select v-model="selectedCollectionId" class="field__control">
            <option value="">新建记忆库</option>
            <option v-for="collection in tmCollections" :key="collection.id" :value="collection.id">
              {{ collection.name }}（{{ collection.entry_count }} 条）
            </option>
          </select>
        </label>

        <p v-if="!selectedCollectionId" class="hint-text">
          未选择现有记忆库时，会用上方名称或 Excel 文件名创建新记忆库。
        </p>

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

        <button
          class="button button--primary"
          type="button"
          :disabled="tmImporting"
          @click="uploadTMWorkbook"
        >
          {{ tmImporting ? '导入中...' : '导入记忆库' }}
        </button>

        <p v-if="tmImportMessage" class="form-message" :class="{ 'is-error': !tmImportSummary }">
          {{ tmImportMessage }}
        </p>
      </div>
    </section>

    <section class="panel">
      <div class="section-title">导入结果</div>
      <div v-if="tmImportSummary" class="summary-grid summary-grid--wide">
        <div class="summary-item">
          <strong>{{ tmImportSummary.collection_name || '未分组' }}</strong>
          <span>目标记忆库</span>
        </div>
        <div class="summary-item">
          <strong>{{ tmImportSummary.imported_rows }}</strong>
          <span>写入总行数</span>
        </div>
        <div class="summary-item">
          <strong>{{ tmImportSummary.created_rows }}</strong>
          <span>新增</span>
        </div>
        <div class="summary-item">
          <strong>{{ tmImportSummary.updated_rows }}</strong>
          <span>更新</span>
        </div>
        <div class="summary-item">
          <strong>{{ tmImportSummary.skipped_header_rows }}</strong>
          <span>跳过表头</span>
        </div>
        <div class="summary-item">
          <strong>{{ tmImportSummary.skipped_empty_rows }}</strong>
          <span>跳过空行</span>
        </div>
      </div>
      <div v-else class="empty-state">导入完成后，这里会展示本次写入统计</div>
    </section>
  </div>
</template>
