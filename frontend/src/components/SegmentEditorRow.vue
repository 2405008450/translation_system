<script setup lang="ts">
import { Check, X } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'

import type { RevisionMark, Segment, TermMatch } from '../types/api'

const props = defineProps<{
  segment: Segment
  index: number
  active: boolean
  disabled?: boolean
  termMatches?: TermMatch[]
  revisionEnabled?: boolean
  revisionMarks?: RevisionMark[]
}>()

const emit = defineEmits<{
  update: [sentenceId: string, value: string]
  focus: [sentenceId: string]
  requestTMPanel: [sentenceId: string]
  acceptRevision: [sentenceId: string, markId: string]
  rejectRevision: [sentenceId: string, markId: string]
}>()

const statusClass = computed(() => `segment-row--${props.segment.status || 'none'}`)
const sourceClass = computed(() => `segment-row__tag--source-${props.segment.source || 'none'}`)

const targetEditor = ref<HTMLElement | null>(null)
const isComposing = ref(false)
const localText = ref(props.segment.target_text || '')
const isEditing = ref(false)
const hoveredMarkId = ref<string | null>(null)
const tooltipPosition = ref({ x: 0, y: 0 })
let highlightTimer: number | null = null

// 检查是否有修订标记
const hasRevisions = computed(() => 
  props.revisionEnabled && props.revisionMarks && props.revisionMarks.length > 0
)

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function highlightTerms(text: string, terms: { searchText: string; titleText: string }[]): string {
  if (!terms || terms.length === 0) {
    return escapeHtml(text)
  }

  const matches: { start: number; end: number; titleText: string }[] = []
  
  for (const term of terms) {
    if (!term.searchText) continue
    let pos = 0
    while ((pos = text.indexOf(term.searchText, pos)) !== -1) {
      matches.push({
        start: pos,
        end: pos + term.searchText.length,
        titleText: term.titleText
      })
      pos += term.searchText.length
    }
  }

  if (matches.length === 0) {
    return escapeHtml(text)
  }

  matches.sort((a, b) => a.start - b.start)
  const nonOverlapping: typeof matches = []
  for (const m of matches) {
    const last = nonOverlapping[nonOverlapping.length - 1]
    if (!last || m.start >= last.end) {
      nonOverlapping.push(m)
    }
  }

  let result = ''
  let lastEnd = 0
  for (const m of nonOverlapping) {
    result += escapeHtml(text.slice(lastEnd, m.start))
    result += `<mark class="term-highlight" title="${escapeHtml(m.titleText)}">${escapeHtml(text.slice(m.start, m.end))}</mark>`
    lastEnd = m.end
  }
  result += escapeHtml(text.slice(lastEnd))

  return result
}

const highlightedSourceText = computed(() => {
  const displayText = props.segment.display_text || props.segment.source_text
  if (!props.termMatches || props.termMatches.length === 0) {
    return escapeHtml(displayText)
  }
  const terms = props.termMatches.map(m => ({
    searchText: m.source_text,
    titleText: m.target_text
  }))
  return highlightTerms(displayText, terms)
})

const highlightedTargetText = computed(() => {
  const text = localText.value || ''
  if (!props.termMatches || props.termMatches.length === 0 || !text) {
    return escapeHtml(text)
  }
  const terms = props.termMatches.map(m => ({
    searchText: m.target_text,
    titleText: m.source_text
  }))
  return highlightTerms(text, terms)
})

// 渲染带修订标记的文本
const revisionRenderedText = computed(() => {
  if (!hasRevisions.value || !props.revisionMarks) {
    return highlightedTargetText.value
  }
  
  const text = localText.value || ''
  const marks = [...props.revisionMarks].sort((a, b) => a.position - b.position)
  
  let result = ''
  let lastEnd = 0
  
  for (const mark of marks) {
    // 添加标记前的普通文本
    if (mark.position > lastEnd) {
      result += escapeHtml(text.slice(lastEnd, mark.position))
    }
    
    if (mark.type === 'insert') {
      result += `<ins class="revision-mark revision-mark--insert" data-mark-id="${mark.id}" data-author="${escapeHtml(mark.author_username || '')}" data-time="${mark.created_at}">${escapeHtml(mark.text)}</ins>`
      lastEnd = mark.position + mark.length
    } else if (mark.type === 'delete') {
      result += `<del class="revision-mark revision-mark--delete" data-mark-id="${mark.id}" data-author="${escapeHtml(mark.author_username || '')}" data-time="${mark.created_at}">${escapeHtml(mark.text)}</del>`
      lastEnd = mark.position
    }
  }
  
  // 添加剩余文本
  if (lastEnd < text.length) {
    result += escapeHtml(text.slice(lastEnd))
  }
  
  return result
})

