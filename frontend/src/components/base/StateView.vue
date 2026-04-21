<script setup lang="ts">
import { AlertCircle, FileX, Loader2, ShieldAlert } from 'lucide-vue-next'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

type StateKind = 'loading' | 'empty' | 'error' | 'forbidden'

const props = withDefaults(defineProps<{
  kind: StateKind
  title?: string
  message?: string
  actionText?: string
}>(), {
  title: '',
  message: '',
  actionText: '',
})

const emit = defineEmits<{
  action: []
}>()
const { t } = useI18n()

const iconMap = {
  loading: Loader2,
  empty: FileX,
  error: AlertCircle,
  forbidden: ShieldAlert,
} as const

const computedTitle = computed(() => {
  if (props.title) {
    return props.title
  }

  return {
    loading: t('stateView.loading'),
    empty: t('stateView.empty'),
    error: t('stateView.error'),
    forbidden: t('stateView.forbidden'),
  }[props.kind]
})
</script>

<template>
  <section class="state-view" :class="`state-view--${kind}`">
    <component :is="iconMap[kind]" class="state-view__icon" :class="{ 'lucide-spin': kind === 'loading' }" :size="28" />
    <div class="state-view__copy">
      <strong class="state-view__title">{{ computedTitle }}</strong>
      <p v-if="message" class="state-view__message">{{ message }}</p>
    </div>
    <button
      v-if="actionText"
      class="button"
      type="button"
      @click="emit('action')"
    >
      {{ actionText }}
    </button>
  </section>
</template>
