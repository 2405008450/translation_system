import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { DEFAULT_LOCALE, isSupportedUILocale } from '../constants/languages'
import { i18n, type AppLocale } from '../i18n'

export type ThemeMode = 'light' | 'dark'
export type WorkbenchConfirmJumpMode = 'next_segment' | 'next_unconfirmed'

const LOCALE_STORAGE_KEY = 'tm-workbench-locale'
const THEME_STORAGE_KEY = 'tm-workbench-theme'
const WORKBENCH_AUTO_FILL_EXACT_STORAGE_KEY = 'tm-workbench-auto-fill-exact'
const WORKBENCH_AUTO_FILL_FUZZY_STORAGE_KEY = 'tm-workbench-auto-fill-fuzzy'
const WORKBENCH_CONFIRM_JUMP_STORAGE_KEY = 'tm-workbench-confirm-jump'

function normalizeLocale(value: string | null | undefined): AppLocale {
  return isSupportedUILocale(value) ? value : DEFAULT_LOCALE
}

function getStoredBoolean(key: string, fallback: boolean) {
  const value = window.localStorage.getItem(key)
  if (value === '1') {
    return true
  }
  if (value === '0') {
    return false
  }
  return fallback
}

function normalizeConfirmJumpMode(value: string | null | undefined): WorkbenchConfirmJumpMode {
  return value === 'next_unconfirmed' ? 'next_unconfirmed' : 'next_segment'
}

export const usePreferencesStore = defineStore('preferences', () => {
  const locale = ref<AppLocale>(DEFAULT_LOCALE)
  const theme = ref<ThemeMode>('light')
  const autoFillExactMatches = ref(true)
  const autoFillFuzzyMatches = ref(true)
  const confirmJumpMode = ref<WorkbenchConfirmJumpMode>('next_segment')

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
    const savedConfirmJumpMode = window.localStorage.getItem(WORKBENCH_CONFIRM_JUMP_STORAGE_KEY)

    locale.value = normalizeLocale(savedLocale)
    theme.value = savedTheme === 'dark' ? 'dark' : 'light'
    autoFillExactMatches.value = getStoredBoolean(WORKBENCH_AUTO_FILL_EXACT_STORAGE_KEY, true)
    autoFillFuzzyMatches.value = getStoredBoolean(WORKBENCH_AUTO_FILL_FUZZY_STORAGE_KEY, true)
    confirmJumpMode.value = normalizeConfirmJumpMode(savedConfirmJumpMode)

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

  function setAutoFillExactMatches(enabled: boolean) {
    autoFillExactMatches.value = enabled
    window.localStorage.setItem(WORKBENCH_AUTO_FILL_EXACT_STORAGE_KEY, enabled ? '1' : '0')
  }

  function setAutoFillFuzzyMatches(enabled: boolean) {
    autoFillFuzzyMatches.value = enabled
    window.localStorage.setItem(WORKBENCH_AUTO_FILL_FUZZY_STORAGE_KEY, enabled ? '1' : '0')
  }

  function setConfirmJumpMode(mode: WorkbenchConfirmJumpMode) {
    confirmJumpMode.value = normalizeConfirmJumpMode(mode)
    window.localStorage.setItem(WORKBENCH_CONFIRM_JUMP_STORAGE_KEY, confirmJumpMode.value)
  }

  return {
    locale,
    theme,
    autoFillExactMatches,
    autoFillFuzzyMatches,
    confirmJumpMode,
    isDark,
    bootstrap,
    setLocale,
    setTheme,
    setAutoFillExactMatches,
    setAutoFillFuzzyMatches,
    setConfirmJumpMode,
  }
})
