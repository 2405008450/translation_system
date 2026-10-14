import { ref } from 'vue'
import type { AlignmentPair, AlignmentPairReplacement } from '../api/documentAlignment'

export interface AlignmentCommand {
  startOrder: number
  before: AlignmentPairReplacement[]
  after: AlignmentPairReplacement[]
  label: string
}

export interface AlignmentFocusTarget {
  pairId?: string
  order: number
}

const clone = (items: AlignmentPairReplacement[]) => items.map(item => ({
  ...item, src_indices: [...item.src_indices], tgt_indices: [...item.tgt_indices],
}))

/** 结构提交与刷新分开：已提交的操作只重试读取，绝不重复写入。 */
export function useAlignmentStructureOperations(deps: {
  flush: () => Promise<boolean>
  expectedIds: (order: number, before: AlignmentPairReplacement[]) => Promise<string[]>
  replace: (order: number, before: AlignmentPairReplacement[], after: AlignmentPairReplacement[], ids: string[]) => Promise<AlignmentPair[]>
  refresh: (target: AlignmentFocusTarget) => Promise<void>
  success: (message: string) => void
  error: (error: unknown, fallback: string) => void
}) {
  const busy = ref(false)
  const pendingRefresh = ref<AlignmentFocusTarget | null>(null)
  const undoStack = ref<AlignmentCommand[]>([])
  const redoStack = ref<AlignmentCommand[]>([])

  async function refreshCommitted(target: AlignmentFocusTarget) {
    pendingRefresh.value = target
    await deps.refresh(target)
    pendingRefresh.value = null
  }

  async function run(order: number, before: AlignmentPairReplacement[], after: AlignmentPairReplacement[], label: string, history: 'record' | 'undo' | 'redo' = 'record') {
    if (busy.value || pendingRefresh.value) return false
    busy.value = true
    let committed = false
    try {
      if (!await deps.flush()) throw new Error('仍有对齐文本未保存，请检查后重试。')
      const ids = await deps.expectedIds(order, before)
      const result = await deps.replace(order, before, after, ids)
      committed = true
      const snapshot = result.map(pair => ({
        src_indices: [...pair.src_indices], tgt_indices: [...pair.tgt_indices], locked: pair.locked,
        source_text: pair.source_text, target_text: pair.target_text,
      }))
      // 服务端已提交时必须记录历史，即使后续读取失败也能准确撤回。
      if (history === 'record') {
        undoStack.value.push({ startOrder: order, before: clone(before), after: snapshot, label })
        if (undoStack.value.length > 50) undoStack.value.shift()
        redoStack.value = []
      } else if (history === 'undo') {
        const command = undoStack.value.pop()!
        redoStack.value.push(command)
      } else {
        const command = redoStack.value.pop()!
        undoStack.value.push(command)
      }
      await refreshCommitted({ pairId: result[0]?.id, order })
      deps.success(`${label}完成，已自动保存。`)
      return true
    } catch (error) {
      deps.error(error, committed ? '操作已保存，但列表刷新失败，请重试刷新。' : `${label}失败。`)
      return committed
    } finally {
      busy.value = false
    }
  }

  async function retryRefresh() {
    if (busy.value || !pendingRefresh.value) return
    busy.value = true
    try {
      await refreshCommitted(pendingRefresh.value)
    } catch (error) {
      deps.error(error, '列表刷新失败，请重试。')
    } finally {
      busy.value = false
    }
  }

  async function undo() {
    const command = undoStack.value[undoStack.value.length - 1]
    if (command) await run(command.startOrder, command.after, command.before, `撤回：${command.label}`, 'undo')
  }

  async function redo() {
    const command = redoStack.value[redoStack.value.length - 1]
    if (command) await run(command.startOrder, command.before, command.after, `重做：${command.label}`, 'redo')
  }

  return { busy, pendingRefresh, undoStack, redoStack, run, undo, redo, retryRefresh, refreshCommitted }
}
