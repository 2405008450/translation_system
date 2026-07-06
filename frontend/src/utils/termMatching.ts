const ASCII_WORD_CHAR_PATTERN = /[A-Za-z0-9_]/
const ASCII_LETTER_PATTERN = /[A-Za-z]/
const ASCII_LOWER_PATTERN = /[a-z]/
const ASCII_UPPER_PATTERN = /[A-Z]/

export interface TermTextRange {
  start: number
  end: number
}

export interface TermMatchOptions {
  caseSensitive?: boolean
}

function isAsciiWordChar(value: string): boolean {
  return Boolean(value && ASCII_WORD_CHAR_PATTERN.test(value))
}

function isAcronymLikeTerm(term: string): boolean {
  const compact = term.trim().replace(/[^A-Za-z0-9]/g, '')
  return Boolean(
    compact
    && ASCII_LETTER_PATTERN.test(compact)
    && ASCII_UPPER_PATTERN.test(compact)
    && !ASCII_LOWER_PATTERN.test(compact),
  )
}

function useCaseSensitiveMatch(term: string, options: TermMatchOptions): boolean {
  return Boolean(options.caseSensitive) || isAcronymLikeTerm(term)
}

function hasAsciiWordBoundary(text: string, start: number, end: number, term: string): boolean {
  const cleanTerm = term.trim()
  if (!cleanTerm) {
    return false
  }

  // 英文/数字术语只允许在词边界命中；中文等非 ASCII 术语仍允许子串匹配。
  if (isAsciiWordChar(cleanTerm[0]) && start > 0 && isAsciiWordChar(text[start - 1])) {
    return false
  }
  if (isAsciiWordChar(cleanTerm[cleanTerm.length - 1]) && end < text.length && isAsciiWordChar(text[end])) {
    return false
  }
  return true
}

export function findTermTextRanges(
  text: string,
  term: string,
  options: TermMatchOptions = {},
): TermTextRange[] {
  const cleanTerm = term.trim()
  if (!text || !cleanTerm) {
    return []
  }

  const caseSensitive = useCaseSensitiveMatch(cleanTerm, options)
  const haystack = caseSensitive ? text : text.toLowerCase()
  const needle = caseSensitive ? cleanTerm : cleanTerm.toLowerCase()
  const ranges: TermTextRange[] = []
  let start = 0

  while (start < haystack.length) {
    const pos = haystack.indexOf(needle, start)
    if (pos === -1) {
      break
    }

    const end = pos + cleanTerm.length
    if (hasAsciiWordBoundary(text, pos, end, cleanTerm)) {
      ranges.push({ start: pos, end })
    }
    start = pos + 1
  }

  return ranges
}

export function hasTermTextMatch(
  text: string,
  term: string,
  options: TermMatchOptions = {},
): boolean {
  return findTermTextRanges(text, term, options).length > 0
}
