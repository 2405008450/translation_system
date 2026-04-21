<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'

import type { Segment, TermMatch } from '../types/api'

const props = defineProps<{
  segment: Segment
  index: number
  active: boolean
  disabled?: boolean
  termMatches?: TermMatch[]
}>()

const emit = defineEmits<{
  update: [sentenceId: string, value: string]
  focus: [sentenceId: string]
  requestTMPanel: [sentenceId: string]
}>()

const statusClass = computed(() => `segment-row--${props.segment.status || 'none'}`)
const sourceClass = computed(() => `segment-row__tag--source-${props.segment.source || 'none'}`)

const targetEditor = ref<HTMLElement | null>(null)
const isComposing = ref(false)
const localText = ref(props.segment.target_text || '')
const isEditing = ref(false)
let highlightTimer: number | null = null

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
    :class="[statusClass, { 'is-active': active }]"
    :data-sentence-id="segment.sentence_id"
  >
    <div class="segment-row__head">
      <span class="segment-row__index">#{{ index + 1 }}</span>
      <span class="segment-row__tag segment-row__tag--status">{{ segment.status }}</span>
      <span class="segment-row__tag is-muted" :class="sourceClass">{{ segment.source }}</span>
      <span v-if="segment.score" class="segment-row__tag is-muted">
        {{ segment.score.toFixed(2) }}
      </span>
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
        <div
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
  </article>
</template>
