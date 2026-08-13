<script setup lang="ts">
import axios from 'axios'
import { Bot, CheckCircle2, ChevronDown, Download, ExternalLink, FileSpreadsheet, FileText, Loader2, MessageSquareText, Pause, Play, Settings2, Upload } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  cancelProofreadingBatch,
  createProofreadingBatch,
  downloadProofreadingBatchExport,
  exportProofreadingBatch,
  generateProofreadingBatch,
  getProofreadingBatch,
  listProofreadingBatches,
  previewProofreadingWorkbook,
  type ProofreadingBatch,
  type ProofreadingPreview,
  type ProofreadingSheetMapping,
} from '../api/proofreading'
import { getLanguageLabel, languageOptions } from '../constants/languages'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'

const props = defineProps<{ projectId: string }>()
const emit = defineEmits<{ refreshProject: [] }>()
const router = useRouter()

interface TargetDraft {
  enabled: boolean
  language: string
}

interface SheetDraft {
  enabled: boolean
  headerRow: number
  sourceColumn: number
  targets: Record<number, TargetDraft>
}

type ProofreadingProvider = 'auto' | 'deepseek' | 'openrouter'

interface GenerationDraft {
  provider: ProofreadingProvider
  model: string
  userInstructions: string
}

const fileInput = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const preview = ref<ProofreadingPreview | null>(null)
const sourceLanguage = ref('en-US')
const sheetDrafts = reactive<Record<number, SheetDraft>>({})
const generationDrafts = reactive<Record<string, GenerationDraft>>({})
const batches = ref<ProofreadingBatch[]>([])
const busy = ref(false)
const actionBatchId = ref('')
const errorMessage = ref('')
const selectedImportMode = ref<'document_pair' | 'xlsx_columns' | null>(null)
const expandedBatchIds = ref(new Set<string>())
let pollTimer: ReturnType<typeof setInterval> | null = null

const ACTIVE_GENERATE_STATUSES = new Set(['aligning', 'queued', 'running', 'canceling'])
const CONFIGURABLE_STATUSES = new Set(['ready', 'partial_failed', 'failed', 'canceled'])

const hasRunningBatch = computed(() => batches.value.some((item) => (
  ACTIVE_GENERATE_STATUSES.has(item.status) || ['queued', 'running'].includes(item.export_status)
)))
const canCreate = computed(() => {
  if (!preview.value || !sourceLanguage.value) return false
  return buildMappings().some((mapping) => mapping.targets.length > 0)
})

function canConfigureBatch(batch: ProofreadingBatch) {
  return CONFIGURABLE_STATUSES.has(batch.status)
}

function isBatchExpanded(batchId: string) {
  return expandedBatchIds.value.has(batchId)
}

function toggleBatchSettings(batchId: string) {
  const next = new Set(expandedBatchIds.value)
  if (next.has(batchId)) next.delete(batchId)
  else next.add(batchId)
  expandedBatchIds.value = next
}

function isBatchGenerating(batch: ProofreadingBatch) {
  return ACTIVE_GENERATE_STATUSES.has(batch.status)
}

function startButtonLabel(batch: ProofreadingBatch) {
  if (batch.status === 'canceled') return '重新开始校对'
  if (batch.status === 'ready') return '开始 LLM 校对'
  return '重试失败项'
}

function generationDraft(batch: ProofreadingBatch) {
  if (!generationDrafts[batch.id]) {
    generationDrafts[batch.id] = {
      provider: batch.generation_settings?.provider || 'auto',
      model: batch.generation_settings?.model || '',
      userInstructions: batch.generation_settings?.user_instructions || '',
    }
  }
  return generationDrafts[batch.id]
}

function handleProviderChange(batch: ProofreadingBatch) {
  const draft = generationDraft(batch)
  draft.model = draft.provider === 'deepseek'
    ? 'deepseek-chat'
    : draft.provider === 'openrouter'
      ? 'google/gemini-3-flash-preview'
      : ''
}

function providerDescription(provider: ProofreadingProvider) {
  if (provider === 'deepseek') return '固定使用 DeepSeek Chat，不自动切换其他提供方。'
  if (provider === 'openrouter') return '通过 OpenRouter 使用指定模型。'
  return '自动模式优先 DeepSeek Chat；请求失败时回退到 OpenRouter。'
}

