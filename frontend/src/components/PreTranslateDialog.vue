<script setup lang="ts">
import { AlertTriangle, BookOpen, BookOpenCheck, Bot, Check, Database, Loader2, Pause, Search, Sparkles, Upload, X } from 'lucide-vue-next'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import { canonicalizeLanguagePair, formatLanguagePair } from '../constants/languages'
import { defaultLLMModelId, llmModelOptions as baseLLMModelOptions } from '../constants/llm'
import { pushToast } from '../composables/useToast'
import type { GlossaryBase, GuidelineTemplateSummary, LLMProvider, LLMTranslateScope, TermBase, TMCollection } from '../types/api'
import { consumeLLMStream } from '../utils/llmStream'
import { isProgressComplete } from '../utils/progress'
import ResourceImportDialog from './ResourceImportDialog.vue'
import Modal from './base/Modal.vue'

interface ProjectFileItem {
  id: string
  filename: string
  source_language: string | null
  target_language: string | null
  collection_id?: string | null
  collection_ids?: string[]
  glossary_base_ids?: string[]
  term_base_id?: string | null
  term_base_ids?: string[]
  tm_match_threshold?: number
  is_edit_locked?: boolean
  active_operation_message?: string
}

interface PreTranslateProgressPayload {
  fileId: string
  progress: number
  status: string
  running: boolean
}

interface FileOperationLockResponse {
  id: string
  token: string
  active_operation: string | null
  active_operation_message: string
  is_edit_locked: boolean
}

interface PretranslationTaskStatus {
  id: string
  run_id: string
  file_record_id: string
  filename?: string | null
  status: string
  stage: string
  progress: number
  message: string
  provider?: string | null
  model?: string | null
  scope?: string | null
  total_segments: number
  unique_segments: number
  deduplicated_segments: number
  processed_segments: number
  updated_segments: number
  error_segments: number
  current_action?: string | null
  cancel_requested: boolean
  error?: string | null
  updated_at?: string | null
  last_heartbeat_at?: string | null
}

interface PretranslationRunStatus {
  id: string
  project_id: string
  status: string
  progress: number
  message: string
  total_files: number
  completed_files: number
  failed_files: number
  canceled_files: number
  tasks: PretranslationTaskStatus[]
}

type ResourceImportTab = 'tm' | 'glossary' | 'term'
type LanguagePairStat = {
  source: string | null
  target: string | null
  fileCount: number
}

const props = defineProps<{
  open: boolean
  projectId: string | null
  files: ProjectFileItem[]
  sourceLanguage: string | null
  targetLanguage: string | null
  translationGuidelines: string
}>()

const emit = defineEmits<{
  close: []
  done: []
  progress: [payload: PreTranslateProgressPayload]
}>()

const FILE_OPERATION_TOKEN_HEADER = 'X-File-Operation-Token'
const LOCK_HEARTBEAT_INTERVAL_MS = 30_000
const LLM_STREAM_IDLE_TIMEOUT_MS = 150_000
const RELEASE_LOCK_RETRY_DELAYS_MS = [600, 1_500, 3_000]
const PRETRANSLATION_POLL_INTERVAL_MS = 2_000
const ACTIVE_PRETRANSLATION_STATUSES = new Set(['queued', 'running', 'canceling'])

const { t } = useI18n()

const loadingResources = ref(false)
const running = ref(false)
const stopRequested = ref(false)
const currentAbortController = ref<AbortController | null>(null)
const currentLLMReader = ref<ReadableStreamDefaultReader<Uint8Array> | null>(null)

const tmCollections = ref<TMCollection[]>([])
const termBases = ref<TermBase[]>([])
const glossaryBases = ref<GlossaryBase[]>([])
const guidelineTemplates = ref<GuidelineTemplateSummary[]>([])

const useTm = ref(true)
const tmCollectionIds = ref<string[]>([])
const tmSearchQuery = ref('')
const tmThreshold = ref(0.75)
const tmSkipConfirmed = ref(true)
const tmOverwriteFuzzy = ref(true)
const tmAutoConfirmExact = ref(true)

const useLlm = ref(false)
const llmScope = ref<LLMTranslateScope>('all')
const llmProvider = ref<LLMProvider>('openrouter')
const llmModel = ref(defaultLLMModelId)
const llmGuidelines = ref('')
const selectedGuidelineTemplateId = ref('')
const importingGuidelineTemplate = ref(false)
const guidelineTemplateInputRef = ref<HTMLInputElement | null>(null)

const useTermBase = ref(false)
const termBaseIds = ref<string[]>([])
const termBaseSearchQuery = ref('')

const useGlossary = ref(false)
const glossaryBaseIds = ref<string[]>([])
const glossarySearchQuery = ref('')

const errorMessage = ref('')
const finishedCount = ref(0)
const runFiles = ref<ProjectFileItem[]>([])
const activeResourceImportTab = ref<ResourceImportTab | null>(null)

const progressByFileId = ref<Record<string, number>>({})
const statusByFileId = ref<Record<string, string>>({})
const activeRunId = ref('')
const activeTaskIdsByFileId = ref<Record<string, string>>({})
const pretranslationPollTimer = ref<number | null>(null)

const llmProviderOptions = computed<Array<{ value: LLMProvider, label: string }>>(() => [
  { value: 'openrouter', label: t('projectDetail.preTranslate.llm.providers.openrouter') },
  { value: 'deepseek', label: t('projectDetail.preTranslate.llm.providers.deepseek') },
  { value: 'auto', label: t('projectDetail.preTranslate.llm.providers.auto') },
])

const llmModelOptions = computed(() => [
  { id: '', name: t('projectDetail.preTranslate.llm.modelDefault') },
  ...baseLLMModelOptions.filter((option) => (
    llmProvider.value === 'auto' || option.provider === llmProvider.value
  )),
])

const llmScopeOptions = computed<Array<{ value: LLMTranslateScope, label: string }>>(() => [
  { value: 'all', label: t('projectDetail.preTranslate.llm.scopes.all') },
  { value: 'fuzzy_only', label: t('projectDetail.preTranslate.llm.scopes.fuzzyOnly') },
  { value: 'none_only', label: t('projectDetail.preTranslate.llm.scopes.noneOnly') },
  { value: 'empty_target_only', label: t('projectDetail.preTranslate.llm.scopes.emptyTargetOnly') },
  { value: 'all_with_exact', label: t('projectDetail.preTranslate.llm.scopes.allWithExact') },
])

const selectedFileLanguagePairs = computed(() => {
  const pairMap = new Map<string, { source: string, target: string }>()
  for (const file of props.files) {
    const pair = canonicalizeLanguagePair(
      file.source_language || props.sourceLanguage,
      file.target_language || props.targetLanguage,
    )
    if (!pair) {
      continue
    }
    pairMap.set(`${pair.source}__${pair.target}`, pair)
  }
  return Array.from(pairMap.values())
})

const selectedFileLanguagePairStats = computed<LanguagePairStat[]>(() => {
  const pairMap = new Map<string, LanguagePairStat>()
  let invalidCount = 0
  for (const file of props.files) {
    const pair = canonicalizeLanguagePair(
      file.source_language || props.sourceLanguage,
      file.target_language || props.targetLanguage,
    )
    if (!pair) {
      invalidCount += 1
      continue
    }
    const key = `${pair.source}__${pair.target}`
    const current = pairMap.get(key)
    if (current) {
      current.fileCount += 1
    } else {
      pairMap.set(key, {
        source: pair.source,
        target: pair.target,
        fileCount: 1,
      })
    }
  }
  const stats = Array.from(pairMap.values())
  if (invalidCount > 0) {
    stats.push({ source: null, target: null, fileCount: invalidCount })
  }
  return stats
})

const filesWithInvalidLanguagePair = computed(() => (
  props.files.filter((file) => !canonicalizeLanguagePair(
    file.source_language || props.sourceLanguage,
    file.target_language || props.targetLanguage,
  ))
))

const selectedFileLanguagePair = computed(() => (
  selectedFileLanguagePairs.value.length === 1 ? selectedFileLanguagePairs.value[0] : null
))

const selectedLanguagePairLabel = computed(() => (
  selectedFileLanguagePair.value
    ? formatLanguagePair(selectedFileLanguagePair.value.source, selectedFileLanguagePair.value.target)
    : (selectedFileLanguagePairs.value.length > 1 ? '混合语言对' : t('common.notSet'))
))

const invalidLanguagePairIssue = computed(() => {
  if (props.files.length === 0) {
    return ''
  }
  if (filesWithInvalidLanguagePair.value.length > 0) {
    return t('projectDetail.preTranslate.errors.fileLanguagePairRequired')
  }
  return ''
})

const mixedLanguagePairIssue = computed(() => {
  if (props.files.length === 0) {
    return ''
  }
  if (selectedFileLanguagePairs.value.length > 1) {
    return t('projectDetail.preTranslate.errors.fileLanguagePairMixed', {
      count: selectedFileLanguagePairs.value.length,
    })
  }
  return ''
})

const languagePairIssue = computed(() => invalidLanguagePairIssue.value || mixedLanguagePairIssue.value)

const usesLanguagePairResources = computed(() => useTm.value || useGlossary.value || useTermBase.value)

const pureLlmMixedLanguagePairNotice = computed(() => {
  if (!useLlm.value || usesLanguagePairResources.value || selectedFileLanguagePairs.value.length <= 1) {
    return ''
  }
  return `已选择 ${selectedFileLanguagePairs.value.length} 个语言对；纯 LLM 会按每个文件自己的语言对逐个执行，不共用 TM、术语库或词汇表。`
})

function formatLanguagePairStat(stat: LanguagePairStat) {
  return `${formatLanguagePair(stat.source, stat.target)} · ${stat.fileCount} 个文件`
}

function resourceMatchesSelectedLanguagePair(resource: TMCollection | TermBase | GlossaryBase) {
  const selectedPair = selectedFileLanguagePair.value
  const resourcePair = canonicalizeLanguagePair(resource.source_language, resource.target_language)
  if (!selectedPair || !resourcePair) {
    return false
  }
  return resourcePair.source === selectedPair.source && resourcePair.target === selectedPair.target
}

const availableTMCollections = computed(() => {
  return tmCollections.value.filter((collection) => resourceMatchesSelectedLanguagePair(collection))
})

const selectedTmCollectionIds = computed(() => (
  normalizeResourceIds(tmCollectionIds.value, availableTMCollections.value)
))

const shouldRunTm = computed(() => useTm.value && selectedTmCollectionIds.value.length > 0)

const availableTermBases = computed(() => {
  return termBases.value.filter((termBase) => resourceMatchesSelectedLanguagePair(termBase))
})

const selectedTermBaseIds = computed(() => (
  normalizeResourceIds(termBaseIds.value, availableTermBases.value)
))

