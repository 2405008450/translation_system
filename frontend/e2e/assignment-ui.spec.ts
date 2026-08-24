import { expect, test } from '@playwright/test'

test('大量文件与译者下保持单文件列表并锁定已分配文件', async ({ page }) => {
  const pageErrors: Error[] = []
  page.on('pageerror', (error) => pageErrors.push(error))
  await page.goto('/e2e/assignment-harness.html')
  expect(pageErrors).toEqual([])
  await expect(page.getByTestId('assignment-workbench')).toBeVisible()
  await expect(page.getByRole('radiogroup', { name: '译者类型' })).toHaveCount(0)
  await expect(
    page.getByTestId('assignment-user-list').getByRole('option').filter({ hasText: 'translator_05' }),
  ).toHaveCount(0)
  await expect(page.getByText('待分配 118')).toBeVisible()
  await expect(page.getByText('已分配 2')).toBeVisible()

  const compactLayout = await page.evaluate(() => {
    const modalBody = document.querySelector<HTMLElement>('.modal-overlay .modal-body')
    const userList = document.querySelector<HTMLElement>('.assignment-user-list')
    const userButton = userList?.querySelector<HTMLElement>(':scope > button')
    const typeSelect = document.querySelector<HTMLElement>('.assignment-assignee-filters > select')
    return {
      modalClientHeight: modalBody?.clientHeight ?? 0,
      modalScrollHeight: modalBody?.scrollHeight ?? 0,
      userColumns: userList ? getComputedStyle(userList).gridTemplateColumns.split(' ').length : 0,
      userButtonHeight: userButton?.getBoundingClientRect().height ?? 0,
      typeSelectWidth: typeSelect?.getBoundingClientRect().width ?? 0,
    }
  })
  expect(compactLayout.modalScrollHeight).toBeLessThanOrEqual(compactLayout.modalClientHeight + 1)
  expect(compactLayout.userColumns).toBe(1)
  expect(compactLayout.userButtonHeight).toBeLessThanOrEqual(64)
  expect(compactLayout.typeSelectWidth).toBeLessThanOrEqual(120)

  const renderedRows = page.getByTestId('assignment-file-row')
  await expect(renderedRows).not.toHaveCount(0)
  expect(await renderedRows.count()).toBeLessThan(40)

  await page.getByLabel('文件分配状态').selectOption('assigned')
  const wholeFileRow = page.getByTestId('assignment-file-row').filter({ hasText: '001_公司年度可持续发展报告' })
  await expect(wholeFileRow).toContainText('语言对：中文（简体） -> 英语（美国）')
  await expect(wholeFileRow).toContainText('测试译者 1 · 整文件')
  await expect(wholeFileRow.getByRole('checkbox')).toBeDisabled()
  await page.getByLabel('负责人筛选').selectOption('user-1')
  await expect(page.getByTestId('assignment-file-row')).toHaveCount(1)
  await page.getByLabel('负责人筛选').selectOption('all')

  await page.getByLabel('文件分配状态').selectOption('unassigned')
  const availableRow = page.getByTestId('assignment-file-row').filter({ hasText: '003_公司年度可持续发展报告' })
  await availableRow.getByRole('checkbox').check()
  await page.getByTestId('assignment-user-search').getByRole('searchbox').fill('测试译者 58')
  const filteredUserList = await page.getByTestId('assignment-user-list').evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }))
  expect(filteredUserList.scrollHeight).toBeLessThanOrEqual(filteredUserList.clientHeight)
  await page.getByTestId('assignment-user-list').getByRole('option').filter({ hasText: '测试译者 58' }).click()
  await page.getByTestId('assignment-apply-button').click()

  await expect(page.getByText('待保存：新增 2')).toBeVisible()
  await page.getByTestId('assignment-save-button').click()
  const confirmDialog = page.getByRole('alertdialog', { name: '确认保存任务分配' })
  await expect(confirmDialog).toBeVisible()
  await expect(confirmDialog).toContainText('新增分配')
})

test('按字数生成安全拆分并应用到草稿', async ({ page }) => {
  await page.goto('/e2e/assignment-harness.html')
  const fileRow = page.getByTestId('assignment-file-row').filter({
    hasText: '003_公司年度可持续发展报告',
  })
  await fileRow.getByRole('button', { name: '高级拆分' }).click()

  const splitPanel = page.getByTestId('assignment-auto-split')
  await expect(splitPanel).toContainText('系统只会在完整句段或段落边界切分')
  await page.getByRole('tab', { name: '手动指定范围' }).click()
  await expect(page.getByText('逐条指定句段范围')).toBeVisible()
  await page.getByRole('tab', { name: '智能均分' }).click()
  await expect(page.getByTestId('assignment-smart-user-user-5')).toHaveCount(0)
  await page.getByTestId('assignment-smart-user-user-1').click()
  await page.getByTestId('assignment-smart-user-user-2').click()
  await page.getByTestId('assignment-smart-user-user-3').click()
  await expect(splitPanel).toContainText('已选 3 人，将自动生成 3 份')
  await page.getByTestId('assignment-split-generate-button').click()
  await expect(page.getByTestId('assignment-split-parts').locator(':scope > div')).toHaveCount(3)
  await expect(splitPanel).toContainText('所有切点均位于完整句段边界')

  await expect(splitPanel.getByLabel('第 1 份译者')).toHaveValue('user-1')
  await expect(splitPanel.getByLabel('第 2 份译者')).toHaveValue('user-2')
  await expect(splitPanel.getByLabel('第 3 份译者')).toHaveValue('user-3')
  await page.getByTestId('assignment-split-apply-button').click()

  await expect(page.getByText('已将 003_公司年度可持续发展报告_2.docx 的 3 份安全范围应用到草稿。')).toBeVisible()
  const appliedRanges = page.locator('.assignment-current-ranges > div')
  await expect(appliedRanges.locator(':scope > span')).toHaveCount(3)
  await expect(appliedRanges).toContainText('1–271 段')
  await expect(appliedRanges).toContainText('272–542 段')
  await expect(appliedRanges).toContainText('543–814 段')
})
