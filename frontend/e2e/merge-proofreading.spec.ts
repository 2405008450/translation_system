import { expect, test, type Page } from '@playwright/test'

async function setup(page: Page, options: { running?: boolean; failure?: boolean } = {}) {
  const requests: Array<{ path: string; body: any }> = []
  const errors: string[] = []
  let runs: any[] = []
  let finishNextRead = false
  page.on('pageerror', error => errors.push(error.message))
  const workflow = [{ id: 'review', name: '审校', step_key: 'review', step_type: 'review', sort_order: 1 },
    { id: 'proofread', name: '校对', step_key: 'proofread', step_type: 'proofread', sort_order: 2 }]
  const files = [1, 2, 3].map(index => ({ id: `file-${index}`, filename: `文件${index}.txt`,
    total_segments: 1, status_stats: { total: 1, none: 1, confirmed: 0, exact: 0, fuzzy: 0 },
    status: 'in_progress', progress: 0, can_write: true, is_edit_locked: false,
    source_language: 'en-US', target_language: 'fr-FR', collection_ids: [], term_base_ids: [],
    qa_term_base_ids: [], term_base_write_ids: [],
  }))
  const rows = files.map((file, index) => ({ id: `segment-${index}`, file_record_id: file.id, filename: file.filename,
    sentence_id: `s${index}`, source_text: `Source ${index}`, display_text: `Source ${index}`,
    target_text: `Target ${index}`, status: 'none', source: 'manual', version: 1,
    display_index: index, sequence_index: 0, workflow_step_id: 'proofread', workflow_step_name: '校对',
    can_write: true, score: 0, block_type: 'paragraph', block_index: index, qa_issues: [],
  }))
  const counts = (file: typeof files[number]) => ({ file_record_id: file.id, filename: file.filename,
    pending: 1, reviewed: 0, protected: 0, failed: 0, skipped_reason: null, available_formats: ['proofreading_audit_xlsx'],
    readiness: { unconfirmed: 1, missing_translation: 0, llm_failed: 0 } })
  const run = (kind = 'proofreading') => ({ id: kind === 'export' ? 'export-1' : 'run-1', kind, status: 'running', progress: 0,
    cancel_requested: false, can_cancel: true, download_available: false,
    files: files.map(file => ({ file_record_id: file.id, filename: file.filename, status: 'pending', progress: 0,
      message: '等待处理', counts: counts(file) })) })
  if (options.running) runs = [run()]
  await page.route('http://127.0.0.1:5182/api/**', async route => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api', '')
    const method = route.request().method()
    let data: unknown = []
    if (path.startsWith('/merge-views/view-1/proofreading')) {
      const body = method === 'POST' ? route.request().postDataJSON() : undefined
      requests.push({ path, body })
      if (path.endsWith('/context')) {
        const selected = url.searchParams.get('file_ids')?.split(',') || files.map(file => file.id)
        data = { files: files.filter(file => selected.includes(file.id)).map((file, index) => ({
          file_record_id: file.id, is_proofreading: false,
          proofreading_context: { batch_id: `batch-${file.id}`, batch_kind: 'workflow_segments', workflow_step_id: 'proofread',
            batch_status: 'ready', can_generate: true, provider: 'openrouter', model: '', user_instructions: '', failed_segments: 0 },
          items: [{ segment_id: rows[index]!.id, sentence_id: rows[index]!.sentence_id, original_target_text: `Old ${index}`,
            llm_reviewed: true, review_suggestion: `文件${index + 1}修改理由`, review_category: 'accuracy', review_confidence: 'high' }],
        })) }
      } else if (path.endsWith('/preview')) {
        if (options.failure) return route.fulfill({ status: 500, json: { detail: '预览失败' } })
        const selected = body.file_id ? files.filter(file => file.id === body.file_id) : files
        data = { files: selected.map(counts), pending: selected.length, reviewed: 0, protected: 0 }
      } else if (path.endsWith('/export-readiness')) {
        data = { files: files.map(counts), available_formats: ['proofreading_audit_xlsx'],
          format_file_counts: { proofreading_audit_xlsx: 3 }, has_warnings: false }
      } else if (path.endsWith('/export-tasks')) {
        runs = [run('export')]
        data = runs[0]
        finishNextRead = true
      } else if (path.endsWith('/download')) {
        return route.fulfill({ body: 'zip content', contentType: 'application/zip',
          headers: { 'Content-Disposition': 'attachment; filename="proofreading.zip"' } })
      } else if (path.endsWith('/cancel')) {
        runs[0] = { ...runs[0], status: 'canceled', can_cancel: false, cancel_requested: true }
        data = runs[0]
      } else if (path.endsWith('/runs') && method === 'POST') {
        runs = [run()]
        if (body.file_id) runs[0].files = runs[0].files.filter((file: any) => file.file_record_id === body.file_id)
        data = runs[0]
      } else if (path.endsWith('/runs')) {
        if (finishNextRead) {
          runs[0] = { ...runs[0], status: 'completed', progress: 100, can_cancel: false, download_available: true }
          finishNextRead = false
        }
        data = { items: runs }
      }
    } else if (path === '/merge-views/view-1') {
      data = { id: 'view-1', project_id: 'project-1', name: '三文件校对', files, file_ids: files.map(file => file.id),
        total_files: 3, total_segments: 3, language_pairs: [], workflow_steps: workflow, workflow_active_step_ids: ['proofread'] }
    } else if (path === '/merge-views/view-1/segments') {
      data = { merge_view_id: 'view-1', segments: rows, total_segments: 3, matched_segments: 3, skip: 0, limit: 100,
        groups: files.map(file => ({ file_record_id: file.id, filename: file.filename, matched_segments: 1, page_segment_count: 1 })) }
    } else if (path.endsWith('/revision-settings')) data = { track_changes: true }
    else if (path.endsWith('/segments/changes')) data = { segments: [], has_more: false }
    else if (path.endsWith('/segments/events')) return route.fulfill({ status: 404, body: '' })
    else if (path.endsWith('/comments')) data = { items: [], total: 0 }
    await route.fulfill({ json: data })
  })
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=merge')
  await expect(page.getByTestId('workflow-proofreading-actions')).toBeVisible()
  return { requests, errors }
}

