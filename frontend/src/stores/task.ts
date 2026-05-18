import { ref } from 'vue'
import { defineStore } from 'pinia'
import type { AxiosProgressEvent } from 'axios'

import {
  isImportTaskAccepted,
  waitForImportTask,
  type ImportTaskAccepted,
} from '../api/importTasks'
import { http } from '../api/http'
import type { DocumentParseMode, DocumentParseOptions, FileRecordSummary } from '../types/api'

interface UploadingState {
  active: boolean
  percent: number
  fileName: string
}

export const useTaskStore = defineStore('task', () => {
  const tasks = ref<FileRecordSummary[]>([])
  const loading = ref(false)
  const uploading = ref<UploadingState>({
    active: false,
    percent: 0,
    fileName: '',
  })

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

  function resetUploading() {
    uploading.value = {
      active: false,
      percent: 0,
      fileName: '',
    }
  }

  function handleUploadProgress(event: AxiosProgressEvent) {
    const total = event.total || 0
    const loaded = event.loaded || 0
    uploading.value = {
      ...uploading.value,
      percent: total > 0 ? Math.min(40, Math.round((loaded / total) * 40)) : 0,
    }
  }

  async function uploadTask(
    file: File,
    threshold = 0.6,
    collectionIds: string[] = [],
    termBaseId: string | null = null,
    sourceLanguage = '',
    targetLanguage = '',
    documentParseMode: DocumentParseMode = 'full',
    documentParseOptions: DocumentParseOptions | null = null,
  ) {
    uploading.value = {
      active: true,
      percent: 0,
      fileName: file.name,
    }
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('threshold', String(threshold))
      formData.append('source_language', sourceLanguage)
      formData.append('target_language', targetLanguage)
      formData.append('document_parse_mode', documentParseMode)
      if (documentParseOptions) {
        formData.append('document_parse_options', JSON.stringify(documentParseOptions))
      }
      collectionIds.forEach((collectionId) => {
        formData.append('collection_ids', collectionId)
      })
      if (termBaseId) {
        formData.append('term_base_id', termBaseId)
      }
      const { data } = await http.post<{ id: string } | ImportTaskAccepted>('/file-records', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
        onUploadProgress: handleUploadProgress,
      })
      if (isImportTaskAccepted(data)) {
        return await waitForImportTask<{ id: string }>(data.task_id, (status) => {
          uploading.value = {
            ...uploading.value,
            percent: Math.min(100, 40 + Math.round(status.progress * 0.6)),
          }
        })
      }
      return data
    } finally {
      resetUploading()
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