const availableGlossaryBases = computed(() => {
  return glossaryBases.value.filter((glossaryBase) => resourceMatchesSelectedLanguagePair(glossaryBase))
})

const selectedGlossaryBaseIds = computed(() => (
  normalizeResourceIds(glossaryBaseIds.value, availableGlossaryBases.value)
))

const filteredTMCollections = computed(() => (
  filterResources(availableTMCollections.value, tmSearchQuery.value)
))

const filteredTermBases = computed(() => (
  filterResources(availableTermBases.value, termBaseSearchQuery.value)
))

const filteredGlossaryBases = computed(() => (
  filterResources(availableGlossaryBases.value, glossarySearchQuery.value)
))

const hiddenTMCollectionCount = computed(() => (
  Math.max(0, tmCollections.value.length - availableTMCollections.value.length)
))

const hiddenTermBaseCount = computed(() => (
  Math.max(0, termBases.value.length - availableTermBases.value.length)
))

const hiddenGlossaryBaseCount = computed(() => (
  Math.max(0, glossaryBases.value.length - availableGlossaryBases.value.length)
))

const selectedTMCollections = computed(() => {
  const selectedIds = new Set(selectedTmCollectionIds.value)
  return availableTMCollections.value.filter((collection) => selectedIds.has(collection.id))
})

const selectedTermBases = computed(() => {
  const selectedIds = new Set(selectedTermBaseIds.value)
  return availableTermBases.value.filter((termBase) => selectedIds.has(termBase.id))
})

const selectedGlossaryBases = computed(() => {
  const selectedIds = new Set(selectedGlossaryBaseIds.value)
  return availableGlossaryBases.value.filter((glossaryBase) => selectedIds.has(glossaryBase.id))
})

const selectedDisplayFiles = computed(() => (
  running.value && runFiles.value.length > 0 ? runFiles.value : props.files
))
const selectedCount = computed(() => selectedDisplayFiles.value.length)
const selectedFilePreview = computed(() => selectedDisplayFiles.value.slice(0, 4))
const configuredActionCount = computed(() => (
  Number(useTm.value) + Number(useGlossary.value) + Number(useLlm.value) + Number(useTermBase.value)
))
const progressFiles = computed(() => (
  runFiles.value.length > 0 ? runFiles.value : props.files
))

const resourceImportDialogTab = computed<ResourceImportTab>(() => activeResourceImportTab.value || 'tm')

const resourceImportDialogTitle = computed(() => {
  if (resourceImportDialogTab.value === 'tm') {
    return '导入记忆库'
  }
  if (resourceImportDialogTab.value === 'glossary') {
    return '导入词汇表'
  }
  return '导入术语库'
})

const resourceImportContextLabel = computed(() => (
  `预翻译：${selectedLanguagePairLabel.value}`
))
const overallProgress = computed(() => {
  if (!progressFiles.value.length) {
    return 0
  }
  const total = progressFiles.value.reduce((sum, file) => sum + (progressByFileId.value[file.id] || 0), 0)
  return Math.round(total / progressFiles.value.length)
})

function normalizeTMThreshold(value: unknown) {
  const numericValue = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numericValue)) {
    return 0.75
  }
  return Math.min(1, Math.max(0.5, Math.round(numericValue * 100) / 100))
}

function applyTMThresholdDefaultFromFiles() {
  const firstThreshold = props.files.find((file) => file.tm_match_threshold != null)?.tm_match_threshold
  tmThreshold.value = normalizeTMThreshold(firstThreshold ?? tmThreshold.value)
}

function applyResourceDefaultsFromFiles(force = false) {
  applyTMDefaultsFromFiles(force)
  applyTermBaseDefaultsFromFiles(force)
  applyGlossaryDefaultsFromFiles(force)
}

watch(() => props.open, (open) => {
  if (open) {
    void loadResources()
    if (!running.value) {
      resetProgress()
      applyTMThresholdDefaultFromFiles()
      applyResourceDefaultsFromFiles(true)
    }
    errorMessage.value = ''
    stopRequested.value = false
    llmGuidelines.value = props.translationGuidelines || ''
  }
})

watch(availableTMCollections, () => {
  tmCollectionIds.value = normalizeResourceIds(tmCollectionIds.value, availableTMCollections.value)
  applyTMDefaultsFromFiles()
})

watch(availableTermBases, () => {
  termBaseIds.value = normalizeResourceIds(termBaseIds.value, availableTermBases.value)
  applyTermBaseDefaultsFromFiles()
})

watch(availableGlossaryBases, () => {
  glossaryBaseIds.value = normalizeResourceIds(glossaryBaseIds.value, availableGlossaryBases.value)
  applyGlossaryDefaultsFromFiles()
})

watch(llmModel, (modelId) => {
  const selectedModel = baseLLMModelOptions.find((option) => option.id === modelId)
  if (selectedModel) {
    llmProvider.value = selectedModel.provider
  }
})

watch(llmProvider, (provider) => {
  if (!llmModel.value || provider === 'auto') {
    return
  }
  const selectedModel = baseLLMModelOptions.find((option) => option.id === llmModel.value)
  if (selectedModel && selectedModel.provider !== provider) {
    llmModel.value = ''
  }
})

function resetProgress() {
  finishedCount.value = 0
  runFiles.value = []
  progressByFileId.value = {}
  statusByFileId.value = {}
  activeRunId.value = ''
  activeTaskIdsByFileId.value = {}
}

function normalizeProgress(progress: number) {
  const safeProgress = Number.isFinite(progress) ? progress : 0
  return Math.max(0, Math.min(100, Math.round(safeProgress)))
}

function getRunActionCount() {
  return Math.max(
    1,
    Number(shouldRunTm.value) + Number(useGlossary.value) + Number(useLlm.value) + Number(useTermBase.value),
  )
}

function getActionProgress(completedActions: number, actionPercent: number, actionCount: number) {
  return ((completedActions + Math.max(0, Math.min(100, actionPercent)) / 100) / actionCount) * 100
}

function setFileProgress(fileId: string, progress: number, status: string) {
  const normalized = normalizeProgress(progress)
  progressByFileId.value = {
    ...progressByFileId.value,
    [fileId]: normalized,
  }
  statusByFileId.value = {
    ...statusByFileId.value,
    [fileId]: status,
  }
  emit('progress', {
    fileId,
    progress: normalized,
    status,
    running: running.value,
  })
}

function stopPretranslationPolling() {
  if (pretranslationPollTimer.value !== null) {
    window.clearInterval(pretranslationPollTimer.value)
    pretranslationPollTimer.value = null
  }
}

function buildTaskStatusText(task: PretranslationTaskStatus) {
  const parts: string[] = []
  if (task.message) {
    parts.push(task.message)
  } else if (task.status === 'queued') {
    parts.push(t('projectDetail.preTranslate.progress.pending'))
  } else if (task.status === 'running') {
    parts.push(t('projectDetail.preTranslate.progress.running'))
  }
  if (task.provider || task.model) {
    parts.push([task.provider, task.model].filter(Boolean).join(' / '))
  }
  if (task.total_segments > 0 || task.processed_segments > 0) {
    const total = Math.max(task.total_segments, task.processed_segments)
    parts.push(`${task.processed_segments}/${total}`)
  }
  if (task.unique_segments > 0 && task.deduplicated_segments > 0) {
    parts.push(`唯一 ${task.unique_segments}，去重 ${task.deduplicated_segments}`)
  }
  if (task.updated_segments > 0 || task.error_segments > 0) {
    parts.push(`成功 ${task.updated_segments}，失败 ${task.error_segments}`)
  }
  if (task.error && (task.status === 'failed' || task.error_segments > 0)) {
    parts.push(task.error)
  }
  return parts.filter(Boolean).join(' · ')
}

function applyPretranslationRun(run: PretranslationRunStatus) {
  activeRunId.value = run.id
  const nextTaskIdsByFileId = { ...activeTaskIdsByFileId.value }
  let activeCount = 0
  let terminalCount = 0

  for (const task of run.tasks || []) {
    nextTaskIdsByFileId[task.file_record_id] = task.id
    const taskRunning = ACTIVE_PRETRANSLATION_STATUSES.has(task.status)
    if (taskRunning) {
      activeCount += 1
    } else {
      terminalCount += 1
    }
    setFileProgress(
      task.file_record_id,
      normalizeProgress(task.progress),
      buildTaskStatusText(task),
    )
  }

  activeTaskIdsByFileId.value = nextTaskIdsByFileId
  finishedCount.value = terminalCount
  running.value = activeCount > 0
  if (!running.value && run.tasks.length > 0) {
    stopPretranslationPolling()
    emitCurrentProgress(false)
  }
}

async function pollPretranslationRun() {
  if (!activeRunId.value) {
    return
  }
  try {
    const { data } = await http.get<PretranslationRunStatus>(`/pretranslation-runs/${activeRunId.value}`)
    applyPretranslationRun(data)
    if (!ACTIVE_PRETRANSLATION_STATUSES.has(data.status)) {
      await handlePretranslationRunFinished(data)
    }
  } catch (error) {
    stopPretranslationPolling()
    running.value = false
    const message = error instanceof Error ? error.message : t('projectDetail.preTranslate.errors.unknown')
    pushToast({
      tone: 'error',
      title: t('projectDetail.preTranslate.toast.loadFailedTitle'),
      message,
    })
  }
}

function startPretranslationPolling() {
  stopPretranslationPolling()
  pretranslationPollTimer.value = window.setInterval(() => {
    void pollPretranslationRun()
  }, PRETRANSLATION_POLL_INTERVAL_MS)
}

async function handlePretranslationRunFinished(run: PretranslationRunStatus) {
  stopPretranslationPolling()
  running.value = false
  emitCurrentProgress(false)
  activeRunId.value = ''

  if (run.status === 'canceled' || stopRequested.value) {
    pushToast({
      tone: 'info',
      title: t('projectDetail.preTranslate.toast.stoppedTitle'),
      message: t('projectDetail.preTranslate.toast.stoppedMessage'),
    })
  } else if (run.status === 'failed') {
    pushToast({
      tone: 'warn',
      title: t('projectDetail.preTranslate.toast.fileFailedTitle', { name: t('projectDetail.preTranslate.dialogTitle') }),
      message: run.message || t('projectDetail.preTranslate.errors.unknown'),
    })
  } else {
    pushToast({
      tone: 'success',
      title: t('projectDetail.preTranslate.toast.doneTitle'),
      message: t('projectDetail.preTranslate.toast.doneMessage', {
        done: run.completed_files,
        total: Math.max(run.total_files, run.tasks.length, selectedCount.value),
      }),
    })
  }

  emit('done')
}

