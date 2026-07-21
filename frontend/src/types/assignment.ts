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
