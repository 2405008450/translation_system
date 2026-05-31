<script setup lang="ts">
import { computed } from 'vue'
import { FileText, Settings2 } from 'lucide-vue-next'

import type { DocumentParseMode, DocumentParseOptions, UploadCapability } from '../types/api'

type ParseOptionKey = keyof DocumentParseOptions
type UploadCapabilitySetting = NonNullable<UploadCapability['settings']>[number]

const props = withDefaults(defineProps<{
  modelValue: DocumentParseMode
  parseOptions: DocumentParseOptions
  capabilities: UploadCapability[]
  selectedFiles?: File[]
  loading?: boolean
  variant?: 'panel' | 'inline'
}>(), {
  selectedFiles: () => [],
  loading: false,
  variant: 'panel',
})

const emit = defineEmits<{
  'update:modelValue': [value: DocumentParseMode]
  'update:parseOptions': [value: DocumentParseOptions]
}>()

const optionDefaults: DocumentParseOptions = {
  include_headers_footers: true,
  include_footnotes_endnotes: true,
  include_comments: true,
  clean_format: false,
  preserve_hyperlinks: true,
  translate_code_blocks: true,
  extract_links: false,
  skip_non_translatable: true,
  xml_inline_elements_no_split: true,
  custom_parse_config: false,
  translate_idml_comments: false,
  translate_idml_hidden_layers: false,
  pptx_translate_comments: true,
  pptx_translate_notes: true,
  pptx_translate_document_properties: false,
  xlsx_translate_comments: true,
  xlsx_translate_drawing_text: true,
  xlsx_translate_sheet_names: false,
  xlsx_translate_hidden_content: true,
  xlsx_translate_document_properties: false,
  xlsx_translate_numeric_cells: true,
  xlsx_translate_date_cells: true,
  xlsx_translate_boolean_cells: true,
  xlsx_translate_formula_cells: false,
  xlsx_skip_fill_colors: [],
}

const wordTranslationOptionKeys: ParseOptionKey[] = [
  'include_headers_footers',
  'include_footnotes_endnotes',
  'include_comments',
]

const formatOrder = ['.docx', '.pptx', '.xlsx', '.md', '.dat', '.dxf', '.idml', '.xml', '.yaml', '.yml']

const selectedExtensions = computed(() => {
  const extensions = new Set<string>()
  props.selectedFiles.forEach((file) => {
    const dotIndex = file.name.lastIndexOf('.')
    if (dotIndex >= 0) {
      extensions.add(file.name.slice(dotIndex).toLowerCase())
    }
  })
  return extensions
})

const hasSelectedFiles = computed(() => props.selectedFiles.length > 0)
const hasCapabilityData = computed(() => props.capabilities.length > 0)

const visibleCapabilities = computed(() => {
  const capabilities = hasSelectedFiles.value
    ? props.capabilities.filter((item) => item.extensions.some((extension) => selectedExtensions.value.has(extension)))
    : props.capabilities.filter((item) => (item.settings?.length ?? 0) > 0)

  return [...capabilities].sort((left, right) => capabilityOrder(left) - capabilityOrder(right))
})

const selectedFileSummary = computed(() => {
  if (!hasSelectedFiles.value) {
    return '选择文件后会显示对应格式的真实解析能力。'
  }

  const labels = visibleCapabilities.value.map((item) => item.label)
  if (labels.length === 0) {
    return '当前文件格式不在后端任务上传能力中。'
  }
  return `当前文件将使用：${labels.join('、')}。`
})

const compactCapabilityText = computed(() => {
  if (props.loading) {
    return '正在读取后端解析能力...'
  }
  if (!props.capabilities.length) {
    return '暂未获取到后端解析能力，将使用上传接口校验。'
  }
  return selectedFileSummary.value
})

function capabilityOrder(capability: UploadCapability) {
  const indexes = capability.extensions
    .map((extension) => formatOrder.indexOf(extension))
    .filter((index) => index >= 0)
  return indexes.length ? Math.min(...indexes) : 999
}

function canonicalExtension(capability: UploadCapability) {
  const ordered = formatOrder.find((extension) => capability.extensions.includes(extension))
  return ordered || capability.extensions[0] || ''
}

