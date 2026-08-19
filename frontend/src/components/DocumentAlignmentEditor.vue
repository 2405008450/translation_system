<script setup lang="ts">
import axios from 'axios'
import { ArrowRight, Check, Download, FileText, RefreshCw, Square, Upload } from 'lucide-vue-next'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  cancelAlignment,
  confirmAlignment,
  createDocumentAlignmentBatch,
  downloadAlignmentCsv,
  previewDocumentAlignment,
  rerunAlignment,
  type AlignmentPreview,
} from '../api/documentAlignment'
import { getProofreadingBatch, listProofreadingBatches } from '../api/proofreading'
import { languageOptions } from '../constants/languages'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'

const props = defineProps<{ projectId: string; compact?: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const router = useRouter()

const sourceFile = ref<File | null>(null)
const targetFile = ref<File | null>(null)
const preview = ref<AlignmentPreview | null>(null)
const sourceLanguage = ref('zh-CN')
const targetLanguage = ref('en-US')
const granularity = ref<'sentence' | 'paragraph'>('sentence')
const fullReview = ref(true)
const alignmentStrategy = ref<'hierarchical_llm' | 'order_first' | 'structure_aware'>('order_first')

const batchId = ref('')
const alignmentReady = ref(false)
const busy = ref(false)
const alignmentProgress = ref(0)
const message = ref('')
const error = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

function stopPolling() {
  if (!pollTimer) return
  clearInterval(pollTimer)
  pollTimer = null
}

function errorText(value: unknown) {
  return axios.isAxiosError(value)
    ? String(value.response?.data?.detail || '操作失败。')
    : value instanceof Error ? value.message : '操作失败。'
}

function selectFile(side: 'source' | 'target', event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null
  if (side === 'source') sourceFile.value = file
  else targetFile.value = file
  preview.value = null
}

async function makePreview() {
  if (!sourceFile.value || !targetFile.value) return
  busy.value = true
  error.value = ''
  try {
    preview.value = await previewDocumentAlignment(props.projectId, sourceFile.value, targetFile.value)
  } catch (value) {
    error.value = errorText(value)
  } finally {
    busy.value = false
  }
}

async function createBatch() {
  if (!preview.value) return
  busy.value = true
  alignmentReady.value = false
  error.value = ''
  message.value = '正在生成对齐草稿…'
  try {
    const batch = await createDocumentAlignmentBatch(props.projectId, {
      preview_token: preview.value.preview_token,
      source_language: sourceLanguage.value,
      target_language: targetLanguage.value,
      granularity: granularity.value,
      use_llm_for_hard_blocks: false,
      full_review: fullReview.value,
      alignment_strategy: alignmentStrategy.value,
    })
    batchId.value = batch.id
    pollAlignment()
  } catch (value) {
    error.value = errorText(value)
    busy.value = false
  }
}

function pollAlignment() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const batch = await getProofreadingBatch(batchId.value)
      message.value = batch.message
      alignmentProgress.value = Number(batch.progress || 0)
      if (batch.alignment_status === 'draft') {
        stopPolling()
        alignmentReady.value = true
        busy.value = false
        emit('refresh')
      } else if (batch.alignment_status === 'canceled') {
        stopPolling()
        busy.value = false
        message.value = batch.message || '双文档对齐已终止。'
      } else if (batch.alignment_status === 'failed') {
        throw new Error(batch.error_message || '对齐失败。')
      }
    } catch (value) {
      stopPolling()
      busy.value = false
      error.value = errorText(value)
    }
  }, 1200)
}

async function rerun() {
  if (!batchId.value || busy.value) return
  busy.value = true
  alignmentReady.value = false
  alignmentProgress.value = 0
  error.value = ''
  message.value = '正在准备顺序对齐窗口…'
  try {
    await rerunAlignment(batchId.value)
    pollAlignment()
  } catch (value) {
    busy.value = false
    error.value = errorText(value)
  }
}

