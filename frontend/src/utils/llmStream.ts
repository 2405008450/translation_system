export interface LLMStreamEvent {
  event: string
  data: Record<string, unknown>
}

export function parseSSEChunk(chunk: string): LLMStreamEvent | null {
  const eventMatch = chunk.match(/^event:\s*(.+)$/m)
  const dataMatch = chunk.match(/^data:\s*(.+)$/m)
  if (!eventMatch || !dataMatch) {
    return null
  }

  try {
    return {
      event: eventMatch[1].trim(),
      data: JSON.parse(dataMatch[1]),
    }
  } catch {
    return null
  }
}

export async function consumeLLMStream(
  response: Response,
  onEvent: (event: LLMStreamEvent) => void | Promise<void>,
  onReaderChange?: (reader: ReadableStreamDefaultReader<Uint8Array> | null) => void,
) {
  if (!response.body) {
    throw new Error('SSE 响应体为空。')
  }

  const reader = response.body.getReader()
  onReaderChange?.(reader)
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''

      for (const part of parts) {
        const event = parseSSEChunk(part)
        if (!event) {
          continue
        }
        await onEvent(event)
      }
    }

    if (buffer.trim()) {
      const event = parseSSEChunk(buffer)
      if (event) {
        await onEvent(event)
      }
    }
  } finally {
    onReaderChange?.(null)
  }
}
