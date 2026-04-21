<script setup lang="ts">
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Info,
  X,
} from 'lucide-vue-next'
import { computed } from 'vue'

import { useToast } from '../../composables/useToast'

const toast = useToast()

const iconMap = {
  success: CheckCircle2,
  info: Info,
  warn: AlertTriangle,
  error: AlertCircle,
} as const

const toasts = computed(() => toast.toasts.value)
</script>

<template>
  <Teleport to="body">
    <div class="toast-stack" aria-live="polite" aria-atomic="true">
      <TransitionGroup name="toast-slide">
        <section
          v-for="item in toasts"
          :key="item.id"
          class="toast"
          :class="`toast--${item.tone}`"
        >
          <div class="toast__icon">
            <component :is="iconMap[item.tone]" :size="18" />
          </div>
          <div class="toast__content">
            <strong v-if="item.title" class="toast__title">{{ item.title }}</strong>
            <p class="toast__message">{{ item.message }}</p>
          </div>
          <button
            class="toast__close"
            type="button"
            aria-label="关闭通知"
            title="关闭"
            @click="toast.remove(item.id)"
          >
            <X :size="14" />
          </button>
        </section>
      </TransitionGroup>
    </div>
  </Teleport>
</template>
