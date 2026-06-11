export interface LanguageOption {
  code: string
  label: string
}

export const DEFAULT_LOCALE = 'zh-CN' as const
export const SUPPORTED_UI_LOCALES = ['zh-CN', 'en-US'] as const
export type UILocale = typeof SUPPORTED_UI_LOCALES[number]

export function isSupportedUILocale(value: string | null | undefined): value is UILocale {
  return SUPPORTED_UI_LOCALES.includes(value as UILocale)
}

function getActiveUILocale(): UILocale {
  if (typeof document !== 'undefined' && document.documentElement.lang === 'en-US') {
    return 'en-US'
  }
  if (typeof window !== 'undefined' && window.localStorage.getItem('tm-workbench-locale') === 'en-US') {
    return 'en-US'
  }
  return DEFAULT_LOCALE
}

const chineseLanguageLabels: Record<string, string> = {
  'zh-CN': '中文（简体）',
  'zh-TW': '中文（繁体）',
  'zh-HK': '中文（香港）',
  'zh-MO': '中文（繁体澳门）',
  'en-US': '英语（美国）',
  'en-GB': '英语（英国）',
  'ja-JP': '日语',
  'ko-KR': '韩语',
  'fr-FR': '法语',
  'de-DE': '德语',
  'es-ES': '西班牙语',
  'pt-BR': '葡萄牙语（巴西）',
  'it-IT': '意大利语',
  'ru-RU': '俄语',
  'ar-SA': '阿拉伯语',
  'th-TH': '泰语',
  'vi-VN': '越南语',
}

export const languageOptions: LanguageOption[] = Object.keys(chineseLanguageLabels).map((code) => ({
  code,
  get label() {
    return getLanguageLabel(code)
  },
}))

const languageLabelMap = new Map(Object.entries(chineseLanguageLabels))
const englishLanguageLabelMap = new Map<string, string>([
  ['zh-CN', 'Chinese (Simplified)'],
  ['zh-TW', 'Chinese (Traditional)'],
  ['zh-HK', 'Chinese (Hong Kong)'],
  ['zh-MO', 'Chinese (Macau)'],
  ['en-US', 'English (US)'],
  ['en-GB', 'English (UK)'],
  ['ja-JP', 'Japanese'],
  ['ko-KR', 'Korean'],
  ['fr-FR', 'French'],
  ['de-DE', 'German'],
  ['es-ES', 'Spanish'],
  ['pt-BR', 'Portuguese (Brazil)'],
  ['it-IT', 'Italian'],
  ['ru-RU', 'Russian'],
  ['ar-SA', 'Arabic'],
  ['th-TH', 'Thai'],
  ['vi-VN', 'Vietnamese'],
])

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
  const locale = getActiveUILocale()
  if (!code) {
    return locale === 'en-US' ? 'Not set' : '未设置'
  }
  if (locale === 'en-US') {
    return englishLanguageLabelMap.get(code) || code
  }
  return languageLabelMap.get(code) || code
}

export function getLocaleLabel(code: string | null | undefined) {
  return code === 'en-US' ? 'English' : '中文'
}

export function formatLanguagePair(
  sourceLanguage: string | null | undefined,
  targetLanguage: string | null | undefined,
) {
  if (!sourceLanguage || !targetLanguage) {
    return getActiveUILocale() === 'en-US' ? 'Language pair not set' : '未设置语言对'
  }
  return `${getLanguageLabel(sourceLanguage)} -> ${getLanguageLabel(targetLanguage)}`
}
