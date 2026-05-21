<script setup lang="ts">
import axios from 'axios'
import {
  ExternalLink,
  GitMerge,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
} from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { http } from '../api/http'
import { pushToast } from '../composables/useToast'
import { formatLanguagePair } from '../constants/languages'
import type {
  ExtractedTermDraft,
  TermBase,
  TermBatchSaveResult,
  TermExtractionModelResult,
  TermExtractionResult,
} from '../types/api'
import Modal from './base/Modal.vue'

interface ProjectFileItem {
  id: string
  filename: string
  total_segments: number
  source_language: string | null
  target_language: string | null
  term_base_id: string | null
}

type TermDraftAction = 'add' | 'replace' | 'skip'
type VersionKey = 'merged' | `model:${string}`

interface TermDraftRow extends ExtractedTermDraft {
  row_id: string
  action: TermDraftAction
}

interface ModelOption {
  id: string
  name: string
}

interface VersionOption {
  key: VersionKey
  label: string
  model: string | null
  provider: string | null
  terms: ExtractedTermDraft[]
}

const props = defineProps<{
  open: boolean
  file: ProjectFileItem | null
  projectSourceLanguage: string | null
  projectTargetLanguage: string | null
}>()

const emit = defineEmits<{
  close: []
  done: []
}>()

const { t } = useI18n()
const router = useRouter()

const defaultModelId = 'google/gemini-3-flash-preview'
const modelOptions: ModelOption[] = [
  { id: defaultModelId, name: 'Gemini 3 Flash Preview' },
  { id: 'openai/gpt-4o-mini', name: 'GPT-4o mini' },
  { id: 'deepseek/deepseek-chat', name: 'DeepSeek Chat' },
]

const loadingBases = ref(false)
const extracting = ref(false)
const checkingConflicts = ref(false)
const saving = ref(false)
const creatingBase = ref(false)

const termBases = ref<TermBase[]>([])
const selectedTermBaseId = ref('')
const drafts = ref<TermDraftRow[]>([])
const selectedModels = ref<string[]>([defaultModelId])
const customModelId = ref('')
const extractionPrompt = ref('')
const modelResults = ref<TermExtractionModelResult[]>([])
const mergedTerms = ref<ExtractedTermDraft[]>([])
const selectedVersionKey = ref<VersionKey | ''>('')
const errorMessage = ref('')
const saveResult = ref<TermBatchSaveResult | null>(null)
const newBaseName = ref('')

const sourceLanguage = computed(() => props.file?.source_language || props.projectSourceLanguage || '')
const targetLanguage = computed(() => props.file?.target_language || props.projectTargetLanguage || '')
const languagePairLabel = computed(() => (
  sourceLanguage.value && targetLanguage.value
    ? formatLanguagePair(sourceLanguage.value, targetLanguage.value)
    : t('projectDetail.termExtraction.languageMissing')
))
const availableTermBases = computed(() => termBases.value.filter((termBase) => (
  termBase.source_language === sourceLanguage.value
  && termBase.target_language === targetLanguage.value
)))
const selectedTermBase = computed(() => (
  termBases.value.find((termBase) => termBase.id === selectedTermBaseId.value) ?? null
))
const conflictCount = computed(() => drafts.value.filter((draft) => draft.has_conflict).length)
const saveableCount = computed(() => drafts.value.filter((draft) => (
  draft.source_text.trim()
  && draft.target_text.trim()
  && draft.action !== 'skip'
)).length)
const hasExtractionResults = computed(() => modelResults.value.length > 0 || mergedTerms.value.length > 0)
const versionOptions = computed<VersionOption[]>(() => {
  const options: VersionOption[] = []
  if (modelResults.value.length > 1) {
    options.push({
      key: 'merged',
      label: t('projectDetail.termExtraction.mergedVersion'),
      model: null,
      provider: null,
      terms: mergedTerms.value,
    })
  }
  modelResults.value.forEach((result, index) => {
    options.push({
      key: getModelVersionKey(result, index),
      label: getModelLabel(result.model),
      model: result.model,
      provider: result.provider,
      terms: result.terms,
    })
  })
  return options
})
const selectedVersion = computed(() => (
  versionOptions.value.find((option) => option.key === selectedVersionKey.value) ?? null
))
const compareResults = computed(() => (
  modelResults.value.length > 1 ? modelResults.value.slice(0, 2) : []
))
const selectedModelSummary = computed(() => selectedModels.value.map(getModelLabel).join(' / '))
const canExtract = computed(() => Boolean(
  props.file
  && sourceLanguage.value
  && targetLanguage.value
  && selectedModels.value.length > 0
  && selectedModels.value.length <= 2
  && !extracting.value,
))
const canSave = computed(() => Boolean(
  props.file
  && selectedTermBaseId.value
  && drafts.value.length > 0
  && saveableCount.value > 0
  && !saving.value
  && !extracting.value,
))

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

