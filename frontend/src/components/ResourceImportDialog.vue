<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import ResourceImportPanel from './ResourceImportPanel.vue'
import Modal from './base/Modal.vue'
import type { ResourceImportResult } from '../utils/resourceImport'

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
  defaultTmCollectionId?: string
  defaultGlossaryBaseId?: string
  defaultTermBaseId?: string
  fixedTmCollectionId?: string
  fixedGlossaryBaseId?: string
  fixedTermBaseId?: string
}>(), {
  mode: 'all',
  initialTab: 'tm',
  title: '',
  sourceLanguage: null,
  targetLanguage: null,
  contextLabel: '',
  defaultTmCollectionId: '',
  defaultGlossaryBaseId: '',
  defaultTermBaseId: '',
  fixedTmCollectionId: '',
  fixedGlossaryBaseId: '',
  fixedTermBaseId: '',
})

const { t } = useI18n()
const modalTitle = computed(() => props.title || t('resourceImport.title'))
const modalDescription = computed(() => {
  const fixed = (props.mode === 'tm' && props.fixedTmCollectionId)
    || (props.mode === 'glossary' && props.fixedGlossaryBaseId)
    || (props.mode === 'term' && props.fixedTermBaseId)
  return t(fixed ? 'resourceImport.appendDescription' : 'resourceImport.description')
})

const emit = defineEmits<{
  close: []
  imported: [payload: ResourceImportResult]
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
      :default-tm-collection-id="defaultTmCollectionId"
      :default-glossary-base-id="defaultGlossaryBaseId"
      :default-term-base-id="defaultTermBaseId"
      :fixed-tm-collection-id="fixedTmCollectionId"
      :fixed-glossary-base-id="fixedGlossaryBaseId"
      :fixed-term-base-id="fixedTermBaseId"
      @imported="emit('imported', $event)"
    />
  </Modal>
</template>
