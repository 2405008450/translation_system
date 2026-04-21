<script setup lang="ts">
import axios from 'axios'
import { ArrowLeft, ArrowRight, Loader2, Upload } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import { usePageHeader } from '../composables/usePageHeader'
import { useToast } from '../composables/useToast'
import { formatLanguagePair } from '../constants/languages'
import type { TMCollection } from '../types/api'

const props = defineProps<{
  id: string
}>()

interface ProjectDetail {
  id: string
  filename: string
  status: string
  progress: number
  total_segments: number
  translated_segments: number
  source_language: string | null
  target_language: string | null
  creator: string | null
  deadline: string | null
  access_level: string | null
  created_at: string
  updated_at: string
  has_source_document: boolean
}

const router = useRouter()
const toast = useToast()
const { t } = useI18n()

const loading = ref(false)
const uploading = ref(false)
const uploadPercent = ref(0)
const loadingCollections = ref(false)
const project = ref<ProjectDetail | null>(null)
const pageError = ref('')
const uploadMessage = ref('')
const selectedFile = ref<File | null>(null)
const threshold = ref(0.6)
const tmCollections = ref<TMCollection[]>([])
const selectedCollectionIds = ref<string[]>([])

const canEnterWorkbench = computed(() => (
  Boolean(project.value?.has_source_document) && (project.value?.total_segments ?? 0) > 0
))

const languagePairLabel = computed(() => (
  formatLanguagePair(project.value?.source_language ?? null, project.value?.target_language ?? null)
))

usePageHeader(() => ({
  title: project.value?.filename || t('projectDetail.titleFallback'),
  description: t('projectDetail.description'),
  breadcrumbs: [
    { label: t('shell.sections.workspace'), to: { name: 'projects' } },
    { label: project.value?.filename || t('projectDetail.titleFallback') },
  ],
}))

function formatDate(value: string | null) {
  if (!value) {
    return '--'
  }
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function formatAccessLevel(value: string | null) {
  const labels: Record<string, string> = {
    team: t('projectDetail.access.team'),
    private: t('projectDetail.access.private'),
    public: t('projectDetail.access.public'),
  }
  return labels[value || 'team'] || t('projectDetail.access.team')
}

function formatStatus(value: string) {
  const labels: Record<string, string> = {
    draft: t('projectDetail.status.draft'),
    in_progress: t('projectDetail.status.inProgress'),
    pending: t('projectDetail.status.pending'),
    processing: t('projectDetail.status.processing'),
    completed: t('projectDetail.status.completed'),
    translated: t('projectDetail.status.translated'),
    error: t('projectDetail.status.error'),
  }
  return labels[value] || value
}

function onFileChange(event: Event) {
  selectedFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

async function loadProject() {
  loading.value = true
  pageError.value = ''
  try {
    const { data } = await http.get<ProjectDetail>(`/projects/${props.id}`)
    project.value = data
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || t('projectDetail.errors.load'))
      return
    }
    pageError.value = error instanceof Error ? error.message : t('projectDetail.errors.load')
  } finally {
    loading.value = false
  }
}

async function loadTMCollections() {
  loadingCollections.value = true
  try {
    const { data } = await http.get<TMCollection[]>('/translation-memory/collections')
    tmCollections.value = data
    if (selectedCollectionIds.value.length === 0 && data.length > 0) {
      selectedCollectionIds.value = [data[0].id]
    }
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || t('projectDetail.errors.collectionsLoad'))
      return
    }
    pageError.value = error instanceof Error ? error.message : t('projectDetail.errors.collectionsLoad')
  } finally {
    loadingCollections.value = false
  }
}

