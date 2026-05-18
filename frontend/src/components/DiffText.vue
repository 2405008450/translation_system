<script setup lang="ts">
import { computed } from 'vue'

import { computeDiff } from '../utils/textDiff'

const props = withDefaults(defineProps<{
  oldText: string
  newText: string
  emptyText?: string
}>(), {
  emptyText: '',
})

const segments = computed(() => computeDiff(props.oldText, props.newText))
const hasVisibleContent = computed(() => segments.value.some((segment) => segment.text.length > 0))
</script>

<template>
  <div class="diff-text">
    <template v-if="hasVisibleContent">
      <span
        v-for="(segment, index) in segments"
        :key="`${segment.type}-${index}`"
        class="diff-text__segment"
        :class="`diff-text__segment--${segment.type}`"
      >
        {{ segment.text }}
      </span>
    </template>
    <span v-else class="diff-text__empty">{{ emptyText }}</span>
  </div>
</template>

<style scoped>
.diff-text {
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  line-height: 1.6;
}

.diff-text__segment--equal {
  color: inherit;
}

.diff-text__segment--insert {
  text-decoration: underline;
  text-decoration-color: rgba(71, 153, 89, 0.6);
  text-underline-offset: 2px;
  color: #1b5e3b;
}

.diff-text__segment--delete {
  text-decoration: line-through;
  text-decoration-color: rgba(182, 72, 72, 0.6);
  color: #8b3232;
}

.diff-text__empty {
  color: var(--text-muted);
}
</style>
