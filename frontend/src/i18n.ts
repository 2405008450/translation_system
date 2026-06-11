import { createI18n } from 'vue-i18n'

import { DEFAULT_LOCALE } from './constants/languages'
import enUS from './locales/en-US'
import zhCN from './locales/zh-CN'

export const messages = {
  'zh-CN': zhCN,
  'en-US': enUS,
} as const

export type AppLocale = keyof typeof messages

export const i18n = createI18n({
  legacy: false,
  locale: DEFAULT_LOCALE,
  fallbackLocale: DEFAULT_LOCALE,
  messages,
})

export function translate(key: string, values?: Record<string, unknown>) {
  return String(i18n.global.t(key, values || {}))
}
