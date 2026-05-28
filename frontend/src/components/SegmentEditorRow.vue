<script setup lang="ts">
import { computed, onMounted, ref, watch, nextTick } from 'vue'

import InteractiveDiffText from './InteractiveDiffText.vue'

import { getSegmentSourceMeta, getSegmentStatusMeta } from '../constants/status'
import type { Segment, SegmentRevisionEntry, TermEntryRecord } from '../types/api'
import { computeDiff } from '../utils/textDiff'
import type { TextFormat } from '../composables/useRichTextEditor'

const props = withDefaults(defineProps<{
  segment: Segment
  index: number
  active: boolean
  disabled?: boolean
  sourceEditing?: boolean
  selected?: boolean
  pendingRevision?: SegmentRevisionEntry | null
  revisionBusy?: boolean
  matchedTerms?: TermEntryRecord[]
  showVisibleChars?: boolean
  pendingFormats?: Record<TextFormat, boolean> & { _overrideActive?: boolean }
}>(), {
  disabled: false,
  sourceEditing: false,
  selected: false,
  pendingRevision: null,
  revisionBusy: false,
  matchedTerms: () => [],
  showVisibleChars: false,
  pendingFormats: () => ({
    bold: false,
    italic: false,
    underline: false,
    strikethrough: false,
    subscript: false,
    superscript: false,
    _overrideActive: false,
  }),
})

const emit = defineEmits<{
  update: [sentenceId: string, value: string, html?: string]
  updateSource: [sentenceId: string, value: string]
  focus: [sentenceId: string]
  activateTarget: [sentenceId: string]
  applyPartialRevision: [revisionId: string, newText: string]
  ctrlClick: [sentenceId: string, event: MouseEvent]
}>()

const editorRef = ref<HTMLDivElement | null>(null)
const sourceEditorRef = ref<HTMLDivElement | null>(null)
const isFocused = ref(false)
const isSourceFocused = ref(false)
const isComposing = ref(false)

const statusClass = computed(() => `segment-row--${props.segment.status || 'none'}`)
const parityClass = computed(() => (props.index % 2 === 0 ? 'segment-row--odd' : 'segment-row--even'))
const sourceClass = computed(() => `segment-row__tag--source-${props.segment.source || 'none'}`)
const isEmptyTarget = computed(() => {
  const targetText = props.pendingRevision?.after_text ?? props.segment.target_text ?? ''
  return targetText.trim().length === 0
})
const statusMeta = computed(() => getSegmentStatusMeta(props.segment.status))
const sourceMeta = computed(() => getSegmentSourceMeta(props.segment.source))
const sourceLabel = computed(() => {
  if (props.segment.source === 'llm') {
    return props.segment.llm_model?.trim() || sourceMeta.value.label
  }
  return sourceMeta.value.label
})
const sourceTitle = computed(() => {
  if (props.segment.source === 'llm' && props.segment.llm_model?.trim()) {
    return props.segment.llm_provider
      ? `${props.segment.llm_model} (${props.segment.llm_provider})`
      : props.segment.llm_model
  }
  return sourceMeta.value.label
})
const revisionSourceMeta = computed(() => getSegmentSourceMeta(props.pendingRevision?.source || 'manual'))
const revisionAuthorRole = computed(() => props.pendingRevision?.author?.role || 'admin')
const hasPendingRevision = computed(() => Boolean(props.pendingRevision))
const revisionAuthorClass = computed(() => (
  revisionAuthorRole.value === 'user' ? 'is-revision-author-user' : 'is-revision-author-admin'
))
const scorePercent = computed(() => {
  if (!props.segment.score || props.segment.score <= 0) return null
  return Math.round(props.segment.score * 100)
})

