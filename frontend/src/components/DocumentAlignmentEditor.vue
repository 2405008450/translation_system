<script setup lang="ts">
import axios from 'axios'
import { ArrowRight, Check, ChevronDown, ChevronUp, Download, FileText, Lock, Merge, RefreshCw, Scissors, Square, Upload } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  cancelAlignment, confirmAlignment, createDocumentAlignmentBatch, downloadAlignmentCsv,
  listAlignmentPreviewPairs, mergeAlignmentPairRange, mergeAlignmentPairs,
  patchAlignmentPair, previewDocumentAlignment, rerunAlignment, shiftAlignmentBoundary,
  splitAlignmentPair, type AlignmentPair, type AlignmentPreview,
} from '../api/documentAlignment'
import { getProofreadingBatch, listProofreadingBatches } from '../api/proofreading'
import { languageOptions } from '../constants/languages'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'

const props = defineProps<{ projectId: string; compact?: boolean }>()
const emit = defineEmits<{ refresh: []; openWorkbench: [fileRecordId: string] }>()
const sourceFile = ref<File | null>(null)
const targetFile = ref<File | null>(null)
const preview = ref<AlignmentPreview | null>(null)
const sourceLanguage = ref('zh-CN')
const targetLanguage = ref('en-US')
const granularity = ref<'sentence' | 'paragraph'>('sentence')
const fullReview = ref(true)
const batchId = ref('')
const pairs = ref<AlignmentPair[]>([])
const activeIndex = ref(0)
const busy = ref(false)
const alignmentProgress = ref(0)
const pairTotal = ref(0)
const lowConfidenceTotal = ref(0)
const openingPreviewLimit = ref(20)
const lowConfidenceLimit = ref(80)
const message = ref('')
const error = ref('')
const selectedPairIds = ref<Set<string>>(new Set())
const selectionAnchorPairId = ref<string | null>(null)
let pollTimer: ReturnType<typeof setInterval> | null = null

const activePair = computed(() => pairs.value[activeIndex.value])
const suspiciousCount = computed(() => pairs.value.filter(pair => pair.confidence_level === 'low' || !pair.src_indices.length || !pair.tgt_indices.length).length)
const canMergeNext = computed(() => {
  const current = activePair.value
  const next = pairs.value[activeIndex.value + 1]
  return Boolean(current && next && next.pair_order === current.pair_order + 1)
})
const orderedSelectedPairs = computed(() => pairs.value.filter(pair => selectedPairIds.value.has(pair.id)))
const canMergeSelected = computed(() => {
  const selected = orderedSelectedPairs.value
  if (selected.length < 2 || selected.length !== selectedPairIds.value.size) return false
  return selected.every((pair, index) => index === 0 || pair.pair_order === selected[index - 1].pair_order + 1)
})
const previewSummary = computed(() => {
  const lowLoaded = Math.min(lowConfidenceTotal.value, lowConfidenceLimit.value)
  const openingLoaded = Math.min(pairTotal.value, openingPreviewLimit.value)
  return `仅显示开头 ${openingLoaded} 条及低置信度 ${lowLoaded}/${lowConfidenceTotal.value} 条 · 全文 ${pairTotal.value} 条`
})

function errorText(value: unknown) {
  return axios.isAxiosError(value) ? String(value.response?.data?.detail || '操作失败。') : value instanceof Error ? value.message : '操作失败。'
}

function selectFile(side: 'source' | 'target', event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null
  if (side === 'source') sourceFile.value = file
  else targetFile.value = file
  preview.value = null
}

async function makePreview() {
  if (!sourceFile.value || !targetFile.value) return
  busy.value = true; error.value = ''
  try { preview.value = await previewDocumentAlignment(props.projectId, sourceFile.value, targetFile.value) }
  catch (value) { error.value = errorText(value) }
  finally { busy.value = false }
}

