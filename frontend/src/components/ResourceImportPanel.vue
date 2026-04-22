<script setup lang="ts">
import axios from 'axios'
import { BookOpen, Database, Loader2, Upload } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import { formatLanguagePair, languageOptions } from '../constants/languages'
import type {
  TMCollection,
  TMImportSummary,
  TermBase,
  TermImportSummary,
} from '../types/api'

type ImportTab = 'tm' | 'term'
type ImportMode = 'all' | ImportTab

const props = withDefaults(defineProps<{
  mode?: ImportMode
  initialTab?: ImportTab
  sourceLanguage?: string | null
  targetLanguage?: string | null
  contextLabel?: string
  fixedTMCollectionId?: string
  fixedTermBaseId?: string
}>(), {
  mode: 'all',
  initialTab: 'tm',
  sourceLanguage: null,
  targetLanguage: null,
  contextLabel: '',
  fixedTMCollectionId: '',
  fixedTermBaseId: '',
})

const emit = defineEmits<{
  imported: [payload: { tab: ImportTab }]
}>()
const { t } = useI18n()

const activeTab = ref<ImportTab>(props.mode === 'all' ? props.initialTab : props.mode)

const tmCollections = ref<TMCollection[]>([])
const termBases = ref<TermBase[]>([])
const loadingTMCollections = ref(false)
const loadingTermBases = ref(false)

const tmFileInput = ref<HTMLInputElement | null>(null)
const termFileInput = ref<HTMLInputElement | null>(null)

const selectedTMFile = ref<File | null>(null)
const selectedTMCollectionId = ref('')
const newTMCollectionName = ref('')
const newTMCollectionDescription = ref('')
const tmImportSourceLanguage = ref('')
const tmImportTargetLanguage = ref('')
const tmImporting = ref(false)
const tmUploadPercent = ref(0)
const tmImportMessage = ref('')
const tmImportSummary = ref<TMImportSummary | null>(null)

const selectedTermFile = ref<File | null>(null)
const selectedTermBaseId = ref('')
const newTermBaseName = ref('')
const newTermBaseDescription = ref('')
const termImportSourceLanguage = ref('')
const termImportTargetLanguage = ref('')
const termImporting = ref(false)
const termUploadPercent = ref(0)
const termImportMessage = ref('')
const termImportSummary = ref<TermImportSummary | null>(null)

const selectedTMCollection = computed(() => (
  tmCollections.value.find((item) => item.id === selectedTMCollectionId.value) ?? null
))

const selectedTermBase = computed(() => (
  termBases.value.find((item) => item.id === selectedTermBaseId.value) ?? null
))

const hasContext = computed(() => (
  Boolean(props.contextLabel)
  || Boolean(props.sourceLanguage)
  || Boolean(props.targetLanguage)
))

const contextLanguagePair = computed(() => (
  formatLanguagePair(props.sourceLanguage, props.targetLanguage)
))

const showTMCreateFields = computed(() => !props.fixedTMCollectionId && !selectedTMCollectionId.value)
const showTermCreateFields = computed(() => !props.fixedTermBaseId && !selectedTermBaseId.value)

watch(() => props.mode, (mode) => {
  if (mode !== 'all') {
    activeTab.value = mode
  }
})

watch(() => props.initialTab, (initialTab) => {
  if (props.mode === 'all') {
    activeTab.value = initialTab
  }
})

watch(() => props.fixedTMCollectionId, (value) => {
  if (value) {
    selectedTMCollectionId.value = value
  }
}, { immediate: true })

watch(() => props.fixedTermBaseId, (value) => {
  if (value) {
    selectedTermBaseId.value = value
  }
}, { immediate: true })

watch(
  () => [props.sourceLanguage, props.targetLanguage] as const,
  ([sourceLanguage, targetLanguage]) => {
    if (sourceLanguage && !tmImportSourceLanguage.value) {
      tmImportSourceLanguage.value = sourceLanguage
    }
    if (targetLanguage && !tmImportTargetLanguage.value) {
      tmImportTargetLanguage.value = targetLanguage
    }
    if (sourceLanguage && !termImportSourceLanguage.value) {
      termImportSourceLanguage.value = sourceLanguage
    }
    if (targetLanguage && !termImportTargetLanguage.value) {
      termImportTargetLanguage.value = targetLanguage
    }
  },
  { immediate: true },
)

