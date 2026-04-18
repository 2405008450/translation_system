<script setup lang="ts">
import { computed } from 'vue'

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
}>()

const statusClass = computed(() => `segment-row--${props.segment.status || 'none'}`)
const sourceClass = computed(() => `segment-row__tag--source-${props.segment.source || 'none'}`)

const highlightedSourceText = computed(() => {
  const text = props.segment.display_text || props.segment.source_text
  if (!props.termMatches || props.termMatches.length === 0) {
    return text
  }

  // 按位置排序
  const sortedMatches = [...props.termMatches].sort((a, b) => a.start - b.start)
  
  let result = ''
  let lastEnd = 0

  for (const match of sortedMatches) {
    if (match.start >= lastEnd) {
      // 添加匹配前的普通文本
      result += escapeHtml(text.slice(lastEnd, match.start))
      // 添加高亮的术语
      result += `<mark class="term-highlight" title="${escapeHtml(match.target_text)}">${escapeHtml(text.slice(match.start, match.end))}</mark>`
      lastEnd = match.end
    }
  }
  
  // 添加剩余文本
  result += escapeHtml(text.slice(lastEnd))
  
  return result
})

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}
</script>

<template>
  <article class="segment-row" :class="[statusClass, { 'is-active': active }]">
    <div class="segment-row__head">
      <span class="segment-row__index">#{{ index + 1 }}</span>
      <span class="segment-row__tag segment-row__tag--status">{{ segment.status }}</span>
      <span class="segment-row__tag is-muted" :class="sourceClass">{{ segment.source }}</span>
      <span v-if="segment.score" class="segment-row__tag is-muted">
        {{ segment.score.toFixed(2) }}
      </span>
    </div>

    <div class="segment-row__grid">
      <div class="segment-row__cell segment-row__cell--source">
        <div class="segment-row__label">原文</div>
        <div 
          v-if="termMatches && termMatches.length > 0" 
          class="segment-row__text" 
          v-html="highlightedSourceText"
        />
        <div v-else class="segment-row__text">{{ segment.display_text || segment.source_text }}</div>
      </div>

      <label class="segment-row__cell segment-row__cell--target">
        <span class="segment-row__label">译文</span>
        <textarea
          class="segment-row__textarea"
          :value="segment.target_text"
          :disabled="disabled"
          spellcheck="false"
          @focus="emit('focus', segment.sentence_id)"
          @input="emit('update', segment.sentence_id, ($event.target as HTMLTextAreaElement).value)"
        />
      </label>
    </div>
  </article>
</template>
