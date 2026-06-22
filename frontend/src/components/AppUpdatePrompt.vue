<script setup lang="ts">
import { RefreshCw } from 'lucide-vue-next'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

interface AppVersionResponse {
  version?: string
}

const CHECK_INTERVAL_MS = 4 * 60 * 1000
const currentVersion = normalizeVersion(__APP_VERSION__)
const shouldCheckForUpdates = currentVersion !== '' && currentVersion !== 'dev'

const { t } = useI18n()
const hasUpdate = ref(false)
const latestVersion = ref('')

let checkTimer: number | undefined
let checking = false

function normalizeVersion(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

async function checkForUpdate() {
  if (checking || hasUpdate.value || !shouldCheckForUpdates) {
    return
  }

  checking = true
  try {
    const response = await fetch(`/api/app-version?_=${Date.now()}`, {
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        'Cache-Control': 'no-cache',
      },
    })
    if (!response.ok) {
      return
    }

    const data = await response.json() as AppVersionResponse
    const serverVersion = normalizeVersion(data.version)
    if (serverVersion && serverVersion !== currentVersion) {
      latestVersion.value = serverVersion
      hasUpdate.value = true
    }
  } catch {
    // Version checks should stay quiet; normal network errors are handled elsewhere.
  } finally {
    checking = false
  }
}

function reloadPage() {
  window.location.reload()
}

function handleFocus() {
  void checkForUpdate()
}

function handleVisibilityChange() {
  if (document.visibilityState === 'visible') {
    void checkForUpdate()
  }
}

onMounted(() => {
  if (!shouldCheckForUpdates) {
    return
  }

  void checkForUpdate()
  checkTimer = window.setInterval(() => {
    void checkForUpdate()
  }, CHECK_INTERVAL_MS)
  window.addEventListener('focus', handleFocus)
  document.addEventListener('visibilitychange', handleVisibilityChange)
})

onBeforeUnmount(() => {
  if (checkTimer !== undefined) {
    window.clearInterval(checkTimer)
  }
  window.removeEventListener('focus', handleFocus)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="app-update-slide">
      <section
        v-if="hasUpdate"
        class="app-update-prompt"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        <div class="app-update-prompt__icon" aria-hidden="true">
          <RefreshCw :size="18" />
        </div>
        <div class="app-update-prompt__content">
          <strong>{{ t('appUpdate.title') }}</strong>
          <p>{{ t('appUpdate.message') }}</p>
          <p class="app-update-prompt__hint">{{ t('appUpdate.hardRefreshHint') }}</p>
          <span class="sr-only">
            {{ t('appUpdate.versionChanged', { current: currentVersion, latest: latestVersion }) }}
          </span>
        </div>
        <button class="button button--primary app-update-prompt__button" type="button" @click="reloadPage">
          <RefreshCw :size="14" />
          {{ t('appUpdate.reload') }}
        </button>
      </section>
    </Transition>
  </Teleport>
</template>
