import http from 'node:http'

const host = process.env.E2E_FAKE_LLM_HOST || '127.0.0.1'
const port = Number(process.argv[2] || process.env.E2E_FAKE_LLM_PORT || 19114)
const delayMs = Number(process.env.E2E_FAKE_LLM_DELAY_MS || 200)

let activeRequests = 0
let maxConcurrent = 0
let totalRequests = 0
let lastRequests = []

function jsonResponse(response, statusCode, payload) {
  const body = JSON.stringify(payload)
  response.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  })
  response.end(body)
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = []
    request.on('data', (chunk) => chunks.push(chunk))
    request.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    request.on('error', reject)
  })
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)))
}

function extractBalancedJson(text, startIndex) {
  let depth = 0
  let inString = false
  let escaped = false

  for (let index = startIndex; index < text.length; index += 1) {
    const char = text[index]

    if (inString) {
      if (escaped) {
        escaped = false
      } else if (char === '\\') {
        escaped = true
      } else if (char === '"') {
        inString = false
      }
      continue
    }

    if (char === '"') {
      inString = true
    } else if (char === '{') {
      depth += 1
    } else if (char === '}') {
      depth -= 1
      if (depth === 0) {
        return text.slice(startIndex, index + 1)
      }
    }
  }

  return null
}

function extractStructuredPayload(content) {
  for (let index = content.indexOf('{'); index >= 0; index = content.indexOf('{', index + 1)) {
    const candidate = extractBalancedJson(content, index)
    if (!candidate) {
      continue
    }
    try {
      const payload = JSON.parse(candidate)
      if (Array.isArray(payload.required_sentence_ids) && Array.isArray(payload.sentences)) {
        return payload
      }
    } catch {
      // 继续寻找下一个 JSON 块。
    }
  }
  return null
}

function parseNumberedItems(content) {
  const matches = Array.from(content.matchAll(/^\[(\d+)\]\s*(.+)$/gm))
  return matches.map((match) => ({
    index: Number(match[1]),
    sourceText: match[2].trim(),
  }))
}

function buildTargetText(sentenceId, sourceText) {
  const compactSource = String(sourceText || '').replace(/\s+/g, ' ').trim()
  return `MOCK_LLM_TRANSLATION ${sentenceId} ${compactSource}`.trim()
}

function buildCompletionContent(payload) {
  const messages = Array.isArray(payload.messages) ? payload.messages : []
  const userContent = messages
    .filter((message) => message && message.role === 'user')
    .map((message) => String(message.content || ''))
    .join('\n\n')

  const structuredPayload = extractStructuredPayload(userContent)
  if (structuredPayload) {
    const sentenceById = new Map(
      structuredPayload.sentences.map((sentence) => [String(sentence.sentence_id), sentence]),
    )
    const translations = {}
    for (const sentenceId of structuredPayload.required_sentence_ids.map(String)) {
      const sentence = sentenceById.get(sentenceId) || {}
      translations[sentenceId] = {
        source_hash: String(sentence.source_hash || ''),
        target_text: buildTargetText(sentenceId, sentence.source_text),
      }
    }
    return JSON.stringify({ translations })
  }

  const numberedItems = parseNumberedItems(userContent)
  if (numberedItems.length > 0) {
    return numberedItems
      .map((item, index) => `[${index + 1}] ${buildTargetText(`item-${item.index}`, item.sourceText)}`)
      .join('\n')
  }

  return 'MOCK_LLM_TRANSLATION'
}

function resetStats() {
  activeRequests = 0
  maxConcurrent = 0
  totalRequests = 0
  lastRequests = []
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url || '/', `http://${host}:${port}`)

  if (request.method === 'GET' && url.pathname === '/health') {
    jsonResponse(response, 200, { ok: true })
    return
  }

  if (request.method === 'GET' && url.pathname === '/__stats') {
    jsonResponse(response, 200, {
      activeRequests,
      maxConcurrent,
      totalRequests,
      lastRequests,
    })
    return
  }

  if (request.method === 'POST' && url.pathname === '/__reset') {
    resetStats()
    jsonResponse(response, 200, { ok: true })
    return
  }

  if (request.method !== 'POST' || url.pathname !== '/chat/completions') {
    jsonResponse(response, 404, { error: 'Not Found' })
    return
  }

  activeRequests += 1
  totalRequests += 1
  maxConcurrent = Math.max(maxConcurrent, activeRequests)

  try {
    const body = await readBody(request)
    const payload = JSON.parse(body || '{}')
    const content = buildCompletionContent(payload)
    lastRequests = [
      ...lastRequests.slice(-19),
      {
        model: payload.model || null,
        responseFormat: payload.response_format || null,
        contentLength: content.length,
      },
    ]

    await sleep(delayMs)

    jsonResponse(response, 200, {
      id: `fake-${Date.now()}-${totalRequests}`,
      object: 'chat.completion',
      created: Math.floor(Date.now() / 1000),
      model: payload.model || 'e2e-fake-model',
      choices: [
        {
          index: 0,
          message: {
            role: 'assistant',
            content,
          },
          finish_reason: 'stop',
        },
      ],
    })
  } catch (error) {
    jsonResponse(response, 400, { error: error instanceof Error ? error.message : String(error) })
  } finally {
    activeRequests = Math.max(0, activeRequests - 1)
  }
})

server.listen(port, host, () => {
  console.log(`Fake LLM server listening on http://${host}:${port}`)
})

function shutdown() {
  server.close(() => process.exit(0))
}

process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