// 通用的文本高亮函数
function highlightText(
  text: string,
  terms: TermEntryRecord[],
  field: 'source_text' | 'target_text'
): Array<{ text: string; highlight: boolean }> | null {
  if (!text || terms.length === 0) {
    return null
  }

  // 按长度降序排列，优先匹配长术语
  const sortedTerms = [...terms].sort(
    (a, b) => b[field].length - a[field].length
  )

  // 找出所有匹配位置
  const matches: Array<{ start: number; end: number }> = []
  const lowerText = text.toLowerCase()

  for (const term of sortedTerms) {
    const termText = term[field]
    if (!termText) continue
    const lowerTerm = termText.toLowerCase()
    let pos = 0
    while ((pos = lowerText.indexOf(lowerTerm, pos)) !== -1) {
      // 检查是否与已有匹配重叠
      const overlaps = matches.some(
        (m) => !(pos + lowerTerm.length <= m.start || pos >= m.end)
      )
      if (!overlaps) {
        matches.push({ start: pos, end: pos + lowerTerm.length })
      }
      pos += 1
    }
  }

  if (matches.length === 0) {
    return null
  }

  // 按位置排序
  matches.sort((a, b) => a.start - b.start)

  // 构建分段
  const segments: Array<{ text: string; highlight: boolean }> = []
  let lastEnd = 0

  for (const match of matches) {
    if (match.start > lastEnd) {
      segments.push({ text: text.slice(lastEnd, match.start), highlight: false })
    }
    segments.push({ text: text.slice(match.start, match.end), highlight: true })
    lastEnd = match.end
  }

  if (lastEnd < text.length) {
    segments.push({ text: text.slice(lastEnd), highlight: false })
  }

  return segments
}

// 高亮原文中匹配的术语
const highlightedSourceText = computed(() => {
  const text = props.segment.display_text || props.segment.source_text
  return highlightText(text, props.matchedTerms || [], 'source_text')
})

// 高亮译文中匹配的术语
const highlightedTargetText = computed(() => {
  const text = props.segment.target_text || ''
  return highlightText(text, props.matchedTerms || [], 'target_text')
})

// 生成带高亮的 HTML
const targetHtmlContent = computed(() => {
  // 如果有保存的格式化 HTML，优先使用
  if (props.segment.target_html) {
    return props.segment.target_html
  }
  
  const segments = highlightedTargetText.value
  if (!segments) {
    return escapeHtml(props.segment.target_text || '')
  }
  return segments
    .map((seg) =>
      seg.highlight
        ? `<mark class="segment-row__term-highlight">${escapeHtml(seg.text)}</mark>`
        : escapeHtml(seg.text)
    )
    .join('')
})

const editorHtmlContent = computed(() => {
  const revision = props.pendingRevision
  if (!revision) {
    return targetHtmlContent.value
  }
  return computeDiff(revision.before_text || '', revision.after_text || '')
    .map((segment) => {
      const editableAttr = segment.type === 'delete' ? ' contenteditable="false"' : ''
      return [
        `<span class="segment-row__revision-segment segment-row__revision-${segment.type}"`,
        ` data-revision-type="${segment.type}"`,
        editableAttr,
        '>',
        renderTargetTextHtml(segment.text),
        '</span>',
      ].join('')
    })
    .join('')
})

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * 将文本转换为显示标记模式（显示空格、制表符、换行符）
 */
function textToVisibleChars(text: string): string {
  if (!props.showVisibleChars) return text
  return text
    .replace(/ /g, '<span class="visible-char visible-char--space">·</span>')
    .replace(/\t/g, '<span class="visible-char visible-char--tab">→</span>')
    .replace(/\n/g, '<span class="visible-char visible-char--newline">¶</span>\n')
}

function renderTargetTextHtml(text: string): string {
  const segments = highlightText(text, props.matchedTerms || [], 'target_text')
  if (!segments) {
    return escapeHtml(text)
  }
  return segments
    .map((seg) =>
      seg.highlight
        ? `<mark class="segment-row__term-highlight">${escapeHtml(seg.text)}</mark>`
        : escapeHtml(seg.text)
    )
    .join('')
}

