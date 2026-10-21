<script setup lang="ts">
import { computed, h, ref, useId } from 'vue'
import { NSelect, type SelectOption, type SelectProps } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { llmModelOptions } from '../constants/llm'
import { LLM_PRICING_SOURCE_URL, LLM_PRICING_UPDATED_AT, openRouterModelPricing } from '../constants/llmPricing'

type ModelChoice = { id: string; name: string } | { value: string; label: string }

const props = withDefaults(defineProps<{
  modelValue: string
  options?: ModelChoice[]
  disabled?: boolean
  ariaLabel?: string
  size?: 'small' | 'medium' | 'large'
}>(), { options: () => llmModelOptions, disabled: false, size: 'medium' })

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const { t } = useI18n()
const show = ref(false)
const menuId = useId()
const choices = computed(() => props.options.map(option => (
  'id' in option ? { value: option.id, label: option.name } : option
)))
const accessibleName = computed(() => props.ariaLabel || t('projectDetail.preTranslate.llm.model'))

function priceText(modelId: string) {
  if (!modelId) return t('llm.pricing.defaultModel')
  const price = openRouterModelPricing[modelId]
  return price
    ? t('llm.pricing.rate', { input: price.input, output: price.output })
    : t('llm.pricing.unavailable')
}

// 收起时仅显示模型名称；展开后，每个模型用第二行小字显示价格。
function renderLabel(option: SelectOption) {
  return h('div', { class: 'llm-model-select__option' }, [
    h('span', { class: 'llm-model-select__name' }, String(option.label)),
    h('small', { class: 'llm-model-select__price' }, priceText(String(option.value))),
  ])
}

const themeOverrides: SelectProps['themeOverrides'] = {
  peers: {
    InternalSelection: {
      color: 'var(--surface-panel)', colorActive: 'var(--surface-panel)',
      colorDisabled: 'var(--surface-muted)', textColor: 'var(--text-primary)',
      heightMedium: '42px',
      textColorDisabled: 'var(--text-muted)', placeholderColor: 'var(--text-placeholder)',
      border: '1px solid var(--line-strong)', borderRadius: '6px',
      borderHover: '1px solid var(--brand-500)', borderActive: '1px solid var(--brand-500)',
      borderFocus: '1px solid var(--brand-500)', arrowColor: 'var(--text-muted)',
      caretColor: 'var(--brand-500)',
    },
    InternalSelectMenu: {
      color: 'var(--llm-model-menu-bg)', optionTextColor: 'var(--text-primary)',
      optionTextColorActive: 'var(--brand-700)', optionCheckColor: 'var(--brand-500)',
      optionColorPending: 'var(--surface-2)', optionColorActive: 'var(--surface-2)',
      optionColorActivePending: 'var(--surface-2)', actionDividerColor: 'var(--line-soft)',
      optionHeightMedium: '58px', optionHeightSmall: '58px',
      height: 'min(290px, max(100px, calc(50vh - 110px)))',
    },
  },
}
</script>

<template>
  <NSelect
    class="llm-model-select"
    :value="modelValue"
    :options="choices"
    :disabled="disabled"
    :size="size"
    :show="show && !disabled"
    :render-label="renderLabel"
    :render-tag="({ option }) => String(option.label)"
    :theme-overrides="themeOverrides"
    :consistent-menu-width="false"
    :virtual-scroll="false"
    :input-props="{ 'aria-label': accessibleName, role: 'combobox', 'aria-expanded': show && !disabled, 'aria-autocomplete': 'list', 'aria-controls': menuId }"
    :menu-props="{ id: menuId, role: 'listbox', 'aria-label': accessibleName, style: { minWidth: 'min(360px, calc(100vw - 32px))', maxWidth: 'calc(100vw - 32px)' } }"
    :node-props="option => ({ role: 'option', 'aria-selected': option.value === modelValue })"
    filterable
    @update:show="show = $event"
    @update:value="emit('update:modelValue', String($event))"
    @keydown.esc.stop
  >
    <template #action>
      <div class="llm-model-select__footnote">
        <span>{{ t('llm.pricing.note') }}</span>
        <a :href="LLM_PRICING_SOURCE_URL" target="_blank" rel="noopener noreferrer">
          {{ t('llm.pricing.source', { date: LLM_PRICING_UPDATED_AT }) }}
        </a>
      </div>
    </template>
  </NSelect>
</template>

<style>
:root { --llm-model-menu-bg: #fff; }
[data-theme='dark'] { --llm-model-menu-bg: #161f27; }
.llm-model-select { min-width: 0; width: 100%; }
.llm-model-select__option { display: flex; flex-direction: column; gap: 3px; padding: 7px 0; white-space: normal; }
.llm-model-select__name { font-size: 14px; line-height: 20px; }
.llm-model-select__price { font-size: 11px; line-height: 17px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.llm-model-select__footnote { display: flex; flex-direction: column; gap: 3px; max-width: 360px; font-size: 11px; line-height: 17px; color: var(--text-muted); white-space: normal; }
.llm-model-select__footnote a { color: var(--text-secondary); text-decoration: underline; }
</style>
