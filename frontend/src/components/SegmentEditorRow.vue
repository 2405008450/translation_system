<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'

import DiffText from './DiffText.vue'

import { getSegmentSourceMeta, getSegmentStatusMeta } from '../constants/status'
import type { Segment, SegmentRevisionEntry, TermEntryRecord } from '../types/api'

const props = withDefaults(defineProps<{
  segment: Segment
  index: number
  active: boolean
  disabled?: boolean
  pendingRevision?: SegmentRevisionEntry | null
  revisionBusy?: boolean
  matchedTerms?: TermEntryRecord[]
}>(), {
  disabled: false,
  pendingRevision: null,
  revisionBusy: false,
  matchedTerms: () => [],
})

const emit = defineEmits<{
  update: [sentenceId: string, value: string]
  focus: [sentenceId: string]
  activateTarget: [sentenceId: string]
  acceptRevision: [revisionId: string]
  rejectRevision: [revisionId: string]
}>()

const editorRef = ref<HTMLDivElement | null>(null)
const isFocused = ref(false)

const statusClass = computed(() => `segment-row--${props.segment.status || 'none'}`)
const sourceClass = computed(() => `segment-row__tag--source-${props.segment.source || 'none'}`)
const statusMeta = computed(() => getSegmentStatusMeta(props.segment.status))
const sourceMeta = computed(() => getSegmentSourceMeta(props.segment.source))
const revisionSourceMeta = computed(() => getSegmentSourceMeta(props.pendingRevision?.source || 'manual'))
const hasPendingRevision = computed(() => Boolean(props.pendingRevision))
const scorePercent = computed(() => {
  if (!props.segment.score || props.segment.score <= 0) return null
  return Math.round(props.segment.score * 100)
})

// 通用的文本高亮函数
function highlightText(
  text: string,
  terms: TermEntryRecord[],
  field: 'source_text' | 'target_text'
): Array<{ text: string; highlight: boolean }> | null {
  if (!text || terms.length === 0) {
    return null
  }

  // 按长度降序排列，优先匹配长术语
  const sortedTerms = [...terms].sort(
    (a, b) => b[field].length - a[field].length
  )

  // 找出所有匹配位置
  const matches: Array<{ start: number; end: number }> = []
  const lowerText = text.toLowerCase()

  for (const term of sortedTerms) {
    const termText = term[field]
    if (!termText) continue
    const lowerTerm = termText.toLowerCase()
    let pos = 0
    while ((pos = lowerText.indexOf(lowerTerm, pos)) !== -1) {
      // 检查是否与已有匹配重叠
      const overlaps = matches.some(
        (m) => !(pos + lowerTerm.length <= m.start || pos >= m.end)
      )
      if (!overlaps) {
        matches.push({ start: pos, end: pos + lowerTerm.length })
      }
      pos += 1
    }
  }

  if (matches.length === 0) {
    return null
  }

  // 按位置排序
  matches.sort((a, b) => a.start - b.start)

  // 构建分段
  const segments: Array<{ text: string; highlight: boolean }> = []
  let lastEnd = 0

  for (const match of matches) {
    if (match.start > lastEnd) {
      segments.push({ text: text.slice(lastEnd, match.start), highlight: false })
    }
    segments.push({ text: text.slice(match.start, match.end), highlight: true })
    lastEnd = match.end
  }

  if (lastEnd < text.length) {
    segments.push({ text: text.slice(lastEnd), highlight: false })
  }

  return segments
}

// 高亮原文中匹配的术语
const highlightedSourceText = computed(() => {
  const text = props.segment.display_text || props.segment.source_text
  return highlightText(text, props.matchedTerms || [], 'source_text')
})

// 高亮译文中匹配的术语
const highlightedTargetText = computed(() => {
  const text = props.segment.target_text || ''
  return highlightText(text, props.matchedTerms || [], 'target_text')
})

