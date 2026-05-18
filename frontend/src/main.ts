import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import { i18n } from './i18n'
import router from './router'
import { setUnauthorizedHandler } from './api/http'
import { useAuthStore } from './stores/auth'
import { usePreferencesStore } from './stores/preferences'
import './styles.css'

async function bootstrap() {
  const app = createApp(App)
  const pinia = createPinia()

  app.use(pinia)
  const preferencesStore = usePreferencesStore()
  preferencesStore.bootstrap()
  const authStore = useAuthStore()

  setUnauthorizedHandler(async () => {
    authStore.logout()
    if (router.currentRoute.value.name !== 'login') {
      await router.push({ name: 'login' })
    }
  })

  try {
    await authStore.bootstrap()
  } catch {
    // Keep mounting the app so the login view can surface backend/init errors.
  }

  app.use(i18n)
  app.use(router)
  await router.isReady()
  app.mount('#app')
}

void bootstrap()