async function createBatch() {
  if (!preview.value) return
  busy.value = true; error.value = ''; message.value = '正在生成对齐草稿…'
  try {
    const batch = await createDocumentAlignmentBatch(props.projectId, {
      preview_token: preview.value.preview_token, source_language: sourceLanguage.value,
      target_language: targetLanguage.value, granularity: granularity.value,
      use_llm_for_hard_blocks: false, full_review: fullReview.value,
    })
    batchId.value = batch.id
    pollAlignment()
  } catch (value) { error.value = errorText(value); busy.value = false }
}

function pollAlignment() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const batch = await getProofreadingBatch(batchId.value)
      message.value = batch.message
      alignmentProgress.value = Number(batch.progress || 0)
      if (batch.alignment_status === 'draft') {
        if (pollTimer) clearInterval(pollTimer)
        pollTimer = null
        await reloadPairs()
        busy.value = false
        emit('refresh')
      } else if (batch.alignment_status === 'canceled') {
        if (pollTimer) clearInterval(pollTimer)
        pollTimer = null
        busy.value = false
        message.value = batch.message || '双文档对齐已终止。'
      } else if (batch.alignment_status === 'failed') {
        throw new Error(batch.error_message || '对齐失败。')
      }
    } catch (value) {
      if (pollTimer) clearInterval(pollTimer)
      pollTimer = null; busy.value = false; error.value = errorText(value)
    }
  }, 1200)
}

async function reloadPairs() {
  const result = await listAlignmentPreviewPairs(batchId.value)
  pairs.value = result.items
  pairTotal.value = result.total
  lowConfidenceTotal.value = result.low_confidence_total
  openingPreviewLimit.value = result.opening_limit
  lowConfidenceLimit.value = result.low_confidence_limit
  activeIndex.value = Math.min(activeIndex.value, Math.max(0, pairs.value.length - 1))
  const loadedIds = new Set(pairs.value.map(pair => pair.id))
  selectedPairIds.value = new Set([...selectedPairIds.value].filter(id => loadedIds.has(id)))
}

function togglePairSelection(pair: AlignmentPair, event: MouseEvent) {
  if (event.shiftKey && selectionAnchorPairId.value) {
    const anchorIndex = pairs.value.findIndex(item => item.id === selectionAnchorPairId.value)
    const currentIndex = pairs.value.findIndex(item => item.id === pair.id)
    if (anchorIndex >= 0 && currentIndex >= 0) {
      const [start, end] = anchorIndex <= currentIndex ? [anchorIndex, currentIndex] : [currentIndex, anchorIndex]
      selectedPairIds.value = new Set(pairs.value.slice(start, end + 1).map(item => item.id))
    }
  } else {
    const next = new Set(selectedPairIds.value)
    if (next.has(pair.id)) next.delete(pair.id)
    else next.add(pair.id)
    selectedPairIds.value = next
  }
  selectionAnchorPairId.value = pair.id
  activeIndex.value = pairs.value.findIndex(item => item.id === pair.id)
}

async function toggleLock() {
  if (!activePair.value) return
  await patchAlignmentPair(activePair.value.id, { locked: !activePair.value.locked }); await reloadPairs()
}

async function splitCurrent() {
  if (!activePair.value || activePair.value.src_indices.length + activePair.value.tgt_indices.length < 3) return
  await splitAlignmentPair(batchId.value, activePair.value); await reloadPairs()
}

async function mergeNext() {
  const current = activePair.value, next = pairs.value[activeIndex.value + 1]
  if (!current || !next || next.pair_order !== current.pair_order + 1) return
  await mergeAlignmentPairs(batchId.value, current.id, next.id); await reloadPairs()
}

async function mergeSelected() {
  if (!canMergeSelected.value) return
  busy.value = true
  error.value = ''
  try {
    const selected = orderedSelectedPairs.value
    await mergeAlignmentPairRange(batchId.value, selected.map(pair => pair.id))
    selectedPairIds.value = new Set()
    selectionAnchorPairId.value = null
    await reloadPairs()
    message.value = `已合并 ${selected.length} 个连续配对，并自动锁定结果。`
  } catch (value) {
    error.value = errorText(value)
  } finally {
    busy.value = false
  }
}

async function shift(direction: 'next_into_current' | 'current_into_next') {
  if (!activePair.value) return
  await shiftAlignmentBoundary(batchId.value, activePair.value.id, direction); await reloadPairs()
}

