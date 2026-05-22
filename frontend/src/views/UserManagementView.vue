<script setup lang="ts">
import axios from 'axios'
import {
  Check,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Shield,
  User,
  Users,
} from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import Modal from '../components/base/Modal.vue'
import DataTable from '../components/DataTable.vue'
import type { DataTableColumn } from '../components/DataTable.vue'
import Pagination from '../components/Pagination.vue'
import { useToast } from '../composables/useToast'
import { useAuthStore } from '../stores/auth'
import type { User as UserRecord } from '../types/api'

type RoleFilter = 'all' | 'admin' | 'user'
type SortableUserKey = 'nickname' | 'username' | 'role' | 'is_active' | 'created_at'

const authStore = useAuthStore()
const toast = useToast()
const { t } = useI18n()

const uiText = {
  columns: {
    nickname: '\u6635\u79f0',
    username: '\u7528\u6237\u540d',
    role: '\u89d2\u8272',
    status: '\u72b6\u6001',
    createdAt: '\u521b\u5efa\u65f6\u95f4',
  },
  emptyFiltered: '\u6ca1\u6709\u7b26\u5408\u6761\u4ef6\u7684\u7528\u6237',
  emptyDefault: '\u5f53\u524d\u8fd8\u6ca1\u6709\u7528\u6237',
  loadFailed: '\u7528\u6237\u5217\u8868\u52a0\u8f7d\u5931\u8d25\u3002',
  createInvalid: '\u8bf7\u8f93\u5165\u81f3\u5c11 3 \u4f4d\u7528\u6237\u540d\u548c 6 \u4f4d\u5bc6\u7801\u3002',
  createFailed: '\u7528\u6237\u521b\u5efa\u5931\u8d25\u3002',
  createForbidden: '\u53ea\u6709\u7ba1\u7406\u5458\u53ef\u4ee5\u65b0\u589e\u7528\u6237\u6216\u5206\u914d\u8d26\u53f7\u89d2\u8272\u3002',
  createSuccess: (username: string) => `\u7528\u6237 ${username} \u5df2\u521b\u5efa`,
  stats: {
    totalLabel: '\u7528\u6237\u603b\u6570',
    totalMeta: '\u7cfb\u7edf\u5f53\u524d\u5df2\u521b\u5efa\u7684\u5168\u90e8\u8d26\u53f7',
    adminLabel: '\u7ba1\u7406\u5458',
    adminMeta: '\u53ef\u914d\u7f6e\u7cfb\u7edf\u4e0e\u7ba1\u7406\u7528\u6237',
    memberLabel: '\u666e\u901a\u7528\u6237',
    memberMeta: '\u53ef\u767b\u5f55\u3001\u5904\u7406\u4efb\u52a1\u4e0e\u7f16\u8f91\u53e5\u6bb5',
    activeLabel: '\u542f\u7528\u4e2d',
    activeMeta: '\u5f53\u524d\u53ef\u6b63\u5e38\u767b\u5f55\u7684\u8d26\u53f7\u6570\u91cf',
  },
  listTitle: '\u7528\u6237\u5217\u8868',
  listNote: '\u5148\u67e5\u770b\u5df2\u6709\u8d26\u53f7\uff0c\u518d\u65b0\u589e\u7528\u6237\u6216\u5206\u914d\u89d2\u8272\uff0c\u4ea4\u4e92\u4f1a\u66f4\u987a\u624b\u3002',
  refreshing: '\u5237\u65b0\u4e2d...',
  searchPlaceholder: '\u641c\u7d22\u6635\u79f0\u3001\u7528\u6237\u540d\u6216\u89d2\u8272',
  allRoles: '\u5168\u90e8\u89d2\u8272',
  currentAccount: '\u5f53\u524d\u8d26\u53f7',
  usernameSecondary: '\u767b\u5f55\u540d',
  nicknamePlaceholder: '\u4e0d\u586b\u65f6\u9ed8\u8ba4\u4f7f\u7528\u7528\u6237\u540d',
  activeStatus: '\u6b63\u5e38',
  inactiveStatus: '\u5df2\u505c\u7528',
  createHint: '\u65b0\u521b\u5efa\u7684\u8d26\u53f7\u4f1a\u7acb\u523b\u5237\u65b0\u5230\u4e0a\u65b9\u5217\u8868\uff0c\u9ed8\u8ba4\u6309\u521b\u5efa\u65f6\u95f4\u5012\u5e8f\u5c55\u793a\u3002',
  createRestrictedTitle: '\u65b0\u589e\u7528\u6237\u5df2\u53d7\u9650',
  createRestrictedMessage: '\u666e\u901a\u7528\u6237\u53ef\u4ee5\u67e5\u770b\u548c\u7ef4\u62a4\u8d26\u53f7\u57fa\u672c\u4fe1\u606f\uff0c\u65b0\u589e\u7528\u6237\u548c\u5206\u914d\u89d2\u8272\u4ecd\u9700\u8981\u7ba1\u7406\u5458\u64cd\u4f5c\u3002',
  createDialogDescription: '\u8bbe\u7f6e\u8d26\u53f7\u57fa\u672c\u4fe1\u606f\u4e0e\u521d\u59cb\u89d2\u8272\u3002',
  operatorLabel: '\u5f53\u524d\u64cd\u4f5c\u8d26\u53f7',
  editDialogTitle: '\u7f16\u8f91\u7528\u6237',
  editDialogDescription: '\u53ef\u4fee\u6539\u6635\u79f0\u3001\u7528\u6237\u540d\u548c\u5bc6\u7801\uff0c\u5bc6\u7801\u7559\u7a7a\u5219\u4fdd\u6301\u4e0d\u53d8\u3002',
  editPasswordLabel: '\u65b0\u5bc6\u7801',
  editPasswordPlaceholder: '\u7559\u7a7a\u5219\u4e0d\u4fee\u6539\u5bc6\u7801',
  editHint: '\u6635\u79f0\u4e0d\u586b\u65f6\u4f1a\u81ea\u52a8\u56de\u843d\u4e3a\u7528\u6237\u540d\u3002',
  editTargetLabel: '\u5f53\u524d\u7f16\u8f91\u5bf9\u8c61',
  editInvalid: '\u8bf7\u8f93\u5165\u81f3\u5c11 3 \u4f4d\u7528\u6237\u540d\uff0c\u5982\u9700\u4fee\u6539\u5bc6\u7801\u8bf7\u8f93\u5165\u81f3\u5c11 6 \u4f4d\u5bc6\u7801\u3002',
  editFailed: '\u7528\u6237\u4fe1\u606f\u66f4\u65b0\u5931\u8d25\u3002',
  editSubmit: '\u4fdd\u5b58\u4fee\u6539',
  editSubmitting: '\u4fdd\u5b58\u4e2d...',
  editSuccess: (username: string) => `\u7528\u6237 ${username} \u5df2\u66f4\u65b0`,
} as const

