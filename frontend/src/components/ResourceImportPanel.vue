<script setup lang="ts">
import axios from 'axios'
import { BookOpen, BookOpenCheck, CheckCircle2, Database, Loader2, X } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import {
  cancelImportTask,
  isImportTaskAccepted,
  waitForImportTask,
  type ImportTaskAccepted,
} from '../api/importTasks'
import { formatLanguagePair, languageOptions } from '../constants/languages'
import { refreshGlobalNotifications } from '../utils/notifications'
import { isProgressComplete } from '../utils/progress'
import type {
  TMCollection,
  GlossaryBase,
  GlossaryImportPreview,
  GlossaryImportSummary,
  TMImportPreview,
  TMImportSummary,
  TermBase,
  TermImportPreview,
  TermImportSummary,
} from '../types/api'

type ImportTab = 'tm' | 'glossary' | 'term'
type ImportMode = 'all' | ImportTab
type ImportPreviewRow = { row_index: number, status: string, message: string }
type LimitedImportPreview = {
  valid_rows: number
  total_rows: number
  scanned_rows?: number
  truncated?: boolean
  max_scan_rows?: number
}

const props = withDefaults(defineProps<{
  mode?: ImportMode
  initialTab?: ImportTab
  sourceLanguage?: string | null
  targetLanguage?: string | null
  contextLabel?: string
  defaultTMCollectionId?: string
  defaultGlossaryBaseId?: string
  defaultTermBaseId?: string
  fixedTMCollectionId?: string
  fixedGlossaryBaseId?: string
  fixedTermBaseId?: string
}>(), {
  mode: 'all',
  initialTab: 'tm',
  sourceLanguage: null,
  targetLanguage: null,
  contextLabel: '',
  defaultTMCollectionId: '',
  defaultGlossaryBaseId: '',
  defaultTermBaseId: '',
  fixedTMCollectionId: '',
  fixedGlossaryBaseId: '',
  fixedTermBaseId: '',
})

const emit = defineEmits<{
  imported: [payload: { tab: ImportTab, resourceId?: string }]
}>()
const { t } = useI18n()

const activeTab = ref<ImportTab>(props.mode === 'all' ? props.initialTab : props.mode)

const tmCollections = ref<TMCollection[]>([])
const glossaryBases = ref<GlossaryBase[]>([])
const termBases = ref<TermBase[]>([])
const loadingTMCollections = ref(false)
const loadingGlossaryBases = ref(false)
const loadingTermBases = ref(false)

const tmFileInput = ref<HTMLInputElement | null>(null)
const glossaryFileInput = ref<HTMLInputElement | null>(null)
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
const tmImportTaskId = ref('')
const tmCanceling = ref(false)
const tmImportMessage = ref('')
const tmImportSummary = ref<TMImportSummary | null>(null)
const tmImportPreview = ref<TMImportPreview | null>(null)
const tmKeepDuplicateRowIndexes = ref<Set<number>>(new Set())
const tmSkipHeader = ref(false)

const selectedGlossaryFile = ref<File | null>(null)
const selectedGlossaryBaseId = ref('')
const newGlossaryBaseName = ref('')
const newGlossaryBaseDescription = ref('')
const glossaryImportSourceLanguage = ref('')
const glossaryImportTargetLanguage = ref('')
const glossaryImporting = ref(false)
const glossaryPreviewing = ref(false)
const glossaryUploadPercent = ref(0)
const glossaryImportTaskId = ref('')
const glossaryCanceling = ref(false)
const glossaryImportMessage = ref('')
const glossaryImportSummary = ref<GlossaryImportSummary | null>(null)
const glossaryImportPreview = ref<GlossaryImportPreview | null>(null)
const glossarySkipHeader = ref(false)

const selectedTermFile = ref<File | null>(null)
const selectedTermBaseId = ref('')
const newTermBaseName = ref('')
const newTermBaseDescription = ref('')
const termImportSourceLanguage = ref('')
const termImportTargetLanguage = ref('')
const termImporting = ref(false)
const termPreviewing = ref(false)
const termUploadPercent = ref(0)
const termImportTaskId = ref('')
const termCanceling = ref(false)
const termImportMessage = ref('')
const termImportSummary = ref<TermImportSummary | null>(null)
const termImportPreview = ref<TermImportPreview | null>(null)
const termKeepDuplicateRowIndexes = ref<Set<number>>(new Set())
const termSkipHeader = ref(false)

let tmImportAbortController: AbortController | null = null
let glossaryImportAbortController: AbortController | null = null
let termImportAbortController: AbortController | null = null

