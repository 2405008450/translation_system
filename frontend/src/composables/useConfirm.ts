import { reactive } from 'vue'

import { translate } from '../i18n'

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
  confirmText: translate('common.actions.confirm'),
  cancelText: translate('common.actions.cancel'),
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
    confirmState.title = options.title || translate('confirm.title')
    confirmState.message = options.message
    confirmState.confirmText = options.confirmText || translate('common.actions.confirm')
    confirmState.cancelText = options.cancelText || translate('common.actions.cancel')
    confirmState.danger = Boolean(options.danger)

    return new Promise<boolean>((resolve) => {
      pendingResolver = resolve
    })
  }
}
