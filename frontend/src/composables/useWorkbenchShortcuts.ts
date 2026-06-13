import { onBeforeUnmount, onMounted } from 'vue'

interface WorkbenchShortcutHandlers {
  save: () => void
  runAI: () => void
  focusPrev: () => void
  focusNext: () => void
  confirmCurrent: () => void
  undo: () => void
  redo: () => void
  closePanel: () => void
  toggleHelp: () => void
}

function isEditableTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  if (target.isContentEditable) {
    return true
  }

  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

export function useWorkbenchShortcuts(handlers: WorkbenchShortcutHandlers) {
  function onKeydown(event: KeyboardEvent) {
    const ctrlOrMeta = event.ctrlKey || event.metaKey
    const editableTarget = isEditableTarget(event.target)

    if (event.key === '?' && !ctrlOrMeta && !editableTarget) {
      event.preventDefault()
      handlers.toggleHelp()
      return
    }

    if (event.key === 'Escape') {
      handlers.closePanel()
      return
    }

    if (ctrlOrMeta && event.key.toLowerCase() === 's') {
      event.preventDefault()
      handlers.save()
      return
    }

    if (ctrlOrMeta && event.shiftKey && event.key.toLowerCase() === 't') {
      event.preventDefault()
      handlers.runAI()
      return
    }

    if (ctrlOrMeta && event.key === 'Enter') {
      event.preventDefault()
      handlers.confirmCurrent()
      return
    }

    if (ctrlOrMeta && !editableTarget && !event.altKey) {
      const key = event.key.toLowerCase()
      if (key === 'z') {
        event.preventDefault()
        if (event.shiftKey) {
          handlers.redo()
        } else {
          handlers.undo()
        }
        return
      }
      if (key === 'y') {
        event.preventDefault()
        handlers.redo()
        return
      }
    }

    if (editableTarget && !event.altKey) {
      return
    }

    if (event.altKey && event.key === 'ArrowUp') {
      event.preventDefault()
      handlers.focusPrev()
      return
    }

    if (event.altKey && event.key === 'ArrowDown') {
      event.preventDefault()
      handlers.focusNext()
    }
  }

  onMounted(() => {
    window.addEventListener('keydown', onKeydown)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('keydown', onKeydown)
  })
}