function iconText(capability: UploadCapability) {
  const extension = canonicalExtension(capability)
  if (extension === '.docx') return 'W'
  if (extension === '.pptx') return 'P'
  if (extension === '.xlsx') return 'X'
  if (extension === '.dxf') return 'A'
  if (extension === '.idml') return 'ID'
  if (extension === '.markdown') return 'MD'
  return extension.replace('.', '').toUpperCase() || 'DOC'
}

function iconToneClass(capability: UploadCapability) {
  return `doc-file-icon--${canonicalExtension(capability).replace('.', '') || 'generic'}`
}

function settingsForCapability(capability: UploadCapability) {
  return capability.settings ?? []
}

function selectAllSettings(capability: UploadCapability) {
  const settings = settingsForCapability(capability)
  if (capability.extensions.includes('.docx')) {
    return settings.filter((setting) => setting.kind !== 'color_palette' && wordTranslationOptionKeys.includes(setting.id))
  }
  return settings.filter((setting) => setting.kind !== 'color_palette')
}

function selectableSettings(capability: UploadCapability) {
  return selectAllSettings(capability).filter((setting) => !setting.disabled)
}

function getOptionValue(key: ParseOptionKey) {
  return props.parseOptions[key] ?? optionDefaults[key]
}

function getBooleanOptionValue(key: ParseOptionKey) {
  return Boolean(getOptionValue(key))
}

function getColorOptionValue(key: ParseOptionKey) {
  const value = getOptionValue(key)
  return Array.isArray(value) ? value : []
}

function emitParseOptions(nextOptions: DocumentParseOptions) {
  emit('update:parseOptions', nextOptions)
}

function updateWordParseMode(nextOptions: DocumentParseOptions) {
  emit('update:modelValue', wordTranslationOptionKeys.some((key) => nextOptions[key]) ? 'full' : 'body_only')
}

function updateOption(setting: UploadCapabilitySetting, value: boolean) {
  if (setting.disabled || setting.kind === 'color_palette') {
    return
  }

  const key = setting.id
  const nextOptions = {
    ...props.parseOptions,
    [key]: value,
  }
  emitParseOptions(nextOptions)
  if (wordTranslationOptionKeys.includes(key)) {
    updateWordParseMode(nextOptions)
  }
}

function updateColorOption(setting: UploadCapabilitySetting, color: string, selected: boolean) {
  if (setting.disabled || setting.kind !== 'color_palette') {
    return
  }
  const key = setting.id
  const current = new Set(getColorOptionValue(key).map((item) => item.toUpperCase()))
  const normalizedColor = color.toUpperCase()
  if (selected) {
    current.add(normalizedColor)
  } else {
    current.delete(normalizedColor)
  }
  emitParseOptions({
    ...props.parseOptions,
    [key]: Array.from(current),
  })
}

function updateAllColorOptions(setting: UploadCapabilitySetting, selected: boolean) {
  if (setting.disabled || setting.kind !== 'color_palette') {
    return
  }
  const nextColors = selected ? (setting.options ?? []).map((option) => option.value.toUpperCase()) : []
  emitParseOptions({
    ...props.parseOptions,
    [setting.id]: nextColors,
  })
}

function updateCapabilityOptions(capability: UploadCapability, value: boolean) {
  const nextOptions = { ...props.parseOptions }
  const mutableOptions = nextOptions as unknown as Record<string, boolean | string[]>
  selectableSettings(capability).forEach((setting) => {
    mutableOptions[setting.id] = value
  })
  emitParseOptions(nextOptions)

  if (capability.extensions.includes('.docx')) {
    updateWordParseMode(nextOptions)
  }
}

function isCapabilityAllSelected(capability: UploadCapability) {
  const settings = selectAllSettings(capability)
  return settings.length > 0 && settings.every((setting) => getBooleanOptionValue(setting.id))
}

function isCapabilityPartiallySelected(capability: UploadCapability) {
  const settings = selectAllSettings(capability)
  return settings.some((setting) => getBooleanOptionValue(setting.id)) && !isCapabilityAllSelected(capability)
}

function showSelectAll(capability: UploadCapability) {
  return Boolean(capability.settings_select_all) && selectAllSettings(capability).length > 1
}

function selectAllLabel(capability: UploadCapability) {
  return capability.extensions.includes('.docx') ? '全选可翻译范围' : '全选'
}

function formatExtensions(extensions: string[]) {
  return extensions.map((extension) => extension.toUpperCase()).join(' / ')
}
</script>