function resetState() {
  drafts.value = []
  modelResults.value = []
  mergedTerms.value = []
  selectedVersionKey.value = ''
  selectedModels.value = [defaultModelId]
  customModelId.value = ''
  extractionPrompt.value = ''
  errorMessage.value = ''
  saveResult.value = null
  selectedTermBaseId.value = props.file?.term_base_id || ''
  newBaseName.value = props.file ? `${props.file.filename} 术语` : ''
}

function getModelLabel(modelId: string) {
  if (modelId === 'deepseek-chat') {
    return 'DeepSeek Chat'
  }
  return modelOptions.find((option) => option.id === modelId)?.name || modelId
}

function getModelVersionKey(result: TermExtractionModelResult, index: number): VersionKey {
  return `model:${index}:${result.model}`
}

function countTermConflicts(terms: ExtractedTermDraft[]) {
  return terms.filter((term) => term.has_conflict).length
}

function toggleModel(modelId: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  if (checked) {
    if (selectedModels.value.includes(modelId)) {
      return
    }
    if (selectedModels.value.length >= 2) {
      ;(event.target as HTMLInputElement).checked = false
      pushToast({
        tone: 'warn',
        title: t('projectDetail.termExtraction.modelLimit'),
        message: t('projectDetail.termExtraction.modelCompareHint'),
      })
      return
    }
    selectedModels.value = [...selectedModels.value, modelId]
    return
  }
  selectedModels.value = selectedModels.value.filter((selected) => selected !== modelId)
}

function addCustomModel() {
  const modelId = customModelId.value.trim()
  if (!modelId) {
    return
  }
  if (selectedModels.value.includes(modelId)) {
    customModelId.value = ''
    return
  }
  if (selectedModels.value.length >= 2) {
    pushToast({
      tone: 'warn',
      title: t('projectDetail.termExtraction.modelLimit'),
      message: t('projectDetail.termExtraction.modelCompareHint'),
    })
    return
  }
  selectedModels.value = [...selectedModels.value, modelId]
  customModelId.value = ''
}

function removeSelectedModel(modelId: string) {
  selectedModels.value = selectedModels.value.filter((selected) => selected !== modelId)
}

function toDraftRows(terms: ExtractedTermDraft[], versionKey: string = 'manual'): TermDraftRow[] {
  return terms.map((term, index) => ({
    ...term,
    row_id: `${Date.now()}-${versionKey}-${index}-${term.source_normalized || term.source_text}`,
    index,
    action: term.has_conflict ? 'skip' : 'add',
  }))
}

function applyVersion(versionKey: VersionKey) {
  const version = versionOptions.value.find((option) => option.key === versionKey)
  if (!version) {
    return
  }
  selectedVersionKey.value = versionKey
  drafts.value = toDraftRows(version.terms, versionKey)
  saveResult.value = null
}

