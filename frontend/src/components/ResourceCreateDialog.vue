<script setup lang="ts">
import { Loader2, Plus, Upload, X } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import { formatLanguagePair } from '../constants/languages'
import { pushToast } from '../composables/useToast'
import type { GlossaryBase, TermBase } from '../types/api'
import Modal from './base/Modal.vue'

type ResourceType = 'termBase' | 'glossary'

interface Props {
  open: boolean
  resourceType: ResourceType
  sourceLanguage: string
  targetLanguage: string
}

const props = defineProps<Props>()

const emit = defineEmits<{
  close: []
  created: [resource: TermBase | GlossaryBase]
}>()

const { t } = useI18n()

const name = ref('')
const description = ref('')
const file = ref<File | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const creating = ref(false)
const importing = ref(false)

const resourceLabel = computed(() => (
  props.resourceType === 'termBase'
    ? t('resourceCreate.termBase')
    : t('resourceCreate.glossary')
))

const dialogTitle = computed(() => (
  t('resourceCreate.dialogTitle', { type: resourceLabel.value })
))

const dialogDescription = computed(() => (
  t('resourceCreate.dialogDescription', { type: resourceLabel.value })
))

const languagePairLabel = computed(() => (
  props.sourceLanguage && props.targetLanguage
    ? formatLanguagePair(props.sourceLanguage, props.targetLanguage)
    : t('common.notSet')
))

const canCreate = computed(() => (
  name.value.trim().length > 0 &&
  props.sourceLanguage &&
  props.targetLanguage &&
  !creating.value &&
  !importing.value
))

watch(() => props.open, (open) => {
  if (open) {
    name.value = ''
    description.value = ''
    file.value = null
    if (fileInputRef.value) {
      fileInputRef.value.value = ''
    }
  }
})

function openFileDialog() {
  fileInputRef.value?.click()
}

function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const selectedFile = input.files?.[0]
  if (selectedFile) {
    file.value = selectedFile
    if (!name.value.trim()) {
      const fileNameWithoutExt = selectedFile.name.replace(/\.[^/.]+$/, '')
      name.value = fileNameWithoutExt
    }
  }
}

function removeFile() {
  file.value = null
  if (fileInputRef.value) {
    fileInputRef.value.value = ''
  }
}

function requestClose() {
  if (creating.value || importing.value) {
    return
  }
  emit('close')
}

async function createResource() {
  if (!canCreate.value) {
    return
  }

  const trimmedName = name.value.trim()
  const trimmedDescription = description.value.trim() || null

  creating.value = true

  try {
    let createdResource: TermBase | GlossaryBase

    if (props.resourceType === 'termBase') {
      const { data } = await http.post<TermBase>('/term-bases', {
        name: trimmedName,
        description: trimmedDescription,
        source_language: props.sourceLanguage,
        target_language: props.targetLanguage,
      })
      createdResource = data
    } else {
      const { data } = await http.post<GlossaryBase>('/glossary-bases', {
        name: trimmedName,
        description: trimmedDescription,
        source_language: props.sourceLanguage,
        target_language: props.targetLanguage,
      })
      createdResource = data
    }

    if (file.value) {
      await importFileToResource(createdResource.id)
    }

    emit('created', createdResource)
  } catch (error) {
    const message = error instanceof Error ? error.message : t('resourceCreate.errors.createFailed')
    pushToast({
      tone: 'error',
      title: t('resourceCreate.errors.createFailedTitle', { type: resourceLabel.value }),
      message,
    })
  } finally {
    creating.value = false
  }
}

