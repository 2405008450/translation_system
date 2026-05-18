import { computed, reactive } from 'vue'

export type ToastTone = 'success' | 'info' | 'warn' | 'error'

export interface ToastOptions {
  title?: string
  message: string
  tone?: ToastTone
  duration?: number
}

export interface ToastItem {
  id: number
  title: string
  message: string
  tone: ToastTone
  duration: number
}

const DEFAULT_DURATION = 3600
const timers = new Map<number, number>()

const state = reactive({
  toasts: [] as ToastItem[],
})

let nextToastId = 1

function clearToastTimer(id: number) {
  const timer = timers.get(id)
  if (typeof timer === 'number') {
    window.clearTimeout(timer)
    timers.delete(id)
  }
}

export function removeToast(id: number) {
  clearToastTimer(id)
  state.toasts = state.toasts.filter((toast) => toast.id !== id)
}

export function pushToast(input: string | ToastOptions, defaultTone: ToastTone = 'info') {
  const options = typeof input === 'string' ? { message: input } : input
  const toast: ToastItem = {
    id: nextToastId++,
    title: options.title || '',
    message: options.message,
    tone: options.tone || defaultTone,
    duration: options.duration ?? DEFAULT_DURATION,
  }

  state.toasts = [...state.toasts, toast]

  if (toast.duration > 0) {
    const timer = window.setTimeout(() => {
      removeToast(toast.id)
    }, toast.duration)
    timers.set(toast.id, timer)
  }

  return toast.id
}

export function useToast() {
  return {
    toasts: computed(() => state.toasts),
    show: (input: string | ToastOptions) => pushToast(input, 'info'),
    success: (input: string | ToastOptions) => pushToast(input, 'success'),
    info: (input: string | ToastOptions) => pushToast(input, 'info'),
    warn: (input: string | ToastOptions) => pushToast(input, 'warn'),
    error: (input: string | ToastOptions) => pushToast(input, 'error'),
    remove: removeToast,
  }
}