function batchActualModel(batch: ProofreadingBatch) {
  const settings = batch.generation_settings
  if (!settings?.actual_provider && !settings?.actual_model) return ''
  return `${settings.actual_provider || '未知提供方'} / ${settings.actual_model || '默认模型'}`
}

function errorText(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) return String(error.response?.data?.detail || fallback)
  return error instanceof Error ? error.message : fallback
}

function resetDrafts(result: ProofreadingPreview) {
  Object.keys(sheetDrafts).forEach((key) => delete sheetDrafts[Number(key)])
  for (const sheet of result.sheets) {
    const suggestedSource = sheet.columns.find((column) => column.suggested_role === 'source')?.index
      || sheet.columns[0]?.index
      || 1
    const targets: Record<number, TargetDraft> = {}
    for (const column of sheet.columns) {
      targets[column.index] = {
        enabled: column.index !== suggestedSource && Boolean(column.suggested_language),
        language: column.suggested_language || '',
      }
    }
    sheetDrafts[sheet.sheet_index] = {
      enabled: sheet.supported,
      headerRow: sheet.header_row,
      sourceColumn: suggestedSource,
      targets,
    }
  }
}

async function handleFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null
  selectedFile.value = file
  preview.value = null
  errorMessage.value = ''
  if (!file) return
  busy.value = true
  try {
    const result = await previewProofreadingWorkbook(props.projectId, file)
    preview.value = result
    resetDrafts(result)
  } catch (error) {
    errorMessage.value = errorText(error, 'Excel 预览失败。')
  } finally {
    busy.value = false
  }
}

function buildMappings(): ProofreadingSheetMapping[] {
  if (!preview.value) return []
  return preview.value.sheets.flatMap((sheet) => {
    const draft = sheetDrafts[sheet.sheet_index]
    if (!draft?.enabled || !sheet.supported) return []
    const targets = Object.entries(draft.targets)
      .filter(([column, item]) => item.enabled && item.language && Number(column) !== draft.sourceColumn)
      .map(([column, item]) => ({ target_column: Number(column), target_language: item.language }))
    return targets.length
      ? [{ sheet_index: sheet.sheet_index, header_row: draft.headerRow, source_column: draft.sourceColumn, targets }]
      : []
  })
}

function mappedHeader(sheet: ProofreadingPreview['sheets'][number], columnIndex: number) {
  const row = sheet.header_candidates.find((item) => item.row_index === sheetDrafts[sheet.sheet_index].headerRow)
  return row?.values[columnIndex - 1] || sheet.columns.find((item) => item.index === columnIndex)?.header || '无表头'
}

async function submitMapping() {
  if (!preview.value || !canCreate.value) return
  const mappings = buildMappings()
  for (const mapping of mappings) {
    const languages = mapping.targets.map((item) => item.target_language)
    if (new Set(languages).size !== languages.length) {
      errorMessage.value = '同一工作表中，每个目标语言只能绑定一个译文列。'
      return
    }
  }
  busy.value = true
  errorMessage.value = ''
  try {
    const batch = await createProofreadingBatch(props.projectId, {
      preview_token: preview.value.preview_token,
      source_language: sourceLanguage.value,
      mappings,
    })
    batches.value = [batch, ...batches.value.filter((item) => item.id !== batch.id)]
    preview.value = null
    selectedFile.value = null
    if (fileInput.value) fileInput.value.value = ''
    emit('refreshProject')
  } catch (error) {
    errorMessage.value = errorText(error, '创建校对批次失败。')
  } finally {
    busy.value = false
  }
}

async function refreshBatches() {
  try {
    batches.value = await listProofreadingBatches(props.projectId)
    batches.value.forEach((batch) => generationDraft(batch))
  } catch (error) {
    errorMessage.value = errorText(error, '读取校对批次失败。')
  }
}

async function startGeneration(batch: ProofreadingBatch) {
  actionBatchId.value = batch.id
  errorMessage.value = ''
  try {
    const draft = generationDraft(batch)
    await generateProofreadingBatch(batch.id, {
      provider: draft.provider,
      model: draft.model || undefined,
      user_instructions: draft.userInstructions.trim(),
    })
    const latest = await getProofreadingBatch(batch.id)
    batches.value = batches.value.map((item) => item.id === latest.id ? latest : item)
    startPolling()
  } catch (error) {
    errorMessage.value = errorText(error, '启动 LLM 校对失败。')
  } finally {
    actionBatchId.value = ''
  }
}

