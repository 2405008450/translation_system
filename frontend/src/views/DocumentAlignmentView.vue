<script setup lang="ts">
import { ArrowLeft, FileText } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { confirmAlignment } from '../api/documentAlignment'
import { listProofreadingBatches } from '../api/proofreading'
import DocumentAlignmentEditor from '../components/DocumentAlignmentEditor.vue'

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()
const resolvingExisting = ref(true)
const startNew = computed(() => route.query.action === 'new')
const resumeBatchId = computed(() => typeof route.query.batch === 'string' ? route.query.batch : '')

function backToProject() {
  void router.push({ name: 'project-detail', params: { id: props.id } })
}

async function openFocus(fileRecordId: string, stage: string | undefined) {
  await router.replace({
    name: 'workbench-focus',
    params: { id: fileRecordId },
    query: {
      from: 'project',
      pid: props.id,
      ...(stage === 'alignment' ? { mode: 'alignment' } : {}),
    },
  })
}

onMounted(async () => {
  try {
    // “导入新文档”必须直接显示空白上传表单，不能被历史批次劫持到工作台。
    if (startNew.value) return
    const batch = (await listProofreadingBatches(props.id)).find(item => (
      item.batch_kind === 'document_pair'
      && (!resumeBatchId.value || item.id === resumeBatchId.value)
    ))
    if (!batch) return
    const fileRecordId = batch.bindings[0]?.file_record_id
    if (batch.workflow_stage === 'proofreading' && fileRecordId) {
      await openFocus(fileRecordId, 'proofreading')
      return
    }
    if ((batch.workflow_stage === 'alignment' || batch.alignment_status === 'confirmed') && fileRecordId) {
      await openFocus(fileRecordId, 'alignment')
      return
    }
    if (batch.alignment_status === 'draft') {
      const result = await confirmAlignment(batch.id)
      await openFocus(result.file_record_id, 'alignment')
    }
  } finally {
    resolvingExisting.value = false
  }
})
</script>

<template>
  <main class="alignment-workspace">
    <header class="alignment-workspace__ribbon">
      <button class="alignment-workspace__back" type="button" title="返回项目" @click="backToProject">
        <ArrowLeft :size="16" />
        <span>返回项目</span>
      </button>
      <div class="alignment-workspace__title">
        <strong>双文档对齐工作台</strong>
        <span>原文与原始译文边界调整</span>
      </div>
      <div class="alignment-workspace__notice">
        <FileText :size="15" />对齐草稿自动保存 · 当前阶段不生成校对后译文
      </div>
    </header>
    <section v-if="resolvingExisting" class="alignment-workspace__loading">正在进入对齐工作台…</section>
    <section v-else class="alignment-workspace__editor">
      <DocumentAlignmentEditor
        :project-id="id"
        :start-new="startNew"
        :resume-batch-id="resumeBatchId"
        compact
      />
    </section>
  </main>
</template>

<style scoped>
.alignment-workspace { display: grid; grid-template-rows: 50px minmax(0, 1fr); width: 100vw; height: 100dvh; min-width: 0; overflow: hidden; background: #f3f6f8; }
.alignment-workspace__ribbon { display: flex; align-items: stretch; gap: 0; border-bottom: 1px solid #d8e0e7; background: #fff; box-shadow: 0 1px 3px rgba(15, 23, 42, .06); }
.alignment-workspace__back { display: inline-flex; align-items: center; gap: 7px; padding: 0 16px; border: 0; border-right: 1px solid #d8e0e7; background: #fff; color: var(--brand); cursor: pointer; font-weight: 700; }
.alignment-workspace__back:hover { background: rgba(15, 118, 110, .07); }
.alignment-workspace__title { display: grid; align-content: center; gap: 1px; min-width: 260px; padding: 0 18px; }
.alignment-workspace__title strong { color: var(--ink-900, #0f172a); font-size: 15px; }
.alignment-workspace__title span { color: var(--ink-500); font-size: 11px; }
.alignment-workspace__notice { display: inline-flex; align-items: center; gap: 7px; margin-left: auto; padding: 0 18px; color: var(--ink-500); font-size: 12px; }
.alignment-workspace__editor { min-height: 0; padding: 8px 10px 10px; overflow: hidden; }
.alignment-workspace__loading { display: grid; place-items: center; color: var(--ink-500); }
.alignment-workspace__editor :deep(.alignment-editor) { display: flex; flex-direction: column; height: 100%; min-height: 0; padding: 10px; border-radius: 6px; }
.alignment-workspace__editor :deep(.alignment-toolbar) { flex-wrap: nowrap; overflow-x: auto; border-radius: 4px; }
.alignment-workspace__editor :deep(.alignment-grid) { flex: 1 1 auto; height: auto; min-height: 0; max-height: none; }
.alignment-workspace__editor :deep(.alignment-upload) { overflow: auto; }
@media (max-width: 720px) {
  .alignment-workspace__title { min-width: 0; }
  .alignment-workspace__notice { display: none; }
  .alignment-workspace__back span { display: none; }
}
</style>
