<script setup lang="ts">
import { Loader2 } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'

import { http } from '../api/http'
import type { SegmentHistoryResponse } from '../types/api'

const props = defineProps<{
  fileRecordId: string
  activeSentenceId: string | null
}>()

const emit = defineEmits<{
  close: []
}>()

const loading = ref(false)
const error = ref('')
const historyData = ref<SegmentHistoryResponse | null>(null)

// 使用 Myers diff 算法进行字符级别比较
function computeDiff(oldText: string, newText: string): { type: 'equal' | 'insert' | 'delete'; text: string }[] {
  if (oldText === newText) {
    return [{ type: 'equal', text: newText }]
  }
  if (!oldText) {
    return [{ type: 'insert', text: newText }]
  }
  if (!newText) {
    return [{ type: 'delete', text: oldText }]
  }

  const oldChars = [...oldText]
  const newChars = [...newText]
  const ops = myersDiff(oldChars, newChars)
  
  // 合并连续相同类型的操作
  const result: { type: 'equal' | 'insert' | 'delete'; text: string }[] = []
  for (const op of ops) {
    const last = result[result.length - 1]
    if (last && last.type === op.type) {
      last.text += op.text
    } else {
      result.push({ ...op })
    }
  }
  
  return result
}

function myersDiff(oldArr: string[], newArr: string[]): { type: 'equal' | 'insert' | 'delete'; text: string }[] {
  const n = oldArr.length
  const m = newArr.length
  const max = n + m
  const v: Record<number, number> = { 1: 0 }
  const trace: Record<number, number>[] = []

  for (let d = 0; d <= max; d++) {
    trace.push({ ...v })
    for (let k = -d; k <= d; k += 2) {
      let x: number
      if (k === -d || (k !== d && (v[k - 1] ?? 0) < (v[k + 1] ?? 0))) {
        x = v[k + 1] ?? 0
      } else {
        x = (v[k - 1] ?? 0) + 1
      }
      let y = x - k
      while (x < n && y < m && oldArr[x] === newArr[y]) {
        x++
        y++
      }
      v[k] = x
      if (x >= n && y >= m) {
        return backtrack(trace, oldArr, newArr)
      }
    }
  }
  return []
}

function backtrack(
  trace: Record<number, number>[],
  oldArr: string[],
  newArr: string[]
): { type: 'equal' | 'insert' | 'delete'; text: string }[] {
  let x = oldArr.length
  let y = newArr.length
  const ops: { type: 'equal' | 'insert' | 'delete'; text: string }[] = []

  for (let d = trace.length - 1; d >= 0; d--) {
    const v = trace[d]
    const k = x - y
    let prevK: number
    if (k === -d || (k !== d && (v[k - 1] ?? 0) < (v[k + 1] ?? 0))) {
      prevK = k + 1
    } else {
      prevK = k - 1
    }
    const prevX = v[prevK] ?? 0
    const prevY = prevX - prevK

    while (x > prevX && y > prevY) {
      ops.unshift({ type: 'equal', text: oldArr[x - 1] })
      x--
      y--
    }
    if (d > 0) {
      if (x === prevX) {
        ops.unshift({ type: 'insert', text: newArr[y - 1] })
        y--
      } else {
        ops.unshift({ type: 'delete', text: oldArr[x - 1] })
        x--
      }
    }
  }
  return ops
}

function getDiffWithPrevious(index: number): { type: 'equal' | 'insert' | 'delete'; text: string }[] | null {
  if (!historyData.value || index >= historyData.value.history.length - 1) {
    return null
  }
  const current = historyData.value.history[index]
  const previous = historyData.value.history[index + 1]
  return computeDiff(previous.target_text, current.target_text)
}

const historyWithDiff = computed(() => {
  if (!historyData.value) return []
  return historyData.value.history.map((item, index) => ({
    ...item,
    diff: getDiffWithPrevious(index),
  }))
})

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function getStatusLabel(status: string) {
  const map: Record<string, string> = {
    exact: '精确匹配',
    fuzzy: '模糊匹配',
    none: '未匹配',
    confirmed: '已确认',
  }
  return map[status] || status
}

