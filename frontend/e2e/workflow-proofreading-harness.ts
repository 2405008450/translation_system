import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import ProjectListView from '../src/views/ProjectListView.vue'
import ProofreadingPanel from '../src/components/ProofreadingPanel.vue'
import WorkbenchView from '../src/views/WorkbenchView.vue'
import Toast from '../src/components/base/Toast.vue'
import { useAuthStore } from '../src/stores/auth'
import { i18n } from '../src/i18n'
import '../src/styles.css'

async function mount() {
  const mode = new URLSearchParams(window.location.search).get('mode') || 'list'
  const pinia = createPinia()
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/test', name: 'workbench-focus', component: { render: () => null } },
    { path: '/alignment/:id', name: 'document-alignment', component: { render: () => null } },
    { path: '/project/:id', name: 'project-detail', component: { render: () => null } },
  ] })
  const auth = useAuthStore(pinia)
  auth.token = 'ui-test'
  window.localStorage.setItem('token', 'ui-test')
  auth.user = {
    id: 'user-1', username: '测试用户', role: 'admin', translator_type: 'internal', is_active: true, created_at: '',
  }
  auth.ready = true
  await router.push('/test')
  await router.isReady()
  const app = createApp({ render: () => [h(Toast), mode === 'list' ? h(ProjectListView)
    : mode === 'merge' ? h(WorkbenchView, { mergeViewId: 'view-1', standalone: true })
    : mode === 'workbench' || mode === 'standalone' ? h(WorkbenchView, { id: 'file-1', standalone: true })
      : h(ProofreadingPanel, { projectId: 'project-1', templateId: mode === 'pure' ? 'proofread_only' : 'proofread' })] })
  app.use(pinia).use(router).use(i18n).mount('#app')
}
void mount()