async function importFileToResource(resourceId: string) {
  if (!file.value) {
    return
  }

  importing.value = true

  try {
    const formData = new FormData()
    formData.append('file', file.value)
    formData.append('source_language', props.sourceLanguage)
    formData.append('target_language', props.targetLanguage)

    if (props.resourceType === 'termBase') {
      formData.append('term_base_id', resourceId)
      await http.post('/term-bases/import-xlsx', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
    } else {
      formData.append('glossary_base_id', resourceId)
      await http.post('/glossary-bases/import-xlsx', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : t('resourceCreate.errors.importFailed')
    pushToast({
      tone: 'warn',
      title: t('resourceCreate.errors.importFailedTitle'),
      message,
    })
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <Modal
    :open="open"
    :title="dialogTitle"
    :description="dialogDescription"
    width="min(520px, calc(100vw - 32px))"
    @close="requestClose"
  >
    <div class="rcd-form">
      <div class="rcd-language-pair">
        <span class="tag">{{ languagePairLabel }}</span>
        <span class="rcd-hint">{{ t('resourceCreate.languagePairHint') }}</span>
      </div>

      <label class="field">
        <span class="field__label">{{ t('resourceCreate.name') }} <span class="required">*</span></span>
        <input
          v-model="name"
          class="field__control"
          type="text"
          :placeholder="t('resourceCreate.namePlaceholder', { type: resourceLabel })"
          :disabled="creating || importing"
        />
      </label>

      <label class="field">
        <span class="field__label">{{ t('resourceCreate.description') }}</span>
        <textarea
          v-model="description"
          class="field__control rcd-description"
          rows="2"
          :placeholder="t('resourceCreate.descriptionPlaceholder')"
          :disabled="creating || importing"
        />
      </label>

      <div class="rcd-file-section">
        <span class="field__label">{{ t('resourceCreate.importFile') }}</span>
        <p class="rcd-file-hint">{{ t('resourceCreate.importFileHint') }}</p>
        
        <div v-if="file" class="rcd-file-selected">
          <span class="rcd-file-name">{{ file.name }}</span>
          <button
            class="button button--ghost rcd-file-remove"
            type="button"
            :disabled="creating || importing"
            @click="removeFile"
          >
            <X :size="14" />
          </button>
        </div>
        <button
          v-else
          class="button button--outline rcd-file-button"
          type="button"
          :disabled="creating || importing"
          @click="openFileDialog"
        >
          <Upload :size="14" />
          {{ t('resourceCreate.selectFile') }}
        </button>
        <input
          ref="fileInputRef"
          class="rcd-file-input"
          type="file"
          accept=".xlsx,.xls,.csv"
          @change="handleFileChange"
        />
      </div>
    </div>

    <template #footer>
      <div class="rcd-footer">
        <button
          class="button"
          type="button"
          :disabled="creating || importing"
          @click="requestClose"
        >
          {{ t('common.actions.cancel') }}
        </button>
        <button
          class="button button--primary"
          type="button"
          :disabled="!canCreate"
          @click="createResource"
        >
          <Loader2 v-if="creating || importing" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ creating || importing ? t('common.actions.saving') : t('resourceCreate.create') }}
        </button>
      </div>
    </template>
  </Modal>
</template>

<style scoped>
.rcd-form {
  display: grid;
  gap: 16px;
}

.rcd-language-pair {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
}

.rcd-language-pair .tag {
  flex-shrink: 0;
}

.rcd-hint {
  color: var(--text-muted);
  font-size: 12px;
}

.rcd-description {
  resize: vertical;
  min-height: 60px;
  max-height: 120px;
}

.rcd-file-section {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-1);
}

.rcd-file-section .field__label {
  margin-bottom: 0;
}

.rcd-file-hint {
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
}

.rcd-file-selected {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--state-success);
  border-radius: 6px;
  background: var(--state-success-bg);
}

.rcd-file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
  font-size: 13px;
}

.rcd-file-remove {
  flex-shrink: 0;
  min-width: 28px;
  min-height: 28px;
  padding: 6px;
  border-radius: 4px;
}

.rcd-file-button {
  justify-content: center;
}

.rcd-file-input {
  display: none;
}

.rcd-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.required {
  color: var(--state-danger);
}
</style>
