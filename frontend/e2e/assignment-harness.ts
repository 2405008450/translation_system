import { createApp, h } from 'vue'

import AssignmentModal from '../src/components/AssignmentModal.vue'
import { i18n } from '../src/i18n'
import '../src/styles.css'
import type { AssignmentDraft } from '../src/types/assignment'
import type { MergeView, User, WorkflowStep } from '../src/types/api'

const workflowSteps: WorkflowStep[] = [
  { id: 'step-translate', step_key: 'translate', name: '翻译', step_type: 'translation', sort_order: 0 },
  { id: 'step-review', step_key: 'review', name: '审校', step_type: 'review', sort_order: 1 },
]

const users: User[] = Array.from({ length: 58 }, (_, index) => ({
  id: `user-${index + 1}`,
  username: `translator_${String(index + 1).padStart(2, '0')}`,
  nickname: `测试译者 ${index + 1}`,
  role: 'user',
  translator_type: index % 5 === 0 ? 'internal' : 'external',
  is_active: true,
  created_at: '2026-07-21T00:00:00',
}))

const files = Array.from({ length: 120 }, (_, index) => ({
  id: `file-${index + 1}`,
  filename: `${String(index + 1).padStart(3, '0')}_公司年度可持续发展报告_${index % 3}.docx`,
  total_segments: 800 + index * 7,
  creator: `创建人 ${index % 8 + 1}`,
  created_at: '2026-07-21T00:00:00',
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
  }),
})

app.use(i18n)
app.mount('#app')
