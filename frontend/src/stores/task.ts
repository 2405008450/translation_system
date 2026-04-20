import { ref } from 'vue'
import { defineStore } from 'pinia'

import { http } from '../api/http'
import type { FileRecordSummary } from '../types/api'

export const useTaskStore = defineStore('task', () => {
  const tasks = ref<FileRecordSummary[]>([])
  const loading = ref(false)
  const uploading = ref(false)

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

  async function uploadTask(file: File, threshold = 0.6, collectionIds: string[] = []) {
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
    fetchTasks,
    uploadTask,
    deleteTask,
  }
})