const selectedTMCollection = computed(() => (
  tmCollections.value.find((item) => item.id === selectedTMCollectionId.value) ?? null
))

const selectedGlossaryBase = computed(() => (
  glossaryBases.value.find((item) => item.id === selectedGlossaryBaseId.value) ?? null
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
const showGlossaryCreateFields = computed(() => !props.fixedGlossaryBaseId && !selectedGlossaryBaseId.value)
const showTermCreateFields = computed(() => !props.fixedTermBaseId && !selectedTermBaseId.value)
const fixedTMTargetLabel = computed(() => props.contextLabel || selectedTMCollection.value?.name || '当前记忆库')
const fixedGlossaryTargetLabel = computed(() => props.contextLabel || selectedGlossaryBase.value?.name || '当前词汇表')
const fixedTermTargetLabel = computed(() => props.contextLabel || selectedTermBase.value?.name || '当前术语库')
const tmPreviewRowsHidden = computed(() => {
  const preview = tmImportPreview.value
  return preview ? Math.max(0, preview.total_rows - preview.rows.length) : 0
})
const termPreviewRowsHidden = computed(() => {
  const preview = termImportPreview.value
  return preview ? Math.max(0, preview.total_rows - preview.rows.length) : 0
})
const glossaryPreviewRowsHidden = computed(() => {
  const preview = glossaryImportPreview.value
  return preview ? Math.max(0, preview.total_rows - preview.rows.length) : 0
})
const tmKeptDuplicateRows = computed(() => countKeptDuplicateRows(tmImportPreview.value?.rows ?? [], tmKeepDuplicateRowIndexes.value))
const termKeptDuplicateRows = computed(() => countKeptDuplicateRows(termImportPreview.value?.rows ?? [], termKeepDuplicateRowIndexes.value))
const canUploadTMWorkbook = computed(() => Boolean(selectedTMFile.value) && !tmImporting.value)
const canUploadGlossaryWorkbook = computed(() => Boolean(selectedGlossaryFile.value) && !glossaryImporting.value)
const canUploadTermWorkbook = computed(() => Boolean(selectedTermFile.value) && !termImporting.value)

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

watch(() => props.fixedGlossaryBaseId, (value) => {
  if (value) {
    selectedGlossaryBaseId.value = value
  }
}, { immediate: true })

watch(() => props.defaultTMCollectionId, (value) => {
  if (!props.fixedTMCollectionId && value && !selectedTMCollectionId.value) {
    selectedTMCollectionId.value = value
  }
}, { immediate: true })

watch(() => props.defaultGlossaryBaseId, (value) => {
  if (!props.fixedGlossaryBaseId && value && !selectedGlossaryBaseId.value) {
    selectedGlossaryBaseId.value = value
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
    if (sourceLanguage && !glossaryImportSourceLanguage.value) {
      glossaryImportSourceLanguage.value = sourceLanguage
    }
    if (targetLanguage && !glossaryImportTargetLanguage.value) {
      glossaryImportTargetLanguage.value = targetLanguage
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

watch(selectedGlossaryBase, (glossaryBase) => {
  if (!glossaryBase) {
    return
  }
  resetGlossaryImport()
  glossaryImportSourceLanguage.value = glossaryBase.source_language
  glossaryImportTargetLanguage.value = glossaryBase.target_language
})

watch([selectedGlossaryBaseId, glossaryImportSourceLanguage, glossaryImportTargetLanguage], () => {
  resetGlossaryImport()
})

watch([glossaryImportSourceLanguage, glossaryImportTargetLanguage], () => {
  ensureDefaultGlossaryBaseSelection()
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

function isAbortError(error: unknown) {
  return (
    error instanceof DOMException && error.name === 'AbortError'
  ) || (
    axios.isCancel(error)
  ) || (
    axios.isAxiosError(error) && error.code === 'ERR_CANCELED'
  )
}

function resetTMImportTaskState() {
  tmImportTaskId.value = ''
  tmCanceling.value = false
  tmImportAbortController = null
}

function resetGlossaryImportTaskState() {
  glossaryImportTaskId.value = ''
  glossaryCanceling.value = false
  glossaryImportAbortController = null
}

function resetTermImportTaskState() {
  termImportTaskId.value = ''
  termCanceling.value = false
  termImportAbortController = null
}

async function cancelTMImport() {
  if (!tmImporting.value) return
  tmCanceling.value = true
  tmImportMessage.value = '正在停止记忆库导入...'
  if (tmImportTaskId.value) {
    await cancelImportTask(tmImportTaskId.value).catch(() => undefined)
  }
  tmImportAbortController?.abort()
}

async function cancelGlossaryImport() {
  if (!glossaryImporting.value) return
  glossaryCanceling.value = true
  glossaryImportMessage.value = '正在停止词汇表导入...'
  if (glossaryImportTaskId.value) {
    await cancelImportTask(glossaryImportTaskId.value).catch(() => undefined)
  }
  glossaryImportAbortController?.abort()
}

async function cancelTermImport() {
  if (!termImporting.value) return
  termCanceling.value = true
  termImportMessage.value = '正在停止术语库导入...'
  if (termImportTaskId.value) {
    await cancelImportTask(termImportTaskId.value).catch(() => undefined)
  }
  termImportAbortController?.abort()
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

function buildPreviewCompleteMessage(resourceName: string, preview: LimitedImportPreview) {
  const scannedRows = preview.scanned_rows ?? preview.total_rows
  const limit = preview.max_scan_rows ?? scannedRows
  const suffix = preview.truncated
    ? `；已达到预览上限 ${limit} 行，导入时仍会分批处理完整文件。`
    : ''
  return `预览完成：已扫描 ${scannedRows} 行，识别 ${preview.valid_rows} 条有效${resourceName}${suffix}`
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

function findMatchingGlossaryBaseId() {
  const sourceLanguage = glossaryImportSourceLanguage.value || props.sourceLanguage || ''
  const targetLanguage = glossaryImportTargetLanguage.value || props.targetLanguage || ''
  if (!sourceLanguage || !targetLanguage) {
    return ''
  }
  return glossaryBases.value.find((glossaryBase) => (
    glossaryBase.source_language === sourceLanguage
    && glossaryBase.target_language === targetLanguage
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

function ensureDefaultGlossaryBaseSelection() {
  if (props.fixedGlossaryBaseId) {
    selectedGlossaryBaseId.value = props.fixedGlossaryBaseId
    return
  }
  if (selectedGlossaryBaseId.value) {
    return
  }
  const defaultId = props.defaultGlossaryBaseId || findMatchingGlossaryBaseId()
  if (defaultId && glossaryBases.value.some((glossaryBase) => glossaryBase.id === defaultId)) {
    selectedGlossaryBaseId.value = defaultId
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

async function loadGlossaryBases() {
  loadingGlossaryBases.value = true
  try {
    const { data } = await http.get<GlossaryBase[]>('/glossary-bases')
    glossaryBases.value = data
    if (!props.fixedGlossaryBaseId && selectedGlossaryBaseId.value && !data.some((item) => item.id === selectedGlossaryBaseId.value)) {
      selectedGlossaryBaseId.value = ''
    }
    ensureDefaultGlossaryBaseSelection()
  } catch (error) {
    glossaryImportMessage.value = getErrorMessage(error, '词汇表列表加载失败。')
  } finally {
    loadingGlossaryBases.value = false
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

async function createGlossaryBase(
  name: string,
  description = '',
  sourceLanguage = '',
  targetLanguage = '',
) {
  const cleanedName = name.trim()
  if (!cleanedName) {
    throw new Error('词汇表名称不能为空。')
  }
  ensureLanguagePair(sourceLanguage, targetLanguage)
  const { data } = await http.post<GlossaryBase>('/glossary-bases', {
    name: cleanedName,
    description: description.trim() || null,
    source_language: sourceLanguage,
    target_language: targetLanguage,
  })
  await loadGlossaryBases()
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

function onGlossaryFileChange(event: Event) {
  selectedGlossaryFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  resetGlossaryImport()
  if (selectedGlossaryFile.value && !selectedGlossaryBaseId.value && !newGlossaryBaseName.value.trim()) {
    newGlossaryBaseName.value = fileBaseName(selectedGlossaryFile.value)
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
  tmImportSummary.value = null
  tmImportMessage.value = ''
}

function resetGlossaryImport() {
  glossaryImportSummary.value = null
  glossaryImportMessage.value = ''
}

function buildTMImportFormData(collectionId?: string) {
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
  formData.append('duplicate_policy', 'keep')
  formData.append('skip_header', tmSkipHeader.value ? 'true' : 'false')
  return formData
}

function buildGlossaryImportFormData(glossaryBaseId?: string) {
  if (!selectedGlossaryFile.value) {
    throw new Error('请先选择要导入的词汇表文件。')
  }
  const formData = new FormData()
  formData.append('file', selectedGlossaryFile.value)
  if (glossaryBaseId) {
    formData.append('glossary_base_id', glossaryBaseId)
  }
  formData.append('source_language', glossaryImportSourceLanguage.value)
  formData.append('target_language', glossaryImportTargetLanguage.value)
  formData.append('skip_header', glossarySkipHeader.value ? 'true' : 'false')
  return formData
}

async function previewGlossaryWorkbook() {
  if (!selectedGlossaryFile.value) {
    glossaryImportMessage.value = '请先选择要导入的词汇表文件。'
    return
  }

  try {
    ensureLanguagePair(glossaryImportSourceLanguage.value, glossaryImportTargetLanguage.value)
  } catch (error) {
    resetGlossaryImport()
    glossaryImportMessage.value = error instanceof Error ? error.message : t('resourceImport.errors.selectLanguage')
    return
  }

  glossaryPreviewing.value = true
  glossaryImportMessage.value = ''
  glossaryImportSummary.value = null

  try {
    const glossaryBaseId = selectedGlossaryBaseId.value || props.fixedGlossaryBaseId
    const formData = buildGlossaryImportFormData(glossaryBaseId)
    formData.append('preview_limit', '500')
    const { data } = await http.post<GlossaryImportPreview>('/glossary-bases/import/preview', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    glossaryImportPreview.value = data
    glossaryImportMessage.value = buildPreviewCompleteMessage('词汇', data)
  } catch (error) {
    glossaryImportPreview.value = null
    glossaryImportMessage.value = getErrorMessage(error, '词汇表预览失败。')
  } finally {
    glossaryPreviewing.value = false
  }
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
    const { data } = await http.post<TMImportPreview>('/translation-memory/import/preview', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    tmImportPreview.value = data
    tmKeepDuplicateRowIndexes.value = getInitialKeepDuplicateRowIndexes(data.rows)
    tmImportMessage.value = buildPreviewCompleteMessage('记忆', data)
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
  formData.append('skip_header', termSkipHeader.value ? 'true' : 'false')
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
    const { data } = await http.post<TermImportPreview>('/term-bases/import/preview', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    termImportPreview.value = data
    termKeepDuplicateRowIndexes.value = getInitialKeepDuplicateRowIndexes(data.rows)
    termImportMessage.value = buildPreviewCompleteMessage('术语', data)
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

async function ensureImportGlossaryBase() {
  if (props.fixedGlossaryBaseId) {
    return props.fixedGlossaryBaseId
  }
  if (selectedGlossaryBaseId.value) {
    return selectedGlossaryBaseId.value
  }
  const fallbackName = selectedGlossaryFile.value ? fileBaseName(selectedGlossaryFile.value) : ''
  const glossaryBase = await createGlossaryBase(
    newGlossaryBaseName.value || fallbackName,
    newGlossaryBaseDescription.value,
    glossaryImportSourceLanguage.value,
    glossaryImportTargetLanguage.value,
  )
  selectedGlossaryBaseId.value = glossaryBase.id
  return glossaryBase.id
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
  tmImportTaskId.value = ''
  tmCanceling.value = false
  tmImportMessage.value = ''
  tmImportSummary.value = null
  tmImportAbortController = new AbortController()

  try {
    const collectionId = await ensureImportCollection()
    const formData = buildTMImportFormData(collectionId)

    const { data } = await http.post<TMImportSummary | ImportTaskAccepted>('/translation-memory/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      signal: tmImportAbortController.signal,
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        tmUploadPercent.value = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
      },
    })
    if (isImportTaskAccepted(data)) {
      tmImportTaskId.value = data.task_id
    }
    const summary = isImportTaskAccepted(data)
      ? await waitForImportTask<TMImportSummary>(
        data.task_id,
        (status) => {
          tmImportTaskId.value = status.task_id
          tmUploadPercent.value = status.progress
          tmImportMessage.value = status.message || '记忆库导入处理中。'
        },
        { signal: tmImportAbortController.signal },
      )
      : data

    tmImportSummary.value = summary
    tmImportPreview.value = null
    tmImportMessage.value = t('resourceImport.tm.success.imported', { filename: summary.filename })
    refreshGlobalNotifications()
    selectedTMFile.value = null
    if (tmFileInput.value) {
      tmFileInput.value.value = ''
    }
    newTMCollectionName.value = ''
    newTMCollectionDescription.value = ''
    emit('imported', { tab: 'tm', resourceId: summary.collection_id || collectionId })
    await loadTMCollections()
  } catch (error) {
    tmImportMessage.value = isAbortError(error)
      ? '记忆库导入已停止。'
      : getErrorMessage(error, t('resourceImport.tm.errors.importFailed'))
  } finally {
    tmImporting.value = false
    tmUploadPercent.value = 0
    resetTMImportTaskState()
  }
}

async function uploadGlossaryWorkbook() {
  if (!selectedGlossaryFile.value) {
    glossaryImportMessage.value = '请先选择要导入的词汇表文件。'
    return
  }

  try {
    ensureLanguagePair(glossaryImportSourceLanguage.value, glossaryImportTargetLanguage.value)
  } catch (error) {
    glossaryImportMessage.value = error instanceof Error ? error.message : t('resourceImport.errors.selectLanguage')
    glossaryImportSummary.value = null
    return
  }

  glossaryImporting.value = true
  glossaryUploadPercent.value = 0
  glossaryImportTaskId.value = ''
  glossaryCanceling.value = false
  glossaryImportMessage.value = ''
  glossaryImportSummary.value = null
  glossaryImportAbortController = new AbortController()

  try {
    const glossaryBaseId = await ensureImportGlossaryBase()
    const formData = buildGlossaryImportFormData(glossaryBaseId)

    const { data } = await http.post<GlossaryImportSummary | ImportTaskAccepted>('/glossary-bases/import-xlsx', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      signal: glossaryImportAbortController.signal,
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        glossaryUploadPercent.value = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
      },
    })
    if (isImportTaskAccepted(data)) {
      glossaryImportTaskId.value = data.task_id
    }
    const summary = isImportTaskAccepted(data)
      ? await waitForImportTask<GlossaryImportSummary>(
        data.task_id,
        (status) => {
          glossaryImportTaskId.value = status.task_id
          glossaryUploadPercent.value = status.progress
          glossaryImportMessage.value = status.message || '词汇表导入处理中。'
        },
        { signal: glossaryImportAbortController.signal },
      )
      : data

    glossaryImportSummary.value = summary
    glossaryImportPreview.value = null
    glossaryImportMessage.value = `导入完成：${summary.filename}`
    refreshGlobalNotifications()
    selectedGlossaryFile.value = null
    if (glossaryFileInput.value) {
      glossaryFileInput.value.value = ''
    }
    newGlossaryBaseName.value = ''
    newGlossaryBaseDescription.value = ''
    emit('imported', { tab: 'glossary', resourceId: summary.glossary_base_id || glossaryBaseId })
    await loadGlossaryBases()
  } catch (error) {
    glossaryImportMessage.value = isAbortError(error)
      ? '词汇表导入已停止。'
      : getErrorMessage(error, '词汇表导入失败。')
  } finally {
    glossaryImporting.value = false
    glossaryUploadPercent.value = 0
    resetGlossaryImportTaskState()
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
  termImportTaskId.value = ''
  termCanceling.value = false
  termImportMessage.value = ''
  termImportSummary.value = null
  termImportAbortController = new AbortController()

  try {
    const termBaseId = await ensureImportTermBase()
    const formData = buildTermImportFormData(termBaseId)

    const { data } = await http.post<TermImportSummary | ImportTaskAccepted>('/term-bases/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      signal: termImportAbortController.signal,
      onUploadProgress: (event) => {
        const total = event.total || 0
        const loaded = event.loaded || 0
        termUploadPercent.value = total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : 0
      },
    })
    if (isImportTaskAccepted(data)) {
      termImportTaskId.value = data.task_id
    }
    const summary = isImportTaskAccepted(data)
      ? await waitForImportTask<TermImportSummary>(
        data.task_id,
        (status) => {
          termImportTaskId.value = status.task_id
          termUploadPercent.value = status.progress
          termImportMessage.value = status.message || '术语库导入处理中。'
        },
        { signal: termImportAbortController.signal },
      )
      : data

    termImportSummary.value = summary
    termImportPreview.value = null
    termImportMessage.value = t('resourceImport.term.success.imported', { filename: summary.filename })
    refreshGlobalNotifications()
    selectedTermFile.value = null
    if (termFileInput.value) {
      termFileInput.value.value = ''
    }
    newTermBaseName.value = ''
    newTermBaseDescription.value = ''
    emit('imported', { tab: 'term', resourceId: summary.term_base_id || termBaseId })
    await loadTermBases()
  } catch (error) {
    termImportMessage.value = isAbortError(error)
      ? '术语库导入已停止。'
      : getErrorMessage(error, t('resourceImport.term.errors.importFailed'))
  } finally {
    termImporting.value = false
    termUploadPercent.value = 0
    resetTermImportTaskState()
  }
}

onMounted(() => {
  if (props.mode === 'all' || props.mode === 'tm') {
    void loadTMCollections()
  }
  if (props.mode === 'all' || props.mode === 'glossary') {
    void loadGlossaryBases()
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
        :class="{ 'is-active': activeTab === 'glossary' }"
        type="button"
        @click="activeTab = 'glossary'"
      >
        <BookOpen :size="14" />
        {{ t('resourceImport.tabs.glossary') }}
      </button>
      <button
        class="tab-item"
        :class="{ 'is-active': activeTab === 'term' }"
        type="button"
        @click="activeTab = 'term'"
      >
        <BookOpenCheck :size="14" />
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

        <label class="resource-import-panel__toggle">
          <input v-model="tmSkipHeader" type="checkbox" />
          <span class="resource-import-panel__toggle-control" aria-hidden="true" />
          <span>
            <strong>{{ t('resourceImport.options.skipHeader') }}</strong>
            <small>{{ t('resourceImport.options.skipHeaderHint') }}</small>
          </span>
        </label>
      </div>

      <div class="resource-import-panel__actions">
        <button
          class="button button--primary"
          type="button"
          :disabled="!canUploadTMWorkbook"
          @click="uploadTMWorkbook"
        >
          <Loader2 v-if="tmImporting" class="lucide-spin" />
          <CheckCircle2 v-else :size="14" />
          {{ tmImporting ? t('resourceImport.tm.importing', { percent: tmUploadPercent }) : '直接导入' }}
        </button>
        <button
          v-if="tmImporting"
          class="button button--danger"
          type="button"
          :disabled="tmCanceling"
          @click="cancelTMImport"
        >
          <X :size="14" />
          {{ tmCanceling ? '停止中...' : '停止导入' }}
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
        <p v-if="tmPreviewRowsHidden > 0 || tmImportPreview.truncated" class="hint-text">
          仅显示前 {{ tmImportPreview.rows.length }} 行；预览最多扫描 {{ tmImportPreview.max_scan_rows || tmImportPreview.scanned_rows }} 行，完整文件会在导入时分批处理。
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

    <section v-else-if="activeTab === 'glossary'" class="resource-import-panel__section">
      <p class="hint-text">
        {{ t('resourceImport.glossary.intro') }}
      </p>

      <div class="upload-form form-grid-2 resource-import-panel__form">
        <label class="field">
          <span class="field__label">{{ t('resourceImport.glossary.target') }}</span>
          <select
            v-model="selectedGlossaryBaseId"
            class="field__control"
            :disabled="loadingGlossaryBases || Boolean(props.fixedGlossaryBaseId)"
          >
            <template v-if="props.fixedGlossaryBaseId">
              <option :value="props.fixedGlossaryBaseId">{{ fixedGlossaryTargetLabel }}（当前词汇表）</option>
            </template>
            <template v-else>
              <option value="">{{ t('resourceImport.glossary.createNew') }}</option>
              <option
                v-for="glossaryBase in glossaryBases"
                :key="glossaryBase.id"
                :value="glossaryBase.id"
              >
                {{ glossaryBase.name }}（{{ formatLanguagePair(glossaryBase.source_language, glossaryBase.target_language) }} / {{ glossaryBase.entry_count }} 条）
              </option>
            </template>
          </select>
        </label>

        <label class="field">
          <span class="field__label">{{ t('resourceImport.glossary.file') }}</span>
          <input
            ref="glossaryFileInput"
            class="field__control"
            type="file"
            accept=".xlsx"
            @change="onGlossaryFileChange"
          />
        </label>

        <label v-if="showGlossaryCreateFields" class="field">
          <span class="field__label">{{ t('resourceImport.glossary.newName') }}</span>
          <input
            v-model="newGlossaryBaseName"
            class="field__control"
            type="text"
            :placeholder="t('resourceImport.glossary.newNamePlaceholder')"
          />
        </label>

        <label v-if="showGlossaryCreateFields" class="field">
          <span class="field__label">{{ t('resourceImport.glossary.description') }}</span>
          <input
            v-model="newGlossaryBaseDescription"
            class="field__control"
            type="text"
            :placeholder="t('resourceImport.glossary.descriptionPlaceholder')"
          />
        </label>

        <label class="field">
          <span class="field__label">{{ t('resourceImport.glossary.sourceLanguage') }}</span>
          <select v-model="glossaryImportSourceLanguage" class="field__control">
            <option value="">{{ t('resourceImport.glossary.selectLanguage') }}</option>
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
          <span class="field__label">{{ t('resourceImport.glossary.targetLanguage') }}</span>
          <select v-model="glossaryImportTargetLanguage" class="field__control">
            <option value="">{{ t('resourceImport.glossary.selectLanguage') }}</option>
            <option
              v-for="option in languageOptions"
              :key="option.code"
              :value="option.code"
            >
              {{ option.label }}
            </option>
          </select>
        </label>

        <label class="resource-import-panel__toggle">
          <input v-model="glossarySkipHeader" type="checkbox" />
          <span class="resource-import-panel__toggle-control" aria-hidden="true" />
          <span>
            <strong>{{ t('resourceImport.options.skipHeader') }}</strong>
            <small>{{ t('resourceImport.options.skipHeaderHint') }}</small>
          </span>
        </label>
      </div>

      <div class="resource-import-panel__actions">
        <button
          class="button button--primary"
          type="button"
          :disabled="!canUploadGlossaryWorkbook"
          @click="uploadGlossaryWorkbook"
        >
          <Loader2 v-if="glossaryImporting" class="lucide-spin" />
          <CheckCircle2 v-else :size="14" />
          {{ glossaryImporting ? t('resourceImport.glossary.importing', { percent: glossaryUploadPercent }) : '直接导入' }}
        </button>
        <button
          v-if="glossaryImporting"
          class="button button--danger"
          type="button"
          :disabled="glossaryCanceling"
          @click="cancelGlossaryImport"
        >
          <X :size="14" />
          {{ glossaryCanceling ? '停止中...' : '停止导入' }}
        </button>
      </div>

      <div v-if="glossaryImporting" class="resource-import-panel__progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div
              class="progress-bar__fill"
              :class="{ 'is-complete': isProgressComplete(glossaryUploadPercent) }"
              :style="{ width: `${glossaryUploadPercent}%` }"
            />
          </div>
          <span class="progress-bar__text">{{ glossaryUploadPercent }}%</span>
        </div>
      </div>

      <p
        v-if="glossaryImportMessage"
        class="form-message"
        :class="{ 'is-error': !glossaryImportSummary && !glossaryImportPreview }"
      >
        {{ glossaryImportMessage }}
      </p>

      <div v-if="glossaryImportPreview" class="resource-import-panel__preview">
        <div class="resource-import-panel__preview-head">
          <div>
            <div class="section-title">导入预览</div>
            <p class="hint-text">
              {{ glossaryImportPreview.filename }}，将导入到 {{ glossaryImportPreview.glossary_base_name || (newGlossaryBaseName || '新词汇表') }}
            </p>
          </div>
          <div class="resource-import-panel__preview-stats">
            <span>有效 {{ glossaryImportPreview.valid_rows }}</span>
            <span>新增 {{ glossaryImportPreview.create_rows }}</span>
            <span>覆盖 {{ glossaryImportPreview.update_rows }}</span>
            <span>重复 {{ glossaryImportPreview.duplicate_rows }}</span>
            <span>跳过 {{ glossaryImportPreview.skipped_empty_rows + glossaryImportPreview.skipped_header_rows }}</span>
          </div>
        </div>

        <div class="resource-import-panel__preview-table-wrap">
          <table class="resource-import-panel__preview-table resource-import-panel__preview-table--glossary">
            <thead>
              <tr>
                <th>行号</th>
                <th>原文</th>
                <th>译文</th>
                <th>备注</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in glossaryImportPreview.rows" :key="row.row_index" :class="`is-${row.status}`">
                <td>{{ row.row_index }}</td>
                <td>{{ row.source_text || '-' }}</td>
                <td>{{ row.target_text || '-' }}</td>
                <td>{{ row.note || '-' }}</td>
                <td>
                  <strong>{{ row.message }}</strong>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="glossaryPreviewRowsHidden > 0 || glossaryImportPreview.truncated" class="hint-text">
          仅显示前 {{ glossaryImportPreview.rows.length }} 行；预览最多扫描 {{ glossaryImportPreview.max_scan_rows || glossaryImportPreview.scanned_rows }} 行，完整文件会在导入时分批处理。
        </p>
      </div>

      <div v-if="glossaryImportSummary" class="resource-import-panel__summary">
        <div class="section-title">{{ t('resourceImport.glossary.summary.title') }}</div>
        <div class="summary-grid summary-grid--wide">
          <div class="summary-item">
            <strong>{{ glossaryImportSummary.glossary_base_name }}</strong>
            <span>{{ t('resourceImport.glossary.summary.target') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ formatLanguagePair(glossaryImportSummary.source_language, glossaryImportSummary.target_language) }}</strong>
            <span>{{ t('resourceImport.glossary.summary.pair') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ glossaryImportSummary.imported_rows }}</strong>
            <span>{{ t('resourceImport.glossary.summary.importedRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ glossaryImportSummary.created_rows }}</strong>
            <span>{{ t('resourceImport.glossary.summary.createdRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ glossaryImportSummary.updated_rows }}</strong>
            <span>{{ t('resourceImport.glossary.summary.updatedRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ glossaryImportSummary.skipped_header_rows }}</strong>
            <span>{{ t('resourceImport.glossary.summary.skippedHeaderRows') }}</span>
          </div>
          <div class="summary-item">
            <strong>{{ glossaryImportSummary.skipped_empty_rows }}</strong>
            <span>{{ t('resourceImport.glossary.summary.skippedEmptyRows') }}</span>
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
            accept=".tmx,.tbx,.xls,.xlsx,.csv"
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

        <label class="resource-import-panel__toggle">
          <input v-model="termSkipHeader" type="checkbox" />
          <span class="resource-import-panel__toggle-control" aria-hidden="true" />
          <span>
            <strong>{{ t('resourceImport.options.skipHeader') }}</strong>
            <small>{{ t('resourceImport.options.skipHeaderHint') }}</small>
          </span>
        </label>
      </div>

      <div class="resource-import-panel__actions">
        <button
          class="button button--primary"
          type="button"
          :disabled="!canUploadTermWorkbook"
          @click="uploadTermWorkbook"
        >
          <Loader2 v-if="termImporting" class="lucide-spin" />
          <CheckCircle2 v-else :size="14" />
          {{ termImporting ? t('resourceImport.term.importing', { percent: termUploadPercent }) : '直接导入' }}
        </button>
        <button
          v-if="termImporting"
          class="button button--danger"
          type="button"
          :disabled="termCanceling"
          @click="cancelTermImport"
        >
          <X :size="14" />
          {{ termCanceling ? '停止中...' : '停止导入' }}
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
        <p v-if="termPreviewRowsHidden > 0 || termImportPreview.truncated" class="hint-text">
          仅显示前 {{ termImportPreview.rows.length }} 行；预览最多扫描 {{ termImportPreview.max_scan_rows || termImportPreview.scanned_rows }} 行，完整文件会在导入时分批处理。
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

.resource-import-panel__toggle {
  --resource-toggle-off: color-mix(in srgb, var(--state-danger) 72%, var(--text-muted) 28%);
  --resource-toggle-off-strong: color-mix(in srgb, var(--state-danger) 82%, var(--text-secondary) 18%);
  --resource-toggle-on: var(--state-success);
  --resource-toggle-on-soft: color-mix(in srgb, var(--state-success) 78%, var(--surface-panel) 22%);

  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 9px 12px;
  border: 1px solid color-mix(in srgb, var(--brand-500) 18%, var(--line-soft));
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-1) 88%, var(--brand-050) 12%);
  color: var(--text-primary);
}

.resource-import-panel__toggle input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.resource-import-panel__toggle-control {
  position: relative;
  width: 42px;
  height: 24px;
  flex-shrink: 0;
  border: 1px solid var(--resource-toggle-off-strong);
  border-radius: 999px;
  background: linear-gradient(135deg, var(--resource-toggle-off-strong), var(--resource-toggle-off));
  box-shadow:
    inset 0 1px 2px rgba(17, 49, 42, 0.12),
    0 0 0 2px color-mix(in srgb, var(--resource-toggle-off) 8%, transparent);
  transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}

.resource-import-panel__toggle-control::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: #ffffff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.2);
  transition: transform 0.18s ease;
}

.resource-import-panel__toggle input:checked + .resource-import-panel__toggle-control {
  border-color: var(--resource-toggle-on);
  background: linear-gradient(135deg, var(--resource-toggle-on), var(--resource-toggle-on-soft));
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--resource-toggle-on) 14%, transparent);
}

.resource-import-panel__toggle input:checked + .resource-import-panel__toggle-control::after {
  transform: translateX(18px);
}

.resource-import-panel__toggle input:focus-visible + .resource-import-panel__toggle-control {
  outline: 2px solid color-mix(in srgb, var(--brand-700) 24%, transparent);
  outline-offset: 2px;
}

.resource-import-panel__toggle strong,
.resource-import-panel__toggle small {
  display: block;
  line-height: 1.25;
}

.resource-import-panel__toggle small {
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 12px;
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