test('三文件均加载自己的修改理由，默认补齐整个视图', async ({ page }) => {
  const { requests, errors } = await setup(page)
  await expect(page.getByTitle('文件2修改理由', { exact: true })).toBeVisible()
  await expect(page.getByTitle('文件3修改理由', { exact: true })).toBeVisible()
  await expect(page.getByLabel('校对操作范围')).toHaveValue('merge_view')
  await page.getByRole('button', { name: '开始 LLM 校对', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('radio', { name: /补齐尚未校对/ })).toBeChecked()
  await expect(dialog.getByTestId('merge-proofreading-preview')).toContainText('待校对 3 条')
  await expect(dialog.getByTestId('merge-proofreading-preview')).toContainText('文件3.txt')
  await dialog.getByRole('button', { name: '开始 LLM 校对', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(requests.find(request => request.path.endsWith('/runs') && request.body)?.body)
    .toMatchObject({ retry_scope: 'unreviewed' })
  await expect(page.getByTestId('merge-proofreading-tasks')).toContainText('文件3.txt')
  expect(errors).toEqual([])
})

test('当前文件只提交选中文件并可切换失败重试', async ({ page }) => {
  const { requests } = await setup(page)
  await page.getByLabel('校对操作范围').selectOption('current_file')
  await page.getByRole('button', { name: '开始 LLM 校对', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByTestId('merge-proofreading-preview')).toContainText('待校对 1 条')
  await dialog.getByRole('radio', { name: /仅重试生成失败项/ }).check()
  await dialog.getByRole('button', { name: '开始 LLM 校对', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(requests.find(request => request.path.endsWith('/runs') && request.body)?.body)
    .toMatchObject({ file_id: 'file-1', retry_scope: 'failed_only' })
})

test('刷新页面恢复进度并取消持久化任务', async ({ page }) => {
  const { requests, errors } = await setup(page, { running: true })
  await expect(page.getByTestId('merge-proofreading-tasks')).toContainText('文件3.txt')
  await page.reload()
  await expect(page.getByTestId('merge-proofreading-tasks')).toContainText('文件3.txt')
  await page.getByTestId('merge-proofreading-tasks').getByRole('button', { name: '取消任务' }).click()
  await expect(page.getByTestId('merge-proofreading-tasks')).toContainText('已取消')
  expect(requests.some(request => request.path.endsWith('/run-1/cancel'))).toBeTruthy()
  expect(errors).toEqual([])
})

test('整个视图按文件数量提供格式并下载 ZIP', async ({ page }) => {
  const { requests, errors } = await setup(page)
  await page.getByTestId('workflow-proofreading-export-button').click()
  await expect(page.getByTestId('export-proofreading_audit_xlsx')).toContainText('3 个文件')
  await page.getByTestId('export-proofreading_audit_xlsx').click()
  await expect(page.getByRole('button', { name: '下载校对文件 ZIP' })).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载校对文件 ZIP' }).click()
  expect((await download).suggestedFilename()).toBe('proofreading.zip')
  expect(requests.some(request => request.path.endsWith('/export-tasks'))).toBeTruthy()
  expect(errors).toEqual([])
})

test('预览失败不可启动批量校对', async ({ page }) => {
  const { requests } = await setup(page, { failure: true })
  await page.getByRole('button', { name: '开始 LLM 校对', exact: true }).click()
  await expect(page.getByRole('dialog').getByRole('button', { name: '开始 LLM 校对', exact: true })).toBeDisabled()
  expect(requests.some(request => request.path.endsWith('/runs') && request.body)).toBeFalsy()
})