function applyDefaultVersion(data: TermExtractionResult) {
  const results = data.results && data.results.length > 0
    ? data.results
    : [{
        provider: data.provider,
        model: data.model,
        terms: data.terms,
        total: data.total,
      }]
  modelResults.value = results
  mergedTerms.value = data.merged_terms && data.merged_terms.length > 0 ? data.merged_terms : data.terms
  const defaultVersionKey: VersionKey = results.length > 1 ? 'merged' : `model:0:${results[0].model}`
  applyVersion(defaultVersionKey)
}

async function loadTermBases() {
  loadingBases.value = true
  try {
    const { data } = await http.get<TermBase[]>('/term-bases')
    termBases.value = data
    if (selectedTermBaseId.value && !data.some((termBase) => termBase.id === selectedTermBaseId.value)) {
      selectedTermBaseId.value = ''
    }
  } catch (error) {
    errorMessage.value = getErrorMessage(error, t('projectDetail.termExtraction.errors.loadTermBases'))
  } finally {
    loadingBases.value = false
  }
}

async function runExtraction() {
  if (!props.file || !canExtract.value) {
    return
  }

  extracting.value = true
  errorMessage.value = ''
  saveResult.value = null
  try {
    const { data } = await http.post<TermExtractionResult>(
      `/file-records/${props.file.id}/term-extraction`,
      {
        term_base_id: selectedTermBaseId.value || null,
        max_terms: 150,
        models: selectedModels.value,
        extraction_prompt: extractionPrompt.value.trim(),
      },
    )
    applyDefaultVersion(data)
    if (data.errors && data.errors.length > 0) {
      errorMessage.value = data.errors
        .map((item) => `${getModelLabel(item.model)}：${item.message}`)
        .join('\n')
    }
    if (drafts.value.length === 0) {
      errorMessage.value = t('projectDetail.termExtraction.emptyResult')
    }
  } catch (error) {
    errorMessage.value = getErrorMessage(error, t('projectDetail.termExtraction.errors.extract'))
  } finally {
    extracting.value = false
  }
}

async function refreshConflicts() {
  if (!selectedTermBaseId.value || drafts.value.length === 0) {
    drafts.value = drafts.value.map((draft) => ({
      ...draft,
      has_conflict: false,
      conflict: null,
      action: draft.action === 'skip' ? 'skip' : 'add',
    }))
    return
  }

  checkingConflicts.value = true
  errorMessage.value = ''
  try {
    const { data } = await http.post<{ items: ExtractedTermDraft[], conflict_count: number }>(
      `/term-bases/${selectedTermBaseId.value}/entries/conflicts`,
      {
        entries: drafts.value.map((draft) => ({
          source_text: draft.source_text,
          target_text: draft.target_text,
          action: draft.action,
        })),
      },
    )
    const conflictByIndex = new Map(data.items.map((item) => [item.index, item]))
    drafts.value = drafts.value.map((draft, index) => {
      const next = conflictByIndex.get(index)
      if (!next) {
        return draft
      }
      const action = next.has_conflict && draft.action === 'add' ? 'skip' : draft.action
      return {
        ...draft,
        source_normalized: next.source_normalized,
        has_conflict: next.has_conflict,
        conflict: next.conflict,
        action: next.has_conflict ? action : (draft.action === 'replace' ? 'add' : draft.action),
      }
    })
  } catch (error) {
    errorMessage.value = getErrorMessage(error, t('projectDetail.termExtraction.errors.conflicts'))
  } finally {
    checkingConflicts.value = false
  }
}

