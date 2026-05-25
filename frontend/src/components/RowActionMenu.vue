<script setup lang="ts">
import { MoreHorizontal } from 'lucide-vue-next'
import { nextTick, onBeforeUnmount, ref } from 'vue'

const props = withDefaults(defineProps<{
  title?: string
  menuLabel?: string
  minWidth?: number
}>(), {
  title: '更多操作',
  menuLabel: '更多操作',
  minWidth: 148,
})

const MENU_OPEN_EVENT = 'row-action-menu-open'

defineSlots<{
  default?: (props: { close: () => void }) => unknown
}>()

const menuId = Math.random().toString(36)
const triggerRef = ref<HTMLButtonElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
const open = ref(false)
const menuStyle = ref<Record<string, string>>({})

function updateMenuPosition() {
  const trigger = triggerRef.value
  if (!trigger) {
    return
  }

  const rect = trigger.getBoundingClientRect()
  const gap = 6
  const viewportPadding = 8
  const menuWidth = props.minWidth
  const menuHeight = menuRef.value?.offsetHeight ?? 0
  const maxLeft = window.innerWidth - menuWidth - viewportPadding
  const left = Math.min(Math.max(viewportPadding, rect.right - menuWidth), Math.max(viewportPadding, maxLeft))
  let top = rect.bottom + gap

  if (menuHeight && top + menuHeight > window.innerHeight - viewportPadding) {
    top = Math.max(viewportPadding, rect.top - menuHeight - gap)
  }

  menuStyle.value = {
    position: 'fixed',
    top: `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
    zIndex: '3000',
    minWidth: `${menuWidth}px`,
  }
}

function removeGlobalListeners() {
  document.removeEventListener('click', handleDocumentClick)
  document.removeEventListener('keydown', handleDocumentKeydown)
  window.removeEventListener(MENU_OPEN_EVENT, handlePeerMenuOpen)
  window.removeEventListener('resize', close)
  window.removeEventListener('scroll', close, true)
}

async function setOpen(nextOpen: boolean) {
  if (open.value === nextOpen) {
    return
  }

  open.value = nextOpen
  if (!nextOpen) {
    removeGlobalListeners()
    return
  }

  window.dispatchEvent(new CustomEvent(MENU_OPEN_EVENT, { detail: menuId }))
  updateMenuPosition()
  document.addEventListener('click', handleDocumentClick)
  document.addEventListener('keydown', handleDocumentKeydown)
  window.addEventListener(MENU_OPEN_EVENT, handlePeerMenuOpen)
  window.addEventListener('resize', close)
  window.addEventListener('scroll', close, true)
  await nextTick()
  updateMenuPosition()
}

function toggle() {
  void setOpen(!open.value)
}

function close() {
  void setOpen(false)
}

function handleDocumentClick(event: MouseEvent) {
  const target = event.target
  if (!(target instanceof Node)) {
    close()
    return
  }
  if (triggerRef.value?.contains(target) || menuRef.value?.contains(target)) {
    return
  }
  close()
}

function handleDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    close()
  }
}

function handlePeerMenuOpen(event: Event) {
  if ((event as CustomEvent<string>).detail !== menuId) {
    close()
  }
}

onBeforeUnmount(() => {
  removeGlobalListeners()
})
</script>

<template>
  <div class="row-action-menu" @click.stop>
    <button
      ref="triggerRef"
      class="data-table__actions-btn"
      type="button"
      :title="title"
      :aria-label="title"
      aria-haspopup="menu"
      :aria-expanded="open"
      @click.stop="toggle"
    >
      <MoreHorizontal :size="16" />
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="menuRef"
        class="row-action-menu__dropdown"
        :style="menuStyle"
        role="menu"
        :aria-label="menuLabel"
        @click.stop
      >
        <slot :close="close" />
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.row-action-menu {
  display: inline-flex;
}

.row-action-menu__dropdown {
  display: grid;
  gap: 4px;
  max-width: calc(100vw - 16px);
  padding: 8px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: var(--shadow-soft);
}

.row-action-menu__dropdown :deep(button) {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  width: 100%;
  min-height: 34px;
  padding: 6px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  font: inherit;
  line-height: 1.2;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
  box-shadow: none;
}

.row-action-menu__dropdown :deep(button:hover:not(:disabled)) {
  background: var(--surface-muted);
  color: var(--text-primary);
}

.row-action-menu__dropdown :deep(button:disabled) {
  cursor: not-allowed;
  opacity: 0.58;
}

.row-action-menu__dropdown :deep(button.is-danger) {
  color: var(--state-danger);
}

.row-action-menu__dropdown :deep(button.is-danger:hover:not(:disabled)) {
  background: var(--state-danger-bg);
}
</style>