watch(selectedTMCollection, (collection) => {
  if (!collection) {
    return
  }
  if (collection.source_language) {
    tmImportSourceLanguage.value = collection.source_language
  }
  if (collection.target_language) {
    tmImportTargetLanguage.value = collection.target_language
  }
})

watch(selectedTermBase, (termBase) => {
  if (!termBase) {
    return
  }
  termImportSourceLanguage.value = termBase.source_language
  termImportTargetLanguage.value = termBase.target_language
})

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

function fileBaseName(file: File) {
  return file.name.replace(/\.[^.]+$/, '')
}

function ensureLanguagePair(sourceLanguage: string, targetLanguage: string) {
  if (!sourceLanguage || !targetLanguage) {
    throw new Error(t('resourceImport.errors.selectLanguage'))
  }
  if (sourceLanguage === targetLanguage) {
    throw new Error(t('resourceImport.errors.sameLanguage'))
  }
}

async function loadTMCollections() {
  loadingTMCollections.value = true
  try {
    const { data } = await http.get<TMCollection[]>('/translation-memory/collections')
    tmCollections.value = data
    if (selectedTMCollectionId.value && !data.some((item) => item.id === selectedTMCollectionId.value)) {
      selectedTMCollectionId.value = ''
    }
  } catch (error) {
    tmImportMessage.value = getErrorMessage(error, t('resourceImport.tm.errors.loadCollections'))
  } finally {
    loadingTMCollections.value = false
  }
}

async function loadTermBases() {
  loadingTermBases.value = true
  try {
    const { data } = await http.get<TermBase[]>('/term-bases')
    termBases.value = data
    if (selectedTermBaseId.value && !data.some((item) => item.id === selectedTermBaseId.value)) {
      selectedTermBaseId.value = ''
    }
  } catch (error) {
    termImportMessage.value = getErrorMessage(error, t('resourceImport.term.errors.loadBases'))
  } finally {
    loadingTermBases.value = false
  }
}

async function createTMCollection(
  name: string,
  description = '',
  sourceLanguage = '',
  targetLanguage = '',
) {
  const cleanedName = name.trim()
  if (!cleanedName) {
    throw new Error(t('resourceImport.tm.errors.createCollectionEmpty'))
  }
  ensureLanguagePair(sourceLanguage, targetLanguage)
  const { data } = await http.post<TMCollection>('/translation-memory/collections', {
    name: cleanedName,
    description: description.trim() || null,
    source_language: sourceLanguage,
    target_language: targetLanguage,
  })
  await loadTMCollections()
  return data
}

async function createTermBase(
  name: string,
  description = '',
  sourceLanguage = '',
  targetLanguage = '',
) {
  const cleanedName = name.trim()
  if (!cleanedName) {
    throw new Error(t('resourceImport.term.errors.createBaseEmpty'))
  }
  ensureLanguagePair(sourceLanguage, targetLanguage)
  const { data } = await http.post<TermBase>('/term-bases', {
    name: cleanedName,
    description: description.trim() || null,
    source_language: sourceLanguage,
    target_language: targetLanguage,
  })
  await loadTermBases()
  return data
}

function onTMFileChange(event: Event) {
  selectedTMFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  if (selectedTMFile.value && !selectedTMCollectionId.value && !newTMCollectionName.value.trim()) {
    newTMCollectionName.value = fileBaseName(selectedTMFile.value)
  }
}

function onTermFileChange(event: Event) {
  selectedTermFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  if (selectedTermFile.value && !selectedTermBaseId.value && !newTermBaseName.value.trim()) {
    newTermBaseName.value = fileBaseName(selectedTermFile.value)
  }
}

