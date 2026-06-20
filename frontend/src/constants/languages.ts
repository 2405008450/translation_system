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
  'pt-PT': '葡萄牙语（葡萄牙）',
  'it-IT': '意大利语',
  'ru-RU': '俄语',
  'pl-PL': '波兰语',
  'nl-NL': '荷兰语',
  'sv-SE': '瑞典语',
  'da-DK': '丹麦语',
  'fi-FI': '芬兰语',
  'no-NO': '挪威语',
  'tr-TR': '土耳其语',
  'uk-UA': '乌克兰语',
  'cs-CZ': '捷克语',
  'sk-SK': '斯洛伐克语',
  'ro-RO': '罗马尼亚语',
  'hu-HU': '匈牙利语',
  'el-GR': '希腊语',
  'bg-BG': '保加利亚语',
  'hr-HR': '克罗地亚语',
  'sr-RS': '塞尔维亚语',
  'sl-SI': '斯洛文尼亚语',
  'lt-LT': '立陶宛语',
  'lv-LV': '拉脱维亚语',
  'et-EE': '爱沙尼亚语',
  'ar-SA': '阿拉伯语',
  'he-IL': '希伯来语',
  'fa-IR': '波斯语',
  'ur-PK': '乌尔都语',
  'hi-IN': '印地语',
  'bn-BD': '孟加拉语',
  'id-ID': '印尼语',
  'ms-MY': '马来语',
  'th-TH': '泰语',
  'vi-VN': '越南语',
  'fil-PH': '菲律宾语',
  'my-MM': '缅甸语',
  'km-KH': '高棉语',
  'lo-LA': '老挝语',
  'sw-KE': '斯瓦希里语',
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
  ['pt-PT', 'Portuguese (Portugal)'],
  ['it-IT', 'Italian'],
  ['ru-RU', 'Russian'],
  ['pl-PL', 'Polish'],
  ['nl-NL', 'Dutch'],
  ['sv-SE', 'Swedish'],
  ['da-DK', 'Danish'],
  ['fi-FI', 'Finnish'],
  ['no-NO', 'Norwegian'],
  ['tr-TR', 'Turkish'],
  ['uk-UA', 'Ukrainian'],
  ['cs-CZ', 'Czech'],
  ['sk-SK', 'Slovak'],
  ['ro-RO', 'Romanian'],
  ['hu-HU', 'Hungarian'],
  ['el-GR', 'Greek'],
  ['bg-BG', 'Bulgarian'],
  ['hr-HR', 'Croatian'],
  ['sr-RS', 'Serbian'],
  ['sl-SI', 'Slovenian'],
  ['lt-LT', 'Lithuanian'],
  ['lv-LV', 'Latvian'],
  ['et-EE', 'Estonian'],
  ['ar-SA', 'Arabic'],
  ['he-IL', 'Hebrew'],
  ['fa-IR', 'Persian'],
  ['ur-PK', 'Urdu'],
  ['hi-IN', 'Hindi'],
  ['bn-BD', 'Bengali'],
  ['id-ID', 'Indonesian'],
  ['ms-MY', 'Malay'],
  ['th-TH', 'Thai'],
  ['vi-VN', 'Vietnamese'],
  ['fil-PH', 'Filipino'],
  ['my-MM', 'Burmese'],
  ['km-KH', 'Khmer'],
  ['lo-LA', 'Lao'],
  ['sw-KE', 'Swahili'],
])

// 与后端 app/services/language_pairs.py 的 LANGUAGE_ALIASES 保持一致。
const LANGUAGE_ALIASES: Record<string, string> = {
  zh: 'zh-CN',
  'zh-cn': 'zh-CN',
  'zh-hans': 'zh-CN',
  'zh-tw': 'zh-TW',
  'zh-hant': 'zh-TW',
  'zh-hk': 'zh-HK',
  'zh-hant-hk': 'zh-HK',
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
  'pt-pt': 'pt-PT',
  it: 'it-IT',
  'it-it': 'it-IT',
  ru: 'ru-RU',
  'ru-ru': 'ru-RU',
  pl: 'pl-PL',
  'pl-pl': 'pl-PL',
  nl: 'nl-NL',
  'nl-nl': 'nl-NL',
  sv: 'sv-SE',
  'sv-se': 'sv-SE',
  da: 'da-DK',
  'da-dk': 'da-DK',
  fi: 'fi-FI',
  'fi-fi': 'fi-FI',
  no: 'no-NO',
  'no-no': 'no-NO',
  nb: 'no-NO',
  'nb-no': 'no-NO',
  tr: 'tr-TR',
  'tr-tr': 'tr-TR',
  uk: 'uk-UA',
  'uk-ua': 'uk-UA',
  cs: 'cs-CZ',
  'cs-cz': 'cs-CZ',
  sk: 'sk-SK',
  'sk-sk': 'sk-SK',
  ro: 'ro-RO',
  'ro-ro': 'ro-RO',
  hu: 'hu-HU',
  'hu-hu': 'hu-HU',
  el: 'el-GR',
  'el-gr': 'el-GR',
  bg: 'bg-BG',
  'bg-bg': 'bg-BG',
  hr: 'hr-HR',
  'hr-hr': 'hr-HR',
  sr: 'sr-RS',
  'sr-rs': 'sr-RS',
  sl: 'sl-SI',
  'sl-si': 'sl-SI',
  lt: 'lt-LT',
  'lt-lt': 'lt-LT',
  lv: 'lv-LV',
  'lv-lv': 'lv-LV',
  et: 'et-EE',
  'et-ee': 'et-EE',
  ar: 'ar-SA',
  'ar-sa': 'ar-SA',
  he: 'he-IL',
  iw: 'he-IL',
  'he-il': 'he-IL',
  fa: 'fa-IR',
  'fa-ir': 'fa-IR',
  ur: 'ur-PK',
  'ur-pk': 'ur-PK',
  hi: 'hi-IN',
  'hi-in': 'hi-IN',
  bn: 'bn-BD',
  'bn-bd': 'bn-BD',
  id: 'id-ID',
  in: 'id-ID',
  'id-id': 'id-ID',
  ms: 'ms-MY',
  'ms-my': 'ms-MY',
  th: 'th-TH',
  'th-th': 'th-TH',
  vi: 'vi-VN',
  'vi-vn': 'vi-VN',
  fil: 'fil-PH',
  tl: 'fil-PH',
  'fil-ph': 'fil-PH',
  my: 'my-MM',
  'my-mm': 'my-MM',
  km: 'km-KH',
  'km-kh': 'km-KH',
  lo: 'lo-LA',
  'lo-la': 'lo-LA',
  sw: 'sw-KE',
  'sw-ke': 'sw-KE',
}

/**
 * 将输入规范为受支持的 canonical 语言码；无法识别时返回 null。
 * 合并校验等场景应与后端 require_language_pair 的规范化结果一致。
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
