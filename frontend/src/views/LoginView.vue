<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const username = ref('')
const password = ref('')
const errorMessage = ref('')

const title = computed(() => (authStore.initialized ? t('auth.login') : t('auth.initialize')))
const subtitle = computed(() =>
  authStore.initialized
    ? t('auth.subtitleLogin')
    : t('auth.subtitleInit'),
)
const submitDisabled = computed(() => authStore.loading || !authStore.tableExists)
const externalTranslatorBlockedRedirects = new Set(['/dashboard', '/projects'])

function getPostLoginRedirect() {
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
  if (authStore.isExternalTranslator && (redirect === '/' || externalTranslatorBlockedRedirects.has(redirect))) {
    return '/tasks'
  }
  return redirect
}

async function submit() {
  errorMessage.value = ''
  try {
    if (authStore.initialized) {
      await authStore.login(username.value, password.value)
    } else {
      await authStore.initializeAdmin(username.value, password.value)
    }

    await router.replace(getPostLoginRedirect())
  } catch (error) {
    if (axios.isAxiosError(error)) {
      errorMessage.value = String(error.response?.data?.detail || t('auth.requestError'))
      return
    }
    errorMessage.value = error instanceof Error ? error.message : t('auth.requestError')
  }
}

onMounted(async () => {
  try {
    await authStore.checkInitStatus()
  } catch {
    errorMessage.value = t('auth.statusError')
  }
})
</script>

<template>
  <main class="auth-layout">
    <section class="auth-panel">
      <div class="auth-panel__header">
        <div class="section-kicker">{{ t('login.kicker') }}</div>
        <h1>{{ title }}</h1>
        <p>{{ subtitle }}</p>
      </div>

      <form class="auth-form" data-testid="auth-form" @submit.prevent="submit">
        <label class="field">
          <span class="field__label">{{ t('auth.username') }}</span>
          <input
            v-model.trim="username"
            class="field__control"
            data-testid="auth-username"
            type="text"
            minlength="3"
            maxlength="50"
            autocomplete="username"
            :aria-label="t('auth.username')"
            required
          />
        </label>

        <label class="field">
          <span class="field__label">{{ t('auth.password') }}</span>
          <input
            v-model="password"
            class="field__control"
            data-testid="auth-password"
            type="password"
            minlength="6"
            maxlength="128"
            autocomplete="current-password"
            :aria-label="t('auth.password')"
            required
          />
        </label>

        <p v-if="errorMessage" class="form-message is-error">{{ errorMessage }}</p>
        <p v-if="authStore.initMessage" class="form-message is-error">{{ authStore.initMessage }}</p>

        <button class="button button--primary" data-testid="auth-submit" type="submit" :disabled="submitDisabled">
          {{ authStore.loading ? t('auth.submitLoading') : title }}
        </button>
      </form>
    </section>
  </main>
</template>
