import { createI18n } from 'vue-i18n'
import en from './locales/en'
import zhTW from './locales/zh-TW'

const STORAGE_KEY = 'llmops-locale'

const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: localStorage.getItem(STORAGE_KEY) || 'en',
  fallbackLocale: 'en',
  messages: { en, 'zh-TW': zhTW },
})

export function setLocale(locale: 'en' | 'zh-TW') {
  ;(i18n.global.locale as unknown as { value: string }).value = locale
  localStorage.setItem(STORAGE_KEY, locale)
}

export function currentLocale(): string {
  return (i18n.global.locale as unknown as { value: string }).value
}

export default i18n
