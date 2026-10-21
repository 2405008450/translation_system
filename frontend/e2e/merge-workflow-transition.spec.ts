import { expect, test, type Page } from '@playwright/test'

async function setup(page: Page, options: { skipped?: boolean; previewFailure?: boolean; firstAtEnd?: boolean } = {}) {
  const errors: string[] = []
  const requests: Array<{ path: string; body: any }> = []
  let detailLoads = 0
  let pageLoads = 0
  page.on('pageerror', (error) => { errors.push(error.message); console.error(error.stack) })
  const steps = [
    { id: 'review', step_key: 'review', name: '审校', step_type: 'review', sort_order: 1 },
    { id: 'proofread', step_key: 'proofread', name: '校对', step_type: 'proofread', sort_order: 2 },
  ]
  const stats = { total: 2, confirmed: 0, none: 2, exact: 0, fuzzy: 0, empty_target: 0 }
  const files = [1, 2].map((id) => ({ id: `file-${id}`, filename: `文件${id}.txt`, total_segments: 2,
    status: 'in_progress', status_stats: stats, progress: 0, can_write: true, is_edit_locked: false,
    source_language: 'en-US', target_language: 'fr-FR', collection_ids: [], term_base_ids: [],
    qa_term_base_ids: [], term_base_write_ids: [],
  }))
  const rows = files.flatMap((file, fileIndex) => [0, 1].map((index) => ({
    id: `${file.id}-${index}`, file_record_id: file.id, filename: file.filename, sentence_id: `s${index}`,
    source_text: `Source ${fileIndex}-${index}`, display_text: `Source ${fileIndex}-${index}`,
    target_text: `Traduction ${index}`, status: 'none', source: 'manual', version: 1,
    display_index: fileIndex * 2 + index, sequence_index: index, workflow_step_id: 'review',
    workflow_step_name: '审校', can_write: true, score: 0, block_type: 'paragraph', block_index: index, qa_issues: [],
  })))
  if (options.firstAtEnd) rows[0]!.workflow_step_id = 'proofread'
  await page.route('http://127.0.0.1:5182/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api', '')
    let data: unknown = []
    if (path.includes('/workflow/transition')) {
      const body = route.request().postDataJSON()
      requests.push({ path, body })
      if (options.previewFailure && path.endsWith('/preview')) return route.fulfill({ status: 500, json: { detail: '模拟预览失败' } })
      const preview = path.endsWith('/preview')
      const perFile = files.map((file, index) => ({ file_record_id: file.id, filename: file.filename,
        matched_count: options.skipped && index === 1 ? 0 : 2,
        updated_count: preview ? 0 : 2, skipped_reason: options.skipped && index === 1 ? '文件正在被其他任务锁定。' : null }))
      const merge = path.startsWith('/merge-views')
      data = { matched_count: merge ? options.skipped ? 2 : 4 : 2, updated_count: merge ? options.skipped ? 2 : 4 : 2,
        ...(merge ? { files: perFile } : {}) }
      if (!preview) for (const row of rows) {
        if (merge || path.includes(row.file_record_id)) row.workflow_step_id = body.target_step_id
      }
    } else if (path === '/merge-views/view-1') {
      detailLoads++
      data = { id: 'view-1', project_id: 'project-1', name: '测试合并视图', file_ids: files.map((file) => file.id),
        files, total_files: 2, total_segments: 4, workflow_steps: steps, workflow_active_step_ids: [...new Set(rows.map((row) => row.workflow_step_id))], language_pairs: [], is_mixed_language_pair: false }
    } else if (path === '/merge-views/view-1/segments') {
      pageLoads++
      data = { merge_view_id: 'view-1', segments: rows, total_segments: 4, matched_segments: 4, skip: 0, limit: 100,
        groups: files.map((file) => ({ file_record_id: file.id, filename: file.filename, matched_segments: 2, page_segment_count: 2 })) }
    } else if (path.endsWith('/proofreading-baselines')) data = { is_proofreading: false, proofreading_context: null, items: [] }
    else if (path.endsWith('/segments/changes')) data = { segments: [], items: [], has_more: false }
    else if (path.endsWith('/segments/events')) return route.fulfill({ status: 404, body: '' })
    else if (path.endsWith('/comments')) data = { items: [], total: 0 }
    else if (path.endsWith('/revision-settings')) data = { track_changes: true }
    else if (path.endsWith('/prepare')) data = {}
    else if (path.includes('/reference-match')) data = { items: [], matches: [] }
    await route.fulfill({ json: data })
  })
  await page.goto('/e2e/workflow-proofreading-harness.html?mode=merge')
  const forward = page.getByRole('button', { name: '前进流程', exact: true })
  await expect(forward).toBeEnabled()
  if (!options.firstAtEnd) await expect(page.getByRole('button', { name: '退回流程', exact: true })).toBeDisabled()
  await forward.click()
  const dialog = page.getByRole('dialog')
  return { errors, requests, dialog, loads: () => ({ detailLoads, pageLoads }) }
}

