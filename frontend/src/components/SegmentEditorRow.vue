<script setup lang="ts">
import { computed } from 'vue'

import type { Segment } from '../types/api'

const props = defineProps<{
  segment: Segment
  index: number
  active: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{
  update: [sentenceId: string, value: string]
  focus: [sentenceId: string]
}>()

const statusClass = computed(() => `segment-row--${props.segment.status || 'none'}`)
const sourceClass = computed(() => `segment-row__tag--source-${props.segment.source || 'none'}`)
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
      <div class="segment-row__cell segment-row__cell--source">
        <div class="segment-row__label">原文</div>
        <div class="segment-row__text">{{ segment.display_text || segment.source_text }}</div>
      </div>

      <label class="segment-row__cell segment-row__cell--target">
        <span class="segment-row__label">译文</span>
        <textarea
          class="segment-row__textarea"
          :value="segment.target_text"
          :disabled="disabled"
          data-segment-target="true"
          :data-sentence-id="segment.sentence_id"
          spellcheck="false"
          @focus="emit('focus', segment.sentence_id)"
          @input="emit('update', segment.sentence_id, ($event.target as HTMLTextAreaElement).value)"
        />
      </label>
    </div>
  </article>
</template>
