import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { AxiosError } from 'axios'
import WorkbenchView from '../src/views/WorkbenchView.vue'
import ProofreadingPanel from '../src/components/ProofreadingPanel.vue'
import ConfirmDialog from '../src/components/base/ConfirmDialog.vue'
import { http } from '../src/api/http'
import { i18n } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import { useSegmentStore } from '../src/stores/segment'
import '../src/styles.css'

// 挂载真实工作台；所有接口由内存模拟，测试不连接生产服务。
const params = new URLSearchParams(location.search)
const pairs = Array.from({ length: 105 }, (_, index) => ({
  id: `pair-${index}`, pair_order: index, src_indices: [index * 2, index * 2 + 1],
  tgt_indices: [index * 2, index * 2 + 1], locked: false,
  source_text: `原文 ${index} A\n原文 ${index} B`, target_text: `Target ${index} A\nTarget ${index} B`,
  confidence: 0.8, confidence_level: 'high', method: 'dp', features: {},
}))
let sequence = 0
let delay = 0
let failRefresh = false
let failSave = false
let failReplace = false
let saveDelay = 0
const stage = params.get('stage') || 'alignment'
const batchStatus = params.has('generating') ? 'running' : 'ready'
const exportSnapshots: Array<{ source: string; target: string }> = []
let exportFormat = ''
const requests: Array<{ method: string; url: string; body: any }> = []
const apiError = (config: any, message: string, status = 400) => new AxiosError(message, 'ERR_BAD_REQUEST', config, undefined,
  { data: { detail: message }, status, statusText: 'Error', headers: {}, config })
const unitText = (side: string, indices: number[]) => indices.map(i => side === 'source'
  ? `原文 ${Math.floor(i / 2)} ${i % 2 ? 'B' : 'A'}` : `Target ${Math.floor(i / 2)} ${i % 2 ? 'B' : 'A'}`).join('\n')
const segments = () => pairs.map((pair, index) => ({
  id: `segment-${pair.id}`, file_record_id: 'file1', sentence_id: `align-${pair.id}`,
  source_text: pair.source_text || '（增译，无对应原文）', display_text: pair.source_text,
  target_text: pair.target_text, target_html: null, can_write: true, status: 'none', source: 'imported',
  block_type: 'paragraph', block_index: index, sequence_index: index, display_index: index + 1,
  source_word_count: 3, version: 1, score: 0, alignment_pair_id: pair.id, alignment_pair_order: index,
  alignment_src_indices: pair.src_indices, alignment_tgt_indices: pair.tgt_indices,
  alignment_translation_only: !pair.src_indices.length,
}))
const stats = () => ({ total: pairs.length, confirmed: 0, none: pairs.length, exact: 0, fuzzy: 0,
  project_sync: 0, empty_target: 0, translated: pairs.length })