// 保存和恢复光标位置
function saveCaretPosition(el: HTMLElement): number {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) return 0
  
  const range = selection.getRangeAt(0)
  const preCaretRange = range.cloneRange()
  preCaretRange.selectNodeContents(el)
  preCaretRange.setEnd(range.startContainer, range.startOffset)
  return preCaretRange.toString().length
}

function restoreCaretPosition(el: HTMLElement, offset: number) {
  const selection = window.getSelection()
  if (!selection) return

  const range = document.createRange()
  let currentOffset = 0
  let found = false

  function traverse(node: Node): boolean {
    if (node.nodeType === Node.TEXT_NODE) {
      const textLength = node.textContent?.length || 0
      if (currentOffset + textLength >= offset) {
        range.setStart(node, offset - currentOffset)
        range.collapse(true)
        return true
      }
      currentOffset += textLength
    } else {
      for (const child of Array.from(node.childNodes)) {
        if (traverse(child)) return true
      }
    }
    return false
  }

  found = traverse(el)
  if (!found) {
    // 如果没找到，放到末尾
    range.selectNodeContents(el)
    range.collapse(false)
  }

  selection.removeAllRanges()
  selection.addRange(range)
}

function isRevisionDeleteNode(node: Node): boolean {
  const element = node.nodeType === Node.ELEMENT_NODE
    ? node as Element
    : node.parentElement
  return Boolean(element?.closest('[data-revision-type="delete"]'))
}

function getSerializableNodeLength(node: Node): number {
  if (isRevisionDeleteNode(node)) {
    return 0
  }
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent?.length || 0
  }
  if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR') {
    return 1
  }
  return Array.from(node.childNodes).reduce((total, child) => total + getSerializableNodeLength(child), 0)
}

function serializeEditorContent(node: Node): string {
  if (isRevisionDeleteNode(node)) {
    return ''
  }
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent || ''
  }
  if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR') {
    return '\n'
  }
  return Array.from(node.childNodes).map(serializeEditorContent).join('')
}

/**
 * 序列化编辑器内容，保留格式标签
 */
function serializeEditorContentWithFormat(node: Node): string {
  if (isRevisionDeleteNode(node)) {
    return ''
  }
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent || ''
  }
  if (node.nodeType === Node.ELEMENT_NODE) {
    const el = node as HTMLElement
    const tagName = el.tagName.toLowerCase()
    
    // BR 标签转换为换行
    if (tagName === 'br') {
      return '\n'
    }
    
    // 处理子节点
    const childContent = Array.from(el.childNodes)
      .map(child => serializeEditorContentWithFormat(child))
      .join('')
    
    // 保留格式标签
    const formatTags = ['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'del', 'sub', 'sup']
    if (formatTags.includes(tagName)) {
      const normalizedTag = normalizeTagName(tagName)
      return `<${normalizedTag}>${childContent}</${normalizedTag}>`
    }
    
    return childContent
  }
  return ''
}

function saveSerializableCaretPosition(el: HTMLElement): number {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0) return 0

  const range = selection.getRangeAt(0)
  if (!el.contains(range.startContainer)) {
    return 0
  }

  let offset = 0
  let found = false

  function traverse(node: Node): boolean {
    if (found) {
      return true
    }

    if (node === range.startContainer) {
      if (node.nodeType === Node.TEXT_NODE) {
        offset += isRevisionDeleteNode(node)
          ? 0
          : (node.textContent || '').slice(0, range.startOffset).length
      } else {
        const children = Array.from(node.childNodes).slice(0, range.startOffset)
        offset += children.reduce((total, child) => total + getSerializableNodeLength(child), 0)
      }
      found = true
      return true
    }

    if (node.nodeType === Node.TEXT_NODE || (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR')) {
      offset += getSerializableNodeLength(node)
      return false
    }

    for (const child of Array.from(node.childNodes)) {
      if (traverse(child)) {
        return true
      }
    }
    return false
  }

  traverse(el)
  return offset
}

