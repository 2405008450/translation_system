<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const errorMessage = ref('')

const title = computed(() => (authStore.initialized ? '登录' : '初始化管理员'))
const subtitle = computed(() =>
  authStore.initialized
    ? '输入账号密码进入翻译工作台'
    : '系统当前还没有用户，请先创建管理员账号',
)
const submitDisabled = computed(() => authStore.loading || !authStore.tableExists)

async function submit() {
  errorMessage.value = ''
  try {
    if (authStore.initialized) {
      await authStore.login(username.value, password.value)
    } else {
      await authStore.initializeAdmin(username.value, password.value)
    }

    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect)
  } catch (error) {
    if (axios.isAxiosError(error)) {
      errorMessage.value = String(error.response?.data?.detail || '请求失败，请稍后重试。')
      return
    }
    errorMessage.value = error instanceof Error ? error.message : '请求失败，请稍后重试。'
  }
}

onMounted(async () => {
  try {
    await authStore.checkInitStatus()
  } catch {
    errorMessage.value = '无法获取系统初始化状态，请确认后端已启动。'
  }
})
</script>

<template>
  <main class="auth-layout">
    <section class="auth-panel">
      <div class="auth-panel__header">
        <div class="section-kicker">翻译工作台</div>
        <h1>{{ title }}</h1>
        <p>{{ subtitle }}</p>
      </div>

      <form class="auth-form" @submit.prevent="submit">
        <label class="field">
          <span class="field__label">用户名</span>
          <input
            v-model.trim="username"
            class="field__control"
            type="text"
            minlength="3"
            maxlength="50"
            autocomplete="username"
            required
          />
        </label>

        <label class="field">
          <span class="field__label">密码</span>
          <input
            v-model="password"
            class="field__control"
            type="password"
            minlength="6"
            maxlength="128"
            autocomplete="current-password"
            required
          />
        </label>

        <p v-if="errorMessage" class="form-message is-error">{{ errorMessage }}</p>
        <p v-if="authStore.initMessage" class="form-message is-error">{{ authStore.initMessage }}</p>

        <button class="button button--primary" type="submit" :disabled="submitDisabled">
          {{ authStore.loading ? '提交中...' : title }}
        </button>
      </form>
    </section>
  </main>
</template>
