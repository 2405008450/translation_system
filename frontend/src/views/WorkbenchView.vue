<script setup lang="ts">
import axios from 'axios'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'

import PreviewPanel from '../components/PreviewPanel.vue'
import SegmentEditorRow from '../components/SegmentEditorRow.vue'
import VirtualList from '../components/VirtualList.vue'
import { useSegmentStore } from '../stores/segment'

const props = defineProps<{
  id: string
}>()

const router = useRouter()
const segmentStore = useSegmentStore()

const pageError = ref('')
const llmScope = ref<'fuzzy_only' | 'none_only' | 'all'>('all')
const llmProvider = ref<'auto' | 'deepseek' | 'openrouter'>('auto')
const itemHeight = ref(resolveItemHeight())

function resolveItemHeight() {
  if (window.innerWidth <= 720) {
    return 388
  }
  if (window.innerWidth <= 1180) {
    return 260
  }
  return 236
}

function handleResize() {
  itemHeight.value = resolveItemHeight()
}

const statusSummary = computed(() => {
  const counters = {
    exact: 0,
    fuzzy: 0,
    none: 0,
    confirmed: 0,
  }

  for (const segment of segmentStore.segments) {
    if (segment.status in counters) {
      counters[segment.status as keyof typeof counters] += 1
    }
  }

  return [
    { key: 'exact', label: '精确匹配', value: counters.exact, tone: 'exact' },
    { key: 'fuzzy', label: '模糊匹配', value: counters.fuzzy, tone: 'fuzzy' },
    { key: 'none', label: '未匹配', value: counters.none, tone: 'none' },
    { key: 'confirmed', label: '已确认', value: counters.confirmed, tone: 'confirmed' },
  ]
})

async function loadTask() {
  pageError.value = ''
  try {
    await segmentStore.loadTask(props.id)
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '任务加载失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '任务加载失败。'
  }
}

async function runLLMTranslation() {
  pageError.value = ''
  try {
    await segmentStore.startLLMTranslation(llmScope.value, llmProvider.value)
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || 'AI 修正失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : 'AI 修正失败。'
  }
}

async function saveNow() {
  pageError.value = ''
  try {
    await segmentStore.syncToBackend()
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || '保存失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : '保存失败。'
  }
}

async function exportDocx() {
  pageError.value = ''
  try {
    await segmentStore.downloadTranslatedDocx()
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || 'DOCX 导出失败。')
      return
    }
    pageError.value = error instanceof Error ? error.message : 'DOCX 导出失败。'
  }
}

watch(() => props.id, () => {
  void loadTask()
})

onMounted(() => {
  window.addEventListener('resize', handleResize)
  void loadTask()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
})

onBeforeRouteLeave(async () => {
  await segmentStore.syncToBackend()
})
</script>

<template>
  <div class="content-stack content-stack--workbench">
    <section class="panel panel--header panel--hero">
      <div class="panel-header">
        <div>
          <div class="section-title section-title--tight">{{ segmentStore.fileRecord?.filename || '任务加载中' }}</div>
          <p class="panel-subtitle">
            共 {{ segmentStore.segments.length }} 条句段，{{ segmentStore.syncMessage }}
          </p>
        </div>

        <div class="header-actions">
          <button class="button" type="button" @click="router.push({ name: 'tasks' })">返回任务列表</button>
          <button class="button" type="button" :disabled="segmentStore.saving" @click="saveNow">
            {{ segmentStore.saving ? '保存中...' : '立即保存' }}
          </button>
          <button class="button" type="button" @click="exportDocx">导出 DOCX</button>
        </div>
      </div>

      <div class="workbench-stats">
        <div
          v-for="item in statusSummary"
          :key="item.key"
          class="workbench-stat"
          :class="`workbench-stat--${item.tone}`"
        >
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </div>
      </div>
    </section>

    <p v-if="pageError" class="form-message is-error">{{ pageError }}</p>

    <section class="toolbar-panel">
      <div class="toolbar-panel__group">
        <label class="field field--compact">
          <span class="field__label">AI 处理范围</span>
          <select v-model="llmScope" class="field__control">
            <option value="all">fuzzy + none</option>
            <option value="fuzzy_only">仅 fuzzy</option>
            <option value="none_only">仅 none</option>
          </select>
        </label>

        <label class="field field--compact">
          <span class="field__label">AI 提供方</span>
          <select v-model="llmProvider" class="field__control">
            <option value="auto">自动</option>
            <option value="deepseek">DeepSeek</option>
            <option value="openrouter">OpenRouter</option>
          </select>
        </label>

        <button
          class="button button--primary"
          type="button"
          :disabled="segmentStore.llmRunning"
          @click="runLLMTranslation"
        >
          {{ segmentStore.llmRunning ? 'AI 处理中...' : '执行 AI 修正' }}
        </button>
      </div>

      <div class="toolbar-panel__status">
        <span>待同步 {{ segmentStore.dirtyCount }} 条</span>
        <span>{{ segmentStore.llmMessage }}</span>
      </div>
    </section>

    <section v-if="segmentStore.loading" class="panel">
      <div class="empty-state">任务内容加载中...</div>
    </section>

    <section v-else class="workbench-layout">
      <section class="panel panel--stretch panel--editor">
        <div class="panel-header panel-header--compact">
          <div>
            <div class="section-title section-title--tight">句段编辑</div>
            <p class="panel-subtitle">逐句校对并整理译文。</p>
          </div>
        </div>
        <VirtualList :items="segmentStore.segments" :item-height="itemHeight">
          <template #default="{ item, index }">
            <SegmentEditorRow
              :segment="item"
              :index="index"
              :active="segmentStore.activeSentenceId === item.sentence_id"
              @focus="segmentStore.setActiveSentence"
              @update="segmentStore.updateTarget"
            />
          </template>
        </VirtualList>
      </section>

      <PreviewPanel
        :html="segmentStore.previewHtml"
        :supported="segmentStore.previewSupported"
        :active-sentence-id="segmentStore.activeSentenceId"
      />
    </section>
  </div>
</template>
