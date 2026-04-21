<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import ResourceImportPanel from './ResourceImportPanel.vue'
import Modal from './base/Modal.vue'

type ImportTab = 'tm' | 'term'

const props = withDefaults(defineProps<{
  open: boolean
  initialTab?: ImportTab
  title?: string
  sourceLanguage?: string | null
  targetLanguage?: string | null
  contextLabel?: string
}>(), {
  initialTab: 'tm',
  title: '',
  sourceLanguage: null,
  targetLanguage: null,
  contextLabel: '',
})

const { t } = useI18n()
const modalTitle = computed(() => props.title || t('resourceImport.title'))
const modalDescription = computed(() => t('resourceImport.description'))

const emit = defineEmits<{
  close: []
  imported: [payload: { tab: ImportTab }]
}>()
</script>

<template>
  <Modal
    :open="open"
    :title="modalTitle"
    :description="modalDescription"
    width="min(920px, calc(100vw - 32px))"
    @close="emit('close')"
  >
    <ResourceImportPanel
      mode="all"
      :initial-tab="initialTab"
      :source-language="sourceLanguage"
      :target-language="targetLanguage"
      :context-label="contextLabel"
      @imported="emit('imported', $event)"
    />
  </Modal>
</template>
