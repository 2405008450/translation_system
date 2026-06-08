<script setup lang="ts">
import {
  Bell,
  BarChart3,
  BookOpen,
  Briefcase,
  ChevronRight,
  ClipboardList,
  Database,
  FileText,
  Globe,
  LogOut,
  Moon,
  PanelLeft,
  PanelLeftClose,
  Settings,
  Sun,
  Users,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { getLocaleLabel } from '../constants/languages'
import { http } from '../api/http'
import { useAuthStore } from '../stores/auth'
import { usePreferencesStore } from '../stores/preferences'
import { useShellStore } from '../stores/shell'
import type { NotificationItem, NotificationsResponse } from '../types/api'

interface NavChild {
  name: string
  label: string
  icon: any
  visible: boolean
}

interface NavGroup {
  key: string
  label: string
  icon: any
  children?: NavChild[]
  routeName?: string
  visible: boolean
}

const authStore = useAuthStore()
const preferencesStore = usePreferencesStore()
const shellStore = useShellStore()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const SIDEBAR_STORAGE_KEY = 'tm-workbench-sidebar-collapsed'

const sidebarCollapsed = ref(
  typeof window !== 'undefined' && window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1',
)
const notificationsOpen = ref(false)
const languageMenuOpen = ref(false)
const notificationsLoading = ref(false)
const notifications = ref<NotificationItem[]>([])
const unreadNotificationCount = ref(0)

const expandedGroups = reactive<Record<string, boolean>>({
  assets: true,
  system: true,
})

const navGroups = computed<NavGroup[]>(() => [
  {
    key: 'dashboard',
    label: t('shell.sections.dashboard'),
    icon: BarChart3,
    routeName: 'dashboard',
    visible: !authStore.isExternalTranslator,
  },
  {
    key: 'workspace',
    label: t('shell.sections.workspace'),
    icon: Briefcase,
    routeName: 'projects',
    visible: true,
  },
  {
    key: 'mytasks',
    label: t('shell.sections.tasks'),
    icon: ClipboardList,
    routeName: 'tasks',
    visible: true,
  },
  {
    key: 'assets',
    label: t('shell.sections.assets'),
    icon: BookOpen,
    visible: true,
    children: [
      {
        name: 'tm',
        label: t('shell.sections.tm'),
        icon: Database,
        visible: true,
      },
      {
        name: 'term-base',
        label: t('shell.sections.termBase'),
        icon: BookOpen,
        visible: true,
      },
      {
        name: 'glossary',
        label: t('shell.sections.glossary'),
        icon: FileText,
        visible: true,
      },
      {
        name: 'translation-rules',
        label: t('shell.sections.translationRules'),
        icon: FileText,
        visible: true,
      },
    ],
  },
  {
    key: 'system',
    label: t('shell.sections.system'),
    icon: Settings,
    visible: authStore.isAdmin,
    children: [
      {
        name: 'assignment-events',
        label: t('shell.sections.assignmentEvents'),
        icon: ClipboardList,
        visible: authStore.isAdmin,
      },
      {
        name: 'users',
        label: t('shell.sections.users'),
        icon: Users,
        visible: authStore.isAdmin,
      },
    ],
  },
])

const recentItems = computed(() => shellStore.recentItems)
const pageTitle = computed(() => {
  if (shellStore.pageContext.title) {
    return String(shellStore.pageContext.title)
  }
  if (typeof route.meta.pageTitleKey === 'string') {
    return t(route.meta.pageTitleKey)
  }
  return String(route.meta.pageTitle || t('pages.workbench.title'))
})
const currentSection = computed(() => String(route.meta.navSection || 'tasks'))
const currentLocale = computed(() => getLocaleLabel(preferencesStore.locale))
const isDarkTheme = computed(() => preferencesStore.theme === 'dark')
const currentThemeLabel = computed(() => (
  isDarkTheme.value ? t('shell.topbar.themeDark') : t('shell.topbar.themeLight')
))
const nextThemeLabel = computed(() => (
  isDarkTheme.value ? t('shell.topbar.themeLight') : t('shell.topbar.themeDark')
))

const breadcrumbs = computed(() => {
  if (shellStore.pageContext.breadcrumbs?.length) {
    return shellStore.pageContext.breadcrumbs
  }

  switch (route.name) {
    case 'dashboard':
      return [{ label: t('shell.sections.dashboard') }]
    case 'projects':
      return [{ label: t('shell.sections.workspace') }]
    case 'project-detail':
      return [
        {
          label: authStore.isExternalTranslator ? t('shell.sections.tasks') : t('shell.sections.workspace'),
          to: { name: authStore.isExternalTranslator ? 'tasks' : 'projects' },
        },
        { label: pageTitle.value },
      ]
    case 'tasks':
      return [{ label: t('shell.sections.tasks') }]
    case 'workbench':
      return [
        { label: t('shell.sections.tasks'), to: { name: 'tasks' } },
        { label: pageTitle.value },
      ]
    case 'tm':
      return [
        { label: t('shell.sections.assets') },
        { label: t('shell.sections.tm') },
      ]
    case 'tm-edit':
      return [
        { label: t('shell.sections.assets') },
        { label: t('shell.sections.tm'), to: { name: 'tm' } },
        { label: pageTitle.value },
      ]
    case 'term-base':
      return [
        { label: t('shell.sections.assets') },
        { label: t('shell.sections.termBase') },
      ]
    case 'glossary':
      return [
        { label: t('shell.sections.assets') },
        { label: t('shell.sections.glossary') },
      ]
    case 'glossary-edit':
      return [
        { label: t('shell.sections.assets') },
        { label: t('shell.sections.glossary'), to: { name: 'glossary' } },
        { label: pageTitle.value },
      ]
    case 'translation-rules':
      return [
        { label: t('shell.sections.assets') },
        { label: t('shell.sections.translationRules') },
      ]
    case 'term-base-edit':
      return [
        { label: t('shell.sections.assets') },
        { label: t('shell.sections.termBase'), to: { name: 'term-base' } },
        { label: pageTitle.value },
      ]
    case 'users':
      return [
        { label: t('shell.sections.system') },
        { label: pageTitle.value },
      ]
    case 'assignment-events':
      return [
        { label: t('shell.sections.system') },
        { label: pageTitle.value },
      ]
    default:
      return [{ label: pageTitle.value }]
  }
})

function isNavActive(name: string) {
  return currentSection.value === name
}

function isGroupActive(group: NavGroup) {
  if (group.routeName) {
    return currentSection.value === group.routeName || currentSection.value === group.key
  }
  return group.children?.some((child) => isNavActive(child.name)) ?? false
}

function toggleGroup(key: string) {
  expandedGroups[key] = !expandedGroups[key]
}

function formatNotificationDate(value: string) {
  const date = new Date(value)
  return date.toLocaleString(preferencesStore.locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

async function loadNotifications() {
  if (!authStore.isAuthenticated) {
    return
  }
  notificationsLoading.value = true
  try {
    const { data } = await http.get<NotificationsResponse>('/notifications', {
      params: { limit: 20 },
    })
    notifications.value = data.items
    unreadNotificationCount.value = data.unread_count
  } catch {
    notifications.value = []
    unreadNotificationCount.value = 0
  } finally {
    notificationsLoading.value = false
  }
}

async function markNotificationRead(notification: NotificationItem) {
  if (!notification.read_at) {
    await http.patch(`/notifications/${notification.id}/read`)
    notification.read_at = new Date().toISOString()
    unreadNotificationCount.value = Math.max(0, unreadNotificationCount.value - 1)
  }
}

async function markAllNotificationsRead() {
  await http.patch('/notifications/read-all')
  const now = new Date().toISOString()
  notifications.value = notifications.value.map((item) => ({
    ...item,
    read_at: item.read_at || now,
  }))
  unreadNotificationCount.value = 0
}

async function openNotification(notification: NotificationItem) {
  await markNotificationRead(notification)
  notificationsOpen.value = false
  if (notification.file_record_id) {
    await router.push({ name: 'workbench', params: { id: notification.file_record_id } })
    return
  }
  if (notification.project_id) {
    await router.push({ name: 'project-detail', params: { id: notification.project_id } })
  }
}

function toggleNotifications() {
  notificationsOpen.value = !notificationsOpen.value
  languageMenuOpen.value = false
  if (notificationsOpen.value) {
    void loadNotifications()
  }
}

function toggleLanguageMenu() {
  languageMenuOpen.value = !languageMenuOpen.value
  notificationsOpen.value = false
}

function closeNotificationsOnOutsideClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null
  if (!target?.closest?.('.shell-notifications')) {
    notificationsOpen.value = false
  }
  if (!target?.closest?.('.shell-language')) {
    languageMenuOpen.value = false
  }
}

function getFirstVisibleChildName(group: NavGroup) {
  return group.children?.find((child) => child.visible)?.name || ''
}

function handleGroupClick(group: NavGroup) {
  if (sidebarCollapsed.value) {
    const targetName = getFirstVisibleChildName(group)
    if (targetName) {
      navigateTo(targetName)
    }
    return
  }

  toggleGroup(group.key)
}

function navigateTo(name: string) {
  void router.push({ name })
}

function openRecentItem(item: (typeof recentItems.value)[number]) {
  void router.push(item.route)
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, sidebarCollapsed.value ? '1' : '0')
  }
}

