<script setup lang="ts">
import axios from 'axios'
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
          <span class="field__label">DOCX 文件</span>
          <input id="upload-file" class="field__control" type="file" accept=".docx" @change="onFileChange" />
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
          {{ taskStore.uploading ? '上传中...' : '上传并进入工作台' }}
        </button>
      </div>
    </section>

    <p v-if="pageError" class="form-message is-error">{{ pageError }}</p>

    <section class="panel">
      <div class="section-title">任务列表</div>
      <div v-if="taskStore.loading" class="empty-state">任务列表加载中...</div>
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
              继续翻译
            </button>
            <button
              v-if="authStore.isAdmin"
              class="button"
              type="button"
              @click="removeTask(task.id)"
            >
              删除
            </button>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>
