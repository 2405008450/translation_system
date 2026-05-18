import type { Segment } from '../types/api'
import { buildDocumentPreviewHtml } from './documentPreview'

function buildTargetMap(segments: Segment[]) {
  const targetMap = new Map<string, string>()
  for (const segment of segments) {
    targetMap.set(
      segment.sentence_id,
      segment.target_text || segment.display_text || segment.source_text || '',
    )
  }
  return targetMap
}

export function renderTargetPreview(sourceHtml: string, segments: Segment[]) {
  const fallbackHtml = buildDocumentPreviewHtml(segments, 'target')
  if (!sourceHtml.trim()) {
    return fallbackHtml
  }

  const parser = new DOMParser()
  const documentNode = parser.parseFromString(`<div id="preview-root">${sourceHtml}</div>`, 'text/html')
  const root = documentNode.getElementById('preview-root')
  if (!root) {
    return fallbackHtml
  }

  const targetMap = buildTargetMap(segments)
  const sentenceNodes = root.querySelectorAll<HTMLElement>('.doc-sentence[data-sentence-id]')

  sentenceNodes.forEach((node) => {
    const sentenceId = node.dataset.sentenceId || ''
    if (!sentenceId || !targetMap.has(sentenceId)) {
      return
    }
    node.textContent = targetMap.get(sentenceId) || ''
  })

  return root.innerHTML || fallbackHtml
}
