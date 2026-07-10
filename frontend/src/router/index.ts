import { ref } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '../stores/auth'
import AppLayout from '../views/AppLayout.vue'
import AssignmentEventsView from '../views/AssignmentEventsView.vue'
import DashboardView from '../views/DashboardView.vue'
import GlossaryBaseEditView from '../views/GlossaryBaseEditView.vue'
import GlossaryBaseView from '../views/GlossaryBaseView.vue'
import LoginView from '../views/LoginView.vue'
import ProjectDetailView from '../views/ProjectDetailView.vue'
import ProjectListView from '../views/ProjectListView.vue'
import TaskListView from '../views/TaskListView.vue'
import TermBaseEditView from '../views/TermBaseEditView.vue'
import TermBaseView from '../views/TermBaseView.vue'
import TMCollectionEditView from '../views/TMCollectionEditView.vue'
import TMManagementView from '../views/TMManagementView.vue'
import TranslationRulesView from '../views/TranslationRulesView.vue'
import UserManagementView from '../views/UserManagementView.vue'
import WorkbenchView from '../views/WorkbenchView.vue'

export const routeLoading = ref(false)

let routeLoadingShowTimer: ReturnType<typeof setTimeout> | null = null
let routeLoadingHideTimer: ReturnType<typeof setTimeout> | null = null

function startRouteLoading() {
  if (routeLoadingHideTimer) {
    clearTimeout(routeLoadingHideTimer)
    routeLoadingHideTimer = null
  }
  if (routeLoading.value || routeLoadingShowTimer) {
    return
  }
  routeLoadingShowTimer = setTimeout(() => {
    routeLoading.value = true
    routeLoadingShowTimer = null
  }, 80)
}