const columns: DataTableColumn[] = [
  { key: 'nickname', label: uiText.columns.nickname, sortable: true },
  { key: 'username', label: uiText.columns.username, sortable: true },
  { key: 'role', label: uiText.columns.role, width: '120px', sortable: true },
  { key: 'is_active', label: uiText.columns.status, width: '120px', sortable: true },
  { key: 'created_at', label: uiText.columns.createdAt, width: '180px', sortable: true },
]

const users = ref<UserRecord[]>([])
const loadingUsers = ref(false)
const listError = ref('')

const searchQuery = ref('')
const roleFilter = ref<RoleFilter>('all')
const currentPage = ref(1)
const pageSize = ref(10)
const sortKey = ref<SortableUserKey>('created_at')
const sortOrder = ref<'asc' | 'desc'>('desc')

const createUserMessage = ref('')
const createUserMessageTone = ref<'success' | 'error'>('success')
const creatingUser = ref(false)
const showCreateDialog = ref(false)
const newUsername = ref('')
const newNickname = ref('')
const newPassword = ref('')
const newRole = ref<'admin' | 'user'>('user')
const editDialogOpen = ref(false)
const editingUserId = ref('')
const editUsername = ref('')
const editNickname = ref('')
const editPassword = ref('')
const editUserMessage = ref('')
const editUserMessageTone = ref<'success' | 'error'>('success')
const updatingUser = ref(false)

