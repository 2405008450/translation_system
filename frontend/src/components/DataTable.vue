<script setup lang="ts">
import { Loader2 } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

export interface DataTableColumn {
  key: string
  label: string
  width?: string
  sortable?: boolean
  align?: 'left' | 'center' | 'right'
}

const props = withDefaults(defineProps<{
  columns: DataTableColumn[]
  data: Record<string, any>[]
  loading?: boolean
  selectable?: boolean
  selectedIds?: Set<string>
  rowKey?: string
  sortKey?: string
  sortOrder?: 'asc' | 'desc'
  showIndex?: boolean
  indexOffset?: number
  emptyText?: string
  testId?: string
  rowTestIdPrefix?: string
}>(), {
  loading: false,
  selectable: false,
  selectedIds: () => new Set<string>(),
  rowKey: 'id',
  sortKey: '',
  sortOrder: 'asc',
  showIndex: false,
  indexOffset: 0,
  emptyText: '',
  testId: undefined,
  rowTestIdPrefix: undefined,
})

const emit = defineEmits<{
  sort: [key: string, order: 'asc' | 'desc']
  select: [ids: Set<string>]
  'row-action': [row: Record<string, any>, action: string]
}>()

const slots = defineSlots<{
  [key: string]: (props: { row: Record<string, any>, index: number }) => any
}>()

const selectAllRef = ref<HTMLInputElement | null>(null)
const { t } = useI18n()

const allSelected = computed(() => {
  if (props.data.length === 0) return false
  return props.data.every(row => props.selectedIds.has(row[props.rowKey]))
})

const someSelected = computed(() => {
  if (props.data.length === 0) return false
  const count = props.data.filter(row => props.selectedIds.has(row[props.rowKey])).length
  return count > 0 && count < props.data.length
})

const tableColspan = computed(() => (
  props.columns.length
  + (props.selectable ? 1 : 0)
  + (props.showIndex ? 1 : 0)
  + (slots.actions ? 1 : 0)
))

function handleSort(col: DataTableColumn) {
  if (!col.sortable) return
  const newOrder = props.sortKey === col.key && props.sortOrder === 'asc' ? 'desc' : 'asc'
  emit('sort', col.key, newOrder)
}

function toggleAll() {
  const newSet = new Set(props.selectedIds)
  if (allSelected.value) {
    props.data.forEach(row => newSet.delete(row[props.rowKey]))
  } else {
    props.data.forEach(row => newSet.add(row[props.rowKey]))
  }
  emit('select', newSet)
}

function toggleRow(row: Record<string, any>) {
  const id = row[props.rowKey]
  const newSet = new Set(props.selectedIds)
  if (newSet.has(id)) {
    newSet.delete(id)
  } else {
    newSet.add(id)
  }
  emit('select', newSet)
}

function getSortArrow(col: DataTableColumn) {
  if (props.sortKey !== col.key) return '↕'
  return props.sortOrder === 'asc' ? '↑' : '↓'
}

function getColumnStyle(col: DataTableColumn) {
  return {
    ...(col.width ? { width: col.width, minWidth: col.width } : {}),
    ...(col.align ? { textAlign: col.align } : {}),
  }
}

watch([allSelected, someSelected], () => {
  if (selectAllRef.value) {
    selectAllRef.value.indeterminate = someSelected.value && !allSelected.value
  }
}, { immediate: true })
</script>

<template>
  <div
    class="data-table-wrapper"
    :class="{ 'is-loading': loading }"
    :aria-busy="loading"
    :data-testid="testId"
  >
    <table class="data-table">
      <thead>
        <tr>
          <th v-if="selectable" class="data-table__checkbox">
            <input
              ref="selectAllRef"
              type="checkbox"
              :checked="allSelected"
              :aria-label="t('table.selectPage')"
              @change="toggleAll"
            />
          </th>
          <th v-if="showIndex" class="data-table__index">{{ t('table.index') }}</th>
          <th
            v-for="col in columns"
            :key="col.key"
            :class="{
              'is-sortable': col.sortable,
              'is-sorted': sortKey === col.key,
            }"
            :style="getColumnStyle(col)"
            @click="handleSort(col)"
          >
            {{ col.label }}
            <span v-if="col.sortable" class="sort-arrow">{{ getSortArrow(col) }}</span>
          </th>
          <th v-if="$slots.actions" class="data-table__actions">{{ t('table.actions') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading" class="data-table__loading-spacer" aria-hidden="true">
          <td :colspan="tableColspan"></td>
        </tr>
        <tr v-else-if="data.length === 0">
          <td
            :colspan="tableColspan"
            class="data-table__empty"
          >
            {{ emptyText || t('table.empty') }}
          </td>
        </tr>
        <template v-else>
          <tr
            v-for="(row, rowIndex) in data"
            :key="row[rowKey]"
            :class="{ 'is-selected': selectedIds.has(row[rowKey]) }"
            :data-testid="rowTestIdPrefix ? `${rowTestIdPrefix}-${row[rowKey]}` : undefined"
          >
            <td v-if="selectable" class="data-table__checkbox">
              <input
                type="checkbox"
                :checked="selectedIds.has(row[rowKey])"
                :aria-label="t('table.selectRow', { index: indexOffset + rowIndex + 1 })"
                @change="toggleRow(row)"
              />
            </td>
            <td v-if="showIndex" class="data-table__index">{{ indexOffset + rowIndex + 1 }}</td>
            <td
              v-for="col in columns"
              :key="col.key"
              :style="getColumnStyle(col)"
            >
              <slot :name="col.key" :row="row" :index="rowIndex">
                {{ row[col.key] ?? '-' }}
              </slot>
            </td>
            <td v-if="$slots.actions" class="data-table__actions">
              <slot name="actions" :row="row" :index="rowIndex" />
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <div v-if="loading" class="data-table__loading-overlay" role="status" aria-live="polite">
      <Loader2 class="lucide-spin" :size="24" />
      <span>{{ t('table.loading') }}</span>
    </div>
  </div>
</template>
