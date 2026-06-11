import axios from 'axios'

import { pushToast } from '../composables/useToast'
import { translate } from '../i18n'

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
        || translate('errors.network'),
      )
      pushToast({
        tone: 'error',
        title: translate('errors.requestFailed'),
        message,
      })
    }

    return Promise.reject(error)
  },
)