const totalUsers = computed(() => users.value.length)
const adminUsers = computed(() => users.value.filter((item) => item.role === 'admin').length)
const normalUsers = computed(() => users.value.filter((item) => item.role === 'user').length)
const activeUsers = computed(() => users.value.filter((item) => item.is_active).length)
const canManageUserPermissions = computed(() => authStore.isAdmin)
const canCreateUser = computed(() => (
  canManageUserPermissions.value
  && newUsername.value.trim().length >= 3
  && newPassword.value.length >= 6
))
const editingUser = computed(() => users.value.find((item) => item.id === editingUserId.value) ?? null)
const canUpdateUser = computed(() => (
  Boolean(editingUserId.value)
  && editUsername.value.trim().length >= 3
  && (editPassword.value.length === 0 || editPassword.value.length >= 6)
))

const filteredUsers = computed(() => {
  let data = [...users.value]

  if (roleFilter.value !== 'all') {
    data = data.filter((item) => item.role === roleFilter.value)
  }

  if (searchQuery.value.trim()) {
    const keyword = searchQuery.value.trim().toLowerCase()
    data = data.filter((item) => (
      getUserDisplayName(item).toLowerCase().includes(keyword)
      || item.username.toLowerCase().includes(keyword)
      || getRoleLabel(item.role).toLowerCase().includes(keyword)
    ))
  }

  const direction = sortOrder.value === 'asc' ? 1 : -1
  data.sort((left, right) => {
    switch (sortKey.value) {
      case 'nickname':
        return getUserDisplayName(left).localeCompare(getUserDisplayName(right), 'zh-CN') * direction
      case 'username':
        return left.username.localeCompare(right.username, 'zh-CN') * direction
      case 'role':
        return left.role.localeCompare(right.role) * direction
      case 'is_active':
        return (Number(left.is_active) - Number(right.is_active)) * direction
      case 'created_at':
      default:
        return (new Date(left.created_at).getTime() - new Date(right.created_at).getTime()) * direction
    }
  })

  return data
})

const totalCount = computed(() => filteredUsers.value.length)
const pagedUsers = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredUsers.value.slice(start, start + pageSize.value)
})
const indexOffset = computed(() => (currentPage.value - 1) * pageSize.value)
const emptyText = computed(() => (
  searchQuery.value.trim() || roleFilter.value !== 'all'
    ? uiText.emptyFiltered
    : uiText.emptyDefault
))
const resultSummary = computed(() => `\u5171 ${totalCount.value} \u4e2a\u7ed3\u679c`)
const operatorInitial = computed(() => getUserInitial(authStore.user))

function getErrorMessage(error: unknown, fallback: string) {
  if (axios.isAxiosError(error)) {
    return String(error.response?.data?.detail || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

function getRoleLabel(role: UserRecord['role']) {
  return role === 'admin' ? t('common.roles.admin') : t('common.roles.user')
}

function getUserDisplayName(
  user: { nickname?: string | null, username?: string | null } | null | undefined,
) {
  return user?.nickname || user?.username || ''
}

function getUserInitial(
  user: { nickname?: string | null, username?: string | null } | null | undefined,
) {
  const name = getUserDisplayName(user)
  return name ? name.charAt(0).toUpperCase() : '?'
}

function formatDate(value: string) {
  const date = new Date(value)
  return {
    date: date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }),
    time: date.toLocaleTimeString('zh-CN', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }),
  }
}

async function loadUsers() {
  loadingUsers.value = true
  listError.value = ''
  try {
    const { data } = await http.get<UserRecord[]>('/auth/users')
    users.value = data
  } catch (error) {
    listError.value = getErrorMessage(error, uiText.loadFailed)
  } finally {
    loadingUsers.value = false
  }
}

function openCreateDialog() {
  if (!canManageUserPermissions.value) {
    createUserMessageTone.value = 'error'
    createUserMessage.value = uiText.createForbidden
    return
  }

  createUserMessage.value = ''
  createUserMessageTone.value = 'success'
  showCreateDialog.value = true
}

