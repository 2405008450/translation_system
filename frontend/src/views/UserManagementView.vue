<script setup lang="ts">
import axios from 'axios'
import { ref } from 'vue'

import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()

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
    createUserMessage.value = '用户已创建'
  } catch (error) {
    if (axios.isAxiosError(error)) {
      createUserMessage.value = String(error.response?.data?.detail || '用户创建失败。')
      return
    }
    createUserMessage.value = error instanceof Error ? error.message : '用户创建失败。'
  }
}
</script>

<template>
  <div class="content-stack">
    <section class="panel">
      <div class="section-title">创建用户</div>
      <div class="upload-form form-grid-2">
        <label class="field field--compact">
          <span class="field__label">用户名</span>
          <input v-model.trim="newUsername" class="field__control" type="text" minlength="3" maxlength="50" />
        </label>
        <label class="field field--compact">
          <span class="field__label">密码</span>
          <input v-model="newPassword" class="field__control" type="password" minlength="6" maxlength="128" />
        </label>
        <label class="field field--compact">
          <span class="field__label">角色</span>
          <select v-model="newRole" class="field__control">
            <option value="user">普通用户</option>
            <option value="admin">管理员</option>
          </select>
        </label>
        <div class="field field--compact field-actions">
          <span class="field__label">执行</span>
          <button class="button button--primary" type="button" @click="createUser">创建用户</button>
        </div>
      </div>
      <p v-if="createUserMessage" class="form-message" :class="{ 'is-error': createUserMessage !== '用户已创建' }">
        {{ createUserMessage }}
      </p>
    </section>

    <section class="panel">
      <div class="section-title">管理说明</div>
      <div class="info-list">
        <div class="info-item">
          <strong>管理员</strong>
          <span>可导入 TM、创建用户、删除任务</span>
        </div>
        <div class="info-item">
          <strong>普通用户</strong>
          <span>可登录、上传文档、编辑句段、执行翻译流程</span>
        </div>
      </div>
    </section>
  </div>
</template>
