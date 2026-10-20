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

async function mockApi(page: Page, standalone = false) {
  const errors: string[] = []
  page.on('pageerror', (error) => { errors.push(error.message); console.error('Browser error:', error.message) })
  await page.route('http://127.0.0.1:5182/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api', '')
    let data: unknown = []
    if (path === '/workflow-templates') data = { items: templates }
    else if (path === '/projects') data = { items: [], total: 0 }
    else if (path.endsWith('/proofreading-batches')) data = { items: [] }
    else if (path.endsWith('/prepare')) data = { batch_id: 'batch-1' }
    else if (path.endsWith('/proofreading-baselines')) data = {
      is_proofreading: standalone,
      proofreading_context: { batch_id: 'batch-1', batch_kind: standalone ? 'xlsx_columns' : 'workflow_segments',
        workflow_step_id: 'step-2',
        workflow_stage: 'proofreading', batch_status: 'ready', sheet_name: '测试资料', provider: 'auto', model: '',
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
      can_write: true, can_manage: true, is_edit_locked: false, reference_files: [],
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