function closeCreateDialog() {
  if (creatingUser.value) {
    return
  }
  showCreateDialog.value = false
}

async function createUser() {
  if (!canManageUserPermissions.value) {
    createUserMessageTone.value = 'error'
    createUserMessage.value = uiText.createForbidden
    return
  }

  if (!canCreateUser.value) {
    createUserMessageTone.value = 'error'
    createUserMessage.value = uiText.createInvalid
    return
  }

  createUserMessage.value = ''
  creatingUser.value = true
  try {
    const createdUser = await authStore.registerUser(
      newUsername.value.trim(),
      newPassword.value,
      newRole.value,
      newNickname.value.trim() || null,
    )

    newUsername.value = ''
    newNickname.value = ''
    newPassword.value = ''
    newRole.value = 'user'
    createUserMessageTone.value = 'success'
    createUserMessage.value = uiText.createSuccess(createdUser.username)
    currentPage.value = 1
    await loadUsers()
    showCreateDialog.value = false
    toast.success(createUserMessage.value)
  } catch (error) {
    createUserMessageTone.value = 'error'
    createUserMessage.value = getErrorMessage(error, uiText.createFailed)
  } finally {
    creatingUser.value = false
  }
}

function resetEditState() {
  editDialogOpen.value = false
  editingUserId.value = ''
  editUsername.value = ''
  editNickname.value = ''
  editPassword.value = ''
  editUserMessage.value = ''
  editUserMessageTone.value = 'success'
}

function openEditUser(
  user: { id?: string, username?: string | null, nickname?: string | null } | null | undefined,
) {
  if (!user?.id || !user.username) {
    return
  }
  editingUserId.value = user.id
  editUsername.value = user.username
  editNickname.value = user.nickname || ''
  editPassword.value = ''
  editUserMessage.value = ''
  editUserMessageTone.value = 'success'
  editDialogOpen.value = true
}

function closeEditDialog() {
  if (updatingUser.value) {
    return
  }
  resetEditState()
}

async function updateUser() {
  if (!canUpdateUser.value || !editingUser.value) {
    editUserMessageTone.value = 'error'
    editUserMessage.value = uiText.editInvalid
    return
  }

  editUserMessage.value = ''
  updatingUser.value = true
  try {
    const payload: {
      username: string
      nickname: string | null
      password?: string
    } = {
      username: editUsername.value.trim(),
      nickname: editNickname.value.trim() || null,
    }

    if (editPassword.value) {
      payload.password = editPassword.value
    }

    const { data } = await http.patch<UserRecord>(`/auth/users/${editingUserId.value}`, payload)

    if (authStore.user?.id === data.id) {
      authStore.user = data
    }

    await loadUsers()
    resetEditState()
    toast.success(uiText.editSuccess(data.username))
  } catch (error) {
    editUserMessageTone.value = 'error'
    editUserMessage.value = getErrorMessage(error, uiText.editFailed)
  } finally {
    updatingUser.value = false
  }
}

function handleSort(key: string, order: 'asc' | 'desc') {
  sortKey.value = key as SortableUserKey
  sortOrder.value = order
}

watch([searchQuery, roleFilter], () => {
  currentPage.value = 1
})

watch(pageSize, () => {
  currentPage.value = 1
})

watch(totalCount, (count) => {
  const maxPage = Math.max(1, Math.ceil(count / pageSize.value))
  if (currentPage.value > maxPage) {
    currentPage.value = maxPage
  }
})

onMounted(() => {
  void loadUsers()
})
</script>

