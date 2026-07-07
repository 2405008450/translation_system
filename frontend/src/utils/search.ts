interface SearchNormalizeOptions {
  caseSensitive?: boolean
}

interface SearchMatchOptions extends SearchNormalizeOptions {
  fuzzy?: boolean
  minSubsequenceLength?: number
}

const DIACRITIC_PATTERN = /[\u0300-\u036f]/g
const COMPACT_SEARCH_SEPARATOR_PATTERN = /[\s\p{P}\p{S}_]+/gu

export function normalizeSearchText(value: unknown, options: SearchNormalizeOptions = {}) {
  const normalized = String(value ?? '')
    .normalize('NFKC')
    .normalize('NFD')
    .replace(DIACRITIC_PATTERN, '')
    .normalize('NFC')
  return options.caseSensitive ? normalized : normalized.toLocaleLowerCase()
}

export function normalizeSearchKeyword(value: unknown, options: SearchNormalizeOptions = {}) {
  return normalizeSearchText(value, options).trim()
}

export function splitSearchKeywords(value: unknown, options: SearchNormalizeOptions = {}) {
  return normalizeSearchKeyword(value, options).split(/\s+/).filter(Boolean)
}

export function normalizeCompactSearchText(value: unknown, options: SearchNormalizeOptions = {}) {
  return normalizeSearchText(value, options).replace(COMPACT_SEARCH_SEPARATOR_PATTERN, '')
}

export function isSubsequenceSearchMatch(value: unknown, keyword: unknown, options: SearchNormalizeOptions = {}) {
  const normalizedValue = normalizeCompactSearchText(value, options)
  const normalizedKeyword = normalizeCompactSearchText(keyword, options)
  if (!normalizedKeyword) {
    return !normalizeSearchKeyword(keyword, options)
  }

  let cursor = 0
  for (const char of normalizedValue) {
    if (char === normalizedKeyword[cursor]) {
      cursor += 1
      if (cursor >= normalizedKeyword.length) {
        return true
      }
    }
  }
  return false
}

export function matchesSearchKeyword(value: unknown, keyword: unknown, options: SearchMatchOptions = {}) {
  const normalizedKeyword = normalizeSearchKeyword(keyword, options)
  if (!normalizedKeyword) {
    return true
  }

  const normalizedValue = normalizeSearchText(value, options)
  if (normalizedValue.includes(normalizedKeyword)) {
    return true
  }

  if (options.fuzzy === false) {
    return false
  }

  const compactKeyword = normalizeCompactSearchText(normalizedKeyword, options)
  if (!compactKeyword) {
    return true
  }
  const compactValue = normalizeCompactSearchText(normalizedValue, options)
  if (compactValue.includes(compactKeyword)) {
    return true
  }

  const minSubsequenceLength = options.minSubsequenceLength ?? 3
  return compactKeyword.length >= minSubsequenceLength
    && isSubsequenceSearchMatch(compactValue, compactKeyword, options)
}
