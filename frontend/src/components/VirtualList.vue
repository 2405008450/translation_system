<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    items: any[]
    itemHeight: number
    overscan?: number
    adaptive?: boolean
    activeDescendant?: string | null
  }>(),
  {
    overscan: 4,
    adaptive: false,
    activeDescendant: null,
  },
)

const emit = defineEmits<{
  reachEnd: []
}>()

const containerRef = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const viewportHeight = ref(0)
const heightCache = reactive<Record<number, number>>({})
const rowObservers = new Map<number, ResizeObserver>()
let containerResizeObserver: ResizeObserver | null = null

function getMeasuredHeight(index: number) {
  if (!props.adaptive) {
    return props.itemHeight
  }
  return heightCache[index] || props.itemHeight
}

const prefixHeights = computed(() => {
  const heights = new Array(props.items.length + 1)
  heights[0] = 0
  for (let index = 0; index < props.items.length; index += 1) {
    heights[index + 1] = heights[index] + getMeasuredHeight(index)
  }
  return heights
})

const totalHeight = computed(() => prefixHeights.value[prefixHeights.value.length - 1] || 0)

function findIndexForOffset(offset: number) {
  let low = 0
  let high = props.items.length

  while (low < high) {
    const mid = Math.floor((low + high) / 2)
    if (prefixHeights.value[mid + 1] <= offset) {
      low = mid + 1
    } else {
      high = mid
    }
  }

  return low
}

const startIndex = computed(() => {
  if (!props.adaptive) {
    return Math.max(0, Math.floor(scrollTop.value / props.itemHeight) - props.overscan)
  }
  return Math.max(0, findIndexForOffset(Math.max(0, scrollTop.value - props.overscan * props.itemHeight)))
})

const endIndex = computed(() => {
  if (!props.adaptive) {
    const visibleCount = Math.ceil(viewportHeight.value / props.itemHeight) + props.overscan * 2
    return Math.min(props.items.length, startIndex.value + visibleCount)
  }

  const maxOffset = scrollTop.value + viewportHeight.value + props.overscan * props.itemHeight
  return Math.min(props.items.length, findIndexForOffset(maxOffset) + 1)
})

const offsetY = computed(() => prefixHeights.value[startIndex.value] || 0)
const visibleItems = computed(() =>
  props.items.slice(startIndex.value, endIndex.value).map((item, offset) => ({
    item,
    index: startIndex.value + offset,
  })),
)

function emitReachEndIfNeeded() {
  if (!props.items.length) {
    return
  }
  if (endIndex.value >= props.items.length - Math.max(props.overscan, 2)) {
    emit('reachEnd')
  }
}

function updateViewportHeight() {
  viewportHeight.value = containerRef.value?.clientHeight ?? 0
}

function onScroll(event: Event) {
  scrollTop.value = (event.target as HTMLElement).scrollTop
  emitReachEndIfNeeded()
}

function cleanupRowObserver(index: number) {
  rowObservers.get(index)?.disconnect()
  rowObservers.delete(index)
}

function setRowElement(element: Element | { $el?: Element | null } | null, index: number) {
  if (!props.adaptive) {
    return
  }

  cleanupRowObserver(index)

  const target = element instanceof HTMLElement
    ? element
    : element && '$el' in element
      ? element.$el
      : null

  if (!(target instanceof HTMLElement)) {
    return
  }

  const observer = new ResizeObserver((entries) => {
    const nextHeight = Math.ceil(entries[0]?.contentRect.height || 0)
    if (nextHeight > 0 && heightCache[index] !== nextHeight) {
      heightCache[index] = nextHeight
    }
  })

  observer.observe(target)
  rowObservers.set(index, observer)
}

async function scrollToIndex(index: number, align: ScrollLogicalPosition = 'center') {
  const container = containerRef.value
  if (!container || index < 0 || index >= props.items.length) {
    return false
  }

  const itemTop = prefixHeights.value[index] || 0
  const itemHeight = getMeasuredHeight(index)
  const maxScrollTop = Math.max(0, totalHeight.value - container.clientHeight)
  let nextScrollTop = itemTop

  if (align === 'center') {
    nextScrollTop = itemTop - (container.clientHeight - itemHeight) / 2
  } else if (align === 'end') {
    nextScrollTop = itemTop - container.clientHeight + itemHeight
  } else if (align === 'nearest') {
    const itemBottom = itemTop + itemHeight
    const viewportTop = container.scrollTop
    const viewportBottom = viewportTop + container.clientHeight
    if (itemTop < viewportTop) {
      nextScrollTop = itemTop
    } else if (itemBottom > viewportBottom) {
      nextScrollTop = itemBottom - container.clientHeight
    } else {
      nextScrollTop = container.scrollTop
    }
  }

  const safeScrollTop = Math.min(maxScrollTop, Math.max(0, nextScrollTop))
  container.scrollTop = safeScrollTop
  scrollTop.value = safeScrollTop

  await nextTick()
  await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()))
  return true
}

async function focusIndex(
  index: number,
  selector = 'textarea, input, select, button, [tabindex]',
  align: ScrollLogicalPosition = 'nearest',
) {
  const scrolled = await scrollToIndex(index, align)
  if (!scrolled) {
    return false
  }

  const container = containerRef.value
  if (!container) {
    return false
  }

  const row = container.querySelector<HTMLElement>(`[data-virtual-index="${index}"]`)
  const target = row?.querySelector<HTMLElement>(selector)
  if (!target) {
    return false
  }

  target.focus({ preventScroll: true })
  return true
}

defineExpose({
  scrollToIndex,
  focusIndex,
})

onMounted(async () => {
  await nextTick()
  updateViewportHeight()
  emitReachEndIfNeeded()
  if (containerRef.value) {
    containerResizeObserver = new ResizeObserver(() => updateViewportHeight())
    containerResizeObserver.observe(containerRef.value)
  }
})

watch(
  () => props.items.length,
  async (length) => {
    Object.keys(heightCache).forEach((key) => {
      if (Number(key) >= length) {
        delete heightCache[Number(key)]
      }
    })
    await nextTick()
    updateViewportHeight()
    emitReachEndIfNeeded()
  },
)

watch(endIndex, () => {
  emitReachEndIfNeeded()
})

onBeforeUnmount(() => {
  containerResizeObserver?.disconnect()
  rowObservers.forEach((observer) => observer.disconnect())
  rowObservers.clear()
})
</script>

<template>
  <div
    ref="containerRef"
    class="virtual-list"
    role="list"
    :aria-activedescendant="activeDescendant || undefined"
    @scroll="onScroll"
  >
    <div :style="{ height: `${totalHeight}px` }" class="virtual-list-spacer">
      <div
        class="virtual-list-window"
        :style="{ transform: `translateY(${offsetY}px)` }"
      >
        <div
          v-for="entry in visibleItems"
          :key="entry.index"
          :ref="(element) => setRowElement(element, entry.index)"
          class="virtual-list-row"
          role="listitem"
          :data-virtual-index="entry.index"
          :style="adaptive ? undefined : { height: `${itemHeight}px` }"
        >
          <slot :item="entry.item" :index="entry.index" />
        </div>
      </div>
    </div>
  </div>
</template>