async function loadHistory() {
  if (!props.activeSentenceId) {
    historyData.value = null
    return
  }

  loading.value = true
  error.value = ''
  try {
    const { data } = await http.get<SegmentHistoryResponse>(
      `/file-records/${props.fileRecordId}/segments/${props.activeSentenceId}/history`
    )
    historyData.value = data
  } catch (e) {
    error.value = '加载历史记录失败'
    historyData.value = null
  } finally {
    loading.value = false
  }
}

watch(() => props.activeSentenceId, () => {
  void loadHistory()
}, { immediate: true })
</script>

<template>
  <section class="panel history-panel">
    <div class="history-panel__header">
      <div>
        <div class="section-title section-title--tight">历史记录</div>
        <p class="panel-subtitle">查看句段的修改记录和差异对比。</p>
      </div>
      <button class="button preview-panel__close" type="button" @click="emit('close')">关闭</button>
    </div>

    <div v-if="!activeSentenceId" class="empty-state">
      请先选择一个句段查看历史记录。
    </div>

    <div v-else-if="loading" class="empty-state">
      <Loader2 class="lucide-spin" :size="24" />
      加载中...
    </div>

    <div v-else-if="error" class="empty-state">
      {{ error }}
    </div>

    <div v-else-if="!historyData || historyData.history.length === 0" class="empty-state">
      该句段暂无修改记录。
    </div>

    <div v-else class="history-panel__content">
      <div class="history-panel__source">
        <span class="history-panel__label">原文</span>
        <div class="history-panel__source-text">{{ historyData.source_text }}</div>
      </div>

      <div class="history-panel__list">
        <article
          v-for="(item, index) in historyWithDiff"
          :key="item.id"
          class="history-item"
        >
          <div class="history-item__head">
            <span class="history-item__index">#{{ historyData!.history.length - index }}</span>
            <span class="history-item__time">{{ formatDateTime(item.created_at) }}</span>
            <span class="history-item__tag">{{ getStatusLabel(item.status) }}</span>
            <span v-if="item.operator" class="history-item__user">{{ item.operator.username }}</span>
            <span v-if="item.confirm_type" class="history-item__type">{{ item.confirm_type }}</span>
          </div>

          <div class="history-item__target">
            <template v-if="item.diff && item.diff.length > 0">
              <span
                v-for="(part, partIndex) in item.diff"
                :key="partIndex"
                :class="{
                  'diff-insert': part.type === 'insert',
                  'diff-delete': part.type === 'delete',
                }"
              >{{ part.text }}</span>
            </template>
            <template v-else>
              {{ item.target_text }}
            </template>
          </div>
        </article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.history-panel {
  width: min(100%, 500px);
  min-height: 360px;
  display: grid;
  gap: 14px;
  background: linear-gradient(180deg, rgba(250, 252, 251, 0.98), rgba(240, 247, 244, 0.95));
}

.history-panel__header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.history-panel__content {
  display: grid;
  gap: 14px;
}

.history-panel__source {
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(247, 251, 251, 0.96), rgba(255, 255, 255, 0.94));
}

.history-panel__label {
  display: block;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--ink-500);
}

.history-panel__source-text {
  color: var(--ink-900);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

.history-panel__list {
  display: grid;
  gap: 10px;
  max-height: calc(100vh - 400px);
  overflow-y: auto;
  padding-right: 4px;
}

.history-item {
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.97), rgba(246, 251, 248, 0.94));
}

.history-item__head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}

.history-item__index {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  background: var(--brand-100);
  color: #0b6b5b;
}

.history-item__time {
  font-size: 12px;
  color: var(--ink-500);
}

.history-item__tag,
.history-item__type {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  font-size: 12px;
  background: var(--slate-100);
  color: #556d72;
}

.history-item__user {
  font-size: 12px;
  color: var(--ink-700);
  font-weight: 500;
}

.history-item__target {
  color: var(--ink-900);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.diff-insert {
  background: rgba(34, 197, 94, 0.2);
  color: #166534;
}

.diff-delete {
  background: rgba(239, 68, 68, 0.15);
  color: #991b1b;
  text-decoration: line-through;
}
</style>
