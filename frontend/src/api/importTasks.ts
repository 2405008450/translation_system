import { http } from './http'
import { translate } from '../i18n'

export interface ImportTaskStatus<T = unknown> {
  task_id: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'canceling' | 'canceled'
  progress: number
  message: string
  result: T | null
  error: string | null
  cancel_requested?: boolean
  updated_at?: string
}

export interface ImportTaskAccepted {
  task_id: string
  status: 'queued'
  progress: number
  message: string
}

export function isImportTaskAccepted(value: unknown): value is ImportTaskAccepted {
  return Boolean(
    value
      && typeof value === 'object'
      && 'task_id' in value
      && typeof (value as { task_id?: unknown }).task_id === 'string',
  )
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

const DEFAULT_POLL_INTERVAL_MS = 1000
const DEFAULT_IMPORT_TASK_TIMEOUT_MS = 60 * 60 * 1000

export interface WaitForImportTaskOptions {
  intervalMs?: number
  timeoutMs?: number
  signal?: AbortSignal
}

export async function waitForImportTask<T>(
  taskId: string,
  onStatus?: (status: ImportTaskStatus<T>) => void,
  options?: WaitForImportTaskOptions,
) {
  const intervalMs = options?.intervalMs ?? DEFAULT_POLL_INTERVAL_MS
  const timeoutMs = options?.timeoutMs ?? DEFAULT_IMPORT_TASK_TIMEOUT_MS
  const startedAt = Date.now()

  for (;;) {
    if (options?.signal?.aborted) {
      throw new DOMException('导入已取消。', 'AbortError')
    }

    if (Date.now() - startedAt > timeoutMs) {
      throw new Error(translate('errors.importTaskTimeout'))
    }

    const { data } = await http.get<ImportTaskStatus<T>>(`/import-tasks/${taskId}`)
    onStatus?.(data)

    if (data.status === 'completed') {
      return data.result as T
    }

    if (data.status === 'failed') {
      throw new Error(data.error || data.message || translate('errors.importFailed'))
    }

    if (data.status === 'canceled') {
      throw new DOMException(data.message || '导入已取消。', 'AbortError')
    }

    await delay(intervalMs)
  }
}

export async function cancelImportTask(taskId: string) {
  const { data } = await http.post<ImportTaskStatus>(`/import-tasks/${taskId}/cancel`)
  return data
}
