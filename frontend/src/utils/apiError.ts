import axios from 'axios'

function extractDetailMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail.trim()

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item.trim()
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg?: unknown }).msg || '').trim()
        }
        return ''
      })
      .filter(Boolean)
      .join('；')
  }

  if (detail && typeof detail === 'object') {
    const value = detail as Record<string, unknown>
    for (const key of ['message', 'detail', 'error']) {
      const message = extractDetailMessage(value[key])
      if (message) return message
    }
  }

  return ''
}

/** 从 Axios/FastAPI 错误中提取可直接展示给用户的文字，避免出现 [object Object]。 */
export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data
    const message = extractDetailMessage(data?.detail ?? data?.message)
    return message || fallback
  }
  return error instanceof Error && error.message ? error.message : fallback
}
