<script setup lang="ts">
import axios from 'axios'
import { FileText, Loader2, Upload } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import { usePageHeader } from '../composables/usePageHeader'
import { useToast } from '../composables/useToast'
import { downloadBlob, resolveDownloadFilename } from '../utils/download'

type ScopeWidth = '全部' | '半角' | '全角'
type ScopeShape = '全部' | '直引号' | '弯引号'
type TargetWidth = '半角' | '全角'
type TargetShape = '弯引号' | '直引号'

const { t } = useI18n()
const toast = useToast()

const SUPPORTED_EXTS = [
  '.txt', '.md', '.markdown', '.srt', '.rtf',
  '.docx', '.xlsx', '.pptx',
  '.html', '.htm',
]
const ACCEPT_ATTR = SUPPORTED_EXTS.join(',')

const fileInputRef = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const scopeWidth = ref<ScopeWidth>('全部')
const scopeShape = ref<ScopeShape>('全部')
const targetWidth = ref<TargetWidth>('半角')
const targetShape = ref<TargetShape>('弯引号')
const converting = ref(false)
const pageMessage = ref('')

usePageHeader(() => ({
  title: t('pages.quoteConverter.title'),
  description: t('pages.quoteConverter.description'),
  breadcrumbs: [
    { label: t('quoteConverter.breadcrumbTools') },
    { label: t('pages.quoteConverter.title') },
  ],
}))

const scopeWidthOptions = computed(() => [
  { value: '全部' as ScopeWidth, label: t('quoteConverter.scopeWidth.all') },
  { value: '半角' as ScopeWidth, label: t('quoteConverter.scopeWidth.half') },
  { value: '全角' as ScopeWidth, label: t('quoteConverter.scopeWidth.full') },
])
const scopeShapeOptions = computed(() => [
  { value: '全部' as ScopeShape, label: t('quoteConverter.scopeShape.all') },
  { value: '直引号' as ScopeShape, label: t('quoteConverter.scopeShape.straight') },
  { value: '弯引号' as ScopeShape, label: t('quoteConverter.scopeShape.curly') },
])
const targetWidthOptions = computed(() => [
  { value: '半角' as TargetWidth, label: t('quoteConverter.targetWidth.half') },
  { value: '全角' as TargetWidth, label: t('quoteConverter.targetWidth.full') },
])
const targetShapeOptions = computed(() => [
  { value: '弯引号' as TargetShape, label: t('quoteConverter.targetShape.curly') },
  { value: '直引号' as TargetShape, label: t('quoteConverter.targetShape.straight') },
])

watch(scopeWidth, (value) => {
  if (value === '半角') {
    targetWidth.value = '全角'
  } else if (value === '全角') {
    targetWidth.value = '半角'
  }
})

function chooseFile() {
  fileInputRef.value?.click()
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0] || null
  if (!file) {
    return
  }
  const name = file.name.toLowerCase()
  if (!SUPPORTED_EXTS.some((ext) => name.endsWith(ext))) {
    pageMessage.value = t('quoteConverter.errors.unsupported')
    target.value = ''
    return
  }
  selectedFile.value = file
  pageMessage.value = ''
}

async function submit() {
  if (!selectedFile.value) {
    pageMessage.value = t('quoteConverter.errors.fileRequired')
    return
  }
  converting.value = true
  pageMessage.value = ''
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    formData.append('scope_width', scopeWidth.value)
    formData.append('scope_shape', scopeShape.value)
    formData.append('target_width', targetWidth.value)
    formData.append('target_shape', targetShape.value)
    const response = await http.post('/tools/quote-convert', formData, {
      responseType: 'blob',
    })
    const filename = resolveDownloadFilename(
      response.headers['content-disposition'],
      selectedFile.value.name,
    )
    downloadBlob(response.data as Blob, filename)
    toast.success({
      title: t('pages.quoteConverter.title'),
      message: t('quoteConverter.messages.success', { name: filename }),
    })
  } catch (error) {
    let message = t('quoteConverter.errors.convertFailed')
    if (axios.isAxiosError(error) && error.response?.data) {
      const data = error.response.data
      if (data instanceof Blob) {
        try {
          const text = await data.text()
          try {
            const parsed = JSON.parse(text)
            if (parsed?.detail) {
              message = String(parsed.detail)
            }
          } catch {
            if (text) {
              message = text
            }
          }
        } catch {
          // ignore
        }
      } else if (typeof data === 'object' && data && 'detail' in data) {
        message = String((data as { detail?: unknown }).detail || message)
      }
    }
    pageMessage.value = message
  } finally {
    converting.value = false
  }
}
</script>