function emitCurrentProgress(runningState = running.value) {
  for (const file of runFiles.value) {
    const progress = progressByFileId.value[file.id]
    const status = statusByFileId.value[file.id]
    if (typeof progress !== 'number' || !status) {
      continue
    }
    emit('progress', {
      fileId: file.id,
      progress,
      status,
      running: runningState,
    })
  }
}

function requestClose() {
  if (running.value) {
    pushToast({
      tone: 'info',
      title: t('projectDetail.preTranslate.toast.closeRunningTitle'),
      message: t('projectDetail.preTranslate.toast.closeRunningMessage'),
      duration: 5200,
    })
  }
  emit('close')
}

function normalizeResourceIds<T extends { id: string }>(resourceIds: string[], resources: T[]) {
  const availableIds = new Set(resources.map((resource) => resource.id))
  return Array.from(new Set(resourceIds.filter((resourceId) => availableIds.has(resourceId))))
}

function filterResources<T extends { name: string, description: string | null }>(resources: T[], query: string) {
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) {
    return resources
  }
  return resources.filter((resource) => (
    resource.name.toLowerCase().includes(normalizedQuery)
    || (resource.description || '').toLowerCase().includes(normalizedQuery)
  ))
}

function setSelectedResourceId(resourceIds: string[], resourceId: string, checked: boolean) {
  if (checked) {
    return Array.from(new Set([...resourceIds, resourceId]))
  }
  return resourceIds.filter((candidateId) => candidateId !== resourceId)
}

function toggleTmCollection(collectionId: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  tmCollectionIds.value = normalizeResourceIds(
    setSelectedResourceId(tmCollectionIds.value, collectionId, checked),
    availableTMCollections.value,
  )
}

function toggleTermBase(termBaseId: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  termBaseIds.value = normalizeResourceIds(
    setSelectedResourceId(termBaseIds.value, termBaseId, checked),
    availableTermBases.value,
  )
}

function toggleGlossaryBase(glossaryBaseId: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  glossaryBaseIds.value = normalizeResourceIds(
    setSelectedResourceId(glossaryBaseIds.value, glossaryBaseId, checked),
    availableGlossaryBases.value,
  )
}

function selectAllTmCollections() {
  tmCollectionIds.value = availableTMCollections.value.map((collection) => collection.id)
}

function selectAllTermBases() {
  termBaseIds.value = availableTermBases.value.map((termBase) => termBase.id)
}

function selectAllGlossaryBases() {
  glossaryBaseIds.value = availableGlossaryBases.value.map((glossaryBase) => glossaryBase.id)
}

function clearTmCollections() {
  tmCollectionIds.value = []
}

function clearTermBases() {
  termBaseIds.value = []
}

function clearGlossaryBases() {
  glossaryBaseIds.value = []
}

function openResourceImport(tab: ResourceImportTab) {
  activeResourceImportTab.value = tab
}

function closeResourceImport() {
  activeResourceImportTab.value = null
}

async function handleResourceImported(payload: { tab: ResourceImportTab, resourceId?: string }) {
  await loadResources()
  if (payload.tab === 'tm' && payload.resourceId) {
    useTm.value = true
    tmCollectionIds.value = normalizeResourceIds(
      [...tmCollectionIds.value, payload.resourceId],
      availableTMCollections.value,
    )
  }
  if (payload.tab === 'glossary' && payload.resourceId) {
    useGlossary.value = true
    glossaryBaseIds.value = normalizeResourceIds(
      [...glossaryBaseIds.value, payload.resourceId],
      availableGlossaryBases.value,
    )
  }
  if (payload.tab === 'term' && payload.resourceId) {
    useTermBase.value = true
    termBaseIds.value = normalizeResourceIds(
      [...termBaseIds.value, payload.resourceId],
      availableTermBases.value,
    )
  }
  closeResourceImport()
}

function getBoundTMCollectionIdsFromFiles() {
  const orderedIds: string[] = []
  const seenIds = new Set<string>()
  for (const file of props.files) {
    for (const collectionId of file.collection_ids || []) {
      if (!seenIds.has(collectionId)) {
        seenIds.add(collectionId)
        orderedIds.push(collectionId)
      }
    }
    if (file.collection_id && !seenIds.has(file.collection_id)) {
      seenIds.add(file.collection_id)
      orderedIds.push(file.collection_id)
    }
  }
  return orderedIds
}

function getBoundTermBaseIdsFromFiles() {
  const orderedIds: string[] = []
  const seenIds = new Set<string>()
  for (const file of props.files) {
    for (const termBaseId of file.term_base_ids || []) {
      if (!seenIds.has(termBaseId)) {
        seenIds.add(termBaseId)
        orderedIds.push(termBaseId)
      }
    }
    if (file.term_base_id && !seenIds.has(file.term_base_id)) {
      seenIds.add(file.term_base_id)
      orderedIds.push(file.term_base_id)
    }
  }
  return orderedIds
}

function getBoundGlossaryBaseIdsFromFiles() {
  const orderedIds: string[] = []
  const seenIds = new Set<string>()
  for (const file of props.files) {
    for (const glossaryBaseId of file.glossary_base_ids || []) {
      if (!seenIds.has(glossaryBaseId)) {
        seenIds.add(glossaryBaseId)
        orderedIds.push(glossaryBaseId)
      }
    }
  }
  return orderedIds
}

function applyTMDefaultsFromFiles(force = false) {
  if (running.value || (!force && tmCollectionIds.value.length > 0)) {
    return
  }
  const boundIds = normalizeResourceIds(getBoundTMCollectionIdsFromFiles(), availableTMCollections.value)
  if (force || boundIds.length > 0) {
    tmCollectionIds.value = boundIds
    if (boundIds.length > 0) {
      useTm.value = true
    }
  }
}

function applyTermBaseDefaultsFromFiles(force = false) {
  if (running.value || (!force && termBaseIds.value.length > 0)) {
    return
  }
  const boundIds = normalizeResourceIds(getBoundTermBaseIdsFromFiles(), availableTermBases.value)
  if (force || boundIds.length > 0) {
    termBaseIds.value = boundIds
    useTermBase.value = boundIds.length > 0
  }
}

function applyGlossaryDefaultsFromFiles(force = false) {
  if (running.value || (!force && glossaryBaseIds.value.length > 0)) {
    return
  }
  const boundIds = normalizeResourceIds(getBoundGlossaryBaseIdsFromFiles(), availableGlossaryBases.value)
  if (force || boundIds.length > 0) {
    glossaryBaseIds.value = boundIds
    useGlossary.value = boundIds.length > 0
  }
}

function resourceEntryCountLabel(count: number) {
  return t('projectDetail.preTranslate.resources.entryCount', { count })
}

async function loadResources() {
  loadingResources.value = true
  try {
    const [{ data: collections }, { data: bases }, { data: glossaries }, { data: templates }] = await Promise.all([
      http.get<TMCollection[]>('/translation-memory/collections'),
      http.get<TermBase[]>('/term-bases'),
      http.get<GlossaryBase[]>('/glossary-bases'),
      http.get<GuidelineTemplateSummary[]>('/guideline-templates'),
    ])
    tmCollections.value = collections
    termBases.value = bases
    glossaryBases.value = glossaries
    guidelineTemplates.value = templates
    applyResourceDefaultsFromFiles()
    if (
      selectedGuidelineTemplateId.value
      && !templates.some((template) => template.id === selectedGuidelineTemplateId.value)
    ) {
      selectedGuidelineTemplateId.value = ''
    }
  } catch (error) {
    console.error(error)
    pushToast({
      tone: 'error',
      title: t('projectDetail.preTranslate.toast.loadFailedTitle'),
      message: t('projectDetail.preTranslate.toast.loadFailedMessage'),
    })
  } finally {
    loadingResources.value = false
  }
}

function openGuidelineTemplateImport() {
  guidelineTemplateInputRef.value?.click()
}

async function importGuidelineTemplate(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) {
    return
  }

  importingGuidelineTemplate.value = true
  try {
    const formData = new FormData()
    formData.append('file', file)
    const { data } = await http.post<GuidelineTemplateSummary>('/guideline-templates/import', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    await loadResources()
    selectedGuidelineTemplateId.value = data.id
  } catch (error) {
    const message = error instanceof Error ? error.message : t('projectDetail.preTranslate.errors.guidelineImport')
    pushToast({
      tone: 'error',
      title: t('projectDetail.preTranslate.errors.guidelineImport'),
      message,
    })
  } finally {
    importingGuidelineTemplate.value = false
  }
}

function validateBeforeStart() {
  if (!useTm.value && !useGlossary.value && !useLlm.value && !useTermBase.value) {
    errorMessage.value = t('projectDetail.preTranslate.errors.selectOneOption')
    return false
  }
  if (invalidLanguagePairIssue.value) {
    errorMessage.value = invalidLanguagePairIssue.value
    return false
  }
  if (usesLanguagePairResources.value && mixedLanguagePairIssue.value) {
    errorMessage.value = mixedLanguagePairIssue.value
    return false
  }
  if (useTm.value && selectedTmCollectionIds.value.length === 0) {
    errorMessage.value = t('projectDetail.preTranslate.errors.tmCollectionRequired')
    return false
  }
  if (useGlossary.value && selectedGlossaryBaseIds.value.length === 0) {
    errorMessage.value = t('projectDetail.preTranslate.errors.glossaryBaseRequired')
    return false
  }
  if (useTermBase.value && selectedTermBaseIds.value.length === 0) {
    errorMessage.value = t('projectDetail.preTranslate.errors.termBaseRequired')
    return false
  }
  errorMessage.value = ''
  return true
}

function buildOperationHeaders(operationToken: string) {
  return {
    [FILE_OPERATION_TOKEN_HEADER]: operationToken,
  }
}

async function acquirePreTranslateLock(fileId: string) {
  const { data } = await http.post<FileOperationLockResponse>(`/file-records/${fileId}/operation-lock`, {
    operation: 'pre_translate',
  })
  return data.token
}

async function heartbeatPreTranslateLock(fileId: string, operationToken: string) {
  await http.patch(
    `/file-records/${fileId}/operation-lock`,
    {},
    { headers: buildOperationHeaders(operationToken) },
  )
}

async function releasePreTranslateLock(fileId: string, operationToken: string) {
  await http.delete(`/file-records/${fileId}/operation-lock`, {
    headers: buildOperationHeaders(operationToken),
  })
}

