import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { DEFAULT_LOCALE, isSupportedUILocale } from '../constants/languages'
import { i18n, type AppLocale } from '../i18n'

export type ThemeMode = 'light' | 'dark'

const LOCALE_STORAGE_KEY = 'tm-workbench-locale'
const THEME_STORAGE_KEY = 'tm-workbench-theme'

function normalizeLocale(value: string | null | undefined): AppLocale {
  return isSupportedUILocale(value) ? value : DEFAULT_LOCALE
}

export const usePreferencesStore = defineStore('preferences', () => {
  const locale = ref<AppLocale>(DEFAULT_LOCALE)
  const theme = ref<ThemeMode>('light')

  const isDark = computed(() => theme.value === 'dark')

  function applyTheme() {
    document.documentElement.dataset.theme = theme.value
  }

  function applyLocale() {
    i18n.global.locale.value = locale.value
    document.documentElement.lang = locale.value
  }

  function bootstrap() {
    const savedLocale = window.localStorage.getItem(LOCALE_STORAGE_KEY)
    const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY)

    locale.value = normalizeLocale(savedLocale)
    theme.value = savedTheme === 'dark' ? 'dark' : 'light'

    applyLocale()
    applyTheme()
  }

  function setLocale(nextLocale: string) {
    locale.value = normalizeLocale(nextLocale)
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale.value)
    applyLocale()
  }

  function setTheme(nextTheme: ThemeMode) {
    theme.value = nextTheme
    window.localStorage.setItem(THEME_STORAGE_KEY, theme.value)
    applyTheme()
  }

  return {
    locale,
    theme,
    isDark,
    bootstrap,
    setLocale,
    setTheme,
  }
})
