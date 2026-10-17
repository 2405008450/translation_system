import { translate } from '../i18n'
import type { GlossaryImportSummary, TermImportSummary, TMImportSummary } from '../types/api'

export interface ResourceImportResult {
  tab: 'tm' | 'glossary' | 'term'
  resourceId?: string
  summary: TMImportSummary | TermImportSummary | GlossaryImportSummary
}

export function formatResourceImportResult(summary: Pick<TMImportSummary,
  'created_rows' | 'updated_rows' | 'skipped_duplicate_rows' | 'skipped_empty_rows' | 'skipped_header_rows'
>) {
  const created = summary.created_rows
  const updated = summary.updated_rows
  const duplicates = summary.skipped_duplicate_rows ?? 0
  const empty = summary.skipped_empty_rows
  const headers = summary.skipped_header_rows
  const status = created + updated > 0 ? 'completed' : 'noChanges'
  let message = translate(`resourceImport.result.${status}`, { created, updated, duplicates, empty, headers })
  if (created + updated === 0 && duplicates + empty + headers === 0) {
    message += translate('resourceImport.result.noValidRows')
  }
  return message
}