<template>
  <aside
    v-if="variant === 'panel'"
    class="document-settings-panel document-parse-settings document-parse-settings--panel"
  >
    <header class="document-parse-settings__header">
      <div>
        <h2>文档设置</h2>
        <p>{{ compactCapabilityText }}</p>
      </div>
      <Settings2 :size="18" />
    </header>

    <section v-if="visibleCapabilities.length" class="document-parse-settings__formats">
      <div
        v-for="capability in visibleCapabilities"
        :key="capability.extensions.join(',')"
        class="document-parse-settings__format"
      >
        <span
          class="doc-file-icon"
          :class="iconToneClass(capability)"
          :title="`${capability.label}（${formatExtensions(capability.extensions)}）`"
        >
          {{ iconText(capability) }}
        </span>

        <div v-if="settingsForCapability(capability).length" class="document-parse-settings__option-list">
          <label v-if="showSelectAll(capability)" class="document-parse-settings__option">
            <input
              type="checkbox"
              :checked="isCapabilityAllSelected(capability)"
              :indeterminate="isCapabilityPartiallySelected(capability)"
              :disabled="selectableSettings(capability).length === 0"
              @change="updateCapabilityOptions(capability, ($event.target as HTMLInputElement).checked)"
            />
            <span>{{ selectAllLabel(capability) }}</span>
          </label>

          <template v-for="setting in settingsForCapability(capability)" :key="setting.id">
            <div
              v-if="setting.kind === 'color_palette'"
              class="document-parse-settings__color-setting"
              :class="{ 'is-disabled': setting.disabled }"
              :title="setting.description || setting.label"
            >
              <label class="document-parse-settings__option">
                <input
                  type="checkbox"
                  :checked="getColorOptionValue(setting.id).length > 0"
                  :disabled="setting.disabled"
                  @change="updateAllColorOptions(setting, ($event.target as HTMLInputElement).checked)"
                />
                <span>{{ setting.label }}</span>
              </label>
              <div class="document-parse-settings__swatches" role="group" :aria-label="setting.label">
                <button
                  v-for="option in setting.options || []"
                  :key="option.value"
                  class="document-parse-settings__swatch"
                  :class="{ 'is-selected': getColorOptionValue(setting.id).includes(option.value.toUpperCase()) }"
                  :style="{ backgroundColor: `#${option.value}` }"
                  type="button"
                  :disabled="setting.disabled"
                  :title="option.label"
                  @click="
                    updateColorOption(
                      setting,
                      option.value,
                      !getColorOptionValue(setting.id).includes(option.value.toUpperCase()),
                    )
                  "
                />
              </div>
            </div>

            <label
              v-else
              class="document-parse-settings__option"
              :class="{ 'is-disabled': setting.disabled }"
              :title="setting.description || setting.label"
            >
              <input
                type="checkbox"
                :checked="getBooleanOptionValue(setting.id)"
                :disabled="setting.disabled"
                @change="updateOption(setting, ($event.target as HTMLInputElement).checked)"
              />
              <span>{{ setting.label }}</span>
              <Settings2 v-if="setting.id === 'custom_parse_config'" :size="13" />
            </label>
          </template>
        </div>

        <ul v-else class="document-parse-settings__features">
          <li v-for="feature in capability.features" :key="feature">{{ feature }}</li>
        </ul>
      </div>
    </section>

    <p v-else-if="hasCapabilityData" class="document-parse-settings__empty">
      选择文件后显示对应格式的文档设置。
    </p>
  </aside>

  <div v-else class="document-parse-settings document-parse-settings--inline">
    <div v-if="visibleCapabilities.some((capability) => settingsForCapability(capability).length)" class="document-parse-settings__inline-options">
      <template v-for="capability in visibleCapabilities" :key="capability.extensions.join(',')">
        <label
          v-for="setting in settingsForCapability(capability).filter((item) => item.kind !== 'color_palette')"
          :key="`${capability.extensions.join(',')}:${setting.id}`"
          class="document-parse-settings__option"
          :class="{ 'is-disabled': setting.disabled }"
          :title="setting.description || `${capability.label}: ${setting.label}`"
        >
          <input
            type="checkbox"
            :checked="getBooleanOptionValue(setting.id)"
            :disabled="setting.disabled"
            @change="updateOption(setting, ($event.target as HTMLInputElement).checked)"
          />
          <span>{{ setting.label }}</span>
          <Settings2 v-if="setting.id === 'custom_parse_config'" :size="13" />
        </label>
      </template>
    </div>

    <div class="document-parse-settings__inline-summary">
      <FileText :size="14" />
      <span>{{ compactCapabilityText }}</span>
    </div>
  </div>
