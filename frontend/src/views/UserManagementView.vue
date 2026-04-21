<script setup lang="ts">
import axios from 'axios'
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const { t } = useI18n()

const createUserMessage = ref('')
const newUsername = ref('')
const newPassword = ref('')
const newRole = ref<'admin' | 'user'>('user')

async function createUser() {
  createUserMessage.value = ''
  try {
    await authStore.registerUser(newUsername.value, newPassword.value, newRole.value)
    newUsername.value = ''
    newPassword.value = ''
    newRole.value = 'user'
    createUserMessage.value = t('userManagement.created')
  } catch (error) {
    if (axios.isAxiosError(error)) {
      createUserMessage.value = String(error.response?.data?.detail || t('userManagement.createFailed'))
      return
    }
    createUserMessage.value = error instanceof Error ? error.message : t('userManagement.createFailed')
  }
}
</script>

<template>
  <div class="content-stack">
    <section class="panel">
      <div class="section-title">{{ t('userManagement.createTitle') }}</div>
      <div class="upload-form form-grid-2">
        <label class="field field--compact">
          <span class="field__label">{{ t('userManagement.username') }}</span>
          <input v-model.trim="newUsername" class="field__control" type="text" minlength="3" maxlength="50" :aria-label="t('userManagement.username')" />
        </label>
        <label class="field field--compact">
          <span class="field__label">{{ t('userManagement.password') }}</span>
          <input v-model="newPassword" class="field__control" type="password" minlength="6" maxlength="128" :aria-label="t('userManagement.password')" />
        </label>
        <label class="field field--compact">
          <span class="field__label">{{ t('userManagement.role') }}</span>
          <select v-model="newRole" class="field__control">
            <option value="user">{{ t('common.roles.user') }}</option>
            <option value="admin">{{ t('common.roles.admin') }}</option>
          </select>
        </label>
        <div class="field field--compact field-actions">
          <span class="field__label">{{ t('userManagement.action') }}</span>
          <button class="button button--primary" type="button" @click="createUser">{{ t('userManagement.createUser') }}</button>
        </div>
      </div>
      <p v-if="createUserMessage" class="form-message" :class="{ 'is-error': createUserMessage !== t('userManagement.created') }">
        {{ createUserMessage }}
      </p>
    </section>

    <section class="panel">
      <div class="section-title">{{ t('userManagement.guideTitle') }}</div>
      <div class="info-list">
        <div class="info-item">
          <strong>{{ t('userManagement.admin') }}</strong>
          <span>{{ t('userManagement.adminDesc') }}</span>
        </div>
        <div class="info-item">
          <strong>{{ t('userManagement.user') }}</strong>
          <span>{{ t('userManagement.userDesc') }}</span>
        </div>
      </div>
    </section>
  </div>
</template>
