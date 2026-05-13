<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  open: boolean
  title?: string
  description?: string
  width?: string
  closeOnOverlay?: boolean
  closeOnEsc?: boolean
}>(), {
  title: '',
  description: '',
  width: 'min(720px, calc(100vw - 32px))',
  closeOnOverlay: true,
  closeOnEsc: true,
})

const emit = defineEmits<{
  close: []
}>()

const panelRef = ref<HTMLElement | null>(null)
let previousActiveElement: HTMLElement | null = null

function getFocusableElements() {
  const panel = panelRef.value
  if (!panel) {
    return []
  }

  return Array.from(
    panel.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => !element.hasAttribute('aria-hidden'))
}

function focusFirstElement() {
  const focusableElements = getFocusableElements()
  if (focusableElements.length > 0) {
    focusableElements[0].focus({ preventScroll: true })
    return
  }

  panelRef.value?.focus({ preventScroll: true })
}

function handleClose() {
  emit('close')
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.open) {
    return
  }

  if (event.key === 'Escape' && props.closeOnEsc) {
    event.preventDefault()
    handleClose()
    return
  }

  if (event.key !== 'Tab') {
    return
  }

  const focusableElements = getFocusableElements()
  if (focusableElements.length === 0) {
    event.preventDefault()
    return
  }

  const firstElement = focusableElements[0]
  const lastElement = focusableElements[focusableElements.length - 1]
  const activeElement = document.activeElement as HTMLElement | null

  if (event.shiftKey && activeElement === firstElement) {
    event.preventDefault()
    lastElement.focus({ preventScroll: true })
    return
  }

  if (!event.shiftKey && activeElement === lastElement) {
    event.preventDefault()
    firstElement.focus({ preventScroll: true })
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      previousActiveElement = document.activeElement as HTMLElement | null
      document.addEventListener('keydown', handleKeydown)
      document.body.classList.add('modal-open')
      await nextTick()
      focusFirstElement()
      return
    }

    document.removeEventListener('keydown', handleKeydown)
    document.body.classList.remove('modal-open')
    previousActiveElement?.focus({ preventScroll: true })
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
  document.body.classList.remove('modal-open')
})
</script>

<template>
  <Teleport to="body">
    <Transition name="modal-pop">
      <div
        v-if="open"
        class="modal-overlay"
        role="presentation"
        @click.self="closeOnOverlay && handleClose()"
      >
        <section
          ref="panelRef"
          class="modal-dialog"
          :style="{ width }"
          tabindex="-1"
          role="dialog"
          aria-modal="true"
          :aria-label="title || undefined"
        >
          <header v-if="$slots.header || title || description" class="modal-header">
            <slot name="header">
              <div class="modal-header__copy">
                <h3 v-if="title" class="modal-title">{{ title }}</h3>
                <p v-if="description" class="modal-description">{{ description }}</p>
              </div>
            </slot>
            <button
              class="modal-close"
              type="button"
              aria-label="关闭弹窗"
              title="关闭"
              @click="handleClose"
            >
              ×
            </button>
          </header>

          <div class="modal-body">
            <slot />
          </div>

          <footer v-if="$slots.footer" class="modal-footer">
            <slot name="footer" />
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
