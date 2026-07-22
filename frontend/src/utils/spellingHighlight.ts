import type { LiveSpellingIssue, SegmentQAIssue } from '../types/api'

type InlineSpellingIssue = SegmentQAIssue | LiveSpellingIssue
type HighlightRegistryLike = {
  set(name: string, highlight: unknown): void
  delete(name: string): boolean
}
type HighlightConstructorLike = new (...ranges: Range[]) => unknown

const HIGHLIGHT_NAME = 'segment-spelling-error'
let activeOwner: string | null = null

function getHighlightApi(): { registry: HighlightRegistryLike; HighlightCtor: HighlightConstructorLike } | null {
  const registry = (globalThis.CSS as typeof CSS & { highlights?: HighlightRegistryLike } | undefined)?.highlights
  const HighlightCtor = (globalThis as typeof globalThis & {
    Highlight?: HighlightConstructorLike
  }).Highlight
  if (!registry || !HighlightCtor) {
    return null
  }
  return { registry, HighlightCtor }
}

interface TextSpan {
  node: Text
  start: number
  end: number
}

function collectLogicalTextSpans(root: HTMLElement): TextSpan[] {
  const spans: TextSpan[] = []
  let cursor = 0

  function walk(node: Node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const textNode = node as Text
      const length = textNode.data.length
      if (length > 0) {
        spans.push({ node: textNode, start: cursor, end: cursor + length })
        cursor += length
      }
      return
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return
    }
    const element = node as HTMLElement
    if (
      element.classList.contains('visible-char')
      || element.dataset.revisionType === 'delete'
      || element.matches('script, style')
    ) {
      return
    }
    if (element.tagName === 'BR') {
      cursor += 1
      return
    }
    Array.from(element.childNodes).forEach(walk)
  }

  Array.from(root.childNodes).forEach(walk)
  return spans
}

function findStartBoundary(spans: TextSpan[], offset: number): { node: Text; offset: number } | null {
  const direct = spans.find((span) => offset >= span.start && offset < span.end)
  if (direct) {
    return { node: direct.node, offset: offset - direct.start }
  }
  const next = spans.find((span) => span.start >= offset)
  return next ? { node: next.node, offset: 0 } : null
}

function findEndBoundary(spans: TextSpan[], offset: number): { node: Text; offset: number } | null {
  const previousOffset = Math.max(0, offset - 1)
  const direct = [...spans].reverse().find((span) => previousOffset >= span.start && previousOffset < span.end)
  if (direct) {
    return { node: direct.node, offset: previousOffset - direct.start + 1 }
  }
  const previous = [...spans].reverse().find((span) => span.end <= offset)
  return previous ? { node: previous.node, offset: previous.node.data.length } : null
}

export function showActiveSpellingHighlight(
  owner: string,
  root: HTMLElement,
  issues: InlineSpellingIssue[],
): boolean {
  const api = getHighlightApi()
  if (!api || root.querySelector('.visible-char')) {
    return false
  }
  const spans = collectLogicalTextSpans(root)
  const ranges: Range[] = []
  const textLength = spans.length ? spans[spans.length - 1].end : 0
  for (const issue of issues) {
    const start = Math.max(0, Math.min(textLength, Number(issue.offset || 0)))
    const end = Math.max(start, Math.min(textLength, start + Math.max(0, Number(issue.length || 0))))
    if (end <= start) continue
    const startBoundary = findStartBoundary(spans, start)
    const endBoundary = findEndBoundary(spans, end)
    if (!startBoundary || !endBoundary) continue
    const range = document.createRange()
    try {
      range.setStart(startBoundary.node, startBoundary.offset)
      range.setEnd(endBoundary.node, endBoundary.offset)
      ranges.push(range)
    } catch {
      // DOM 正在更新时忽略本轮，下一次 watcher 会重新建立 Range。
    }
  }

  api.registry.delete(HIGHLIGHT_NAME)
  activeOwner = owner
  if (ranges.length > 0) {
    api.registry.set(HIGHLIGHT_NAME, new api.HighlightCtor(...ranges))
  }
  return true
}

export function clearActiveSpellingHighlight(owner: string) {
  if (activeOwner !== owner) {
    return
  }
  getHighlightApi()?.registry.delete(HIGHLIGHT_NAME)
  activeOwner = null
}