async function ensureImportCollection() {
  if (props.fixedTMCollectionId) {
    return props.fixedTMCollectionId
  }
  if (selectedTMCollectionId.value) {
    return selectedTMCollectionId.value
  }
  const fallbackName = selectedTMFile.value ? fileBaseName(selectedTMFile.value) : ''
  const collection = await createTMCollection(
    newTMCollectionName.value || fallbackName,
    newTMCollectionDescription.value,
    tmImportSourceLanguage.value,
    tmImportTargetLanguage.value,
  )
  selectedTMCollectionId.value = collection.id
  return collection.id
}

async function ensureImportTermBase() {
  if (props.fixedTermBaseId) {
    return props.fixedTermBaseId
  }
  if (selectedTermBaseId.value) {
    return selectedTermBaseId.value
  }
  const fallbackName = selectedTermFile.value ? fileBaseName(selectedTermFile.value) : ''
  const termBase = await createTermBase(
    newTermBaseName.value || fallbackName,
    newTermBaseDescription.value,
    termImportSourceLanguage.value,
    termImportTargetLanguage.value,
  )
  selectedTermBaseId.value = termBase.id
  return termBase.id
}

async function uploadTMWorkbook() {
  if (!selectedTMFile.value) {
    tmImportMessage.value = t('resourceImport.tm.errors.selectFile')
    return
  }

  try {
    ensureLanguagePair(tmImportSourceLanguage.value, tmImportTargetLanguage.value)
  } catch (error) {
    tmImportMessage.value = error instanceof Error ? error.message : t('resourceImport.tm.errors.selectLanguage')
    tmImportSummary.value = null
    return
  }

  tmImporting.value = true
  tmUploadPercent.value = 0
  tmImportMessage.value = ''
  tmImportSummary.value = null

  try {
    const collectionId = await ensureImportCollection()
    const formData = new FormData()
    formData.append('file', selectedTMFile.value)
    formData.append('collection_id', collectionId)
    formData.append('source_language', tmImportSourceLanguage.value)
    formData.append('target_language', tmImportTargetLanguage.value)

    const { data } = await http.post<TMImportSummary>('/translation-memory/import-xlsx', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        tmUploadPercent.value = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
      },
    })

    tmImportSummary.value = data
    tmImportMessage.value = t('resourceImport.tm.success.imported', { filename: data.filename })
    selectedTMFile.value = null
    if (tmFileInput.value) {
      tmFileInput.value.value = ''
    }
    newTMCollectionName.value = ''
    newTMCollectionDescription.value = ''
    emit('imported', { tab: 'tm' })
    await loadTMCollections()
  } catch (error) {
    tmImportMessage.value = getErrorMessage(error, t('resourceImport.tm.errors.importFailed'))
  } finally {
    tmImporting.value = false
    tmUploadPercent.value = 0
  }
}

async function uploadTermWorkbook() {
  if (!selectedTermFile.value) {
    termImportMessage.value = t('resourceImport.term.errors.selectFile')
    return
  }

  try {
    ensureLanguagePair(termImportSourceLanguage.value, termImportTargetLanguage.value)
  } catch (error) {
    termImportMessage.value = error instanceof Error ? error.message : t('resourceImport.term.errors.selectLanguage')
    termImportSummary.value = null
    return
  }

  termImporting.value = true
  termUploadPercent.value = 0
  termImportMessage.value = ''
  termImportSummary.value = null

  try {
    const termBaseId = await ensureImportTermBase()
    const formData = new FormData()
    formData.append('file', selectedTermFile.value)
    formData.append('term_base_id', termBaseId)
    formData.append('source_language', termImportSourceLanguage.value)
    formData.append('target_language', termImportTargetLanguage.value)

    const { data } = await http.post<TermImportSummary>('/term-bases/import-xlsx', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        termUploadPercent.value = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
      },
    })

    termImportSummary.value = data
    termImportMessage.value = t('resourceImport.term.success.imported', { filename: data.filename })
    selectedTermFile.value = null
    if (termFileInput.value) {
      termFileInput.value.value = ''
    }
    newTermBaseName.value = ''
    newTermBaseDescription.value = ''
    emit('imported', { tab: 'term' })
    await loadTermBases()
  } catch (error) {
    termImportMessage.value = getErrorMessage(error, t('resourceImport.term.errors.importFailed'))
  } finally {
    termImporting.value = false
    termUploadPercent.value = 0
  }
}

