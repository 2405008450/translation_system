import { expect, test, type Page } from '@playwright/test'

const templates = [
  { id: 'proofread', name: '对齐-校对', steps: [{ step_key: 'alignment', name: '对齐', step_type: 'alignment' }, { step_key: 'proofread', name: '校对', step_type: 'proofread' }] },
  { id: 'proofread_only', name: '校对', steps: [{ step_key: 'proofread', name: '校对', step_type: 'proofread' }] },
  { id: 'translate', name: '翻译', steps: [{ step_key: 'translate', name: '翻译', step_type: 'translation' }] },
  { id: 'translate_review', name: '翻译-审校', steps: [{ step_key: 'translate', name: '翻译', step_type: 'translation' }, { step_key: 'review', name: '审校', step_type: 'review' }] },
  { id: 'translate_review_proofread', name: '翻译-审校-校对', steps: [{ step_key: 'translate', name: '翻译', step_type: 'translation' }, { step_key: 'review', name: '审校', step_type: 'review' }, { step_key: 'proofread', name: '校对', step_type: 'proofread' }] },
]
const workflow = templates[4]!.steps.map((step, index) => ({ ...step, id: `step-${index}`, sort_order: index }))
const segments = [0, 1].map((index) => ({
  id: `segment-${index}`, file_record_id: 'file-1', sentence_id: `s${index}`, source_text: `Source ${index}`,
  display_text: `Source ${index}`, target_text: `Traduction ${index}`, target_html: null, status: 'none',
  source: 'manual', workflow_step_id: index === 0 ? 'step-2' : 'step-1',
  workflow_step_name: index === 0 ? '校对' : '审校', can_write: true, version: 1, display_index: index,
  sequence_index: index, score: 0, block_type: 'paragraph', block_index: index, qa_issues: [],
}))

async function mockApi(page: Page, standalone = false, options: { batchStatus?: string; wordUnavailable?: boolean; normalWorkflow?: boolean } = {}) {
  const errors: string[] = []
  page.on('pageerror', (error) => { errors.push(error.message); console.error('Browser error:', error.message) })
  await page.route('http://127.0.0.1:5182/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api', '')
    let data: unknown = []
    if (path === '/workflow-templates') data = { items: templates }
    else if (path === '/projects') data = { items: [], total: 0 }
    else if (path.endsWith('/proofreading-batches')) data = { items: [] }
    else if (path.endsWith('/prepare')) data = { batch_id: 'batch-1' }
    else if (path.endsWith('/export-readiness')) data = {
      batch_id: 'batch-1', total: 1, confirmed: 0, unconfirmed: 1, missing_translation: 0,
      translation_only: 0, translation_only_unreviewed: 0, llm_failed: 0, has_warnings: false,
      available_formats: options.wordUnavailable ? ['proofreading_audit_xlsx'] : ['proofreading_audit_xlsx', 'proofreading_docx_revisions'],
      unavailable_formats: options.wordUnavailable ? { proofreading_docx_revisions: 'Word 源文件缺失，无法生成保留排版的校对修订版。' } : {},
    }
    else if (path.endsWith('/export-options')) data = { export_options: [
      { id: 'original', name: '原格式译文', description: '按原文件格式导出当前译文', extension: '.txt' },
    ] }
    else if (path.endsWith('/export-tasks') && route.request().method() === 'POST') data = {
      task_id: 'export-1', file_record_id: 'file-1', export_type: route.request().postDataJSON().format,
      status: 'completed', progress: 100, filename: '校对交付文件.docx',
    }
    else if (path === '/file-records/export-tasks/export-1/download') return route.fulfill({
      contentType: 'application/octet-stream', body: 'test-export',
      headers: { 'Content-Disposition': "attachment; filename*=UTF-8''proofreading.docx" },
    })
    else if (path.endsWith('/proofreading-baselines')) data = {
      is_proofreading: standalone,
      proofreading_context: options.normalWorkflow ? null : { batch_id: 'batch-1', batch_kind: standalone ? 'xlsx_columns' : 'workflow_segments',
        workflow_step_id: 'step-2',
        workflow_stage: 'proofreading', batch_status: options.batchStatus || 'ready', sheet_name: '测试资料', provider: 'auto', model: '',
        user_instructions: '', can_generate: true, eligible_segments: 1, failed_segments: 0 },
      items: [{ segment_id: 'segment-0', sentence_id: 's0', original_target_text: 'Traduction initiale',
        review_category: 'accuracy', review_suggestion: '统一术语', llm_reviewed: true }],
    }
    else if (path === '/file-records/file-1') data = {
      id: 'file-1', project_id: 'project-1', filename: 'source.txt', status: 'in_progress',
      source_language: 'en-US', target_language: 'fr-FR', segments, total_segments: 2,
      status_stats: { total: 2, confirmed: 0, none: 2, exact: 0, fuzzy: 0 },
      workflow_steps: workflow, workflow_progress: workflow.map((step) => ({ ...step, total_segments: 2, completed_segments: 0 })),
      collection_ids: [], term_base_ids: [], qa_term_base_ids: [], term_base_write_ids: [],
      term_base_names: [], qa_term_base_names: [], term_base_write_names: [], document_parse_options: {}, document_statistics: {},
      can_write: true, can_manage: true, can_export: true, is_edit_locked: false, reference_files: [],
    }
    else if (path.endsWith('/revision-settings')) data = { track_changes: true }
    else if (path.endsWith('/comments')) data = { items: [], total: 0 }
    else if (path.endsWith('/segments/changes')) data = { items: [], segments: [], has_more: false }
    else if (path.endsWith('/segments/events')) return route.fulfill({ status: 404, body: '' })
    else if (path.endsWith('/segments')) data = { segments, total_segments: 2, matched_segments: 2, skip: 0, limit: 100 }
    else if (path.includes('/reference-match')) data = { items: [], matches: [] }
    await route.fulfill({ json: data })
  })
  return errors
}