// 获取当前悬停的修订标记信息
const hoveredMark = computed(() => {
  if (!hoveredMarkId.value || !props.revisionMarks) return null
  return props.revisionMarks.find(m => m.id === hoveredMarkId.value) || null
})

function formatRevisionTime(isoString: string): string {
  return new Date(isoString).toLocaleString('zh-CN', { hour12: false })
}

function handleRevisionMouseEnter(event: MouseEvent) {
  const target = event.target as HTMLElement
  const markId = target.dataset.markId
  if (markId) {
    hoveredMarkId.value = markId
    const rect = target.getBoundingClientRect()
    tooltipPosition.value = {
      x: rect.left + rect.width / 2,
      y: rect.top - 8
    }
  }
}

function handleRevisionMouseLeave() {
  hoveredMarkId.value = null
}

function handleAcceptRevision(markId: string) {
  emit('acceptRevision', props.segment.sentence_id, markId)
}

function handleRejectRevision(markId: string) {
  emit('rejectRevision', props.segment.sentence_id, markId)
}

function handleTargetFocus() {
  emit('focus', props.segment.sentence_id)
  emit('requestTMPanel', props.segment.sentence_id)
}

function getCaretPosition(element: HTMLElement): number {
  const sel = window.getSelection()
  if (!sel || sel.rangeCount === 0) return 0
  
  const range = sel.getRangeAt(0)
  const preRange = range.cloneRange()
  preRange.selectNodeContents(element)
  preRange.setEnd(range.startContainer, range.startOffset)
  return preRange.toString().length
}

function setCaretPosition(element: HTMLElement, offset: number) {
  const sel = window.getSelection()
  if (!sel) return
  
  const range = document.createRange()
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
  
  let currentOffset = 0
  let node: Text | null = null
  
  while (walker.nextNode()) {
    node = walker.currentNode as Text
    if (currentOffset + node.length >= offset) {
      range.setStart(node, offset - currentOffset)
      range.collapse(true)
      sel.removeAllRanges()
      sel.addRange(range)
      return
    }
    currentOffset += node.length
  }
  
  // 如果没找到，放到末尾
  if (node) {
    range.setStart(node, node.length)
    range.collapse(true)
    sel.removeAllRanges()
    sel.addRange(range)
  }
}

function handleInput() {
  if (isComposing.value || !targetEditor.value) return
  
  const text = targetEditor.value.innerText || ''
  localText.value = text
  emit('update', props.segment.sentence_id, text)
  
  // 输入时标记为编辑状态，延迟更新高亮
  isEditing.value = true
  if (highlightTimer) {
    window.clearTimeout(highlightTimer)
  }
  highlightTimer = window.setTimeout(() => {
    isEditing.value = false
    if (targetEditor.value) {
      const caretPos = getCaretPosition(targetEditor.value)
      targetEditor.value.innerHTML = highlightedTargetText.value
      setCaretPosition(targetEditor.value, caretPos)
    }
  }, 500)
}

function handleCompositionStart() {
  isComposing.value = true
}

function handleCompositionEnd() {
  isComposing.value = false
  handleInput()
}

function handleBlur() {
  // 失焦时清除编辑状态并更新高亮
  isEditing.value = false
  if (highlightTimer) {
    window.clearTimeout(highlightTimer)
    highlightTimer = null
  }
  if (!targetEditor.value) return
  targetEditor.value.innerHTML = highlightedTargetText.value
}

