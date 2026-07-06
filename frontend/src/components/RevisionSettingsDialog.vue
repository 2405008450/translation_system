<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import Modal from './base/Modal.vue'
import type { RevisionAuthorColors, RevisionDisplaySettings } from '../types/api'

interface RevisionAuthorOption {
  id: string
  name: string
  username?: string | null
}

const props = withDefaults(defineProps<{
  open: boolean
  settings: RevisionDisplaySettings
  authors: RevisionAuthorOption[]
  saving?: boolean
}>(), {
  saving: false,
})

const emit = defineEmits<{
  close: []
  save: [payload: RevisionDisplaySettings]
}>()

const { t } = useI18n()

const form = reactive({
  show_author_time: true,
  show_others_revisions: true,
  default_insert_color: '#2563eb',
  default_delete_color: '#dc2626',
  author_colors: {} as Record<string, RevisionAuthorColors>,
})

const normalizedAuthors = computed(() => {
  const seen = new Set<string>()
  return props.authors.filter((author) => {
    if (!author.id || seen.has(author.id)) {
      return false
    }
    seen.add(author.id)
    return true
  })
})

function cloneAuthorColors(value: Record<string, RevisionAuthorColors> | null | undefined) {
  const next: Record<string, RevisionAuthorColors> = {}
  Object.entries(value || {}).forEach(([userId, colors]) => {
    next[userId] = {
      insert: colors?.insert || form.default_insert_color,
      delete: colors?.delete || form.default_delete_color,
    }
  })
  return next
}

function ensureAuthorColors(userId: string) {
  if (!form.author_colors[userId]) {
    form.author_colors[userId] = {
      insert: form.default_insert_color,
      delete: form.default_delete_color,
    }
  }
  return form.author_colors[userId]
}

function syncForm() {
  form.show_author_time = props.settings.show_author_time
  form.show_others_revisions = props.settings.show_others_revisions
  form.default_insert_color = props.settings.default_insert_color || '#2563eb'
  form.default_delete_color = props.settings.default_delete_color || '#dc2626'
  form.author_colors = cloneAuthorColors(props.settings.author_colors)
  normalizedAuthors.value.forEach((author) => ensureAuthorColors(author.id))
}

function submit() {
  emit('save', {
    ...props.settings,
    show_author_time: form.show_author_time,
    show_others_revisions: form.show_others_revisions,
    default_insert_color: form.default_insert_color,
    default_delete_color: form.default_delete_color,
    author_colors: cloneAuthorColors(form.author_colors),
  })
}

watch(
  () => [props.open, props.settings, props.authors],
  syncForm,
  { immediate: true, deep: true },
)
</script>

<template>
  <Modal
    :open="open"
    :title="t('workbench.revisionSettings.title')"
    width="min(640px, calc(100vw - 32px))"
    @close="emit('close')"
  >
    <form class="revision-settings" @submit.prevent="submit">
      <label class="revision-settings__switch">
        <input v-model="form.show_author_time" type="checkbox">
        <span>
          <strong>{{ t('workbench.revisionSettings.showAuthorTime') }}</strong>
          <small>{{ t('workbench.revisionSettings.showAuthorTimeHint') }}</small>
        </span>
      </label>

      <label class="revision-settings__switch">
        <input v-model="form.show_others_revisions" type="checkbox">
        <span>
          <strong>{{ t('workbench.revisionSettings.showOthers') }}</strong>
          <small>{{ t('workbench.revisionSettings.showOthersHint') }}</small>
        </span>
      </label>

      <section class="revision-settings__section">
        <div class="revision-settings__section-title">
          <strong>{{ t('workbench.revisionSettings.defaultColors') }}</strong>
        </div>
        <div class="revision-settings__color-grid">
          <label>
            <span>{{ t('workbench.revisionSettings.insertColor') }}</span>
            <input v-model="form.default_insert_color" type="color">
          </label>
          <label>
            <span>{{ t('workbench.revisionSettings.deleteColor') }}</span>
            <input v-model="form.default_delete_color" type="color">
          </label>
        </div>
      </section>

      <section class="revision-settings__section">
        <div class="revision-settings__section-title">
          <strong>{{ t('workbench.revisionSettings.authorColors') }}</strong>
        </div>
        <div class="revision-settings__table" role="table">
          <div class="revision-settings__row revision-settings__row--head" role="row">
            <span role="columnheader">{{ t('workbench.revisionSettings.author') }}</span>
            <span role="columnheader">{{ t('workbench.revisionSettings.insertColor') }}</span>
            <span role="columnheader">{{ t('workbench.revisionSettings.deleteColor') }}</span>
          </div>
          <div
            v-for="author in normalizedAuthors"
            :key="author.id"
            class="revision-settings__row"
            role="row"
          >
            <span class="revision-settings__author" role="cell">
              <strong>{{ author.name }}</strong>
              <small v-if="author.username">{{ author.username }}</small>
            </span>
            <span role="cell">
              <input
                v-model="ensureAuthorColors(author.id).insert"
                type="color"
                :aria-label="`${author.name} ${t('workbench.revisionSettings.insertColor')}`"
              >
            </span>
            <span role="cell">
              <input
                v-model="ensureAuthorColors(author.id).delete"
                type="color"
                :aria-label="`${author.name} ${t('workbench.revisionSettings.deleteColor')}`"
              >
            </span>
          </div>
          <div v-if="normalizedAuthors.length === 0" class="revision-settings__empty">
            {{ t('workbench.revisionSettings.noAuthors') }}
          </div>
        </div>
      </section>

      <div class="revision-settings__actions">
        <button class="button secondary" type="button" :disabled="saving" @click="emit('close')">
          {{ t('common.actions.cancel') }}
        </button>
        <button class="button primary" type="submit" :disabled="saving">
          {{ saving ? t('common.actions.saving') : t('common.actions.save') }}
        </button>
      </div>
    </form>
  </Modal>
</template>

<style scoped>
.revision-settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.revision-settings__switch {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: flex-start;
  padding: 10px 0;
  border-bottom: 1px solid var(--line-soft);
}

.revision-settings__switch input {
  width: 16px;
  height: 16px;
  margin-top: 2px;
}

.revision-settings__switch span,
.revision-settings__author {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.revision-settings strong {
  color: var(--text-primary);
  font-size: 13px;
}

.revision-settings small {
  color: var(--text-muted);
  font-size: 12px;
}

.revision-settings__section {
  display: grid;
  gap: 10px;
}

.revision-settings__section-title {
  color: var(--text-primary);
}

.revision-settings__color-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.revision-settings__color-grid label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 38px;
  padding: 0 10px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-muted);
  color: var(--text-secondary);
  font-size: 13px;
}

.revision-settings input[type="color"] {
  width: 34px;
  height: 26px;
  padding: 0;
  border: 1px solid var(--line-soft);
  border-radius: 4px;
  background: transparent;
}

.revision-settings__table {
  overflow: hidden;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
}

.revision-settings__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 92px 92px;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 7px 10px;
  border-top: 1px solid var(--line-soft);
}

.revision-settings__row:first-child {
  border-top: 0;
}

.revision-settings__row--head {
  min-height: 34px;
  background: var(--surface-muted);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
}

.revision-settings__empty {
  padding: 18px 10px;
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
}

.revision-settings__actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 4px;
}

@media (max-width: 560px) {
  .revision-settings__color-grid,
  .revision-settings__row {
    grid-template-columns: 1fr;
  }

  .revision-settings__row--head {
    display: none;
  }
}
</style>