<template>
  <div class="table-page user-management-page">
    <div class="table-page__header user-page-header">
      <div>
        <h2 class="table-page__title">{{ t('pages.users.title') }}</h2>
        <p class="user-page-header__description">{{ t('pages.users.description') }}</p>
      </div>

      <div class="user-operator">
        <span class="user-avatar">{{ operatorInitial }}</span>
        <span class="user-operator__copy">
          <span class="user-operator__label">{{ uiText.operatorLabel }}</span>
          <strong>{{ getUserDisplayName(authStore.user) || '--' }}</strong>
        </span>
        <span class="user-role-chip" :class="`is-${authStore.user?.role || 'user'}`">
          {{ authStore.user?.role === 'admin' ? t('common.roles.admin') : t('common.roles.user') }}
        </span>
      </div>
    </div>

    <section class="user-overview" aria-label="用户概览">
      <article class="user-overview-item">
        <span class="user-overview-item__icon">
          <Users :size="17" />
        </span>
        <span class="user-overview-item__copy">
          <span>{{ uiText.stats.totalLabel }}</span>
          <strong>{{ totalUsers }}</strong>
        </span>
      </article>

      <article class="user-overview-item">
        <span class="user-overview-item__icon user-overview-item__icon--admin">
          <Shield :size="17" />
        </span>
        <span class="user-overview-item__copy">
          <span>{{ uiText.stats.adminLabel }}</span>
          <strong>{{ adminUsers }}</strong>
        </span>
      </article>

      <article class="user-overview-item">
        <span class="user-overview-item__icon user-overview-item__icon--user">
          <User :size="17" />
        </span>
        <span class="user-overview-item__copy">
          <span>{{ uiText.stats.memberLabel }}</span>
          <strong>{{ normalUsers }}</strong>
        </span>
      </article>

      <article class="user-overview-item">
        <span class="user-overview-item__icon user-overview-item__icon--active">
          <Check :size="17" />
        </span>
        <span class="user-overview-item__copy">
          <span>{{ uiText.stats.activeLabel }}</span>
          <strong>{{ activeUsers }}</strong>
        </span>
      </article>
    </section>

    <div class="table-toolbar table-toolbar--page user-table-toolbar">
      <div class="table-toolbar__left user-toolbar__filters">
        <div class="table-page__search user-search">
          <Search :size="14" class="table-page__search-icon" />
          <input
            v-model="searchQuery"
            class="table-page__search-input"
            type="text"
            :placeholder="uiText.searchPlaceholder"
          />
        </div>

        <select v-model="roleFilter" class="user-filter" :aria-label="uiText.allRoles">
          <option value="all">{{ uiText.allRoles }}</option>
          <option value="admin">{{ t('common.roles.admin') }}</option>
          <option value="user">{{ t('common.roles.user') }}</option>
        </select>

        <span class="table-toolbar__summary">{{ resultSummary }}</span>
      </div>

      <div class="table-toolbar__right user-toolbar__actions">
        <button
          class="button button--primary"
          type="button"
          :disabled="!canManageUserPermissions"
          :title="canManageUserPermissions ? t('userManagement.createUser') : uiText.createRestrictedMessage"
          @click="openCreateDialog"
        >
          <Plus :size="14" />
          {{ t('userManagement.createUser') }}
        </button>

        <button
          class="button"
          type="button"
          :disabled="loadingUsers"
          @click="loadUsers"
        >
          <Loader2 v-if="loadingUsers" class="lucide-spin" :size="14" />
          <RefreshCw v-else :size="14" />
          {{ loadingUsers ? uiText.refreshing : t('common.actions.refresh') }}
        </button>
      </div>
    </div>

    <div v-if="!canManageUserPermissions" class="user-permission-note">
      <Shield :size="15" />
      <span>{{ uiText.createRestrictedMessage }}</span>
    </div>

    <p v-if="listError" class="form-message is-error user-panel-message">{{ listError }}</p>

    <div class="table-page__body user-table-shell">
      <DataTable
        :columns="columns"
        :data="pagedUsers"
        :loading="loadingUsers"
        :sort-key="sortKey"
        :sort-order="sortOrder"
        :show-index="true"
        :index-offset="indexOffset"
        :empty-text="emptyText"
        @sort="handleSort"
      >
        <template #nickname="{ row }">
          <div class="user-identity-cell">
            <span class="user-avatar user-avatar--cell">{{ getUserInitial(row) }}</span>
            <span class="user-cell__main">
              <span class="user-cell__name">{{ getUserDisplayName(row) }}</span>
              <span
                v-if="row.id === authStore.user?.id"
                class="user-badge-pill user-badge-pill--self"
              >
                {{ uiText.currentAccount }}
              </span>
            </span>
          </div>
        </template>

        <template #username="{ row }">
          <div class="user-cell user-cell--secondary">
            <div class="user-cell__stack">
              <span class="user-cell__secondary-label">{{ uiText.usernameSecondary }}</span>
              <span class="user-cell__secondary-text">{{ row.username }}</span>
            </div>
          </div>
        </template>

        <template #role="{ row }">
          <span class="user-role-chip" :class="`is-${row.role}`">
            {{ getRoleLabel(row.role) }}
          </span>
        </template>

        <template #is_active="{ row }">
          <span
            class="user-status-chip"
            :class="row.is_active ? 'is-active' : 'is-inactive'"
          >
            {{ row.is_active ? uiText.activeStatus : uiText.inactiveStatus }}
          </span>
        </template>

        <template #created_at="{ row }">
          <div class="date-cell">
            {{ formatDate(row.created_at).date }}<br>{{ formatDate(row.created_at).time }}
          </div>
        </template>

        <template #actions="{ row }">
          <button
            class="data-table__actions-btn"
            type="button"
            :title="t('common.actions.edit')"
            :aria-label="t('common.actions.edit')"
            @click="openEditUser(row)"
          >
            <Pencil :size="14" />
          </button>
        </template>
      </DataTable>

      <Pagination
        :total="totalCount"
        :page="currentPage"
        :page-size="pageSize"
        :page-sizes="[10, 20, 50]"
        @update:page="currentPage = $event"
        @update:page-size="pageSize = $event"
      />
    </div>

    <Modal
      :open="showCreateDialog"
      :title="t('userManagement.createTitle')"
      :description="uiText.createDialogDescription"
      width="min(620px, calc(100vw - 32px))"
      @close="closeCreateDialog"
    >
      <div class="upload-form form-grid-2 user-create-form">
        <label class="field field--compact">
          <span class="field__label">{{ uiText.columns.nickname }}</span>
          <input
            v-model.trim="newNickname"
            class="field__control"
            type="text"
            maxlength="50"
            autocomplete="off"
            :placeholder="uiText.nicknamePlaceholder"
            :aria-label="uiText.columns.nickname"
          />
        </label>

        <label class="field field--compact">
          <span class="field__label">{{ t('userManagement.username') }}</span>
          <input
            v-model.trim="newUsername"
            class="field__control"
            type="text"
            minlength="3"
            maxlength="50"
            autocomplete="off"
            :aria-label="t('userManagement.username')"
          />
        </label>

        <label class="field field--compact">
          <span class="field__label">{{ t('userManagement.password') }}</span>
          <input
            v-model="newPassword"
            class="field__control"
            type="password"
            minlength="6"
            maxlength="128"
            autocomplete="new-password"
            :aria-label="t('userManagement.password')"
          />
        </label>

        <label class="field field--compact">
          <span class="field__label">{{ t('userManagement.role') }}</span>
          <select v-model="newRole" class="field__control">
            <option value="user">{{ t('common.roles.user') }}</option>
            <option value="admin">{{ t('common.roles.admin') }}</option>
          </select>
        </label>
      </div>

      <p
        v-if="createUserMessage"
        class="form-message user-modal-message"
        :class="{ 'is-error': createUserMessageTone === 'error' }"
      >
        {{ createUserMessage }}
      </p>

      <template #footer>
        <button
          class="button"
          type="button"
          :disabled="creatingUser"
          @click="closeCreateDialog"
        >
          {{ t('common.actions.cancel') }}
        </button>
        <button
          class="button button--primary"
          type="button"
          :disabled="creatingUser || !canCreateUser"
          @click="createUser"
        >
          <Loader2 v-if="creatingUser" class="lucide-spin" :size="14" />
          <Plus v-else :size="14" />
          {{ creatingUser ? '创建中...' : t('userManagement.createUser') }}
        </button>
      </template>
    </Modal>

    <Modal
      :open="editDialogOpen"
      :title="uiText.editDialogTitle"
      :description="uiText.editDialogDescription"
      width="min(620px, calc(100vw - 32px))"
      @close="closeEditDialog"
    >
      <div class="user-edit-summary">
        <span class="user-edit-summary__label">{{ uiText.editTargetLabel }}</span>
        <strong class="user-edit-summary__value">{{ getUserDisplayName(editingUser) || '--' }}</strong>
        <span v-if="editingUser" class="user-edit-summary__meta">{{ editingUser.username }}</span>
      </div>

      <div class="upload-form form-grid-2 user-edit-form">
        <label class="field field--compact">
          <span class="field__label">{{ uiText.columns.nickname }}</span>
          <input
            v-model.trim="editNickname"
            class="field__control"
            type="text"
            maxlength="50"
            autocomplete="off"
            :placeholder="uiText.nicknamePlaceholder"
            :aria-label="uiText.columns.nickname"
          />
        </label>

        <label class="field field--compact">
          <span class="field__label">{{ t('userManagement.username') }}</span>
          <input
            v-model.trim="editUsername"
            class="field__control"
            type="text"
            minlength="3"
            maxlength="50"
            autocomplete="off"
            :aria-label="t('userManagement.username')"
          />
        </label>

        <label class="field field--compact user-edit-password-field">
          <span class="field__label">{{ uiText.editPasswordLabel }}</span>
          <input
            v-model="editPassword"
            class="field__control"
            type="password"
            minlength="6"
            maxlength="128"
            autocomplete="new-password"
            :placeholder="uiText.editPasswordPlaceholder"
            :aria-label="uiText.editPasswordLabel"
          />
        </label>
      </div>

      <p class="user-edit-hint">{{ uiText.editHint }}</p>
      <p
        v-if="editUserMessage"
        class="form-message"
        :class="{ 'is-error': editUserMessageTone === 'error' }"
      >
        {{ editUserMessage }}
      </p>

      <template #footer>
        <button
          class="button"
          type="button"
          :disabled="updatingUser"
          @click="closeEditDialog"
        >
          {{ t('common.actions.cancel') }}
        </button>
        <button
          class="button button--primary"
          type="button"
          :disabled="updatingUser || !canUpdateUser"
          @click="updateUser"
        >
          <Loader2 v-if="updatingUser" class="lucide-spin" :size="14" />
          <span v-else>{{ uiText.editSubmit }}</span>
        </button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.user-management-page {
  min-height: calc(100vh - 120px);
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
}

