<script setup lang="ts">
import axios from 'axios'
import { ClipboardList, Loader2, Search } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { http } from '../api/http'
import type { AssignmentEvent, AssignmentEventsResponse } from '../types/api'

const loading = ref(false)
const pageError = ref('')
const events = ref<AssignmentEvent[]>([])
const actionFilter = ref('')

const actionLabels: Record<string, string> = {
  project_assigned: '项目指派',
  project_unassigned: '取消项目指派',
  file_permission_granted: '文件授权',
  file_permission_revoked: '取消文件授权',
}

const filteredEvents = computed(() => {
  const keyword = actionFilter.value.trim().toLowerCase()
  if (!keyword) {
    return events.value
  }
  return events.value.filter((event) => {
    return [
      actionLabels[event.action] || event.action,
      event.project_name || '',
      event.file_record_name || '',
      event.assignee?.nickname || event.assignee?.username || '',
      event.actor?.nickname || event.actor?.username || '',
    ].some((value) => value.toLowerCase().includes(keyword))
  })
})

function getDisplayName(user: AssignmentEvent['assignee']) {
  return user?.nickname || user?.username || '--'
}

function getActionLabel(action: string) {
  return actionLabels[action] || action
}

function formatDate(value: string) {
  const date = new Date(value)
  return `${date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })} ${date.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
  })}`
}

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

async function loadEvents() {
  loading.value = true
  pageError.value = ''
  try {
    const { data } = await http.get<AssignmentEventsResponse>('/assignment-events', {
      params: { limit: 300 },
    })
    events.value = data.items
  } catch (error) {
    pageError.value = getErrorMessage(error, '指派记录加载失败。')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadEvents()
})
</script>

<template>
  <div class="assignment-page table-page">
    <div class="table-toolbar table-toolbar--page">
      <div class="table-toolbar__left">
        <div class="table-page__search">
          <Search :size="14" class="table-page__search-icon" />
          <input
            v-model="actionFilter"
            class="table-page__search-input"
            type="text"
            placeholder="搜索项目、文件、译者或操作人"
          />
        </div>
        <span class="table-toolbar__summary">共 {{ filteredEvents.length }} 条</span>
      </div>
      <div class="table-toolbar__right">
        <button class="button" type="button" :disabled="loading" @click="loadEvents">
          <Loader2 v-if="loading" class="lucide-spin" :size="14" />
          <ClipboardList v-else :size="14" />
          刷新
        </button>
      </div>
    </div>

    <p v-if="pageError" class="form-message is-error assignment-page__message">{{ pageError }}</p>

    <div class="assignment-table-wrap">
      <table class="assignment-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>操作</th>
            <th>项目</th>
            <th>文件</th>
            <th>译者</th>
            <th>操作人</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="6" class="assignment-table__empty">正在加载...</td>
          </tr>
          <tr v-else-if="filteredEvents.length === 0">
            <td colspan="6" class="assignment-table__empty">暂无指派记录</td>
          </tr>
          <template v-else>
            <tr v-for="event in filteredEvents" :key="event.id">
              <td>{{ formatDate(event.created_at) }}</td>
              <td>
                <span class="assignment-action">{{ getActionLabel(event.action) }}</span>
              </td>
              <td>{{ event.project_name || '--' }}</td>
              <td>{{ event.file_record_name || '项目级' }}</td>
              <td>{{ getDisplayName(event.assignee) }}</td>
              <td>{{ getDisplayName(event.actor) }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.table-toolbar--page {
  padding: 18px 20px 10px;
}

.table-toolbar__summary {
  color: var(--text-muted);
  font-size: 13px;
}

.assignment-page__message {
  margin: 0 20px 8px;
}

.assignment-table-wrap {
  margin: 0 20px 20px;
  overflow: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.assignment-table {
  width: 100%;
  min-width: 820px;
  border-collapse: collapse;
  font-size: 13px;
}

.assignment-table th,
.assignment-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-soft);
  text-align: left;
  vertical-align: middle;
}

.assignment-table th {
  background: var(--surface-muted);
  color: var(--text-muted);
  font-weight: 600;
}

.assignment-table tbody tr:last-child td {
  border-bottom: 0;
}

.assignment-action {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--surface-muted);
  color: var(--text-secondary);
}

.assignment-table__empty {
  height: 140px;
  color: var(--text-muted);
  text-align: center;
}
</style>
