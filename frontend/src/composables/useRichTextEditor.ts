/**
 * 富文本编辑工具 composable
 * 提供格式化、大小写转换、显示标记等功能
 */
import { ref, reactive } from 'vue'

export type TextFormat = 'bold' | 'italic' | 'underline' | 'strikethrough' | 'subscript' | 'superscript'
export type CaseType = 'upper' | 'lower' | 'capitalize' | 'sentence'

// 显示标记的字符映射
const VISIBLE_CHAR_MAP: Record<string, string> = {
  ' ': '·',      // 空格
  '\t': '→',     // 制表符
  '\n': '¶\n',   // 换行
  '\r': '',      // 回车（忽略）
}

const VISIBLE_CHAR_REVERSE_MAP: Record<string, string> = {
  '·': ' ',
  '→': '\t',
  '¶': '',
}

export function useRichTextEditor() {
  const visibleCharactersEnabled = ref(false)
  
  // 当前激活的格式状态（用于按钮高亮显示和新输入文本的格式）
  const activeFormats = reactive<Record<TextFormat, boolean>>({
    bold: false,
    italic: false,
    underline: false,
    strikethrough: false,
    subscript: false,
    superscript: false,
  })
  
  // 标记用户是否主动操作过格式（用于决定是否需要覆盖浏览器的格式继承）
  const formatOverrideActive = ref(false)

  /**
   * 获取当前选区
   */
  function getSelection(): Selection | null {
    return window.getSelection()
  }

  /**
   * 检查选区是否在指定元素内
   */
  function isSelectionInElement(element: HTMLElement): boolean {
    const selection = getSelection()
    if (!selection || selection.rangeCount === 0) return false
    const range = selection.getRangeAt(0)
    return element.contains(range.commonAncestorContainer)
  }

  /**
   * 获取选中的文本
   */
  function getSelectedText(): string {
    const selection = getSelection()
    if (!selection || selection.rangeCount === 0) return ''
    return selection.toString()
  }

  /**
   * 应用文本格式
   * - 有选中文本时：直接应用格式到选中文本
   * - 没有选中文本时：后续输入会应用该格式
   * - 两种情况都会切换按钮激活状态
   */
  function applyFormat(format: TextFormat, element?: HTMLElement): boolean {
    const selection = getSelection()
    const hasValidSelection = selection && selection.rangeCount > 0
    const isInElement = element ? isSelectionInElement(element) : true
    const hasSelectedText = hasValidSelection && isInElement && !selection.isCollapsed
    
    if (hasSelectedText) {
      // 有选中文本：直接应用格式
      const commandMap: Record<TextFormat, string> = {
        bold: 'bold',
        italic: 'italic',
        underline: 'underline',
        strikethrough: 'strikeThrough',
        subscript: 'subscript',
        superscript: 'superscript',
      }
      const command = commandMap[format]
      if (command) {
        document.execCommand(command, false)
      }
    }
    
    // 两种情况都切换格式激活状态
    activeFormats[format] = !activeFormats[format]
    
    // 标记用户主动操作过格式
    formatOverrideActive.value = true
    
    return true
  }

  /**
   * 清除所有格式
   */
  function clearFormat(element?: HTMLElement): boolean {
    if (element && !isSelectionInElement(element)) return false
    
    const selection = getSelection()
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return false

    document.execCommand('removeFormat', false)
    return true
  }

  /**
   * 清除指定元素内的所有格式标签，保留纯文本
   */
  function clearAllFormatInElement(element: HTMLElement): string {
    const text = element.textContent || ''
    element.textContent = text
    return text
  }

  /**
   * 转换大小写
   */
  function changeCase(caseType: CaseType, element?: HTMLElement): boolean {
    if (element && !isSelectionInElement(element)) return false

    const selection = getSelection()
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return false

    const range = selection.getRangeAt(0)
    const selectedText = selection.toString()
    if (!selectedText) return false

    let transformedText: string
    switch (caseType) {
      case 'upper':
        transformedText = selectedText.toUpperCase()
        break
      case 'lower':
        transformedText = selectedText.toLowerCase()
        break
      case 'capitalize':
        // 每个单词首字母大写
        transformedText = selectedText.replace(/\b\w/g, (char) => char.toUpperCase())
        break
      case 'sentence':
        // 句首大写
        transformedText = selectedText.toLowerCase().replace(/(^|[.!?]\s+)(\w)/g, (_, prefix, char) => prefix + char.toUpperCase())
        break
      default:
        return false
    }

    // 替换选中的文本
    range.deleteContents()
    range.insertNode(document.createTextNode(transformedText))
    
    // 重新选中转换后的文本
    selection.removeAllRanges()
    const newRange = document.createRange()
    newRange.selectNodeContents(range.startContainer)
    selection.addRange(newRange)

    return true
  }

  /**
   * 切换显示标记
   */
  function toggleVisibleCharacters(): boolean {
    visibleCharactersEnabled.value = !visibleCharactersEnabled.value
    return visibleCharactersEnabled.value
  }

  /**
   * 将文本转换为显示标记模式
   */
  function textToVisibleChars(text: string): string {
    if (!visibleCharactersEnabled.value) return text
    return text.replace(/[ \t\n\r]/g, (char) => VISIBLE_CHAR_MAP[char] || char)
  }

  /**
   * 将显示标记模式转换回普通文本
   */
  function visibleCharsToText(text: string): string {
    return text.replace(/[·→¶]/g, (char) => VISIBLE_CHAR_REVERSE_MAP[char] || char)
  }

  /**
   * 获取元素的 HTML 内容（用于保存格式）
   */
  function getFormattedHtml(element: HTMLElement): string {
    return element.innerHTML
  }

  /**
   * 获取元素的纯文本内容
   */
  function getPlainText(element: HTMLElement): string {
    return element.textContent || ''
  }

  /**
   * 检查当前选区是否有指定格式
   */
  function hasFormat(format: TextFormat): boolean {
    const commandMap: Record<TextFormat, string> = {
      bold: 'bold',
      italic: 'italic',
      underline: 'underline',
      strikethrough: 'strikeThrough',
      subscript: 'subscript',
      superscript: 'superscript',
    }
    return document.queryCommandState(commandMap[format])
  }

  /**
   * 更新所有格式的激活状态（基于当前光标位置）
   */
  function updateActiveFormats() {
    const formats: TextFormat[] = ['bold', 'italic', 'underline', 'strikethrough', 'subscript', 'superscript']
    for (const format of formats) {
      // 使用 queryCommandState 检测当前位置的格式
      const state = hasFormat(format)
      activeFormats[format] = state
    }
  }

  /**
   * 重置所有格式状态（切换段落时调用）
   */
  function resetActiveFormats() {
    const formats: TextFormat[] = ['bold', 'italic', 'underline', 'strikethrough', 'subscript', 'superscript']
    for (const format of formats) {
      activeFormats[format] = false
    }
    formatOverrideActive.value = false
  }

  /**
   * 切换指定格式的激活状态
   */
  function toggleFormatState(format: TextFormat) {
    activeFormats[format] = !activeFormats[format]
  }
  
  /**
   * 清除格式覆盖标记（输入文本后调用）
   */
  function clearFormatOverride() {
    formatOverrideActive.value = false
  }

  /**
   * 在光标位置插入文本
   */
  function insertText(text: string): boolean {
    const selection = getSelection()
    if (!selection || selection.rangeCount === 0) return false
    
    document.execCommand('insertText', false, text)
    return true
  }

  /**
   * 序列化带格式的 HTML 为内联标签格式（用于存储）
   * 将 <b>, <i>, <u>, <s>, <sub>, <sup> 等标签保留
   */
  function serializeFormattedContent(element: HTMLElement): string {
    // 克隆元素以避免修改原始内容
    const clone = element.cloneNode(true) as HTMLElement
    
    // 移除不需要的属性和样式
    const allElements = clone.querySelectorAll('*')
    allElements.forEach((el) => {
      // 保留格式标签，移除其他属性
      const tagName = el.tagName.toLowerCase()
      const allowedTags = ['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'del', 'sub', 'sup', 'mark']
      
      if (allowedTags.includes(tagName)) {
        // 移除所有属性
        while (el.attributes.length > 0) {
          el.removeAttribute(el.attributes[0].name)
        }
      }
    })
    
    return clone.innerHTML
  }

  /**
   * 规范化格式标签（将 strong -> b, em -> i 等）
   */
  function normalizeFormatTags(html: string): string {
    return html
      .replace(/<strong>/gi, '<b>')
      .replace(/<\/strong>/gi, '</b>')
      .replace(/<em>/gi, '<i>')
      .replace(/<\/em>/gi, '</i>')
      .replace(/<strike>/gi, '<s>')
      .replace(/<\/strike>/gi, '</s>')
      .replace(/<del>/gi, '<s>')
      .replace(/<\/del>/gi, '</s>')
  }

  return {
    visibleCharactersEnabled,
    activeFormats,
    formatOverrideActive,
    getSelection,
    isSelectionInElement,
    getSelectedText,
    applyFormat,
    clearFormat,
    clearAllFormatInElement,
    changeCase,
    toggleVisibleCharacters,
    textToVisibleChars,
    visibleCharsToText,
    getFormattedHtml,
    getPlainText,
    hasFormat,
    updateActiveFormats,
    resetActiveFormats,
    toggleFormatState,
    clearFormatOverride,
    insertText,
    serializeFormattedContent,
    normalizeFormatTags,
  }
}