<template>
  <div class="quote-converter">
    <section class="card">
      <header class="card__header">
        <h2 class="card__title">{{ t('pages.quoteConverter.title') }}</h2>
        <p class="card__description">{{ t('pages.quoteConverter.description') }}</p>
      </header>

      <div class="card__body">
        <div class="form-block">
          <div class="form-block__title">{{ t('quoteConverter.fileSection') }}</div>
          <div class="file-row">
            <button type="button" class="button" @click="chooseFile">
              <Upload :size="16" />
              <span>{{ t('quoteConverter.chooseFile') }}</span>
            </button>
            <div class="file-row__name" :class="{ 'is-empty': !selectedFile }">
              <FileText v-if="selectedFile" :size="14" />
              <span>{{ selectedFile ? selectedFile.name : t('quoteConverter.filePlaceholder') }}</span>
            </div>
            <input
              ref="fileInputRef"
              type="file"
              :accept="ACCEPT_ATTR"
              class="file-row__input"
              @change="onFileChange"
            />
          </div>
          <p class="form-hint">{{ t('quoteConverter.fileAccept') }}</p>
        </div>

        <div class="form-block">
          <div class="form-block__title">{{ t('quoteConverter.scopeSection') }}</div>
          <div class="form-grid">
            <label class="form-item">
              <span>{{ t('quoteConverter.widthLabel') }}</span>
              <select v-model="scopeWidth">
                <option
                  v-for="option in scopeWidthOptions"
                  :key="option.value"
                  :value="option.value"
                >{{ option.label }}</option>
              </select>
            </label>
            <label class="form-item">
              <span>{{ t('quoteConverter.shapeLabel') }}</span>
              <select v-model="scopeShape">
                <option
                  v-for="option in scopeShapeOptions"
                  :key="option.value"
                  :value="option.value"
                >{{ option.label }}</option>
              </select>
            </label>
          </div>
        </div>

        <div class="form-block">
          <div class="form-block__title">{{ t('quoteConverter.targetSection') }}</div>
          <div class="form-grid">
            <label class="form-item">
              <span>{{ t('quoteConverter.widthLabel') }}</span>
              <select v-model="targetWidth">
                <option
                  v-for="option in targetWidthOptions"
                  :key="option.value"
                  :value="option.value"
                >{{ option.label }}</option>
              </select>
            </label>
            <label class="form-item">
              <span>{{ t('quoteConverter.shapeLabel') }}</span>
              <select v-model="targetShape">
                <option
                  v-for="option in targetShapeOptions"
                  :key="option.value"
                  :value="option.value"
                >{{ option.label }}</option>
              </select>
            </label>
          </div>
        </div>

        <p class="form-hint">{{ t('quoteConverter.hint') }}</p>

        <div class="card__actions">
          <button
            type="button"
            class="button button--primary"
            :disabled="converting || !selectedFile"
            @click="submit"
          >
            <Loader2 v-if="converting" :size="16" class="spin" />
            <Upload v-else :size="16" />
            <span>{{ converting ? t('quoteConverter.converting') : t('quoteConverter.convert') }}</span>
          </button>
          <span v-if="pageMessage" class="card__message">{{ pageMessage }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.quote-converter {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px;
}

.card {
  max-width: 720px;
  padding: 24px;
  border: 1px solid var(--line-soft);
  border-radius: 12px;
  background: var(--surface-panel);
  box-shadow: var(--shadow-soft);
}

.card__header {
  margin-bottom: 20px;
}

.card__title {
  margin: 0 0 4px;
  color: var(--text-primary);
  font-size: 18px;
  font-weight: 600;
}

.card__description {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.card__body {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.form-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.form-block__title {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.form-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
}

.form-item select {
  min-height: 36px;
  padding: 0 10px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-input, var(--surface-panel));
  color: var(--text-primary);
}

.form-hint {
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.5;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.file-row__name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-primary);
  font-size: 13px;
  word-break: break-all;
}

.file-row__name.is-empty {
  color: var(--text-muted);
}

.file-row__input {
  display: none;
}

.card__actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 4px;
}

.card__message {
  color: var(--state-danger);
  font-size: 12px;
}

.spin {
  animation: quote-spin 0.9s linear infinite;
}

@keyframes quote-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
