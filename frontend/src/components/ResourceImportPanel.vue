<script setup lang="ts">
import axios from 'axios'
import { BookOpen, CheckCircle2, Database, Eye, Loader2 } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import { formatLanguagePair, languageOptions } from '../constants/languages'
import { refreshGlobalNotifications } from '../utils/notifications'
import { isProgressComplete } from '../utils/progress'
import type {
  TMCollection,
  TMImportPreview,
  TMImportSummary,
  TermBase,
  TermImportPreview,
  TermImportSummary,
} from '../types/api'

type ImportTab = 'tm' | 'term'
type ImportMode = 'all' | ImportTab
type ImportPreviewRow = { row_index: number, status: string, message: string }

const props = withDefaults(defineProps<{
  mode?: ImportMode
  initialTab?: ImportTab
  sourceLanguage?: string | null
  targetLanguage?: string | null
  contextLabel?: string
  defaultTMCollectionId?: string
  defaultTermBaseId?: string
  fixedTMCollectionId?: string
  fixedTermBaseId?: string
}>(), {
  mode: 'all',
  initialTab: 'tm',
  sourceLanguage: null,
  targetLanguage: null,
  contextLabel: '',
  defaultTMCollectionId: '',
  defaultTermBaseId: '',
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
const tmPreviewing = ref(false)
const tmUploadPercent = ref(0)
const tmImportMessage = ref('')
const tmImportSummary = ref<TMImportSummary | null>(null)
const tmImportPreview = ref<TMImportPreview | null>(null)
const tmKeepDuplicateRowIndexes = ref<Set<number>>(new Set())

const selectedTermFile = ref<File | null>(null)
const selectedTermBaseId = ref('')
const newTermBaseName = ref('')
const newTermBaseDescription = ref('')
const termImportSourceLanguage = ref('')
const termImportTargetLanguage = ref('')
const termImporting = ref(false)
const termPreviewing = ref(false)
const termUploadPercent = ref(0)
const termImportMessage = ref('')
const termImportSummary = ref<TermImportSummary | null>(null)
const termImportPreview = ref<TermImportPreview | null>(null)
const termKeepDuplicateRowIndexes = ref<Set<number>>(new Set())

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
const tmPreviewRowsHidden = computed(() => {
  const preview = tmImportPreview.value
  return preview ? Math.max(0, preview.total_rows - preview.rows.length) : 0
})
const termPreviewRowsHidden = computed(() => {
  const preview = termImportPreview.value
  return preview ? Math.max(0, preview.total_rows - preview.rows.length) : 0
})
const fixedTMTargetLabel = computed(() => props.contextLabel || selectedTMCollection.value?.name || '当前记忆库')
const fixedTermTargetLabel = computed(() => props.contextLabel || selectedTermBase.value?.name || '当前术语库')
const tmKeptDuplicateRows = computed(() => countKeptDuplicateRows(tmImportPreview.value?.rows ?? [], tmKeepDuplicateRowIndexes.value))
const termKeptDuplicateRows = computed(() => countKeptDuplicateRows(termImportPreview.value?.rows ?? [], termKeepDuplicateRowIndexes.value))

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

watch(() => props.defaultTMCollectionId, (value) => {
  if (!props.fixedTMCollectionId && value && !selectedTMCollectionId.value) {
    selectedTMCollectionId.value = value
  }
}, { immediate: true })

watch(() => props.fixedTermBaseId, (value) => {
  if (value) {
    selectedTermBaseId.value = value
  }
}, { immediate: true })

watch(() => props.defaultTermBaseId, (value) => {
  if (!props.fixedTermBaseId && value && !selectedTermBaseId.value) {
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
  resetTMPreview()
  if (collection.source_language) {
    tmImportSourceLanguage.value = collection.source_language
  }
  if (collection.target_language) {
    tmImportTargetLanguage.value = collection.target_language
  }
})

watch([selectedTMCollectionId, tmImportSourceLanguage, tmImportTargetLanguage], () => {
  resetTMPreview()
})

watch([tmImportSourceLanguage, tmImportTargetLanguage], () => {
  ensureDefaultTMCollectionSelection()
})

watch(selectedTermBase, (termBase) => {
  if (!termBase) {
    return
  }
  resetTermPreview()
  termImportSourceLanguage.value = termBase.source_language
  termImportTargetLanguage.value = termBase.target_language
})

watch([selectedTermBaseId, termImportSourceLanguage, termImportTargetLanguage], () => {
  resetTermPreview()
})

watch([termImportSourceLanguage, termImportTargetLanguage], () => {
  ensureDefaultTermBaseSelection()
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

function findMatchingTMCollectionId() {
  const sourceLanguage = tmImportSourceLanguage.value || props.sourceLanguage || ''
  const targetLanguage = tmImportTargetLanguage.value || props.targetLanguage || ''
  if (!sourceLanguage || !targetLanguage) {
    return ''
  }
  return tmCollections.value.find((collection) => (
    collection.source_language === sourceLanguage
    && collection.target_language === targetLanguage
  ))?.id || ''
}

function findMatchingTermBaseId() {
  const sourceLanguage = termImportSourceLanguage.value || props.sourceLanguage || ''
  const targetLanguage = termImportTargetLanguage.value || props.targetLanguage || ''
  if (!sourceLanguage || !targetLanguage) {
    return ''
  }
  return termBases.value.find((termBase) => (
    termBase.source_language === sourceLanguage
    && termBase.target_language === targetLanguage
  ))?.id || ''
}

function ensureDefaultTMCollectionSelection() {
  if (props.fixedTMCollectionId) {
    selectedTMCollectionId.value = props.fixedTMCollectionId
    return
  }
  if (selectedTMCollectionId.value) {
    return
  }
  const defaultId = props.defaultTMCollectionId || findMatchingTMCollectionId()
  if (defaultId && tmCollections.value.some((collection) => collection.id === defaultId)) {
    selectedTMCollectionId.value = defaultId
  }
}

function ensureDefaultTermBaseSelection() {
  if (props.fixedTermBaseId) {
    selectedTermBaseId.value = props.fixedTermBaseId
    return
  }
  if (selectedTermBaseId.value) {
    return
  }
  const defaultId = props.defaultTermBaseId || findMatchingTermBaseId()
  if (defaultId && termBases.value.some((termBase) => termBase.id === defaultId)) {
    selectedTermBaseId.value = defaultId
  }
}

function isDuplicatePreviewRow(row: ImportPreviewRow) {
  return row.status === 'update' || row.status === 'keep' || row.status === 'duplicate'
}

function getInitialKeepDuplicateRowIndexes(rows: ImportPreviewRow[]) {
  return new Set(rows.filter(isDuplicatePreviewRow).map((row) => row.row_index))
}

function countKeptDuplicateRows(rows: ImportPreviewRow[], rowIndexes: Set<number>) {
  return rows.filter((row) => isDuplicatePreviewRow(row) && rowIndexes.has(row.row_index)).length
}

function appendSkippedDuplicateRows(formData: FormData, rowIndexes: Set<number>) {
  formData.append('skip_duplicate_row_indexes', JSON.stringify([...rowIndexes].sort((a, b) => a - b)))
}

function isTMDuplicateRowKept(row: ImportPreviewRow) {
  return isDuplicatePreviewRow(row) && tmKeepDuplicateRowIndexes.value.has(row.row_index)
}

function isTermDuplicateRowKept(row: ImportPreviewRow) {
  return isDuplicatePreviewRow(row) && termKeepDuplicateRowIndexes.value.has(row.row_index)
}

function setTMDuplicateRowKeep(rowIndex: number, keep: boolean) {
  const next = new Set(tmKeepDuplicateRowIndexes.value)
  if (keep) {
    next.add(rowIndex)
  } else {
    next.delete(rowIndex)
  }
  tmKeepDuplicateRowIndexes.value = next
}

function setTermDuplicateRowKeep(rowIndex: number, keep: boolean) {
  const next = new Set(termKeepDuplicateRowIndexes.value)
  if (keep) {
    next.add(rowIndex)
  } else {
    next.delete(rowIndex)
  }
  termKeepDuplicateRowIndexes.value = next
}

function getTMPreviewMessage(row: ImportPreviewRow) {
  if (row.status === 'duplicate') {
    return isTMDuplicateRowKept(row)
      ? '文件内重复，将跳过此行并保留前一条导入数据。'
      : '文件内重复，将使用此行作为本次导入数据。'
  }
  if (row.status === 'update' || row.status === 'keep') {
    return isTMDuplicateRowKept(row)
      ? '当前记忆库已有相同源文，将保留现有数据。'
      : '当前记忆库已有相同源文，将使用导入译文覆盖。'
  }
  return row.message
}

function getTermPreviewMessage(row: ImportPreviewRow) {
  if (row.status === 'duplicate') {
    return isTermDuplicateRowKept(row)
      ? '文件内重复，将跳过此行并保留前一条术语数据。'
      : '文件内重复，将使用此行作为本次导入数据。'
  }
  if (row.status === 'update' || row.status === 'keep') {
    return isTermDuplicateRowKept(row)
      ? '当前术语库已有相同源术语，将保留现有数据。'
      : '当前术语库已有相同源术语，将使用导入目标术语覆盖。'
  }
  return row.message
}

async function loadTMCollections() {
  loadingTMCollections.value = true
  try {
    const { data } = await http.get<TMCollection[]>('/translation-memory/collections')
    tmCollections.value = data
    if (!props.fixedTMCollectionId && selectedTMCollectionId.value && !data.some((item) => item.id === selectedTMCollectionId.value)) {
      selectedTMCollectionId.value = ''
    }
    ensureDefaultTMCollectionSelection()
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
    if (!props.fixedTermBaseId && selectedTermBaseId.value && !data.some((item) => item.id === selectedTermBaseId.value)) {
      selectedTermBaseId.value = ''
    }
    ensureDefaultTermBaseSelection()
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
  const file = (event.target as HTMLInputElement).files?.[0] ?? null
  selectedTMFile.value = file
  resetTMPreview()
  
  if (!file) return
  
  // Auto-fill name from filename for non-SDLTM files
  if (!selectedTMCollectionId.value && !newTMCollectionName.value.trim()) {
    newTMCollectionName.value = fileBaseName(file)
  }
  
  // For SDLTM files, fetch metadata to auto-fill language pair and name
  if (file.name.toLowerCase().endsWith('.sdltm')) {
    fetchSDLTMMetadata(file)
  }
}

async function fetchSDLTMMetadata(file: File) {
  try {
    const formData = new FormData()
    formData.append('file', file)
    const { data } = await http.post<{
      name: string
      source_language: string
      target_language: string
      entry_count: number
    }>('/translation-memory/preview-sdltm', formData)
    
    // Auto-fill fields if not already set
    if (!newTMCollectionName.value.trim() && data.name) {
      newTMCollectionName.value = data.name
    }
    if (!tmImportSourceLanguage.value && data.source_language) {
      tmImportSourceLanguage.value = data.source_language
    }
    if (!tmImportTargetLanguage.value && data.target_language) {
      tmImportTargetLanguage.value = data.target_language
    }
  } catch {
    // Silently ignore preview errors, user can still manually select
  }
}

function onTermFileChange(event: Event) {
  selectedTermFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  resetTermPreview()
  if (selectedTermFile.value && !selectedTermBaseId.value && !newTermBaseName.value.trim()) {
    newTermBaseName.value = fileBaseName(selectedTermFile.value)
  }
}

function resetTMPreview() {
  tmImportPreview.value = null
  tmImportSummary.value = null
  tmImportMessage.value = ''
  tmKeepDuplicateRowIndexes.value = new Set()
}

function buildTMImportFormData(collectionId?: string, includeDuplicateDecisions = false) {
  if (!selectedTMFile.value) {
    throw new Error(t('resourceImport.tm.errors.selectFile'))
  }
  const formData = new FormData()
  formData.append('file', selectedTMFile.value)
  if (collectionId) {
    formData.append('collection_id', collectionId)
  }
  formData.append('source_language', tmImportSourceLanguage.value)
  formData.append('target_language', tmImportTargetLanguage.value)
  formData.append('duplicate_policy', 'overwrite')
  if (includeDuplicateDecisions) {
    appendSkippedDuplicateRows(formData, tmKeepDuplicateRowIndexes.value)
  }
  return formData
}

async function previewTMWorkbook() {
  if (!selectedTMFile.value) {
    tmImportMessage.value = t('resourceImport.tm.errors.selectFile')
    return
  }

  try {
    ensureLanguagePair(tmImportSourceLanguage.value, tmImportTargetLanguage.value)
  } catch (error) {
    resetTMPreview()
    tmImportMessage.value = error instanceof Error ? error.message : t('resourceImport.tm.errors.selectLanguage')
    return
  }

  tmPreviewing.value = true
  tmImportMessage.value = ''
  tmImportSummary.value = null

  try {
    const formData = buildTMImportFormData(selectedTMCollectionId.value || props.fixedTMCollectionId)
    formData.append('preview_limit', '500')
    const { data } = await http.post<TMImportPreview>('/translation-memory/import-xlsx/preview', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    tmImportPreview.value = data
    tmKeepDuplicateRowIndexes.value = getInitialKeepDuplicateRowIndexes(data.rows)
    tmImportMessage.value = `预览完成：读取 ${data.valid_rows} 条有效记忆。`
  } catch (error) {
    tmImportPreview.value = null
    tmImportMessage.value = getErrorMessage(error, '记忆库预览失败。')
  } finally {
    tmPreviewing.value = false
  }
}

function resetTermPreview() {
  termImportPreview.value = null
  termImportSummary.value = null
  termImportMessage.value = ''
  termKeepDuplicateRowIndexes.value = new Set()
}

function buildTermImportFormData(termBaseId?: string, includeDuplicateDecisions = false) {
  if (!selectedTermFile.value) {
    throw new Error(t('resourceImport.term.errors.selectFile'))
  }
  const formData = new FormData()
  formData.append('file', selectedTermFile.value)
  if (termBaseId) {
    formData.append('term_base_id', termBaseId)
  }
  formData.append('source_language', termImportSourceLanguage.value)
  formData.append('target_language', termImportTargetLanguage.value)
  if (includeDuplicateDecisions) {
    appendSkippedDuplicateRows(formData, termKeepDuplicateRowIndexes.value)
  }
  return formData
}

async function previewTermWorkbook() {
  if (!selectedTermFile.value) {
    termImportMessage.value = t('resourceImport.term.errors.selectFile')
    return
  }

  try {
    ensureLanguagePair(termImportSourceLanguage.value, termImportTargetLanguage.value)
  } catch (error) {
    resetTermPreview()
    termImportMessage.value = error instanceof Error ? error.message : t('resourceImport.term.errors.selectLanguage')
    return
  }

  termPreviewing.value = true
  termImportMessage.value = ''
  termImportSummary.value = null

  try {
    const formData = buildTermImportFormData(selectedTermBaseId.value || props.fixedTermBaseId)
    formData.append('preview_limit', '500')
    const { data } = await http.post<TermImportPreview>('/term-bases/import-xlsx/preview', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    termImportPreview.value = data
    termKeepDuplicateRowIndexes.value = getInitialKeepDuplicateRowIndexes(data.rows)
    termImportMessage.value = `预览完成：读取 ${data.valid_rows} 条有效术语。`
  } catch (error) {
    termImportPreview.value = null
    termImportMessage.value = getErrorMessage(error, '术语库预览失败。')
  } finally {
    termPreviewing.value = false
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
    const formData = buildTMImportFormData(collectionId, true)

    const { data } = await http.post<TMImportSummary>('/translation-memory/import-xlsx', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        tmUploadPercent.value = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
      },
    })

    tmImportSummary.value = data
    tmImportPreview.value = null
    tmImportMessage.value = t('resourceImport.tm.success.imported', { filename: data.filename })
    refreshGlobalNotifications()
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
    const formData = buildTermImportFormData(termBaseId, true)

    const { data } = await http.post<TermImportSummary>('/term-bases/import-xlsx', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        termUploadPercent.value = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
      },
    })

    termImportSummary.value = data
    termImportPreview.value = null
    termImportMessage.value = t('resourceImport.term.success.imported', { filename: data.filename })
    refreshGlobalNotifications()
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
            <template v-if="props.fixedTMCollectionId">
              <option :value="props.fixedTMCollectionId">{{ fixedTMTargetLabel }}（当前记忆库）</option>
            </template>
            <template v-else>
              <option value="">{{ t('resourceImport.tm.createNew') }}</option>
              <option
                v-for="collection in tmCollections"
                :key="collection.id"
                :value="collection.id"
              >
                {{ collection.name }}（{{ formatLanguagePair(collection.source_language, collection.target_language) }} / {{ collection.entry_count }} 条）
              </option>
            </template>
          </select>
        </label>

        <label class="field">
          <span class="field__label">{{ t('resourceImport.tm.file') }}</span>
          <input
            ref="tmFileInput"
            class="field__control"
            type="file"
            accept=".tmx,.sdltm,.xls,.xlsx,.csv"
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
          class="button"
          type="button"
          :disabled="tmImporting || tmPreviewing"
          @click="previewTMWorkbook"
        >
          <Loader2 v-if="tmPreviewing" class="lucide-spin" />
          <Eye v-else :size="14" />
          {{ tmPreviewing ? '预览中...' : '预览数据' }}
        </button>
        <button
          class="button button--primary"
          type="button"
          :disabled="tmImporting || tmPreviewing || !tmImportPreview"
          @click="uploadTMWorkbook"
        >
          <Loader2 v-if="tmImporting" class="lucide-spin" />
          <CheckCircle2 v-else :size="14" />
          {{ tmImporting ? t('resourceImport.tm.importing', { percent: tmUploadPercent }) : '确认导入' }}
        </button>
      </div>

      <div v-if="tmImporting" class="resource-import-panel__progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div
              class="progress-bar__fill"
              :class="{ 'is-complete': isProgressComplete(tmUploadPercent) }"
              :style="{ width: `${tmUploadPercent}%` }"
            />
          </div>
          <span class="progress-bar__text">{{ tmUploadPercent }}%</span>
        </div>
      </div>

      <p
        v-if="tmImportMessage"
        class="form-message"
        :class="{ 'is-error': !tmImportSummary && !tmImportPreview }"
      >
        {{ tmImportMessage }}
      </p>

      <div v-if="tmImportPreview" class="resource-import-panel__preview">
        <div class="resource-import-panel__preview-head">
          <div>
            <div class="section-title">导入预览</div>
            <p class="hint-text">
              {{ tmImportPreview.filename }}，将导入到 {{ tmImportPreview.collection_name || (newTMCollectionName || '新记忆库') }}
            </p>
          </div>
          <div class="resource-import-panel__preview-stats">
            <span>有效 {{ tmImportPreview.valid_rows }}</span>
            <span>新增 {{ tmImportPreview.create_rows }}</span>
            <span>覆盖 {{ tmImportPreview.update_rows }}</span>
            <span>保留 {{ tmKeptDuplicateRows }}</span>
            <span>重复 {{ tmImportPreview.duplicate_rows }}</span>
            <span>跳过 {{ tmImportPreview.skipped_empty_rows + tmImportPreview.skipped_header_rows }}</span>
          </div>
        </div>

        <div class="resource-import-panel__preview-table-wrap">
          <table class="resource-import-panel__preview-table">
            <thead>
              <tr>
                <th>行号</th>
                <th>源文</th>
                <th>译文</th>
                <th>结果</th>
                <th>重复处理</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in tmImportPreview.rows" :key="row.row_index" :class="`is-${row.status}`">
                <td>{{ row.row_index }}</td>
                <td>{{ row.source_text || '-' }}</td>
                <td>{{ row.target_text || '-' }}</td>
                <td>
                  <strong>{{ getTMPreviewMessage(row) }}</strong>
                </td>
                <td>
                  <div v-if="isDuplicatePreviewRow(row)" class="resource-import-panel__row-actions">
                    <button
                      class="resource-import-panel__choice"
                      :class="{ 'is-active': isTMDuplicateRowKept(row) }"
                      type="button"
                      :disabled="tmImporting || tmPreviewing"
                      @click="setTMDuplicateRowKeep(row.row_index, true)"
                    >
                      {{ row.status === 'duplicate' ? '保留前一条' : '保留现有' }}
                    </button>
                    <button
                      class="resource-import-panel__choice"
                      :class="{ 'is-active': !isTMDuplicateRowKept(row) }"
                      type="button"
                      :disabled="tmImporting || tmPreviewing"
                      @click="setTMDuplicateRowKeep(row.row_index, false)"
                    >
                      {{ row.status === 'duplicate' ? '使用此行' : '使用导入' }}
                    </button>
                  </div>
                  <span v-else class="resource-import-panel__row-empty">-</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="tmPreviewRowsHidden > 0" class="hint-text">
          仅显示前 {{ tmImportPreview.rows.length }} 行，还有 {{ tmPreviewRowsHidden }} 行未展示；未展示行不会参与逐行保留选择。
        </p>
      </div>

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
            <strong>{{ tmImportSummary.skipped_duplicate_rows || 0 }}</strong>
            <span>重复跳过</span>
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
            <template v-if="props.fixedTermBaseId">
              <option :value="props.fixedTermBaseId">{{ fixedTermTargetLabel }}（当前术语库）</option>
            </template>
            <template v-else>
              <option value="">{{ t('resourceImport.term.createNew') }}</option>
              <option
                v-for="termBase in termBases"
                :key="termBase.id"
                :value="termBase.id"
              >
                {{ termBase.name }}（{{ formatLanguagePair(termBase.source_language, termBase.target_language) }} / {{ termBase.entry_count }} 条）
              </option>
            </template>
          </select>
        </label>

        <label class="field">
          <span class="field__label">{{ t('resourceImport.term.file') }}</span>
          <input
            ref="termFileInput"
            class="field__control"
            type="file"
            accept=".xls,.xlsx,.csv"
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
          class="button"
          type="button"
          :disabled="termImporting || termPreviewing"
          @click="previewTermWorkbook"
        >
          <Loader2 v-if="termPreviewing" class="lucide-spin" />
          <Eye v-else :size="14" />
          {{ termPreviewing ? '预览中...' : '预览数据' }}
        </button>
        <button
          class="button button--primary"
          type="button"
          :disabled="termImporting || termPreviewing || !termImportPreview"
          @click="uploadTermWorkbook"
        >
          <Loader2 v-if="termImporting" class="lucide-spin" />
          <CheckCircle2 v-else :size="14" />
          {{ termImporting ? t('resourceImport.term.importing', { percent: termUploadPercent }) : '确认导入' }}
        </button>
      </div>

      <div v-if="termImporting" class="resource-import-panel__progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div
              class="progress-bar__fill"
              :class="{ 'is-complete': isProgressComplete(termUploadPercent) }"
              :style="{ width: `${termUploadPercent}%` }"
            />
          </div>
          <span class="progress-bar__text">{{ termUploadPercent }}%</span>
        </div>
      </div>

      <p
        v-if="termImportMessage"
        class="form-message"
        :class="{ 'is-error': !termImportSummary && !termImportPreview }"
      >
        {{ termImportMessage }}
      </p>

      <div v-if="termImportPreview" class="resource-import-panel__preview">
        <div class="resource-import-panel__preview-head">
          <div>
            <div class="section-title">导入预览</div>
            <p class="hint-text">
              {{ termImportPreview.filename }}，将导入到 {{ termImportPreview.term_base_name || (newTermBaseName || '新术语库') }}
            </p>
          </div>
          <div class="resource-import-panel__preview-stats">
            <span>有效 {{ termImportPreview.valid_rows }}</span>
            <span>新增 {{ termImportPreview.create_rows }}</span>
            <span>覆盖 {{ termImportPreview.update_rows }}</span>
            <span>保留 {{ termKeptDuplicateRows }}</span>
            <span>重复 {{ termImportPreview.duplicate_rows }}</span>
            <span>跳过 {{ termImportPreview.skipped_empty_rows + termImportPreview.skipped_header_rows }}</span>
          </div>
        </div>

        <div class="resource-import-panel__preview-table-wrap">
          <table class="resource-import-panel__preview-table">
            <thead>
              <tr>
                <th>行号</th>
                <th>源术语</th>
                <th>目标术语</th>
                <th>结果</th>
                <th>重复处理</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in termImportPreview.rows" :key="row.row_index" :class="`is-${row.status}`">
                <td>{{ row.row_index }}</td>
                <td>{{ row.source_text || '-' }}</td>
                <td>{{ row.target_text || '-' }}</td>
                <td>
                  <strong>{{ getTermPreviewMessage(row) }}</strong>
                </td>
                <td>
                  <div v-if="isDuplicatePreviewRow(row)" class="resource-import-panel__row-actions">
                    <button
                      class="resource-import-panel__choice"
                      :class="{ 'is-active': isTermDuplicateRowKept(row) }"
                      type="button"
                      :disabled="termImporting || termPreviewing"
                      @click="setTermDuplicateRowKeep(row.row_index, true)"
                    >
                      {{ row.status === 'duplicate' ? '保留前一条' : '保留现有' }}
                    </button>
                    <button
                      class="resource-import-panel__choice"
                      :class="{ 'is-active': !isTermDuplicateRowKept(row) }"
                      type="button"
                      :disabled="termImporting || termPreviewing"
                      @click="setTermDuplicateRowKeep(row.row_index, false)"
                    >
                      {{ row.status === 'duplicate' ? '使用此行' : '使用导入' }}
                    </button>
                  </div>
                  <span v-else class="resource-import-panel__row-empty">-</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="termPreviewRowsHidden > 0" class="hint-text">
          仅显示前 {{ termImportPreview.rows.length }} 行，还有 {{ termPreviewRowsHidden }} 行未展示；未展示行不会参与逐行保留选择。
        </p>
      </div>

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
            <strong>{{ termImportSummary.skipped_duplicate_rows || 0 }}</strong>
            <span>重复跳过</span>
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
  gap: 10px;
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

.resource-import-panel__preview {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-0);
}

.resource-import-panel__preview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.resource-import-panel__preview-head .hint-text {
  margin: 4px 0 0;
}

.resource-import-panel__preview-stats {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.resource-import-panel__preview-stats span {
  min-height: 24px;
  padding: 4px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-1);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.2;
}

.resource-import-panel__preview-table-wrap {
  max-height: 320px;
  overflow: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
}

.resource-import-panel__preview-table {
  width: 100%;
  min-width: 820px;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 13px;
}

.resource-import-panel__preview-table th,
.resource-import-panel__preview-table td {
  padding: 10px;
  border-right: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}

.resource-import-panel__preview-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--brand-050);
  color: var(--text-secondary);
  font-weight: 600;
}

.resource-import-panel__preview-table th:first-child,
.resource-import-panel__preview-table td:first-child {
  width: 64px;
  text-align: center;
}

.resource-import-panel__preview-table th:last-child,
.resource-import-panel__preview-table td:last-child {
  width: 190px;
  border-right: 0;
}

.resource-import-panel__preview-table th:nth-last-child(2),
.resource-import-panel__preview-table td:nth-last-child(2) {
  width: 240px;
}

.resource-import-panel__row-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
}

.resource-import-panel__choice {
  min-height: 30px;
  padding: 0 8px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-1);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.2;
}

.resource-import-panel__choice.is-active {
  border-color: var(--brand-500);
  background: var(--brand-050);
  color: var(--brand-700);
  font-weight: 600;
}

.resource-import-panel__choice:disabled {
  cursor: not-allowed;
  opacity: 0.56;
}

.resource-import-panel__row-empty {
  color: var(--text-muted);
}

.resource-import-panel__preview-table tr.is-create td:nth-last-child(2) {
  color: var(--state-success, #0b7a55);
}

.resource-import-panel__preview-table tr.is-update td:nth-last-child(2) {
  color: var(--brand-700);
}

.resource-import-panel__preview-table tr.is-keep td:nth-last-child(2) {
  color: var(--text-secondary);
}

.resource-import-panel__preview-table tr.is-duplicate td:nth-last-child(2),
.resource-import-panel__preview-table tr.is-empty td:nth-last-child(2),
.resource-import-panel__preview-table tr.is-header td:nth-last-child(2) {
  color: var(--text-muted);
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

  .resource-import-panel__preview-head {
    display: grid;
  }

  .resource-import-panel__preview-stats {
    justify-content: flex-start;
  }
}
</style>