function restoreSerializableCaretPosition(el: HTMLElement, offset: number) {
  const selection = window.getSelection()
  if (!selection) return

  const range = document.createRange()
  let currentOffset = 0
  let found = false

  function traverse(node: Node): boolean {
    if (isRevisionDeleteNode(node)) {
      return false
    }
    if (node.nodeType === Node.TEXT_NODE) {
      const textLength = node.textContent?.length || 0
      if (currentOffset + textLength >= offset) {
        range.setStart(node, offset - currentOffset)
        range.collapse(true)
        return true
      }
      currentOffset += textLength
    } else if (node.nodeType === Node.ELEMENT_NODE && (node as HTMLElement).tagName === 'BR') {
      if (currentOffset + 1 >= offset) {
        range.setStartBefore(node)
        range.collapse(true)
        return true
      }
      currentOffset += 1
    } else {
      for (const child of Array.from(node.childNodes)) {
        if (traverse(child)) return true
      }
    }
    return false
  }

  found = traverse(el)
  if (!found) {
    range.selectNodeContents(el)
    range.collapse(false)
  }

  selection.removeAllRanges()
  selection.addRange(range)
}

function handleFocus() {
  isFocused.value = true
  emit('focus', props.segment.sentence_id)
}

function handleBlur() {
  isFocused.value = false
}

function handleClick(event?: MouseEvent) {
  if (event && (event.ctrlKey || event.metaKey)) {
    emit('ctrlClick', props.segment.sentence_id, event)
    return
  }
  emit('activateTarget', props.segment.sentence_id)
}

function handleSourceFocus() {
  isSourceFocused.value = true
}

function handleSourceBlur() {
  isSourceFocused.value = false
}

function handleSourceInput() {
  if (!sourceEditorRef.value) return
  if (!props.sourceEditing) {
    // 非编辑模式下恢复原文内容
    sourceEditorRef.value.textContent = props.segment.display_text || props.segment.source_text
    return
  }
  const text = sourceEditorRef.value.textContent || ''
  emit('updateSource', props.segment.sentence_id, text)
}

function handleSourceBeforeInput(event: Event) {
  if (!props.sourceEditing) {
    event.preventDefault()
  }
}

function handleSourceKeydown(event: KeyboardEvent) {
  if (!props.sourceEditing) {
    // 允许光标移动键和选择键
    const allowedKeys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']
    if (!allowedKeys.includes(event.key) && !event.ctrlKey && !event.metaKey) {
      event.preventDefault()
    }
  }
}

function handleEditorShellClick() {
  handleClick()
  void nextTick(() => {
    editorRef.value?.focus({ preventScroll: true })
  })
}

function handleInput() {
  if (!editorRef.value) return
  if (isComposing.value) return
  
  // 检查是否有格式标签
  const innerHTML = editorRef.value.innerHTML
  const hasFormatTags = /<(b|strong|i|em|u|s|strike|del|sub|sup)>/i.test(innerHTML)
  
  // 获取纯文本内容用于保存
  const text = serializeEditorContent(editorRef.value)
  
  // 如果有格式标签，同时传递 HTML
  if (hasFormatTags) {
    // 清理 HTML，只保留格式标签
    const cleanHtml = serializeEditorContentWithFormat(editorRef.value)
    emit('update', props.segment.sentence_id, text, cleanHtml)
    return
  }
  
  // 没有格式标签，只传递纯文本
  emit('update', props.segment.sentence_id, text)
  
  // 没有格式标签时，可以重新渲染以显示术语高亮等
  const caretPos = saveSerializableCaretPosition(editorRef.value)
  nextTick(() => {
    if (editorRef.value && isFocused.value) {
      editorRef.value.innerHTML = editorHtmlContent.value
      restoreSerializableCaretPosition(editorRef.value, caretPos)
    }
  })
}

/**
 * 检查是否有待应用的格式
 */
function hasPendingFormats(): boolean {
  if (!props.pendingFormats) return false
  const result = Object.values(props.pendingFormats).some(v => v)
  return result
}