// 生成带高亮的 HTML
const targetHtmlContent = computed(() => {
  const segments = highlightedTargetText.value
  if (!segments) {
    return escapeHtml(props.segment.target_text || '')
  }
  return segments
    .map((seg) =>
      seg.highlight
        ? `<mark class="segment-row__term-highlight">${escapeHtml(seg.text)}</mark>`
        : escapeHtml(seg.text)
    )
    .join('')
})

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// 保存和恢复光标位置
function saveCaretPosition(el: HTMLElement): number {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) return 0
  
  const range = selection.getRangeAt(0)
  const preCaretRange = range.cloneRange()
  preCaretRange.selectNodeContents(el)
  preCaretRange.setEnd(range.startContainer, range.startOffset)
  return preCaretRange.toString().length
}

function restoreCaretPosition(el: HTMLElement, offset: number) {
  const selection = window.getSelection()
  if (!selection) return

  const range = document.createRange()
  let currentOffset = 0
  let found = false

  function traverse(node: Node): boolean {
    if (node.nodeType === Node.TEXT_NODE) {
      const textLength = node.textContent?.length || 0
      if (currentOffset + textLength >= offset) {
        range.setStart(node, offset - currentOffset)
        range.collapse(true)
        return true
      }
      currentOffset += textLength
    } else {
      for (const child of Array.from(node.childNodes)) {
        if (traverse(child)) return true
      }
    }
    return false
  }

  found = traverse(el)
  if (!found) {
    // 如果没找到，放到末尾
    range.selectNodeContents(el)
    range.collapse(false)
  }

  selection.removeAllRanges()
  selection.addRange(range)
}

function handleFocus() {
  isFocused.value = true
  emit('focus', props.segment.sentence_id)
}

function handleBlur() {
  isFocused.value = false
}

function handleClick() {
  emit('activateTarget', props.segment.sentence_id)
}

function handleInput() {
  if (!editorRef.value) return
  
  // 获取纯文本内容
  const text = editorRef.value.textContent || ''
  emit('update', props.segment.sentence_id, text)
  
  // 保存光标位置
  const caretPos = saveCaretPosition(editorRef.value)
  
  // 更新高亮 HTML
  nextTick(() => {
    if (editorRef.value && isFocused.value) {
      const segments = highlightText(text, props.matchedTerms || [], 'target_text')
      if (segments) {
        editorRef.value.innerHTML = segments
          .map((seg) =>
            seg.highlight
              ? `<mark class="segment-row__term-highlight">${escapeHtml(seg.text)}</mark>`
              : escapeHtml(seg.text)
          )
          .join('')
      } else {
        editorRef.value.innerHTML = escapeHtml(text)
      }
      // 恢复光标位置
      restoreCaretPosition(editorRef.value, caretPos)
    }
  })
}

// 监听外部数据变化，更新编辑器内容
watch(
  () => props.segment.target_text,
  (newText) => {
    if (!isFocused.value && editorRef.value) {
      // 非聚焦状态，更新带高亮的 HTML
      editorRef.value.innerHTML = targetHtmlContent.value
    }
  }
)

// 监听高亮内容变化
watch(
  targetHtmlContent,
  (html) => {
    if (!isFocused.value && editorRef.value) {
      editorRef.value.innerHTML = html
    }
  }
)
</script>

