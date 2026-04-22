<script setup lang="ts">
import {
  Bell,
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
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterView, useRoute, useRouter } from 'vue-router'

import PageHeader from '../components/base/PageHeader.vue'
import { getLocaleLabel } from '../constants/languages'
import { useAuthStore } from '../stores/auth'
import { usePreferencesStore } from '../stores/preferences'
import { useShellStore } from '../stores/shell'

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

const expandedGroups = reactive<Record<string, boolean>>({
  assets: true,
  system: true,
})

const navGroups = computed<NavGroup[]>(() => [
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
    visible: authStore.isAdmin,
    children: [
      {
        name: 'tm',
        label: t('shell.sections.tm'),
        icon: Database,
        visible: authStore.isAdmin,
      },
      {
        name: 'term-base',
        label: t('shell.sections.termBase'),
        icon: BookOpen,
        visible: authStore.isAdmin,
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
const pageDescription = computed(() => {
  if (shellStore.pageContext.description) {
    return String(shellStore.pageContext.description)
  }
  if (typeof route.meta.pageDescriptionKey === 'string') {
    return t(route.meta.pageDescriptionKey)
  }
  return String(route.meta.pageDescription || '')
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
    case 'projects':
      return [{ label: t('shell.sections.workspace') }]
    case 'project-detail':
      return [
        { label: t('shell.sections.workspace'), to: { name: 'projects' } },
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
    case 'tm-edit':
      return [
        { label: t('shell.sections.assets'), to: { name: 'tm' } },
        { label: pageTitle.value },
      ]
    case 'term-base':
    case 'term-base-edit':
      return [
        { label: t('shell.sections.assets'), to: { name: 'term-base' } },
        { label: pageTitle.value },
      ]
    case 'users':
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

async function logout() {
  authStore.logout()
  await router.push({ name: 'login' })
}

function getUserDisplayName() {
  return authStore.user?.nickname || authStore.user?.username || ''
}

function getUserInitial() {
  const name = getUserDisplayName()
  return name.charAt(0).toUpperCase()
}

function getRecentIcon(section: string) {
  if (section === 'projects') {
    return Briefcase
  }
  if (section === 'tasks') {
    return ClipboardList
  }
  if (section === 'tm' || section === 'term-base') {
    return BookOpen
  }
  return FileText
}

watch(() => route.meta.navSection, (section) => {
  if (section === 'tm' || section === 'term-base') {
    expandedGroups.assets = true
  }
  if (section === 'users') {
    expandedGroups.system = true
  }
})

watch(
  () => [route.name, route.params.id, pageTitle.value] as const,
  ([name, id, title]) => {
    if (!name || !title) {
      return
    }

    if (!['project-detail', 'workbench', 'tm-edit', 'term-base-edit'].includes(String(name))) {
      return
    }

    shellStore.trackRecent({
      id: `${String(name)}:${String(id || '')}`,
      label: title,
      section: String(route.meta.navSection || 'projects'),
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
                  type="button"
                  @click="toggleGroup(group.key)"
                >
                  <component :is="group.icon" :size="18" />
                  <span>{{ group.label }}</span>
                  <ChevronRight
                    :size="14"
                    class="sidebar-nav__group-arrow"
                    :class="{ 'is-open': expandedGroups[group.key] }"
                  />
                </button>
                <div v-if="expandedGroups[group.key]" class="sidebar-nav__group-children">
                  <button
                    v-for="child in group.children"
                    v-show="child.visible"
                    :key="child.name"
                    class="sidebar-nav__item"
                    :class="{ 'is-active': isNavActive(child.name) }"
                    type="button"
                    :title="child.label"
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
              </div>
            </template>

            <template v-else>
              <button
                class="sidebar-nav__item"
                :class="{ 'is-active': isGroupActive(group) }"
                type="button"
                :title="group.label"
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
          <span>{{ authStore.user?.role === 'admin' ? t('common.roles.admin') : t('common.roles.user') }}</span>
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

          <button
            class="shell-header__placeholder"
            type="button"
            :title="t('common.comingSoon')"
            :aria-label="t('shell.topbar.soon', { name: t('shell.topbar.notifications') })"
            disabled
          >
            <Bell :size="16" />
            <span>{{ t('shell.topbar.notifications') }}</span>
          </button>

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

          <button
            class="shell-header__placeholder"
            type="button"
            :title="t('common.comingSoon')"
            :aria-label="t('shell.topbar.soon', { name: t('shell.topbar.language') })"
            disabled
          >
            <Globe :size="16" />
            <span>{{ currentLocale }}</span>
          </button>

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
        <PageHeader
          :title="pageTitle"
          :description="pageDescription"
          :breadcrumbs="breadcrumbs"
        />
        <RouterView />
      </section>
    </div>
  </div>
</template>