function stopRouteLoading() {
  if (routeLoadingShowTimer) {
    clearTimeout(routeLoadingShowTimer)
    routeLoadingShowTimer = null
  }
  if (!routeLoading.value) {
    return
  }
  routeLoadingHideTimer = setTimeout(() => {
    routeLoading.value = false
    routeLoadingHideTimer = null
  }, 220)
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
    },
    {
      path: '/tasks/:id/focus',
      name: 'workbench-focus',
      component: WorkbenchView,
      props: (route) => ({
        id: String(route.params.id),
        standalone: true,
      }),
      meta: {
        requiresAuth: true,
        navSection: 'tasks',
        pageTitle: '翻译工作台',
        pageDescription: '专注处理当前翻译任务',
        pageTitleKey: 'pages.workbench.title',
        pageDescriptionKey: 'pages.workbench.description',
      },
    },
    {
      path: '/merge/:viewId/focus',
      name: 'merge-view-focus',
      component: WorkbenchView,
      props: (route) => ({
        mergeViewId: String(route.params.viewId),
        standalone: true,
      }),
      meta: {
        requiresAuth: true,
        navSection: 'tasks',
        pageTitle: '合并视图',
        pageDescription: '在同一工作台按文件分区编辑多个文件',
        pageTitleKey: 'pages.workbench.title',
        pageDescriptionKey: 'pages.workbench.description',
      },
    },
    {
      path: '/',
      component: AppLayout,
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          redirect: { name: 'projects' },
        },
        {
          path: 'dashboard',
          name: 'dashboard',
          component: DashboardView,
          meta: {
            navSection: 'dashboard',
            pageTitle: '数据看板',
            pageDescription: '查看项目、翻译字数、LLM 处理量和用户活跃趋势',
            pageTitleKey: 'pages.dashboard.title',
            pageDescriptionKey: 'pages.dashboard.description',
          },
        },
        {
          path: 'projects',
          name: 'projects',
          component: ProjectListView,
          meta: {
            navSection: 'projects',
            pageTitle: '项目管理',
            pageDescription: '查看所有翻译项目，管理项目进度与分配',
            pageTitleKey: 'pages.projects.title',
            pageDescriptionKey: 'pages.projects.description',
          },
        },
        {
          path: 'projects/:id',
          name: 'project-detail',
          component: ProjectDetailView,
          props: true,
          meta: {
            navSection: 'projects',
            pageTitle: '项目详情',
            pageDescription: '上传待翻译文档并查看项目处理进度',
            pageTitleKey: 'pages.projectDetail.title',
            pageDescriptionKey: 'pages.projectDetail.description',
          },
        },
        {
          path: 'tasks',
          name: 'tasks',
          component: TaskListView,
          meta: {
            navSection: 'tasks',
            pageTitle: '我的任务',
            pageDescription: '查看分配给自己的任务，进入翻译工作台',
            pageTitleKey: 'pages.tasks.title',
            pageDescriptionKey: 'pages.tasks.description',
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
            pageTitleKey: 'pages.workbench.title',
            pageDescriptionKey: 'pages.workbench.description',
          },
        },
        {
          path: 'tm',
          name: 'tm',
          component: TMManagementView,
          meta: {
            navSection: 'tm',
            pageTitle: '记忆库管理',
            pageDescription: '导入术语和双语句对，增强匹配效果',
            pageTitleKey: 'pages.tm.title',
            pageDescriptionKey: 'pages.tm.description',
          },
        },
        {
          path: 'tm/:id',
          alias: ['/tm/:id/edit'],
          name: 'tm-edit',
          component: TMCollectionEditView,
          props: true,
          meta: {
            navSection: 'tm',
            pageTitle: '记忆库详情',
            pageDescription: '维护记忆库信息、TM 条目和导入导出操作',
            pageTitleKey: 'pages.tmEdit.title',
            pageDescriptionKey: 'pages.tmEdit.description',
            hidePageHeader: true,
          },
        },
        {
          path: 'term-base',
          name: 'term-base',
          component: TermBaseView,
          meta: {
            navSection: 'term-base',
            pageTitle: '术语库管理',
            pageDescription: '管理译后检查术语库，维护翻译术语一致性',
            pageTitleKey: 'pages.termBase.title',
            pageDescriptionKey: 'pages.termBase.description',
          },
        },
        {
          path: 'translation-rules',
          name: 'translation-rules',
          component: TranslationRulesView,
          meta: {
            navSection: 'translation-rules',
            pageTitle: '翻译规则',
            pageDescription: '维护可复用的翻译规则，供 AI 翻译时选择',
            pageTitleKey: 'pages.translationRules.title',
            pageDescriptionKey: 'pages.translationRules.description',
          },
        },
        {
          path: 'glossary',
          name: 'glossary',
          component: GlossaryBaseView,
          meta: {
            navSection: 'glossary',
            pageTitle: '词汇表管理',
            pageDescription: '管理 AI 预翻译专用词汇表，按原文命中后注入 LLM 上下文',
            pageTitleKey: 'pages.glossary.title',
            pageDescriptionKey: 'pages.glossary.description',
          },
        },
        {
          path: 'glossary/:id',
          alias: ['/glossary/:id/edit'],
          name: 'glossary-edit',
          component: GlossaryBaseEditView,
          props: true,
          meta: {
            navSection: 'glossary',
            pageTitle: '词汇表详情',
            pageDescription: '维护词汇表信息、词条和导入导出操作',
            pageTitleKey: 'pages.glossaryEdit.title',
            pageDescriptionKey: 'pages.glossaryEdit.description',
            hidePageHeader: true,
          },
        },
        {
          path: 'term-base/:id',
          alias: ['/term-base/:id/edit'],
          name: 'term-base-edit',
          component: TermBaseEditView,
          props: true,
          meta: {
            navSection: 'term-base',
            pageTitle: '术语库详情',
            pageDescription: '维护译后检查术语库信息、术语条目和导入导出操作',
            pageTitleKey: 'pages.termBaseEdit.title',
            pageDescriptionKey: 'pages.termBaseEdit.description',
            hidePageHeader: true,
          },
        },
        {
          path: 'assignment-events',
          name: 'assignment-events',
          component: AssignmentEventsView,
          meta: {
            requiresBusinessManager: true,
            navSection: 'assignment-events',
            pageTitle: '指派记录',
            pageDescription: '查看项目和文件任务的指派、授权和取消记录',
            pageTitleKey: 'pages.assignmentEvents.title',
            pageDescriptionKey: 'pages.assignmentEvents.description',
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
            pageTitleKey: 'pages.users.title',
            pageDescriptionKey: 'pages.users.description',
          },
        },
      ],
    },
  ],
})

function getDefaultRouteName(authStore: ReturnType<typeof useAuthStore>) {
  return authStore.isExternalTranslator ? 'tasks' : 'projects'
}

function isExternalTranslatorBlockedRoute(name: unknown) {
  return ['dashboard'].includes(String(name || ''))
}

router.beforeEach(async (to) => {
  startRouteLoading()
  const authStore = useAuthStore()
  if (!authStore.ready) {
    await authStore.bootstrap()
  }

  if (to.name === 'login' && authStore.isAuthenticated) {
    return { name: getDefaultRouteName(authStore) }
  }

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    return {
      name: 'login',
      query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined,
    }
  }

  if (to.meta.requiresAdmin && !authStore.isAdmin) {
    return { name: getDefaultRouteName(authStore) }
  }

  if (to.meta.requiresBusinessManager && !authStore.isBusinessManager) {
    return { name: getDefaultRouteName(authStore) }
  }

  if (authStore.isAuthenticated && authStore.isExternalTranslator && isExternalTranslatorBlockedRoute(to.name)) {
    return { name: 'tasks' }
  }

  return true
})

router.afterEach(() => {
  stopRouteLoading()
})

router.onError(() => {
  stopRouteLoading()
})

export default router