test('合并视图默认批量流转并刷新文件和句段', async ({ page }) => {
  const { errors, requests, dialog, loads } = await setup(page)
  await expect(dialog.getByLabel('操作范围')).toHaveValue('merge_view')
  await expect(dialog.getByText('已查询出 4 个句段')).toBeVisible()
  await dialog.getByRole('button', { name: '前进句段', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(requests.some((request) => request.path === '/merge-views/view-1/workflow/transition')).toBeTruthy()
  expect(loads().detailLoads).toBeGreaterThan(1)
  expect(loads().pageLoads).toBeGreaterThan(1)
  await expect(page.getByRole('button', { name: '前进流程', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: '退回流程', exact: true }).click()
  await expect(page.getByRole('dialog').getByText('已查询出 4 个句段')).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: '退回句段', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(requests.filter((request) => !request.path.endsWith('/preview')).at(-1)?.body)
    .toMatchObject({ from_step_id: 'proofread', target_step_id: 'review' })
  expect(errors).toEqual([])
})

test('切换当前文件重置范围并使用单文件接口', async ({ page }) => {
  const { errors, requests, dialog } = await setup(page)
  await expect(dialog.getByText('已查询出 4 个句段')).toBeVisible()
  await dialog.getByText('全部句段', { exact: true }).click()
  await dialog.locator('input[type=number]').first().fill('2')
  await dialog.getByLabel('操作范围').selectOption('current_file')
  await expect(dialog.locator('input[type=checkbox]').first()).toBeChecked()
  await expect(dialog.locator('input[type=number]').first()).toHaveValue('1')
  await expect(dialog.locator('input[type=number]').last()).toHaveValue('2')
  await expect(dialog.getByText('已查询出 2 个句段')).toBeVisible()
  await dialog.getByRole('button', { name: '前进句段', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(requests.filter((request) => !request.path.endsWith('/preview')).map((request) => request.path))
    .toEqual(['/file-records/file-1/workflow/transition'])
  expect(errors).toEqual([])
})

test('显示跳过明细并保留提交结果', async ({ page }) => {
  const { errors, dialog } = await setup(page, { skipped: true })
  await expect(dialog.getByText('文件2.txt：跳过，文件正在被其他任务锁定。')).toBeVisible()
  await dialog.getByRole('button', { name: '前进句段', exact: true }).click()
  await expect(dialog.getByRole('status')).toHaveText('已流转 2 个句段，跳过 1 个文件')
  await expect(dialog.getByRole('button', { name: '前进句段', exact: true })).toBeDisabled()
  expect(errors).toEqual([])
})

test('预览失败禁止提交', async ({ page }) => {
  const { dialog, requests } = await setup(page, { previewFailure: true })
  await expect(dialog.getByText('暂无有效预览，请检查流转条件。')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '前进句段', exact: true })).toBeDisabled()
  expect(requests.every((request) => request.path.endsWith('/preview'))).toBeTruthy()
})


test('忽略过期预览且加载期间禁止提交', async ({ page }) => {
  const { dialog } = await setup(page)
  await expect(dialog.getByText('已查询出 4 个句段')).toBeVisible()
  let releaseOld!: () => void
  let markStarted!: () => void
  let markFinished!: () => void
  const started = new Promise<void>((resolve) => { markStarted = resolve })
  const finished = new Promise<void>((resolve) => { markFinished = resolve })
  await page.route('**/merge-views/view-1/workflow/transition/preview', async (route) => {
    if (route.request().postDataJSON().target_status === 'confirmed') {
      markStarted()
      await new Promise<void>((resolve) => { releaseOld = resolve })
      await route.fulfill({ json: { matched_count: 99 } })
      markFinished()
    } else await route.fulfill({ json: { matched_count: 1 } })
  })
  await dialog.getByLabel('目标状态', { exact: true }).selectOption('confirmed')
  await started
  await expect(dialog.getByRole('button', { name: '前进句段', exact: true })).toBeDisabled()
  await dialog.getByLabel('目标状态', { exact: true }).selectOption('unconfirmed')
  await expect(dialog.getByText('已查询出 1 个句段')).toBeVisible()
  releaseOld()
  await finished
  await expect(dialog.getByText('已查询出 1 个句段')).toBeVisible()
  await expect(dialog.getByText('已查询出 99 个句段')).toHaveCount(0)
})

test('编辑保存失败时停止流程提交', async ({ page }) => {
  const { dialog, requests } = await setup(page)
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await page.route('**/file-records/file-1/segments', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback()
    await route.fulfill({ status: 400, json: { detail: '测试保存失败' } })
  })
  await page.locator('[data-segment-target="true"]').first().fill('Unsaved translation')
  await page.getByRole('button', { name: '前进流程', exact: true }).click()
  await expect(dialog.getByText('已查询出 4 个句段')).toBeVisible()
  await dialog.getByRole('button', { name: '前进句段', exact: true }).click()
  await expect(page.getByText('待编辑内容保存失败，请保存成功后重试流程流转。', { exact: true })).toBeVisible()
  expect(requests.every((request) => request.path.endsWith('/preview'))).toBeTruthy()
})


test('首句已到末阶段时仍可推进其他文件的句段', async ({ page }) => {
  const { dialog, requests, errors } = await setup(page, { firstAtEnd: true })
  await expect(dialog.getByText('已查询出 4 个句段')).toBeVisible()
  expect(requests.at(-1)?.body).toMatchObject({ from_step_id: 'review', target_step_id: 'proofread' })
  expect(errors).toEqual([])
})
