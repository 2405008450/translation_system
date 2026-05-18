<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount, watch } from 'vue'

import { computeDiff, type DiffSegment } from '../utils/textDiff'

const props = withDefaults(defineProps<{
  oldText: string
  newText: string
  emptyText?: string
  disabled?: boolean
}>(), {
  emptyText: '',
  disabled: false,
})

const emit = defineEmits<{
  applyPartial: [newText: string]
  allResolved: [newText: string]  // 所有修改都处理完毕时触发
}>()

// 修改组：将相邻的 delete 和 insert 组合在一起作为一个修改单元
interface ChangeGroup {
  groupIndex: number
  deleteText: string
  insertText: string
  startSegmentIndex: number
  endSegmentIndex: number
}

// 每个修改组的决定状态：null=未决定, 'accept'=接受, 'reject'=拒绝
const decisions = ref<Map<number, 'accept' | 'reject'>>(new Map())

// 计算修改组
const changeGroups = computed((): ChangeGroup[] => {
  const rawSegments = computeDiff(props.oldText, props.newText)
  const groups: ChangeGroup[] = []
  let i = 0
  let groupIndex = 0
  
  while (i < rawSegments.length) {
    const seg = rawSegments[i]
    if (seg.type === 'equal') {
      i++
      continue
    }
    
    // 找到一个修改组（可能是 delete、insert 或 delete+insert）
    let deleteText = ''
    let insertText = ''
    const startIdx = i
    
    // 收集连续的 delete
    while (i < rawSegments.length && rawSegments[i].type === 'delete') {
      deleteText += rawSegments[i].text
      i++
    }
    // 收集紧随其后的 insert
    while (i < rawSegments.length && rawSegments[i].type === 'insert') {
      insertText += rawSegments[i].text
      i++
    }
    
    if (deleteText || insertText) {
      groups.push({
        groupIndex,
        deleteText,
        insertText,
        startSegmentIndex: startIdx,
        endSegmentIndex: i - 1,
      })
      groupIndex++
    }
  }
  
  return groups
})

// 当 props 变化时重置决定状态
watch([() => props.oldText, () => props.newText], () => {
  decisions.value = new Map()
})

// 计算带状态的段落用于渲染
interface RenderSegment {
  type: 'equal' | 'insert' | 'delete'
  text: string
  groupIndex: number | null
  decision: 'accept' | 'reject' | null
}

const renderSegments = computed((): RenderSegment[] => {
  const rawSegments = computeDiff(props.oldText, props.newText)
  const result: RenderSegment[] = []
  let segIdx = 0
  let groupIdx = 0
  
  while (segIdx < rawSegments.length) {
    const seg = rawSegments[segIdx]
    
    if (seg.type === 'equal') {
      result.push({ ...seg, groupIndex: null, decision: null })
      segIdx++
      continue
    }
    
    // 找到对应的修改组
    const group = changeGroups.value[groupIdx]
    if (!group) {
      segIdx++
      continue
    }
    
    const decision = decisions.value.get(group.groupIndex) || null
    
    // 处理这个修改组的所有段落
    while (segIdx <= group.endSegmentIndex && segIdx < rawSegments.length) {
      const s = rawSegments[segIdx]
      if (s.type !== 'equal') {
        result.push({ ...s, groupIndex: group.groupIndex, decision })
      }
      segIdx++
    }
    
    groupIdx++
  }
  
  return result
})

// 未决定的修改组数量
const pendingCount = computed(() => {
  return changeGroups.value.filter(g => !decisions.value.has(g.groupIndex)).length
})

const hasVisibleContent = computed(() => renderSegments.value.some((segment) => segment.text.length > 0))

// 右键菜单状态
const contextMenuVisible = ref(false)
const contextMenuPosition = ref({ x: 0, y: 0 })
const contextMenuTargetGroup = ref<number | null>(null)

function handleSegmentContextMenu(event: MouseEvent, segment: RenderSegment) {
  if (props.disabled || segment.groupIndex === null || segment.decision !== null) {
    return
  }
  event.preventDefault()
  event.stopPropagation()
  
  contextMenuPosition.value = { x: event.clientX, y: event.clientY }
  contextMenuTargetGroup.value = segment.groupIndex
  contextMenuVisible.value = true
}

