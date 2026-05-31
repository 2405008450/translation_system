import type { Segment } from '../types/api'
import { buildDocumentPreviewHtml } from './documentPreview'
import { buildTargetPreviewTextMap } from './targetTextSpacing'

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

  const targetMap = buildTargetPreviewTextMap(segments)
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