test('五种模板按名称区分，独立流程展示准确阶段', async ({ page }) => {
  const errors = await mockApi(page)
  await page.goto('/e2e/workflow-proofreading-harness.html')
  await page.getByTestId('project-create-button').click()
  const select = page.getByTestId('project-create-workflow-template')
  await expect(select.locator('option:not([disabled])')).toHaveText(['翻译', '翻译-审校', '翻译-审校-校对', '校对', '对齐-校对'])
  await select.selectOption('proofread_only')
  await expect(page.locator('.workflow-editor__row input')).toHaveValue('校对')
  await expect(page.getByTestId('project-create-source-language')).toHaveCount(0)
  await expect(page.getByText('导入已有对应关系的 Excel 原文列和译文列，直接校对，无需对齐。')).toBeVisible()
  await select.selectOption('proofread')
  await expect(page.locator('.workflow-editor__row input')).toHaveCount(2)
  await expect(page.locator('.workflow-editor__row input').first()).toHaveValue('对齐')
  await expect(page.locator('.workflow-editor__row input').last()).toHaveValue('校对')
  expect(errors).toEqual([])
})

test('纯校对只提供表格列映射', async ({ page }) => {
  await mockApi(page)
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=pure')
  await expect(page.getByTestId('proofreading-import-spreadsheet')).toBeVisible()
  await expect(page.getByTestId('proofreading-import-document-pair')).toHaveCount(0)
  await expect(page.getByText('原文与译文按行对应，映射列后直接校对，无需对齐。')).toBeVisible()
})

test('对齐-校对只提供双文档导入', async ({ page }) => {
  await mockApi(page)
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=combined')
  await expect(page.getByTestId('proofreading-import-document-pair')).toBeVisible()
  await expect(page.getByTestId('proofreading-import-spreadsheet')).toHaveCount(0)
  await expect(page.getByText('分别上传原文与译文，完成对齐后再校对。')).toBeVisible()
})

test('普通工作台保留阶段与状态列，仅校对句段展示建议且手动启动', async ({ page }) => {
  const errors = await mockApi(page)
  const starts: string[] = []
  page.on('request', (request) => { if (request.url().endsWith('/generate')) starts.push(request.url()) })
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=workbench')
  await expect(page.getByTestId('workflow-proofreading-actions')).toBeVisible()
  await expect(page.locator('.segment-row__workflow-suggestion')).toHaveCount(1)
  await expect(page.locator('.segment-row__cell--workflow')).toHaveCount(2)
  await expect(page.locator('.segment-row__cell--state')).toHaveCount(2)
  await expect(page.locator('.segment-row__cell--original-target')).toHaveCount(2)
  await expect(page.locator('.segment-row__cell--original-target').last()).toHaveText('—')
  const columns = await page.locator('.segment-table-head').evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length)
  expect(columns).toBe(6)
  expect(starts).toEqual([])
  await page.getByTestId('workflow-proofreading-actions').getByRole('button', { name: '开始 LLM 校对' }).click()
  await expect(page.getByRole('dialog').getByText('校对模型', { exact: true })).toBeVisible()
  expect(errors).toEqual([])
})

test('纯校对工作台阶段编号不包含对齐', async ({ page }) => {
  await mockApi(page, true)
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=standalone')
  await expect(page.getByTestId('proofreading-workbench-toolbar')).toBeVisible()
  await expect(page.getByText('阶段 2/2 · 校对（无需对齐）')).toBeVisible()
  await expect(page.getByTestId('proofreading-reopen-alignment-button')).toHaveCount(0)
})

test('校对顶部与操作栏提供相同交付格式，普通导出收起保留', async ({ page }) => {
  const errors = await mockApi(page)
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=workbench')
  await page.getByTestId('workbench-export-button').click()
  const choices = page.getByTestId('workflow-proofreading-export-options')
  await expect(choices.getByRole('button')).toHaveText([
    /Excel 校对报告/, /Word 校对修订版/,
  ])
  await expect(choices.getByRole('button').last()).toBeEnabled()
  await expect(choices.getByRole('button').last()).toContainText('包含修改建议批注')
  const others = page.locator('details.export-dropdown__other')
  await expect(others).not.toHaveAttribute('open', '')
  await expect(page.getByText('按原文件格式导出当前译文')).toBeHidden()
  await others.locator('summary').click()
  await expect(page.getByText('按原文件格式导出当前译文')).toBeVisible()
  await page.getByTestId('workbench-export-button').click()
  await page.getByTestId('workflow-proofreading-export-button').click()
  await expect(choices.getByRole('button')).toHaveText([
    /Excel 校对报告/, /Word 校对修订版/,
  ])
  await expect(page.locator('details.export-dropdown__other')).toHaveCount(0)
  await page.screenshot({ path: '../logs/workflow-proofreading-export-menu.png', fullPage: true })
  expect(errors).toEqual([])
})

