import { createApp, h } from 'vue'

import AssignmentModal from '../src/components/AssignmentModal.vue'
import { i18n } from '../src/i18n'
import '../src/styles.css'
import type { AssignmentDraft } from '../src/types/assignment'
import type {
  AssignmentSplitPreviewRequest,
  AssignmentSplitPreviewResponse,
  MergeView,
  User,
  WorkflowStep,
} from '../src/types/api'

const workflowSteps: WorkflowStep[] = [
  { id: 'step-translate', step_key: 'translate', name: '翻译', step_type: 'translation', sort_order: 0 },
  { id: 'step-review', step_key: 'review', name: '审校', step_type: 'review', sort_order: 1 },
]

const users: User[] = Array.from({ length: 58 }, (_, index) => ({
  id: `user-${index + 1}`,
  username: `translator_${String(index + 1).padStart(2, '0')}`,
  nickname: `测试译者 ${index + 1}`,
  role: 'user',
  translator_type: index % 5 === 4 ? 'internal' : 'external',
  is_active: true,
  created_at: '2026-07-21T00:00:00',
}))

const files = Array.from({ length: 120 }, (_, index) => ({
  id: `file-${index + 1}`,
  filename: `${String(index + 1).padStart(3, '0')}_公司年度可持续发展报告_${index % 3}.docx`,
  total_segments: 800 + index * 7,
  creator: `创建人 ${index % 8 + 1}`,
  created_at: '2026-07-21T00:00:00',
  source_language: index % 2 === 0 ? 'zh-CN' : 'en-US',
  target_language: index % 2 === 0 ? 'en-US' : 'zh-CN',
}))

const assignments: AssignmentDraft[] = [
  {
    assignee_id: 'user-1',
    workflow_step_id: 'step-translate',
    file_record_ids: new Set(['file-1']),
    file_ranges: new Map(),
  },
  {
    assignee_id: 'user-2',
    workflow_step_id: 'step-translate',
    file_record_ids: new Set(['file-2']),
    file_ranges: new Map([['file-2', { range_start: 1, range_end: 400 }]]),
  },
  {
    assignee_id: 'user-3',
    workflow_step_id: 'step-translate',
    file_record_ids: new Set(['file-2']),
    file_ranges: new Map([['file-2', { range_start: 401, range_end: 807 }]]),
  },
]

const mergeViews: MergeView[] = [{
  id: 'view-1',
  project_id: 'project-1',
  name: 'ESG 报告第一批',
  file_ids: files.slice(0, 20).map((file) => file.id),
  file_count: 20,
  available_file_count: 20,
  creator_id: 'user-1',
  creator_name: '测试译者 1',
  created_at: '2026-07-21T00:00:00',
  updated_at: '2026-07-21T00:00:00',
}]

async function previewSplit(
  payload: AssignmentSplitPreviewRequest,
): Promise<AssignmentSplitPreviewResponse> {
  const file = files.find((item) => item.id === payload.file_record_id)
  const totalSegments = file?.total_segments || 1
  const totalWords = totalSegments * 5
  const partCount = payload.mode === 'by_part_count'
    ? Math.max(1, payload.part_count || 1)
    : Math.max(1, Math.ceil(totalWords / Math.max(1, payload.words_per_part || totalWords)))
  const parts = Array.from({ length: partCount }, (_, index) => {
    const rangeStart = Math.floor(totalSegments * index / partCount) + 1
    const rangeEnd = Math.floor(totalSegments * (index + 1) / partCount)
    const segmentCount = rangeEnd - rangeStart + 1
    const wordCount = segmentCount * 5
    return {
      index: index + 1,
      range_start: rangeStart,
      range_end: rangeEnd,
      segment_count: segmentCount,
      word_count: wordCount,
      word_percent: Number((wordCount * 100 / totalWords).toFixed(2)),
    }
  })
  return {
    total_segments: totalSegments,
    segment_words: totalWords,
    document_words: totalWords + 120,
    parts,
    warnings: ['模拟预览：所有切点均位于完整句段边界。'],
  }
}

const app = createApp({
  render: () => h(AssignmentModal, {
    open: true,
    files,
    users,
    workflowSteps,
    mergeViews,
    assignments,
    revision: 'a'.repeat(64),
    loading: false,
    saving: false,
    previewSplit,
  }),
})

app.use(i18n)
app.mount('#app')