/**
 * 根据待应用的格式包装文本
 */
function wrapTextWithFormats(text: string): string {
  let result = escapeHtml(text)
  
  // 按顺序应用格式标签
  if (props.pendingFormats.subscript) {
    result = `<sub>${result}</sub>`
  }
  if (props.pendingFormats.superscript) {
    result = `<sup>${result}</sup>`
  }
  if (props.pendingFormats.strikethrough) {
    result = `<s>${result}</s>`
  }
  if (props.pendingFormats.underline) {
    result = `<u>${result}</u>`
  }
  if (props.pendingFormats.italic) {
    result = `<i>${result}</i>`
  }
  if (props.pendingFormats.bold) {
    result = `<b>${result}</b>`
  }
  
  return result
}

/**
 * 处理输入前事件，应用待定格式
 */
function handleBeforeInput(event: InputEvent) {
  // 只处理文本插入
  if (event.inputType !== 'insertText' && event.inputType !== 'insertCompositionText') {
    return
  }
  
  // 如果正在输入法组合中，不处理
  if (isComposing.value) {
    return
  }
  
  // 如果用户没有主动操作过格式，让浏览器正常处理
  if (!props.pendingFormats?._overrideActive) {
    return
  }
  
  // 阻止默认行为，手动插入带格式的文本
  const data = event.data
  if (!data) return
  
  event.preventDefault()
  
  const wrappedHtml = wrapTextWithFormats(data)
  document.execCommand('insertHTML', false, wrappedHtml)
  
  // 触发 input 事件以同步数据
  handleInput()
}

// 监听外部数据变化，更新编辑器内容
function handleCompositionStart() {
  isComposing.value = true
}

function handleCompositionEnd() {
  isComposing.value = false
  handleInput()
}

function handlePaste(event: ClipboardEvent) {
  event.preventDefault()
  // 优先获取 HTML 格式，保留格式标签
  const html = event.clipboardData?.getData('text/html') || ''
  const text = event.clipboardData?.getData('text/plain') || ''
  
  // 如果有待应用的格式且粘贴的是纯文本，应用格式
  if (!html && text && hasPendingFormats()) {
    const wrappedHtml = wrapTextWithFormats(text)
    document.execCommand('insertHTML', false, wrappedHtml)
    handleInput()
    return
  }
  
  if (html) {
    // 清理 HTML，只保留允许的格式标签
    const cleanHtml = sanitizeHtml(html)
    document.execCommand('insertHTML', false, cleanHtml)
  } else {
    document.execCommand('insertText', false, text)
  }
}

/**
 * 清理 HTML，只保留允许的格式标签
 */
function sanitizeHtml(html: string): string {
  const allowedTags = ['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'del', 'sub', 'sup']
  const tempDiv = document.createElement('div')
  tempDiv.innerHTML = html
  
  // 递归处理节点
  function processNode(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) {
      return escapeHtml(node.textContent || '')
    }
    
    if (node.nodeType === Node.ELEMENT_NODE) {
      const el = node as HTMLElement
      const tagName = el.tagName.toLowerCase()
      
      // 处理子节点
      const childContent = Array.from(el.childNodes)
        .map(child => processNode(child))
        .join('')
      
      // 如果是允许的标签，保留它
      if (allowedTags.includes(tagName)) {
        // 规范化标签名
        const normalizedTag = normalizeTagName(tagName)
        return `<${normalizedTag}>${childContent}</${normalizedTag}>`
      }
      
      // 否则只返回内容
      return childContent
    }
    
    return ''
  }
  
  return processNode(tempDiv)
}

/**
 * 规范化标签名（strong -> b, em -> i 等）
 */
function normalizeTagName(tag: string): string {
  const map: Record<string, string> = {
    strong: 'b',
    em: 'i',
    strike: 's',
    del: 's',
  }
  return map[tag] || tag
}

onMounted(() => {
  if (editorRef.value) {
    editorRef.value.innerHTML = editorHtmlContent.value
  }
})