async function cancel() {
  if (!batchId.value || !busy.value) return
  error.value = ''
  try {
    const batch = await cancelAlignment(batchId.value)
    message.value = batch.message
  } catch (value) {
    error.value = errorText(value)
  }
}

async function exportCsv() {
  if (!batchId.value) return
  error.value = ''
  try {
    const response = await downloadAlignmentCsv(batchId.value)
    const filename = resolveDownloadFilename(response.headers['content-disposition'], '原文译文对照.csv')
    downloadBlob(response.data, filename)
  } catch (value) {
    error.value = errorText(value)
  }
}

async function openAlignmentWorkbench() {
  if (!batchId.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await confirmAlignment(batchId.value)
    emit('refresh')
    await router.push({
      name: 'workbench-focus',
      params: { id: result.file_record_id },
      query: { from: 'project', pid: props.projectId, mode: 'alignment' },
    })
  } catch (value) {
    error.value = errorText(value)
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    const existing = (await listProofreadingBatches(props.projectId)).find(batch => (
      batch.batch_kind === 'document_pair' && ['aligning', 'canceling', 'draft'].includes(batch.alignment_status || '')
    ))
    if (!existing) return
    batchId.value = existing.id
    message.value = existing.message
    alignmentProgress.value = Number(existing.progress || 0)
    if (existing.alignment_status === 'draft') {
      alignmentReady.value = true
    } else {
      busy.value = true
      pollAlignment()
    }
  } catch (value) {
    error.value = errorText(value)
  }
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="alignment-editor" :class="{ 'is-compact': compact }">
    <header v-if="!compact">
      <div>
        <strong>双文档分块对齐</strong>
        <p>完成自动对齐后，进入对齐工作台统一复核和调整。</p>
      </div>
    </header>

    <p v-if="error" class="form-message is-error">{{ error }}</p>

    <div v-if="!alignmentReady" class="alignment-upload">
      <div class="alignment-upload__head">
        <span class="alignment-upload__head-icon"><Upload :size="20" /></span>
        <div>
          <strong>上传需要校对的两个文档</strong>
          <p>依次选择原文和译文。系统会识别段落、表格、重复页眉页脚和页码。</p>
        </div>
      </div>

      <div class="alignment-upload__files">
        <label class="file-pick" :class="{ 'is-selected': sourceFile }">
          <input type="file" accept=".doc,.docx,.txt,.html,.htm" @change="selectFile('source', $event)">
          <span class="file-pick__step">1</span>
          <span class="file-pick__icon"><Check v-if="sourceFile" :size="22" /><FileText v-else :size="22" /></span>
          <span class="file-pick__copy">
            <strong>选择原文文档</strong>
            <small :title="sourceFile?.name">{{ sourceFile?.name || '点击此处浏览文件' }}</small>
          </span>
          <span class="file-pick__action">{{ sourceFile ? '重新选择' : '选择文件' }}</span>
        </label>

        <span class="alignment-upload__direction" aria-hidden="true"><ArrowRight :size="20" /></span>

        <label class="file-pick" :class="{ 'is-selected': targetFile }">
          <input type="file" accept=".doc,.docx,.txt,.html,.htm" @change="selectFile('target', $event)">
          <span class="file-pick__step">2</span>
          <span class="file-pick__icon"><Check v-if="targetFile" :size="22" /><FileText v-else :size="22" /></span>
          <span class="file-pick__copy">
            <strong>选择译文文档</strong>
            <small :title="targetFile?.name">{{ targetFile?.name || '点击此处浏览文件' }}</small>
          </span>
          <span class="file-pick__action">{{ targetFile ? '重新选择' : '选择文件' }}</span>
        </label>
      </div>

      <div class="alignment-upload__actions">
        <span>支持 DOC、DOCX、TXT、HTML、HTM</span>
        <button class="button button--primary alignment-upload__preview-button" :disabled="busy || !sourceFile || !targetFile" @click="makePreview">
          解析并预览 <ArrowRight :size="16" />
        </button>
      </div>

      <div v-if="preview" class="alignment-preview-config">
        <strong>{{ preview.source.unit_count }} 个原文单元 → {{ preview.target.unit_count }} 个译文单元</strong>
        <span>
          原文 {{ preview.source.paragraph_count }} 个段落块 / {{ preview.source.table_count }} 张表，
          译文 {{ preview.target.paragraph_count }} 个段落块 / {{ preview.target.table_count }} 张表
        </span>
        <select v-model="sourceLanguage" class="field__control" aria-label="原文语言">
          <option v-for="item in languageOptions" :key="item.code" :value="item.code">{{ item.label }}</option>
        </select>
        <select v-model="targetLanguage" class="field__control" aria-label="译文语言">
          <option v-for="item in languageOptions" :key="item.code" :value="item.code">{{ item.label }}</option>
        </select>
        <select v-model="granularity" class="field__control" aria-label="对齐粒度">
          <option value="sentence">句子粒度（默认）</option>
          <option value="paragraph">段落粒度</option>
        </select>
        <select v-model="alignmentStrategy" class="field__control" aria-label="对齐策略">
          <option value="order_first">全文顺序优先（当前稳定版）</option>
          <option value="hierarchical_llm">分块 + LLM（实验版）</option>
          <option value="structure_aware">结构辅助（旧方式）</option>
        </select>
        <label><input v-model="fullReview" type="checkbox"> 启用 Gemini 块内拆分与配对</label>
        <button class="button button--primary" :disabled="busy || sourceLanguage === targetLanguage" @click="createBatch">生成完整对齐草稿</button>
      </div>

      <div v-if="busy && batchId" class="alignment-running" role="status" aria-live="polite">
        <div class="alignment-running__head"><span>{{ message || '正在对齐…' }}</span><strong>{{ alignmentProgress }}%</strong></div>
        <progress :value="alignmentProgress" max="100" />
        <button class="button button--danger" type="button" @click="cancel"><Square :size="14" />终止任务</button>
      </div>
      <template v-else-if="message">
        <span>{{ message }}</span>
        <button v-if="batchId" class="button" type="button" @click="rerun"><RefreshCw :size="14" />重新运行当前批次</button>
      </template>
    </div>

    <div v-else class="alignment-ready" role="status">
      <div class="alignment-ready__summary">
        <span class="alignment-ready__icon"><Check :size="24" /></span>
        <div>
          <strong>对齐草稿已生成</strong>
          <p>{{ message || '自动对齐已完成。' }} 完整配对请进入对齐工作台查看和调整。</p>
        </div>
      </div>
      <div class="alignment-ready__actions">
        <button class="button button--primary" :disabled="busy" @click="openAlignmentWorkbench">
          进入对齐工作台 <ArrowRight :size="16" />
        </button>
        <button class="button" :disabled="busy" @click="exportCsv"><Download :size="15" />导出对照表</button>
        <button class="button" :disabled="busy" @click="rerun"><RefreshCw :size="15" />重新对齐</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.alignment-editor { display: grid; gap: 12px; min-height: 0; padding: 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
.alignment-editor.is-compact { padding: 0; border: 0; border-radius: 0; }
.alignment-editor header p { margin: 4px 0 0; }
.alignment-upload { display: grid; gap: 16px; padding: 20px; border: 1px solid rgba(15, 118, 110, .22); border-radius: 12px; background: linear-gradient(135deg, rgba(240, 253, 250, .86), rgba(248, 250, 252, .96)); }
.alignment-upload__head { display: flex; align-items: center; gap: 12px; }
.alignment-upload__head-icon { display: grid; flex: 0 0 auto; width: 42px; height: 42px; place-items: center; border-radius: 11px; background: var(--brand); color: #fff; box-shadow: 0 8px 20px rgba(15, 118, 110, .2); }
.alignment-upload__head strong { color: var(--ink-900, #0f172a); font-size: 17px; }
.alignment-upload__head p { margin: 3px 0 0; color: var(--ink-500); font-size: 13px; }
.alignment-upload__files { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: stretch; gap: 12px; }
.alignment-upload__direction { display: grid; place-items: center; color: var(--brand); }
.alignment-upload__actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.alignment-upload__actions > span { color: var(--ink-500); font-size: 12px; }
.alignment-upload__preview-button { min-width: 170px; justify-content: center; }
.alignment-preview-config { display: grid; grid-template-columns: minmax(220px, 1fr) repeat(4, minmax(145px, .65fr)); align-items: center; gap: 10px; padding-top: 14px; border-top: 1px solid rgba(15, 118, 110, .18); }
.alignment-preview-config > label { grid-column: 1 / -2; }
.alignment-running { display: grid; grid-template-columns: minmax(240px, 1fr) auto; align-items: center; gap: 8px 12px; width: 100%; padding: 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-muted); }
.alignment-running__head { grid-column: 1 / -1; display: flex; justify-content: space-between; gap: 16px; }
.alignment-running progress { width: 100%; height: 12px; }
.file-pick { position: relative; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; min-height: 96px; padding: 16px 16px 16px 20px; border: 2px dashed rgba(15, 118, 110, .38); border-radius: 12px; background: #fff; cursor: pointer; transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease; }
.file-pick:hover { border-color: var(--brand); box-shadow: 0 10px 24px rgba(15, 23, 42, .08); transform: translateY(-1px); }
.file-pick.is-selected { border-style: solid; border-color: var(--brand); background: rgba(236, 253, 245, .72); }
.file-pick input { display: none; }
.file-pick__step { position: absolute; top: 8px; left: 8px; display: grid; width: 20px; height: 20px; place-items: center; border-radius: 999px; background: var(--brand); color: #fff; font-size: 11px; font-weight: 800; }
.file-pick__icon { display: grid; width: 46px; height: 46px; place-items: center; border-radius: 10px; background: #e7f6f2; color: var(--brand); }
.file-pick.is-selected .file-pick__icon { background: var(--brand); color: #fff; }
.file-pick__copy { display: grid; gap: 5px; min-width: 0; }
.file-pick__copy strong { color: var(--ink-800, #1e293b); font-size: 15px; }
.file-pick__copy small { overflow: hidden; color: var(--ink-500); text-overflow: ellipsis; white-space: nowrap; }
.file-pick__action { padding: 6px 9px; border: 1px solid rgba(15, 118, 110, .24); border-radius: 7px; color: var(--brand); font-size: 12px; font-weight: 700; white-space: nowrap; }
.alignment-ready { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 20px; border: 1px solid rgba(15, 118, 110, .24); border-radius: 12px; background: linear-gradient(135deg, rgba(236, 253, 245, .9), rgba(248, 250, 252, .96)); }
.alignment-ready__summary { display: flex; align-items: center; gap: 12px; min-width: 0; }
.alignment-ready__icon { display: grid; flex: 0 0 auto; width: 46px; height: 46px; place-items: center; border-radius: 50%; background: var(--brand); color: #fff; }
.alignment-ready__summary strong { color: var(--ink-900, #0f172a); font-size: 17px; }
.alignment-ready__summary p { margin: 4px 0 0; color: var(--ink-500); font-size: 13px; }
.alignment-ready__actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
@media (max-width: 900px) {
  .alignment-upload { padding: 14px; }
  .alignment-upload__files, .alignment-preview-config { grid-template-columns: 1fr; }
  .alignment-upload__direction { transform: rotate(90deg); }
  .alignment-upload__actions, .alignment-ready { align-items: stretch; flex-direction: column; }
  .alignment-preview-config > label { grid-column: auto; }
  .alignment-ready__actions { justify-content: flex-start; }
}
</style>