function jumpSuspicious(step: 1 | -1) {
  if (!pairs.value.length) return
  for (let offset = 1; offset <= pairs.value.length; offset++) {
    const index = (activeIndex.value + step * offset + pairs.value.length) % pairs.value.length
    const pair = pairs.value[index]
    if (pair.confidence_level === 'low' || !pair.src_indices.length || !pair.tgt_indices.length) { activeIndex.value = index; break }
  }
}

function featureReason(pair: AlignmentPair) {
  const features = pair.features
  if (features.confidence_reason) return String(features.confidence_reason)
  if (features.ambiguous_path) return '存在代价接近的另一条配对路径'
  if (Number(features.number_cost || 0) > 0) return '两侧数字集合不一致'
  if (Number(features.numbering_cost || 0) > 0) return '两侧编号不一致'
  if (pair.method === 'anchor_exact') return '双侧文本精确一致'
  if (pair.method.startsWith('anchor_field_')) return '字段类型与字段值一致'
  if (pair.method === 'anchor_number_rare') return '全文唯一数字锚点'
  if (features.semantic_similarity != null) return `跨语言语义相似度 ${Math.round(Number(features.semantic_similarity) * 100)}%`
  if (features.bidirectional_consistent) return '正向与反向对齐一致'
  return pair.method
}

