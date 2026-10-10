import { expect, test } from '@playwright/test'

for (const merge of [false, true]) {
  for (const scenario of ['standard', 'mixed']) {
    test(`${merge ? '合并工作台' : '单文件工作台'}传递 ${scenario} 场景且保持范围`, async ({ page }) => {
      await page.goto(`/e2e/review-sync-harness.html${merge ? '?merge' : ''}`)
      await expect(page.getByTestId('one')).toBeVisible()
      const bodies = await page.evaluate(async ({ merge, scenario }) => {
        const bodies: Array<Record<string, unknown>> = []
        const originalFetch = window.fetch
        window.fetch = (input, init) => {
          if (String(input).endsWith('/llm-translate')) {
            bodies.push(JSON.parse(String(init?.body)))
            return Promise.resolve(new Response(
              'event: start\ndata: {"total":0}\n\nevent: complete\ndata: {"updated_count":0,"error_count":0,"total":0}\n\n',
              { headers: { 'Content-Type': 'text/event-stream' } },
            ))
          }
          return originalFetch(input, init)
        }
        await (window as any).reviewHarness.store.startLLMTranslation('all', 'openrouter', {
          ...(scenario === 'mixed' ? { translationScenario: scenario } : {}),
          mergeTarget: merge ? 'merge_view' : 'current_file',
        })
        return bodies
      }, { merge, scenario })
      expect(bodies).toHaveLength(merge ? 2 : 1)
      for (const body of bodies) expect(body).toMatchObject({ translation_scenario: scenario, scope: 'all' })
    })
  }
}

test('预翻译选择混合场景、保存后台参数并在重新打开时恢复默认', async ({ page }) => {
  await page.goto('/e2e/llm-scenario-harness.html')
  await page.getByRole('button', { name: '打开预翻译' }).click()
  const select = page.getByTestId('pretranslate-translation-scenario')
  await expect(select).toHaveValue('standard')
  await expect(select).toBeDisabled()
  await page.locator('label.ptd-switch').filter({ hasText: '启用 TM 预匹配' }).click()
  await page.locator('label.ptd-switch').filter({ hasText: '启用 LLM 翻译' }).click()
  await expect(page.getByLabel('启用 LLM 翻译', { exact: true })).toBeChecked()
  await select.selectOption('mixed')
  await expect(page.getByRole('note').filter({ hasText: '包含 100% 匹配' })).toBeVisible()
  await select.scrollIntoViewIfNeeded()
  await page.screenshot({ path: 'test-results/llm-scenario-pretranslate.png', fullPage: true })
  await page.getByRole('button', { name: '开始预翻译', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).scenarioHarness.requests.length)).toBe(1)
  const requests = await page.evaluate(() => (window as any).scenarioHarness.requests)
  expect(requests).toHaveLength(1)
  expect(requests[0].body).toMatchObject({ llm_translation_scenario: 'mixed', llm_scope: 'all' })
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await page.getByRole('button', { name: '打开预翻译' }).click()
  await expect(select).toHaveValue('standard')
})

test('预翻译显示服务端任务保存的实际场景', async ({ page }) => {
  await page.goto('/e2e/llm-scenario-harness.html?savedScenario=mixed')
  await page.getByRole('button', { name: '打开预翻译' }).click()
  await page.locator('label.ptd-switch').filter({ hasText: '启用 TM 预匹配' }).click()
  await page.locator('label.ptd-switch').filter({ hasText: '启用 LLM 翻译' }).click()
  await page.getByRole('button', { name: '开始预翻译', exact: true }).click()
  await expect(page.getByTestId('pretranslate-translation-scenario')).toHaveValue('mixed')
})
