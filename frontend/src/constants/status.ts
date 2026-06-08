import { translate } from '../i18n'

export type StatusTone = 'info' | 'success' | 'warning' | 'danger' | 'default'

export interface StatusMeta {
  value: string
  label: string
  tone: StatusTone
}

const fileStatusMap: Record<string, StatusMeta> = {
  draft: { value: 'draft', label: 'status.file.draft', tone: 'warning' },
  in_progress: { value: 'in_progress', label: 'status.file.inProgress', tone: 'info' },
  pending: { value: 'pending', label: 'status.file.pending', tone: 'warning' },
  processing: { value: 'processing', label: 'status.file.processing', tone: 'info' },
  completed: { value: 'completed', label: 'status.file.completed', tone: 'success' },
  translated: { value: 'translated', label: 'status.file.translated', tone: 'success' },
  error: { value: 'error', label: 'status.file.error', tone: 'danger' },
}

const segmentStatusMap: Record<string, StatusMeta> = {
  exact: { value: 'exact', label: 'status.segment.exact', tone: 'success' },
  fuzzy: { value: 'fuzzy', label: 'status.segment.fuzzy', tone: 'warning' },
  none: { value: 'none', label: 'status.segment.none', tone: 'default' },
  confirmed: { value: 'confirmed', label: 'status.segment.confirmed', tone: 'info' },
  manual: { value: 'manual', label: 'status.segment.manual', tone: 'info' },
}

const sourceStatusMap: Record<string, StatusMeta> = {
  manual: { value: 'manual', label: 'status.source.manual', tone: 'info' },
  llm: { value: 'llm', label: 'AI', tone: 'success' },
  tm: { value: 'tm', label: 'TM', tone: 'default' },
  project_sync: { value: 'project_sync', label: 'status.source.projectSync', tone: 'success' },
  exact: { value: 'exact', label: 'status.segment.exact', tone: 'success' },
  fuzzy: { value: 'fuzzy', label: 'status.segment.fuzzy', tone: 'warning' },
}

function resolveStatusMeta(meta: StatusMeta | undefined, value: string, fallbackKey: string): StatusMeta {
  if (!meta) {
    return { value, label: value || translate(fallbackKey), tone: 'default' as const }
  }
  return {
    ...meta,
    label: meta.label.includes('.') ? translate(meta.label) : meta.label,
  }
}

export function getFileStatusMeta(status: string) {
  return resolveStatusMeta(fileStatusMap[status], status, 'status.unknownStatus')
}

export function getSegmentStatusMeta(status: string) {
  return resolveStatusMeta(segmentStatusMap[status], status, 'status.unknownStatus')
}

export function getSegmentSourceMeta(source: string) {
  return resolveStatusMeta(sourceStatusMap[source], source, 'status.unknownSource')
}
