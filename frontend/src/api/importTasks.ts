import { http } from './http'

export interface ImportTaskStatus<T = unknown> {
  task_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  message: string
  result: T | null
  error: string | null
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

export async function waitForImportTask<T>(
  taskId: string,
  onStatus?: (status: ImportTaskStatus<T>) => void,
) {
  for (;;) {
    const { data } = await http.get<ImportTaskStatus<T>>(`/import-tasks/${taskId}`)
    onStatus?.(data)

    if (data.status === 'completed') {
      return data.result as T
    }

    if (data.status === 'failed') {
      throw new Error(data.error || data.message || '导入失败。')
    }

    await delay(1000)
  }
}