function closeContextMenu() {
  contextMenuVisible.value = false
  contextMenuTargetGroup.value = null
}

// 计算当前文本（基于已做的决定）
function computeCurrentText(): string {
  const rawSegments = computeDiff(props.oldText, props.newText)
  const result: string[] = []
  let segIdx = 0
  let groupIdx = 0
  
  while (segIdx < rawSegments.length) {
    const seg = rawSegments[segIdx]
    
    if (seg.type === 'equal') {
      result.push(seg.text)
      segIdx++
      continue
    }
    
    const group = changeGroups.value[groupIdx]
    if (!group) {
      segIdx++
      continue
    }
    
    const decision = decisions.value.get(group.groupIndex)
    
    if (decision === 'accept') {
      // 接受：使用 insert 文本
      result.push(group.insertText)
    } else if (decision === 'reject') {
      // 拒绝：使用 delete 文本（原文）
      result.push(group.deleteText)
    } else {
      // 未决定：保持新文本状态（insert）
      result.push(group.insertText)
    }
    
    // 跳过这个组的所有段落
    segIdx = group.endSegmentIndex + 1
    groupIdx++
  }
  
  return result.join('')
}

// 检查是否所有修改都已处理
function checkAllResolved() {
  if (pendingCount.value === 0 && changeGroups.value.length > 0) {
    emit('allResolved', computeCurrentText())
  }
}

// 接受此处修改
function acceptThisChange() {
  if (contextMenuTargetGroup.value === null) return
  
  decisions.value.set(contextMenuTargetGroup.value, 'accept')
  decisions.value = new Map(decisions.value) // 触发响应式更新
  closeContextMenu()
  checkAllResolved()
}

// 拒绝此处修改
function rejectThisChange() {
  if (contextMenuTargetGroup.value === null) return
  
  decisions.value.set(contextMenuTargetGroup.value, 'reject')
  decisions.value = new Map(decisions.value)
  closeContextMenu()
  checkAllResolved()
}

// 总修改数
const totalCount = computed(() => changeGroups.value.length)

// 已处理的修改数
const resolvedCount = computed(() => decisions.value.size)

// 接受所有剩余修改（供父组件调用）
function acceptAllRemaining() {
  for (const group of changeGroups.value) {
    if (!decisions.value.has(group.groupIndex)) {
      decisions.value.set(group.groupIndex, 'accept')
    }
  }
  decisions.value = new Map(decisions.value)
  checkAllResolved()
}

// 拒绝所有剩余修改（供父组件调用）
function rejectAllRemaining() {
  for (const group of changeGroups.value) {
    if (!decisions.value.has(group.groupIndex)) {
      decisions.value.set(group.groupIndex, 'reject')
    }
  }
  decisions.value = new Map(decisions.value)
  checkAllResolved()
}

// 暴露给父组件
defineExpose({
  pendingCount,
  totalCount,
  resolvedCount,
  acceptAllRemaining,
  rejectAllRemaining,
})

// 点击外部关闭菜单
function handleClickOutside(event: MouseEvent) {
  const target = event.target as HTMLElement
  if (!target.closest('.diff-context-menu')) {
    closeContextMenu()
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
  document.addEventListener('contextmenu', handleClickOutside)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside)
  document.removeEventListener('contextmenu', handleClickOutside)
})
</script>

