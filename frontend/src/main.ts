import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from './api/http'
import { useAuthStore } from './stores/auth'
import './styles.css'

async function bootstrap() {
  const app = createApp(App)
  const pinia = createPinia()

  app.use(pinia)
  const authStore = useAuthStore()

  setUnauthorizedHandler(async () => {
    authStore.logout()
    if (router.currentRoute.value.name !== 'login') {
      await router.push({ name: 'login' })
    }
  })

  await authStore.bootstrap()

  app.use(router)
  await router.isReady()
  app.mount('#app')
}

void bootstrap()