.user-page-header {
  align-items: center;
  gap: 16px;
  padding-bottom: 0;
}

.user-page-header > div:first-child {
  min-width: 0;
}

.user-page-header__description {
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}

.user-operator {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 260px;
  max-width: 360px;
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-muted);
}

.user-operator__copy {
  display: grid;
  gap: 1px;
  min-width: 0;
  flex: 1;
}

.user-operator__copy strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-operator__label {
  color: var(--text-muted);
  font-size: 11px;
}

.user-avatar {
  display: inline-grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 6px;
  background: var(--brand-050);
  color: var(--brand-700);
  font-size: 13px;
  font-weight: 700;
  flex: none;
}

.user-avatar--cell {
  width: 28px;
  height: 28px;
  background: #f5f7fa;
  color: var(--text-secondary);
  font-size: 12px;
}

.user-overview {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  padding: 14px 20px 4px;
}

.user-overview-item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: #fbfdfc;
}

.user-overview-item__icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 6px;
  background: var(--brand-050);
  color: var(--brand-700);
  flex: none;
}

.user-overview-item__icon--admin {
  background: var(--brand-050);
  color: var(--brand-700);
}

.user-overview-item__icon--user {
  background: #f4f5f7;
  color: var(--text-secondary);
}

.user-overview-item__icon--active {
  background: var(--state-success-bg);
  color: var(--state-success);
}

