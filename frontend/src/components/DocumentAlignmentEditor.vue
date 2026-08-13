<script setup lang="ts">
import axios from 'axios'
import {
  ArrowLeft, ArrowRight, Check, ChevronDown, ChevronUp, Download, FileText,
  Lock, Merge, Plus, Redo2, RefreshCw, Scissors, Search, Square, Trash2, Undo2, Upload, X,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  cancelAlignment, confirmAlignment, createDocumentAlignmentBatch, downloadAlignmentCsv,
  listAlignmentPairs, patchAlignmentPair, previewDocumentAlignment,
  replaceAlignmentPairRange, rerunAlignment,
  type AlignmentPair, type AlignmentPairReplacement, type AlignmentPreview,
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
const pairs = ref<AlignmentPair[]>([])
const activeIndex = ref(0)
const pairPage = ref(1)
const pairPageSize = 100
const pairFilter = ref<'all' | 'low'>('all')
const searchKeyword = ref('')
const appliedSearchKeyword = ref('')
const searchOpen = ref(false)
const pairTotal = ref(0)
const lowConfidenceTotal = ref(0)
const busy = ref(false)
const alignmentProgress = ref(0)
const message = ref('')
const error = ref('')
const selectedPairIds = ref<Set<string>>(new Set())
const selectionAnchorPairId = ref<string | null>(null)
interface AlignmentEditCommand {
  startOrder: number
  before: AlignmentPairReplacement[]
  after: AlignmentPairReplacement[]
  label: string
}
const undoStack = ref<AlignmentEditCommand[]>([])
const redoStack = ref<AlignmentEditCommand[]>([])
let pollTimer: ReturnType<typeof setInterval> | null = null

const activePair = computed(() => pairs.value[activeIndex.value])
const pairPageCount = computed(() => Math.max(1, Math.ceil(pairTotal.value / pairPageSize)))
const pairRangeLabel = computed(() => {
  if (!pairTotal.value) return '暂无配对'
  const start = (pairPage.value - 1) * pairPageSize + 1
  const end = Math.min(pairPage.value * pairPageSize, pairTotal.value)
  return `第 ${start}–${end} 条，共 ${pairTotal.value} 条`
})
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
const canEditStructure = computed(() => pairFilter.value === 'all' && !busy.value)
const canDeleteCurrent = computed(() => Boolean(
  activePair.value && !activePair.value.src_indices.length && !activePair.value.tgt_indices.length,
))

function pairReplacement(pair: AlignmentPair): AlignmentPairReplacement {
  return {
    src_indices: [...pair.src_indices],
    tgt_indices: [...pair.tgt_indices],
    locked: pair.locked,
  }
}

function cloneReplacements(items: AlignmentPairReplacement[]) {
  return items.map(item => ({
    src_indices: [...item.src_indices],
    tgt_indices: [...item.tgt_indices],
    locked: item.locked,
  }))
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
  error.value = ''
  message.value = '正在生成对齐草稿…'
  undoStack.value = []
  redoStack.value = []
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
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const batch = await getProofreadingBatch(batchId.value)
      message.value = batch.message
      alignmentProgress.value = Number(batch.progress || 0)
      if (batch.alignment_status === 'draft') {
        if (pollTimer) clearInterval(pollTimer)
        pollTimer = null
        pairPage.value = 1
        pairFilter.value = 'all'
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
      pollTimer = null
      busy.value = false
      error.value = errorText(value)
    }
  }, 1200)
}

async function reloadPairs() {
  const [result, lowResult] = await Promise.all([
    listAlignmentPairs(batchId.value, {
      page: pairPage.value,
      page_size: pairPageSize,
      confidence_level: pairFilter.value === 'low' ? 'low' : undefined,
      q: appliedSearchKeyword.value || undefined,
    }),
    listAlignmentPairs(batchId.value, { page: 1, page_size: 1, confidence_level: 'low' }),
  ])
  if (pairPage.value > 1 && !result.items.length && result.total > 0) {
    pairPage.value = Math.max(1, Math.ceil(result.total / pairPageSize))
    await reloadPairs()
    return
  }
  pairs.value = result.items
  pairTotal.value = result.total
  lowConfidenceTotal.value = lowResult.total
  activeIndex.value = Math.min(activeIndex.value, Math.max(0, pairs.value.length - 1))
  const loadedIds = new Set(pairs.value.map(pair => pair.id))
  selectedPairIds.value = new Set([...selectedPairIds.value].filter(id => loadedIds.has(id)))
}