function toggleTheme() {
  preferencesStore.setTheme(preferencesStore.theme === 'dark' ? 'light' : 'dark')
}

function setLocale(locale: 'zh-CN' | 'en-US') {
  preferencesStore.setLocale(locale)
  languageMenuOpen.value = false
}

async function logout() {
  authStore.logout()
  notifications.value = []
  unreadNotificationCount.value = 0
  await router.push({ name: 'login' })
}

function getUserDisplayName() {
  return authStore.user?.nickname || authStore.user?.username || ''
}

function getUserInitial() {
  const name = getUserDisplayName()
  return name.charAt(0).toUpperCase()
}

function getRoleLabel(role?: string | null) {
  if (role === 'super_admin') {
    return t('common.roles.superAdmin')
  }
  return role === 'admin' ? t('common.roles.admin') : t('common.roles.user')
}

function getRecentIcon(section: string) {
  if (section === 'projects') {
    return Briefcase
  }
  if (section === 'tasks') {
    return ClipboardList
  }
  if (section === 'tm' || section === 'term-base' || section === 'glossary') {
    return BookOpen
  }
  if (section === 'translation-rules') {
    return FileText
  }
  return FileText
}

watch(() => route.meta.navSection, (section) => {
  if (section === 'tm' || section === 'term-base' || section === 'glossary' || section === 'translation-rules') {
    expandedGroups.assets = true
  }
  if (section === 'users' || section === 'assignment-events') {
    expandedGroups.system = true
  }
})

