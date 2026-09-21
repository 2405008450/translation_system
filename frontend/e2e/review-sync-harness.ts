import { createApp, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { useSegmentStore } from '../src/stores/segment'
import { http } from '../src/api/http'

setActivePinia(createPinia())
const store = useSegmentStore()
const requests: Array<{ method: string; url: string; body: any }> = []
const merge = new URLSearchParams(location.search).has('merge')
const rows = [
  { id: 'one', file_record_id: 'file1', sentence_id: 's1' },
  { id: 'two', file_record_id: merge ? 'file2' : 'file1', sentence_id: merge ? 's1' : 's2' },
].map(row => ({ ...row, source_text: '相同原文', display_text: '相同原文', target_text: 'A',
  source: 'manual', status: 'confirmed', version: 1, review_sync_enabled: true, can_write: true }))
let revisions: any[] = []
let fail = false
let taskPending = false
const stats = { total: 2, confirmed: 2, none: 2, exact: 0, fuzzy: 0, project_sync: 0, empty_target: 0 }
const files = ['file1', 'file2'].map(id => ({ id, filename: `${id}.txt`, can_write: true, total_segments: 1, status_stats: stats }))
const page = (segments: any[]) => ({ segments, total_segments: segments.length, matched_segments: segments.length,
  skip: 0, limit: 100, groups: [], status_stats: stats, workflow_progress: [], change_cursor: '2026-01-01T00:00:00' })
const revision = (row: any, after: string) => ({ id: `rev-${row.id}`, file_record_id: row.file_record_id,
  segment_id: row.id, sentence_id: row.sentence_id, source: 'manual', status: 'pending', before_text: 'A', after_text: after,
  author: null, resolved_by: null, created_at: '2026-01-01T00:00:00', resolved_at: null,
  review_sync_group_id: 'group1', review_sync_count: 2 })
http.defaults.adapter = async config => {
  const url = config.url || ''
  const method = config.method || 'get'
  const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data
  requests.push({ method, url, body })
  const fileId = url.match(/file-records\/([^/]+)/)?.[1]
  let data: any = {}
  if (url.includes('/project-sync')) {
    if (fail) throw new Error('test failure')
    taskPending = true
    data = { enabled: true, queued_count: 1, source_version: rows[0].version, task_id: 'task1' }
  } else if (url === '/review-sync/tasks/task1') {
    if (taskPending) {
      rows[1].target_text = rows[0].target_text
      rows[1].version++
      rows[1].status = 'none'
      revisions = rows.map(row => revision(row, row.target_text))
      taskPending = false
    }
    data = { status: 'completed', result: { updated_count: 1, skipped_count: 0, reasons: {} } }
  } else if (method === 'put' && url.endsWith('/segments')) {
    for (const update of body.updates) {
      const row = rows.find(r => r.file_record_id === fileId && r.sentence_id === update.sentence_id)!
      row.target_text = update.target_text
      row.version++
      row.status = 'none'
      Object.assign(row, { review_sync_group_id: 'group1' })
      revisions = [revision(row, row.target_text)]
    }
    data = { updated_count: body.updates.length, conflicts: [], segments: rows.filter(r => r.file_record_id === fileId), status_stats: stats }
  } else if (method === 'patch' && url.startsWith('/revisions/')) {
    revisions.forEach(r => { r.status = body.status })
    rows.forEach(r => { if (body.status === 'rejected') r.target_text = 'A'; r.version++ })
    data = { ...revisions.find(r => url.endsWith(r.id)), review_sync_result: { updated_count: 2, skipped_count: 0, reasons: {} } }
  } else if (url.endsWith('/revision-settings')) {
    data = { show_others_revisions: true, show_author_time: true, author_colors: {} }
  } else if (url.endsWith('/revisions')) {
    data = revisions.filter(r => r.file_record_id === fileId)
  } else if (url.endsWith('/segments/changes')) {
    data = { segments: [], has_more: false, next_cursor: '2026-01-01T00:00:00', status_stats: stats }
  } else if (url.includes('merge-views') && url.endsWith('/segments')) {
    data = page(rows)
  } else if (url.includes('merge-views')) {
    data = { id: 'merge1', files, total_segments: 2 }
  } else if (url.endsWith('/segments')) {
    data = page(rows.filter(r => r.file_record_id === fileId))
  } else if (url === '/file-records/file1') {
    data = { id: 'file1', ...page(rows), workflow_steps: [], can_write: true, can_export: true }
  } else if (url.includes('term-matches')) {
    data = []
  }
  return { data: structuredClone(data), status: 200, statusText: 'OK', config, headers: {} }
}
const originalFetch = window.fetch
window.fetch = (input, init) => String(input).startsWith('/api/')
  ? Promise.resolve(new Response('', { status: 503 })) : originalFetch(input, init)

async function initialize() {
  if (merge) await store.loadMergeView('merge1')
  else await store.loadTask('file1')
  store.startRevisionTracking()
  ;(window as any).reviewHarness = { store, requests, setFailure: (value: boolean) => { fail = value } }
  createApp({ render: () => h('main', [
    ...store.segments.map(row => {
      const key = merge ? `${row.file_record_id}:${row.sentence_id}` : row.sentence_id
      const trace = store.getRevisionTrace(key)
      return h('section', { 'data-testid': row.id }, [
        h('input', { value: row.target_text, onInput: (e: Event) => store.updateTarget(key, (e.target as HTMLInputElement).value),
          onBlur: () => store.syncBlurredSegment(key) }),
        h('span', { 'data-testid': `trace-${row.id}` }, trace ? `${trace.before_text}→${trace.after_text}` : ''),
        h('button', { onClick: () => trace && store.rejectRevision(trace.id) }, '拒绝关联修订'),
      ])
    }),
    Object.keys(store.reviewSyncFailures).length ? h('button', { onClick: () => store.retryReviewSync() }, '重试修订同步') : null,
  ]) }).mount('#app')
}
void initialize()
