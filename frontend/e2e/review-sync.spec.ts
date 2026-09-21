import { test, expect } from '@playwright/test'

for (const merge of [false, true]) {
  test(`${merge ? '合并视图同名句段' : '单文件'}失焦保存、刷新修订并联动拒绝`, async ({ page }) => {
    await page.goto(`/e2e/review-sync-harness.html${merge ? '?merge' : ''}`)
    const first = page.getByTestId('one').locator('input')
    const second = page.getByTestId('two').locator('input')
    await first.fill('B')
    await second.focus()
    await expect(second).toHaveValue('B', { timeout: 10000 })
    await expect(page.getByTestId('trace-two')).toHaveText('A→B')
    const requests = await page.evaluate(() => (window as any).reviewHarness.requests)
    const save = requests.findIndex((r: any) => r.method === 'put' && r.url.endsWith('/segments'))
    const sync = requests.findIndex((r: any) => r.url.includes('/project-sync'))
    expect(sync).toBeGreaterThan(save)
    expect(requests[sync].body).toMatchObject({ mode: 'review', group_id: 'group1', expected_version: 2 })
    await page.getByTestId('two').getByRole('button', { name: '拒绝关联修订' }).click()
    await expect(first).toHaveValue('A')
    await expect(second).toHaveValue('A')
    await expect(page.getByTestId('trace-one')).toHaveText('')
    await expect(page.getByTestId('trace-two')).toHaveText('')
  })
}

test('请求失败保留当前编辑并可重试', async ({ page }) => {
  await page.goto('/e2e/review-sync-harness.html')
  await expect(page.getByTestId('one')).toBeVisible()
  await page.evaluate(() => (window as any).reviewHarness.setFailure(true))
  await page.getByTestId('one').locator('input').fill('B')
  await page.getByTestId('two').locator('input').focus()
  await expect(page.getByRole('button', { name: '重试修订同步' })).toBeVisible()
  await expect(page.getByTestId('one').locator('input')).toHaveValue('B')
  await page.evaluate(() => (window as any).reviewHarness.setFailure(false))
  await page.getByRole('button', { name: '重试修订同步' }).click()
  await expect(page.getByTestId('two').locator('input')).toHaveValue('B', { timeout: 10000 })
})