async function rerun() {
  busy.value = true; alignmentProgress.value = 0; message.value = '正在准备向量对齐窗口…'
  await rerunAlignment(batchId.value); pollAlignment()
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

async function confirm() {
  if (!batchId.value) return
  busy.value = true
  try { const result = await confirmAlignment(batchId.value); emit('refresh'); emit('openWorkbench', result.file_record_id) }
  catch (value) { error.value = errorText(value) }
  finally { busy.value = false }
}

function onKeydown(event: KeyboardEvent) {
  if (!pairs.value.length || event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return
  if (event.key.toLowerCase() === 'j') jumpSuspicious(1)
  else if (event.key.toLowerCase() === 'k') jumpSuspicious(-1)
  else if (event.code === 'Space') { event.preventDefault(); void toggleLock() }
  else if (event.altKey && event.key.toLowerCase() === 'm') { event.preventDefault(); void mergeNext() }
  else if (event.altKey && event.key.toLowerCase() === 's') { event.preventDefault(); void splitCurrent() }
  else if (event.altKey && event.key.toLowerCase() === 'r') { event.preventDefault(); void rerun() }
  else if (event.altKey && event.key === 'ArrowDown') { event.preventDefault(); void shift('next_into_current') }
  else if (event.altKey && event.key === 'ArrowUp') { event.preventDefault(); void shift('current_into_next') }
}
window.addEventListener('keydown', onKeydown)
onMounted(async () => {
  const existing = (await listProofreadingBatches(props.projectId)).find(batch => (
    batch.batch_kind === 'document_pair' && ['aligning', 'canceling', 'draft'].includes(batch.alignment_status || '')
  ))
  if (!existing) return
  batchId.value = existing.id
  message.value = existing.message
  if (existing.alignment_status === 'draft') await reloadPairs()
  else { busy.value = true; pollAlignment() }
})
onBeforeUnmount(() => { window.removeEventListener('keydown', onKeydown); if (pollTimer) clearInterval(pollTimer) })
</script>

<template>
  <section class="alignment-editor" :class="{ 'is-compact': compact }">
    <header v-if="!compact"><div><strong>双文档对齐校对</strong><p>分别上传原文与译文，系统解析结构并生成句段对齐草稿。</p></div></header>
    <p v-if="error" class="form-message is-error">{{ error }}</p>
    <div v-if="!pairs.length" class="alignment-upload">
      <div class="alignment-upload__head">
        <span class="alignment-upload__head-icon"><Upload :size="20" /></span>
        <div>
          <strong>上传需要校对的两个文档</strong>
          <p>点击下方卡片，依次选择原文和对应译文。</p>
        </div>
      </div>

      <div class="alignment-upload__files">
        <label class="file-pick" :class="{ 'is-selected': sourceFile }">
          <input type="file" accept=".doc,.docx,.txt" @change="selectFile('source', $event)">
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
          <input type="file" accept=".doc,.docx,.txt" @change="selectFile('target', $event)">
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
        <span>支持 DOC、DOCX、TXT · 单个文件最大以系统配置为准</span>
        <button class="button button--primary alignment-upload__preview-button" :disabled="busy || !sourceFile || !targetFile" @click="makePreview">
          解析并预览
          <ArrowRight :size="16" />
        </button>
      </div>

      <div v-if="preview" class="alignment-preview-config">
        <strong>{{ preview.source.unit_count }} 个原文单元 ↔ {{ preview.target.unit_count }} 个译文单元</strong>
        <select v-model="sourceLanguage" class="field__control" aria-label="原文语言"><option v-for="item in languageOptions" :key="item.code" :value="item.code">{{ item.label }}</option></select>
        <select v-model="targetLanguage" class="field__control" aria-label="译文语言"><option v-for="item in languageOptions" :key="item.code" :value="item.code">{{ item.label }}</option></select>
        <select v-model="granularity" class="field__control" aria-label="对齐粒度"><option value="sentence">句粒度（默认）</option><option value="paragraph">段落粒度</option></select>
        <label><input v-model="fullReview" type="checkbox">全量 Gemini 边界复核（推荐）</label>
        <button class="button button--primary" :disabled="busy || sourceLanguage === targetLanguage" @click="createBatch">生成对齐草稿</button>
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
    <template v-else>
      <div class="alignment-toolbar">
        <span>⚠ 当前预览 {{ suspiciousCount }} 个可疑配对</span>
        <small class="alignment-preview-limit">{{ previewSummary }}</small>
        <span v-if="busy" class="alignment-progress">
          {{ message || '正在对齐…' }}
          <progress :value="alignmentProgress" max="100" />
        </span>
        <button class="button" @click="jumpSuspicious(-1)"><ChevronUp :size="14" />上一个</button>
        <button class="button" @click="jumpSuspicious(1)"><ChevronDown :size="14" />下一个</button>
        <button class="button" @click="toggleLock"><Lock :size="14" />{{ activePair?.locked ? '解锁' : '锁定' }}</button>
        <button class="button" @click="splitCurrent"><Scissors :size="14" />拆分</button>
        <button class="button" :disabled="busy || !canMergeNext" :title="canMergeNext ? '合并相邻的下一项' : '抽样预览中下一项并非全文相邻句段'" @click="mergeNext"><Merge :size="14" />合并下一项</button>
        <button class="button" :disabled="busy || !canMergeSelected" :title="canMergeSelected ? '一次合并并锁定所选连续配对' : '请勾选全文顺序中连续的至少两个配对'" @click="mergeSelected"><Merge :size="14" />合并所选（{{ selectedPairIds.size }}）</button>
        <button class="button" :disabled="busy" @click="rerun"><RefreshCw :size="14" />重跑未锁定区间</button>
        <button v-if="busy" class="button button--danger" @click="cancel"><Square :size="14" />终止任务</button>
        <button class="button" :disabled="busy" @click="exportCsv"><Download :size="14" />导出 CSV</button>
        <button class="button button--primary" :disabled="busy" @click="confirm"><Check :size="14" />确认并生成句段</button>
      </div>
      <div class="alignment-grid">
        <div v-for="(pair, index) in pairs" :key="pair.id" class="alignment-row" :class="[`is-${pair.confidence_level}`, { 'is-active': index === activeIndex, 'is-selected': selectedPairIds.has(pair.id) }]" role="button" tabindex="0" @click="activeIndex = index" @keydown.enter="activeIndex = index">
          <label class="alignment-row__select" title="选择配对；按住 Shift 可连续选择" @click.stop>
            <input type="checkbox" :checked="selectedPairIds.has(pair.id)" @click="togglePairSelection(pair, $event)">
          </label>
          <div><small>S{{ pair.src_indices.join(', S') || '—' }}</small><p>{{ pair.source_text || '（增译）' }}</p></div>
          <div class="alignment-state" :title="JSON.stringify(pair.features)">{{ pair.locked ? '🔒' : pair.target_text ? (pair.confidence_level === 'low' ? '⚠' : '●') : '✕' }}<small>{{ Math.round(pair.confidence * 100) }}%</small><small class="alignment-reason">{{ featureReason(pair) }}</small></div>
          <div><small>T{{ pair.tgt_indices.join(', T') || '—' }}</small><p>{{ pair.target_text || '（缺译文）' }}</p></div>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.alignment-editor { display: grid; gap: 12px; min-height: 0; padding: 16px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
.alignment-editor.is-compact { padding: 0; border: 0; border-radius: 0; }
.alignment-editor header p, .alignment-row p { margin: 4px 0 0; }
.alignment-upload { display: grid; gap: 16px; padding: 20px; border: 1px solid rgba(15, 118, 110, .22); border-radius: 12px; background: linear-gradient(135deg, rgba(240, 253, 250, .86), rgba(248, 250, 252, .96)); }
.alignment-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.alignment-upload__head { display: flex; align-items: center; gap: 12px; }
.alignment-upload__head-icon { display: grid; flex: 0 0 auto; width: 42px; height: 42px; place-items: center; border-radius: 11px; background: var(--brand); color: #fff; box-shadow: 0 8px 20px rgba(15, 118, 110, .2); }
.alignment-upload__head strong { color: var(--ink-900, #0f172a); font-size: 17px; }
.alignment-upload__head p { margin: 3px 0 0; color: var(--ink-500); font-size: 13px; }
.alignment-upload__files { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: stretch; gap: 12px; }
.alignment-upload__direction { display: grid; place-items: center; color: var(--brand); }
.alignment-upload__actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.alignment-upload__actions > span { color: var(--ink-500); font-size: 12px; }
.alignment-upload__preview-button { min-width: 170px; justify-content: center; }
.alignment-preview-config { display: grid; grid-template-columns: minmax(200px, 1fr) repeat(3, minmax(150px, .65fr)); align-items: center; gap: 10px; padding-top: 14px; border-top: 1px solid rgba(15, 118, 110, .18); }
.alignment-preview-config > label { grid-column: 1 / -2; }
.alignment-progress { display: inline-flex; align-items: center; gap: 8px; color: var(--ink-500); }
.alignment-progress progress { width: 120px; }
.alignment-preview-limit { flex: 1 1 100%; color: var(--ink-500); }
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
.alignment-grid { display: grid; max-height: min(620px, calc(100vh - 290px)); overflow: auto; border: 1px solid var(--line); border-radius: 8px; }
.alignment-row { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) 68px minmax(0, 1fr); gap: 12px; padding: 12px 12px 12px 40px; text-align: left; color: inherit; background: transparent; border: 0; border-bottom: 1px solid var(--line); cursor: pointer; }
.alignment-row.is-active { outline: 2px solid var(--brand); outline-offset: -2px; }.alignment-row.is-selected { box-shadow: inset 4px 0 var(--brand); }.alignment-row.is-low { background: #fff4e5; }.alignment-row.is-high { background: #f2fbf5; }
.alignment-row__select { position: absolute; inset-inline-start: 14px; top: 18px; display: grid; place-items: center; cursor: pointer; }
.alignment-row__select input { width: 16px; height: 16px; accent-color: var(--brand); cursor: pointer; }
.alignment-state { display: grid; place-content: center; text-align: center; border-inline: 1px solid var(--line); }.alignment-state small { display: block; }.alignment-reason { max-width: 64px; color: var(--ink-500); font-size: 10px; line-height: 1.2; }
@media (max-width: 760px) {
  .alignment-upload { padding: 14px; }
  .alignment-upload__files { grid-template-columns: 1fr; }
  .alignment-upload__direction { transform: rotate(90deg); }
  .alignment-upload__actions { align-items: stretch; flex-direction: column; }
  .alignment-preview-config { grid-template-columns: 1fr; }
  .alignment-preview-config > label { grid-column: auto; }
}
</style>
