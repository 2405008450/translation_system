export type DiffSegmentType = 'equal' | 'insert' | 'delete'

export interface DiffSegment {
  type: DiffSegmentType
  text: string
}

function mergeSegment(segments: DiffSegment[], nextSegment: DiffSegment) {
  if (!nextSegment.text) {
    return
  }

  const lastSegment = segments[segments.length - 1]
  if (lastSegment && lastSegment.type === nextSegment.type) {
    lastSegment.text += nextSegment.text
    return
  }

  segments.push(nextSegment)
}

/**
 * 将文本分割为 token 列表，用于单词级别的差异比较
 * - 英文单词（包括带连字符的复合词）作为整体
 * - 中文每个字符作为独立 token
 * - 标点符号和空白符单独处理
 */
function tokenize(text: string): string[] {
  if (!text) return []
  // 匹配：英文单词（含连字符）| 中文字符 | 空白符 | 其他标点
  return text.match(/[a-zA-Z0-9]+(?:[-'][a-zA-Z0-9]+)*|[\u4e00-\u9fff]|\s+|[^\s\w\u4e00-\u9fff]+/g) || []
}

export function computeDiff(oldText: string, newText: string): DiffSegment[] {
  const oldTokens = tokenize(oldText)
  const newTokens = tokenize(newText)
  const oldLength = oldTokens.length
  const newLength = newTokens.length
  const matrix = Array.from({ length: oldLength + 1 }, () => Array<number>(newLength + 1).fill(0))

  for (let oldIndex = 0; oldIndex < oldLength; oldIndex += 1) {
    for (let newIndex = 0; newIndex < newLength; newIndex += 1) {
      if (oldTokens[oldIndex] === newTokens[newIndex]) {
        matrix[oldIndex + 1][newIndex + 1] = matrix[oldIndex][newIndex] + 1
      } else {
        matrix[oldIndex + 1][newIndex + 1] = Math.max(
          matrix[oldIndex][newIndex + 1],
          matrix[oldIndex + 1][newIndex],
        )
      }
    }
  }

  const segments: DiffSegment[] = []
  let oldIndex = oldLength
  let newIndex = newLength

  while (oldIndex > 0 || newIndex > 0) {
    if (
      oldIndex > 0
      && newIndex > 0
      && oldTokens[oldIndex - 1] === newTokens[newIndex - 1]
    ) {
      mergeSegment(segments, { type: 'equal', text: oldTokens[oldIndex - 1] })
      oldIndex -= 1
      newIndex -= 1
      continue
    }

    if (
      newIndex > 0
      && (oldIndex === 0 || matrix[oldIndex][newIndex - 1] >= matrix[oldIndex - 1][newIndex])
    ) {
      mergeSegment(segments, { type: 'insert', text: newTokens[newIndex - 1] })
      newIndex -= 1
      continue
    }

    if (oldIndex > 0) {
      mergeSegment(segments, { type: 'delete', text: oldTokens[oldIndex - 1] })
      oldIndex -= 1
    }
  }

  return segments.reverse()
}
