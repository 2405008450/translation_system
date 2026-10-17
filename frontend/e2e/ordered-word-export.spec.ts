import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

for (const stage of ['alignment', 'proofreading']) {
  for (const xlsx of [false, true]) {
    test(`${stage}/${xlsx ? 'XLSX' : 'DOCX'}：当前编辑保存后下载整个批次`, async ({ page }) => {
      await page.goto(`/e2e/alignment-harness.html?stage=${stage}${xlsx ? '&xlsx' : ''}`)
      const editor = page.locator('#segment-align-pair-40 [data-segment-target="true"]')
      await page.evaluate(() => (window as any).alignmentHarness.setSaveDelay(350))
      await editor.fill('当前保存的人工译文')
      const trigger = page.getByTestId(stage === 'alignment' ? 'proofreading-export-alignment-button' : 'proofreading-export-button')
      await trigger.click()
      const downloadPromise = page.waitForEvent('download')
      await page.getByRole('button', { name: /上下对照 Word 按当前对齐顺序/ }).click()
      await expect(trigger).toBeDisabled()
      const download = await downloadPromise
      expect(download.suggestedFilename()).toBe('source_上下对照.docx')
      // 接口模拟只返回快照 JSON；真实 DOCX 包结构由后端回归测试验证。
      const snapshot = JSON.parse(await readFile((await download.path())!, 'utf8'))
      expect(snapshot.format).toBe('proofreading_docx_ordered')
      expect(snapshot.rows).toHaveLength(105)
      expect(snapshot.rows[40].target).toBe('当前保存的人工译文')
      expect(snapshot.rows[104].source).toContain('原文 104')
    })
  }

  test(`${stage}：项目批次列表可下载上下对照 Word`, async ({ page }) => {
    await page.goto(`/e2e/alignment-harness.html?batches&stage=${stage}`)
    const downloadPromise = page.waitForEvent('download')
    await page.getByTestId('batch-export-ordered-word').click()
    expect((await downloadPromise).suggestedFilename()).toBe('source_上下对照.docx')
  })
}

test('保存失败时保留编辑，不创建导出任务', async ({ page }) => {
  await page.goto('/e2e/alignment-harness.html')
  await page.evaluate(() => (window as any).alignmentHarness.setFailSave(true))
  const editor = page.locator('#segment-align-pair-40 [data-segment-target="true"]')
  await editor.fill('保存失败的人工译文')
  await page.getByTestId('proofreading-export-alignment-button').click()
  await page.getByRole('button', { name: /上下对照 Word 按当前对齐顺序/ }).click()
  await expect(page.getByTestId('proofreading-export-alignment-button')).toBeEnabled()
  await expect(editor).toHaveText('保存失败的人工译文')
  const tasks = await page.evaluate(() => (window as any).alignmentHarness.requests.filter((r: any) => r.method === 'post' && r.url.endsWith('/export-tasks')))
  expect(tasks).toHaveLength(0)
})

test('生成中及结构结果尚未刷新时禁用导出', async ({ page }) => {
  await page.goto('/e2e/alignment-harness.html?stage=proofreading&generating')
  await expect(page.getByTestId('proofreading-export-button')).toBeDisabled()
  await page.goto('/e2e/alignment-harness.html?batches&generating')
  await expect(page.getByTestId('batch-export-ordered-word')).toBeDisabled()
  await page.goto('/e2e/alignment-harness.html')
  await page.locator('#segment-align-pair-40 [data-segment-target="true"]').click()
  await page.evaluate(() => (window as any).alignmentHarness.setFailRefresh(true))
  await page.locator('.workbench-ribbon__top-actions').getByRole('button', { name: '拆分', exact: true }).click()
  await expect(page.getByTestId('alignment-retry-refresh')).toBeVisible()
  await expect(page.getByTestId('proofreading-export-alignment-button')).toBeDisabled()
})

test('有待处理项目时沿用导出提示，仍可下载文档', async ({ page }) => {
  await page.goto('/e2e/alignment-harness.html?warnings')
  await page.getByTestId('proofreading-export-alignment-button').click()
  await page.getByRole('button', { name: /上下对照 Word 按当前对齐顺序/ }).click()
  const downloadPromise = page.waitForEvent('download')
  await page.getByTestId('confirm-accept').click()
  expect((await downloadPromise).suggestedFilename()).toBe('source_上下对照.docx')
})

test('搜索筛选后仍导出整个批次', async ({ page }) => {
  await page.goto('/e2e/alignment-harness.html?page=2')
  await expect(page.locator('#segment-align-pair-104')).toBeVisible()
  await page.locator('.workbench-ribbon__top-actions').getByRole('button', { name: '查找', exact: true }).click()
  await page.getByPlaceholder('输入原文句段关键词', { exact: true }).fill('原文 104')
  await expect(page.locator('#segment-align-pair-100')).toHaveCount(0)
  await page.getByTestId('proofreading-export-alignment-button').click()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: /上下对照 Word 按当前对齐顺序/ }).click()
  const download = await downloadPromise
  const snapshot = JSON.parse(await readFile((await download.path())!, 'utf8'))
  expect(snapshot.rows).toHaveLength(105)
  expect(snapshot.rows[0].source).toContain('原文 0')
})