.user-overview-item__copy {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.user-overview-item__copy span {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-overview-item__copy strong {
  color: var(--text-primary);
  font-size: 22px;
  line-height: 1.1;
}

.user-table-toolbar {
  padding: 14px 20px 10px;
}

.user-toolbar__filters {
  flex-wrap: wrap;
}

.user-toolbar__actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.user-search {
  min-width: min(100%, 300px);
}

.user-filter {
  height: 32px;
  width: 160px;
  min-width: 160px;
  padding: 0 28px 0 10px;
  border: 1px solid #dde1e6;
  border-radius: 4px;
  background: #f5f7fa;
  color: var(--text-primary);
  font-size: 13px;
  transition:
    border-color var(--motion-base) var(--ease-standard),
    background var(--motion-base) var(--ease-standard),
    box-shadow var(--motion-base) var(--ease-standard);
}

.user-filter:focus {
  outline: none;
  border-color: var(--brand-700);
  background: #fff;
  box-shadow: var(--focus-ring);
}

.user-permission-note {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 20px 12px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--brand-050);
  color: var(--text-secondary);
  font-size: 13px;
}

.user-permission-note .lucide {
  color: var(--brand-700);
  flex: none;
}

.user-panel-message {
  margin: 0 20px 12px;
}

.user-table-shell {
  min-width: 0;
  padding-top: 0;
  overflow: hidden;
}

.user-table-shell :deep(.data-table-wrapper) {
  min-width: 0;
  max-width: 100%;
}

.user-identity-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.user-cell__main {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.user-cell__stack {
  display: grid;
  gap: 2px;
}

.user-cell__name {
  color: var(--text-primary);
  font-weight: 600;
}

.user-cell--secondary {
  color: var(--text-secondary);
}

.user-cell__secondary-label {
  color: var(--text-muted);
  font-size: 11px;
}

.user-cell__secondary-text {
  color: var(--text-secondary);
  font-size: 13px;
}

.user-badge-pill,
.user-role-chip,
.user-status-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}

.user-badge-pill--self {
  border: 1px solid color-mix(in srgb, var(--brand-700) 20%, transparent);
  background: var(--brand-050);
  color: var(--brand-700);
}

.user-role-chip.is-admin {
  background: var(--brand-050);
  color: var(--brand-700);
}

.user-role-chip.is-user {
  background: #f4f5f7;
  color: var(--text-secondary);
}

.user-status-chip.is-active {
  background: var(--state-success-bg);
  color: var(--state-success);
}

.user-status-chip.is-inactive {
  background: var(--state-danger-bg);
  color: var(--state-danger);
}

.user-create-form,
.user-edit-form {
  margin-top: 0;
}

.user-modal-message {
  margin-top: 12px;
}

.user-edit-summary {
  display: grid;
  gap: 4px;
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-muted);
}

