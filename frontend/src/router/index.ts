import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import AppLayout from '../views/AppLayout.vue'
import LoginView from '../views/LoginView.vue'
import TaskListView from '../views/TaskListView.vue'
import TMManagementView from '../views/TMManagementView.vue'
import UserManagementView from '../views/UserManagementView.vue'
import WorkbenchView from '../views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
    },
    {
      path: '/',
      component: AppLayout,
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          redirect: { name: 'tasks' },
        },
        {
          path: 'tasks',
          name: 'tasks',
          component: TaskListView,
          meta: {
            navSection: 'tasks',
            pageTitle: '任务管理',
            pageDescription: '上传文档、切换任务、进入翻译工作台',
          },
        },
        {
          path: 'tasks/:id',
          name: 'workbench',
          component: WorkbenchView,
          props: true,
          meta: {
            navSection: 'tasks',
            pageTitle: '翻译工作台',
            pageDescription: '编辑句段、执行 AI 修正、导出译后文档',
          },
        },
        {
          path: 'tm',
          name: 'tm',
          component: TMManagementView,
          meta: {
            requiresAdmin: true,
            navSection: 'tm',
            pageTitle: 'TM 记忆库',
            pageDescription: '导入术语和双语句对，增强匹配效果',
          },
        },
        {
          path: 'users',
          name: 'users',
          component: UserManagementView,
          meta: {
            requiresAdmin: true,
            navSection: 'users',
            pageTitle: '用户管理',
            pageDescription: '创建和分配系统用户角色',
          },
        },
      ],
    },
  ],
})

router.beforeEach(async (to) => {
  const authStore = useAuthStore()
  if (!authStore.ready) {
    await authStore.bootstrap()
  }

  if (to.name === 'login' && authStore.isAuthenticated) {
    return { name: 'tasks' }
  }

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    return {
      name: 'login',
      query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined,
    }
  }

  if (to.meta.requiresAdmin && !authStore.isAdmin) {
    return { name: 'tasks' }
  }

  return true
})

export default router
