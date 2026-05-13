<script setup lang="ts">
import { computed } from 'vue'
import { FileText, Settings2 } from 'lucide-vue-next'

import type { DocumentParseMode, DocumentParseOptions, UploadCapability } from '../types/api'

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
const docxCapability = computed(() => props.capabilities.find((item) => item.extensions.includes('.docx')) ?? null)
const hasCapabilityData = computed(() => props.capabilities.length > 0)
const showWordSettings = computed(() => (
  (!hasCapabilityData.value || Boolean(docxCapability.value))
  && (!hasSelectedFiles.value || selectedExtensions.value.has('.docx'))
))

const visibleCapabilities = computed(() => {
  if (!hasSelectedFiles.value) {
    return props.capabilities.filter((item) => !item.extensions.includes('.docx'))
  }

  const selected = selectedExtensions.value
  return props.capabilities.filter((item) => (
    !item.extensions.includes('.docx')
    && item.extensions.some((extension) => selected.has(extension))
  ))
})

const selectedFileSummary = computed(() => {
  if (!hasSelectedFiles.value) {
    return '选择文件后会显示对应格式的真实解析能力。'
  }

  const labels = [
    ...(showWordSettings.value ? ['Word 文档'] : []),
    ...visibleCapabilities.value.map((item) => item.label),
  ]
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

const allWordTranslationOptions = computed(() => (
  props.parseOptions.include_headers_footers
  && props.parseOptions.include_footnotes_endnotes
  && props.parseOptions.include_comments
))

function updateOption(key: keyof DocumentParseOptions, value: boolean) {
  const nextOptions = {
    ...props.parseOptions,
    [key]: value,
  }
  emit('update:parseOptions', nextOptions)
  emit('update:modelValue', (
    nextOptions.include_headers_footers
    || nextOptions.include_footnotes_endnotes
    || nextOptions.include_comments
  ) ? 'full' : 'body_only')
}

function updateAllWordOptions(value: boolean) {
  const nextOptions = {
    ...props.parseOptions,
    include_headers_footers: value,
    include_footnotes_endnotes: value,
    include_comments: value,
  }
  emit('update:parseOptions', nextOptions)
  emit('update:modelValue', value ? 'full' : 'body_only')
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

    <section v-if="showWordSettings" class="doc-setting-card doc-setting-card--word">
      <div class="doc-type-icon">W</div>
      <label>
        <input
          type="checkbox"
          :checked="allWordTranslationOptions"
          @change="updateAllWordOptions(($event.target as HTMLInputElement).checked)"
        />
        全选可翻译范围
      </label>
      <label>
        <input
          type="checkbox"
          :checked="parseOptions.include_headers_footers"
          @change="updateOption('include_headers_footers', ($event.target as HTMLInputElement).checked)"
        />
        翻译页眉页脚
      </label>
      <label>
        <input
          type="checkbox"
          :checked="parseOptions.include_footnotes_endnotes"
          @change="updateOption('include_footnotes_endnotes', ($event.target as HTMLInputElement).checked)"
        />
        翻译脚注尾注
      </label>
      <label>
        <input
          type="checkbox"
          :checked="parseOptions.include_comments"
          @change="updateOption('include_comments', ($event.target as HTMLInputElement).checked)"
        />
        翻译批注
      </label>
      <label>
        <input
          type="checkbox"
          :checked="parseOptions.clean_format"
          @change="updateOption('clean_format', ($event.target as HTMLInputElement).checked)"
        />
        清洗格式
      </label>
    </section>

    <section class="document-parse-settings__formats">
      <div
        v-for="capability in visibleCapabilities"
        :key="capability.extensions.join(',')"
        class="document-parse-settings__format"
      >
        <div class="document-parse-settings__format-head">
          <span class="doc-file-icon">
            {{ capability.extensions[0].slice(1).toUpperCase() }}
          </span>
          <div>
            <strong>{{ capability.label }}</strong>
            <small>{{ formatExtensions(capability.extensions) }} · 最大 {{ capability.max_size_mb }} MB</small>
          </div>
        </div>
        <ul>
          <li v-for="feature in capability.features" :key="feature">{{ feature }}</li>
        </ul>
      </div>
    </section>
  </aside>

  <div v-else class="document-parse-settings document-parse-settings--inline">
    <div v-if="showWordSettings" class="document-parse-settings__inline-options">
      <label>
        <input
          type="checkbox"
          :checked="parseOptions.include_headers_footers"
          @change="updateOption('include_headers_footers', ($event.target as HTMLInputElement).checked)"
        />
        页眉页脚
      </label>
      <label>
        <input
          type="checkbox"
          :checked="parseOptions.include_comments"
          @change="updateOption('include_comments', ($event.target as HTMLInputElement).checked)"
        />
        批注
      </label>
      <label>
        <input
          type="checkbox"
          :checked="parseOptions.clean_format"
          @change="updateOption('clean_format', ($event.target as HTMLInputElement).checked)"
        />
        清洗格式
      </label>
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
  gap: 20px;
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

.doc-setting-card {
  display: grid;
  align-content: start;
  gap: 11px;
  min-width: 0;
}

.doc-type-icon,
.doc-file-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  margin-bottom: 10px;
  border-radius: 4px;
  color: #ffffff;
  font-weight: 700;
}

.doc-setting-card--word .doc-type-icon {
  background: #2b5aa8;
}

.doc-file-icon {
  width: 38px;
  height: 38px;
  flex: 0 0 auto;
  background: #5fa2f3;
  font-size: 12px;
}

.doc-setting-card label,
.document-parse-settings__inline-options label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #1976d2;
  font-size: 13px;
  line-height: 1.3;
}

.doc-setting-card input[type="checkbox"],
.document-parse-settings__inline-options input[type="checkbox"] {
  width: 14px;
  height: 14px;
  margin: 0;
  accent-color: #4596f6;
}

.document-parse-settings__formats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.document-parse-settings__format {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
}

.document-parse-settings__format-head {
  display: flex;
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.document-parse-settings__format strong,
.document-parse-settings__format small {
  display: block;
}

.document-parse-settings__format strong {
  font-size: 13px;
  font-weight: 600;
}

.document-parse-settings__format small {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.4;
}

.document-parse-settings__format ul {
  display: grid;
  gap: 5px;
  margin: 10px 0 0;
  padding-left: 18px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.45;
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
