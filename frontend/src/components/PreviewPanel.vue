<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  html: string
  activeSentenceId: string | null
  supported: boolean
}>()

const containerRef = ref<HTMLElement | null>(null)

async function applyHighlight() {
  await nextTick()
  const container = containerRef.value
  if (!container) {
    return
  }

  const current = container.querySelector('.doc-sentence.is-active')
  current?.classList.remove('is-active')

  if (!props.activeSentenceId) {
    return
  }

  const target = container.querySelector<HTMLElement>(
    `.doc-sentence[data-sentence-id="${props.activeSentenceId}"]`,
  )
  if (!target) {
    return
  }

  target.classList.add('is-active')
  target.scrollIntoView({
    block: 'nearest',
    behavior: 'smooth',
  })
}

onMounted(() => {
  void applyHighlight()
})

watch(() => props.html, () => {
  void applyHighlight()
})

watch(() => props.activeSentenceId, () => {
  void applyHighlight()
})
</script>

<template>
  <section class="preview-panel">
    <div class="section-title">原文预览</div>
    <div v-if="supported" ref="containerRef" class="preview-panel__body" v-html="html" />
    <div v-else class="preview-panel__empty">当前任务没有可展示的预览内容</div>
  </section>
</template>
