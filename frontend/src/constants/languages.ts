export interface LanguageOption {
  code: string
  label: string
}

export const DEFAULT_LOCALE = 'zh-CN'

export const languageOptions: LanguageOption[] = [
  { code: 'zh-CN', label: '中文（简体）' },
  { code: 'zh-TW', label: '中文（繁体）' },
  { code: 'zh-HK', label: '中文（香港）' },
  { code: 'zh-MO', label: '中文（繁体澳门）' },
  { code: 'en-US', label: '英语（美国）' },
  { code: 'en-GB', label: '英语（英国）' },
  { code: 'ja-JP', label: '日语' },
  { code: 'ko-KR', label: '韩语' },
  { code: 'fr-FR', label: '法语' },
  { code: 'de-DE', label: '德语' },
  { code: 'es-ES', label: '西班牙语' },
  { code: 'pt-BR', label: '葡萄牙语（巴西）' },
  { code: 'it-IT', label: '意大利语' },
  { code: 'ru-RU', label: '俄语' },
  { code: 'ar-SA', label: '阿拉伯语' },
  { code: 'th-TH', label: '泰语' },
  { code: 'vi-VN', label: '越南语' },
]

const languageLabelMap = new Map(languageOptions.map((item) => [item.code, item.label]))

/** 与后端 `app/services/language_pairs.py` 的 LANGUAGE_ALIASES 保持一致，用于合并等场景的语言码规范化。 */
const LANGUAGE_ALIASES: Record<string, string> = {
  zh: 'zh-CN',
  'zh-cn': 'zh-CN',
  'zh-hans': 'zh-CN',
  'zh-tw': 'zh-TW',
  'zh-hant': 'zh-TW',
  'zh-hk': 'zh-HK',
  'zh-mo': 'zh-MO',
  'zh-hant-mo': 'zh-MO',
  en: 'en-US',
  'en-us': 'en-US',
  'en-gb': 'en-GB',
  ja: 'ja-JP',
  'ja-jp': 'ja-JP',
  ko: 'ko-KR',
  'ko-kr': 'ko-KR',
  fr: 'fr-FR',
  'fr-fr': 'fr-FR',
  de: 'de-DE',
  'de-de': 'de-DE',
  es: 'es-ES',
  'es-es': 'es-ES',
  pt: 'pt-BR',
  'pt-br': 'pt-BR',
  it: 'it-IT',
  'it-it': 'it-IT',
  ru: 'ru-RU',
  'ru-ru': 'ru-RU',
  ar: 'ar-SA',
  'ar-sa': 'ar-SA',
  th: 'th-TH',
  'th-th': 'th-TH',
  vi: 'vi-VN',
  'vi-vn': 'vi-VN',
}

/**
 * 将输入规范为受支持的 canonical 语言码；无法识别时返回 null。
 * 合并校验等场景应与后端 `require_language_pair` 的规范化结果一致。
 */
export function normalizeLanguageCode(value: string | null | undefined): string | null {
  const cleaned = (value || '').trim()
  if (!cleaned) {
    return null
  }
  const lower = cleaned.toLowerCase()
  const fromAlias = LANGUAGE_ALIASES[lower]
  if (fromAlias) {
    return fromAlias
  }
  if (languageLabelMap.has(cleaned)) {
    return cleaned
  }
  for (const opt of languageOptions) {
    if (opt.code.toLowerCase() === lower) {
      return opt.code
    }
  }
  return null
}

/** 返回规范化后的源/目标语言对；缺省、非法或源目标相同时返回 null。 */
export function canonicalizeLanguagePair(
  sourceLanguage: string | null | undefined,
  targetLanguage: string | null | undefined,
): { source: string; target: string } | null {
  const source = normalizeLanguageCode(sourceLanguage)
  const target = normalizeLanguageCode(targetLanguage)
  if (!source || !target || source === target) {
    return null
  }
  return { source, target }
}

export function getLanguageLabel(code: string | null | undefined) {
  if (!code) {
    return '未设置'
  }
  return languageLabelMap.get(code) || code
}

export function getLocaleLabel(code: string | null | undefined) {
  return getLanguageLabel(code || DEFAULT_LOCALE)
}

export function formatLanguagePair(
  sourceLanguage: string | null | undefined,
  targetLanguage: string | null | undefined,
) {
  if (!sourceLanguage || !targetLanguage) {
    return '未设置语言对'
  }
  return `${getLanguageLabel(sourceLanguage)} -> ${getLanguageLabel(targetLanguage)}`
}
