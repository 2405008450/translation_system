import axios from 'axios'

import { pushToast } from '../composables/useToast'

let unauthorizedHandler: (() => void | Promise<void>) | null = null

export function setUnauthorizedHandler(handler: (() => void | Promise<void>) | null) {
  unauthorizedHandler = handler
}

export const http = axios.create({
  baseURL: '/api',
})

http.interceptors.request.use((config) => {
  const token = window.localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401 && unauthorizedHandler) {
      await unauthorizedHandler()
      return Promise.reject(error)
    }

    const isNetworkError = !error.response || error.code === 'ECONNABORTED'
    const isServerError = Number(error.response?.status || 0) >= 500

    if (isNetworkError || isServerError) {
      const message = String(
        error.response?.data?.detail
        || error.message
        || '网络异常，请稍后重试。',
      )
      pushToast({
        tone: 'error',
        title: '请求失败',
        message,
      })
    }

    return Promise.reject(error)
  },
)
