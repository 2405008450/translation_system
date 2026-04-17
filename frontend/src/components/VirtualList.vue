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
          :style="{ height: `${itemHeight}px` }"
        >
          <slot :item="entry.item" :index="entry.index" />
        </div>
      </div>
    </div>
  </div>
</template>
