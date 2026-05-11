<script setup lang="ts">
import { Loader2, Sparkles } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import { pushToast } from '../composables/useToast'
import type { LLMProvider, LLMTranslateScope, TermBase, TMCollection } from '../types/api'
import { consumeLLMStream } from '../utils/llmStream'
import Modal from './base/Modal.vue'

interface ProjectFileItem {
  id: string
  filename: string
  source_language: string | null
  target_language: string | null
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
}>()

const { t } = useI18n()

const loadingResources = ref(false)
const running = ref(false)
const stopRequested = ref(false)
const currentAbortController = ref<AbortController | null>(null)

const tmCollections = ref<TMCollection[]>([])
const termBases = ref<TermBase[]>([])

const useTm = ref(true)
const tmCollectionIds = ref<string[]>([])
const tmThreshold = ref(0.75)
const tmSkipConfirmed = ref(true)
const tmOverwriteFuzzy = ref(true)
const tmAutoConfirmExact = ref(true)

const useLlm = ref(false)
const llmScope = ref<LLMTranslateScope>('all')
const llmProvider = ref<LLMProvider>('deepseek')
const llmGuidelines = ref('')

const useTermBase = ref(false)
const termBaseId = ref('')

const errorMessage = ref('')
const finishedCount = ref(0)

const progressByFileId = ref<Record<string, number>>({})
const statusByFileId = ref<Record<string, string>>({})

const llmProviderOptions: Array<{ value: LLMProvider, label: string }> = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'auto', label: 'Auto' },
]

const llmScopeOptions: Array<{ value: LLMTranslateScope, label: string }> = [
  { value: 'all', label: 'all' },
  { value: 'fuzzy_only', label: 'fuzzy_only' },
  { value: 'none_only', label: 'none_only' },
  { value: 'all_with_exact', label: 'all_with_exact' },
]

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

const availableTermBases = computed(() => {
  if (!props.sourceLanguage || !props.targetLanguage) {
    return termBases.value
  }
  return termBases.value.filter((termBase) => (
    termBase.source_language === props.sourceLanguage
    && termBase.target_language === props.targetLanguage
  ))
})

const selectedCount = computed(() => props.files.length)
const overallProgress = computed(() => {
  if (!props.files.length) {
    return 0
  }
  const total = props.files.reduce((sum, file) => sum + (progressByFileId.value[file.id] || 0), 0)
  return Math.round(total / props.files.length)
})

watch(() => props.open, (open) => {
  if (open) {
    void loadResources()
    resetProgress()
    errorMessage.value = ''
    stopRequested.value = false
    llmGuidelines.value = props.translationGuidelines || ''
  }
})

watch(availableTMCollections, () => {
  tmCollectionIds.value = tmCollectionIds.value.filter((id) => (
    availableTMCollections.value.some((collection) => collection.id === id)
  ))
})

watch(availableTermBases, () => {
  if (termBaseId.value && !availableTermBases.value.some((termBase) => termBase.id === termBaseId.value)) {
    termBaseId.value = ''
  }
})

function resetProgress() {
  finishedCount.value = 0
  progressByFileId.value = {}
  statusByFileId.value = {}
}

