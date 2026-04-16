import type { Segment } from '../types/api'

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function renderSentence(sentenceId: string, text: string) {
  return (
    `<span class="doc-sentence" id="${escapeHtml(sentenceId)}" data-sentence-id="${escapeHtml(sentenceId)}">` +
    `${escapeHtml(text)}` +
    '</span>'
  )
}

export function buildDocumentPreviewHtml(
  segments: Segment[],
  mode: 'source' | 'target' = 'source',
) {
  if (!segments.length) {
    return '<p class="doc-paragraph doc-empty"><br></p>'
  }

  const htmlParts: string[] = []
  let paragraphBuffer: string[] = []
  let currentParagraphKey = ''
  let currentTableIndex: number | null = null
  let tableRows: string[][][] = []

  function flushParagraph() {
    if (paragraphBuffer.length) {
      htmlParts.push(`<p class="doc-paragraph">${paragraphBuffer.join('')}</p>`)
      paragraphBuffer = []
    }
    currentParagraphKey = ''
  }

  function flushTable() {
    if (!tableRows.length) {
      currentTableIndex = null
      return
    }

    const rowHtml = tableRows
      .map((row) => {
        const cellHtml = row
          .map((cellSentences) => {
            const content = cellSentences.length
              ? `<p class="doc-paragraph">${cellSentences.join('')}</p>`
              : '<p class="doc-paragraph doc-empty"><br></p>'
            return `<td class="doc-table-cell">${content}</td>`
          })
          .join('')
        return `<tr>${cellHtml}</tr>`
      })
      .join('')

    htmlParts.push(`<table class="doc-table"><tbody>${rowHtml}</tbody></table>`)
    tableRows = []
    currentTableIndex = null
  }

  for (const segment of segments) {
    const blockType = segment.block_type || 'paragraph'
    const sentenceId = segment.sentence_id || ''
    const text = mode === 'target'
      ? (segment.target_text || segment.display_text || segment.source_text || '')
      : (segment.display_text || segment.source_text || '')
    const sentenceHtml = renderSentence(sentenceId, text)

    if (blockType === 'table_cell') {
      flushParagraph()
      if (currentTableIndex === null) {
        currentTableIndex = segment.block_index
      } else if (currentTableIndex !== segment.block_index) {
        flushTable()
        currentTableIndex = segment.block_index
      }

      const rowIndex = segment.row_index ?? 0
      const cellIndex = segment.cell_index ?? 0
      while (tableRows.length <= rowIndex) {
        tableRows.push([])
      }
      while (tableRows[rowIndex].length <= cellIndex) {
        tableRows[rowIndex].push([])
      }
      tableRows[rowIndex][cellIndex].push(sentenceHtml)
      continue
    }

    flushTable()
    const paragraphKey = `${segment.block_index}-${blockType}`
    if (currentParagraphKey !== paragraphKey) {
      flushParagraph()
      currentParagraphKey = paragraphKey
    }
    paragraphBuffer.push(sentenceHtml)
  }

  flushParagraph()
  flushTable()

  return htmlParts.join('') || '<p class="doc-paragraph doc-empty"><br></p>'
}
