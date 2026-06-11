<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'

export interface PageBreadcrumbItem {
  label: string
  to?: RouteLocationRaw | null
}

withDefaults(defineProps<{
  title: string
  description?: string
  breadcrumbs?: PageBreadcrumbItem[]
}>(), {
  description: '',
  breadcrumbs: () => [],
})

const { t } = useI18n()
</script>

<template>
  <section class="page-header">
    <div class="page-header__copy">
      <nav v-if="breadcrumbs.length" class="breadcrumb" :aria-label="t('shell.topbar.globalBreadcrumb')">
        <template v-for="(item, index) in breadcrumbs" :key="`${item.label}-${index}`">
          <RouterLink
            v-if="item.to"
            class="breadcrumb__item is-link"
            :to="item.to"
          >
            {{ item.label }}
          </RouterLink>
          <span v-else class="breadcrumb__item is-current">{{ item.label }}</span>
          <span v-if="index < breadcrumbs.length - 1" class="breadcrumb__sep">/</span>
        </template>
      </nav>
      <h1 class="page-header__title">{{ title }}</h1>
      <p v-if="description" class="page-header__description">{{ description }}</p>
    </div>

    <div v-if="$slots.actions" class="page-header__actions">
      <slot name="actions" />
    </div>
  </section>
</template>
