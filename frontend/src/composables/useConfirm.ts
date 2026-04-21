import { reactive } from 'vue'

export interface ConfirmOptions {
  title?: string
  message: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}

type PendingResolver = ((value: boolean) => void) | null

export const confirmState = reactive({
  open: false,
  title: '',
  message: '',
  confirmText: '确认',
  cancelText: '取消',
  danger: false,
})

let pendingResolver: PendingResolver = null

function settle(value: boolean) {
  const resolver = pendingResolver
  pendingResolver = null
  confirmState.open = false
  if (resolver) {
    resolver(value)
  }
}

export function resolveConfirm() {
  settle(true)
}

export function rejectConfirm() {
  settle(false)
}

export function useConfirm() {
  return (options: ConfirmOptions) => {
    if (pendingResolver) {
      settle(false)
    }

    confirmState.open = true
    confirmState.title = options.title || '请确认'
    confirmState.message = options.message
    confirmState.confirmText = options.confirmText || '确认'
    confirmState.cancelText = options.cancelText || '取消'
    confirmState.danger = Boolean(options.danger)

    return new Promise<boolean>((resolve) => {
      pendingResolver = resolve
    })
  }
}
