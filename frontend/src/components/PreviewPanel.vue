<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  html: string
  activeSentenceId: string | null
  syncSentenceId?: string | null
  supported: boolean
  title?: string
  closable?: boolean
}>(), {
  syncSentenceId: null,
  title: '原文预览',
  closable: true,
})

const emit = defineEmits<{
  close: []
  focusSentence: [sentenceId: string]
  visibleSentenceChange: [sentenceId: string]
}>()

const containerRef = ref<HTMLElement | null>(null)
const currentPage = ref(1)
const totalPages = ref(1)
const pageSummary = computed(() => {
  if (!props.supported) {
    return ''
  }
  return `分页感知：第 ${currentPage.value} / ${totalPages.value} 页`
})

let resizeObserver: ResizeObserver | null = null
let scrollFrame = 0
let programmaticScrollTimer = 0
let ignoreScrollEvents = false
let lastVisibleSentenceId: string | null = null

function getSentenceNode(sentenceId: string | null | undefined) {
  if (!sentenceId) {
    return null
  }
  return containerRef.value?.querySelector<HTMLElement>(
    `.doc-sentence[data-sentence-id="${sentenceId}"]`,
  ) || null
}

function updatePagination() {
  const container = containerRef.value
  if (!container) {
    currentPage.value = 1
    totalPages.value = 1
    return
  }

  const viewportHeight = Math.max(container.clientHeight, 1)
  container.style.setProperty('--preview-page-height', `${viewportHeight}px`)

  const explicitBreaks = Array.from(container.querySelectorAll<HTMLElement>('.doc-page-break'))
  const estimatedPages = Math.max(1, Math.ceil(container.scrollHeight / viewportHeight))
  totalPages.value = explicitBreaks.length ? Math.max(explicitBreaks.length + 1, estimatedPages) : estimatedPages

  if (explicitBreaks.length) {
    const scrollAnchor = container.scrollTop + 8
    currentPage.value = 1 + explicitBreaks.filter((item) => item.offsetTop <= scrollAnchor).length
    return
  }

  currentPage.value = Math.min(
    totalPages.value,
    Math.max(1, Math.floor(container.scrollTop / viewportHeight) + 1),
  )
}

function findVisibleSentenceId() {
  const container = containerRef.value
  if (!container) {
    return null
  }

  const sentences = Array.from(
    container.querySelectorAll<HTMLElement>('.doc-sentence[data-sentence-id]'),
  )
  if (!sentences.length) {
    return null
  }

  const viewportCenter = container.scrollTop + container.clientHeight / 2
  let bestSentenceId = sentences[0].dataset.sentenceId || null
  let bestDistance = Number.POSITIVE_INFINITY

  for (const sentence of sentences) {
    const distance = Math.abs(sentence.offsetTop + sentence.offsetHeight / 2 - viewportCenter)
    if (distance < bestDistance) {
      bestDistance = distance
      bestSentenceId = sentence.dataset.sentenceId || null
    }
  }

  return bestSentenceId
}

function notifyVisibleSentence() {
  const sentenceId = findVisibleSentenceId()
  if (!sentenceId || sentenceId === lastVisibleSentenceId) {
    return
  }
  lastVisibleSentenceId = sentenceId
  emit('visibleSentenceChange', sentenceId)
}

function scheduleScrollMetrics() {
  if (scrollFrame) {
    window.cancelAnimationFrame(scrollFrame)
  }

  scrollFrame = window.requestAnimationFrame(() => {
    scrollFrame = 0
    updatePagination()
    notifyVisibleSentence()
  })
}

function markProgrammaticScroll(durationMs = 220) {
  ignoreScrollEvents = true
  window.clearTimeout(programmaticScrollTimer)
  programmaticScrollTimer = window.setTimeout(() => {
    ignoreScrollEvents = false
    updatePagination()
    notifyVisibleSentence()
  }, durationMs)
}

function scrollSentenceIntoView(sentenceId: string | null | undefined, behavior: ScrollBehavior = 'smooth') {
  const target = getSentenceNode(sentenceId)
  if (!target) {
    return
  }

  markProgrammaticScroll(260)
  target.scrollIntoView({
    block: 'center',
    behavior,
  })
}

async function applyHighlight(scrollToActive = true) {
  await nextTick()
  const container = containerRef.value
  if (!container) {
    return
  }

  const current = container.querySelector('.doc-sentence.is-active')
  current?.classList.remove('is-active')

  if (!props.activeSentenceId) {
    updatePagination()
    notifyVisibleSentence()
    return
  }

  const target = getSentenceNode(props.activeSentenceId)
  if (!target) {
    updatePagination()
    notifyVisibleSentence()
    return
  }

  target.classList.add('is-active')
  if (scrollToActive) {
    scrollSentenceIntoView(props.activeSentenceId)
    return
  }

  updatePagination()
  notifyVisibleSentence()
}

function handleScroll() {
  if (ignoreScrollEvents) {
    return
  }
  scheduleScrollMetrics()
}

function handleClick(event: MouseEvent) {
  const target = event.target instanceof HTMLElement ? event.target : null
  const sentence = target?.closest<HTMLElement>('.doc-sentence[data-sentence-id]')
  const sentenceId = sentence?.dataset.sentenceId
  if (!sentenceId) {
    return
  }
  emit('focusSentence', sentenceId)
}

onMounted(() => {
  void applyHighlight(true)

  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      updatePagination()
      notifyVisibleSentence()
    })
    if (containerRef.value) {
      resizeObserver.observe(containerRef.value)
    }
  }
})

onBeforeUnmount(() => {
  if (scrollFrame) {
    window.cancelAnimationFrame(scrollFrame)
  }
  window.clearTimeout(programmaticScrollTimer)
  resizeObserver?.disconnect()
})

watch(() => props.html, () => {
  lastVisibleSentenceId = null
  void applyHighlight(false)
})

watch(() => props.activeSentenceId, () => {
  void applyHighlight(true)
})

watch(() => props.syncSentenceId, (sentenceId) => {
  if (!sentenceId || sentenceId === props.activeSentenceId) {
    return
  }
  void nextTick(() => {
    scrollSentenceIntoView(sentenceId, 'auto')
  })
})
</script>

<template>
  <section class="preview-panel">
    <div class="preview-panel__header">
      <div>
        <div class="section-title section-title--tight">{{ title }}</div>
        <p v-if="pageSummary" class="preview-panel__meta">{{ pageSummary }}</p>
      </div>
      <button v-if="closable" class="button preview-panel__close" type="button" @click="emit('close')">
        关闭
      </button>
    </div>

    <div class="preview-panel__viewport">
      <div class="preview-panel__paper">
        <div
          v-if="supported"
          ref="containerRef"
          class="preview-panel__body"
          @click="handleClick"
          @scroll="handleScroll"
          v-html="html"
        />
        <div v-else class="preview-panel__empty">当前任务没有可展示的预览内容</div>
      </div>
    </div>
  </section>
</template>
