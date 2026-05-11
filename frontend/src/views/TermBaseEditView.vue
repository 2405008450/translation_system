<script setup lang="ts">
import ResourceDetailPage from '../components/ResourceDetailPage.vue'

defineProps<{
  id: string
}>()
</script>

<template>
  <ResourceDetailPage :id="id" mode="term" />
</template>
