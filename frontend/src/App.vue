<script setup lang="ts">
import { Loader2 } from 'lucide-vue-next'

import ConfirmDialog from './components/base/ConfirmDialog.vue'
import Toast from './components/base/Toast.vue'
import { routeLoading } from './router'
</script>

<template>
  <RouterView />
  <Transition name="route-loading-fade">
    <div v-if="routeLoading" class="route-loading-overlay" role="status" aria-live="polite">
      <div class="route-loading-card">
        <Loader2 class="lucide-spin" :size="24" />
        <span>正在加载...</span>
      </div>
    </div>
  </Transition>
  <Toast />
  <ConfirmDialog />
</template>