async function createTermBase() {
  if (!sourceLanguage.value || !targetLanguage.value) {
    errorMessage.value = t('projectDetail.termExtraction.languageMissing')
    return
  }
  const name = newBaseName.value.trim()
  if (!name) {
    errorMessage.value = t('projectDetail.termExtraction.errors.baseNameRequired')
    return
  }

  creatingBase.value = true
  errorMessage.value = ''
  try {
    const { data } = await http.post<TermBase>('/term-bases', {
      name,
      description: props.file ? `从文件“${props.file.filename}”提取生成` : '',
      source_language: sourceLanguage.value,
      target_language: targetLanguage.value,
    })
    await loadTermBases()
    selectedTermBaseId.value = data.id
    await refreshConflicts()
    pushToast({
      tone: 'success',
      title: t('projectDetail.termExtraction.toast.baseCreated'),
      message: data.name,
    })
  } catch (error) {
    errorMessage.value = getErrorMessage(error, t('projectDetail.termExtraction.errors.createBase'))
  } finally {
    creatingBase.value = false
  }
}

function removeDraft(rowId: string) {
  drafts.value = drafts.value.filter((draft) => draft.row_id !== rowId)
}

function addEmptyDraft() {
  drafts.value = [
    ...drafts.value,
    {
      row_id: `${Date.now()}-manual`,
      index: drafts.value.length,
      source_text: '',
      target_text: '',
      source_normalized: '',
      has_conflict: false,
      conflict: null,
      action: 'add',
    },
  ]
}

async function saveTerms() {
  if (!props.file || !selectedTermBaseId.value || saving.value) {
    return
  }

  saving.value = true
  errorMessage.value = ''
  saveResult.value = null
  try {
    const { data } = await http.post<TermBatchSaveResult>(
      `/term-bases/${selectedTermBaseId.value}/entries/batch`,
      {
        entries: drafts.value.map((draft) => ({
          source_text: draft.source_text,
          target_text: draft.target_text,
          action: draft.action,
        })),
      },
    )
    saveResult.value = data
    await http.patch(`/file-records/${props.file.id}/bindings`, {
      term_base_id: selectedTermBaseId.value,
    })
    await refreshConflicts()
    emit('done')
    pushToast({
      tone: data.conflict_count > 0 ? 'warn' : 'success',
      title: t('projectDetail.termExtraction.toast.saved'),
      message: t('projectDetail.termExtraction.toast.savedMessage', {
        created: data.created_count,
        updated: data.updated_count,
        skipped: data.skipped_count,
        conflicts: data.conflict_count,
      }),
    })
  } catch (error) {
    errorMessage.value = getErrorMessage(error, t('projectDetail.termExtraction.errors.save'))
  } finally {
    saving.value = false
  }
}

function openTermBase() {
  if (!selectedTermBaseId.value) {
    return
  }
  void router.push({ name: 'term-base-edit', params: { id: selectedTermBaseId.value } })
  emit('close')
}

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      return
    }
    resetState()
    await loadTermBases()
  },
)

watch(selectedTermBaseId, async (next, previous) => {
  if (!props.open || next === previous || extracting.value) {
    return
  }
  await refreshConflicts()
})
</script>