test('校对两个入口都使用校对导出队列，运行期间显示共同进度', async ({ page }) => {
  const errors = await mockApi(page)
  const formats: string[] = []
  await page.route('**/proofreading-batches/batch-1/export-tasks', async (route) => {
    formats.push(route.request().postDataJSON().format)
    await route.fulfill({ json: { task_id: 'export-1', status: 'queued', progress: 20 } })
  })
  await page.route('**/file-records/export-tasks/export-1', async (route) => {
    await route.fulfill({ json: { task_id: 'export-1', status: 'completed', progress: 100, filename: '校对.docx' } })
  })
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=workbench')
  for (const [buttonId, format] of [
    ['workbench-export-button', 'proofreading_audit_xlsx'],
    ['workflow-proofreading-export-button', 'proofreading_docx_revisions'],
  ] as const) {
    await page.getByTestId(buttonId).click()
    const download = page.waitForEvent('download')
    await page.getByTestId(`export-${format}`).click()
    await expect(page.getByRole('progressbar', { name: '校对文件导出进度' })).toBeVisible()
    await expect(page.getByTestId('workbench-export-button')).toBeDisabled()
    await download
    await expect(page.getByTestId('workbench-export-button')).toBeEnabled()
  }
  expect(formats).toEqual(['proofreading_audit_xlsx', 'proofreading_docx_revisions'])
  expect(errors).toEqual([])
})

test('缺失 Word 原件时保留 Excel 并展示不可用原因', async ({ page }) => {
  await mockApi(page, false, { wordUnavailable: true })
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=workbench')
  await page.getByTestId('workbench-export-button').click()
  await expect(page.getByTestId('export-proofreading_audit_xlsx')).toBeEnabled()
  await expect(page.getByTestId('export-proofreading_docx_revisions')).toBeDisabled()
  await expect(page.getByTestId('workflow-proofreading-export-options')).toContainText('Word 源文件缺失')
})

test('LLM 运行期间两个入口均禁止校对导出', async ({ page }) => {
  await mockApi(page, false, { batchStatus: 'running' })
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=workbench')
  await expect(page.getByTestId('workflow-proofreading-export-button')).toBeDisabled()
  await page.getByTestId('workbench-export-button').click()
  await expect(page.getByTestId('export-proofreading_audit_xlsx')).toBeDisabled()
  await expect(page.getByTestId('export-proofreading_docx_revisions')).toBeDisabled()
})

test('普通翻译阶段保持原有导出菜单', async ({ page }) => {
  await mockApi(page, false, { normalWorkflow: true })
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=workbench')
  await page.getByTestId('workbench-export-button').click()
  await expect(page.getByTestId('workflow-proofreading-export-options')).toHaveCount(0)
  await expect(page.getByText('按原文件格式导出当前译文')).toBeVisible()
  await expect(page.locator('summary', { hasText: '其他导出' })).toHaveCount(0)
})

test('校对导出等待当前人工编辑保存成功', async ({ page }) => {
  await mockApi(page)
  const events: string[] = []
  await page.route('**/file-records/file-1/segments', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback()
    const update = route.request().postDataJSON().updates[0]
    expect(update.target_text).toBe('Manually saved translation')
    events.push('saved')
    await route.fulfill({ json: { updated_count: 1, conflicts: [], segments: [
      { ...segments[0], target_text: update.target_text, target_html: update.target_html, version: 2 },
    ] } })
  })
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/export-tasks')) events.push('export')
  })
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=workbench')
  await page.locator('[data-segment-target="true"]').first().fill('Manually saved translation')
  await page.getByTestId('workflow-proofreading-export-button').click()
  const download = page.waitForEvent('download')
  await page.getByTestId('export-proofreading_docx_revisions').click()
  await download
  expect(events).toEqual(['saved', 'export'])
})

test('人工编辑保存失败时不创建校对导出任务', async ({ page }) => {
  await mockApi(page)
  const exports: string[] = []
  await page.route('**/file-records/file-1/segments', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback()
    await route.fulfill({ status: 400, json: { detail: '测试保存失败' } })
  })
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/export-tasks')) exports.push(request.url())
  })
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=workbench')
  await page.locator('[data-segment-target="true"]').first().fill('Unsaved translation')
  await page.getByTestId('workflow-proofreading-export-button').click()
  await page.getByTestId('export-proofreading_audit_xlsx').click()
  await expect(page.getByText('当前编辑尚未保存，已取消导出。', { exact: true })).toBeVisible()
  expect(exports).toEqual([])
})
