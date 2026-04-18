<script setup lang="ts">
import { FolderKanban, Database, Users, LogOut, PanelLeftClose, PanelLeft, Languages } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

interface NavItem {
  name: string
  label: string
  icon: any
  shortLabel: string
  description: string
  visible: boolean
}

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()
const SIDEBAR_STORAGE_KEY = 'tm-workbench-sidebar-collapsed'

const sidebarCollapsed = ref(
  typeof window !== 'undefined' && window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1',
)

const navItems = computed<NavItem[]>(() => [
  {
    name: 'tasks',
    label: '任务管理',
    icon: FolderKanban,
    shortLabel: '任',
    description: '上传与翻译任务',
    visible: true,
  },
  {
    name: 'tm',
    label: 'TM 记忆库',
    icon: Database,
    shortLabel: 'TM',
    description: '双语记忆与导入',
    visible: authStore.isAdmin,
  },
  {
    name: 'termbase',
    label: '术语库',
    shortLabel: 'TB',
    description: '术语管理与匹配',
    visible: authStore.isAdmin,
  },
  {
    name: 'users',
    label: '用户管理',
    icon: Users,
    shortLabel: '用',
    description: '账号和角色',
    visible: authStore.isAdmin,
  },
])

const pageTitle = computed(() => String(route.meta.pageTitle || '翻译工作台'))
const pageDescription = computed(() => String(route.meta.pageDescription || ''))
const currentSection = computed(() => String(route.meta.navSection || 'tasks'))
const breadcrumbItems = computed(() => {
  const items: Array<{ label: string; routeName?: string }> = []
  const currentNav = navItems.value.find((item) => item.name === currentSection.value)

  if (currentNav) {
    items.push({ label: currentNav.label, routeName: currentNav.name })
  }

  if (!currentNav || pageTitle.value !== currentNav.label) {
    items.push({ label: pageTitle.value })
  }

  return items
})

function isNavActive(name: string) {
  return currentSection.value === name
}

function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, sidebarCollapsed.value ? '1' : '0')
  }
}

async function logout() {
  authStore.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <div class="app-frame" :class="{ 'is-sidebar-collapsed': sidebarCollapsed }">
    <aside class="app-sidebar" :class="{ 'is-collapsed': sidebarCollapsed }">
      <div class="sidebar-brand">
        <div class="sidebar-brand__top">
          <div class="sidebar-brand__identity">
            <div class="brand-logo" :class="{ 'is-collapsed': sidebarCollapsed }">
              <Languages :size="sidebarCollapsed ? 28 : 24" stroke-width="2.5" />
            </div>
            <div v-if="!sidebarCollapsed" class="brand-text">
              <div class="section-kicker">协同翻译</div>
              <h1>翻译工作台</h1>
            </div>
          </div>
          <button
            class="button sidebar-toggle"
            type="button"
            :title="sidebarCollapsed ? '展开导航栏' : '收起导航栏'"
            :aria-label="sidebarCollapsed ? '展开导航栏' : '收起导航栏'"
            @click="toggleSidebar"
          >
            <component :is="sidebarCollapsed ? PanelLeft : PanelLeftClose" :size="20" />
          </button>
        </div>
        <p v-if="!sidebarCollapsed">按板块管理任务、TM 和记忆库用户</p>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <button
          v-for="item in navItems"
          v-show="item.visible"
          :key="item.name"
          class="sidebar-nav__item"
          :class="{ 'is-active': isNavActive(item.name) }"
          type="button"
          :title="item.label"
          @click="router.push({ name: item.name })"
        >
          <span class="sidebar-nav__mark">
            <component :is="item.icon" :size="20" />
          </span>
          <div v-if="!sidebarCollapsed" class="sidebar-nav__text">
            <strong>{{ item.label }}</strong>
            <span>{{ item.description }}</span>
          </div>
        </button>
      </nav>

    </aside>

    <div class="app-main">
      <header class="shell-header">
        <div class="shell-header__content">
          <nav class="breadcrumb" aria-label="面包屑">
            <template v-for="(item, index) in breadcrumbItems" :key="`${item.label}-${index}`">
              <button
                v-if="item.routeName && index < breadcrumbItems.length - 1"
                class="breadcrumb__item is-link"
                type="button"
                @click="router.push({ name: item.routeName })"
              >
                {{ item.label }}
              </button>
              <span v-else class="breadcrumb__item" :class="{ 'is-current': index === breadcrumbItems.length - 1 }">
                {{ item.label }}
              </span>
              <span v-if="index < breadcrumbItems.length - 1" class="breadcrumb__sep">/</span>
            </template>
          </nav>
          <div>
            <div class="section-kicker">当前板块</div>
            <h2>{{ pageTitle }}</h2>
            <p>{{ pageDescription }}</p>
          </div>
        </div>
        <div class="shell-header__actions">
          <div class="user-badge" style="text-align: right;">
            <strong>{{ authStore.user?.username }}</strong>
            <span>{{ authStore.user?.role === 'admin' ? '管理员' : '普通用户' }}</span>
          </div>
          <button
            class="button"
            type="button"
            :title="'退出登录'"
            :aria-label="'退出登录'"
            @click="logout"
          >
            <LogOut :size="16" /> 退出
          </button>
        </div>
      </header>

      <section class="shell-body">
        <RouterView />
      </section>
    </div>
  </div>
</template>
