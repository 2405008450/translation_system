import type { AssignmentEvent } from '../types/api'

export function getAssignmentEventScope(event: AssignmentEvent): string {
  const payload = event.after_payload?.workflow_step_id ? event.after_payload : event.before_payload
  if (!payload?.workflow_step_id) return event.file_record_id ? '全部句段（历史记录）' : '项目级'
  const stage = event.workflow_step_name || '已移除的流程阶段'
  const start = payload.range_start
  const end = payload.range_end
  return start != null && end != null
    ? `${stage} · 句段 ${start}–${end}`
    : `${stage} · 全部句段`
}
