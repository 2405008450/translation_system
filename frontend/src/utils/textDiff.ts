export type DiffSegmentType = 'equal' | 'insert' | 'delete'

export interface DiffSegment {
  type: DiffSegmentType
  text: string
}

const TOKEN_PATTERN = /[a-zA-Z0-9]+(?:[-'][a-zA-Z0-9]+)*|[\u4e00-\u9fff]|\s+|[^\s\w\u4e00-\u9fff]+/g
const WORD_TOKEN_PATTERN = /^[a-zA-Z0-9]+(?:[-'][a-zA-Z0-9]+)*$/

function mergeSegment(segments: DiffSegment[], nextSegment: DiffSegment) {
  if (!nextSegment.text) {
    return
  }

  const lastSegment = segments[segments.length - 1]
  if (lastSegment && lastSegment.type === nextSegment.type) {
    // 因为是倒序遍历，新的 token 应该放在前面
    lastSegment.text = nextSegment.text + lastSegment.text
    return
  }

  segments.push(nextSegment)
}

function appendSegment(segments: DiffSegment[], nextSegment: DiffSegment) {
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
  return text.match(TOKEN_PATTERN) || []
}

function isWordToken(text: string) {
  return WORD_TOKEN_PATTERN.test(text)
}

function computeCharacterDiff(oldText: string, newText: string): DiffSegment[] {
  const oldChars = Array.from(oldText)
  const newChars = Array.from(newText)
  const oldLength = oldChars.length
  const newLength = newChars.length
  const matrix = Array.from({ length: oldLength + 1 }, () => Array<number>(newLength + 1).fill(0))

  for (let oldIndex = 0; oldIndex < oldLength; oldIndex += 1) {
    for (let newIndex = 0; newIndex < newLength; newIndex += 1) {
      if (oldChars[oldIndex] === newChars[newIndex]) {
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
      && oldChars[oldIndex - 1] === newChars[newIndex - 1]
    ) {
      mergeSegment(segments, { type: 'equal', text: oldChars[oldIndex - 1] })
      oldIndex -= 1
      newIndex -= 1
      continue
    }

    if (
      newIndex > 0
      && (oldIndex === 0 || matrix[oldIndex][newIndex - 1] >= matrix[oldIndex - 1][newIndex])
    ) {
      mergeSegment(segments, { type: 'insert', text: newChars[newIndex - 1] })
      newIndex -= 1
      continue
    }

    if (oldIndex > 0) {
      mergeSegment(segments, { type: 'delete', text: oldChars[oldIndex - 1] })
      oldIndex -= 1
    }
  }

  return segments.reverse()
}

function splitWordReplacement(beforeText: string, afterText: string): DiffSegment[] | null {
  if (!isWordToken(beforeText) || !isWordToken(afterText) || beforeText === afterText) {
    return null
  }

  const minLength = Math.min(beforeText.length, afterText.length)
  let prefixLength = 0
  while (
    prefixLength < minLength
    && beforeText[prefixLength] === afterText[prefixLength]
  ) {
    prefixLength += 1
  }

  let suffixLength = 0
  while (
    suffixLength < minLength - prefixLength
    && beforeText[beforeText.length - 1 - suffixLength] === afterText[afterText.length - 1 - suffixLength]
  ) {
    suffixLength += 1
  }

  if (prefixLength === 0 && suffixLength === 0) {
    return null
  }

  return computeCharacterDiff(beforeText, afterText)
}

function refineWordReplacements(segments: DiffSegment[]) {
  const refinedSegments: DiffSegment[] = []

  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index]
    const nextSegment = segments[index + 1]
    if (segment.type === 'delete' && nextSegment?.type === 'insert') {
      const wordSegments = splitWordReplacement(segment.text, nextSegment.text)
      if (wordSegments) {
        for (const wordSegment of wordSegments) {
          appendSegment(refinedSegments, wordSegment)
        }
        index += 1
        continue
      }
    }

    appendSegment(refinedSegments, segment)
  }

  return refinedSegments
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

  return refineWordReplacements(segments.reverse())
}
