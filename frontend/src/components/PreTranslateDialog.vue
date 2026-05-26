<script setup lang="ts">
import { BookOpenCheck, Bot, Database, Loader2, Sparkles, Upload } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import { pushToast } from '../composables/useToast'
import type { GuidelineTemplateSummary, LLMProvider, LLMTranslateScope, TermBase, TMCollection } from '../types/api'
import { consumeLLMStream } from '../utils/llmStream'
import { isProgressComplete } from '../utils/progress'
import Modal from './base/Modal.vue'

interface ProjectFileItem {
  id: string
  filename: string
  source_language: string | null
  target_language: string | null
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

const props = defineProps<{
  open: boolean
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

const NO_TM_COLLECTION_ID = '__NO_TM_COLLECTION__'
const FILE_OPERATION_TOKEN_HEADER = 'X-File-Operation-Token'
const LOCK_HEARTBEAT_INTERVAL_MS = 30_000
const LLM_STREAM_IDLE_TIMEOUT_MS = 150_000

const { t } = useI18n()

const loadingResources = ref(false)
const running = ref(false)
const stopRequested = ref(false)
const currentAbortController = ref<AbortController | null>(null)

const tmCollections = ref<TMCollection[]>([])
const termBases = ref<TermBase[]>([])
const guidelineTemplates = ref<GuidelineTemplateSummary[]>([])

const useTm = ref(true)
const tmCollectionIds = ref<string[]>([NO_TM_COLLECTION_ID])
const tmThreshold = ref(0.75)
const tmSkipConfirmed = ref(true)
const tmOverwriteFuzzy = ref(true)
const tmAutoConfirmExact = ref(true)

const useLlm = ref(false)
const llmScope = ref<LLMTranslateScope>('all')
const llmProvider = ref<LLMProvider>('deepseek')
const llmGuidelines = ref('')
const selectedGuidelineTemplateId = ref('')
const importingGuidelineTemplate = ref(false)
const guidelineTemplateInputRef = ref<HTMLInputElement | null>(null)

const useTermBase = ref(false)
const termBaseId = ref('')

const errorMessage = ref('')
const finishedCount = ref(0)
const runFiles = ref<ProjectFileItem[]>([])

const progressByFileId = ref<Record<string, number>>({})
const statusByFileId = ref<Record<string, string>>({})

const llmProviderOptions = computed<Array<{ value: LLMProvider, label: string }>>(() => [
  { value: 'deepseek', label: t('projectDetail.preTranslate.llm.providers.deepseek') },
  { value: 'openrouter', label: t('projectDetail.preTranslate.llm.providers.openrouter') },
  { value: 'auto', label: t('projectDetail.preTranslate.llm.providers.auto') },
])

const llmScopeOptions = computed<Array<{ value: LLMTranslateScope, label: string }>>(() => [
  { value: 'all', label: t('projectDetail.preTranslate.llm.scopes.all') },
  { value: 'fuzzy_only', label: t('projectDetail.preTranslate.llm.scopes.fuzzyOnly') },
  { value: 'none_only', label: t('projectDetail.preTranslate.llm.scopes.noneOnly') },
  { value: 'empty_target_only', label: t('projectDetail.preTranslate.llm.scopes.emptyTargetOnly') },
  { value: 'all_with_exact', label: t('projectDetail.preTranslate.llm.scopes.allWithExact') },
])

const selectedFileLanguagePairs = computed(() => {
  const pairSet = new Set<string>()
  for (const file of props.files) {
    const source = file.source_language || props.sourceLanguage
    const target = file.target_language || props.targetLanguage
    if (!source || !target) {
      continue
    }
    pairSet.add(`${source}__${target}`)
  }
  return Array.from(pairSet)
})

function collectionMatchesSelectedFiles(collection: TMCollection) {
  if (selectedFileLanguagePairs.value.length === 0) {
    return true
  }
  if (!collection.source_language || !collection.target_language) {
    return false
  }
  return selectedFileLanguagePairs.value.every((pairKey) => (
    pairKey === `${collection.source_language}__${collection.target_language}`
  ))
}

const availableTMCollections = computed(() => {
  return tmCollections.value.filter((collection) => collectionMatchesSelectedFiles(collection))
})

const selectedTmCollectionIds = computed(() => (
  tmCollectionIds.value.filter((collectionId) => collectionId !== NO_TM_COLLECTION_ID)
))

const shouldRunTm = computed(() => useTm.value && selectedTmCollectionIds.value.length > 0)

const tmCollectionIdsModel = computed<string[]>({
  get: () => tmCollectionIds.value,
  set: (collectionIds) => {
    tmCollectionIds.value = normalizeTmCollectionIds(collectionIds)
  },
})

const availableTermBases = computed(() => {
  if (!props.sourceLanguage || !props.targetLanguage) {
    return termBases.value
  }
  return termBases.value.filter((termBase) => (
    termBase.source_language === props.sourceLanguage
    && termBase.target_language === props.targetLanguage
  ))
})

const selectedDisplayFiles = computed(() => (
  running.value && runFiles.value.length > 0 ? runFiles.value : props.files
))
const selectedCount = computed(() => selectedDisplayFiles.value.length)
const selectedFilePreview = computed(() => selectedDisplayFiles.value.slice(0, 4))
const configuredActionCount = computed(() => (
  Number(useTm.value) + Number(useLlm.value) + Number(useTermBase.value)
))
const progressFiles = computed(() => (
  runFiles.value.length > 0 ? runFiles.value : props.files
))
const overallProgress = computed(() => {
  if (!progressFiles.value.length) {
    return 0
  }
  const total = progressFiles.value.reduce((sum, file) => sum + (progressByFileId.value[file.id] || 0), 0)
  return Math.round(total / progressFiles.value.length)
})

watch(() => props.open, (open) => {
  if (open) {
    void loadResources()
    if (!running.value) {
      resetProgress()
    }
    errorMessage.value = ''
    stopRequested.value = false
    llmGuidelines.value = props.translationGuidelines || ''
  }
})

watch(availableTMCollections, () => {
  tmCollectionIds.value = normalizeTmCollectionIds(tmCollectionIds.value)
})

watch(availableTermBases, () => {
  if (termBaseId.value && !availableTermBases.value.some((termBase) => termBase.id === termBaseId.value)) {
    termBaseId.value = ''
  }
})

function resetProgress() {
  finishedCount.value = 0
  runFiles.value = []
  progressByFileId.value = {}
  statusByFileId.value = {}
}

function normalizeProgress(progress: number) {
  const safeProgress = Number.isFinite(progress) ? progress : 0
  return Math.max(0, Math.min(100, Math.round(safeProgress)))
}

function getRunActionCount() {
  return Math.max(
    1,
    Number(shouldRunTm.value) + Number(useLlm.value) + Number(useTermBase.value),
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

function normalizeTmCollectionIds(collectionIds: string[]) {
  const availableIds = new Set(availableTMCollections.value.map((collection) => collection.id))
  const wantsNoCollection = collectionIds.includes(NO_TM_COLLECTION_ID)
  const hadNoCollection = tmCollectionIds.value.includes(NO_TM_COLLECTION_ID)

  if (wantsNoCollection && (!hadNoCollection || collectionIds.length === 1)) {
    return [NO_TM_COLLECTION_ID]
  }

  const normalizedIds = collectionIds.filter((collectionId) => (
    collectionId !== NO_TM_COLLECTION_ID && availableIds.has(collectionId)
  ))
  return normalizedIds.length > 0 ? normalizedIds : [NO_TM_COLLECTION_ID]
}

async function loadResources() {
  loadingResources.value = true
  try {
    const [{ data: collections }, { data: bases }, { data: templates }] = await Promise.all([
      http.get<TMCollection[]>('/translation-memory/collections'),
      http.get<TermBase[]>('/term-bases'),
      http.get<GuidelineTemplateSummary[]>('/guideline-templates'),
    ])
    tmCollections.value = collections
    termBases.value = bases
    guidelineTemplates.value = templates
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
  if (!useTm.value && !useLlm.value && !useTermBase.value) {
    errorMessage.value = t('projectDetail.preTranslate.errors.selectOneOption')
    return false
  }
  if (useTm.value && selectedTmCollectionIds.value.length === 0) {
    errorMessage.value = t('projectDetail.preTranslate.errors.tmCollectionRequired')
    return false
  }
  if (useTermBase.value && !termBaseId.value) {
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
        guideline_template_id: selectedGuidelineTemplateId.value || null,
        temporary_prompt: llmGuidelines.value,
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

    await consumeLLMStream(response, ({ event, data }) => {
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
    })

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
  }
}

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
            threshold: tmThreshold.value,
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
          const bindingsPayload: Record<string, string | null> = {
            term_base_id: termBaseId.value || null,
          }
          if (shouldRunTm.value) {
            bindingsPayload.collection_id = selectedTmCollectionIds.value[0] || null
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
          try {
            await releasePreTranslateLock(file.id, operationToken)
          } catch (error) {
            console.warn('Failed to release pre-translate lock:', error)
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

function stopPreTranslate() {
  if (!running.value) {
    return
  }
  stopRequested.value = true
  currentAbortController.value?.abort()
}
</script>

<template>
  <Modal
    :open="open"
    :title="t('projectDetail.preTranslate.dialogTitle')"
    :description="t('projectDetail.preTranslate.dialogDescription')"
    width="min(940px, calc(100vw - 32px))"
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
          >
            {{ file.filename }}
          </span>
          <span v-if="selectedCount > selectedFilePreview.length" class="ptd-summary__more">
            +{{ selectedCount - selectedFilePreview.length }}
          </span>
        </div>
      </aside>

      <div class="ptd-flow">
      <div class="ptd-section" :class="{ 'is-disabled': !useTm }">
        <div class="ptd-section__head">
          <label class="ptd-switch">
            <input v-model="useTm" type="checkbox" :disabled="running" />
            <span class="ptd-switch__control" aria-hidden="true" />
            <span class="ptd-section__icon"><Database :size="17" /></span>
            <span>{{ t('projectDetail.preTranslate.sections.tm') }}</span>
          </label>
          <span class="ptd-section__meta">{{ Math.round(tmThreshold * 100) }}%</span>
        </div>

        <div class="ptd-grid ptd-grid--tm">
          <label class="field field--full">
            <span class="field__label">{{ t('projectDetail.preTranslate.tm.collections') }}</span>
            <select
              v-model="tmCollectionIdsModel"
              class="field__control field__control--multi"
              multiple
              :disabled="running || loadingResources || !useTm"
            >
              <option :value="NO_TM_COLLECTION_ID">
                {{ t('projectDetail.preTranslate.tm.noCollection') }}
              </option>
              <option v-for="collection in availableTMCollections" :key="collection.id" :value="collection.id">
                {{ collection.name }}（{{ collection.entry_count }}）
              </option>
            </select>
          </label>

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

      <div class="ptd-section" :class="{ 'is-disabled': !useLlm }">
        <div class="ptd-section__head">
          <label class="ptd-switch">
            <input v-model="useLlm" type="checkbox" :disabled="running" />
            <span class="ptd-switch__control" aria-hidden="true" />
            <span class="ptd-section__icon"><Bot :size="17" /></span>
            <span>{{ t('projectDetail.preTranslate.sections.llm') }}</span>
          </label>
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
            <span class="field__label">{{ t('projectDetail.preTranslate.llm.scope') }}</span>
            <select v-model="llmScope" class="field__control" :disabled="running || !useLlm">
              <option v-for="option in llmScopeOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
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
        <p class="hint-text">{{ t('projectDetail.preTranslate.llm.hint') }}</p>
      </div>

      <div class="ptd-section" :class="{ 'is-disabled': !useTermBase }">
        <div class="ptd-section__head">
          <label class="ptd-switch">
            <input v-model="useTermBase" type="checkbox" :disabled="running" />
            <span class="ptd-switch__control" aria-hidden="true" />
            <span class="ptd-section__icon"><BookOpenCheck :size="17" /></span>
            <span>{{ t('projectDetail.preTranslate.sections.termBase') }}</span>
          </label>
        </div>
        <label class="field">
          <span class="field__label">{{ t('projectDetail.preTranslate.termBase.select') }}</span>
          <select v-model="termBaseId" class="field__control" :disabled="running || loadingResources || !useTermBase">
            <option value="">{{ t('projectDetail.preTranslate.termBase.placeholder') }}</option>
            <option v-for="termBase in availableTermBases" :key="termBase.id" :value="termBase.id">
              {{ termBase.name }}（{{ termBase.entry_count }}）
            </option>
          </select>
        </label>
        <p class="hint-text">{{ t('projectDetail.preTranslate.termBase.hint') }}</p>
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
            class="button"
            type="button"
            @click="stopPreTranslate"
          >
            {{ t('common.actions.stop') }}
          </button>
          <button
            class="button button--primary"
            type="button"
            :disabled="running || selectedCount === 0"
            @click="startPreTranslate"
          >
            <Loader2 v-if="running" class="lucide-spin" :size="14" />
            <Sparkles v-else :size="14" />
            {{ t('projectDetail.preTranslate.start') }}
          </button>
        </div>
      </div>
    </template>
  </Modal>
</template>

<style scoped>
.ptd-layout {
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.ptd-summary {
  position: sticky;
  top: 0;
  display: grid;
  gap: 10px;
}

.ptd-summary__stat {
  display: grid;
  gap: 2px;
  min-height: 70px;
  align-content: center;
  padding: 12px;
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
  padding: 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
}

.ptd-summary__file {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-secondary);
  font-size: 12px;
}

.ptd-flow {
  display: grid;
  gap: 12px;
  min-width: 0;
}

.ptd-section {
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.ptd-section.is-disabled {
  background: var(--surface-1);
}

.ptd-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.ptd-section__icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--brand-050);
  color: var(--brand-700);
}

.ptd-section__meta {
  color: var(--text-muted);
  font-size: 13px;
}

.ptd-switch {
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
  width: 34px;
  height: 20px;
  border: 1px solid var(--line-strong);
  border-radius: 999px;
  background: var(--surface-muted);
  flex-shrink: 0;
}

.ptd-switch__control::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 999px;
  background: var(--text-muted);
  transition: transform 0.18s ease, background 0.18s ease;
}

.ptd-switch input:checked + .ptd-switch__control {
  border-color: var(--brand-700);
  background: var(--brand-100);
}

.ptd-switch input:checked + .ptd-switch__control::after {
  background: var(--brand-700);
  transform: translateX(14px);
}

.ptd-switch input:focus-visible + .ptd-switch__control {
  outline: 2px solid rgba(13, 122, 104, 0.18);
  outline-offset: 2px;
}

.ptd-grid {
  display: grid;
  gap: 12px;
  align-items: end;
}

.ptd-grid--tm {
  grid-template-columns: minmax(0, 1fr) 128px;
}

.ptd-grid--llm {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.ptd-guideline-tools {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
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
  accent-color: var(--brand-700);
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
  min-height: 80px;
  max-height: 220px;
  font-size: 13px;
  line-height: 1.5;
}

@media (max-width: 820px) {
  .ptd-layout,
  .ptd-grid--tm,
  .ptd-grid--llm,
  .ptd-guideline-tools {
    grid-template-columns: 1fr;
  }

  .ptd-summary {
    position: static;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .ptd-summary__files {
    grid-column: 1 / -1;
  }
}

@media (max-width: 620px) {
  .ptd-footer,
  .ptd-actions {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
