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

test('模型下拉框显示小字价格，支持搜索选择并提交对应模型', async ({ page }) => {
  await page.goto('/e2e/llm-scenario-harness.html')
  await page.getByRole('button', { name: '打开预翻译' }).click()
  const model = page.getByRole('combobox', { name: '模型', exact: true })
  await expect(model).toBeDisabled()
  await page.locator('label.ptd-switch').filter({ hasText: '启用 TM 预匹配' }).click()
  await page.locator('label.ptd-switch').filter({ hasText: '启用 LLM 翻译' }).click()
  await model.click()
  const argon = page.getByRole('option', { name: /^Gemini 4 Argon/ })
  await expect(argon).toContainText('输入 $2 · 输出 $10 / 百万 token')
  await expect(argon.locator('small')).toHaveCSS('font-size', '11px')
  await expect(page.getByRole('option', { name: /^Gemini 3.8 Flash/ })).toContainText('输入 $0.825 · 输出 $4.125')
  await expect(page.getByRole('option', { name: /^使用后端默认/ })).toContainText('按实际调用的模型计费')
  await expect(page.getByText('OpenRouter 报价 · 更新于 2026-10-21', { exact: true })).toBeVisible()
  await expect(page.getByRole('listbox', { name: '模型', exact: true })).toHaveCSS('opacity', '1')
  await page.screenshot({ path: 'test-results/llm-model-pricing-desktop.png', fullPage: true, animations: 'disabled' })
  await model.fill('GPT-6 Luna')
  const filteredOptions = page.getByRole('listbox', { name: '模型', exact: true }).getByRole('option')
  await expect(filteredOptions).toHaveCount(1)
  await expect(filteredOptions).toContainText('输入 $0.1 · 输出 $0.5 / 百万 token')
  await model.press('ArrowDown')
  await model.press('Enter')
  await expect(page.getByRole('listbox', { name: '模型', exact: true })).toBeHidden()
  await page.getByRole('button', { name: '开始预翻译', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).scenarioHarness.requests.length)).toBe(1)
  const requests = await page.evaluate(() => (window as any).scenarioHarness.requests)
  expect(requests[0].body).toMatchObject({ llm_model: 'openai/gpt-6-luna', llm_provider: 'openrouter' })
})

test('模型价格下拉框在窄屏和深色模式下可见，Escape 仅收起列表', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/e2e/llm-scenario-harness.html')
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark' })
  await page.getByRole('button', { name: '打开预翻译' }).click()
  await page.locator('label.ptd-switch').filter({ hasText: '启用 LLM 翻译' }).click()
  const model = page.getByRole('combobox', { name: '模型', exact: true })
  await model.click()
  const menu = page.getByRole('listbox', { name: '模型', exact: true })
  await expect(menu).toBeVisible()
  await expect(menu).toHaveCSS('opacity', '1')
  const box = await menu.boundingBox()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(391)
  expect(box!.y).toBeGreaterThanOrEqual(0)
  expect(box!.y + box!.height).toBeLessThanOrEqual(844)
  await page.screenshot({ path: 'test-results/llm-model-pricing-mobile-dark.png', fullPage: true, animations: 'disabled' })
  await model.press('Escape')
  await expect(menu).toBeHidden()
  await expect(page.getByRole('dialog')).toBeVisible()
})
