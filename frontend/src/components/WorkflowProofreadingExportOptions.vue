<script setup lang="ts">
import type { ProofreadingExportFormat } from '../api/proofreading'

defineProps<{
  choices: Array<{ format: ProofreadingExportFormat; name: string; description: string; disabledReason?: string }>
  disabled: boolean
}>()
defineEmits<{ export: [format: ProofreadingExportFormat] }>()
</script>

<template>
  <div class="proofreading-export-options" data-testid="workflow-proofreading-export-options">
    <div class="proofreading-export-options__title">校对交付</div>
    <button v-for="choice in choices" :key="choice.format" type="button"
      class="proofreading-export-options__item" :data-testid="`export-${choice.format}`"
      :disabled="disabled || Boolean(choice.disabledReason)" :title="choice.disabledReason || choice.description"
      @click="$emit('export', choice.format)">
      <strong>{{ choice.name }}</strong>
      <span>{{ choice.disabledReason || choice.description }}</span>
    </button>
  </div>
</template>

<style scoped>
.proofreading-export-options__title { padding: 8px 12px; font-size: 12px; color: #64748b; }
.proofreading-export-options__item { display: flex; flex-direction: column; gap: 4px; width: 100%; padding: 10px 12px; border: 0; background: transparent; text-align: left; cursor: pointer; color: inherit; }
.proofreading-export-options__item:hover:not(:disabled) { background: #eff6ff; }
.proofreading-export-options__item:disabled { opacity: .55; cursor: not-allowed; }
.proofreading-export-options__item strong { font-size: 13px; }
.proofreading-export-options__item span { font-size: 12px; color: #64748b; line-height: 1.5; }
</style>