watch(
  () => authStore.isAuthenticated,
  (isAuthenticated) => {
    if (isAuthenticated) {
      void loadNotifications()
    } else {
      notifications.value = []
      unreadNotificationCount.value = 0
    }
  },
  { immediate: true },
)

onMounted(() => {
  document.addEventListener('click', closeNotificationsOnOutsideClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', closeNotificationsOnOutsideClick)
})

watch(
  () => [route.name, route.params.id, pageTitle.value] as const,
  ([name, id, title]) => {
    if (!name || !title) {
      return
    }

    if (!['project-detail', 'workbench', 'tm-edit', 'term-base-edit', 'glossary-edit'].includes(String(name))) {
      return
    }

    shellStore.trackRecent({
      id: `${String(name)}:${String(id || '')}`,
      label: title,
      section: authStore.isExternalTranslator && route.meta.navSection === 'projects'
        ? 'tasks'
        : String(route.meta.navSection || 'projects'),
      route: {
        name: String(name),
        params: Object.fromEntries(
          Object.entries(route.params).map(([key, value]) => [key, String(value)]),
        ),
        query: Object.fromEntries(
          Object.entries(route.query).map(([key, value]) => [key, String(value)]),
        ),
      },
    })
  },
  { immediate: true },
)
</script>

<template>
  <div class="app-frame" :class="{ 'is-sidebar-collapsed': sidebarCollapsed }">
    <aside class="app-sidebar" :class="{ 'is-collapsed': sidebarCollapsed }">
      <div class="sidebar-brand">
        <div class="sidebar-brand__top">
          <div class="sidebar-brand__identity sidebar-brand__identity--centered">
            <div class="brand-logo brand-logo--plain" :class="{ 'is-collapsed': sidebarCollapsed }">
              <div v-if="sidebarCollapsed" class="brand-logo__wordmark">{{ t('appLayout.collapsedBrand') }}</div>
            </div>
            <div v-if="!sidebarCollapsed" class="brand-text brand-text--centered">
              <h1 class="brand-text__title">{{ t('appLayout.brand') }}</h1>
            </div>
          </div>
        </div>
      </div>

      <nav class="sidebar-nav" :aria-label="t('shell.mainNav')">
        <template v-for="group in navGroups" :key="group.key">
          <template v-if="group.visible">
            <template v-if="group.children && group.children.length > 0">
              <div class="sidebar-nav__group">
                <button
                  class="sidebar-nav__group-header"
                  :class="{ 'is-active': isGroupActive(group), 'has-flyout': sidebarCollapsed }"
                  type="button"
                  :title="group.label"
                  :data-label="group.label"
                  :aria-expanded="expandedGroups[group.key]"
                  @click="handleGroupClick(group)"
                >
                  <component :is="group.icon" :size="18" />
                  <span>{{ group.label }}</span>
                  <ChevronRight
                    :size="14"
                    class="sidebar-nav__group-arrow"
                    :class="{ 'is-open': expandedGroups[group.key] }"
                  />
                </button>
                <div v-if="!sidebarCollapsed && expandedGroups[group.key]" class="sidebar-nav__group-children">
                  <button
                    v-for="child in group.children"
                    v-show="child.visible"
                    :key="child.name"
                    class="sidebar-nav__item"
                    :class="{ 'is-active': isNavActive(child.name) }"
                    type="button"
                    :title="child.label"
                    :data-label="child.label"
                    @click="navigateTo(child.name)"
                  >
                    <span class="sidebar-nav__mark">
                      <component :is="child.icon" :size="16" />
                    </span>
                    <div v-if="!sidebarCollapsed" class="sidebar-nav__text">
                      <strong>{{ child.label }}</strong>
                    </div>
                  </button>
                </div>
                <div v-else-if="sidebarCollapsed" class="sidebar-nav__flyout" role="menu">
                  <div class="sidebar-nav__flyout-title">{{ group.label }}</div>
                  <button
                    v-for="child in group.children"
                    v-show="child.visible"
                    :key="child.name"
                    class="sidebar-nav__flyout-item"
                    :class="{ 'is-active': isNavActive(child.name) }"
                    type="button"
                    role="menuitem"
                    @click="navigateTo(child.name)"
                  >
                    <component :is="child.icon" :size="15" />
                    <span>{{ child.label }}</span>
                  </button>
                </div>
              </div>
            </template>

            <template v-else>
              <button
                class="sidebar-nav__item"
                :class="{ 'is-active': isGroupActive(group) }"
                type="button"
                :title="group.label"
                :data-label="group.label"
                @click="group.routeName && navigateTo(group.routeName)"
              >
                <span class="sidebar-nav__mark">
                  <component :is="group.icon" :size="18" />
                </span>
                <div v-if="!sidebarCollapsed" class="sidebar-nav__text">
                  <strong>{{ group.label }}</strong>
                </div>
              </button>
            </template>
          </template>
        </template>
      </nav>

      <div v-if="!sidebarCollapsed" class="sidebar-recent">
        <div class="sidebar-recent__title">{{ t('shell.recent.title') }}</div>
        <div class="sidebar-recent__list">
          <button
            v-for="item in recentItems"
            :key="item.id"
            class="sidebar-recent__item"
            type="button"
            :title="item.label"
            @click="openRecentItem(item)"
          >
            <component :is="getRecentIcon(item.section)" :size="14" />
            {{ item.label }}
          </button>
          <div v-if="recentItems.length === 0" class="sidebar-recent__empty">
            {{ t('shell.recent.empty') }}
          </div>
        </div>
      </div>

      <div class="sidebar-footer">
        <div class="sidebar-user-avatar">{{ getUserInitial() }}</div>
        <div v-if="!sidebarCollapsed" class="sidebar-user-info">
          <strong>{{ getUserDisplayName() || authStore.user?.username }}</strong>
          <span>{{ getRoleLabel(authStore.user?.role) }}</span>
        </div>
        <button
          v-if="!sidebarCollapsed"
          class="button"
          type="button"
          :title="t('shell.topbar.logout')"
          :aria-label="t('shell.topbar.logout')"
          style="margin-left: auto;"
          @click="logout"
        >
          <LogOut :size="14" />
        </button>
        <button
          v-else
          class="button"
          type="button"
          :title="t('shell.topbar.logout')"
          :aria-label="t('shell.topbar.logout')"
          @click="logout"
        >
          <LogOut :size="14" />
        </button>
      </div>
    </aside>

    <div class="app-main">
      <header class="shell-header">
        <div class="shell-header__content">
          <button
            class="button sidebar-toggle"
            type="button"
            :title="sidebarCollapsed ? t('shell.topbar.expand') : t('shell.topbar.collapse')"
            :aria-label="sidebarCollapsed ? t('shell.topbar.expand') : t('shell.topbar.collapse')"
            @click="toggleSidebar"
          >
            <component :is="sidebarCollapsed ? PanelLeft : PanelLeftClose" :size="20" />
          </button>
          <nav v-if="breadcrumbs.length" class="shell-breadcrumb breadcrumb" :aria-label="t('shell.topbar.globalBreadcrumb')">
            <template v-for="(item, index) in breadcrumbs" :key="`${item.label}-${index}`">
              <RouterLink
                v-if="item.to"
                class="breadcrumb__item is-link"
                :to="item.to"
              >
                {{ item.label }}
              </RouterLink>
              <span v-else class="breadcrumb__item is-current" :aria-current="index === breadcrumbs.length - 1 ? 'page' : undefined">
                {{ item.label }}
              </span>
              <ChevronRight v-if="index < breadcrumbs.length - 1" :size="14" class="breadcrumb__sep" />
            </template>
          </nav>
        </div>
        <div class="shell-header__actions">
          <button
            class="shell-header__placeholder"
            type="button"
            :title="t('shell.topbar.switchTheme', { name: nextThemeLabel })"
            :aria-label="t('shell.topbar.switchTheme', { name: nextThemeLabel })"
            @click="toggleTheme"
          >
            <component :is="isDarkTheme ? Sun : Moon" :size="16" />
            <span>{{ currentThemeLabel }}</span>
          </button>

          <div class="shell-notifications" @click.stop>
            <button
              class="shell-header__placeholder shell-notifications__trigger"
              type="button"
              :aria-expanded="notificationsOpen"
              :aria-label="t('shell.topbar.notifications')"
              @click="toggleNotifications"
            >
              <Bell :size="16" />
              <span>{{ t('shell.topbar.notifications') }}</span>
              <span v-if="unreadNotificationCount > 0" class="shell-notifications__badge">
                {{ unreadNotificationCount > 99 ? '99+' : unreadNotificationCount }}
              </span>
            </button>
            <div v-if="notificationsOpen" class="shell-notifications__panel">
              <div class="shell-notifications__head">
                <strong>{{ t('shell.topbar.notifications') }}</strong>
                <button
                  class="shell-notifications__link"
                  type="button"
                  :disabled="unreadNotificationCount === 0"
                  @click="markAllNotificationsRead"
                >
                  {{ t('shell.topbar.markAllRead') }}
                </button>
              </div>
              <div v-if="notificationsLoading" class="shell-notifications__empty">{{ t('common.loading') }}</div>
              <div v-else-if="notifications.length === 0" class="shell-notifications__empty">{{ t('shell.topbar.noNotifications') }}</div>
              <template v-else>
                <button
                  v-for="notification in notifications"
                  :key="notification.id"
                  class="shell-notification"
                  :class="{ 'is-unread': !notification.read_at }"
                  type="button"
                  @click="openNotification(notification)"
                >
                  <span class="shell-notification__title">{{ notification.title }}</span>
                  <span class="shell-notification__body">{{ notification.body }}</span>
                  <span class="shell-notification__time">{{ formatNotificationDate(notification.created_at) }}</span>
                </button>
              </template>
            </div>
          </div>

          <button
            class="shell-header__placeholder"
            type="button"
            :title="t('common.comingSoon')"
            :aria-label="t('shell.topbar.soon', { name: t('shell.topbar.settings') })"
            disabled
          >
            <Settings :size="16" />
            <span>{{ t('shell.topbar.settings') }}</span>
          </button>

          <div class="shell-language" @click.stop>
            <button
              class="shell-header__placeholder"
              type="button"
              :title="t('shell.topbar.language')"
              :aria-label="t('shell.topbar.language')"
              :aria-expanded="languageMenuOpen"
              @click="toggleLanguageMenu"
            >
              <Globe :size="16" />
              <span>{{ currentLocale }}</span>
            </button>
            <div v-if="languageMenuOpen" class="shell-language__menu" role="menu">
              <button
                class="shell-language__item"
                :class="{ 'is-active': preferencesStore.locale === 'zh-CN' }"
                type="button"
                role="menuitemradio"
                :aria-checked="preferencesStore.locale === 'zh-CN'"
                @click="setLocale('zh-CN')"
              >
                中文
              </button>
              <button
                class="shell-language__item"
                :class="{ 'is-active': preferencesStore.locale === 'en-US' }"
                type="button"
                role="menuitemradio"
                :aria-checked="preferencesStore.locale === 'en-US'"
                @click="setLocale('en-US')"
              >
                English
              </button>
            </div>
          </div>

          <div class="shell-header__user-display">
            <div class="shell-header__user-avatar">{{ getUserInitial() }}</div>
            <span class="shell-header__user-name">{{ getUserDisplayName() || t('shell.userFallback') }}</span>
          </div>

          <button
            class="shell-header__logout"
            type="button"
            :title="t('shell.topbar.logout')"
            :aria-label="t('shell.topbar.logout')"
            @click="logout"
          >
            <LogOut :size="16" />
          </button>
        </div>
      </header>

      <section class="shell-body">
        <RouterView />
      </section>
    </div>
  </div>
</template>

<style scoped>
.shell-notifications {
  position: relative;
}

.shell-language {
  position: relative;
}

.shell-language__menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 80;
  min-width: 136px;
  overflow: hidden;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: var(--shadow-soft);
}

