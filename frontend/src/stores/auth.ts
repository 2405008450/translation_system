import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { http } from '../api/http'
import type { AuthResponse, InitStatusResponse, User } from '../types/api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(window.localStorage.getItem('token'))
  const user = ref<User | null>(null)
  const initialized = ref(true)
  const tableExists = ref(true)
  const initMessage = ref<string | null>(null)
  const ready = ref(false)
  const loading = ref(false)

  let bootstrapPromise: Promise<void> | null = null

  const isAuthenticated = computed(() => Boolean(token.value && user.value))
  const isAdmin = computed(() => user.value?.role === 'admin')

  function persistToken(nextToken: string | null) {
    token.value = nextToken
    if (nextToken) {
      window.localStorage.setItem('token', nextToken)
    } else {
      window.localStorage.removeItem('token')
    }
  }

  function setAuth(response: AuthResponse) {
    persistToken(response.access_token)
    user.value = response.user
    initialized.value = true
  }

  function clearSession() {
    persistToken(null)
    user.value = null
  }

  async function checkInitStatus() {
    const { data } = await http.get<InitStatusResponse>('/auth/init')
    initialized.value = data.initialized
    tableExists.value = data.table_exists
    initMessage.value = data.message
    return data
  }

  async function fetchMe() {
    const { data } = await http.get<User>('/auth/me')
    user.value = data
    return data
  }

  async function bootstrap() {
    if (bootstrapPromise) {
      return bootstrapPromise
    }

    bootstrapPromise = (async () => {
      try {
        const initStatus = await checkInitStatus()
        if (!initStatus.initialized) {
          clearSession()
          return
        }

        if (!token.value) {
          clearSession()
          return
        }

        try {
          await fetchMe()
        } catch {
          clearSession()
        }
      } finally {
        ready.value = true
        bootstrapPromise = null
      }
    })()

    return bootstrapPromise
  }

  async function login(username: string, password: string) {
    loading.value = true
    try {
      const { data } = await http.post<AuthResponse>('/auth/login', {
        username,
        password,
      })
      setAuth(data)
      return data
    } finally {
      loading.value = false
    }
  }

  async function initializeAdmin(username: string, password: string) {
    loading.value = true
    try {
      const { data } = await http.post<AuthResponse>('/auth/init', {
        username,
        password,
      })
      setAuth(data)
      return data
    } finally {
      loading.value = false
    }
  }

  async function registerUser(username: string, password: string, role: 'admin' | 'user') {
    const { data } = await http.post<User>('/auth/register', {
      username,
      password,
      role,
    })
    return data
  }

  function logout() {
    clearSession()
  }

  return {
    token,
    user,
    initialized,
    tableExists,
    initMessage,
    ready,
    loading,
    isAuthenticated,
    isAdmin,
    bootstrap,
    checkInitStatus,
    fetchMe,
    login,
    initializeAdmin,
    registerUser,
    logout,
  }
})