function sleep(ms: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

async function releasePreTranslateLockWithRetry(fileId: string, operationToken: string) {
  let lastError: unknown = null
  for (let attempt = 0; attempt <= RELEASE_LOCK_RETRY_DELAYS_MS.length; attempt += 1) {
    try {
      await releasePreTranslateLock(fileId, operationToken)
      return true
    } catch (error) {
      lastError = error
      const delay = RELEASE_LOCK_RETRY_DELAYS_MS[attempt]
      if (typeof delay === 'number') {
        await sleep(delay)
      }
    }
  }

  console.warn('Failed to release pre-translate lock:', lastError)
  return false
}

function startLockHeartbeat(fileId: string, operationToken: string) {
  return window.setInterval(() => {
    void heartbeatPreTranslateLock(fileId, operationToken)
  }, LOCK_HEARTBEAT_INTERVAL_MS)
}

async function runLLMForFile(fileId: string, completedActions: number, actionCount: number, operationToken: string) {
  const token = window.localStorage.getItem('token')
  const controller = new AbortController()
  let plannedCount = 0
  let processedCount = 0
  let sawComplete = false
  let stalled = false
  let idleTimer: number | null = null
  currentAbortController.value = controller

  const updateLLMProgress = (status: string, actionPercent: number) => {
    setFileProgress(
      fileId,
      getActionProgress(completedActions, actionPercent, actionCount),
      status,
    )
  }

  const clearIdleTimer = () => {
    if (idleTimer !== null) {
      window.clearTimeout(idleTimer)
      idleTimer = null
    }
  }

  const refreshIdleTimer = () => {
    clearIdleTimer()
    idleTimer = window.setTimeout(() => {
      stalled = true
      controller.abort()
    }, LLM_STREAM_IDLE_TIMEOUT_MS)
  }

  try {
    refreshIdleTimer()
    const response = await fetch(`/api/file-records/${fileId}/llm-translate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...buildOperationHeaders(operationToken),
      },
      body: JSON.stringify({
        scope: llmScope.value,
        provider: llmProvider.value,
        model: llmModel.value || null,
        guideline_template_id: selectedGuidelineTemplateId.value || null,
        temporary_prompt: llmGuidelines.value,
        glossary_base_ids: useGlossary.value ? selectedGlossaryBaseIds.value : [],
      }),
      signal: controller.signal,
    })
    if (!response.ok) {
      let message = t('projectDetail.preTranslate.errors.llmFailed')
      try {
        const payload = await response.json()
        message = String(payload.detail || message)
      } catch {
        // ignore parse errors
      }
      throw new Error(message)
    }

    await consumeLLMStream(
      response,
      ({ event, data }) => {
        refreshIdleTimer()
        if (stopRequested.value) {
          return
        }

        if (event === 'start') {
          plannedCount = Number(data.total || 0)
          processedCount = 0
          updateLLMProgress(
            plannedCount > 0
              ? t('projectDetail.preTranslate.progress.llmRunning', { processed: 0, total: plannedCount })
              : t('projectDetail.preTranslate.progress.llmStarting'),
            0,
          )
          return
        }

        if (event === 'segment' || event === 'error') {
          processedCount += 1
          const total = Math.max(plannedCount, processedCount)
          const actionPercent = total > 0 ? (processedCount / total) * 100 : 0
          updateLLMProgress(
            t('projectDetail.preTranslate.progress.llmRunning', {
              processed: processedCount,
              total,
            }),
            actionPercent,
          )
          return
        }

        if (event === 'complete') {
          sawComplete = true
          const total = Number(data.total || plannedCount || processedCount)
          const updated = Number(data.updated_count || 0)
          const error = Number(data.error_count || 0)
          plannedCount = total
          processedCount = Math.max(total, updated + error, processedCount)
          updateLLMProgress(
            total > 0
              ? t('projectDetail.preTranslate.progress.llmDone', { updated, error })
              : t('projectDetail.preTranslate.progress.llmSkipped'),
            100,
          )
        }
      },
      (reader) => {
        currentLLMReader.value = reader
      },
    )

    if (!sawComplete) {
      throw new Error(t('projectDetail.preTranslate.errors.llmStreamInterrupted'))
    }
  } catch (error) {
    if (stalled && !stopRequested.value) {
      throw new Error(t('projectDetail.preTranslate.errors.llmStalled'))
    }
    throw error
  } finally {
    clearIdleTimer()
    currentAbortController.value = null
    currentLLMReader.value = null
  }
}

/*
async function startPreTranslate() {
  if (running.value || !validateBeforeStart()) {
    return
  }

  running.value = true
  stopRequested.value = false
  resetProgress()
  runFiles.value = [...props.files]
  const actionCount = getRunActionCount()

  try {
    for (const file of runFiles.value) {
      if (stopRequested.value) {
        break
      }

      let completedActions = 0
      let operationToken = ''
      let heartbeatTimer: number | null = null
      setFileProgress(file.id, 0, t('projectDetail.preTranslate.progress.running'))

      try {
        operationToken = await acquirePreTranslateLock(file.id)
        heartbeatTimer = startLockHeartbeat(file.id, operationToken)

        if (shouldRunTm.value) {
          setFileProgress(
            file.id,
            getActionProgress(completedActions, 0, actionCount),
            t('projectDetail.preTranslate.progress.tmRunning'),
          )
          await http.post(`/file-records/${file.id}/rematch`, {
            collection_ids: selectedTmCollectionIds.value,
            threshold: normalizeTMThreshold(tmThreshold.value),
            skip_confirmed: tmSkipConfirmed.value,
            overwrite_fuzzy: tmOverwriteFuzzy.value,
            auto_confirm_exact: tmAutoConfirmExact.value,
          }, {
            headers: buildOperationHeaders(operationToken),
          })
          completedActions += 1
          setFileProgress(
            file.id,
            getActionProgress(completedActions, 0, actionCount),
            t('projectDetail.preTranslate.progress.tmDone'),
          )
        }

        if (shouldRunTm.value && !stopRequested.value) {
          await http.patch(`/file-records/${file.id}/bindings`, {
            collection_id: selectedTmCollectionIds.value[0] || null,
            collection_ids: selectedTmCollectionIds.value,
            tm_match_threshold: normalizeTMThreshold(tmThreshold.value),
          }, {
            headers: buildOperationHeaders(operationToken),
          })
        }

        if (useGlossary.value && !stopRequested.value) {
          setFileProgress(
            file.id,
            getActionProgress(completedActions, 0, actionCount),
            t('projectDetail.preTranslate.progress.glossaryRunning'),
          )
          await http.patch(`/file-records/${file.id}/bindings`, {
            glossary_base_ids: selectedGlossaryBaseIds.value,
          }, {
            headers: buildOperationHeaders(operationToken),
          })
          completedActions += 1
          setFileProgress(
            file.id,
            getActionProgress(completedActions, 0, actionCount),
            t('projectDetail.preTranslate.progress.glossaryDone'),
          )
        }

        if (useLlm.value && !stopRequested.value) {
          await runLLMForFile(file.id, completedActions, actionCount, operationToken)
          completedActions += 1
          setFileProgress(
            file.id,
            getActionProgress(completedActions, 0, actionCount),
            t('projectDetail.preTranslate.progress.llmComplete'),
          )
        }

        if (useTermBase.value && !stopRequested.value) {
          setFileProgress(
            file.id,
            getActionProgress(completedActions, 0, actionCount),
            t('projectDetail.preTranslate.progress.termBaseRunning'),
          )
          const bindingsPayload: Record<string, string | string[] | number | null> = {
            term_base_id: selectedTermBaseIds.value[0] || null,
            term_base_ids: selectedTermBaseIds.value,
            qa_term_base_ids: selectedTermBaseIds.value,
          }
          if (shouldRunTm.value) {
            bindingsPayload.collection_id = selectedTmCollectionIds.value[0] || null
            bindingsPayload.collection_ids = selectedTmCollectionIds.value
            bindingsPayload.tm_match_threshold = normalizeTMThreshold(tmThreshold.value)
          }
          await http.patch(`/file-records/${file.id}/bindings`, bindingsPayload, {
            headers: buildOperationHeaders(operationToken),
          })
          completedActions += 1
          setFileProgress(
            file.id,
            getActionProgress(completedActions, 0, actionCount),
            t('projectDetail.preTranslate.progress.termBaseDone'),
          )
        }

        if (!shouldRunTm.value && !useLlm.value && useTermBase.value) {
          setFileProgress(file.id, 100, t('projectDetail.preTranslate.progress.termBaseDone'))
        }

        finishedCount.value += 1
        setFileProgress(file.id, 100, t('projectDetail.preTranslate.progress.done'))
      } catch (error) {
        if (stopRequested.value) {
          setFileProgress(
            file.id,
            progressByFileId.value[file.id] || 0,
            t('projectDetail.preTranslate.progress.stopped'),
          )
          break
        }
        const message = error instanceof Error ? error.message : t('projectDetail.preTranslate.errors.unknown')
        setFileProgress(
          file.id,
          progressByFileId.value[file.id] || 0,
          t('projectDetail.preTranslate.progress.failed'),
        )
        pushToast({
          tone: 'error',
          title: t('projectDetail.preTranslate.toast.fileFailedTitle', { name: file.filename }),
          message,
        })
      } finally {
        if (heartbeatTimer !== null) {
          window.clearInterval(heartbeatTimer)
        }
        if (operationToken) {
          const released = await releasePreTranslateLockWithRetry(file.id, operationToken)
          if (!released) {
            pushToast({
              tone: 'warn',
              title: t('projectDetail.preTranslate.toast.releaseLockFailedTitle'),
              message: t('projectDetail.preTranslate.toast.releaseLockFailedMessage'),
              duration: 7000,
            })
          }
        }
      }
    }

    if (stopRequested.value) {
      running.value = false
      emitCurrentProgress(false)
      pushToast({
        tone: 'info',
        title: t('projectDetail.preTranslate.toast.stoppedTitle'),
        message: t('projectDetail.preTranslate.toast.stoppedMessage'),
      })
      return
    }

    pushToast({
      tone: 'success',
      title: t('projectDetail.preTranslate.toast.doneTitle'),
      message: t('projectDetail.preTranslate.toast.doneMessage', {
        done: finishedCount.value,
        total: runFiles.value.length || selectedCount.value,
      }),
    })
    running.value = false
    emitCurrentProgress(false)
    emit('done')
  } finally {
    running.value = false
  }
}

async function stopPreTranslate() {
  if (!running.value) {
    return
  }
  stopRequested.value = true
  currentAbortController.value?.abort()
  try {
    await currentLLMReader.value?.cancel()
  } catch {
    // 忽略浏览器取消流时的竞态错误。
  }
    // 忽略浏览器取消流时的竞态错误。
  }
}
}

*/
async function startPreTranslateTaskRun() {
  if (running.value || !validateBeforeStart()) {
    return
  }

  if (!props.projectId) {
    errorMessage.value = t('projectDetail.preTranslate.errors.unknown')
    return
  }

  running.value = true
  stopRequested.value = false
  resetProgress()
  runFiles.value = [...props.files]
  for (const file of runFiles.value) {
    setFileProgress(file.id, 0, t('projectDetail.preTranslate.progress.pending'))
  }

  try {
    const { data } = await http.post<PretranslationRunStatus>(`/projects/${props.projectId}/pretranslation-runs`, {
      file_ids: runFiles.value.map((file) => file.id),
      use_tm: shouldRunTm.value,
      tm_collection_ids: selectedTmCollectionIds.value,
      tm_threshold: normalizeTMThreshold(tmThreshold.value),
      tm_skip_confirmed: tmSkipConfirmed.value,
      tm_overwrite_fuzzy: tmOverwriteFuzzy.value,
      tm_auto_confirm_exact: tmAutoConfirmExact.value,
      use_glossary: useGlossary.value,
      glossary_base_ids: selectedGlossaryBaseIds.value,
      use_term_base: useTermBase.value,
      term_base_ids: selectedTermBaseIds.value,
      use_llm: useLlm.value,
      llm_scope: llmScope.value,
      llm_provider: llmProvider.value,
      llm_model: llmModel.value || null,
      llm_translation_unit: 'sentence',
      guideline_template_id: selectedGuidelineTemplateId.value || null,
      temporary_prompt: llmGuidelines.value,
    })

    applyPretranslationRun(data)
    if (ACTIVE_PRETRANSLATION_STATUSES.has(data.status)) {
      startPretranslationPolling()
      return
    }
    await handlePretranslationRunFinished(data)
  } catch (error) {
    running.value = false
    stopPretranslationPolling()
    const message = error instanceof Error ? error.message : t('projectDetail.preTranslate.errors.unknown')
    errorMessage.value = message
    pushToast({
      tone: 'error',
      title: t('projectDetail.preTranslate.toast.fileFailedTitle', { name: t('projectDetail.preTranslate.dialogTitle') }),
      message,
    })
  }
}

async function stopPreTranslateTaskRun() {
  if (!running.value) {
    return
  }
  stopRequested.value = true
  const taskIds = Array.from(new Set(Object.values(activeTaskIdsByFileId.value)))
  await Promise.allSettled(taskIds.map((taskId) => (
    http.post(`/pretranslation-tasks/${taskId}/cancel`)
  )))
  await pollPretranslationRun()
}

onBeforeUnmount(() => {
  stopPretranslationPolling()
})

</script>

<template>
  <Modal
    :open="open"
    :title="t('projectDetail.preTranslate.dialogTitle')"
    :description="t('projectDetail.preTranslate.dialogDescription')"
    width="min(1340px, calc(100vw - 32px))"
    @close="requestClose"
  >
    <div class="ptd-layout">
      <aside class="ptd-summary">
        <div class="ptd-summary__stat">
          <strong>{{ selectedCount }}</strong>
          <span>{{ t('projectDetail.preTranslate.summary.files') }}</span>
        </div>
        <div class="ptd-summary__stat">
          <strong>{{ configuredActionCount }}</strong>
          <span>{{ t('projectDetail.preTranslate.summary.actions') }}</span>
        </div>
        <div class="ptd-summary__files">
          <span
            v-for="file in selectedFilePreview"
            :key="file.id"
            class="ptd-summary__file"
            :title="file.filename"
          >
            {{ file.filename }}
          </span>
          <span v-if="selectedCount > selectedFilePreview.length" class="ptd-summary__more">
            +{{ selectedCount - selectedFilePreview.length }}
          </span>
        </div>
      </aside>

      <div class="ptd-flow">
        <div
          v-if="selectedFileLanguagePairStats.length > 0"
          class="ptd-language-notice"
          :class="{ 'is-warning': Boolean(languagePairIssue), 'is-info': !languagePairIssue }"
        >
          <div class="ptd-language-notice__head">
            <strong>{{ selectedFileLanguagePairs.length > 1 ? '已选择混合语言对' : '已选择语言对' }}</strong>
            <span>{{ selectedFileLanguagePairStats.map(formatLanguagePairStat).join('；') }}</span>
          </div>
          <p v-if="invalidLanguagePairIssue">{{ invalidLanguagePairIssue }}</p>
          <p v-else-if="mixedLanguagePairIssue">
            混合语言对可以使用纯 LLM 逐文件预翻译；如启用 TM、词汇表或术语库，请按语言对分批执行。
          </p>
        </div>

        <div class="ptd-section ptd-section--tm" :class="{ 'is-disabled': !useTm }">
          <div class="ptd-section__head">
            <label class="ptd-switch">
              <input v-model="useTm" type="checkbox" :disabled="running" />
              <span class="ptd-switch__control" aria-hidden="true" />
              <span class="ptd-section__icon"><Database :size="17" /></span>
              <span>{{ t('projectDetail.preTranslate.sections.tm') }}</span>
            </label>
            <span class="ptd-section__meta">{{ Math.round(tmThreshold * 100) }}%</span>
          </div>

          <div class="ptd-resource">
            <div class="ptd-resource__topline">
              <span class="tag">{{ selectedLanguagePairLabel }}</span>
              <span>
                {{ t('projectDetail.preTranslate.resources.selectedCount', {
                  selected: selectedTmCollectionIds.length,
                  total: availableTMCollections.length,
                }) }}
              </span>
            </div>
            <div class="ptd-resource__toolbar">
              <label class="ptd-resource-search">
                <Search :size="15" />
                <input
                  v-model="tmSearchQuery"
                  type="search"
                  :placeholder="t('projectDetail.preTranslate.tm.searchPlaceholder')"
                  :disabled="running || loadingResources || !useTm"
                />
              </label>
              <div class="ptd-resource__buttons">
                <button
                  class="button button--ghost ptd-resource__button ptd-resource__button--import"
                  type="button"
                  :disabled="running || loadingResources || Boolean(languagePairIssue)"
                  @click="openResourceImport('tm')"
                >
                  <Upload :size="14" />
                  {{ t('common.actions.import') }}
                </button>
                <button
                  class="button button--ghost ptd-resource__button ptd-resource__button--select"
                  type="button"
                  :disabled="running || loadingResources || !useTm || availableTMCollections.length === 0"
                  @click="selectAllTmCollections"
                >
                  {{ t('projectDetail.preTranslate.resources.selectAll') }}
                </button>
                <button
                  class="button button--ghost ptd-resource__button ptd-resource__button--clear"
                  type="button"
                  :disabled="running || !useTm || selectedTmCollectionIds.length === 0"
                  @click="clearTmCollections"
                >
                  <X :size="14" />
                  {{ t('projectDetail.preTranslate.resources.clear') }}
                </button>
              </div>
            </div>

            <p v-if="hiddenTMCollectionCount > 0 && !languagePairIssue" class="ptd-resource__note">
              {{ t('projectDetail.preTranslate.tm.hiddenByLanguagePair', { count: hiddenTMCollectionCount }) }}
            </p>

            <div v-if="languagePairIssue" class="ptd-resource-empty is-warning">
              {{ languagePairIssue }}
            </div>
            <div v-else-if="loadingResources" class="ptd-resource-empty">
              {{ t('projectDetail.preTranslate.resources.loading') }}
            </div>
            <div v-else-if="availableTMCollections.length === 0" class="ptd-resource-empty">
              {{ t('projectDetail.preTranslate.tm.emptyForLanguagePair') }}
            </div>
            <div v-else class="ptd-resource-list">
              <label
                v-for="collection in filteredTMCollections"
                :key="collection.id"
                class="ptd-resource-item"
                :class="{ 'is-selected': selectedTmCollectionIds.includes(collection.id), 'is-disabled': running || !useTm }"
              >
                <input
                  type="checkbox"
                  :checked="selectedTmCollectionIds.includes(collection.id)"
                  :disabled="running || !useTm"
                  @change="toggleTmCollection(collection.id, $event)"
                />
                <span class="ptd-resource-item__check" aria-hidden="true">
                  <Check :size="14" />
                </span>
                <span class="ptd-resource-item__body">
                  <strong>{{ collection.name }}</strong>
                  <span>{{ formatLanguagePair(collection.source_language, collection.target_language) }}</span>
                </span>
                <span class="ptd-resource-item__count">{{ resourceEntryCountLabel(collection.entry_count) }}</span>
              </label>
              <div v-if="filteredTMCollections.length === 0" class="ptd-resource-empty">
                {{ t('projectDetail.preTranslate.resources.noSearchResult') }}
              </div>
            </div>

            <div v-if="selectedTMCollections.length > 0" class="ptd-selected-resources">
              <span v-for="collection in selectedTMCollections" :key="collection.id" class="ptd-selected-resource">
                {{ collection.name }}
              </span>
            </div>
          </div>

          <div class="ptd-grid ptd-grid--threshold">
            <label class="field">
              <span class="field__label">{{ t('projectDetail.preTranslate.tm.threshold') }}</span>
              <input
                v-model.number="tmThreshold"
                class="field__control"
                type="number"
                min="0.5"
                max="1"
                step="0.01"
                :disabled="running || !useTm"
              />
            </label>
          </div>

          <div class="ptd-checks">
            <label><input v-model="tmSkipConfirmed" type="checkbox" :disabled="running || !useTm" />{{ t('projectDetail.preTranslate.tm.skipConfirmed') }}</label>
            <label><input v-model="tmOverwriteFuzzy" type="checkbox" :disabled="running || !useTm" />{{ t('projectDetail.preTranslate.tm.overwriteFuzzy') }}</label>
            <label><input v-model="tmAutoConfirmExact" type="checkbox" :disabled="running || !useTm" />{{ t('projectDetail.preTranslate.tm.autoConfirmExact') }}</label>
          </div>
        </div>

      <div class="ptd-section ptd-section--glossary" :class="{ 'is-disabled': !useGlossary }">
        <div class="ptd-section__head">
          <label class="ptd-switch">
            <input v-model="useGlossary" type="checkbox" :disabled="running" />
            <span class="ptd-switch__control" aria-hidden="true" />
            <span class="ptd-section__icon"><BookOpen :size="17" /></span>
            <span>{{ t('projectDetail.preTranslate.sections.glossary') }}</span>
          </label>
        </div>

        <div class="ptd-resource">
          <div class="ptd-resource__topline">
            <span class="tag">{{ selectedLanguagePairLabel }}</span>
            <span>
              {{ t('projectDetail.preTranslate.resources.selectedCount', {
                selected: selectedGlossaryBaseIds.length,
                total: availableGlossaryBases.length,
              }) }}
            </span>
          </div>
          <div class="ptd-resource__toolbar">
            <label class="ptd-resource-search">
              <Search :size="15" />
              <input
                v-model="glossarySearchQuery"
                type="search"
                :placeholder="t('projectDetail.preTranslate.glossary.searchPlaceholder')"
                :disabled="running || loadingResources || !useGlossary"
              />
            </label>
            <div class="ptd-resource__buttons">
              <button
                class="button button--ghost ptd-resource__button ptd-resource__button--import"
                type="button"
                :disabled="running || loadingResources || Boolean(languagePairIssue)"
                @click="openResourceImport('glossary')"
              >
                <Upload :size="14" />
                {{ t('common.actions.import') }}
              </button>
              <button
                class="button button--ghost ptd-resource__button ptd-resource__button--select"
                type="button"
                :disabled="running || loadingResources || !useGlossary || availableGlossaryBases.length === 0"
                @click="selectAllGlossaryBases"
              >
                {{ t('projectDetail.preTranslate.resources.selectAll') }}
              </button>
              <button
                class="button button--ghost ptd-resource__button ptd-resource__button--clear"
                type="button"
                :disabled="running || !useGlossary || selectedGlossaryBaseIds.length === 0"
                @click="clearGlossaryBases"
              >
                <X :size="14" />
                {{ t('projectDetail.preTranslate.resources.clear') }}
              </button>
            </div>
          </div>

          <p v-if="hiddenGlossaryBaseCount > 0 && !languagePairIssue" class="ptd-resource__note">
            {{ t('projectDetail.preTranslate.glossary.hiddenByLanguagePair', { count: hiddenGlossaryBaseCount }) }}
          </p>

          <div v-if="languagePairIssue" class="ptd-resource-empty is-warning">
            {{ languagePairIssue }}
          </div>
          <div v-else-if="loadingResources" class="ptd-resource-empty">
            {{ t('projectDetail.preTranslate.resources.loading') }}
          </div>
          <div v-else-if="availableGlossaryBases.length === 0" class="ptd-resource-empty">
            {{ t('projectDetail.preTranslate.glossary.emptyForLanguagePair') }}
          </div>
          <div v-else class="ptd-resource-list">
            <label
              v-for="glossaryBase in filteredGlossaryBases"
              :key="glossaryBase.id"
              class="ptd-resource-item"
              :class="{ 'is-selected': selectedGlossaryBaseIds.includes(glossaryBase.id), 'is-disabled': running || !useGlossary }"
            >
              <input
                type="checkbox"
                :checked="selectedGlossaryBaseIds.includes(glossaryBase.id)"
                :disabled="running || !useGlossary"
                @change="toggleGlossaryBase(glossaryBase.id, $event)"
              />
              <span class="ptd-resource-item__check" aria-hidden="true">
                <Check :size="14" />
              </span>
              <span class="ptd-resource-item__body">
                <strong>{{ glossaryBase.name }}</strong>
                <span>{{ formatLanguagePair(glossaryBase.source_language, glossaryBase.target_language) }}</span>
              </span>
              <span class="ptd-resource-item__count">{{ resourceEntryCountLabel(glossaryBase.entry_count) }}</span>
            </label>
            <div v-if="filteredGlossaryBases.length === 0" class="ptd-resource-empty">
              {{ t('projectDetail.preTranslate.resources.noSearchResult') }}
            </div>
          </div>

          <div v-if="selectedGlossaryBases.length > 0" class="ptd-selected-resources">
            <span v-for="glossaryBase in selectedGlossaryBases" :key="glossaryBase.id" class="ptd-selected-resource">
              {{ glossaryBase.name }}
            </span>
          </div>
        </div>
        <p class="hint-text">{{ t('projectDetail.preTranslate.glossary.hint') }}</p>
      </div>

      <div class="ptd-section ptd-section--term" :class="{ 'is-disabled': !useTermBase }">
        <div class="ptd-section__head">
          <label class="ptd-switch">
            <input v-model="useTermBase" type="checkbox" :disabled="running" />
            <span class="ptd-switch__control" aria-hidden="true" />
            <span class="ptd-section__icon"><BookOpenCheck :size="17" /></span>
            <span>{{ t('projectDetail.preTranslate.sections.termBase') }}</span>
          </label>
        </div>

        <div class="ptd-resource">
          <div class="ptd-resource__topline">
            <span class="tag">{{ selectedLanguagePairLabel }}</span>
            <span>
              {{ t('projectDetail.preTranslate.resources.selectedCount', {
                selected: selectedTermBaseIds.length,
                total: availableTermBases.length,
              }) }}
            </span>
          </div>
          <div class="ptd-resource__toolbar">
            <label class="ptd-resource-search">
              <Search :size="15" />
              <input
                v-model="termBaseSearchQuery"
                type="search"
                :placeholder="t('projectDetail.preTranslate.termBase.searchPlaceholder')"
                :disabled="running || loadingResources || !useTermBase"
              />
            </label>
            <div class="ptd-resource__buttons">
              <button
                class="button button--ghost ptd-resource__button ptd-resource__button--import"
                type="button"
                :disabled="running || loadingResources || Boolean(languagePairIssue)"
                @click="openResourceImport('term')"
              >
                <Upload :size="14" />
                {{ t('common.actions.import') }}
              </button>
              <button
                class="button button--ghost ptd-resource__button ptd-resource__button--select"
                type="button"
                :disabled="running || loadingResources || !useTermBase || availableTermBases.length === 0"
                @click="selectAllTermBases"
              >
                {{ t('projectDetail.preTranslate.resources.selectAll') }}
              </button>
              <button
                class="button button--ghost ptd-resource__button ptd-resource__button--clear"
                type="button"
                :disabled="running || !useTermBase || selectedTermBaseIds.length === 0"
                @click="clearTermBases"
              >
                <X :size="14" />
                {{ t('projectDetail.preTranslate.resources.clear') }}
              </button>
            </div>
          </div>

          <p v-if="hiddenTermBaseCount > 0 && !languagePairIssue" class="ptd-resource__note">
            {{ t('projectDetail.preTranslate.termBase.hiddenByLanguagePair', { count: hiddenTermBaseCount }) }}
          </p>

          <div v-if="languagePairIssue" class="ptd-resource-empty is-warning">
            {{ languagePairIssue }}
          </div>
          <div v-else-if="loadingResources" class="ptd-resource-empty">
            {{ t('projectDetail.preTranslate.resources.loading') }}
          </div>
          <div v-else-if="availableTermBases.length === 0" class="ptd-resource-empty">
            {{ t('projectDetail.preTranslate.termBase.emptyForLanguagePair') }}
          </div>
          <div v-else class="ptd-resource-list">
            <label
              v-for="termBase in filteredTermBases"
              :key="termBase.id"
              class="ptd-resource-item"
              :class="{ 'is-selected': selectedTermBaseIds.includes(termBase.id), 'is-disabled': running || !useTermBase }"
            >
              <input
                type="checkbox"
                :checked="selectedTermBaseIds.includes(termBase.id)"
                :disabled="running || !useTermBase"
                @change="toggleTermBase(termBase.id, $event)"
              />
              <span class="ptd-resource-item__check" aria-hidden="true">
                <Check :size="14" />
              </span>
              <span class="ptd-resource-item__body">
                <strong>{{ termBase.name }}</strong>
                <span>{{ formatLanguagePair(termBase.source_language, termBase.target_language) }}</span>
              </span>
              <span class="ptd-resource-item__count">{{ resourceEntryCountLabel(termBase.entry_count) }}</span>
            </label>
            <div v-if="filteredTermBases.length === 0" class="ptd-resource-empty">
              {{ t('projectDetail.preTranslate.resources.noSearchResult') }}
            </div>
          </div>

          <div v-if="selectedTermBases.length > 0" class="ptd-selected-resources">
            <span v-for="termBase in selectedTermBases" :key="termBase.id" class="ptd-selected-resource">
              {{ termBase.name }}
            </span>
          </div>
        </div>
        <p class="hint-text">{{ t('projectDetail.preTranslate.termBase.hint') }}</p>
      </div>

      <div class="ptd-section ptd-section--llm" :class="{ 'is-disabled': !useLlm }">
        <div class="ptd-section__head">
          <label class="ptd-switch">
            <input v-model="useLlm" type="checkbox" :disabled="running" />
            <span class="ptd-switch__control" aria-hidden="true" />
            <span class="ptd-section__icon"><Bot :size="17" /></span>
            <span>{{ t('projectDetail.preTranslate.sections.llm') }}</span>
          </label>
        </div>
        <div class="ptd-llm-tip" role="note">
          <AlertTriangle :size="18" />
          <div class="ptd-llm-tip__content">
            <strong>{{ t('projectDetail.preTranslate.llm.modelTipTitle') }}</strong>
            <p>{{ t('projectDetail.preTranslate.llm.modelTipBody') }}</p>
          </div>
        </div>
        <div class="ptd-grid ptd-grid--llm">
          <label class="field">
            <span class="field__label">{{ t('projectDetail.preTranslate.llm.provider') }}</span>
            <select v-model="llmProvider" class="field__control" :disabled="running || !useLlm">
              <option v-for="option in llmProviderOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
          <label class="field">
            <span class="field__label">{{ t('projectDetail.preTranslate.llm.model') }}</span>
            <select v-model="llmModel" class="field__control" :disabled="running || !useLlm">
              <option v-for="option in llmModelOptions" :key="option.id" :value="option.id">
                {{ option.name }}
              </option>
            </select>
          </label>
          <label class="field">
            <span class="field__label">{{ t('projectDetail.preTranslate.llm.scope') }}</span>
            <select v-model="llmScope" class="field__control" :disabled="running || !useLlm">
              <option v-for="option in llmScopeOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
            <span class="field__note">{{ t('projectDetail.preTranslate.llm.scopeHint') }}</span>
          </label>
        </div>
        <label class="field field--full">
          <span class="field__label">{{ t('projectDetail.preTranslate.llm.guidelineTemplate') }}</span>
          <div class="ptd-guideline-tools">
            <select
              v-model="selectedGuidelineTemplateId"
              class="field__control"
              :disabled="running || loadingResources || !useLlm"
            >
              <option value="">{{ t('projectDetail.preTranslate.llm.guidelineTemplateNone') }}</option>
              <option v-for="template in guidelineTemplates" :key="template.id" :value="template.id">
                {{ template.name }}
              </option>
            </select>
            <button
              class="button"
              type="button"
              :disabled="running || importingGuidelineTemplate || !useLlm"
              @click="openGuidelineTemplateImport"
            >
              <Upload :size="14" />
              {{ importingGuidelineTemplate ? t('common.actions.saving') : t('common.actions.import') }}
            </button>
          </div>
          <input
            ref="guidelineTemplateInputRef"
            class="ptd-guideline-file"
            type="file"
            accept=".md,.markdown,.txt"
            @change="importGuidelineTemplate"
          />
        </label>
        <label class="field field--full">
          <span class="field__label">{{ t('projectDetail.preTranslate.llm.temporaryPrompt') }}</span>
          <textarea
            v-model="llmGuidelines"
            class="field__control ptd-guidelines"
            rows="4"
            :placeholder="t('projectDetail.preTranslate.llm.guidelinesPlaceholder')"
            :disabled="running || !useLlm"
          />
        </label>
        <p v-if="pureLlmMixedLanguagePairNotice" class="hint-text is-warning">{{ pureLlmMixedLanguagePairNotice }}</p>
        <p class="hint-text">{{ t('projectDetail.preTranslate.llm.hint') }}</p>
      </div>
      </div>

      <div v-if="running || Object.keys(progressByFileId).length > 0" class="ptd-progress">
        <div class="ptd-progress__head">
          <span>{{ t('projectDetail.preTranslate.progress.overall') }}</span>
          <span v-if="running">{{ t('projectDetail.preTranslate.progress.closeHint') }}</span>
        </div>
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div
              class="progress-bar__fill"
              :class="{ 'is-complete': isProgressComplete(overallProgress) }"
              :style="{ width: `${overallProgress}%` }"
            />
          </div>
          <span class="progress-bar__text">{{ overallProgress }}%</span>
        </div>
        <div class="ptd-progress-list">
          <div v-for="file in progressFiles" :key="file.id" class="ptd-progress-item">
            <div class="ptd-progress-item__top">
              <span class="ptd-progress-name">{{ file.filename }}</span>
              <span class="ptd-progress-state">{{ statusByFileId[file.id] || t('projectDetail.preTranslate.progress.pending') }}</span>
            </div>
            <div class="ptd-progress-item__bar">
              <div class="progress-bar">
                <div class="progress-bar__track">
                  <div
                    class="progress-bar__fill"
                    :class="{ 'is-complete': isProgressComplete(progressByFileId[file.id] || 0) }"
                    :style="{ width: `${progressByFileId[file.id] || 0}%` }"
                  />
                </div>
                <span class="progress-bar__text">{{ progressByFileId[file.id] || 0 }}%</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <p v-if="errorMessage" class="form-message is-error">{{ errorMessage }}</p>
    </div>

    <template #footer>
      <div class="ptd-footer">
        <span class="ptd-selected">{{ t('projectDetail.preTranslate.selectedSummary', { count: selectedCount }) }}</span>
        <div class="ptd-actions">
          <button class="button" type="button" @click="requestClose">
            {{ running ? t('common.actions.close') : t('common.actions.cancel') }}
          </button>
          <button
            v-if="running"
            class="button button--danger"
            type="button"
            :disabled="stopRequested"
            @click="stopPreTranslateTaskRun"
          >
            <Pause :size="14" />
            {{ t('common.actions.pause') }}
          </button>
          <button
            class="button button--primary"
            type="button"
            :disabled="running || selectedCount === 0"
            @click="startPreTranslateTaskRun"
          >
            <Loader2 v-if="running" class="lucide-spin" :size="14" />
            <Sparkles v-else :size="14" />
            {{ t('projectDetail.preTranslate.start') }}
          </button>
        </div>
      </div>
    </template>
  </Modal>

  <ResourceImportDialog
    :open="Boolean(activeResourceImportTab)"
    :mode="resourceImportDialogTab"
    :initial-tab="resourceImportDialogTab"
    :title="resourceImportDialogTitle"
    :context-label="resourceImportContextLabel"
    :source-language="selectedFileLanguagePair?.source || props.sourceLanguage"
    :target-language="selectedFileLanguagePair?.target || props.targetLanguage"
    @close="closeResourceImport"
    @imported="handleResourceImported"
  />
</template>

<style scoped>
.ptd-layout {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.ptd-summary {
  position: sticky;
  top: 0;
  display: grid;
  gap: 8px;
}

.ptd-summary__stat {
  display: grid;
  gap: 2px;
  min-height: 58px;
  align-content: center;
  padding: 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
}

.ptd-summary__stat strong {
  color: var(--brand-700);
  font-size: 22px;
  line-height: 1;
}

.ptd-summary__stat span,
.ptd-summary__more {
  color: var(--text-muted);
  font-size: 12px;
}

.ptd-summary__files {
  display: grid;
  gap: 6px;
  max-height: 124px;
  overflow: auto;
  padding: 9px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
}

.ptd-summary__file {
  overflow-wrap: anywhere;
  word-break: break-word;
  white-space: normal;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.45;
}

.ptd-flow {
  display: grid;
  grid-template-columns: repeat(2, minmax(320px, 1fr));
  gap: 12px;
  align-items: start;
  min-width: 0;
}

.ptd-language-notice {
  grid-column: 1 / -1;
  display: grid;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.ptd-language-notice.is-warning {
  border-color: rgba(194, 120, 3, 0.3);
  background: var(--state-warning-bg);
}

.ptd-language-notice.is-info {
  border-color: color-mix(in srgb, var(--brand-700) 16%, var(--line-soft));
  background: color-mix(in srgb, var(--brand-050) 48%, var(--surface-panel));
}

.ptd-language-notice__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
  min-width: 0;
}

.ptd-language-notice__head strong {
  color: var(--text-primary);
}

.ptd-language-notice p {
  margin: 0;
}

.ptd-section {
  --ptd-accent: var(--brand-700);
  --ptd-accent-strong: var(--brand-700);
  --ptd-accent-soft: color-mix(in srgb, var(--ptd-accent) 8%, transparent);

  display: grid;
  gap: 12px;
  align-content: start;
  min-width: 0;
  padding: 14px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 6%, var(--line-soft));
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-panel) 99%, var(--ptd-accent) 1%);
  transition: border-color 0.18s ease, box-shadow 0.18s ease, background 0.18s ease, opacity 0.2s ease, filter 0.2s ease;
}

.ptd-section--tm {
  order: 1;
}

.ptd-section--term {
  order: 4;
}

.ptd-section--glossary {
  order: 2;
}

.ptd-section--llm {
  order: 3;
}

.ptd-section.is-disabled {
  background: color-mix(in srgb, var(--surface-1) 97%, var(--ptd-accent) 3%);
  opacity: 0.72;
  filter: grayscale(18%);
}

.ptd-section:not(.is-disabled) {
  border-color: color-mix(in srgb, var(--ptd-accent) 16%, var(--line-soft));
  box-shadow: 0 8px 18px color-mix(in srgb, var(--ptd-accent) 5%, transparent);
  filter: none;
}

.ptd-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: -4px -4px 0;
  padding: 9px 10px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 6%, var(--line-soft));
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-panel) 98%, var(--ptd-accent) 2%);
}

.ptd-section:not(.is-disabled) .ptd-section__head {
  border-color: color-mix(in srgb, var(--ptd-accent) 14%, var(--line-soft));
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--surface-panel) 94%, var(--ptd-accent) 6%),
    color-mix(in srgb, var(--surface-panel) 98%, var(--ptd-accent) 2%)
  );
}

.ptd-section__icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 9%, var(--line-soft));
  border-radius: 6px;
  background: color-mix(in srgb, var(--surface-panel) 95%, var(--ptd-accent) 5%);
  color: var(--ptd-accent-strong);
}

.ptd-section__meta {
  color: var(--text-muted);
  font-size: 13px;
}

.ptd-section--llm.is-disabled {
  opacity: 1;
  filter: none;
}

.ptd-section--llm.is-disabled .ptd-grid--llm,
.ptd-section--llm.is-disabled .field--full,
.ptd-section--llm.is-disabled > .hint-text {
  opacity: 0.72;
  filter: grayscale(18%);
}

.ptd-llm-tip {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 11px 12px;
  border: 1px solid rgba(194, 120, 3, 0.34);
  border-left: 4px solid var(--state-warning);
  border-radius: 8px;
  background:
    linear-gradient(
      135deg,
      color-mix(in srgb, var(--state-warning-bg) 78%, var(--surface-panel) 22%),
      color-mix(in srgb, var(--surface-panel) 96%, var(--state-warning) 4%)
    ),
    var(--state-warning-bg);
  box-shadow: 0 8px 18px rgba(194, 120, 3, 0.12);
}

.ptd-llm-tip svg {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--state-warning);
}

.ptd-llm-tip__content {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.ptd-llm-tip strong {
  color: color-mix(in srgb, var(--state-warning) 72%, var(--text-primary));
  font-size: 14px;
}

.ptd-llm-tip p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.ptd-switch {
  --ptd-switch-off: color-mix(in srgb, var(--state-danger) 64%, var(--text-muted) 36%);
  --ptd-switch-off-strong: color-mix(in srgb, var(--state-danger) 76%, var(--text-secondary) 24%);
  --ptd-switch-on: var(--brand-500);
  --ptd-switch-on-strong: var(--brand-700);

  display: inline-flex;
  gap: 10px;
  align-items: center;
  position: relative;
  min-width: 0;
  font-weight: 600;
}

.ptd-switch input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.ptd-switch__control {
  position: relative;
  width: 46px;
  height: 26px;
  border: 1px solid var(--ptd-switch-off-strong);
  border-radius: 999px;
  background: linear-gradient(
    135deg,
    var(--ptd-switch-off-strong),
    color-mix(in srgb, var(--ptd-switch-off) 62%, var(--surface-panel) 38%)
  );
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 2px rgba(17, 49, 42, 0.12),
    0 0 0 2px color-mix(in srgb, var(--ptd-switch-off) 7%, transparent);
  transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}

.ptd-switch__control::before {
  content: '\2715';
  position: absolute;
  top: 50%;
  right: 5px;
  transform: translateY(-50%);
  font-size: 9px;
  font-weight: 600;
  line-height: 1;
  color: rgba(255, 255, 255, 0.82);
  opacity: 0.9;
  transition: opacity 0.18s ease;
}

.ptd-switch__control::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: #ffffff;
  box-shadow: 0 2px 5px rgba(15, 23, 42, 0.24);
  transition: transform 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
}

.ptd-switch input:checked + .ptd-switch__control {
  border-color: var(--ptd-switch-on-strong);
  background: linear-gradient(
    135deg,
    var(--ptd-switch-on-strong),
    color-mix(in srgb, var(--ptd-switch-on) 76%, #ffffff 24%)
  );
  box-shadow:
    0 0 0 3px color-mix(in srgb, var(--ptd-switch-on) 12%, transparent),
    0 6px 14px color-mix(in srgb, var(--ptd-switch-on) 12%, transparent);
}

.ptd-switch input:checked + .ptd-switch__control::before {
  content: '\2713';
  right: auto;
  left: 6px;
  color: rgba(255, 255, 255, 0.94);
  opacity: 1;
}

.ptd-switch input:checked + .ptd-switch__control::after {
  background: #ffffff;
  transform: translateX(20px);
}

.ptd-switch input:focus-visible + .ptd-switch__control {
  outline: 2px solid color-mix(in srgb, var(--brand-700) 24%, transparent);
  outline-offset: 2px;
}

.ptd-grid {
  display: grid;
  gap: 12px;
  align-items: end;
}

.ptd-grid--threshold {
  grid-template-columns: minmax(150px, 180px);
}

.ptd-grid--llm {
  grid-template-columns: 1fr;
}

.ptd-resource {
  display: grid;
  gap: 10px;
  padding: 10px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 5%, var(--line-soft));
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-1) 99%, var(--ptd-accent) 1%);
}

.ptd-resource__topline,
.ptd-resource__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-width: 0;
}

.ptd-resource__topline {
  flex-wrap: wrap;
  color: var(--text-muted);
  font-size: 12px;
}

.ptd-resource__topline .tag {
  border-color: color-mix(in srgb, var(--ptd-accent) 6%, var(--line-soft));
  background: color-mix(in srgb, var(--surface-panel) 96%, var(--ptd-accent) 4%);
  color: color-mix(in srgb, var(--ptd-accent-strong) 50%, var(--text-secondary));
}

.ptd-resource__toolbar {
  align-items: center;
  flex-wrap: wrap;
}

.ptd-resource-search {
  min-width: 0;
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 40px;
  padding: 0 11px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 5%, var(--line-soft));
  border-radius: 8px;
  background: var(--surface-1);
  color: var(--text-muted);
  font-size: 13px;
}

.ptd-resource-search:focus-within {
  border-color: color-mix(in srgb, var(--ptd-accent) 34%, var(--line-strong));
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ptd-accent) 7%, transparent);
}

.ptd-resource-search input {
  min-width: 0;
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
}

.ptd-resource__buttons {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.ptd-resource__button {
  min-width: 54px;
  min-height: 32px;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.2;
  box-shadow: none;
}

.ptd-resource__button .lucide {
  width: 13px;
  height: 13px;
}

.ptd-resource__button--import {
  border-color: color-mix(in srgb, var(--ptd-accent) 18%, var(--line-soft));
  background: color-mix(in srgb, var(--surface-panel) 94%, var(--ptd-accent) 6%);
  color: var(--ptd-accent-strong);
  font-weight: 600;
}

.ptd-resource__button--import:not(:disabled):hover {
  border-color: color-mix(in srgb, var(--ptd-accent) 28%, var(--line-strong));
  background: color-mix(in srgb, var(--surface-panel) 90%, var(--ptd-accent) 10%);
}

.ptd-resource__button--select {
  border-color: color-mix(in srgb, var(--ptd-accent) 8%, var(--line-soft));
  background: color-mix(in srgb, var(--surface-panel) 98%, var(--ptd-accent) 2%);
  color: color-mix(in srgb, var(--ptd-accent-strong) 42%, var(--text-secondary));
}

.ptd-resource__button--select:not(:disabled):hover {
  border-color: color-mix(in srgb, var(--ptd-accent) 18%, var(--line-strong));
  background: color-mix(in srgb, var(--surface-panel) 94%, var(--ptd-accent) 6%);
  color: color-mix(in srgb, var(--ptd-accent-strong) 58%, var(--text-primary));
}

.ptd-resource__button--clear {
  border-color: color-mix(in srgb, var(--state-danger) 8%, var(--line-soft));
  background: color-mix(in srgb, var(--surface-panel) 92%, var(--state-danger-bg) 8%);
  color: color-mix(in srgb, var(--state-danger) 42%, var(--text-secondary));
}

.ptd-resource__button--clear:not(:disabled):hover {
  border-color: color-mix(in srgb, var(--state-danger) 18%, var(--line-strong));
  background: color-mix(in srgb, var(--surface-panel) 86%, var(--state-danger-bg) 14%);
  color: color-mix(in srgb, var(--state-danger) 68%, var(--text-primary));
}

.ptd-resource__note {
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
}

.ptd-resource-list {
  display: grid;
  gap: 9px;
  max-height: 246px;
  overflow: auto;
  padding: 8px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 5%, var(--line-soft));
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-panel) 99%, var(--ptd-accent) 1%);
}

.ptd-resource-item {
  position: relative;
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) max-content;
  gap: 11px;
  align-items: center;
  min-height: 62px;
  padding: 11px 12px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 4%, var(--line-soft));
  border-radius: 8px;
  background: var(--surface-1);
  cursor: pointer;
  transition: border-color 0.16s ease, background 0.16s ease, box-shadow 0.16s ease;
}

.ptd-resource-item:hover {
  border-color: color-mix(in srgb, var(--ptd-accent) 12%, var(--line-soft));
  background: color-mix(in srgb, var(--surface-panel) 97%, var(--ptd-accent) 3%);
}

.ptd-resource-item.is-selected {
  border-color: color-mix(in srgb, var(--ptd-accent) 22%, var(--line-strong));
  background: color-mix(in srgb, var(--surface-panel) 96%, var(--ptd-accent) 4%);
  box-shadow: inset 3px 0 0 var(--ptd-accent-strong);
}

.ptd-resource-item.is-disabled {
  cursor: not-allowed;
  opacity: 0.62;
}

.ptd-resource-item input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.ptd-resource-item__check {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 7%, var(--line-strong));
  border-radius: 6px;
  color: transparent;
  background: var(--surface-panel);
}

.ptd-resource-item.is-selected .ptd-resource-item__check {
  border-color: var(--ptd-accent-strong);
  background: var(--ptd-accent-strong);
  color: #fff;
}

.ptd-resource-item__body {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.ptd-resource-item__body strong,
.ptd-resource-item__body span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ptd-resource-item__body strong {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 650;
  line-height: 1.25;
}

.ptd-resource-item__body span {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.35;
}

.ptd-resource-item__count {
  align-self: center;
  padding: 4px 7px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 5%, var(--line-soft));
  border-radius: 999px;
  background: color-mix(in srgb, var(--surface-muted) 97%, var(--ptd-accent) 3%);
  color: color-mix(in srgb, var(--ptd-accent-strong) 32%, var(--text-secondary));
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
}

.ptd-resource-empty {
  display: grid;
  place-items: center;
  min-height: 58px;
  padding: 12px;
  border: 1px dashed var(--line-soft);
  border-radius: 8px;
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
  background: var(--surface-1);
}

.ptd-resource-empty.is-warning {
  border-color: rgba(194, 120, 3, 0.28);
  color: var(--state-warning);
  background: var(--state-warning-bg);
}

.ptd-selected-resources {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ptd-selected-resource {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 4px 8px;
  border-radius: 6px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 7%, var(--line-soft));
  background: color-mix(in srgb, var(--surface-panel) 96%, var(--ptd-accent) 4%);
  color: color-mix(in srgb, var(--ptd-accent-strong) 54%, var(--text-secondary));
  font-size: 12px;
}

.ptd-guideline-tools {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
  align-items: center;
}

.ptd-guideline-file {
  display: none;
}

.ptd-checks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  padding: 10px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 5%, var(--line-soft));
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-1) 99%, var(--ptd-accent) 1%);
  color: var(--text-secondary);
  font-size: 13px;
}

.ptd-checks label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.ptd-checks input,
.ptd-section input[type="checkbox"] {
  accent-color: var(--ptd-accent-strong);
}

.ptd-section .field {
  min-width: 0;
  padding: 10px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 5%, var(--line-soft));
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-1) 99%, var(--ptd-accent) 1%);
}

.ptd-section .field__label {
  width: max-content;
  max-width: 100%;
  padding: 2px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--surface-panel) 97%, var(--ptd-accent) 3%);
  color: color-mix(in srgb, var(--ptd-accent-strong) 36%, var(--text-secondary));
  font-weight: 600;
}

.ptd-section .field__note {
  color: var(--text-secondary);
  font-size: 0.8rem;
  line-height: 1.45;
}

.ptd-section .hint-text {
  padding: 9px 10px;
  border: 1px solid color-mix(in srgb, var(--ptd-accent) 5%, var(--line-soft));
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-1) 99%, var(--ptd-accent) 1%);
}

.ptd-section .hint-text.is-warning {
  border-color: rgba(194, 120, 3, 0.28);
  background: var(--state-warning-bg);
  color: var(--state-warning);
}

.ptd-progress {
  grid-column: 1 / -1;
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
}

.ptd-progress__head,
.ptd-progress-item__top {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  min-width: 0;
}

.ptd-progress__head {
  color: var(--text-secondary);
  font-size: 12px;
}

.ptd-progress__head span:last-child {
  color: var(--state-info);
  text-align: right;
}

.ptd-progress-list {
  display: grid;
  gap: 10px;
  max-height: 180px;
  overflow: auto;
}

.ptd-progress-item {
  display: grid;
  gap: 6px;
  font-size: 13px;
}

.ptd-progress-name {
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ptd-progress-state {
  color: var(--text-muted);
  flex-shrink: 0;
}

.ptd-progress-item__bar .progress-bar__track {
  height: 7px;
}

.ptd-progress-item__bar .progress-bar__text {
  min-width: 36px;
  font-size: 12px;
}

.ptd-footer {
  width: 100%;
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
}

.ptd-selected {
  color: var(--text-muted);
  font-size: 13px;
}

.ptd-actions {
  display: flex;
  gap: 8px;
}

.ptd-guidelines {
  resize: vertical;
  min-height: 118px;
  max-height: 180px;
  font-size: 13px;
  line-height: 1.5;
}

@media (max-width: 1180px) {
  .ptd-layout {
    grid-template-columns: 1fr;
  }

  .ptd-summary {
    position: static;
    grid-template-columns: 90px 90px minmax(0, 1fr);
  }

  .ptd-summary__files {
    max-height: 58px;
  }

  .ptd-flow {
    grid-template-columns: repeat(2, minmax(300px, 1fr));
  }
}

@media (max-width: 1080px) {
  .ptd-flow {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .ptd-section--llm {
    grid-column: 1 / -1;
  }

  .ptd-section--llm .ptd-grid--llm,
  .ptd-section--llm .ptd-guideline-tools {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 820px) {
  .ptd-flow,
  .ptd-grid--threshold,
  .ptd-grid--llm,
  .ptd-guideline-tools,
  .ptd-section--llm .ptd-grid--llm,
  .ptd-section--llm .ptd-guideline-tools {
    grid-template-columns: 1fr;
  }

  .ptd-section--llm {
    grid-column: auto;
  }

  .ptd-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .ptd-summary__files {
    grid-column: 1 / -1;
  }
}

@media (max-width: 620px) {
  .ptd-footer,
  .ptd-actions,
  .ptd-resource__toolbar,
  .ptd-resource__buttons {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