watch(
  () => props.segment.target_text,
  (newText) => {
    if (!isFocused.value && editorRef.value) {
      // 非聚焦状态，更新带高亮的 HTML
      editorRef.value.innerHTML = editorHtmlContent.value
    }
  }
)

// 监听高亮内容变化
watch(
  editorHtmlContent,
  (html) => {
    if (!isFocused.value && editorRef.value) {
      editorRef.value.innerHTML = html
    }
  }
)

// 将光标移动到元素末尾
function moveCursorToEnd(el: HTMLElement) {
  const range = document.createRange()
  const selection = window.getSelection()
  if (!selection) return
  
  range.selectNodeContents(el)
  range.collapse(false) // false 表示折叠到末尾
  selection.removeAllRanges()
  selection.addRange(range)
}

// 原文编辑器的当前文本（用于避免响应式干扰）
const sourceEditText = ref('')

// 监听 active 变化，初始化原文编辑器内容（允许放置光标）
watch(
  () => props.active,
  (isActive) => {
    if (isActive) {
      sourceEditText.value = props.segment.display_text || props.segment.source_text
      nextTick(() => {
        if (sourceEditorRef.value) {
          sourceEditorRef.value.textContent = sourceEditText.value
        }
      })
    }
  },
  { immediate: true }
)

// 进入原文编辑模式时聚焦
watch(
  () => props.sourceEditing && props.active,
  (shouldEdit) => {
    if (shouldEdit) {
      nextTick(() => {
        if (sourceEditorRef.value) {
          sourceEditorRef.value.focus()
          moveCursorToEnd(sourceEditorRef.value)
        }
      })
    }
  },
)

</script>

<template>
  <article
    class="segment-row"
    :class="[statusClass, parityClass, { 'is-active': active, 'is-selected': selected, 'has-pending-revision': hasPendingRevision, 'is-empty-target': isEmptyTarget }]"
    :id="`segment-${segment.sentence_id}`"
    data-testid="segment-row"
    :data-sentence-id="segment.sentence_id"
    role="group"
    :aria-label="`segment ${index + 1}`"
  >
    <div class="segment-row__meta">
      <span class="segment-row__index">#{{ index + 1 }}</span>
      <span class="segment-row__tag segment-row__tag--status">{{ statusMeta.label }}</span>
      <span class="segment-row__tag is-muted" :class="sourceClass" :title="sourceTitle">{{ sourceLabel }}</span>
      <span v-if="scorePercent !== null" class="segment-row__tag segment-row__tag--score">
        {{ scorePercent }}%
      </span>
      <span
        v-if="hasPendingRevision"
        class="segment-row__tag segment-row__tag--revision"
        :title="`修订来源：${revisionSourceMeta.label}`"
      >
        待审核
      </span>
    </div>

    <div class="segment-row__cell segment-row__cell--source" @click="handleClick">
      <div
        v-if="active"
        ref="sourceEditorRef"
        class="segment-row__source-editor"
        :class="{ 'is-focused': isSourceFocused, 'is-readonly': !sourceEditing }"
        :contenteditable="true"
        tabindex="0"
        spellcheck="false"
        @focus="handleSourceFocus"
        @blur="handleSourceBlur"
        @input="handleSourceInput"
        @keydown="handleSourceKeydown"
        @beforeinput="handleSourceBeforeInput"
      ></div>
      <div v-else class="segment-row__text">
        <template v-if="highlightedSourceText">
          <template v-for="(seg, idx) in highlightedSourceText" :key="idx">
            <mark v-if="seg.highlight" class="segment-row__term-highlight">{{ seg.text }}</mark>
            <template v-else>{{ seg.text }}</template>
          </template>
        </template>
        <template v-else>{{ segment.display_text || segment.source_text }}</template>
      </div>
    </div>

    <div class="segment-row__cell segment-row__cell--target" :class="{ 'is-pending': hasPendingRevision }">
      <div
        class="segment-row__editor-shell"
        :class="{ 'is-focused': isFocused, 'is-disabled': disabled, 'has-revision': hasPendingRevision }"
        @click="handleEditorShellClick"
      >
      <div
        v-if="false && pendingRevision"
        class="segment-row__revision-inline"
        :data-sentence-id="segment.sentence_id"
        :aria-label="`translation revision for segment ${index + 1}`"
        @click="handleClick"
      >
        <InteractiveDiffText
          :key="`${pendingRevision?.id || ''}:${pendingRevision?.after_text || ''}`"
          class="segment-row__revision-diff"
          :old-text="pendingRevision?.before_text || ''"
          :new-text="pendingRevision?.after_text || ''"
          :disabled="disabled || revisionBusy"
          :show-context-menu="false"
          :show-pending-hint="false"
          :revision-author-role="revisionAuthorRole"
          empty-text="空"
        />
      </div>
      <div
        ref="editorRef"
        class="segment-row__editor"
        :class="[
          { 'is-focused': isFocused, 'has-revision': hasPendingRevision },
          revisionAuthorClass,
        ]"
        :contenteditable="!disabled"
        tabindex="0"
        data-testid="segment-target-editor"
        data-segment-target="true"
        :data-sentence-id="segment.sentence_id"
        :aria-label="`translation for segment ${index + 1}`"
        spellcheck="false"
        @focus="handleFocus"
        @blur="handleBlur"
        @click="handleClick"
        @compositionstart="handleCompositionStart"
        @compositionend="handleCompositionEnd"
        @beforeinput="handleBeforeInput"
        @input="handleInput"
        @paste="handlePaste"
      />
      </div>
    </div>
  </article>
