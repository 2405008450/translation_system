import path from 'node:path'
import type { Page } from '@playwright/test'

import { expect, test } from './test-fixtures'

const ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME || 'e2e_admin'
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'E2ePass123!'
const FIXTURE_FILE = path.resolve(process.cwd(), 'e2e', 'fixtures', 'smoke-source.txt')

async function loginOrInitialize(page: Page) {
  await page.goto('/login')
  await expect(page.getByTestId('auth-form')).toBeVisible()
  await page.getByTestId('auth-username').fill(ADMIN_USERNAME)
  await page.getByTestId('auth-password').fill(ADMIN_PASSWORD)
  await Promise.all([
    page.waitForURL(/\/projects(?:$|\?)/),
    page.getByTestId('auth-submit').click(),
  ])
}

async function createProjectWithFile(page: Page, projectName: string) {
  await page.getByTestId('project-create-button').click()
  await page.getByTestId('project-create-name').fill(projectName)
  await page.getByTestId('project-create-workflow-template').selectOption('translate')
  await Promise.all([
    page.waitForURL(/\/projects\/[0-9a-f-]+/i),
    page.getByTestId('project-create-submit').click(),
  ])

  await page.getByTestId('project-upload-open').click()
  await page.getByTestId('project-upload-file-input').setInputFiles(FIXTURE_FILE)
  await page.getByTestId('project-upload-source-language').selectOption('zh-CN')
  await page.getByTestId('project-upload-target-trigger').click()
  await page.getByTestId('project-upload-target-en-US').check()
  await page.getByTestId('project-upload-submit').click()
  await expect(page.getByTestId('project-upload-page')).toBeHidden({ timeout: 45_000 })
  await expect(page.getByTestId('project-file-table')).toContainText('smoke-source.txt')
}

test('文件优先分配、变更确认与 revision 保存', async ({ page }) => {
  await loginOrInitialize(page)

  const suffix = Date.now()
  const translatorName = `指派译者${suffix}`
  const token = await page.evaluate(() => window.localStorage.getItem('token'))
  const registerResponse = await page.request.post('/api/auth/register', {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      username: `assignment_${suffix}`,
      nickname: translatorName,
      password: 'Translator123!',
      role: 'user',
      translator_type: 'external',
    },
  })
  expect(registerResponse.ok()).toBeTruthy()

  await createProjectWithFile(page, `E2E Assignment ${suffix}`)
  await page.getByTestId('project-assignment-open').click()
  await expect(page.getByTestId('assignment-workbench')).toBeVisible()

  const fileRow = page.getByTestId('assignment-file-row').filter({ hasText: 'smoke-source.txt' })
  await expect(fileRow).toHaveCount(1)
  await fileRow.getByRole('checkbox').check()

  await page.getByTestId('assignment-user-search').getByRole('searchbox').fill(translatorName)
  const translatorOption = page.getByTestId('assignment-user-list').getByRole('option').filter({ hasText: translatorName })
  await expect(translatorOption).toHaveCount(1)
  await translatorOption.click()
  await page.getByTestId('assignment-apply-button').click()

  await page.getByLabel('文件分配状态').selectOption('assigned')
  const assignedRow = page.getByTestId('assignment-file-row').filter({ hasText: 'smoke-source.txt' })
  await expect(assignedRow).toContainText(translatorName)
  await expect(assignedRow.getByRole('checkbox')).toBeDisabled()

  await page.getByTestId('assignment-save-button').click()
  await expect(page.getByRole('alertdialog', { name: '确认保存任务分配' })).toBeVisible()
  const saveResponsePromise = page.waitForResponse((response) => (
    response.url().includes('/assignments')
    && response.request().method() === 'PATCH'
  ))
  await page.getByRole('button', { name: '确认保存' }).click()
  const saveResponse = await saveResponsePromise
  expect(saveResponse.ok()).toBeTruthy()
  const requestPayload = saveResponse.request().postDataJSON()
  expect(requestPayload.base_revision).toMatch(/^[0-9a-f]{64}$/)
  await expect(page.getByTestId('assignment-workbench')).toBeHidden()
})
