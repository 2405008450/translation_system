import { onBeforeUnmount, onMounted } from 'vue'

interface WorkbenchShortcutHandlers {
  save: () => void
  runAI: () => void
  focusPrev: () => void
  focusNext: () => void
  focusPrevSearchResult: () => void
  focusNextSearchResult: () => void
  confirmSegment: () => void
  undo: () => void
  redo: () => void
  closePanel: () => void
  toggleHelp: () => void
  toggleSearch: () => void
  openGuidelines: () => void
  openResourceSearch: () => void
  openAddTerm: () => void
  openComment: () => void
  copySourceToTarget: () => void
  clearTarget: () => void
  mergeSegment: () => void
  splitSegment: () => void
  acceptRevision: () => void
  rejectRevision: () => void
  cancelSegmentConfirmation: () => void
  autoTagging: () => void
  webSearch: () => void
  addToDictionary: () => void
  applyMatchResult: (index: number) => void
  toggleSourceTarget: () => void
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

function isTargetEditorTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  return Boolean(target.closest('[data-segment-target="true"]'))
}

function isSegmentEditorTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) {
    return false
  }

  return Boolean(target.closest('[data-segment-target="true"], .segment-row__source-editor'))
}

export function useWorkbenchShortcuts(handlers: WorkbenchShortcutHandlers) {
  function onKeydown(event: KeyboardEvent) {
    const ctrlOrMeta = event.ctrlKey || event.metaKey
    const key = event.key.toLowerCase()
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

    if (
      event.key === 'Tab'
      && !ctrlOrMeta
      && !event.altKey
      && !event.shiftKey
      && isSegmentEditorTarget(event.target)
    ) {
      event.preventDefault()
      handlers.toggleSourceTarget()
      return
    }

    if (ctrlOrMeta && !event.altKey && !event.shiftKey && /^[1-5]$/.test(event.key)) {
      event.preventDefault()
      handlers.applyMatchResult(Number(event.key) - 1)
      return
    }

    if (ctrlOrMeta && !event.altKey && !event.shiftKey && event.key === 'ArrowUp') {
      event.preventDefault()
      handlers.focusPrev()
      return
    }

    if (ctrlOrMeta && !event.altKey && !event.shiftKey && event.key === 'ArrowDown') {
      event.preventDefault()
      handlers.focusNext()
      return
    }

    if (ctrlOrMeta && key === 's') {
      event.preventDefault()
      handlers.save()
      return
    }

    if (ctrlOrMeta && event.shiftKey && key === 't') {
      event.preventDefault()
      handlers.runAI()
      return
    }

    if (event.altKey && !ctrlOrMeta) {
      if (event.key === 'ArrowUp' && !event.shiftKey) {
        event.preventDefault()
        handlers.focusPrevSearchResult()
        return
      }

      if (event.key === 'ArrowDown' && !event.shiftKey) {
        event.preventDefault()
        handlers.focusNextSearchResult()
        return
      }

      if (event.key === 'Delete' && !event.shiftKey) {
        event.preventDefault()
        handlers.clearTarget()
        return
      }

      if (event.shiftKey) {
        if (key === 't') {
          event.preventDefault()
          handlers.autoTagging()
          return
        }
        if (key === 's') {
          event.preventDefault()
          handlers.webSearch()
          return
        }
        if (key === 'z') {
          event.preventDefault()
          handlers.cancelSegmentConfirmation()
          return
        }
        if (key === 'd') {
          event.preventDefault()
          handlers.addToDictionary()
          return
        }
      } else {
        if (key === 'f') {
          event.preventDefault()
          handlers.toggleSearch()
          return
        }
        if (key === 'p') {
          event.preventDefault()
          handlers.runAI()
          return
        }
        if (key === 'g') {
          event.preventDefault()
          handlers.openGuidelines()
          return
        }
        if (key === 'e') {
          event.preventDefault()
          handlers.acceptRevision()
          return
        }
        if (key === 'r') {
          event.preventDefault()
          handlers.rejectRevision()
          return
        }
        if (key === 'k') {
          event.preventDefault()
          handlers.openResourceSearch()
          return
        }
        if (key === 't') {
          event.preventDefault()
          handlers.openAddTerm()
          return
        }
        if (key === 'n') {
          event.preventDefault()
          handlers.openComment()
          return
        }
        if (key === 'c') {
          event.preventDefault()
          handlers.copySourceToTarget()
          return
        }
        if (key === 'm') {
          event.preventDefault()
          handlers.mergeSegment()
          return
        }
        if (key === 's') {
          event.preventDefault()
          handlers.splitSegment()
          return
        }
      }
    }

    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !event.repeat) {
      if (ctrlOrMeta) {
        return
      }

      const targetEditor = isTargetEditorTarget(event.target)
      const otherEditable = isEditableTarget(event.target) && !targetEditor
      if (otherEditable) {
        return
      }

      event.preventDefault()
      handlers.confirmSegment()
      return
    }

    if (ctrlOrMeta && !editableTarget && !event.altKey) {
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
  }

  onMounted(() => {
    window.addEventListener('keydown', onKeydown)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('keydown', onKeydown)
  })
}