onMounted(() => {
  if (props.mode === 'all' || props.mode === 'tm') {
    void loadTMCollections()
  }
  if (props.mode === 'all' || props.mode === 'term') {
    void loadTermBases()
  }
})
</script>

<template>
  <div class="resource-import-panel">
    <div v-if="hasContext" class="resource-import-panel__context">
      <div class="resource-import-panel__context-copy">
        <strong>{{ contextLabel || t('resourceImport.contextFallback') }}</strong>
        <span>{{ t('resourceImport.contextPair', { pair: contextLanguagePair }) }}</span>
      </div>
      <span class="resource-import-panel__context-tip">
        {{ t('resourceImport.contextTip') }}
      </span>
    </div>

    <div v-if="mode === 'all'" class="tab-bar resource-import-panel__tabs">
      <button
        class="tab-item"
        :class="{ 'is-active': activeTab === 'tm' }"
        type="button"
        @click="activeTab = 'tm'"
      >
        <Database :size="14" />
        {{ t('resourceImport.tabs.tm') }}
      </button>
      <button
        class="tab-item"
        :class="{ 'is-active': activeTab === 'term' }"
        type="button"
        @click="activeTab = 'term'"
      >
        <BookOpen :size="14" />
        {{ t('resourceImport.tabs.term') }}
      </button>
    </div>

    <section v-if="activeTab === 'tm'" class="resource-import-panel__section">
      <p class="hint-text">
        {{ t('resourceImport.tm.intro') }}
      </p>

      <div class="upload-form form-grid-2 resource-import-panel__form">
        <label class="field">
          <span class="field__label">{{ t('resourceImport.tm.target') }}</span>
          <select
            v-model="selectedTMCollectionId"
            class="field__control"
            :disabled="loadingTMCollections || Boolean(props.fixedTMCollectionId)"
          >
            <option value="">{{ t('resourceImport.tm.createNew') }}</option>
            <option
              v-for="collection in tmCollections"
              :key="collection.id"
              :value="collection.id"
            >
              {{ collection.name }}（{{ formatLanguagePair(collection.source_language, collection.target_language) }} / {{ collection.entry_count }} 条）
            </option>
          </select>
        </label>

        <label class="field">
          <span class="field__label">{{ t('resourceImport.tm.file') }}</span>
          <input
            ref="tmFileInput"
            class="field__control"
            type="file"
            accept=".xlsx"
            @change="onTMFileChange"
          />
        </label>

        <label v-if="showTMCreateFields" class="field">
          <span class="field__label">{{ t('resourceImport.tm.newName') }}</span>
          <input
            v-model="newTMCollectionName"
            class="field__control"
            type="text"
            :placeholder="t('resourceImport.tm.newNamePlaceholder')"
          />
        </label>

        <label v-if="showTMCreateFields" class="field">
          <span class="field__label">{{ t('resourceImport.tm.description') }}</span>
          <input
            v-model="newTMCollectionDescription"
            class="field__control"
            type="text"
            :placeholder="t('resourceImport.tm.descriptionPlaceholder')"
          />
        </label>

        <label class="field">
          <span class="field__label">{{ t('resourceImport.tm.sourceLanguage') }}</span>
          <select v-model="tmImportSourceLanguage" class="field__control">
            <option value="">{{ t('resourceImport.tm.selectLanguage') }}</option>
            <option
              v-for="option in languageOptions"
              :key="option.code"
              :value="option.code"
            >
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="field">
          <span class="field__label">{{ t('resourceImport.tm.targetLanguage') }}</span>
          <select v-model="tmImportTargetLanguage" class="field__control">
            <option value="">{{ t('resourceImport.tm.selectLanguage') }}</option>
            <option
              v-for="option in languageOptions"
              :key="option.code"
              :value="option.code"
            >
              {{ option.label }}
            </option>
          </select>
        </label>
      </div>

      <div class="resource-import-panel__actions">
        <button
          class="button button--primary"
          type="button"
          :disabled="tmImporting"
          @click="uploadTMWorkbook"
        >
          <Loader2 v-if="tmImporting" class="lucide-spin" />
          <Upload v-else :size="14" />
          {{ tmImporting ? t('resourceImport.tm.importing', { percent: tmUploadPercent }) : t('resourceImport.tm.importAction') }}
        </button>
      </div>

      <div v-if="tmImporting" class="resource-import-panel__progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div class="progress-bar__fill" :style="{ width: `${tmUploadPercent}%` }" />
          </div>
          <span class="progress-bar__text">{{ tmUploadPercent }}%</span>
        </div>
      </div>

      <p
        v-if="tmImportMessage"
        class="form-message"
        :class="{ 'is-error': !tmImportSummary }"
      >
        {{ tmImportMessage }}
      </p>

      <div v-if="tmImportSummary" class="resource-import-panel__summary">
        <div class="section-title">{{ t('resourceImport.tm.summary.title') }}</div>
        <div class="summary-grid summary-grid--wide">
          <div class="summary-item">
            <strong>{{ tmImportSummary.collection_name }}</strong>
            <span>{{ t('resourceImport.tm.summary.target') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ formatLanguagePair(tmImportSummary.source_language, tmImportSummary.target_language) }}</strong>
            <span>{{ t('resourceImport.tm.summary.pair') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ tmImportSummary.imported_rows }}</strong>
            <span>{{ t('resourceImport.tm.summary.importedRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ tmImportSummary.created_rows }}</strong>
            <span>{{ t('resourceImport.tm.summary.createdRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ tmImportSummary.updated_rows }}</strong>
            <span>{{ t('resourceImport.tm.summary.updatedRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ tmImportSummary.skipped_header_rows }}</strong>
            <span>{{ t('resourceImport.tm.summary.skippedHeaderRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ tmImportSummary.skipped_empty_rows }}</strong>
            <span>{{ t('resourceImport.tm.summary.skippedEmptyRows') }}</span>
          </div>
        </div>
      </div>
    </section>

    <section v-else class="resource-import-panel__section">
      <p class="hint-text">
        {{ t('resourceImport.term.intro') }}
      </p>

      <div class="upload-form form-grid-2 resource-import-panel__form">
        <label class="field">
          <span class="field__label">{{ t('resourceImport.term.target') }}</span>
          <select
            v-model="selectedTermBaseId"
            class="field__control"
            :disabled="loadingTermBases || Boolean(props.fixedTermBaseId)"
          >
            <option value="">{{ t('resourceImport.term.createNew') }}</option>
            <option
              v-for="termBase in termBases"
              :key="termBase.id"
              :value="termBase.id"
            >
              {{ termBase.name }}（{{ formatLanguagePair(termBase.source_language, termBase.target_language) }} / {{ termBase.entry_count }} 条）
            </option>
          </select>
        </label>

        <label class="field">
          <span class="field__label">{{ t('resourceImport.term.file') }}</span>
          <input
            ref="termFileInput"
            class="field__control"
            type="file"
            accept=".xlsx"
            @change="onTermFileChange"
          />
        </label>

        <label v-if="showTermCreateFields" class="field">
          <span class="field__label">{{ t('resourceImport.term.newName') }}</span>
          <input
            v-model="newTermBaseName"
            class="field__control"
            type="text"
            :placeholder="t('resourceImport.term.newNamePlaceholder')"
          />
        </label>

        <label v-if="showTermCreateFields" class="field">
          <span class="field__label">{{ t('resourceImport.term.description') }}</span>
          <input
            v-model="newTermBaseDescription"
            class="field__control"
            type="text"
            :placeholder="t('resourceImport.term.descriptionPlaceholder')"
          />
        </label>

        <label class="field">
          <span class="field__label">{{ t('resourceImport.term.sourceLanguage') }}</span>
          <select v-model="termImportSourceLanguage" class="field__control">
            <option value="">{{ t('resourceImport.term.selectLanguage') }}</option>
            <option
              v-for="option in languageOptions"
              :key="option.code"
              :value="option.code"
            >
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="field">
          <span class="field__label">{{ t('resourceImport.term.targetLanguage') }}</span>
          <select v-model="termImportTargetLanguage" class="field__control">
            <option value="">{{ t('resourceImport.term.selectLanguage') }}</option>
            <option
              v-for="option in languageOptions"
              :key="option.code"
              :value="option.code"
            >
              {{ option.label }}
            </option>
          </select>
        </label>
      </div>

      <div class="resource-import-panel__actions">
        <button
          class="button button--primary"
          type="button"
          :disabled="termImporting"
          @click="uploadTermWorkbook"
        >
          <Loader2 v-if="termImporting" class="lucide-spin" />
          <Upload v-else :size="14" />
          {{ termImporting ? t('resourceImport.term.importing', { percent: termUploadPercent }) : t('resourceImport.term.importAction') }}
        </button>
      </div>

      <div v-if="termImporting" class="resource-import-panel__progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div class="progress-bar__fill" :style="{ width: `${termUploadPercent}%` }" />
          </div>
          <span class="progress-bar__text">{{ termUploadPercent }}%</span>
        </div>
      </div>

      <p
        v-if="termImportMessage"
        class="form-message"
        :class="{ 'is-error': !termImportSummary }"
      >
        {{ termImportMessage }}
      </p>

      <div v-if="termImportSummary" class="resource-import-panel__summary">
        <div class="section-title">{{ t('resourceImport.term.summary.title') }}</div>
        <div class="summary-grid summary-grid--wide">
          <div class="summary-item">
            <strong>{{ termImportSummary.term_base_name }}</strong>
            <span>{{ t('resourceImport.term.summary.target') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ formatLanguagePair(termImportSummary.source_language, termImportSummary.target_language) }}</strong>
            <span>{{ t('resourceImport.term.summary.pair') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ termImportSummary.imported_rows }}</strong>
            <span>{{ t('resourceImport.term.summary.importedRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ termImportSummary.created_rows }}</strong>
            <span>{{ t('resourceImport.term.summary.createdRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ termImportSummary.updated_rows }}</strong>
            <span>{{ t('resourceImport.term.summary.updatedRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ termImportSummary.skipped_header_rows }}</strong>
            <span>{{ t('resourceImport.term.summary.skippedHeaderRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ termImportSummary.skipped_empty_rows }}</strong>
            <span>{{ t('resourceImport.term.summary.skippedEmptyRows') }}</span>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.resource-import-panel {
  display: grid;
  gap: 16px;
}

.resource-import-panel__context {
  display: grid;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: linear-gradient(180deg, var(--surface-1), var(--surface-2));
}

.resource-import-panel__context-copy {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  align-items: center;
}

.resource-import-panel__context-copy strong {
  color: var(--text-primary);
  font-size: 14px;
}

.resource-import-panel__context-copy span,
.resource-import-panel__context-tip {
  color: var(--text-muted);
  font-size: 13px;
}

.resource-import-panel__tabs {
  border-bottom: 1px solid var(--line-soft);
}

.resource-import-panel__section {
  display: grid;
  gap: 14px;
}

.resource-import-panel__form {
  margin-top: 0;
}

.resource-import-panel__actions {
  display: flex;
  justify-content: flex-end;
}

.resource-import-panel__progress {
  display: grid;
  gap: 8px;
}

.resource-import-panel__summary {
  display: grid;
  gap: 12px;
}

@media (max-width: 720px) {
  .resource-import-panel__context-copy {
    flex-direction: column;
    align-items: flex-start;
  }

  .resource-import-panel__actions {
    justify-content: stretch;
  }

  .resource-import-panel__actions .button {
    width: 100%;
  }
}
</style>