const segmentPage = (skip = 0, limit = 100, sourceQuery = '') => ({
  segments: segments().filter(row => row.source_text.includes(sourceQuery)).slice(skip, skip + limit),
  total_segments: pairs.length, matched_segments: segments().filter(row => row.source_text.includes(sourceQuery)).length,
  skip, limit, status_stats: stats(), workflow_progress: [], change_cursor: '2026-10-14T00:00:00',
})
http.defaults.adapter = async config => {
  const url = config.url || ''
  const method = config.method || 'get'
  const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data
  requests.push({ method, url, body })
  let data: any = []
  if (url.endsWith('/replace-range')) {
    if (delay) await new Promise(resolve => setTimeout(resolve, delay))
    if (failReplace) throw apiError(config, '模拟配对冲突', 409)
    const removed = pairs.slice(body.start_order, body.start_order + body.delete_count)
    if (JSON.stringify(removed.map(pair => pair.id)) !== JSON.stringify(body.expected_pair_ids)) throw apiError(config, '对齐配对已变化', 409)
    for (const old of removed) {
      for (const [side, field] of [['source', 'src_indices'], ['target', 'tgt_indices']]) {
        if ((old as any)[`${side}_text`] === unitText(side, (old as any)[field])) continue
        const destinations = body.replacements.filter((item: any) => item[field].some((i: number) => (old as any)[field].includes(i)))
        if (destinations.length > 1 && destinations.some((item: any) => item[`${side}_text`] === undefined)) throw apiError(config, '人工编辑的文本无法无损分配到调整后的行')
      }
    }
    const created = body.replacements.map((item: any, offset: number) => ({
      ...item, id: `new-${++sequence}`, pair_order: body.start_order + offset,
      source_text: item.source_text ?? unitText('source', item.src_indices),
      target_text: item.target_text ?? unitText('target', item.tgt_indices),
      confidence: 1, confidence_level: 'high', method: 'manual', features: {},
    }))
    pairs.splice(body.start_order, body.delete_count, ...created)
    pairs.forEach((pair, index) => { pair.pair_order = index })
    data = { items: created }
  } else if (method === 'patch' && url.endsWith('/text')) {
    if (saveDelay) await new Promise(resolve => setTimeout(resolve, saveDelay))
    if (failSave) throw apiError(config, '模拟保存失败')
    const pair = pairs.find(pair => url === `/alignment-pairs/${pair.id}/text`)!
    Object.assign(pair, body)
    data = pair
  } else if (method === 'put' && url.endsWith('/segments')) {
    if (saveDelay) await new Promise(resolve => setTimeout(resolve, saveDelay))
    if (failSave) throw apiError(config, '模拟保存失败')
    for (const update of body.updates) {
      const pair = pairs.find(pair => `align-${pair.id}` === update.sentence_id)!
      pair.target_text = update.target_text
    }
    data = { updated_count: body.updates.length, conflicts: [], segments: segments(), status_stats: stats() }
  } else if (url.endsWith('/export-readiness')) {
    data = { total: pairs.length, unconfirmed: pairs.length, confirmed: 0, missing_translation: 0,
      translation_only: 0, translation_only_unreviewed: 0, llm_failed: 0,
      available_formats: ['proofreading_docx_ordered', 'proofreading_audit_xlsx'], has_warnings: params.has('warnings') }
  } else if (url.endsWith('/export-tasks') && method === 'post') {
    exportFormat = body.format
    exportSnapshots.splice(0, exportSnapshots.length, ...pairs.map(pair => ({ source: pair.source_text, target: pair.target_text })))
    data = { task_id: 'export1', status: 'completed', progress: 100, filename: 'source_上下对照.docx' }
  } else if (url.endsWith('/export-tasks/export1/download')) {
    data = new Blob([JSON.stringify({ format: exportFormat, rows: exportSnapshots })],
      { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    return { data, status: 200, statusText: 'OK', config, headers: { 'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent('source_上下对照.docx')}` } }
  } else if (url.endsWith('/proofreading-batches') && url.includes('/projects/')) {
    data = { items: [{ id: 'batch1', project_id: 'project1', filename: 'source.docx', batch_kind: 'document_pair',
      source_language: 'zh-CN', target_language: 'en-US',
      pair_format: params.has('xlsx') ? 'xlsx' : 'document', workflow_stage: stage,
      alignment_status: 'confirmed', status: batchStatus, progress: 100, total_segments: pairs.length,
      changed_segments: 0, skipped_segments: 0, failed_segments: 0, message: '', error_message: '',
      export_status: 'idle', generation_settings: { provider: 'openrouter', model: '', user_instructions: '' },
      bindings: [{ file_record_id: 'file1', target_language: 'en-US', sheet_name: '双文档对齐' }] }] }
  } else if (url.endsWith('/position')) {
    if (failRefresh) throw apiError(config, '模拟读取失败', 503)
    const sentence = decodeURIComponent(url.split('/').at(-2)!)
    const index = segments().findIndex(item => item.sentence_id === sentence)
    const pageSize = Number(config.params.page_size)
    data = { sentence_id: sentence, index, page: Math.floor(index / pageSize) + 1, page_size: pageSize, page_index: index % pageSize }
  } else if (url.endsWith('/alignment-pairs')) {
    const page = Number(config.params?.page || 1)
    const size = Number(config.params?.page_size || 100)
    data = { items: pairs.slice((page - 1) * size, page * size), total: pairs.length }
  } else if (url === '/file-records/file1') {
    data = { id: 'file1', filename: 'source.docx', project_id: 'project1', project_name: '对齐测试',
      source_language: 'zh-CN', target_language: 'en-US', can_write: true, can_export: true,
      workflow_steps: [], ...segmentPage(0, Number(config.params?.limit || 100)) }
  } else if (url.endsWith('/proofreading-baselines')) {
    data = { is_proofreading: true, proofreading_context: { batch_id: 'batch1', batch_kind: 'document_pair',
      workflow_stage: stage, batch_status: batchStatus, pair_format: params.has('xlsx') ? 'xlsx' : 'document', sheet_name: '双文档对齐' },
      items: segments().map(item => ({ segment_id: item.id, original_target_text: item.target_text })) }
  } else if (url.endsWith('/segments')) {
    data = segmentPage(Number(config.params?.skip || 0), Number(config.params?.limit || 100), config.params?.source_query || '')
  } else if (url.endsWith('/segments/changes')) {
    data = { segments: [], has_more: false, next_cursor: '2026-10-14T00:00:00' }
  } else if (url.endsWith('/revision-settings')) {
    data = { show_others_revisions: true, show_author_time: false, author_colors: {} }
  } else if (url.includes('/comments')) {
    data = { items: [], total: 0 }
  } else if (url.includes('qa-report') || url.includes('qa/reports')) {
    data = null
  }
  return { data: structuredClone(data), status: 200, statusText: 'OK', config, headers: {} }
}
const nativeFetch = window.fetch
window.fetch = (input, init) => String(input).startsWith('/api/')
  ? Promise.resolve(new Response('', { status: 503 })) : nativeFetch(input, init)
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/workbench/:id', name: 'workbench-focus',
  component: params.has('batches') ? ProofreadingPanel : WorkbenchView,
  props: route => params.has('batches') ? { projectId: 'project1' } : ({ id: route.params.id, standalone: !params.has('virtual') }) }] })
const app = createApp({ render: () => h('div', [h(RouterView), h(ConfirmDialog)]) })
const pinia = createPinia()
app.use(pinia)
useAuthStore().user = { id: 'user1', username: 'admin', role: 'admin' } as any
app.use(i18n)
app.use(router)
await router.push(`/workbench/file1?${stage === 'alignment' ? 'mode=alignment&' : ''}page=${params.get('page') || 1}&pageSize=100`)
await router.isReady()
app.mount('#app')
Object.assign(window, { alignmentHarness: { requests, pairs, exportSnapshots, store: useSegmentStore(),
  setSaveDelay: (value: number) => { saveDelay = value },
  setDelay: (value: number) => { delay = value }, setFailRefresh: (value: boolean) => { failRefresh = value },
  setFailSave: (value: boolean) => { failSave = value }, setFailReplace: (value: boolean) => { failReplace = value },
} })