async function loadResources() {
  loadingResources.value = true
  try {
    const [{ data: collections }, { data: bases }] = await Promise.all([
      http.get<TMCollection[]>('/translation-memory/collections'),
      http.get<TermBase[]>('/term-bases'),
    ])
    tmCollections.value = collections
    termBases.value = bases
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

function validateBeforeStart() {
  if (!useTm.value && !useLlm.value && !useTermBase.value) {
    errorMessage.value = t('projectDetail.preTranslate.errors.selectOneOption')
    return false
  }
  if (useTm.value && tmCollectionIds.value.length === 0) {
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

async function runLLMForFile(fileId: string) {
  const token = window.localStorage.getItem('token')
  const controller = new AbortController()
  currentAbortController.value = controller
  try {
    const response = await fetch(`/api/file-records/${fileId}/llm-translate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        scope: llmScope.value,
        provider: llmProvider.value,
        translation_guidelines: llmGuidelines.value,
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

    await consumeLLMStream(response, () => {
      if (stopRequested.value) {
        return
      }
    })
  } finally {
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

  try {
    for (const file of props.files) {
      if (stopRequested.value) {
        break
      }

      statusByFileId.value[file.id] = t('projectDetail.preTranslate.progress.running')
      progressByFileId.value[file.id] = 0

      try {
        if (useTm.value) {
          await http.post(`/file-records/${file.id}/rematch`, {
            collection_ids: tmCollectionIds.value,
            threshold: tmThreshold.value,
            skip_confirmed: tmSkipConfirmed.value,
            overwrite_fuzzy: tmOverwriteFuzzy.value,
            auto_confirm_exact: tmAutoConfirmExact.value,
          })
          progressByFileId.value[file.id] = 34
        }

        if (useLlm.value && !stopRequested.value) {
          await runLLMForFile(file.id)
          progressByFileId.value[file.id] = useTermBase.value ? 67 : 100
        }

        if (useTermBase.value && !stopRequested.value) {
          const bindingsPayload: Record<string, string | null> = {
            term_base_id: termBaseId.value || null,
          }
          if (useTm.value) {
            bindingsPayload.collection_id = tmCollectionIds.value[0] || null
          }
          await http.patch(`/file-records/${file.id}/bindings`, bindingsPayload)
          progressByFileId.value[file.id] = 100
        }

        if (!useTm.value && !useLlm.value && useTermBase.value) {
          progressByFileId.value[file.id] = 100
        }

        finishedCount.value += 1
        statusByFileId.value[file.id] = t('projectDetail.preTranslate.progress.done')
      } catch (error) {
        const message = error instanceof Error ? error.message : t('projectDetail.preTranslate.errors.unknown')
        statusByFileId.value[file.id] = t('projectDetail.preTranslate.progress.failed')
        pushToast({
          tone: 'error',
          title: t('projectDetail.preTranslate.toast.fileFailedTitle', { name: file.filename }),
          message,
        })
      }
    }

    if (stopRequested.value) {
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
        total: selectedCount.value,
      }),
    })
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
    width="min(880px, calc(100vw - 32px))"
    @close="emit('close')"
  >
    <div class="ptd-layout">
      <div class="ptd-section">
        <label class="ptd-switch">
          <input v-model="useTm" type="checkbox" :disabled="running" />
          <span>{{ t('projectDetail.preTranslate.sections.tm') }}</span>
        </label>

        <div class="ptd-grid">
          <label class="field field--full">
            <span class="field__label">{{ t('projectDetail.preTranslate.tm.collections') }}</span>
            <select
              v-model="tmCollectionIds"
              class="field__control field__control--multi"
              multiple
              :disabled="running || loadingResources || !useTm"
            >
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
          <span class="ptd-percent">{{ Math.round(tmThreshold * 100) }}%</span>
        </div>

        <div class="ptd-checks">
          <label><input v-model="tmSkipConfirmed" type="checkbox" :disabled="running || !useTm" />{{ t('projectDetail.preTranslate.tm.skipConfirmed') }}</label>
          <label><input v-model="tmOverwriteFuzzy" type="checkbox" :disabled="running || !useTm" />{{ t('projectDetail.preTranslate.tm.overwriteFuzzy') }}</label>
          <label><input v-model="tmAutoConfirmExact" type="checkbox" :disabled="running || !useTm" />{{ t('projectDetail.preTranslate.tm.autoConfirmExact') }}</label>
        </div>
      </div>

      <div class="ptd-section">
        <label class="ptd-switch">
          <input v-model="useLlm" type="checkbox" :disabled="running" />
          <span>{{ t('projectDetail.preTranslate.sections.llm') }}</span>
        </label>
        <div class="ptd-grid">
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
          <span class="field__label">{{ t('projectDetail.preTranslate.llm.guidelines') }}</span>
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

      <div class="ptd-section">
        <label class="ptd-switch">
          <input v-model="useTermBase" type="checkbox" :disabled="running" />
          <span>{{ t('projectDetail.preTranslate.sections.termBase') }}</span>
        </label>
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

      <div v-if="running || Object.keys(progressByFileId).length > 0" class="ptd-progress">
        <div class="progress-bar">
          <div class="progress-bar__track">
            <div class="progress-bar__fill" :style="{ width: `${overallProgress}%` }" />
          </div>
          <span class="progress-bar__text">{{ overallProgress }}%</span>
        </div>
        <div class="ptd-progress-list">
          <div v-for="file in files" :key="file.id" class="ptd-progress-item">
            <span class="ptd-progress-name">{{ file.filename }}</span>
            <span class="ptd-progress-state">{{ statusByFileId[file.id] || t('projectDetail.preTranslate.progress.pending') }}</span>
          </div>
        </div>
      </div>

      <p v-if="errorMessage" class="form-message is-error">{{ errorMessage }}</p>
    </div>

    <template #footer>
      <div class="ptd-footer">
        <span class="ptd-selected">{{ t('projectDetail.preTranslate.selectedSummary', { count: selectedCount }) }}</span>
        <div class="ptd-actions">
          <button class="button" type="button" :disabled="running" @click="emit('close')">
            {{ t('common.actions.cancel') }}
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
.ptd-layout { display: grid; gap: 14px; }
.ptd-section { border: 1px solid var(--line-soft); border-radius: 10px; padding: 12px; display: grid; gap: 10px; }
.ptd-switch { display: inline-flex; gap: 8px; align-items: center; font-weight: 600; }
.ptd-grid { display: grid; grid-template-columns: minmax(0, 1fr) 96px; gap: 10px; align-items: end; }
.ptd-percent { color: var(--text-muted); font-size: 12px; }
.ptd-checks { display: grid; gap: 6px; font-size: 13px; }
.ptd-checks label { display: inline-flex; align-items: center; gap: 6px; }
.ptd-progress { display: grid; gap: 8px; }
.ptd-progress-list { display: grid; gap: 6px; max-height: 180px; overflow: auto; }
.ptd-progress-item { display: flex; justify-content: space-between; gap: 10px; font-size: 13px; }
.ptd-progress-name { color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ptd-progress-state { color: var(--text-muted); flex-shrink: 0; }
.ptd-footer { width: 100%; display: flex; justify-content: space-between; gap: 10px; align-items: center; }
.ptd-selected { color: var(--text-muted); font-size: 13px; }
.ptd-actions { display: flex; gap: 8px; }
.ptd-guidelines { resize: vertical; min-height: 60px; max-height: 200px; font-size: 13px; line-height: 1.5; }
</style>
