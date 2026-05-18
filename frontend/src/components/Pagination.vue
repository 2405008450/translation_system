<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const props = withDefaults(defineProps<{
  total: number
  page: number
  pageSize: number
  pageSizes?: number[]
}>(), {
  pageSizes: () => [10, 20, 50],
})

const emit = defineEmits<{
  'update:page': [page: number]
  'update:pageSize': [size: number]
}>()

const jumpValue = ref('')
const { t } = useI18n()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const liveText = computed(() => t('pagination.live', { page: props.page, total: totalPages.value }))

const visiblePages = computed(() => {
  const pages: (number | 'ellipsis-left' | 'ellipsis-right')[] = []
  const total = totalPages.value
  const current = props.page

  if (total <= 7) {
    for (let i = 1; i <= total; i++) pages.push(i)
    return pages
  }

  pages.push(1)

  if (current > 4) {
    pages.push('ellipsis-left')
  }

  const start = Math.max(2, current - 1)
  const end = Math.min(total - 1, current + 1)

  for (let i = start; i <= end; i++) {
    pages.push(i)
  }

  if (current < total - 3) {
    pages.push('ellipsis-right')
  }

  if (total > 1) {
    pages.push(total)
  }

  return pages
})

function goTo(page: number) {
  const clamped = Math.max(1, Math.min(page, totalPages.value))
  if (clamped !== props.page) {
    emit('update:page', clamped)
  }
}

function prev() {
  goTo(props.page - 1)
}

function next() {
  goTo(props.page + 1)
}

function handleJump() {
  const val = parseInt(jumpValue.value, 10)
  if (!isNaN(val)) {
    goTo(val)
  }
  jumpValue.value = ''
}

function changePageSize(e: Event) {
  const val = parseInt((e.target as HTMLSelectElement).value, 10)
  emit('update:pageSize', val)
  emit('update:page', 1)
}
</script>

<template>
  <div class="pagination">
    <span class="pagination__info">{{ t('pagination.total', { total }) }}</span>
    <span class="sr-only" aria-live="polite">{{ liveText }}</span>

    <select
      class="pagination__size-select"
      :value="pageSize"
      @change="changePageSize"
    >
      <option v-for="size in pageSizes" :key="size" :value="size">{{ t('pagination.pageSize', { size }) }}</option>
    </select>

    <button
      class="pagination__btn"
      :disabled="page <= 1"
      @click="prev"
    >&lt;</button>

    <template v-for="item in visiblePages" :key="item">
      <span v-if="typeof item === 'string'" class="pagination__ellipsis">...</span>
      <button
        v-else
        class="pagination__btn"
        :class="{ 'is-active': item === page }"
        @click="goTo(item)"
      >{{ item }}</button>
    </template>

    <button
      class="pagination__btn"
      :disabled="page >= totalPages"
      @click="next"
    >&gt;</button>

    <div class="pagination__jump">
      <span>{{ t('pagination.jumpTo') }}</span>
      <input
        v-model="jumpValue"
        class="pagination__jump-input"
        type="number"
        min="1"
        :max="totalPages"
        :aria-label="t('pagination.jumpInput')"
        @keyup.enter="handleJump"
      />
      <span>{{ t('pagination.page') }}</span>
    </div>
  </div>
</template>