async function uploadSourceDocument() {
  if (!selectedFile.value) {
    uploadMessage.value = t('projectDetail.errors.selectFile')
    return
  }

  if (selectedCollectionIds.value.length === 0) {
    uploadMessage.value = t('projectDetail.errors.selectCollection')
    return
  }

  uploadMessage.value = ''
  pageError.value = ''
  uploading.value = true
  uploadPercent.value = 0

  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    formData.append('threshold', String(threshold.value))
    selectedCollectionIds.value.forEach((collectionId) => {
      formData.append('collection_ids', collectionId)
    })

    await http.post(`/projects/${props.id}/source-document`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        uploadPercent.value = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
      },
    })

    await loadProject()
    toast.success(t('projectDetail.messages.uploaded'))
    await router.push({
      name: 'workbench',
      params: { id: props.id },
      query: { from: 'project', pid: props.id },
    })
  } catch (error) {
    if (axios.isAxiosError(error)) {
      uploadMessage.value = String(error.response?.data?.detail || t('projectDetail.errors.upload'))
      return
    }
    uploadMessage.value = error instanceof Error ? error.message : t('projectDetail.errors.upload')
  } finally {
    uploading.value = false
    uploadPercent.value = 0
  }
}

onMounted(() => {
  void loadProject()
  void loadTMCollections()
})
</script>

