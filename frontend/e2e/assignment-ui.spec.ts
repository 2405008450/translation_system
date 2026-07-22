import { expect, test } from '@playwright/test'

test('大量文件与译者下保持单文件列表并锁定已分配文件', async ({ page }) => {
  const pageErrors: Error[] = []
  page.on('pageerror', (error) => pageErrors.push(error))
  await page.goto('/e2e/assignment-harness.html')
  expect(pageErrors).toEqual([])
  await expect(page.getByTestId('assignment-workbench')).toBeVisible()
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
  expect(compactLayout.userColumns).toBe(2)
  expect(compactLayout.userButtonHeight).toBeLessThanOrEqual(40)
  expect(compactLayout.typeSelectWidth).toBeLessThanOrEqual(120)

  const renderedRows = page.getByTestId('assignment-file-row')
  await expect(renderedRows).not.toHaveCount(0)
  expect(await renderedRows.count()).toBeLessThan(40)

  await page.getByLabel('文件分配状态').selectOption('assigned')
  const wholeFileRow = page.getByTestId('assignment-file-row').filter({ hasText: '001_公司年度可持续发展报告' })
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