</template>

<style scoped>
.document-parse-settings {
  color: var(--text-primary);
}

.document-parse-settings--panel {
  min-height: 100%;
  display: grid;
  align-content: start;
  gap: 22px;
  padding: 18px 20px 28px;
  border-left: 1px solid #dbe3e1;
  background: #ffffff;
}

.document-parse-settings__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.document-parse-settings__header h2 {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 600;
}

.document-parse-settings__header p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.document-parse-settings__formats {
  display: grid;
  grid-template-columns: repeat(2, minmax(132px, 1fr));
  gap: 30px 38px;
}

.document-parse-settings__format {
  min-width: 0;
}

.doc-file-icon {
  position: relative;
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  margin-bottom: 14px;
  border-radius: 3px;
  color: #ffffff;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  box-shadow: inset 0 -12px 20px rgb(0 0 0 / 10%);
}

.doc-file-icon::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  border-top: 9px solid rgb(255 255 255 / 78%);
  border-left: 9px solid rgb(255 255 255 / 30%);
}

.doc-file-icon--md {
  background: linear-gradient(135deg, #2b8be9, #72b7ff);
}

.doc-file-icon--dat,
.doc-file-icon--yaml,
.doc-file-icon--yml {
  background: linear-gradient(135deg, #18a4a7, #45d0d1);
}

.doc-file-icon--dxf {
  background: linear-gradient(135deg, #c52026, #f07b66);
  font-size: 26px;
  font-family: Georgia, 'Times New Roman', serif;
}

.doc-file-icon--idml {
  background: linear-gradient(135deg, #8b145f, #d749a9);
}

.doc-file-icon--xml {
  background: linear-gradient(135deg, #f07c22, #ffb15d);
}

.doc-file-icon--docx {
  background: linear-gradient(135deg, #2b5aa8, #5fa2f3);
}

.doc-file-icon--pptx {
  background: linear-gradient(135deg, #c64a2b, #f18a5b);
}

.doc-file-icon--xlsx {
  background: linear-gradient(135deg, #18723b, #49b96d);
}

.doc-file-icon--generic {
  background: linear-gradient(135deg, #64748b, #94a3b8);
}

.document-parse-settings__option-list,
.document-parse-settings__features {
  display: grid;
  gap: 12px;
  margin: 0;
}

.document-parse-settings__option,
.document-parse-settings__inline-options label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: #1680db;
  font-size: 13px;
  line-height: 1.35;
}

.document-parse-settings__option span {
  min-width: 0;
}

.document-parse-settings__option svg {
  flex: 0 0 auto;
  color: #1f2937;
}

.document-parse-settings__option.is-disabled {
  color: #9aa6b2;
}

.document-parse-settings__color-setting {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.document-parse-settings__color-setting.is-disabled {
  opacity: 0.58;
}

.document-parse-settings__swatches {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-left: 22px;
}

.document-parse-settings__swatch {
  width: 18px;
  height: 18px;
  border: 2px solid #ffffff;
  border-radius: 2px;
  box-shadow: 0 0 0 1px #c7d0d8;
  cursor: pointer;
}

.document-parse-settings__swatch.is-selected {
  box-shadow: 0 0 0 2px #1f2937;
}

.document-parse-settings__swatch:disabled {
  cursor: not-allowed;
}

.document-parse-settings__option input[type="checkbox"],
.document-parse-settings__inline-options input[type="checkbox"] {
  width: 14px;
  height: 14px;
  margin: 0;
  flex: 0 0 auto;
  accent-color: #4596f6;
}

.document-parse-settings__features {
  padding-left: 18px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.45;
}

.document-parse-settings__empty {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.document-parse-settings--inline {
  display: grid;
  gap: 8px;
  min-width: 240px;
}

.document-parse-settings__inline-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
}

.document-parse-settings__inline-summary {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.45;
}

.document-parse-settings__inline-summary svg {
  flex: 0 0 auto;
  margin-top: 1px;
}

@media (max-width: 960px) {
  .document-parse-settings--panel {
    border-left: 0;
    border-top: 1px solid #dbe3e1;
  }
}

@media (max-width: 720px) {
  .document-parse-settings--panel {
    padding: 14px;
  }

  .document-parse-settings__formats {
    grid-template-columns: 1fr;
  }
}
</style>