// 监听外部数据变化
watch(() => props.segment.target_text, (newVal) => {
  if (newVal !== localText.value) {
    localText.value = newVal || ''
    // 只在非聚焦状态下更新 DOM
    if (targetEditor.value && document.activeElement !== targetEditor.value) {
      targetEditor.value.innerHTML = highlightedTargetText.value
    }
  }
})

// 监听术语变化，更新高亮
watch(() => props.termMatches, () => {
  if (targetEditor.value && !isEditing.value) {
    const caretPos = document.activeElement === targetEditor.value ? getCaretPosition(targetEditor.value) : -1
    targetEditor.value.innerHTML = highlightedTargetText.value
    if (caretPos >= 0) {
      setCaretPosition(targetEditor.value, caretPos)
    }
  }
}, { deep: true })

// 初始化时设置内容
watch(targetEditor, (el) => {
  if (el) {
    el.innerHTML = highlightedTargetText.value
  }
}, { immediate: true })
</script>

<template>
  <article
    class="segment-row"
    :class="[statusClass, { 'is-active': active, 'has-revisions': hasRevisions }]"
    :data-sentence-id="segment.sentence_id"
  >
    <div class="segment-row__head">
      <span class="segment-row__index">#{{ index + 1 }}</span>
      <span class="segment-row__tag segment-row__tag--status">{{ segment.status }}</span>
      <span class="segment-row__tag is-muted" :class="sourceClass">{{ segment.source }}</span>
      <span v-if="segment.score" class="segment-row__tag is-muted">
        {{ segment.score.toFixed(2) }}
      </span>
      
      <!-- 修订操作按钮 -->
      <template v-if="hasRevisions && revisionMarks && revisionMarks.length > 0">
        <div class="segment-row__revision-actions">
          <button
            class="segment-row__revision-btn segment-row__revision-btn--accept"
            type="button"
            title="接受此句段的修订"
            @click="handleAcceptRevision(revisionMarks[0].id)"
          >
            <Check :size="14" />
          </button>
          <button
            class="segment-row__revision-btn segment-row__revision-btn--reject"
            type="button"
            title="拒绝此句段的修订"
            @click="handleRejectRevision(revisionMarks[0].id)"
          >
            <X :size="14" />
          </button>
        </div>
      </template>
    </div>

    <div class="segment-row__grid">
      <div 
        class="segment-row__cell segment-row__cell--source"
        @click="emit('focus', segment.sentence_id)"
      >
        <div class="segment-row__label">原文</div>
        <div 
          v-if="termMatches && termMatches.length > 0" 
          class="segment-row__text" 
          v-html="highlightedSourceText"
        />
        <div v-else class="segment-row__text">{{ segment.display_text || segment.source_text }}</div>
      </div>

      <div class="segment-row__cell segment-row__cell--target">
        <span class="segment-row__label">译文</span>
        <!-- 修订模式下显示只读的修订标记 -->
        <div
          v-if="hasRevisions"
          class="segment-row__revision-view"
          v-html="revisionRenderedText"
          @mouseover="handleRevisionMouseEnter"
          @mouseout="handleRevisionMouseLeave"
        />
        <!-- 正常编辑模式 -->
        <div
          v-else
          ref="targetEditor"
          class="segment-row__editor"
          :class="{ 'is-disabled': disabled }"
          :contenteditable="!disabled"
          data-segment-target="true"
          :data-sentence-id="segment.sentence_id"
          spellcheck="false"
          @focus="handleTargetFocus"
          @blur="handleBlur"
          @input="handleInput"
          @compositionstart="handleCompositionStart"
          @compositionend="handleCompositionEnd"
        />
      </div>
    </div>
    
    <!-- 修订信息 Tooltip -->
    <Teleport to="body">
      <div
        v-if="hoveredMark"
        class="revision-tooltip"
        :style="{
          left: `${tooltipPosition.x}px`,
          top: `${tooltipPosition.y}px`
        }"
      >
        <span class="revision-tooltip__author">{{ hoveredMark.author_username || '未知用户' }}</span>
        <span class="revision-tooltip__time">{{ formatRevisionTime(hoveredMark.created_at) }}</span>
      </div>
    </Teleport>
  </article>
</template>