<template>
  <article
    class="segment-row"
    :class="[statusClass, { 'is-active': active, 'has-pending-revision': hasPendingRevision }]"
    :id="`segment-${segment.sentence_id}`"
    :data-sentence-id="segment.sentence_id"
    role="group"
    :aria-label="`segment ${index + 1}`"
  >
    <div class="segment-row__meta">
      <span class="segment-row__index">#{{ index + 1 }}</span>
      <span class="segment-row__tag segment-row__tag--status">{{ statusMeta.label }}</span>
      <span class="segment-row__tag is-muted" :class="sourceClass">{{ sourceMeta.label }}</span>
      <span v-if="scorePercent !== null" class="segment-row__tag segment-row__tag--score">
        {{ scorePercent }}%
      </span>
    </div>

    <div class="segment-row__cell segment-row__cell--source">
      <div class="segment-row__text">
        <template v-if="highlightedSourceText">
          <template v-for="(seg, idx) in highlightedSourceText" :key="idx">
            <mark v-if="seg.highlight" class="segment-row__term-highlight">{{ seg.text }}</mark>
            <template v-else>{{ seg.text }}</template>
          </template>
        </template>
        <template v-else>{{ segment.display_text || segment.source_text }}</template>
      </div>
    </div>

    <div class="segment-row__cell segment-row__cell--target" :class="{ 'is-pending': hasPendingRevision }">
      <div v-if="hasPendingRevision" class="segment-row__pending-row">
        <span class="segment-row__badge segment-row__badge--pending">待审核</span>
      </div>
      <div v-if="pendingRevision" class="segment-row__revision-panel">
        <div class="segment-row__revision-head">
          <strong>修订对比</strong>
          <span>{{ revisionSourceMeta.label }}</span>
        </div>
        <DiffText
          :old-text="pendingRevision.before_text"
          :new-text="pendingRevision.after_text"
          empty-text="空"
        />
        <div class="segment-row__revision-actions">
          <button
            class="button segment-row__revision-button"
            type="button"
            :disabled="disabled || revisionBusy"
            @click.stop="emit('acceptRevision', pendingRevision.id)"
          >
            接受
          </button>
          <button
            class="button segment-row__revision-button segment-row__revision-button--danger"
            type="button"
            :disabled="disabled || revisionBusy"
            @click.stop="emit('rejectRevision', pendingRevision.id)"
          >
            拒绝
          </button>
        </div>
      </div>
      <div
        ref="editorRef"
        class="segment-row__editor"
        :class="{ 'is-focused': isFocused }"
        :contenteditable="!disabled"
        tabindex="0"
        data-segment-target="true"
        :data-sentence-id="segment.sentence_id"
        :aria-label="`translation for segment ${index + 1}`"
        spellcheck="false"
        @focus="handleFocus"
        @blur="handleBlur"
        @click="handleClick"
        @input="handleInput"
        v-html="targetHtmlContent"
      />
    </div>
  </article>
</template>

<style scoped>
.segment-row__pending-row,
.segment-row__revision-head,
.segment-row__revision-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.segment-row__pending-row {
  justify-content: flex-end;
  min-height: 18px;
}

.segment-row__badge {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: rgba(216, 183, 78, 0.18);
  color: #8a6700;
  font-size: 11px;
}

.segment-row__badge--pending {
  background: rgba(13, 122, 104, 0.14);
  color: #0b6b5b;
}

.segment-row__cell--target.is-pending {
  border-color: rgba(13, 122, 104, 0.35);
  box-shadow: inset 2px 0 0 rgba(13, 122, 104, 0.42);
}

.segment-row__revision-panel {
  display: grid;
  gap: 6px;
  padding: 6px 8px;
  border: 1px solid rgba(13, 122, 104, 0.16);
  border-radius: 6px;
  background: rgba(243, 250, 247, 0.96);
}

.segment-row__revision-head {
  color: var(--text-muted);
  font-size: 12px;
}

.segment-row__revision-head strong {
  color: var(--text-primary);
  font-size: 13px;
}

.segment-row__revision-actions {
  justify-content: flex-end;
}

.segment-row__revision-button {
  min-height: 28px;
  padding: 5px 10px;
  font-size: 12px;
  box-shadow: none;
}

.segment-row__revision-button--danger {
  border-color: rgba(194, 59, 63, 0.28);
  color: #a43a3d;
}

.segment-row__tag--score {
  background: rgba(216, 183, 78, 0.18);
  color: #8a6700;
}

.segment-row__term-highlight {
  background: rgba(216, 183, 78, 0.28);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  font-weight: 500;
}

/* 穿透 scoped 样式，让 v-html 插入的 mark 标签也能应用样式 */
.segment-row__editor :deep(.segment-row__term-highlight) {
  background: rgba(216, 183, 78, 0.28);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  font-weight: 500;
}

.segment-row__editor {
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 64px;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  font-size: 13px;
  line-height: 1.45;
  color: var(--text-primary);
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  overflow: auto;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.segment-row__editor:focus,
.segment-row__editor.is-focused {
  border-color: var(--brand-700);
  background: var(--surface-panel);
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.12);
}

.segment-row__editor[contenteditable="false"] {
  background: var(--surface-muted);
  cursor: not-allowed;
  opacity: 0.7;
}

.segment-row__editor:empty::before {
  content: '';
  color: var(--text-placeholder);
}
</style>