.user-edit-summary__label {
  color: var(--text-muted);
  font-size: 12px;
}

.user-edit-summary__value {
  color: var(--text-primary);
  font-size: 18px;
}

.user-edit-summary__meta {
  color: var(--text-secondary);
  font-size: 13px;
}

.user-edit-password-field {
  grid-column: 1 / -1;
}

.user-edit-hint {
  margin: 12px 0 0;
  color: var(--text-muted);
  font-size: 13px;
}

@media (max-width: 1100px) {
  .user-page-header {
    align-items: stretch;
    flex-direction: column;
  }

  .user-operator {
    max-width: none;
    width: 100%;
  }

  .user-overview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .user-overview {
    grid-template-columns: 1fr;
    padding-inline: 16px;
  }

  .user-table-toolbar {
    flex-direction: column;
    padding-inline: 16px;
  }

  .user-toolbar__filters,
  .user-toolbar__actions {
    align-items: stretch;
  }

  .user-toolbar__filters {
    width: 100%;
    flex-direction: column;
  }

  .user-toolbar__actions {
    width: 100%;
  }

  .user-toolbar__actions .button {
    flex: 1;
  }

  .user-search,
  .user-filter {
    width: 100%;
    min-width: 0;
  }

  .user-edit-password-field {
    grid-column: auto;
  }

  .user-table-shell :deep(.data-table) {
    min-width: 760px;
  }

  .user-table-shell :deep(.pagination) {
    flex-wrap: wrap;
    align-items: center;
  }
}
</style>
