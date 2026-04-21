import { ref } from 'vue'
import { defineStore } from 'pinia'

export interface BreadcrumbItem {
  label: string
  to?: {
    name: string
    params?: Record<string, string>
    query?: Record<string, string>
  } | null
}

export interface PageContext {
  title?: string
  description?: string
  breadcrumbs?: BreadcrumbItem[]
}

export interface RecentItem {
  id: string
  label: string
  section: string
  route: {
    name: string
    params?: Record<string, string>
    query?: Record<string, string>
  }
  openedAt: string
}

const RECENT_ITEMS_STORAGE_KEY = 'tm-workbench-recent-items'
const MAX_RECENT_ITEMS = 5

function loadRecentItems() {
  try {
    const raw = window.localStorage.getItem(RECENT_ITEMS_STORAGE_KEY)
    if (!raw) {
      return [] as RecentItem[]
    }

    const parsed = JSON.parse(raw) as RecentItem[]
    if (!Array.isArray(parsed)) {
      return [] as RecentItem[]
    }

    return parsed.slice(0, MAX_RECENT_ITEMS)
  } catch {
    return [] as RecentItem[]
  }
}

function persistRecentItems(items: RecentItem[]) {
  window.localStorage.setItem(RECENT_ITEMS_STORAGE_KEY, JSON.stringify(items))
}

export const useShellStore = defineStore('shell', () => {
  const pageContext = ref<PageContext>({
    title: '',
    description: '',
    breadcrumbs: [],
  })
  const recentItems = ref<RecentItem[]>(loadRecentItems())

  function setPageContext(nextContext: Partial<PageContext>) {
    pageContext.value = {
      ...pageContext.value,
      ...nextContext,
    }
  }

  function clearPageContext() {
    pageContext.value = {
      title: '',
      description: '',
      breadcrumbs: [],
    }
  }

  function trackRecent(item: Omit<RecentItem, 'openedAt'>) {
    const nextItem: RecentItem = {
      ...item,
      openedAt: new Date().toISOString(),
    }

    recentItems.value = [
      nextItem,
      ...recentItems.value.filter((currentItem) => currentItem.id !== item.id),
    ].slice(0, MAX_RECENT_ITEMS)

    persistRecentItems(recentItems.value)
  }

  return {
    pageContext,
    recentItems,
    setPageContext,
    clearPageContext,
    trackRecent,
  }
})
