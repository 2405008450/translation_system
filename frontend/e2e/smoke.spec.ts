import path from 'node:path'

import { expect, test } from './test-fixtures'

const ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME || 'e2e_admin'
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'E2ePass123!'
const FIXTURE_FILE = path.resolve(process.cwd(), 'e2e', 'fixtures', 'smoke-source.txt')

async function initializeAdmin(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await expect(page.getByTestId('auth-form')).toBeVisible()
  await page.getByTestId('auth-username').fill(ADMIN_USERNAME)
  await page.getByTestId('auth-password').fill(ADMIN_PASSWORD)
  await expect(page.getByTestId('auth-submit')).toBeEnabled()
  await Promise.all([
    page.waitForURL(/\/projects(?:$|\?)/),
    page.getByTestId('auth-submit').click(),
  ])
}

async function login(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await expect(page.getByTestId('auth-form')).toBeVisible()
  await page.getByTestId('auth-username').fill(ADMIN_USERNAME)
  await page.getByTestId('auth-password').fill(ADMIN_PASSWORD)
  await expect(page.getByTestId('auth-submit')).toBeEnabled()
  await Promise.all([
    page.waitForURL(/\/projects(?:$|\?)/),
    page.getByTestId('auth-submit').click(),
  ])
}

test.describe.serial('核心 E2E 冒烟流程', () => {
  test('未登录访问项目页会跳转到登录页', async ({ page }) => {
    await page.goto('/projects')
    await expect(page).toHaveURL(/\/login/)
  })

  test('首次无用户时可以初始化管理员', async ({ page }) => {
    await initializeAdmin(page)
    await expect(page.getByTestId('project-table')).toBeVisible()
  })

  test('系统初始化后可以重新登录', async ({ page }) => {
    await login(page)
    await expect(page.getByTestId('project-table')).toBeVisible()
  })

  test('创建项目、上传 TXT、编辑保存并导出', async ({ page }) => {
    const projectName = `E2E Smoke ${Date.now()}`
    const translatedText = `E2E translated text ${Date.now()}`

    await login(page)

    await page.getByTestId('project-create-button').click()
    await expect(page.getByTestId('project-create-dialog')).toBeVisible()
    await page.getByTestId('project-create-name').fill(projectName)
    await Promise.all([
      page.waitForURL(/\/projects\/[0-9a-f-]+/i),
      page.getByTestId('project-create-submit').click(),
    ])

    await page.getByTestId('project-upload-open').click()
    await expect(page.getByTestId('project-upload-page')).toBeVisible()
    await page.getByTestId('project-upload-file-input').setInputFiles(FIXTURE_FILE)
    await page.getByTestId('project-upload-source-language').selectOption('zh-CN')
    await page.getByTestId('project-upload-target-language').selectOption('en-US')
    await expect(page.getByTestId('project-upload-submit')).toBeEnabled()
    await page.getByTestId('project-upload-submit').click()

    await expect(page.getByTestId('project-upload-page')).toBeHidden({ timeout: 45_000 })
    await expect(page.getByTestId('project-file-table')).toContainText('smoke-source.txt')

    const focusPagePromise = page.waitForEvent('popup')
    await page.getByTestId('project-file-open-workbench-focus').click()
    const focusPage = await focusPagePromise
    await expect(focusPage).toHaveURL(/\/tasks\/[0-9a-f-]+\/focus/i)
    await expect(focusPage.getByTestId('workbench-page')).toBeVisible()
    await expect(focusPage.locator('.app-sidebar')).toHaveCount(0)
    await expect(focusPage.locator('.shell-header')).toHaveCount(0)
    await expect(focusPage.getByTestId('workbench-ribbon')).toBeVisible()

    const focusEditor = focusPage.getByTestId('segment-target-editor').first()
    await expect(focusEditor).toBeVisible()
    await focusEditor.fill(translatedText)

    const focusSaveResponse = focusPage.waitForResponse((response) => (
      response.url().includes('/segments')
      && response.request().method() === 'PUT'
      && response.status() < 400
    ))
    await focusPage.getByTestId('workbench-save-button').click()
    await focusSaveResponse

    await focusPage.reload()
    await expect(focusPage.getByTestId('segment-target-editor').first()).toContainText(translatedText)

    const focusDownloadPromise = focusPage.waitForEvent('download')
    await expect(focusPage.getByTestId('workbench-export-button')).toBeEnabled()
    await focusPage.getByTestId('workbench-export-button').click()
    const focusDownload = await focusDownloadPromise
    expect(focusDownload.suggestedFilename()).toContain('smoke-source')
    await focusPage.close()

    await page.getByTestId('project-file-open-workbench').click()
    await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]+/i)
    await expect(page.getByTestId('workbench-page')).toBeVisible()
    await expect(page.locator('.app-sidebar')).toBeVisible()
    await expect(page.locator('.shell-header')).toBeVisible()
    await expect(page.getByTestId('segment-target-editor').first()).toContainText(translatedText)
  })
})
