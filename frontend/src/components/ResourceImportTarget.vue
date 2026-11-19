<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { NSelect, type SelectProps } from 'naive-ui'
import { useI18n } from 'vue-i18n'

import { formatLanguagePair } from '../constants/languages'

const props = defineProps<{
  label: string
  resources: {
    id: string
    name: string
    source_language: string | null
    target_language: string | null
    entry_count: number
  }[]
  createLabel: string
  fixedLabel?: string
  loading?: boolean
  disabled?: boolean
}>()

const model = defineModel<string>({ required: true })
const { t } = useI18n()
const show = ref(false)
const menuId = useId()
const options = computed(() => [
  { value: '', label: props.createLabel },
  ...props.resources.map((resource) => ({
    value: resource.id,
    label: `${resource.name}（${formatLanguagePair(resource.source_language, resource.target_language)} / ${t('resourceImport.targetEntryCount', { count: resource.entry_count })}）`,
  })),
])

const themeOverrides: SelectProps['themeOverrides'] = {
  peers: {
    InternalSelection: {
      heightMedium: '42px',
      borderRadius: '6px',
      color: 'var(--control-bg)',
      colorActive: 'var(--surface-panel)',
      colorDisabled: 'var(--surface-2)',
      textColor: 'var(--text-primary)',
      textColorDisabled: 'var(--text-muted)',
      placeholderColor: 'var(--text-placeholder)',
      arrowColor: 'var(--text-muted)',
      border: '1px solid var(--line-strong)',
      borderHover: '1px solid var(--brand-700)',
      borderActive: '1px solid var(--brand-700)',
      borderFocus: '1px solid var(--brand-700)',
    },
    InternalSelectMenu: {
      color: 'var(--surface-panel)',
      optionTextColor: 'var(--text-primary)',
      optionTextColorActive: 'var(--brand-700)',
      optionCheckColor: 'var(--brand-700)',
      optionColorPending: 'var(--surface-2)',
      optionColorActivePending: 'var(--surface-2)',
    },
  },
}

function closeMenuOnEscape(event: KeyboardEvent) {
  if (show.value) {
    event.stopPropagation()
    show.value = false
  }
}
</script>

<template>
  <div class="field resource-import-target" @keydown.esc.capture="closeMenuOnEscape">
    <span class="field__label">{{ label }}</span>
    <div v-if="fixedLabel" class="resource-import-target__current">
      {{ fixedLabel }}
    </div>
    <NSelect
      v-else
      v-model:value="model"
      v-model:show="show"
      :options="options"
      :theme-overrides="themeOverrides"
      :placeholder="t('resourceImport.searchTarget')"
      :loading="loading"
      :disabled="loading || disabled"
      :input-props="{ role: 'combobox', 'aria-label': label, 'aria-expanded': show, 'aria-autocomplete': 'list', 'aria-controls': show ? menuId : undefined }"
      :menu-props="{ id: menuId, role: 'listbox', 'aria-label': label }"
      :node-props="option => ({ role: 'option', 'aria-selected': option.value === model })"
      filterable
    >
      <template #empty>{{ t('resourceImport.noMatchingTarget') }}</template>
    </NSelect>
  </div>
</template>

<style scoped>
.resource-import-target {
  min-width: 0;
}

.resource-import-target__current {
  min-height: 42px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-2);
  color: var(--text-primary);
  font-weight: 600;
  overflow-wrap: anywhere;
}
</style>
