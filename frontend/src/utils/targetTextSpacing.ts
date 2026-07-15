export interface PreviewLikeSegment {
  sentence_id: string
  source_text: string
  display_text?: string | null
  target_text?: string | null
  block_type?: string | null
  block_index?: number | null
  row_index?: number | null
  cell_index?: number | null
  sequence_index?: number | null
}

const englishBoundaryTrailingPattern = /[,;:.!?]["')\]\}]*$/u
const englishWordLeadingPattern = /^["'“‘(\[]*[A-Za-z0-9]/u

export function resolveTargetPreviewText(segment: PreviewLikeSegment) {
  return segment.target_text || segment.display_text || segment.source_text || ''
}

export function buildTargetPreviewTextMap(segments: PreviewLikeSegment[]) {
  const targetMap = new Map<string, string>()
  let previousSegment: PreviewLikeSegment | null = null
  let previousText = ''

  for (const segment of segments) {
    let text = resolveTargetPreviewText(segment)
    if (
      previousSegment
      && isSamePreviewBlock(previousSegment, segment)
      && shouldInsertEnglishBoundarySpace(previousText, text)
    ) {
      text = ` ${text}`
    }

    targetMap.set(segment.sentence_id, text)
    previousSegment = segment
    previousText = text.trim() ? text : ''
  }

  return targetMap
}

function isSamePreviewBlock(left: PreviewLikeSegment, right: PreviewLikeSegment) {
  const leftBlockType = left.block_type || 'paragraph'
  const rightBlockType = right.block_type || 'paragraph'
  if (leftBlockType !== rightBlockType) {
    return false
  }

  if (left.block_index !== undefined || right.block_index !== undefined) {
    return left.block_index === right.block_index
      && (left.row_index ?? null) === (right.row_index ?? null)
      && (left.cell_index ?? null) === (right.cell_index ?? null)
  }

  return true
}

function shouldInsertEnglishBoundarySpace(previousText: string, currentText: string) {
  if (!previousText || !currentText) {
    return false
  }
  if (/\s$/u.test(previousText) || /^\s/u.test(currentText)) {
    return false
  }
  return englishBoundaryTrailingPattern.test(previousText) && englishWordLeadingPattern.test(currentText)
}
