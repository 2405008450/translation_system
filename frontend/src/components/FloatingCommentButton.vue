<script setup lang="ts">
defineProps<{
  top: number
  left: number
}>()

const emit = defineEmits<{
  create: []
}>()
</script>

<template>
  <button
    class="floating-comment-button"
    type="button"
    :style="{ top: `${top}px`, left: `${left}px` }"
    @click="emit('create')"
  >
    批注
  </button>
</template>
