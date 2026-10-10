import { createApp, h, ref } from 'vue'
import { http } from '../src/api/http'
import { i18n } from '../src/i18n'
import PreTranslateDialog from '../src/components/PreTranslateDialog.vue'
import '../src/styles.css'

const requests: Array<{ url: string; body: Record<string, unknown> }> = []
const savedScenario = new URLSearchParams(location.search).get('savedScenario')
http.defaults.adapter = async config => {
  const url = config.url || ''
  const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data
  let data: unknown = []
  if (url.endsWith('/pretranslation-runs')) {
    requests.push({ url, body })
    data = {
      id: 'run1', project_id: 'project1', status: 'completed', progress: 100,
      total_files: 1, completed_files: 1, failed_files: 0, canceled_files: 0,
      llm_translation_scenario: savedScenario || body.llm_translation_scenario,
      tasks: [{ id: 'task1', file_record_id: 'file1', status: 'completed', progress: 100 }],
    }
  }
  return { data, status: 200, statusText: 'OK', config, headers: {} }
}
;(window as any).scenarioHarness = { requests }

createApp({
  setup() {
    const open = ref(false)
    return () => h('main', [
      h('button', { onClick: () => { open.value = true } }, '打开预翻译'),
      h(PreTranslateDialog, {
        open: open.value, projectId: 'project1',
        files: [{ id: 'file1', filename: '英韩混合.xlsx', source_language: 'ko-KR', target_language: 'zh-CN' }],
        sourceLanguage: 'ko-KR', targetLanguage: 'zh-CN', translationGuidelines: '',
        onClose: () => { open.value = false },
      }),
    ])
  },
}).use(i18n).mount('#app')
