<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    items: any[]
    itemHeight: number
    overscan?: number
  }>(),
  {
    overscan: 4,
  },
)

const containerRef = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const viewportHeight = ref(0)
let resizeObserver: ResizeObserver | null = null

const totalHeight = computed(() => props.items.length * props.itemHeight)
const startIndex = computed(() =>
  Math.max(0, Math.floor(scrollTop.value / props.itemHeight) - props.overscan),
)
const visibleCount = computed(
  () => Math.ceil(viewportHeight.value / props.itemHeight) + props.overscan * 2,
)
const endIndex = computed(() => Math.min(props.items.length, startIndex.value + visibleCount.value))
const offsetY = computed(() => startIndex.value * props.itemHeight)
const visibleItems = computed(() =>
  props.items.slice(startIndex.value, endIndex.value).map((item, offset) => ({
    item,
    index: startIndex.value + offset,
  })),
)

function updateViewportHeight() {
  viewportHeight.value = containerRef.value?.clientHeight ?? 0
}

function onScroll(event: Event) {
  scrollTop.value = (event.target as HTMLElement).scrollTop
}

async function scrollToIndex(index: number, align: ScrollLogicalPosition = 'center') {
  const container = containerRef.value
  if (!container || index < 0 || index >= props.items.length) {
    return false
  }

  const itemTop = index * props.itemHeight
  const maxScrollTop = Math.max(0, totalHeight.value - container.clientHeight)
  let nextScrollTop = itemTop

  if (align === 'center') {
    nextScrollTop = itemTop - (container.clientHeight - props.itemHeight) / 2
  } else if (align === 'end') {
    nextScrollTop = itemTop - container.clientHeight + props.itemHeight
  } else if (align === 'nearest') {
    const itemBottom = itemTop + props.itemHeight
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
  if (containerRef.value) {
    resizeObserver = new ResizeObserver(() => updateViewportHeight())
    resizeObserver.observe(containerRef.value)
  }
})

watch(
  () => props.items.length,
  async () => {
    await nextTick()
    updateViewportHeight()
  },
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
})
</script>

<template>
  <div ref="containerRef" class="virtual-list" @scroll="onScroll">
    <div :style="{ height: `${totalHeight}px` }" class="virtual-list-spacer">
      <div
        class="virtual-list-window"
        :style="{ transform: `translateY(${offsetY}px)` }"
      >
        <div
          v-for="entry in visibleItems"
          :key="entry.index"
          class="virtual-list-row"
          :data-virtual-index="entry.index"
          :style="{ height: `${itemHeight}px` }"
        >
          <slot :item="entry.item" :index="entry.index" />
        </div>
      </div>
    </div>
  </div>
</template>
