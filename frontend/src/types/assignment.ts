import type { ProjectAssignmentPayload } from './api'

export interface AssignmentFileRangeDraft {
  range_start: number | null
  range_end: number | null
}

export interface AssignmentDraft {
  assignee_id: string
  workflow_step_id: string
  file_record_ids: Set<string>
  file_ranges: Map<string, AssignmentFileRangeDraft>
}

export interface AssignmentSaveRequest extends ProjectAssignmentPayload {
  base_revision: string
  workflow_transition_mode?: 'prompt' | 'advance' | 'assign_only'
}

export interface AssignmentWorkflowTransitionItem {
  file_record_id: string
  filename: string
  from_step: {
    id: string
    name: string
    sort_order: number
  }
  target_step: {
    id: string
    name: string
    sort_order: number
  }
  ranges: Array<{
    range_start: number | null
    range_end: number | null
  }>
  matched_count: number
}

export interface AssignmentWorkflowTransitionRequired {
  code: 'workflow_transition_required'
  message: string
  file_count: number
  matched_count: number
  transitions: AssignmentWorkflowTransitionItem[]
}

export function cloneAssignmentDrafts(drafts: AssignmentDraft[]): AssignmentDraft[] {
  return drafts.map((draft) => ({
    ...draft,
    file_record_ids: new Set(draft.file_record_ids),
    file_ranges: new Map(
      Array.from(draft.file_ranges.entries()).map(([fileId, range]) => [fileId, { ...range }]),
    ),
  }))
}