<template>
  <div class="diff-text diff-text--interactive">
    <template v-if="hasVisibleContent">
      <template v-for="(segment, index) in renderSegments" :key="`${segment.type}-${index}`">
        <!-- 已接受的修改 -->
        <template v-if="segment.decision === 'accept'">
          <!-- 接受 insert：显示新文本 -->
          <span
            v-if="segment.type === 'insert'"
            class="diff-text__segment diff-text__segment--accepted"
          >
            {{ segment.text }}
          </span>
          <!-- 接受 delete：直接删除，不显示 -->
        </template>
        <!-- 已拒绝的修改 -->
        <template v-else-if="segment.decision === 'reject'">
          <!-- 拒绝 delete：显示保留的原文本 -->
          <span
            v-if="segment.type === 'delete'"
            class="diff-text__segment diff-text__segment--rejected"
          >
            {{ segment.text }}
          </span>
          <!-- 拒绝 insert：直接不显示 -->
        </template>
        <!-- 未决定的修改：正常显示 -->
        <template v-else>
          <span
            class="diff-text__segment"
            :class="[
              `diff-text__segment--${segment.type}`,
              { 'is-clickable': segment.groupIndex !== null && !disabled }
            ]"
            @contextmenu="handleSegmentContextMenu($event, segment)"
          >
            {{ segment.text }}
          </span>
        </template>
      </template>
    </template>
    <span v-else class="diff-text__empty">{{ emptyText }}</span>

    <!-- 待处理数量提示 -->
    <div v-if="pendingCount > 0" class="diff-text__pending-hint">
      还有 {{ pendingCount }} 处修改待处理
    </div>

    <!-- 右键菜单 -->
    <Teleport to="body">
      <div
        v-if="contextMenuVisible"
        class="diff-context-menu"
        :style="{ left: contextMenuPosition.x + 'px', top: contextMenuPosition.y + 'px' }"
      >
        <button
          class="diff-context-menu__item"
          type="button"
          :disabled="disabled"
          @click="acceptThisChange"
        >
          接受此处修改
        </button>
        <button
          class="diff-context-menu__item diff-context-menu__item--danger"
          type="button"
          :disabled="disabled"
          @click="rejectThisChange"
        >
          拒绝此处修改
        </button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.diff-text {
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  line-height: 1.6;
}

.diff-text__segment--equal {
  color: inherit;
}

.diff-text__segment--insert {
  text-decoration: underline;
  text-decoration-color: rgba(71, 153, 89, 0.6);
  text-underline-offset: 2px;
  color: #1b5e3b;
}

.diff-text__segment--delete {
  text-decoration: line-through;
  text-decoration-color: rgba(182, 72, 72, 0.6);
  color: #8b3232;
}

/* 已接受的修改：新增文字，淡绿色背景 + 下划线 */
.diff-text__segment--accepted {
  background: rgba(118, 196, 132, 0.15);
  border-radius: 3px;
  text-decoration: underline;
  text-decoration-color: rgba(71, 153, 89, 0.5);
  text-underline-offset: 2px;
  color: #1b5e3b;
}

/* 已拒绝的修改：保留原文，淡灰色背景 */
.diff-text__segment--rejected {
  background: rgba(150, 150, 150, 0.12);
  border-radius: 3px;
  color: var(--text-secondary, #35515a);
}

.diff-text__segment.is-clickable {
  cursor: context-menu;
}

.diff-text__segment.is-clickable:hover {
  filter: brightness(0.92);
}

.diff-text__empty {
  color: var(--text-muted);
}

.diff-text__pending-hint {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-muted, #607677);
}
</style>

<!-- 右键菜单样式需要非 scoped，因为使用了 Teleport -->
<style>
.diff-context-menu {
  position: fixed;
  z-index: 9999;
  min-width: 160px;
  padding: 4px 0;
  border: 1px solid var(--line-soft, #d6e2de);
  border-radius: 8px;
  background: var(--surface-panel, #fff);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
}

.diff-context-menu__item {
  display: block;
  width: 100%;
  padding: 8px 14px;
  border: none;
  background: transparent;
  color: var(--text-primary, #17313b);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease;
}

.diff-context-menu__item:hover:not(:disabled) {
  background: var(--brand-050, #f4faf7);
}

.diff-context-menu__item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.diff-context-menu__item--danger {
  color: #a43a3d;
}

.diff-context-menu__item--danger:hover:not(:disabled) {
  background: rgba(194, 59, 63, 0.08);
}

.diff-context-menu__divider {
  height: 1px;
  margin: 4px 0;
  background: var(--line-soft, #d6e2de);
}
</style>