.shell-language__item {
  width: 100%;
  min-height: 36px;
  justify-content: flex-start;
  padding: 0 12px;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--text-secondary);
  box-shadow: none;
  text-align: left;
}

.shell-language__item:hover,
.shell-language__item.is-active {
  background: var(--surface-muted);
  color: var(--text-primary);
}

.shell-notifications__trigger {
  position: relative;
}

.shell-notifications__badge {
  position: absolute;
  top: -6px;
  right: -6px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border: 2px solid var(--surface-panel);
  border-radius: 999px;
  background: var(--state-danger);
  color: #fff;
  font-size: 11px;
  line-height: 14px;
  text-align: center;
}

.shell-notifications__panel {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 80;
  width: min(360px, calc(100vw - 32px));
  max-height: min(520px, calc(100vh - 96px));
  overflow: auto;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
  box-shadow: var(--shadow-soft);
}

.shell-notifications__head {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line-soft);
  background: var(--surface-panel);
}

.shell-notifications__head strong {
  color: var(--text-primary);
  font-size: 14px;
}

.shell-notifications__link {
  min-height: 28px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--brand-700);
  font-size: 12px;
  box-shadow: none;
}

.shell-notifications__link:disabled {
  color: var(--text-muted);
  cursor: not-allowed;
}

.shell-notifications__empty {
  padding: 36px 14px;
  color: var(--text-muted);
  font-size: 13px;
  text-align: center;
}

.shell-notification {
  width: 100%;
  display: grid;
  gap: 4px;
  padding: 12px 14px;
  border: 0;
  border-bottom: 1px solid var(--line-soft);
  border-radius: 0;
  background: transparent;
  color: var(--text-secondary);
  text-align: left;
  box-shadow: none;
}

.shell-notification:hover {
  background: var(--surface-muted);
}

.shell-notification:last-child {
  border-bottom: 0;
}

.shell-notification.is-unread {
  background: color-mix(in srgb, var(--brand-050) 70%, var(--surface-panel));
}

.shell-notification__title {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.35;
}

.shell-notification__body {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.45;
}

.shell-notification__time {
  color: var(--text-muted);
  font-size: 11px;
}
</style>
