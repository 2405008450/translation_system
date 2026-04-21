export interface LanguageOption {
  code: string
  label: string
}

export const DEFAULT_LOCALE = 'zh-CN'

export const languageOptions: LanguageOption[] = [
  { code: 'zh-CN', label: '中文（简体）' },
  { code: 'zh-TW', label: '中文（繁体）' },
  { code: 'zh-HK', label: '中文（香港）' },
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