async function stopGeneration(batch: ProofreadingBatch) {
  actionBatchId.value = batch.id
  errorMessage.value = ''
  try {
    const latest = await cancelProofreadingBatch(batch.id)
    batches.value = batches.value.map((item) => item.id === latest.id ? latest : item)
    startPolling()
  } catch (error) {
    errorMessage.value = errorText(error, '取消校对失败。')
  } finally {
    actionBatchId.value = ''
  }
}

async function downloadBatch(batch: ProofreadingBatch) {
  actionBatchId.value = batch.id
  errorMessage.value = ''
  try {
    if (batch.export_status !== 'completed') {
      await exportProofreadingBatch(batch.id)
      await refreshBatches()
      startPolling()
      return
    }
    const response = await downloadProofreadingBatchExport(batch.id)
    const fallback = `${batch.filename.replace(/\.xlsx$/i, '')}_校对版.xlsx`
    downloadBlob(response.data, resolveDownloadFilename(response.headers['content-disposition'], fallback))
  } catch (error) {
    errorMessage.value = errorText(error, '导出校对版 Excel 失败。')
  } finally {
    actionBatchId.value = ''
  }
}

function openWorkbench(fileRecordId: string) {
  const resolved = router.resolve({
    name: 'workbench-focus',
    params: { id: fileRecordId },
    query: { from: 'project', pid: props.projectId },
  })
  window.open(resolved.href, '_blank', 'noopener,noreferrer')
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(async () => {
    await refreshBatches()
    if (!hasRunningBatch.value && pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
      emit('refreshProject')
    }
  }, 2000)
}

function statusLabel(status: ProofreadingBatch['status']) {
  return ({
    aligning: '对齐中',
    draft: '待确认对齐',
    ready: '待生成',
    queued: '排队中',
    running: '校对中',
    canceling: '取消中',
    completed: '已完成',
    partial_failed: '部分失败',
    failed: '失败',
    canceled: '已取消',
  } as Record<string, string>)[status] || status
}

function statusTone(status: ProofreadingBatch['status']) {
  if (status === 'completed') return 'is-success'
  if (status === 'partial_failed' || status === 'failed') return 'is-danger'
  if (status === 'canceled' || status === 'canceling') return 'is-muted'
  if (ACTIVE_GENERATE_STATUSES.has(status)) return 'is-running'
  return 'is-ready'
}

function batchKindLabel(batch: ProofreadingBatch) {
  return batch.batch_kind === 'document_pair' ? '双文档' : '表格列'
}

function selectImportMode(mode: 'document_pair' | 'xlsx_columns') {
  if (mode === 'document_pair') {
    void router.push({ name: 'document-alignment', params: { id: props.projectId } })
    return
  }
  selectedImportMode.value = selectedImportMode.value === mode ? null : mode
  errorMessage.value = ''
}

function openDocumentPairBatch(batch: ProofreadingBatch) {
  const fileRecordId = batch.bindings[0]?.file_record_id
  if (batch.alignment_status === 'confirmed' && fileRecordId) {
    void router.push({
      name: 'workbench-focus',
      params: { id: fileRecordId },
      query: { from: 'project', pid: props.projectId, mode: 'alignment' },
    })
    return
  }
  void router.push({ name: 'document-alignment', params: { id: props.projectId } })
}