async function changePairPage(step: -1 | 1) {
  const next = pairPage.value + step
  if (next < 1 || next > pairPageCount.value) return
  pairPage.value = next
  activeIndex.value = 0
  selectedPairIds.value = new Set()
  await reloadPairs()
}

async function setPairFilter(filter: 'all' | 'low') {
  if (pairFilter.value === filter) return
  pairFilter.value = filter
  pairPage.value = 1
  activeIndex.value = 0
  selectedPairIds.value = new Set()
  await reloadPairs()
}

async function applySearch() {
  appliedSearchKeyword.value = searchKeyword.value.trim()
  pairPage.value = 1
  activeIndex.value = 0
  selectedPairIds.value = new Set()
  await reloadPairs()
}

async function clearSearch() {
  searchKeyword.value = ''
  appliedSearchKeyword.value = ''
  searchOpen.value = false
  pairPage.value = 1
  activeIndex.value = 0
  await reloadPairs()
}

function togglePairSelection(pair: AlignmentPair, event: MouseEvent) {
  if (event.shiftKey && selectionAnchorPairId.value) {
    const anchorIndex = pairs.value.findIndex(item => item.id === selectionAnchorPairId.value)
    const currentIndex = pairs.value.findIndex(item => item.id === pair.id)
    if (anchorIndex >= 0 && currentIndex >= 0) {
      const [start, end] = anchorIndex <= currentIndex
        ? [anchorIndex, currentIndex]
        : [currentIndex, anchorIndex]
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
  await patchAlignmentPair(activePair.value.id, { locked: !activePair.value.locked })
  await reloadPairs()
}

async function executeEdit(
  startOrder: number,
  before: AlignmentPairReplacement[],
  after: AlignmentPairReplacement[],
  label: string,
) {
  busy.value = true
  error.value = ''
  try {
    await replaceAlignmentPairRange(batchId.value, {
      start_order: startOrder,
      delete_count: before.length,
      replacements: after,
    })
    undoStack.value.push({
      startOrder,
      before: cloneReplacements(before),
      after: cloneReplacements(after),
      label,
    })
    if (undoStack.value.length > 50) undoStack.value.shift()
    redoStack.value = []
    selectedPairIds.value = new Set()
    selectionAnchorPairId.value = null
    await reloadPairs()
    const localIndex = pairs.value.findIndex(pair => pair.pair_order === startOrder)
    if (localIndex >= 0) activeIndex.value = localIndex
    message.value = `${label}完成，已自动保存。`
  } catch (value) {
    error.value = errorText(value)
  } finally {
    busy.value = false
  }
}

async function splitCurrent() {
  const current = activePair.value
  if (!current || !canEditStructure.value || current.src_indices.length + current.tgt_indices.length < 3) return
  const srcAt = Math.ceil(current.src_indices.length / 2)
  const tgtAt = Math.ceil(current.tgt_indices.length / 2)
  const after = [
    { src_indices: current.src_indices.slice(0, srcAt), tgt_indices: current.tgt_indices.slice(0, tgtAt), locked: true },
    { src_indices: current.src_indices.slice(srcAt), tgt_indices: current.tgt_indices.slice(tgtAt), locked: true },
  ]
  if (after.some(item => !item.src_indices.length && !item.tgt_indices.length)) return
  await executeEdit(current.pair_order, [pairReplacement(current)], after, '拆分')
}

async function mergeNext() {
  const current = activePair.value
  const next = pairs.value[activeIndex.value + 1]
  if (!current || !next || !canEditStructure.value || next.pair_order !== current.pair_order + 1) return
  await executeEdit(current.pair_order, [pairReplacement(current), pairReplacement(next)], [{
    src_indices: [...current.src_indices, ...next.src_indices],
    tgt_indices: [...current.tgt_indices, ...next.tgt_indices],
    locked: true,
  }], '合并')
}

async function mergeSelected() {
  if (!canMergeSelected.value) return
  busy.value = true
  error.value = ''
  try {
    const selected = orderedSelectedPairs.value
    const merged: AlignmentPairReplacement = {
      src_indices: selected.flatMap(pair => pair.src_indices),
      tgt_indices: selected.flatMap(pair => pair.tgt_indices),
      locked: true,
    }
    await executeEdit(selected[0].pair_order, selected.map(pairReplacement), [merged], `合并 ${selected.length} 项`)
  } catch (value) {
    error.value = errorText(value)
  } finally {
    busy.value = false
  }
}

async function shift(side: 'source' | 'target', direction: 'next_into_current' | 'current_into_next') {
  const current = activePair.value
  const next = pairs.value[activeIndex.value + 1]
  if (!current || !next || !canEditStructure.value || next.pair_order !== current.pair_order + 1) return
  const before = [pairReplacement(current), pairReplacement(next)]
  const after = cloneReplacements(before)
  const field = side === 'source' ? 'src_indices' : 'tgt_indices'
  if (direction === 'next_into_current') {
    const value = after[1][field].shift()
    if (value == null) return
    after[0][field].push(value)
  } else {
    const value = after[0][field].pop()
    if (value == null) return
    after[1][field].unshift(value)
  }
  const sideLabel = side === 'source' ? '原文' : '译文'
  const directionLabel = direction === 'next_into_current' ? '上移' : '下移'
  await executeEdit(current.pair_order, before, after, `${sideLabel}${directionLabel}`)
}

async function insertEmptyPair() {
  const current = activePair.value
  if (!current || !canEditStructure.value) return
  await executeEdit(current.pair_order + 1, [], [{ src_indices: [], tgt_indices: [], locked: true }], '插入空行')
}

async function deleteEmptyPair() {
  const current = activePair.value
  if (!current || !canEditStructure.value || !canDeleteCurrent.value) return
  await executeEdit(current.pair_order, [pairReplacement(current)], [], '删除空行')
}

async function undoEdit() {
  const command = undoStack.value.pop()
  if (!command || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await replaceAlignmentPairRange(batchId.value, {
      start_order: command.startOrder,
      delete_count: command.after.length,
      replacements: command.before,
    })
    redoStack.value.push(command)
    await reloadPairs()
    message.value = `已撤回：${command.label}`
  } catch (value) {
    undoStack.value.push(command)
    error.value = errorText(value)
  } finally {
    busy.value = false
  }
}

async function redoEdit() {
  const command = redoStack.value.pop()
  if (!command || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await replaceAlignmentPairRange(batchId.value, {
      start_order: command.startOrder,
      delete_count: command.before.length,
      replacements: command.after,
    })
    undoStack.value.push(command)
    await reloadPairs()
    message.value = `已重做：${command.label}`
  } catch (value) {
    redoStack.value.push(command)
    error.value = errorText(value)
  } finally {
    busy.value = false
  }
}

function jumpSuspicious(step: 1 | -1) {
  if (!pairs.value.length) return
  for (let offset = 1; offset <= pairs.value.length; offset += 1) {
    const index = (activeIndex.value + step * offset + pairs.value.length) % pairs.value.length
    const pair = pairs.value[index]
    if (pair.confidence_level === 'low' || !pair.src_indices.length || !pair.tgt_indices.length) {
      activeIndex.value = index
      break
    }
  }
}

function featureReason(pair: AlignmentPair) {
  const features = pair.features
  if (features.confidence_reason) return String(features.confidence_reason)
  if (features.ambiguous_path) return '存在多个接近的对齐路径'
  if (Number(features.number_cost || 0) > 0) return '两侧数字不一致'
  if (Number(features.numbering_cost || 0) > 0) return '两侧编号不一致'
  if (pair.method === 'anchor_exact') return '双侧文本完全一致'
  if (pair.method.startsWith('anchor_field_')) return '字段类型与值一致'
  if (pair.method === 'anchor_number_rare') return '全文唯一数字锚点'
  if (features.semantic_similarity != null) return `语义相似度 ${Math.round(Number(features.semantic_similarity) * 100)}%`
  if (features.bidirectional_consistent) return '正向与反向结果一致'
  return pair.method
}

async function rerun() {
  busy.value = true
  alignmentProgress.value = 0
  message.value = '正在准备顺序对齐窗口…'
  undoStack.value = []
  redoStack.value = []
  await rerunAlignment(batchId.value)
  pollAlignment()
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

function onKeydown(event: KeyboardEvent) {
  if (!pairs.value.length || event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'z') {
    event.preventDefault(); void undoEdit()
  } else if ((event.ctrlKey || event.metaKey) && (event.key.toLowerCase() === 'y' || event.shiftKey && event.key.toLowerCase() === 'z')) {
    event.preventDefault(); void redoEdit()
  } else if (event.key.toLowerCase() === 'j') jumpSuspicious(1)
  else if (event.key.toLowerCase() === 'k') jumpSuspicious(-1)
  else if (event.code === 'Space') { event.preventDefault(); void toggleLock() }
  else if (event.altKey && event.key.toLowerCase() === 'm') { event.preventDefault(); void mergeNext() }
  else if (event.altKey && event.key.toLowerCase() === 's') { event.preventDefault(); void splitCurrent() }
  else if (event.altKey && event.key.toLowerCase() === 'r') { event.preventDefault(); void rerun() }
  else if (event.altKey && event.key === 'ArrowDown') { event.preventDefault(); void shift('target', 'current_into_next') }
  else if (event.altKey && event.key === 'ArrowUp') { event.preventDefault(); void shift('target', 'next_into_current') }
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
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <section class="alignment-editor" :class="{ 'is-compact': compact }">
    <header v-if="!compact">
      <div>
        <strong>双文档分块对齐</strong>
        <p>先对应段落与表格粗块，再在块内自动拆分和配对句段；疑难结果仍可人工微调。</p>
      </div>
    </header>

    <p v-if="error" class="form-message is-error">{{ error }}</p>

    <div v-if="!pairs.length" class="alignment-upload">
      <div class="alignment-upload__head">
        <span class="alignment-upload__head-icon"><Upload :size="20" /></span>
        <div>
          <strong>上传需要校对的两个文档</strong>
          <p>依次选择原文和译文。系统会识别段落、表格、重复页眉页脚和页码。</p>
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
        <span>支持 DOC、DOCX、TXT</span>
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

    <template v-else>
      <div class="alignment-toolbar">
        <button class="alignment-tool" :disabled="busy" title="导出当前完整对齐结果" @click="exportCsv"><Download :size="17" />导出</button>
        <button class="alignment-tool" :disabled="busy" title="进入校对大屏样式的人工对齐工作台" @click="openAlignmentWorkbench"><ArrowRight :size="17" />进入对齐工作台</button>
        <button class="alignment-tool" :disabled="busy" title="保留已锁定配对，重新对齐其余区间" @click="rerun"><RefreshCw :size="17" />对齐</button>
        <span class="alignment-toolbar__divider" />
        <button class="button" :disabled="busy || !undoStack.length" title="撤回上一次人工调整（Ctrl+Z）" @click="undoEdit"><Undo2 :size="14" />撤回</button>
        <button class="button" :disabled="busy || !redoStack.length" title="重新执行已撤回的调整（Ctrl+Y）" @click="redoEdit"><Redo2 :size="14" />前进</button>
        <span class="alignment-toolbar__divider" />
        <button class="button" :disabled="!canEditStructure || !activePair || activePair.src_indices.length + activePair.tgt_indices.length < 3" @click="splitCurrent"><Scissors :size="14" />拆分</button>
        <button class="button" :disabled="!canEditStructure || !canMergeNext" @click="mergeNext"><Merge :size="14" />合并下一项</button>
        <button class="button" :disabled="!canEditStructure || !canMergeSelected" @click="mergeSelected"><Merge :size="14" />合并所选（{{ selectedPairIds.size }}）</button>
        <span class="alignment-toolbar__divider" />
        <button class="button" :disabled="!canEditStructure || !canMergeNext" title="把下一项开头的译文移入当前项" @click="shift('target', 'next_into_current')"><ChevronUp :size="14" />上移</button>
        <button class="button" :disabled="!canEditStructure || !canMergeNext" title="把当前项末尾的译文移入下一项" @click="shift('target', 'current_into_next')"><ChevronDown :size="14" />下移</button>
        <button class="button" :disabled="!canEditStructure || !canMergeNext" title="把下一项开头的原文移入当前项" @click="shift('source', 'next_into_current')"><ChevronUp :size="14" />原文上移</button>
        <button class="button" :disabled="!canEditStructure || !canMergeNext" title="把当前项末尾的原文移入下一项" @click="shift('source', 'current_into_next')"><ChevronDown :size="14" />原文下移</button>
        <span class="alignment-toolbar__divider" />
        <button class="button" :disabled="!canEditStructure || !activePair" title="在当前项后插入一个空配对" @click="insertEmptyPair"><Plus :size="14" />插入</button>
        <button class="button" :disabled="!canEditStructure || !canDeleteCurrent" title="仅允许删除没有内容的空配对" @click="deleteEmptyPair"><Trash2 :size="14" />删除</button>
        <button class="button" @click="toggleLock"><Lock :size="14" />{{ activePair?.locked ? '解锁' : '锁定' }}</button>
        <span class="alignment-toolbar__divider" />
        <button class="button" :class="{ 'is-active': searchOpen }" title="在原文和译文中查找并定位，不修改客户文件内容" @click="searchOpen = !searchOpen"><Search :size="14" />查找定位</button>
        <form v-if="searchOpen" class="alignment-search" @submit.prevent="applySearch">
          <input v-model="searchKeyword" type="search" maxlength="200" placeholder="输入原文或译文" aria-label="查找原文或译文">
          <button class="button" type="submit">查找</button>
          <button class="button" type="button" title="清除查找" @click="clearSearch"><X :size="13" /></button>
        </form>
        <button class="button" :class="{ 'is-active': pairFilter === 'low' }" @click="setPairFilter(pairFilter === 'low' ? 'all' : 'low')">疑点 {{ lowConfidenceTotal }}</button>
        <button class="button" @click="jumpSuspicious(-1)"><ChevronUp :size="14" />上一疑点</button>
        <button class="button" @click="jumpSuspicious(1)"><ChevronDown :size="14" />下一疑点</button>
        <button v-if="busy" class="button button--danger" @click="cancel"><Square :size="14" />终止任务</button>
        <span class="alignment-toolbar__spacer" />
        <div class="alignment-toolbar__summary"><small>{{ pairRangeLabel }}</small></div>
        <button class="button" :disabled="pairPage <= 1" title="上一页" @click="changePairPage(-1)"><ArrowLeft :size="14" /></button>
        <button class="button" :disabled="pairPage >= pairPageCount" title="下一页" @click="changePairPage(1)"><ArrowRight :size="14" /></button>
      </div>

      <div class="alignment-grid">
        <div class="alignment-grid__head">
          <span>序号</span><span>原文</span><span>状态</span><span>译文</span>
        </div>
        <div
          v-for="(pair, index) in pairs"
          :key="pair.id"
          class="alignment-row"
          :class="[`is-${pair.confidence_level}`, { 'is-active': index === activeIndex, 'is-selected': selectedPairIds.has(pair.id) }]"
          role="button"
          tabindex="0"
          @click="activeIndex = index"
          @keydown.enter="activeIndex = index"
        >
          <label class="alignment-row__select" title="选择配对；按住 Shift 可连续选择" @click.stop>
            <input type="checkbox" :checked="selectedPairIds.has(pair.id)" @click="togglePairSelection(pair, $event)">
            <span>{{ pair.pair_order + 1 }}</span>
          </label>
          <div><small>S{{ pair.src_indices.join(', S') || '—' }}</small><p>{{ pair.source_text || '（增译，无对应原文）' }}</p></div>
          <div class="alignment-state" :title="JSON.stringify(pair.features)">
            <strong>{{ pair.locked ? '已锁定' : pair.confidence_level === 'low' ? '待复核' : '已对齐' }}</strong>
            <small>{{ Math.round(pair.confidence * 100) }}%</small>
            <small class="alignment-reason">{{ featureReason(pair) }}</small>
          </div>
          <div><small>T{{ pair.tgt_indices.join(', T') || '—' }}</small><p>{{ pair.target_text || '（漏译，无对应译文）' }}</p></div>
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
.alignment-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface-muted); }
.alignment-toolbar__summary { display: grid; white-space: nowrap; }
.alignment-toolbar__summary small { color: var(--ink-500); }
.alignment-toolbar__spacer { flex: 1 1 auto; }
.alignment-toolbar__divider { width: 1px; align-self: stretch; margin: 1px 2px; background: var(--line); }
.alignment-toolbar .button.is-active { border-color: var(--brand); background: rgba(15, 118, 110, .1); color: var(--brand); }
.alignment-tool { display: inline-flex; align-items: center; gap: 5px; min-height: 34px; padding: 5px 8px; border: 0; background: transparent; color: #0878c9; cursor: pointer; font-weight: 700; white-space: nowrap; }
.alignment-tool:hover:not(:disabled) { background: rgba(8, 120, 201, .08); }
.alignment-tool:disabled { cursor: not-allowed; opacity: .45; }
.alignment-search { display: inline-flex; align-items: center; gap: 5px; }
.alignment-search input { width: 190px; height: 32px; padding: 5px 9px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: inherit; outline: none; }
.alignment-search input:focus { border-color: var(--brand); box-shadow: 0 0 0 2px rgba(15, 118, 110, .12); }
.alignment-grid { display: grid; max-height: min(660px, calc(100vh - 250px)); overflow: auto; border: 1px solid var(--line); border-radius: 8px; }
.alignment-grid__head { position: sticky; z-index: 2; top: 0; display: grid; grid-template-columns: 66px minmax(0, 1fr) 100px minmax(0, 1fr); gap: 12px; padding: 9px 12px; border-bottom: 1px solid var(--line); background: var(--surface-muted); color: var(--ink-500); font-size: 12px; font-weight: 700; }
.alignment-row { position: relative; display: grid; grid-template-columns: 66px minmax(0, 1fr) 100px minmax(0, 1fr); gap: 12px; min-height: 76px; padding: 12px; text-align: left; color: inherit; background: transparent; border: 0; border-bottom: 1px solid var(--line); cursor: pointer; }
.alignment-row.is-active { outline: 2px solid var(--brand); outline-offset: -2px; }
.alignment-row.is-selected { box-shadow: inset 4px 0 var(--brand); }
.alignment-row.is-low { background: #fff7e8; }
.alignment-row.is-high { background: #f3fbf6; }
.alignment-row__select { display: flex; align-items: flex-start; gap: 8px; color: var(--ink-500); cursor: pointer; font-size: 12px; font-weight: 700; }
.alignment-row__select input { width: 16px; height: 16px; accent-color: var(--brand); cursor: pointer; }
.alignment-state { display: grid; align-content: center; gap: 2px; text-align: center; border-inline: 1px solid var(--line); }
.alignment-state strong { font-size: 12px; }
.alignment-state small { display: block; }
.alignment-reason { color: var(--ink-500); font-size: 10px; line-height: 1.2; }
@media (max-width: 900px) {
  .alignment-upload { padding: 14px; }
  .alignment-upload__files, .alignment-preview-config { grid-template-columns: 1fr; }
  .alignment-upload__direction { transform: rotate(90deg); }
  .alignment-upload__actions { align-items: stretch; flex-direction: column; }
  .alignment-preview-config > label { grid-column: auto; }
  .alignment-grid__head { display: none; }
  .alignment-row { grid-template-columns: 48px 1fr; }
  .alignment-state { border: 0; }
}
</style>
