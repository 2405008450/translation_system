<script setup lang="ts">
import axios from 'axios'
import { onMounted, ref } from 'vue'

import { http } from '../api/http'
import type { TermbaseCollection, TermbaseImportSummary } from '../types/api'

const selectedFile = ref<File | null>(null)
const importing = ref(false)
const importMessage = ref('')
const importSummary = ref<TermbaseImportSummary | null>(null)
const collections = ref<TermbaseCollection[]>([])
const loadingCollections = ref(false)
const collectionMessage = ref('')
const collectionSubmitting = ref(false)
const selectedCollectionId = ref('')
const newCollectionName = ref('')
const newCollectionDescription = ref('')
const newCollectionSourceLang = ref('')
const newCollectionTargetLang = ref('')

const languageOptions = [
  { code: '', label: '不指定' },
  { code: 'zh-CN', label: '简体中文 (zh-CN)' },
  { code: 'zh-TW', label: '繁体中文 (zh-TW)' },
  { code: 'en-US', label: '美式英语 (en-US)' },
  { code: 'en-GB', label: '英式英语 (en-GB)' },
  { code: 'ja-JP', label: '日语 (ja-JP)' },
  { code: 'ko-KR', label: '韩语 (ko-KR)' },
  { code: 'fr-FR', label: '法语 (fr-FR)' },
  { code: 'de-DE', label: '德语 (de-DE)' },
  { code: 'es-ES', label: '西班牙语 (es-ES)' },
  { code: 'pt-BR', label: '葡萄牙语-巴西 (pt-BR)' },
  { code: 'pt-PT', label: '葡萄牙语-葡萄牙 (pt-PT)' },
  { code: 'ru-RU', label: '俄语 (ru-RU)' },
  { code: 'ar-SA', label: '阿拉伯语 (ar-SA)' },
  { code: 'it-IT', label: '意大利语 (it-IT)' },
  { code: 'nl-NL', label: '荷兰语 (nl-NL)' },
  { code: 'pl-PL', label: '波兰语 (pl-PL)' },
  { code: 'th-TH', label: '泰语 (th-TH)' },
  { code: 'vi-VN', label: '越南语 (vi-VN)' },
]

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

function onFileChange(event: Event) {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  if (selectedFile.value && !selectedCollectionId.value && !newCollectionName.value.trim()) {
    newCollectionName.value = fileBaseName(selectedFile.value)
  }
}

async function loadCollections() {
  loadingCollections.value = true
  try {
    const { data } = await http.get<TermbaseCollection[]>('/termbase/collections')
    collections.value = data
    if (selectedCollectionId.value && !data.some((item) => item.id === selectedCollectionId.value)) {
      selectedCollectionId.value = ''
    }
  } catch (error) {
    collectionMessage.value = getErrorMessage(error, '术语库列表加载失败。')
  } finally {
    loadingCollections.value = false
  }
}

async function createCollection(name: string, description = '', sourceLanguage = '', targetLanguage = '') {
  const cleanedName = name.trim()
  if (!cleanedName) {
    throw new Error('术语库名称不能为空。')
  }

  const { data } = await http.post<TermbaseCollection>('/termbase/collections', {
    name: cleanedName,
    description: description.trim() || null,
    source_language: sourceLanguage.trim() || null,
    target_language: targetLanguage.trim() || null,
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
      newCollectionSourceLang.value,
      newCollectionTargetLang.value
    )
    selectedCollectionId.value = collection.id
    newCollectionName.value = ''
    newCollectionDescription.value = ''
    newCollectionSourceLang.value = ''
    newCollectionTargetLang.value = ''
    collectionMessage.value = `已创建术语库：${collection.name}`
  } catch (error) {
    collectionMessage.value = getErrorMessage(error, '术语库创建失败。')
  } finally {
    collectionSubmitting.value = false
  }
}

async function ensureImportCollection() {
  if (selectedCollectionId.value) {
    return selectedCollectionId.value
  }

  const fallbackName = selectedFile.value ? fileBaseName(selectedFile.value) : ''
  const collection = await createCollection(
    newCollectionName.value || fallbackName,
    newCollectionDescription.value,
    newCollectionSourceLang.value,
    newCollectionTargetLang.value
  )
  selectedCollectionId.value = collection.id
  newCollectionName.value = ''
  newCollectionDescription.value = ''
  newCollectionSourceLang.value = ''
  newCollectionTargetLang.value = ''
  return collection.id
}

async function deleteCollection(collection: TermbaseCollection) {
  if (collection.entry_count > 0) {
    collectionMessage.value = '已有术语记录的术语库不能直接删除。'
    return
  }
  if (!window.confirm(`确定删除术语库"${collection.name}"吗？`)) {
    return
  }

  collectionMessage.value = ''
  try {
    await http.delete(`/termbase/collections/${collection.id}`)
    if (selectedCollectionId.value === collection.id) {
      selectedCollectionId.value = ''
    }
    await loadCollections()
    collectionMessage.value = '术语库已删除。'
  } catch (error) {
    collectionMessage.value = getErrorMessage(error, '术语库删除失败。')
  }
}

