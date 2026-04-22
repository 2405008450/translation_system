<script setup lang="ts">
import { Check, ChevronDown, ChevronUp, Eye, EyeOff, Filter, X } from 'lucide-vue-next'
import { computed } from 'vue'

import type { RevisionAuthorSummary } from '../types/api'

const props = defineProps<{
  enabled: boolean
  authors: RevisionAuthorSummary[]
  selectedAuthorId: string | null
  currentIndex: number
  totalCount: number
}>()

const emit = defineEmits<{
  'update:enabled': [value: boolean]
  'update:selectedAuthorId': [value: string | null]
  acceptAll: []
  rejectAll: []
  navigatePrev: []
  navigateNext: []
}>()

const canNavigate = computed(() => props.totalCount > 0)
const navigationLabel = computed(() => {
  if (props.totalCount === 0) return '无修订'
  return `${props.currentIndex + 1}/${props.totalCount}`
})
</script>

<template>
  <div class="revision-toolbar">
    <template v-if="enabled">
      <div class="revision-toolbar__group">
        <div class="revision-toolbar__filter">
          <Filter :size="14" />
          <select
            class="revision-toolbar__select"
            :value="selectedAuthorId ?? ''"
            @change="emit('update:selectedAuthorId', ($event.target as HTMLSelectElement).value || null)"
          >
            <option value="">全部作者</option>
            <option
              v-for="author in authors"
              :key="author.id"
              :value="author.id"
            >
              {{ author.username }} ({{ author.count }})
            </option>
          </select>
        </div>
      </div>

      <div class="revision-toolbar__divider" />

      <div class="revision-toolbar__group revision-toolbar__group--nav">
        <button
          class="revision-toolbar__nav"
          type="button"
          title="上一处修订"
          :disabled="!canNavigate"
          @click="emit('navigatePrev')"
        >
          <ChevronUp :size="16" />
        </button>
        <span class="revision-toolbar__counter">{{ navigationLabel }}</span>
        <button
          class="revision-toolbar__nav"
          type="button"
          title="下一处修订"
          :disabled="!canNavigate"
          @click="emit('navigateNext')"
        >
          <ChevronDown :size="16" />
        </button>
      </div>

      <div class="revision-toolbar__divider" />

      <div class="revision-toolbar__group">
        <button
          class="revision-toolbar__action"
          type="button"
          title="拒绝所有修订"
          :disabled="totalCount === 0"
          @click="emit('rejectAll')"
        >
          <X :size="16" />
          <span>全部拒绝</span>
        </button>
        <button
          class="revision-toolbar__action"
          type="button"
          title="接受所有修订"
          :disabled="totalCount === 0"
          @click="emit('acceptAll')"
        >
          <Check :size="16" />
          <span>全部接受</span>
        </button>
      </div>

      <div class="revision-toolbar__divider" />
    </template>

    <div class="revision-toolbar__group">
      <button
        class="revision-toolbar__toggle"
        :class="{ 'is-active': enabled }"
        type="button"
        :title="enabled ? '关闭修订模式' : '开启修订模式'"
        @click="emit('update:enabled', !enabled)"
      >
        <Eye v-if="enabled" :size="16" />
        <EyeOff v-else :size="16" />
        <span>修订模式</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.revision-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.revision-toolbar__group {
  display: flex;
  align-items: center;
  gap: 6px;
}

.revision-toolbar__group--nav {
  gap: 4px;
}

.revision-toolbar__divider {
  width: 1px;
  height: 24px;
  background: var(--line-soft);
}

.revision-toolbar__toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(247, 251, 249, 0.94));
  color: var(--ink-700);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.revision-toolbar__toggle:hover {
  border-color: var(--brand-700);
  color: var(--brand-700);
}

.revision-toolbar__toggle.is-active {
  border-color: var(--brand-700);
  background: linear-gradient(180deg, rgba(220, 239, 232, 0.96), rgba(245, 250, 247, 0.96));
  color: var(--brand-700);
}

.revision-toolbar__action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(247, 251, 249, 0.94));
  color: var(--ink-700);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.revision-toolbar__action:hover:not(:disabled) {
  border-color: var(--brand-700);
  color: var(--brand-700);
}

.revision-toolbar__action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.revision-toolbar__nav {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(247, 251, 249, 0.94));
  color: var(--ink-700);
  cursor: pointer;
  transition: all 0.2s ease;
}

.revision-toolbar__nav:hover:not(:disabled) {
  border-color: var(--brand-700);
  color: var(--brand-700);
}

.revision-toolbar__nav:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.revision-toolbar__counter {
  min-width: 48px;
  text-align: center;
  font-size: 13px;
  color: var(--ink-700);
}

.revision-toolbar__filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 8px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(247, 251, 249, 0.94));
  color: var(--ink-500);
}

.revision-toolbar__select {
  border: none;
  background: transparent;
  color: var(--ink-700);
  font-size: 13px;
  cursor: pointer;
  outline: none;
}

.revision-toolbar__select:focus {
  outline: none;
}
</style>
