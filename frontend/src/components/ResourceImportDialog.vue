<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import ResourceImportPanel from './ResourceImportPanel.vue'
import Modal from './base/Modal.vue'

type ImportTab = 'tm' | 'glossary' | 'term'
type ImportMode = 'all' | ImportTab

const props = withDefaults(defineProps<{
  open: boolean
  mode?: ImportMode
  initialTab?: ImportTab
  title?: string
  sourceLanguage?: string | null
  targetLanguage?: string | null
  contextLabel?: string
  defaultTMCollectionId?: string
  defaultGlossaryBaseId?: string
  defaultTermBaseId?: string
  fixedTMCollectionId?: string
  fixedGlossaryBaseId?: string
  fixedTermBaseId?: string
}>(), {
  mode: 'all',
  initialTab: 'tm',
  title: '',
  sourceLanguage: null,
  targetLanguage: null,
  contextLabel: '',
  defaultTMCollectionId: '',
  defaultGlossaryBaseId: '',
  defaultTermBaseId: '',
  fixedTMCollectionId: '',
  fixedGlossaryBaseId: '',
  fixedTermBaseId: '',
})

const { t } = useI18n()
const modalTitle = computed(() => props.title || t('resourceImport.title'))
const modalDescription = computed(() => t('resourceImport.description'))

const emit = defineEmits<{
  close: []
  imported: [payload: { tab: ImportTab, resourceId?: string }]
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
      :mode="mode"
      :initial-tab="initialTab"
      :source-language="sourceLanguage"
      :target-language="targetLanguage"
      :context-label="contextLabel"
      :default-tm-collection-id="defaultTMCollectionId"
      :default-glossary-base-id="defaultGlossaryBaseId"
      :default-term-base-id="defaultTermBaseId"
      :fixed-tm-collection-id="fixedTMCollectionId"
      :fixed-glossary-base-id="fixedGlossaryBaseId"
      :fixed-term-base-id="fixedTermBaseId"
      @imported="emit('imported', $event)"
    />
  </Modal>
</template>