async function uploadWorkbook() {
  if (!selectedFile.value) {
    importMessage.value = '请先选择要导入的 Excel 文件。'
    return
  }

  importMessage.value = ''
  importSummary.value = null
  importing.value = true

  try {
    const collectionId = await ensureImportCollection()
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    if (collectionId) {
      formData.append('collection_id', collectionId)
    }
    const { data } = await http.post<TermbaseImportSummary>('/termbase/import-xlsx', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })

    importSummary.value = data
    importMessage.value = `导入完成：${data.filename}`
    selectedFile.value = null
    selectedCollectionId.value = ''
    const fileInput = document.getElementById('termbase-upload-file') as HTMLInputElement | null
    if (fileInput) {
      fileInput.value = ''
    }
    await loadCollections()
  } catch (error) {
    importMessage.value = getErrorMessage(error, '术语库导入失败。')
  } finally {
    importing.value = false
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
          <div class="section-title section-title--tight">术语库集合</div>
          <p class="hint-text">管理术语库，在工作台中自动高亮匹配的术语。</p>
        </div>
        <button class="button" type="button" :disabled="loadingCollections" @click="loadCollections">
          {{ loadingCollections ? '刷新中...' : '刷新列表' }}
        </button>
      </div>

      <div class="upload-form form-grid-2">
        <label class="field">
          <span class="field__label">新术语库名称</span>
          <input v-model="newCollectionName" class="field__control" type="text" placeholder="例如：技术术语库" />
        </label>

        <label class="field">
          <span class="field__label">说明</span>
          <input v-model="newCollectionDescription" class="field__control" type="text" placeholder="可选" />
        </label>

        <label class="field">
          <span class="field__label">源语言</span>
          <select v-model="newCollectionSourceLang" class="field__control">
            <option v-for="lang in languageOptions" :key="lang.code" :value="lang.code">
              {{ lang.label }}
            </option>
          </select>
        </label>

        <label class="field">
          <span class="field__label">目标语言</span>
          <select v-model="newCollectionTargetLang" class="field__control">
            <option v-for="lang in languageOptions" :key="lang.code" :value="lang.code">
              {{ lang.label }}
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
            {{ collectionSubmitting ? '创建中...' : '创建术语库' }}
          </button>
        </div>
      </div>

      <p v-if="collectionMessage" class="form-message">{{ collectionMessage }}</p>

      <div v-if="collections.length" class="collection-list">
        <article v-for="collection in collections" :key="collection.id" class="collection-item">
          <div>
            <strong>{{ collection.name }}</strong>
            <span v-if="collection.source_language || collection.target_language" style="color: var(--color-text-muted); font-size: 0.85em; margin-left: 0.5em;">
              {{ collection.source_language || '?' }} → {{ collection.target_language || '?' }}
            </span>
            <span>{{ collection.description || '无说明' }}</span>
            <small>术语：{{ collection.entry_count }} 条 · 创建：{{ formatTime(collection.created_at) }}</small>
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
      <div v-else class="empty-state">当前还没有术语库</div>
    </section>

    <section class="panel">
      <div class="section-title">导入术语库</div>
      <div class="upload-form">
        <p class="hint-text">
          Excel 约定：第一列原文术语，第二列译文，首行可以保留表头。重复原文会按现有记录更新。
        </p>

        <label class="field">
          <span class="field__label">目标术语库</span>
          <select v-model="selectedCollectionId" class="field__control">
            <option value="">新建术语库</option>
            <option v-for="collection in collections" :key="collection.id" :value="collection.id">
              {{ collection.name }}（{{ collection.entry_count }} 条）
            </option>
          </select>
        </label>

        <p v-if="!selectedCollectionId" class="hint-text">
          未选择现有术语库时，会用上方名称或 Excel 文件名创建新术语库。
        </p>

        <label class="field">
          <span class="field__label">Excel 文件</span>
          <input
            id="termbase-upload-file"
            class="field__control"
            type="file"
            accept=".xlsx"
            @change="onFileChange"
          />
        </label>

        <button
          class="button button--primary"
          type="button"
          :disabled="importing"
          @click="uploadWorkbook"
        >
          {{ importing ? '导入中...' : '导入术语库' }}
        </button>

        <p v-if="importMessage" class="form-message" :class="{ 'is-error': !importSummary }">
          {{ importMessage }}
        </p>
      </div>
    </section>

    <section class="panel">
      <div class="section-title">导入结果</div>
      <div v-if="importSummary" class="summary-grid summary-grid--wide">
        <div class="summary-item">
          <strong>{{ importSummary.collection_name || '未分组' }}</strong>
          <span>目标术语库</span>
        </div>
        <div class="summary-item">
          <strong>{{ importSummary.imported_rows }}</strong>
          <span>写入总行数</span>
        </div>
        <div class="summary-item">
          <strong>{{ importSummary.created_rows }}</strong>
          <span>新增</span>
        </div>
        <div class="summary-item">
          <strong>{{ importSummary.updated_rows }}</strong>
          <span>更新</span>
        </div>
        <div class="summary-item">
          <strong>{{ importSummary.skipped_rows }}</strong>
          <span>跳过</span>
        </div>
      </div>
      <div v-else class="empty-state">导入完成后，这里会展示本次写入统计</div>
    </section>
  </div>
</template>