<template>
  <div class="content-stack">
    <section class="panel panel--hero">
      <div class="panel-header">
        <div>
          <div class="section-title section-title--tight">{{ project?.filename || t('projectDetail.titleFallback') }}</div>
          <p class="panel-subtitle">{{ t('projectDetail.heroSubtitle') }}</p>
        </div>

        <div class="header-actions">
          <button class="button" type="button" @click="router.push({ name: 'projects' })">
            <ArrowLeft /> {{ t('projectDetail.back') }}
          </button>
          <button
            v-if="canEnterWorkbench"
            class="button button--primary"
            type="button"
            @click="router.push({ name: 'workbench', params: { id: props.id }, query: { from: 'project', pid: props.id } })"
          >
            <ArrowRight /> {{ t('projectDetail.enterWorkbench') }}
          </button>
        </div>
      </div>

      <div class="summary-grid summary-grid--wide">
        <div class="summary-item">
          <strong>{{ formatStatus(project?.status || 'draft') }}</strong>
          <span>{{ t('projectDetail.summaries.currentStatus') }}</span>
        </div>
        <div class="summary-item">
          <strong>{{ languagePairLabel }}</strong>
          <span>{{ t('projectDetail.summaries.languagePair') }}</span>
        </div>
        <div class="summary-item">
          <strong>{{ project?.progress ?? 0 }}%</strong>
          <span>{{ t('projectDetail.summaries.progress') }}</span>
        </div>
        <div class="summary-item">
          <strong>{{ project?.total_segments ?? 0 }}</strong>
          <span>{{ t('projectDetail.summaries.totalSegments') }}</span>
        </div>
        <div class="summary-item">
          <strong>{{ project?.creator || '--' }}</strong>
          <span>{{ t('projectDetail.summaries.creator') }}</span>
        </div>
        <div class="summary-item">
          <strong>{{ formatAccessLevel(project?.access_level || null) }}</strong>
          <span>{{ t('projectDetail.summaries.accessLevel') }}</span>
        </div>
      </div>
    </section>

    <p v-if="pageError" class="form-message is-error">{{ pageError }}</p>

    <section v-if="loading && !project" class="panel">
      <div class="empty-state">
        <Loader2 class="lucide-spin" :size="32" />
        {{ t('projectDetail.loading') }}
      </div>
    </section>

    <template v-else-if="project">
      <section v-if="!project.has_source_document" class="panel">
        <div class="panel-header panel-header--compact">
          <div>
            <div class="section-title section-title--tight">{{ t('projectDetail.uploadSectionTitle') }}</div>
            <p class="panel-subtitle">{{ t('projectDetail.uploadSectionDescription') }}</p>
          </div>
        </div>

        <div class="upload-form form-grid-2">
          <label class="field">
            <span class="field__label">{{ t('projectDetail.fields.wordFile') }}</span>
            <input class="field__control" type="file" accept=".docx" :aria-label="t('projectDetail.fields.wordFile')" @change="onFileChange" />
          </label>

          <label class="field field--compact">
            <span class="field__label">{{ t('projectDetail.fields.threshold') }}</span>
            <input
              v-model.number="threshold"
              class="field__control"
              type="number"
              step="0.05"
              min="0"
              max="1"
              :aria-label="t('projectDetail.fields.threshold')"
            />
          </label>

          <label class="field field--full">
            <span class="field__label">{{ t('projectDetail.fields.collections') }}</span>
            <select
              v-model="selectedCollectionIds"
              class="field__control field__control--multi"
              multiple
              :disabled="loadingCollections || tmCollections.length === 0"
              :aria-label="t('projectDetail.fields.collections')"
            >
              <option v-for="collection in tmCollections" :key="collection.id" :value="collection.id">
                {{ collection.name }}（{{ formatLanguagePair(collection.source_language, collection.target_language) }} / {{ collection.entry_count }} 条）
              </option>
            </select>
            <span class="hint-text">
              {{ tmCollections.length ? t('projectDetail.hints.collections') : t('projectDetail.hints.noCollections') }}
            </span>
          </label>
        </div>

        <div class="project-detail__footer">
          <div class="project-detail__meta">
            <span class="tag">{{ t('projectDetail.summaries.createdAt') }}：{{ formatDate(project.created_at) }}</span>
            <span class="tag">{{ t('projectDetail.summaries.deadline') }}：{{ formatDate(project.deadline) }}</span>
          </div>
          <button
            class="button button--primary"
            type="button"
            :disabled="uploading || loadingCollections || tmCollections.length === 0 || selectedCollectionIds.length === 0"
            @click="uploadSourceDocument"
          >
            <Loader2 v-if="uploading" class="lucide-spin" />
            <Upload v-else :size="14" />
            {{ uploading ? t('projectDetail.messages.uploading', { percent: uploadPercent }) : t('projectDetail.messages.startUpload') }}
          </button>
        </div>

        <div v-if="uploading" class="project-detail__progress">
          <div class="progress-bar">
            <div class="progress-bar__track">
              <div class="progress-bar__fill" :style="{ width: `${uploadPercent}%` }" />
            </div>
            <span class="progress-bar__text">{{ uploadPercent }}%</span>
          </div>
        </div>

        <p v-if="uploadMessage" class="form-message is-error">{{ uploadMessage }}</p>
      </section>

      <section v-else class="panel">
        <div class="panel-header panel-header--compact">
          <div>
            <div class="section-title section-title--tight">{{ t('projectDetail.readyTitle') }}</div>
            <p class="panel-subtitle">{{ t('projectDetail.readyDescription') }}</p>
          </div>
          <button
            class="button button--primary"
            type="button"
            @click="router.push({ name: 'workbench', params: { id: props.id }, query: { from: 'project', pid: props.id } })"
          >
            <ArrowRight /> {{ t('projectDetail.enterWorkbench') }}
          </button>
        </div>

        <div class="summary-grid summary-grid--wide">
          <div class="summary-item">
            <strong>{{ project.total_segments }}</strong>
            <span>{{ t('projectDetail.summaries.totalSegments') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ project.translated_segments }}</strong>
            <span>{{ t('projectDetail.summaries.translatedSegments') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ project.progress }}%</strong>
            <span>{{ t('projectDetail.summaries.progress') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ formatDate(project.updated_at) }}</strong>
            <span>{{ t('projectDetail.summaries.updatedAt') }}</span>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.project-detail__footer {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  margin-top: 18px;
}

.project-detail__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.project-detail__progress {
  margin-top: 12px;
}

.field--full {
  grid-column: 1 / -1;
}

@media (max-width: 720px) {
  .project-detail__footer {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
