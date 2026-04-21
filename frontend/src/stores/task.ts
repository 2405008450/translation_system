import { ref } from 'vue'
import { defineStore } from 'pinia'

import { http } from '../api/http'
import type { FileRecordSummary } from '../types/api'

const TERMBASE_STORAGE_KEY = 'task_termbase_collection_ids'

export const useTaskStore = defineStore('task', () => {
  const tasks = ref<FileRecordSummary[]>([])
  const loading = ref(false)
  const uploading = ref(false)
  // 当前任务选中的术语库 collection_ids（按任务ID存储）
  const termbaseCollectionMap = ref<Record<string, string[]>>(loadTermbaseMap())

  function loadTermbaseMap(): Record<string, string[]> {
    try {
      const stored = localStorage.getItem(TERMBASE_STORAGE_KEY)
      return stored ? JSON.parse(stored) : {}
    } catch {
      return {}
    }
  }

  function saveTermbaseMap() {
    try {
      localStorage.setItem(TERMBASE_STORAGE_KEY, JSON.stringify(termbaseCollectionMap.value))
    } catch {
      // 静默失败
    }
  }

  function setTermbaseCollections(taskId: string, collectionIds: string[]) {
    termbaseCollectionMap.value = {
      ...termbaseCollectionMap.value,
      [taskId]: collectionIds,
    }
    saveTermbaseMap()
  }

  function getTermbaseCollections(taskId: string): string[] {
    return termbaseCollectionMap.value[taskId] || []
  }

  async function fetchTasks() {
    loading.value = true
    try {
      const { data } = await http.get<FileRecordSummary[]>('/file-records', {
        params: {
          skip: 0,
          limit: 200,
        },
      })
      tasks.value = data
      return data
    } finally {
      loading.value = false
    }
  }

  async function uploadTask(
    file: File,
    threshold = 0.6,
    collectionIds: string[] = [],
    termbaseCollectionIds: string[] = [],
  ) {
    uploading.value = true
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('threshold', String(threshold))
      collectionIds.forEach((collectionId) => {
        formData.append('collection_ids', collectionId)
      })
      const { data } = await http.post<{ id: string }>('/file-records', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      })
      // 保存术语库选择
      if (termbaseCollectionIds.length > 0) {
        setTermbaseCollections(data.id, termbaseCollectionIds)
      }
      await fetchTasks()
      return data
    } finally {
      uploading.value = false
    }
  }

  async function deleteTask(fileRecordId: string) {
    await http.delete(`/file-records/${fileRecordId}`)
    tasks.value = tasks.value.filter((task) => task.id !== fileRecordId)
  }

  return {
    tasks,
    loading,
    uploading,
    termbaseCollectionMap,
    fetchTasks,
    uploadTask,
    deleteTask,
    setTermbaseCollections,
    getTermbaseCollections,
  }
})