<template>
  <Modal
    :open="open"
    :title="t('projectDetail.termExtraction.dialogTitle')"
    :description="file ? t('projectDetail.termExtraction.dialogDescription', { name: file.filename }) : ''"
    width="min(1080px, calc(100vw - 32px))"
    :close-on-overlay="!extracting && !saving"
    :close-on-esc="!extracting && !saving"
    @close="emit('close')"
  >
    <div class="term-extract">
      <div class="term-extract__summary">
        <span class="tag">{{ languagePairLabel }}</span>
        <span class="tag">{{ t('projectDetail.termExtraction.segmentCount', { count: file?.total_segments || 0 }) }}</span>
        <span v-if="selectedModelSummary" class="tag">{{ selectedModelSummary }}</span>
        <span v-if="selectedVersion" class="tag">{{ selectedVersion.label }}</span>
        <span class="tag">{{ t('projectDetail.termExtraction.termCount', { count: drafts.length }) }}</span>
        <span v-if="conflictCount > 0" class="tag is-warning">
          {{ t('projectDetail.termExtraction.conflictCount', { count: conflictCount }) }}
        </span>
      </div>

      <section class="term-extract__target">
        <label class="field">
          <span class="field__label">{{ t('projectDetail.termExtraction.targetBase') }}</span>
          <select
            v-model="selectedTermBaseId"
            class="field__control"
            :disabled="loadingBases || extracting || saving"
          >
            <option value="">{{ t('projectDetail.termExtraction.selectBase') }}</option>
            <option v-for="termBase in availableTermBases" :key="termBase.id" :value="termBase.id">
              {{ termBase.name }}（{{ termBase.entry_count }}）
            </option>
          </select>
        </label>
        <label class="field">
          <span class="field__label">{{ t('projectDetail.termExtraction.newBaseName') }}</span>
          <input
            v-model="newBaseName"
            class="field__control"
            type="text"
            :disabled="creatingBase || extracting || saving"
          />
        </label>
        <button
          class="button"
          type="button"
          :disabled="creatingBase || extracting || saving"
          @click="createTermBase"
        >
          <Loader2 v-if="creatingBase" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ t('projectDetail.termExtraction.createBase') }}
        </button>
      </section>

      <section class="term-extract__config">
        <div class="term-extract__config-head">
          <span class="section-title section-title--tight">{{ t('projectDetail.termExtraction.modelConfig') }}</span>
          <span class="term-extract__muted">{{ t('projectDetail.termExtraction.modelCompareHint') }}</span>
        </div>
        <div class="term-extract__model-grid">
          <label
            v-for="option in modelOptions"
            :key="option.id"
            class="term-extract__model-option"
            :class="{ 'is-selected': selectedModels.includes(option.id) }"
          >
            <input
              type="checkbox"
              :checked="selectedModels.includes(option.id)"
              :disabled="extracting || saving"
              @change="toggleModel(option.id, $event)"
            />
            <span>
              <strong>{{ option.name }}</strong>
              <small>{{ option.id }}</small>
            </span>
          </label>
        </div>
        <div class="term-extract__custom-model">
          <input
            v-model.trim="customModelId"
            class="field__control"
            type="text"
            :placeholder="t('projectDetail.termExtraction.customModelPlaceholder')"
            :disabled="extracting || saving"
            @keydown.enter.prevent="addCustomModel"
          />
          <button class="button" type="button" :disabled="extracting || saving || !customModelId.trim()" @click="addCustomModel">
            <Plus :size="14" />
            {{ t('projectDetail.termExtraction.addModel') }}
          </button>
        </div>
        <div v-if="selectedModels.length > 0" class="term-extract__selected-models">
          <button
            v-for="modelId in selectedModels"
            :key="modelId"
            class="tag term-extract__model-chip"
            type="button"
            :disabled="extracting || saving"
            @click="removeSelectedModel(modelId)"
          >
            {{ getModelLabel(modelId) }} ×
          </button>
        </div>
        <label class="field">
          <span class="field__label">{{ t('projectDetail.termExtraction.promptLabel') }}</span>
          <textarea
            v-model="extractionPrompt"
            class="field__control term-extract__prompt"
            rows="4"
            :placeholder="t('projectDetail.termExtraction.promptPlaceholder')"
            :disabled="extracting || saving"
          />
        </label>
      </section>

      <div class="term-extract__tools">
        <button class="button" type="button" :disabled="!canExtract" @click="runExtraction">
          <Loader2 v-if="extracting" class="lucide-spin" :size="14" />
          <Sparkles v-else :size="14" />
          {{ hasExtractionResults ? t('projectDetail.termExtraction.extractAgain') : t('projectDetail.termExtraction.startExtract') }}
        </button>
        <button
          class="button"
          type="button"
          :disabled="checkingConflicts || !selectedTermBaseId || drafts.length === 0"
          @click="refreshConflicts"
        >
          <Loader2 v-if="checkingConflicts" class="lucide-spin" :size="14" />
          <RefreshCw v-else :size="14" />
          {{ t('projectDetail.termExtraction.refreshConflicts') }}
        </button>
        <button class="button" type="button" :disabled="extracting || saving" @click="addEmptyDraft">
          <Plus :size="14" />
          {{ t('projectDetail.termExtraction.addRow') }}
        </button>
      </div>

      <section v-if="hasExtractionResults" class="term-extract__versions">
        <div class="term-extract__config-head">
          <span class="section-title section-title--tight">{{ t('projectDetail.termExtraction.versionTitle') }}</span>
          <span class="term-extract__muted">{{ t('projectDetail.termExtraction.versionHint') }}</span>
        </div>
        <div v-if="compareResults.length === 2" class="term-extract__compare">
          <article
            v-for="(result, index) in compareResults"
            :key="`${result.model}-${index}`"
            class="term-extract__compare-pane"
            :class="{ 'is-selected': selectedVersionKey === getModelVersionKey(result, index) }"
          >
            <div class="term-extract__compare-head">
              <div>
                <strong>{{ getModelLabel(result.model) }}</strong>
                <small>{{ result.model }}</small>
              </div>
              <button
                class="button"
                type="button"
                :disabled="extracting || saving"
                @click="applyVersion(getModelVersionKey(result, index))"
              >
                <Sparkles :size="14" />
                {{ t('projectDetail.termExtraction.useVersion') }}
              </button>
            </div>
            <div class="term-extract__compare-table-wrap">
              <table class="term-extract__compare-table">
                <thead>
                  <tr>
                    <th>{{ t('projectDetail.termExtraction.columns.source') }}</th>
                    <th>{{ t('projectDetail.termExtraction.columns.target') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-if="result.terms.length === 0">
                    <td colspan="2" class="term-extract__compare-empty">
                      {{ t('projectDetail.termExtraction.emptyResult') }}
                    </td>
                  </tr>
                  <tr
                    v-for="term in result.terms"
                    v-else
                    :key="`${result.model}-${term.index}-${term.source_normalized || term.source_text}`"
                    :class="{ 'has-conflict': term.has_conflict }"
                  >
                    <td>{{ term.source_text }}</td>
                    <td>{{ term.target_text }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="term-extract__compare-meta">
              <span>{{ t('projectDetail.termExtraction.termCount', { count: result.terms.length }) }}</span>
              <span v-if="countTermConflicts(result.terms) > 0">
                {{ t('projectDetail.termExtraction.conflictCount', { count: countTermConflicts(result.terms) }) }}
              </span>
            </div>
          </article>
        </div>
        <div class="term-extract__version-grid" :class="{ 'is-compact': compareResults.length === 2 }">
          <button
            v-for="version in versionOptions"
            :key="version.key"
            v-show="compareResults.length !== 2 || version.key === 'merged'"
            class="term-extract__version-card"
            :class="{ 'is-selected': selectedVersionKey === version.key }"
            type="button"
            :disabled="extracting || saving"
            @click="applyVersion(version.key)"
          >
            <GitMerge v-if="version.key === 'merged'" :size="16" />
            <Sparkles v-else :size="16" />
            <span>
              <strong>{{ version.label }}</strong>
              <small>
                {{ t('projectDetail.termExtraction.termCount', { count: version.terms.length }) }}
                <template v-if="version.provider"> · {{ version.provider }}</template>
              </small>
            </span>
          </button>
        </div>
      </section>

      <div class="term-extract__table-wrap">
        <table class="term-extract__table">
          <thead>
            <tr>
              <th>{{ t('projectDetail.termExtraction.columns.source') }}</th>
              <th>{{ t('projectDetail.termExtraction.columns.target') }}</th>
              <th>{{ t('projectDetail.termExtraction.columns.conflict') }}</th>
              <th>{{ t('projectDetail.termExtraction.columns.action') }}</th>
              <th>{{ t('table.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="extracting">
              <td colspan="5" class="term-extract__empty">
                <Loader2 class="lucide-spin" :size="18" />
                {{ t('projectDetail.termExtraction.extracting') }}
              </td>
            </tr>
            <tr v-else-if="drafts.length === 0">
              <td colspan="5" class="term-extract__empty">{{ t('projectDetail.termExtraction.empty') }}</td>
            </tr>
            <tr v-for="draft in drafts" v-else :key="draft.row_id" :class="{ 'has-conflict': draft.has_conflict }">
              <td>
                <input
                  v-model="draft.source_text"
                  class="field__control term-extract__input"
                  type="text"
                  @blur="refreshConflicts"
                />
              </td>
              <td>
                <input
                  v-model="draft.target_text"
                  class="field__control term-extract__input"
                  type="text"
                />
              </td>
              <td>
                <div v-if="draft.conflict" class="term-extract__conflict">
                  <strong>{{ draft.conflict.source_text }}</strong>
                  <span>{{ draft.conflict.target_text }}</span>
                </div>
                <span v-else class="term-extract__muted">{{ t('projectDetail.termExtraction.noConflict') }}</span>
              </td>
              <td>
                <select v-model="draft.action" class="field__control term-extract__action">
                  <option value="add">{{ t('projectDetail.termExtraction.actions.add') }}</option>
                  <option value="replace">{{ t('projectDetail.termExtraction.actions.replace') }}</option>
                  <option value="skip">{{ t('projectDetail.termExtraction.actions.skip') }}</option>
                </select>
              </td>
              <td>
                <button
                  class="button button--danger term-extract__icon"
                  type="button"
                  :title="t('common.actions.delete')"
                  @click="removeDraft(draft.row_id)"
                >
                  <Trash2 :size="14" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-if="selectedTermBase" class="hint-text">
        {{ t('projectDetail.termExtraction.saveHint', { name: selectedTermBase.name, count: saveableCount }) }}
      </p>
      <p v-if="saveResult" class="form-message">
        {{ t('projectDetail.termExtraction.saveResult', {
          created: saveResult.created_count,
          updated: saveResult.updated_count,
          skipped: saveResult.skipped_count,
          conflicts: saveResult.conflict_count,
        }) }}
      </p>
      <p v-if="errorMessage" class="form-message is-error">{{ errorMessage }}</p>
    </div>

    <template #footer>
      <div class="term-extract__footer">
        <button class="button" type="button" :disabled="extracting || saving" @click="emit('close')">
          {{ t('common.actions.cancel') }}
        </button>
        <button class="button" type="button" :disabled="!selectedTermBaseId" @click="openTermBase">
          <ExternalLink :size="14" />
          {{ t('projectDetail.termExtraction.openBase') }}
        </button>
        <button class="button button--primary" type="button" :disabled="!canSave" @click="saveTerms">
          <Loader2 v-if="saving" class="lucide-spin" :size="14" />
          <Save v-else :size="14" />
          {{ t('projectDetail.termExtraction.saveToBase') }}
        </button>
      </div>
    </template>
  </Modal>
</template>

<style scoped>
.term-extract {
  display: grid;
  gap: 14px;
}

.term-extract__summary,
.term-extract__tools,
.term-extract__footer {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.term-extract__summary .tag.is-warning {
  color: var(--state-warning);
  background: var(--state-warning-bg);
  border-color: color-mix(in srgb, var(--state-warning) 40%, var(--line-soft));
}

.term-extract__target {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) auto;
  gap: 10px;
  align-items: end;
}

.term-extract__config,
.term-extract__versions {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.term-extract__config-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.term-extract__model-grid,
.term-extract__version-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.term-extract__version-grid.is-compact {
  grid-template-columns: minmax(240px, 360px);
}

.term-extract__model-option,
.term-extract__version-card {
  display: flex;
  min-height: 62px;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
  color: var(--text-primary);
}

.term-extract__version-card {
  text-align: left;
  cursor: pointer;
}

.term-extract__model-option.is-selected,
.term-extract__version-card.is-selected {
  border-color: color-mix(in srgb, var(--brand-700) 58%, var(--line-soft));
  background: color-mix(in srgb, var(--brand-050) 72%, var(--surface-1));
}

.term-extract__model-option span,
.term-extract__version-card span {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.term-extract__model-option small,
.term-extract__version-card small {
  color: var(--text-muted);
  overflow-wrap: anywhere;
}

.term-extract__custom-model {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
}

.term-extract__selected-models {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.term-extract__model-chip {
  cursor: pointer;
}

.term-extract__prompt {
  min-height: 88px;
  resize: vertical;
}

.term-extract__compare {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.term-extract__compare-pane {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  min-height: 280px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
  overflow: hidden;
}

.term-extract__compare-pane.is-selected {
  border-color: color-mix(in srgb, var(--brand-700) 58%, var(--line-soft));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--brand-700) 34%, transparent);
}

.term-extract__compare-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px;
  border-bottom: 1px solid var(--line-soft);
  background: color-mix(in srgb, var(--surface-panel) 74%, var(--surface-1));
}

.term-extract__compare-head > div {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.term-extract__compare-head small {
  color: var(--text-muted);
  overflow-wrap: anywhere;
}

.term-extract__compare-table-wrap {
  max-height: 320px;
  overflow: auto;
}

.term-extract__compare-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 12px;
}

.term-extract__compare-table th,
.term-extract__compare-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--line-soft);
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}

.term-extract__compare-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  color: var(--text-secondary);
  background: var(--surface-1);
}

.term-extract__compare-table tr.has-conflict {
  background: color-mix(in srgb, var(--state-warning-bg) 52%, transparent);
}

.term-extract__compare-empty {
  height: 96px;
  color: var(--text-muted);
  text-align: center;
}

.term-extract__compare-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 10px;
  color: var(--text-muted);
  font-size: 12px;
  border-top: 1px solid var(--line-soft);
}

.term-extract__table-wrap {
  max-height: min(52vh, 560px);
  overflow: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
}

.term-extract__table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  background: var(--surface-panel);
}

.term-extract__table th,
.term-extract__table td {
  padding: 8px;
  border-bottom: 1px solid var(--line-soft);
  text-align: left;
  vertical-align: top;
}

.term-extract__table th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface-1);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.term-extract__table th:nth-child(1),
.term-extract__table th:nth-child(2) {
  width: 26%;
}

.term-extract__table th:nth-child(3) {
  width: 24%;
}

.term-extract__table th:nth-child(4) {
  width: 120px;
}

.term-extract__table th:nth-child(5) {
  width: 58px;
}

.term-extract__table tr.has-conflict {
  background: color-mix(in srgb, var(--state-warning-bg) 58%, transparent);
}

.term-extract__input,
.term-extract__action {
  width: 100%;
}

.term-extract__conflict {
  display: grid;
  gap: 3px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.35;
}

.term-extract__conflict strong,
.term-extract__conflict span {
  overflow-wrap: anywhere;
}

.term-extract__muted {
  color: var(--text-muted);
  font-size: 12px;
}

.term-extract__icon {
  min-width: 32px;
  min-height: 32px;
  padding: 0;
  justify-content: center;
}

.term-extract__empty {
  height: 120px;
  color: var(--text-muted);
  text-align: center;
}

.term-extract__empty > * {
  vertical-align: middle;
}

.term-extract__footer {
  justify-content: flex-end;
}

@media (max-width: 760px) {
  .term-extract__target {
    grid-template-columns: 1fr;
  }

  .term-extract__model-grid,
  .term-extract__version-grid,
  .term-extract__compare,
  .term-extract__custom-model {
    grid-template-columns: 1fr;
  }

  .term-extract__table {
    min-width: 820px;
  }
}
</style>
