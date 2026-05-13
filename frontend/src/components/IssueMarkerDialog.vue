<script setup lang="ts">
import axios from 'axios'
import { Flag, Loader2 } from 'lucide-vue-next'
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import Modal from './base/Modal.vue'
import type {
  IssueCategory,
  IssueMarker,
  IssueMarkerCreatePayload,
  IssueSeverity,
} from '../types/api'

const props = withDefaults(defineProps<{
  open: boolean
  projectId: string | null
  fileRecordId?: string | null
  contextLabel?: string
}>(), {
  fileRecordId: null,
  contextLabel: '',
})

const emit = defineEmits<{
  close: []
  saved: [marker: IssueMarker]
}>()

const { t } = useI18n()

const saving = ref(false)
const formError = ref('')
const form = reactive<{
  title: string
  category: IssueCategory
  severity: IssueSeverity
  description: string
}>({
  title: '',
  category: 'other',
  severity: 'medium',
  description: '',
})

const categoryOptions = computed<Array<{ value: IssueCategory; label: string }>>(() => [
  { value: 'bug', label: t('issueMarker.categories.bug') },
  { value: 'translation', label: t('issueMarker.categories.translation') },
  { value: 'format', label: t('issueMarker.categories.format') },
  { value: 'performance', label: t('issueMarker.categories.performance') },
  { value: 'data', label: t('issueMarker.categories.data') },
  { value: 'other', label: t('issueMarker.categories.other') },
])

const severityOptions = computed<Array<{ value: IssueSeverity; label: string }>>(() => [
  { value: 'low', label: t('issueMarker.severity.low') },
  { value: 'medium', label: t('issueMarker.severity.medium') },
  { value: 'high', label: t('issueMarker.severity.high') },
  { value: 'critical', label: t('issueMarker.severity.critical') },
])

const dialogDescription = computed(() => (
  props.contextLabel
    ? t('issueMarker.dialogDescriptionWithContext', { context: props.contextLabel })
    : t('issueMarker.dialogDescription')
))

function resetForm() {
  form.title = ''
  form.category = 'other'
  form.severity = 'medium'
  form.description = ''
  formError.value = ''
}

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

function closeDialog() {
  if (saving.value) {
    return
  }
  emit('close')
}

async function submitIssueMarker() {
  if (!props.projectId) {
    formError.value = t('issueMarker.errors.missingProject')
    return
  }
  if (!form.description.trim()) {
    formError.value = t('issueMarker.errors.requiredDescription')
    return
  }

  saving.value = true
  formError.value = ''
  try {
    const payload: IssueMarkerCreatePayload = {
      file_record_id: props.fileRecordId || null,
      title: form.title.trim() || null,
      description: form.description.trim(),
      category: form.category,
      severity: form.severity,
      page_url: window.location.href,
      user_agent: navigator.userAgent,
    }
    const { data } = await http.post<IssueMarker>(`/projects/${props.projectId}/issue-markers`, payload)
    emit('saved', data)
    resetForm()
  } catch (error) {
    formError.value = getErrorMessage(error, t('issueMarker.errors.save'))
  } finally {
    saving.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      resetForm()
    }
  },
)
</script>

<template>
  <Modal
    :open="open"
    :title="t('issueMarker.dialogTitle')"
    :description="dialogDescription"
    width="min(600px, calc(100vw - 32px))"
    @close="closeDialog"
  >
    <div class="issue-marker-form">
      <label class="field field--full">
        <span class="field__label">{{ t('issueMarker.fields.title') }}</span>
        <input
          v-model="form.title"
          class="field__control"
          type="text"
          maxlength="160"
          :placeholder="t('issueMarker.placeholders.title')"
        />
      </label>

      <label class="field">
        <span class="field__label">{{ t('issueMarker.fields.category') }}</span>
        <select v-model="form.category" class="field__control">
          <option v-for="option in categoryOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </label>

      <label class="field">
        <span class="field__label">{{ t('issueMarker.fields.severity') }}</span>
        <select v-model="form.severity" class="field__control">
          <option v-for="option in severityOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
      </label>

      <label class="field field--full">
        <span class="field__label">
          {{ t('issueMarker.fields.description') }}
          <span class="field__required">*</span>
        </span>
        <textarea
          v-model="form.description"
          class="field__control issue-marker-form__textarea"
          rows="6"
          :placeholder="t('issueMarker.placeholders.description')"
        />
      </label>

      <p class="hint-text issue-marker-form__hint">{{ t('issueMarker.hint') }}</p>
      <p v-if="formError" class="form-message is-error">{{ formError }}</p>
    </div>

    <template #footer>
      <button class="button" type="button" :disabled="saving" @click="closeDialog">
        {{ t('common.actions.cancel') }}
      </button>
      <button class="button button--primary" type="button" :disabled="saving" @click="submitIssueMarker">
        <Loader2 v-if="saving" class="lucide-spin" :size="14" />
        <Flag v-else :size="14" />
        {{ saving ? t('common.actions.saving') : t('issueMarker.actions.submit') }}
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.issue-marker-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.field--full {
  grid-column: 1 / -1;
}

.field__required {
  color: var(--state-danger);
}

.issue-marker-form__textarea {
  resize: vertical;
  min-height: 126px;
  line-height: 1.55;
}

.issue-marker-form__hint {
  grid-column: 1 / -1;
  margin: -4px 0 0;
}

.issue-marker-form .form-message {
  grid-column: 1 / -1;
  margin: 0;
}

@media (max-width: 640px) {
  .issue-marker-form {
    grid-template-columns: 1fr;
  }
}
</style>
