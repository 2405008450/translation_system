export type StatusTone = 'info' | 'success' | 'warning' | 'danger' | 'default'

export interface StatusMeta {
  value: string
  label: string
  tone: StatusTone
}

const fileStatusMap: Record<string, StatusMeta> = {
  draft: { value: 'draft', label: '草稿', tone: 'warning' },
  in_progress: { value: 'in_progress', label: '处理中', tone: 'info' },
  pending: { value: 'pending', label: '待处理', tone: 'warning' },
  processing: { value: 'processing', label: '处理中', tone: 'info' },
  completed: { value: 'completed', label: '已完成', tone: 'success' },
  translated: { value: 'translated', label: '已翻译', tone: 'success' },
  error: { value: 'error', label: '异常', tone: 'danger' },
}

const segmentStatusMap: Record<string, StatusMeta> = {
  exact: { value: 'exact', label: '精确匹配', tone: 'success' },
  fuzzy: { value: 'fuzzy', label: '模糊匹配', tone: 'warning' },
  none: { value: 'none', label: '无匹配', tone: 'default' },
  confirmed: { value: 'confirmed', label: '已确认', tone: 'info' },
  manual: { value: 'manual', label: '人工处理', tone: 'info' },
}

const sourceStatusMap: Record<string, StatusMeta> = {
  manual: { value: 'manual', label: '人工', tone: 'info' },
  llm: { value: 'llm', label: 'AI', tone: 'success' },
  tm: { value: 'tm', label: 'TM', tone: 'default' },
  exact: { value: 'exact', label: '精确匹配', tone: 'success' },
  fuzzy: { value: 'fuzzy', label: '模糊匹配', tone: 'warning' },
}

export function getFileStatusMeta(status: string) {
  return fileStatusMap[status] || { value: status, label: status || '未知状态', tone: 'default' as const }
}

export function getSegmentStatusMeta(status: string) {
  return segmentStatusMap[status] || { value: status, label: status || '未知状态', tone: 'default' as const }
}

export function getSegmentSourceMeta(source: string) {
  return sourceStatusMap[source] || { value: source, label: source || '未知来源', tone: 'default' as const }
}
