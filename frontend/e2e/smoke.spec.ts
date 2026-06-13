import path from 'node:path'
import type { Locator, Page } from '@playwright/test'

import { expect, test } from './test-fixtures'

const ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME || 'e2e_admin'
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'E2ePass123!'
const FIXTURE_FILE = path.resolve(process.cwd(), 'e2e', 'fixtures', 'smoke-source.txt')

async function initializeAdmin(page: Page) {
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

async function login(page: Page) {
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

async function createProjectWithFixture(page: Page, projectName: string) {
  await page.getByTestId('project-create-button').click()
  await expect(page.getByTestId('project-create-dialog')).toBeVisible()
  await page.getByTestId('project-create-name').fill(projectName)
  await page.getByTestId('project-create-workflow-template').selectOption('translate')
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
}

async function createProjectWithFixtureAndOpenFocusWorkbench(page: Page, projectName: string) {
  await createProjectWithFixture(page, projectName)

  const focusPagePromise = page.waitForEvent('popup')
  await page.getByTestId('project-file-open-workbench').click()
  const focusPage = await focusPagePromise
  await expect(focusPage).toHaveURL(/\/tasks\/[0-9a-f-]+\/focus/i)
  await expect(focusPage.getByTestId('workbench-page')).toBeVisible()
  await expect(focusPage.locator('.app-sidebar')).toHaveCount(0)
  await expect(focusPage.locator('.shell-header')).toHaveCount(0)
  await expect(focusPage.getByTestId('workbench-ribbon')).toBeVisible()
  return focusPage
}

async function saveWorkbenchNow(page: Page) {
  const saveResponse = page.waitForResponse((response) => (
    response.url().includes('/segments')
    && response.request().method() === 'PUT'
    && response.status() < 400
  ))
  await page.getByTestId('workbench-save-button').click()
  await saveResponse
}

async function expectEditorText(editor: Locator, expected: string) {
  await expect.poll(async () => (
    (await editor.evaluate((element) => element.textContent || '')).replace(/\u00a0/g, ' ')
  )).toBe(expected)
}

async function expectEditorHtml(editor: Locator, matcher: RegExp) {
  await expect.poll(async () => editor.evaluate((element) => element.innerHTML)).toMatch(matcher)
}

async function expectEditorHtmlNot(editor: Locator, matcher: RegExp) {
  await expect.poll(async () => editor.evaluate((element) => element.innerHTML)).not.toMatch(matcher)
}

async function selectEditorRange(editor: Locator, start: number, end: number) {
  await editor.evaluate((element, rangeOffsets) => {
    element.focus()
    const selection = window.getSelection()
    if (!selection) return

    const range = document.createRange()
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
    let offset = 0
    let startSet = false
    let endSet = false
    let node = walker.nextNode()

    while (node) {
      const textLength = node.textContent?.length || 0
      if (!startSet && offset + textLength >= rangeOffsets.start) {
        range.setStart(node, Math.max(0, rangeOffsets.start - offset))
        startSet = true
      }
      if (!endSet && offset + textLength >= rangeOffsets.end) {
        range.setEnd(node, Math.max(0, rangeOffsets.end - offset))
        endSet = true
        break
      }
      offset += textLength
      node = walker.nextNode()
    }

    if (!startSet) {
      range.selectNodeContents(element)
      range.collapse(false)
    } else if (!endSet) {
      range.setEnd(element, element.childNodes.length)
    }

    selection.removeAllRanges()
    selection.addRange(range)
  }, { start, end })
}

async function moveEditorCaretToEnd(editor: Locator) {
  await editor.evaluate((element) => {
    element.focus()
    const selection = window.getSelection()
    if (!selection) return
    const range = document.createRange()
    range.selectNodeContents(element)
    range.collapse(false)
    selection.removeAllRanges()
    selection.addRange(range)
  })
}

async function dispatchEditorPaste(editor: Locator, text: string, html = '') {
  await editor.evaluate((element, payload) => {
    const data = new DataTransfer()
    data.setData('text/plain', payload.text)
    if (payload.html) {
      data.setData('text/html', payload.html)
    }
    const event = new ClipboardEvent('paste', {
      clipboardData: data,
      bubbles: true,
      cancelable: true,
    })
    element.dispatchEvent(event)
  }, { text, html })
}

async function acceptCurrentRevision(page: Page) {
  const response = page.waitForResponse((item) => (
    item.url().includes('/revisions/')
    && item.request().method() === 'PATCH'
    && item.status() < 400
  ))
  await page.getByTestId('workbench-revision-accept-menu').click()
  await page.getByTestId('workbench-revision-accept-current').click()
  await response
}

async function rejectCurrentRevision(page: Page) {
  const response = page.waitForResponse((item) => (
    item.url().includes('/revisions/')
    && item.request().method() === 'PATCH'
    && item.status() < 400
  ))
  await page.getByTestId('workbench-revision-reject-menu').click()
  await page.getByTestId('workbench-revision-reject-current').click()
  await response
}

async function acceptAllRevisions(page: Page) {
  const response = page.waitForResponse((item) => (
    item.url().includes('/revisions/batch-accept')
    && item.request().method() === 'POST'
    && item.status() < 400
  ))
  await page.getByTestId('workbench-revision-accept-menu').click()
  await page.getByTestId('workbench-revision-accept-all').click()
  await expect(page.getByTestId('confirm-accept')).toBeVisible()
  await page.getByTestId('confirm-accept').click()
  await response
}

async function rejectAllRevisions(page: Page) {
  const response = page.waitForResponse((item) => (
    item.url().includes('/revisions/batch-reject')
    && item.request().method() === 'POST'
    && item.status() < 400
  ))
  await page.getByTestId('workbench-revision-reject-menu').click()
  await page.getByTestId('workbench-revision-reject-all').click()
  await expect(page.getByTestId('confirm-accept')).toBeVisible()
  await page.getByTestId('confirm-accept').click()
  await response
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

  test('项目详情工具栏删除只删除选中文件', async ({ page }) => {
    const projectDeleteRequests: string[] = []
    page.on('request', (request) => {
      if (request.method() === 'DELETE' && /\/api\/projects\/[0-9a-f-]+/i.test(request.url())) {
        projectDeleteRequests.push(request.url())
      }
    })

    await login(page)
    await createProjectWithFixture(page, `E2E Delete File ${Date.now()}`)
    const detailUrl = page.url()

    const deleteSelectedButton = page.getByTestId('project-file-delete-selected')
    await expect(deleteSelectedButton).toBeDisabled()

    const fileTable = page.getByTestId('project-file-table')
    await fileTable.getByRole('checkbox', { name: '选择第 1 行' }).check()
    await expect(deleteSelectedButton).toBeEnabled()

    await deleteSelectedButton.click()
    await expect(page.getByTestId('confirm-accept')).toBeVisible()
    await expect(page.getByText('确定删除文件“smoke-source.txt”吗？删除后无法恢复。')).toBeVisible()

    const deleteFileResponse = page.waitForResponse((response) => (
      response.request().method() === 'DELETE'
      && /\/api\/file-records\/[0-9a-f-]+/i.test(response.url())
      && response.status() < 400
    ))
    await page.getByTestId('confirm-accept').click()
    await deleteFileResponse

    await expect(page).toHaveURL(detailUrl)
    await expect(fileTable).not.toContainText('smoke-source.txt')
    expect(projectDeleteRequests).toEqual([])
  })

  test('创建项目、上传 TXT、编辑保存并导出', async ({ page }) => {
    const translatedText = `E2E translated text ${Date.now()}`

    await login(page)
    const focusPage = await createProjectWithFixtureAndOpenFocusWorkbench(
      page,
      `E2E Smoke ${Date.now()}`,
    )

    const focusEditor = focusPage.getByTestId('segment-target-editor').first()
    await expect(focusEditor).toBeVisible()
    await focusEditor.fill(translatedText)
    await saveWorkbenchNow(focusPage)

    await focusPage.reload()
    await expect(focusPage.getByTestId('segment-target-editor').first()).toContainText(translatedText)

    const focusDownloadPromise = focusPage.waitForEvent('download')
    await expect(focusPage.getByTestId('workbench-export-button')).toBeEnabled()
    await focusPage.getByTestId('workbench-export-button').click()
    const focusDownload = await focusDownloadPromise
    expect(focusDownload.suggestedFilename()).toContain('smoke-source')
    await focusPage.close()
  })

  test('目标译文编辑支持稳定撤销和恢复', async ({ page }) => {
    await login(page)
    const focusPage = await createProjectWithFixtureAndOpenFocusWorkbench(
      page,
      `E2E Undo ${Date.now()}`,
    )

    const editor = focusPage.getByTestId('segment-target-editor').first()
    await expect(editor).toBeVisible()

    await editor.fill('')
    await editor.click()
    await focusPage.keyboard.type('Alpha Beta')
    await expectEditorText(editor, 'Alpha Beta')

    await focusPage.keyboard.press('Control+Z')
    await expectEditorText(editor, 'Alpha')
    await focusPage.keyboard.press('Control+Y')
    await expectEditorText(editor, 'Alpha Beta')

    await saveWorkbenchNow(focusPage)
    await expectEditorText(editor, 'Alpha Beta')
    await focusPage.keyboard.press('Control+Z')
    await expectEditorText(editor, 'Alpha')
    await focusPage.keyboard.press('Control+Y')
    await expectEditorText(editor, 'Alpha Beta')

    await focusPage.getByTestId('workbench-clear-target-button').click()
    await expectEditorText(editor, '')
    await focusPage.keyboard.press('Control+Z')
    await expectEditorText(editor, 'Alpha Beta')

    await focusPage.getByTestId('workbench-copy-source-button').click()
    await focusPage.keyboard.press('Control+Z')
    await expectEditorText(editor, 'Alpha Beta')

    await editor.fill('format me')
    await selectEditorRange(editor, 0, 6)
    await focusPage.getByTestId('workbench-format-bold').click()
    await expectEditorHtml(editor, /<b>format<\/b>/i)
    await focusPage.keyboard.press('Control+Z')
    await expectEditorHtmlNot(editor, /<b>format<\/b>/i)

    await selectEditorRange(editor, 0, 6)
    await focusPage.getByTestId('workbench-format-bold').click()
    await expectEditorHtml(editor, /<b>format<\/b>/i)
    await selectEditorRange(editor, 0, 6)
    await focusPage.getByTestId('workbench-clear-format-button').click()
    await expectEditorHtmlNot(editor, /<b>format<\/b>/i)
    await focusPage.keyboard.press('Control+Z')
    await expectEditorHtml(editor, /<b>format<\/b>/i)

    await editor.fill('case word')
    await selectEditorRange(editor, 0, 4)
    await focusPage.getByTestId('workbench-case-menu-button').click()
    await focusPage.getByTestId('workbench-case-upper').click()
    await expectEditorText(editor, 'CASE word')
    await focusPage.keyboard.press('Control+Z')
    await expectEditorText(editor, 'case word')

    await editor.fill('symbol')
    await moveEditorCaretToEnd(editor)
    await focusPage.getByTestId('workbench-special-character-menu').click()
    await focusPage.getByTestId('workbench-special-character-0-0').click()
    await expectEditorText(editor, 'symbol&')
    await focusPage.keyboard.press('Control+Z')
    await expectEditorText(editor, 'symbol')

    await editor.fill('paste')
    await moveEditorCaretToEnd(editor)
    await dispatchEditorPaste(editor, ' plain')
    await expectEditorText(editor, 'paste plain')
    await focusPage.keyboard.press('Control+Z')
    await expectEditorText(editor, 'paste')

    await moveEditorCaretToEnd(editor)
    await dispatchEditorPaste(editor, ' rich', '<b> rich</b>')
    await expectEditorText(editor, 'paste rich')
    await expectEditorHtml(editor, /<b>\s*rich<\/b>/i)
    await focusPage.keyboard.press('Control+Z')
    await expectEditorText(editor, 'paste')
    await expectEditorHtmlNot(editor, /<b>\s*rich<\/b>/i)

    await focusPage.close()
  })

  test('跟踪修订会在编辑、保存、接受/拒绝和批量操作后实时刷新', async ({ page }) => {
    const originalText = `Revision original ${Date.now()}`
    const acceptedText = `Revision accepted ${Date.now()}`
    const rejectedText = `Revision rejected ${Date.now()}`
    const batchAcceptedText = `Revision batch accepted ${Date.now()}`
    const batchRejectedText = `Revision batch rejected ${Date.now()}`

    await login(page)
    const focusPage = await createProjectWithFixtureAndOpenFocusWorkbench(
      page,
      `E2E Revision ${Date.now()}`,
    )

    const firstRow = focusPage.getByTestId('segment-row').first()
    const editor = focusPage.getByTestId('segment-target-editor').first()
    await expect(editor).toBeVisible()

    await expect(focusPage.getByTestId('workbench-revision-toggle')).toHaveAttribute('aria-pressed', 'false')
    await expect(focusPage.getByTestId('workbench-revision-accept-menu')).toBeDisabled()
    await editor.fill(originalText)
    await saveWorkbenchNow(focusPage)
    await expect(firstRow).toHaveAttribute('data-has-pending-revision', 'false')
    await expect(focusPage.getByTestId('workbench-revision-accept-menu')).toBeDisabled()

    await focusPage.getByTestId('workbench-revision-toggle').click()
    await expect(focusPage.getByTestId('workbench-revision-toggle')).toHaveAttribute('aria-pressed', 'true')

    await editor.fill(acceptedText)
    await expect(firstRow.locator('[data-testid="segment-revision-insert"]')).toContainText(acceptedText)
    await expect(firstRow.locator('[data-testid="segment-revision-delete"]')).toContainText(originalText)

    await saveWorkbenchNow(focusPage)
    await expect(firstRow).toHaveAttribute('data-has-pending-revision', 'true')
    await expect(firstRow.locator('[data-testid="segment-revision-insert"]')).toContainText(acceptedText)
    await expect(focusPage.getByTestId('workbench-revision-accept-menu')).toBeEnabled()

    await acceptCurrentRevision(focusPage)
    await expect(firstRow).toHaveAttribute('data-has-pending-revision', 'false')
    await expect(editor).toContainText(acceptedText)

    await editor.fill(rejectedText)
    await saveWorkbenchNow(focusPage)
    await expect(firstRow).toHaveAttribute('data-has-pending-revision', 'true')
    await rejectCurrentRevision(focusPage)
    await expect(firstRow).toHaveAttribute('data-has-pending-revision', 'false')
    await expect(editor).toContainText(acceptedText)

    await editor.fill(batchAcceptedText)
    await saveWorkbenchNow(focusPage)
    await expect(firstRow).toHaveAttribute('data-has-pending-revision', 'true')
    await acceptAllRevisions(focusPage)
    await expect(firstRow).toHaveAttribute('data-has-pending-revision', 'false')
    await expect(editor).toContainText(batchAcceptedText)

    await editor.fill(batchRejectedText)
    await saveWorkbenchNow(focusPage)
    await expect(firstRow).toHaveAttribute('data-has-pending-revision', 'true')
    await rejectAllRevisions(focusPage)
    await expect(firstRow).toHaveAttribute('data-has-pending-revision', 'false')
    await expect(editor).toContainText(batchAcceptedText)

    await focusPage.close()
  })
})