</template>

<style scoped>
.segment-row.is-selected {
  background-color: rgba(13, 122, 104, 0.12);
  outline: 2px solid rgba(13, 122, 104, 0.45);
  outline-offset: -2px;
  border-radius: 4px;
}

.segment-row__cell--target.is-pending {
  box-shadow: inset 2px 0 0 rgba(0, 122, 204, 0.36);
}

.segment-row__editor-shell {
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 64px;
  display: contents;
  flex-direction: column;
  border: 1px solid transparent;
  border-radius: 5px;
  background:
    linear-gradient(
      0deg,
      var(--segment-editor-stripe, transparent),
      var(--segment-editor-stripe, transparent)
    );
  color: var(--text-primary);
  overflow: hidden;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease;
}

.segment-row__editor-shell.is-focused {
  border-color: var(--brand-700);
  background: var(--surface-panel);
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.12);
}

.segment-row__editor-shell.has-revision {
  border-color: rgba(0, 122, 204, 0.28);
}

.segment-row__editor-shell.is-disabled {
  background: var(--surface-muted);
  cursor: not-allowed;
  opacity: 0.7;
}

.segment-row__revision-inline {
  flex: 0 0 auto;
  width: 100%;
  max-height: 96px;
  display: block;
  padding: 6px 8px 4px;
  border-bottom: 1px dashed rgba(0, 122, 204, 0.24);
  background:
    linear-gradient(
      0deg,
      rgba(0, 122, 204, 0.035),
      rgba(0, 122, 204, 0.035)
    ),
    transparent;
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.45;
  outline: none;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.segment-row__revision-diff {
  min-height: 0;
  overflow: visible;
}

.segment-row__tag--revision {
  background: rgba(0, 122, 204, 0.12);
  color: #0070c0;
}

.segment-row__tag--score {
  background: rgba(216, 183, 78, 0.18);
  color: #8a6700;
}

.segment-row__term-highlight {
  background: rgba(216, 183, 78, 0.28);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  font-weight: 500;
}

/* 穿透 scoped 样式，让 innerHTML 插入的 mark 标签也能应用样式 */
.segment-row__editor :deep(.segment-row__term-highlight) {
  background: rgba(216, 183, 78, 0.28);
  color: inherit;
  padding: 1px 2px;
  border-radius: 3px;
  font-weight: 500;
}

.segment-row__editor {
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 64px;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 5px;
  background:
    linear-gradient(
      0deg,
      var(--segment-editor-stripe, transparent),
      var(--segment-editor-stripe, transparent)
    );
  font-size: 13px;
  line-height: 1.45;
  color: var(--text-primary);
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  overflow: auto;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.segment-row__editor:focus,
.segment-row__editor.is-focused {
  border-color: var(--brand-700);
  background: var(--surface-panel);
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.12);
}

.segment-row__editor.has-revision {
  border-color: rgba(0, 122, 204, 0.32);
}

.segment-row__editor :deep(.segment-row__revision-segment) {
  white-space: pre-wrap;
}

.segment-row__editor :deep(.segment-row__revision-insert) {
  color: #0070c0;
  text-decoration: underline;
  text-decoration-color: rgba(0, 122, 204, 0.9);
  text-decoration-thickness: 1px;
  text-underline-offset: 2px;
}

.segment-row__editor :deep(.segment-row__revision-delete) {
  color: #d69a00;
  text-decoration: line-through;
  text-decoration-color: rgba(214, 154, 0, 0.95);
  text-decoration-thickness: 1px;
  user-select: text;
}

.segment-row__editor.is-revision-author-user :deep(.segment-row__revision-insert) {
  color: #2e7d32;
  text-decoration-color: rgba(46, 125, 50, 0.9);
}

.segment-row__editor.is-revision-author-user :deep(.segment-row__revision-delete) {
  color: #c62828;
  text-decoration-color: rgba(198, 40, 40, 0.9);
}

.segment-row__editor[contenteditable="false"] {
  background: var(--surface-muted);
  cursor: not-allowed;
  opacity: 0.7;
}

.segment-row__editor:empty::before {
  content: '';
  color: var(--text-placeholder);
}

.segment-row__source-editor {
  flex: 1 1 auto;
  width: 100%;
  min-height: 64px;
  padding: 6px 8px;
  border: 1px solid var(--brand-700, #0d7a68);
  border-radius: 5px;
  background: var(--surface-panel, #fff);
  font-size: 13px;
  line-height: 1.45;
  color: var(--text-primary);
  outline: none;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  overflow: auto;
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.12);
}

.segment-row__source-editor.is-focused {
  border-color: var(--brand-700, #0d7a68);
  box-shadow: 0 0 0 3px rgba(13, 122, 104, 0.18);
}

.segment-row__source-editor.is-readonly {
  border-color: var(--border-muted, #e2e8f0);
  background: var(--surface-panel, #fff);
  box-shadow: none;
  cursor: text;
}

.segment-row__source-editor.is-readonly.is-focused {
  border-color: var(--brand-400, #5bb5a6);
  box-shadow: 0 0 0 2px rgba(13, 122, 104, 0.08);
}

/* 显示标记样式 */
.segment-row__editor :deep(.visible-char) {
  color: #9ca3af;
  font-size: 0.85em;
  user-select: none;
  pointer-events: none;
}

.segment-row__editor :deep(.visible-char--space) {
  color: #d1d5db;
}

.segment-row__editor :deep(.visible-char--tab) {
  color: #93c5fd;
}

.segment-row__editor :deep(.visible-char--newline) {
  color: #fca5a5;
}

/* 富文本格式样式 */
.segment-row__editor :deep(b),
.segment-row__editor :deep(strong) {
  font-weight: 700;
}

.segment-row__editor :deep(i),
.segment-row__editor :deep(em) {
  font-style: italic;
}

.segment-row__editor :deep(u) {
  text-decoration: underline;
}

.segment-row__editor :deep(s),
.segment-row__editor :deep(strike),
.segment-row__editor :deep(del) {
  text-decoration: line-through;
}

.segment-row__editor :deep(sub) {
  vertical-align: sub;
  font-size: 0.75em;
}

.segment-row__editor :deep(sup) {
  vertical-align: super;
  font-size: 0.75em;
}
</style>
