<script setup lang="ts">
import axios from 'axios'
import { Bot, CheckCircle2, Download, ExternalLink, FileSpreadsheet, Loader2, MessageSquareText, Play, Upload } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
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
import DocumentAlignmentEditor from './DocumentAlignmentEditor.vue'

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
let pollTimer: ReturnType<typeof setInterval> | null = null

const hasRunningBatch = computed(() => batches.value.some((item) => (
  ['aligning', 'queued', 'running'].includes(item.status) || ['queued', 'running'].includes(item.export_status)
)))
const canCreate = computed(() => {
  if (!preview.value || !sourceLanguage.value) return false
  return buildMappings().some((mapping) => mapping.targets.length > 0)
})

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
    aligning: '对齐中', draft: '待确认对齐', ready: '待生成', queued: '排队中', running: '校对中', completed: '已完成',
    partial_failed: '部分失败', failed: '失败', canceled: '已取消',
  } as Record<string, string>)[status] || status
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
    <DocumentAlignmentEditor :project-id="projectId" @refresh="refreshBatches" @open-workbench="openWorkbench" />
    <div class="proofreading-panel__head">
      <div>
        <div class="section-title section-title--tight">多语种 Excel 校对</div>
        <p class="panel-subtitle">上传原文与多语种译文表，映射列后按语言生成统一校对版。</p>
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

    <div v-if="batches.length" class="proofreading-batches">
      <article v-for="batch in batches" :key="batch.id" class="proofreading-batch">
        <div class="proofreading-batch__main">
          <strong>{{ batch.filename }}</strong>
          <span>{{ statusLabel(batch.status) }} · {{ batch.progress }}%</span>
          <div class="progress-bar"><div class="progress-bar__track"><div class="progress-bar__fill" :style="{ width: `${batch.progress}%` }" /></div></div>
          <small>共 {{ batch.total_segments }} 条，修改 {{ batch.changed_segments }}，缺失 {{ batch.skipped_segments }}，失败 {{ batch.failed_segments }}</small>
          <small v-if="batch.error_message" class="is-error">{{ batch.error_message }}</small>
          <small v-if="batch.export_error_message" class="is-error">导出失败：{{ batch.export_error_message }}</small>
        </div>
        <div class="proofreading-batch__languages">
          <button v-for="binding in batch.bindings" :key="binding.id" class="button" type="button" @click="openWorkbench(binding.file_record_id)">
            {{ getLanguageLabel(binding.target_language) }} · {{ binding.sheet_name }} <ExternalLink :size="12" />
          </button>
        </div>
        <section
          v-if="['ready', 'partial_failed', 'failed'].includes(batch.status)"
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
              rows="3"
              maxlength="12000"
              placeholder="例如：统一车载诊断术语；保持缩写不变；语气简洁；不要改写法规编号。该内容会追加到系统和项目校对规则中。"
            />
            <small>明确写出本批次特有要求；留空时使用系统默认规则和项目翻译规则。</small>
          </label>
        </section>
        <div class="proofreading-batch__actions">
          <button v-if="['ready', 'partial_failed', 'failed'].includes(batch.status)" class="button button--primary" type="button" :disabled="Boolean(actionBatchId)" @click="startGeneration(batch)">
            <Loader2 v-if="actionBatchId === batch.id" class="lucide-spin" :size="14" /><Play v-else :size="14" />{{ batch.status === 'ready' ? '开始 LLM 校对' : '重试失败项' }}
          </button>
          <button class="button" type="button" :disabled="Boolean(actionBatchId) || batch.status === 'ready' || ['queued', 'running'].includes(batch.status) || ['queued', 'running'].includes(batch.export_status)" @click="downloadBatch(batch)">
            <Loader2 v-if="['queued', 'running'].includes(batch.export_status)" class="lucide-spin" :size="14" />
            <Download v-else :size="14" />
            {{ batch.export_status === 'completed' ? '下载合并 Excel' : (['queued', 'running'].includes(batch.export_status) ? `生成中 ${batch.export_progress}%` : '生成合并 Excel') }}
          </button>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.proofreading-panel { display: grid; gap: 16px; }
.proofreading-panel__head, .proofreading-mapping__top, .proofreading-mapping__actions, .proofreading-sheet__head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.proofreading-panel__head input { display: none; }
.proofreading-panel__notice { display: flex; align-items: center; gap: 8px; color: var(--ink-600); }
.proofreading-mapping, .proofreading-batches { display: grid; gap: 12px; }
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
.proofreading-batch { display: grid; grid-template-columns: minmax(240px, 0.8fr) minmax(260px, 1.2fr) auto; align-items: start; gap: 14px 18px; padding: 16px; border: 1px solid var(--line-soft); border-radius: 10px; background: #fff; }
.proofreading-batch__main { display: grid; min-width: 250px; gap: 5px; }
.proofreading-batch__main > span, .proofreading-batch__main small { color: var(--ink-500); }
.proofreading-batch__languages { display: flex; flex: 1; flex-wrap: wrap; gap: 6px; }
.proofreading-batch__actions { display: flex; gap: 8px; }
.proofreading-generation-config { grid-column: 1 / -1; display: grid; gap: 10px; padding: 14px; border: 1px solid rgba(37, 99, 235, 0.2); border-radius: 9px; background: linear-gradient(135deg, rgba(239, 246, 255, 0.95), rgba(248, 250, 252, 0.96)); }
.proofreading-generation-config__head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.proofreading-generation-config__head strong, .proofreading-generation-config__prompt .field__label { display: inline-flex; align-items: center; gap: 6px; }
.proofreading-generation-config__head small { color: #475569; }
.proofreading-generation-config__fields { display: grid; grid-template-columns: repeat(2, minmax(220px, 1fr)); gap: 12px; }
.proofreading-generation-config__hint { margin: -3px 0 0; color: #475569; font-size: 12px; }
.proofreading-generation-config__prompt textarea { min-height: 82px; resize: vertical; line-height: 1.5; }
.proofreading-generation-config__prompt small { color: #64748b; }
.is-error { color: var(--danger-600, #b42318) !important; }
@media (max-width: 980px) {
  .proofreading-sheet__settings { grid-template-columns: 1fr; }
  .proofreading-batch { grid-template-columns: 1fr; }
  .proofreading-generation-config { grid-column: 1; }
  .proofreading-generation-config__fields { grid-template-columns: 1fr; }
}
</style>
