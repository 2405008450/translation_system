<script setup lang="ts">
import ConfirmDialog from './components/base/ConfirmDialog.vue'
import Toast from './components/base/Toast.vue'
</script>

<template>
  <RouterView />
  <Toast />
  <ConfirmDialog />
</template>