onMounted(async () => {
  await refreshBatches()
  if (hasRunningBatch.value) startPolling()
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <section class="proofreading-panel">
    <div class="proofreading-import">
      <div class="proofreading-import__head">
        <div class="proofreading-import__intro">
          <span class="proofreading-import__eyebrow"><Upload :size="14" />新增资料</span>
          <div class="section-title section-title--tight">导入校对资料</div>
          <p class="panel-subtitle">选择资料结构，导入后统一进入语言任务。</p>
        </div>
      </div>

      <div class="proofreading-import__modes" role="group" aria-label="校对资料导入模式">
        <button
          class="proofreading-import-mode"
          :class="{ 'is-active': selectedImportMode === 'document_pair' }"
          type="button"
          data-testid="proofreading-import-document-pair"
          @click="selectImportMode('document_pair')"
        >
          <span class="proofreading-import-mode__icon"><FileText :size="22" /></span>
          <span class="proofreading-import-mode__copy">
            <strong>原文 + 译文两个文档</strong>
            <small>分别上传两个文档，自动解析并对齐句段。</small>
            <em>支持 DOC、DOCX、TXT</em>
          </span>
          <ExternalLink class="proofreading-import-mode__arrow" :size="16" />
        </button>
        <button
          class="proofreading-import-mode"
          :class="{ 'is-active': selectedImportMode === 'xlsx_columns' }"
          type="button"
          data-testid="proofreading-import-spreadsheet"
          @click="selectImportMode('xlsx_columns')"
        >
          <span class="proofreading-import-mode__icon"><FileSpreadsheet :size="22" /></span>
          <span class="proofreading-import-mode__copy">
            <strong>原文列 + 译文列的表格</strong>
            <small>上传工作簿，映射原文列、译文列与语言。</small>
            <em>支持 XLSX</em>
          </span>
          <ChevronDown class="proofreading-import-mode__arrow" :size="16" />
        </button>
      </div>
    </div>

    <div v-if="selectedImportMode === 'xlsx_columns'" class="proofreading-section proofreading-import-workspace">
      <div class="proofreading-panel__head">
        <div>
          <div class="section-title section-title--tight">表格列映射</div>
          <p class="panel-subtitle">选择工作簿后确认原文列、译文列及目标语言。</p>
        </div>
        <label class="button button--primary">
          <Upload :size="14" />
          选择 .xlsx
          <input ref="fileInput" class="sr-only" type="file" accept=".xlsx" :disabled="busy" @change="handleFileChange">
        </label>
      </div>

      <p v-if="busy && !preview" class="proofreading-panel__notice"><Loader2 class="lucide-spin" :size="16" />正在读取工作簿…</p>
      <p v-if="errorMessage" class="form-message is-error">{{ errorMessage }}</p>

      <div v-if="preview" class="proofreading-mapping">
        <div class="proofreading-mapping__top">
          <span><FileSpreadsheet :size="16" />{{ preview.filename }}</span>
          <label class="field proofreading-source-language">
            <span class="field__label">原文语言</span>
            <select v-model="sourceLanguage" class="field__control">
              <option v-for="language in languageOptions" :key="language.code" :value="language.code">{{ language.label }}</option>
            </select>
          </label>
        </div>

        <article v-for="sheet in preview.sheets" :key="sheet.sheet_index" class="proofreading-sheet" :class="{ 'is-blocked': !sheet.supported }">
          <div class="proofreading-sheet__head">
            <label><input v-model="sheetDrafts[sheet.sheet_index].enabled" type="checkbox" :disabled="!sheet.supported"> {{ sheet.name }}</label>
            <span>{{ sheet.max_row }} 行 × {{ sheet.max_column }} 列</span>
          </div>
          <p v-if="!sheet.supported" class="form-message is-error">暂不能安全处理：{{ sheet.blocked_reasons.join('；') }}</p>
          <div v-else-if="sheetDrafts[sheet.sheet_index].enabled" class="proofreading-sheet__settings">
            <label class="field">
              <span class="field__label">表头行</span>
              <input v-model.number="sheetDrafts[sheet.sheet_index].headerRow" class="field__control" type="number" min="1" :max="sheet.max_row">
            </label>
            <label class="field">
              <span class="field__label">原文列</span>
              <select v-model.number="sheetDrafts[sheet.sheet_index].sourceColumn" class="field__control">
                <option v-for="column in sheet.columns" :key="column.index" :value="column.index">{{ column.letter }} · {{ mappedHeader(sheet, column.index) }}</option>
              </select>
            </label>
            <div class="proofreading-targets">
              <div class="field__label">译文列与目标语言</div>
              <label v-for="column in sheet.columns.filter((item) => item.index !== sheetDrafts[sheet.sheet_index].sourceColumn)" :key="column.index" class="proofreading-target-row">
                <input v-model="sheetDrafts[sheet.sheet_index].targets[column.index].enabled" type="checkbox">
                <span class="proofreading-target-row__name">{{ column.letter }} · {{ mappedHeader(sheet, column.index) }}</span>
                <select v-model="sheetDrafts[sheet.sheet_index].targets[column.index].language" class="field__control" :disabled="!sheetDrafts[sheet.sheet_index].targets[column.index].enabled">
                  <option value="" disabled>选择目标语言</option>
                  <option v-for="language in languageOptions" :key="language.code" :value="language.code" :disabled="language.code === sourceLanguage">{{ language.label }}</option>
                </select>
                <small>{{ column.samples.filter(Boolean).slice(0, 2).join(' / ') || '空列' }}</small>
              </label>
            </div>
          </div>
        </article>

        <div class="proofreading-mapping__actions">
          <span>空原文将跳过；空译文只统计，不自动补译。</span>
          <button class="button button--primary" type="button" :disabled="busy || !canCreate" @click="submitMapping">
            <Loader2 v-if="busy" class="lucide-spin" :size="14" /><CheckCircle2 v-else :size="14" />创建校对批次
          </button>
        </div>
      </div>
    </div>

    <div class="proofreading-task-section">
      <div class="proofreading-task-section__head">
        <div>
          <div class="section-title section-title--tight">导入批次</div>
          <p class="panel-subtitle">这里展示资料处理和生成进度；生成后的文件统一显示在下方语言任务表格。</p>
        </div>
        <span class="proofreading-task-section__count">{{ batches.length }} 个批次</span>
      </div>

      <div v-if="batches.length" class="proofreading-batches">
        <article v-for="batch in batches" :key="batch.id" class="proofreading-batch">
          <div class="proofreading-batch__header">
            <div class="proofreading-batch__title">
              <strong>{{ batch.filename }}</strong>
              <span class="proofreading-batch__kind" :class="`is-${batch.batch_kind || 'xlsx_columns'}`">{{ batchKindLabel(batch) }}</span>
              <span class="proofreading-batch__badge" :class="statusTone(batch.status)">{{ statusLabel(batch.status) }}</span>
            </div>
            <small class="proofreading-batch__stats">
              <span>共 {{ batch.total_segments }} 条</span>
              <span>修改 {{ batch.changed_segments }}</span>
              <span>缺失 {{ batch.skipped_segments }}</span>
              <span :class="{ 'is-error': batch.failed_segments > 0 }">失败 {{ batch.failed_segments }}</span>
            </small>
            <span class="proofreading-batch__percent">{{ batch.progress }}%</span>
          </div>

          <div class="proofreading-batch__progress">
            <div class="progress-bar"><div class="progress-bar__track"><div class="progress-bar__fill" :style="{ width: `${batch.progress}%` }" /></div></div>
          </div>

          <div class="proofreading-batch__footer">
            <div class="proofreading-batch__context">
              <p v-if="batch.message" class="proofreading-batch__message" :title="batch.message">{{ batch.message }}</p>
              <div v-if="batch.bindings.length" class="proofreading-batch__language-list" aria-label="语言任务">
                <button
                  v-for="binding in batch.bindings"
                  :key="binding.id"
                  class="proofreading-batch__language-chip"
                  type="button"
                  @click="openWorkbench(binding.file_record_id)"
                >
                  <span>{{ getLanguageLabel(binding.target_language) }}</span>
                  <span class="proofreading-batch__language-sheet">{{ binding.sheet_name }}</span>
                  <ExternalLink :size="12" />
                </button>
              </div>
            </div>

            <div class="proofreading-batch__actions">
              <button
                v-if="canConfigureBatch(batch)"
                class="button proofreading-batch__settings-toggle"
                type="button"
                :aria-expanded="isBatchExpanded(batch.id)"
                @click="toggleBatchSettings(batch.id)"
              >
                <Settings2 :size="14" />
                校对设置
                <ChevronDown :size="14" :class="{ 'is-rotated': isBatchExpanded(batch.id) }" />
              </button>
              <button
                v-if="batch.batch_kind === 'document_pair' && ['aligning', 'canceling', 'draft', 'confirmed'].includes(batch.alignment_status || '')"
                class="button button--primary"
                type="button"
                @click="openDocumentPairBatch(batch)"
              >
                <FileText :size="14" />
                {{ ['draft', 'confirmed'].includes(batch.alignment_status || '') ? '进入对齐工作台' : '查看对齐进度' }}
              </button>
              <button
                v-if="canConfigureBatch(batch)"
                class="button button--primary"
                type="button"
                :disabled="Boolean(actionBatchId)"
                @click="startGeneration(batch)"
              >
                <Loader2 v-if="actionBatchId === batch.id" class="lucide-spin" :size="14" />
                <Play v-else :size="14" />
                {{ startButtonLabel(batch) }}
              </button>
              <button
                v-if="isBatchGenerating(batch)"
                class="button button--danger"
                type="button"
                :disabled="Boolean(actionBatchId) || batch.status === 'canceling'"
                @click="stopGeneration(batch)"
              >
                <Loader2 v-if="actionBatchId === batch.id || batch.status === 'canceling'" class="lucide-spin" :size="14" />
                <Pause v-else :size="14" />
                {{ batch.status === 'canceling' ? '取消中…' : '暂停' }}
              </button>
              <button
                class="button"
                type="button"
                :disabled="Boolean(actionBatchId) || batch.status === 'ready' || isBatchGenerating(batch) || ['queued', 'running'].includes(batch.export_status)"
                @click="downloadBatch(batch)"
              >
                <Loader2 v-if="['queued', 'running'].includes(batch.export_status)" class="lucide-spin" :size="14" />
                <Download v-else :size="14" />
                {{ batch.export_status === 'completed' ? '下载合并 Excel' : (['queued', 'running'].includes(batch.export_status) ? `生成中 ${batch.export_progress}%` : '生成合并 Excel') }}
              </button>
            </div>
          </div>

          <small v-if="batch.error_message" class="is-error proofreading-batch__error">{{ batch.error_message }}</small>
          <small v-if="batch.export_error_message" class="is-error proofreading-batch__error">导出失败：{{ batch.export_error_message }}</small>

          <section
            v-if="canConfigureBatch(batch) && isBatchExpanded(batch.id)"
            class="proofreading-generation-config"
            aria-label="LLM 校对设置"
          >
            <div class="proofreading-generation-config__head">
              <strong><Bot :size="16" />LLM 校对设置</strong>
              <small v-if="batchActualModel(batch)">上次实际使用：{{ batchActualModel(batch) }}</small>
            </div>
            <div class="proofreading-generation-config__fields">
              <label class="field">
                <span class="field__label">模型提供方</span>
                <select
                  v-model="generationDraft(batch).provider"
                  class="field__control"
                  @change="handleProviderChange(batch)"
                >
                  <option value="auto">自动（优先 DeepSeek，失败回退）</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="openrouter">OpenRouter</option>
                </select>
              </label>
              <label class="field">
                <span class="field__label">校对模型</span>
                <select
                  v-model="generationDraft(batch).model"
                  class="field__control"
                  :disabled="generationDraft(batch).provider === 'auto'"
                >
                  <option v-if="generationDraft(batch).provider === 'auto'" value="">自动选择默认模型</option>
                  <option v-if="generationDraft(batch).provider === 'deepseek'" value="deepseek-chat">DeepSeek Chat</option>
                  <option v-if="generationDraft(batch).provider === 'openrouter'" value="google/gemini-3-flash-preview">Gemini 3 Flash Preview</option>
                </select>
              </label>
            </div>
            <p class="proofreading-generation-config__hint">
              {{ providerDescription(generationDraft(batch).provider) }}
            </p>
            <label class="field proofreading-generation-config__prompt">
              <span class="field__label"><MessageSquareText :size="14" />本批次校对提示词</span>
              <textarea
                v-model="generationDraft(batch).userInstructions"
                class="field__control"
                rows="4"
                maxlength="12000"
                placeholder="例如：统一车载诊断术语；保持缩写不变；语气简洁；不要改写法规编号。该内容会追加到系统和项目校对规则中。"
              />
              <small>直接调用 LLM 校对，不使用 TM / 词汇表。留空时使用系统默认规则和项目翻译规则。</small>
            </label>
          </section>

        </article>
      </div>
      <div v-else class="proofreading-task-empty">
        <FileText :size="24" />
        <strong>还没有校对语言任务</strong>
        <span>请从上方选择一种导入方式开始。</span>
      </div>
    </div>

  </section>
</template>

<style scoped>
.proofreading-panel { display: grid; gap: 18px; }
.proofreading-section { display: grid; gap: 14px; }
.proofreading-import {
  display: grid;
  grid-template-columns: minmax(220px, .55fr) minmax(520px, 1.45fr);
  gap: 20px;
  align-items: center;
  padding: 16px 18px;
  border: 1px solid rgba(15, 118, 110, .16);
  border-radius: 12px;
  background: linear-gradient(105deg, rgba(240, 253, 250, .8), rgba(248, 250, 252, .72));
}
.proofreading-import__head, .proofreading-task-section__head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.proofreading-import__intro { display: grid; gap: 4px; }
.proofreading-import__eyebrow { display: inline-flex; align-items: center; gap: 5px; color: var(--brand); font-size: 11px; font-weight: 700; letter-spacing: .05em; }
.proofreading-import__intro .panel-subtitle { margin-top: 1px; }
.proofreading-import__modes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.proofreading-import-mode {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 12px;
  background: #fff;
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color .16s ease, box-shadow .16s ease, background .16s ease;
}
.proofreading-import-mode:hover { border-color: rgba(15, 118, 110, .42); box-shadow: 0 6px 18px rgba(15, 23, 42, .06); }
.proofreading-import-mode.is-active { border-color: var(--brand); background: rgba(240, 253, 250, .8); box-shadow: 0 0 0 2px rgba(15, 118, 110, .1); }
.proofreading-import-mode__icon { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 9px; background: #eef8f5; color: var(--brand); }
.proofreading-import-mode__copy { display: grid; gap: 3px; min-width: 0; }
.proofreading-import-mode__copy strong { font-size: 15px; }
.proofreading-import-mode__copy small { overflow: hidden; color: var(--ink-500); line-height: 1.4; text-overflow: ellipsis; white-space: nowrap; }
.proofreading-import-mode__copy em { color: var(--brand); font-size: 11px; font-style: normal; font-weight: 700; }
.proofreading-import-mode__arrow { color: var(--ink-400, #94a3b8); transform: rotate(-90deg); transition: transform .16s ease; }
.proofreading-import-mode.is-active .proofreading-import-mode__arrow { transform: rotate(0); }
.proofreading-import-workspace {
  padding: 14px;
  border: 1px solid rgba(15, 118, 110, .2);
  border-radius: 12px;
  background: #f8fbfa;
}
.proofreading-task-section { display: grid; gap: 8px; padding-top: 12px; border-top: 1px solid var(--line-soft); }
.proofreading-task-section__head > div { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
.proofreading-task-section__head .panel-subtitle { overflow: hidden; margin: 0; text-overflow: ellipsis; white-space: nowrap; }
.proofreading-task-section__count { flex: 0 0 auto; color: var(--ink-500); font-size: 12px; }
.proofreading-task-empty { display: grid; place-items: center; gap: 6px; padding: 28px; border: 1px dashed var(--line); border-radius: 10px; color: var(--ink-500); text-align: center; }
.proofreading-task-empty strong { color: var(--ink-700); }
.proofreading-task-empty span { font-size: 12px; }
.proofreading-batch__kind { display: inline-flex; padding: 2px 7px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 11px; font-weight: 700; }
.proofreading-batch__kind.is-document_pair { background: #fff7ed; color: #c2410c; }
.proofreading-batch__kind.is-xlsx_columns { background: #ecfdf5; color: #047857; }
.proofreading-panel__head, .proofreading-mapping__top, .proofreading-mapping__actions, .proofreading-sheet__head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.proofreading-panel__head input { display: none; }
.proofreading-panel__notice { display: flex; align-items: center; gap: 8px; color: var(--ink-600); }
.proofreading-mapping, .proofreading-batches { display: grid; gap: 8px; }
.proofreading-mapping { padding: 16px; border: 1px solid var(--line-soft); border-radius: 10px; background: #fafcfb; }
.proofreading-mapping__top > span { display: flex; align-items: center; gap: 8px; font-weight: 700; }
.proofreading-source-language { width: min(280px, 45%); }
.proofreading-sheet { padding: 14px; border: 1px solid var(--line-soft); border-radius: 8px; background: #fff; }
.proofreading-sheet.is-blocked { background: #fff8f6; }
.proofreading-sheet__head span { color: var(--ink-500); font-size: 12px; }
.proofreading-sheet__settings { display: grid; grid-template-columns: 150px minmax(220px, 0.7fr) minmax(420px, 2fr); gap: 14px; margin-top: 14px; align-items: start; }
.proofreading-targets { display: grid; gap: 8px; }
.proofreading-target-row { display: grid; grid-template-columns: 18px minmax(130px, 1fr) minmax(180px, 0.8fr); gap: 8px; align-items: center; }
.proofreading-target-row small { grid-column: 2 / 4; overflow: hidden; color: var(--ink-500); text-overflow: ellipsis; white-space: nowrap; }
.proofreading-mapping__actions > span { color: var(--ink-500); font-size: 12px; }
.proofreading-batch {
  display: grid;
  gap: 7px;
  padding: 10px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: #fff;
}
.proofreading-batch__header {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) auto auto;
  align-items: center;
  gap: 12px;
}
.proofreading-batch__title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}
.proofreading-batch__title strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.proofreading-batch__badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  background: #eef2f7;
  color: #475569;
}
.proofreading-batch__badge.is-ready { background: #eef2f7; color: #475569; }
.proofreading-batch__badge.is-running { background: #e0f2fe; color: #0369a1; }
.proofreading-batch__badge.is-success { background: #dcfce7; color: #15803d; }
.proofreading-batch__badge.is-danger { background: #fee2e2; color: #b91c1c; }
.proofreading-batch__badge.is-muted { background: #f1f5f9; color: #64748b; }
.proofreading-batch__percent { color: var(--ink-500); font-size: 13px; font-variant-numeric: tabular-nums; }
.proofreading-batch__progress { display: grid; }
.proofreading-batch__progress .progress-bar__track { height: 6px; }
.proofreading-batch__progress small { color: var(--ink-500); }
.proofreading-batch__message { overflow: hidden; margin: 0; color: var(--ink-600); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.proofreading-batch__stats { display: inline-flex; flex: 0 0 auto; gap: 8px; color: var(--ink-500); font-variant-numeric: tabular-nums; white-space: nowrap; }
.proofreading-batch__stats span + span { padding-left: 10px; border-left: 1px solid var(--line-soft); }
.proofreading-batch__footer { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.proofreading-batch__context { display: flex; flex: 1 1 auto; align-items: center; gap: 12px; min-width: 0; }
.proofreading-batch__language-list { display: flex; flex-wrap: wrap; gap: 8px; }
.proofreading-batch__language-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: #f8fafc;
  color: var(--ink-700, #334155);
  font-size: 13px;
  cursor: pointer;
}
.proofreading-batch__language-chip:hover { border-color: #94a3b8; background: #fff; }
.proofreading-batch__language-sheet { color: var(--ink-500); }
.proofreading-batch__actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.proofreading-batch__actions .button { min-height: 32px; padding: 6px 10px; }
.proofreading-batch__settings-toggle svg:last-child { transition: transform .16s ease; }
.proofreading-batch__settings-toggle .is-rotated { transform: rotate(180deg); }
.proofreading-batch__error { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.proofreading-generation-config {
  display: grid;
  gap: 10px;
  padding: 14px;
  border: 1px solid rgba(15, 118, 110, 0.18);
  border-radius: 9px;
  background: linear-gradient(135deg, rgba(240, 253, 250, 0.95), rgba(248, 250, 252, 0.96));
}
.proofreading-generation-config__head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.proofreading-generation-config__head strong, .proofreading-generation-config__prompt .field__label { display: inline-flex; align-items: center; gap: 6px; }
.proofreading-generation-config__head small { color: #475569; }
.proofreading-generation-config__fields { display: grid; grid-template-columns: repeat(2, minmax(220px, 1fr)); gap: 12px; }
.proofreading-generation-config__hint { margin: -3px 0 0; color: #475569; font-size: 12px; }
.proofreading-generation-config__prompt textarea { min-height: 96px; resize: vertical; line-height: 1.5; }
.proofreading-generation-config__prompt small { color: #64748b; }
.is-error { color: var(--danger-600, #b42318) !important; }
@media (max-width: 980px) {
  .proofreading-import { grid-template-columns: 1fr; gap: 12px; }
  .proofreading-import__modes { grid-template-columns: 1fr; }
  .proofreading-sheet__settings { grid-template-columns: 1fr; }
  .proofreading-generation-config__fields { grid-template-columns: 1fr; }
  .proofreading-batch__header { grid-template-columns: minmax(220px, 1fr) auto; }
  .proofreading-batch__stats { grid-column: 1 / -1; grid-row: 2; }
  .proofreading-batch__percent { grid-column: 2; grid-row: 1; }
  .proofreading-batch__footer { align-items: flex-start; flex-direction: column; }
  .proofreading-batch__actions { justify-content: flex-start; }
}
@media (max-width: 640px) {
  .proofreading-import { padding: 14px; }
  .proofreading-batch__stats { flex-wrap: wrap; gap: 5px 8px; }
  .proofreading-batch__stats span + span { padding-left: 8px; }
  .proofreading-batch__context { align-items: flex-start; flex-direction: column; }
  .proofreading-batch__actions { width: 100%; }
}
</style>
