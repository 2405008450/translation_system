import { onBeforeUnmount, watchEffect } from 'vue'

import type { PageContext } from '../stores/shell'
import { useShellStore } from '../stores/shell'

export function usePageHeader(factory: () => Partial<PageContext> | null | undefined) {
  const shellStore = useShellStore()

  watchEffect(() => {
    shellStore.setPageContext(factory() || {})
  })

  onBeforeUnmount(() => {
    shellStore.clearPageContext()
  })
}
