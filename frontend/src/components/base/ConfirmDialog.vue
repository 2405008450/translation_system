<script setup lang="ts">
import Modal from './Modal.vue'

import {
  confirmState,
  rejectConfirm,
  resolveConfirm,
} from '../../composables/useConfirm'
</script>

<template>
  <Modal
    :open="confirmState.open"
    :title="confirmState.title"
    width="min(440px, calc(100vw - 32px))"
    @close="rejectConfirm"
  >
    <p class="confirm-dialog__message">{{ confirmState.message }}</p>

    <template #footer>
      <button class="button" type="button" @click="rejectConfirm">
        {{ confirmState.cancelText }}
      </button>
      <button
        class="button"
        :class="{ 'button--danger': confirmState.danger, 'button--primary': !confirmState.danger }"
        type="button"
        @click="resolveConfirm"
      >
        {{ confirmState.confirmText }}
      </button>
    </template>
  </Modal>
</template>
