<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

interface NavItem {
  name: string
  label: string
  description: string
  visible: boolean
}

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

const navItems = computed<NavItem[]>(() => [
  {
    name: 'tasks',
    label: '任务管理',
    description: '上传与翻译任务',
    visible: true,
  },
  {
    name: 'tm',
    label: 'TM 记忆库',
    description: '双语记忆与导入',
    visible: authStore.isAdmin,
  },
  {
    name: 'users',
    label: '用户管理',
    description: '账号和角色',
    visible: authStore.isAdmin,
  },
])

const pageTitle = computed(() => String(route.meta.pageTitle || '翻译工作台'))
const pageDescription = computed(() => String(route.meta.pageDescription || ''))
const currentSection = computed(() => String(route.meta.navSection || 'tasks'))

function isNavActive(name: string) {
  return currentSection.value === name
}

async function logout() {
  authStore.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <div class="app-frame">
    <aside class="app-sidebar">
      <div class="sidebar-brand">
        <div class="section-kicker">协同翻译</div>
        <h1>翻译工作台</h1>
        <p>按板块管理任务、TM 和记忆库用户</p>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <button
          v-for="item in navItems"
          v-show="item.visible"
          :key="item.name"
          class="sidebar-nav__item"
          :class="{ 'is-active': isNavActive(item.name) }"
          type="button"
          @click="router.push({ name: item.name })"
        >
          <strong>{{ item.label }}</strong>
          <span>{{ item.description }}</span>
        </button>
      </nav>

      <div class="sidebar-footer">
        <div class="user-badge user-badge--sidebar">
          <strong>{{ authStore.user?.username }}</strong>
          <span>{{ authStore.user?.role === 'admin' ? '管理员' : '普通用户' }}</span>
        </div>
        <button class="button" type="button" @click="logout">退出登录</button>
      </div>
    </aside>

    <div class="app-main">
      <header class="shell-header">
        <div>
          <div class="section-kicker">当前板块</div>
          <h2>{{ pageTitle }}</h2>
          <p>{{ pageDescription }}</p>
        </div>
      </header>

      <section class="shell-body">
        <RouterView />
      </section>
    </div>
  </div>
</template>
