import { expect, test as base } from '@playwright/test'

const IGNORED_CONSOLE_ERRORS = [
  'ResizeObserver loop completed with undelivered notifications',
  'ResizeObserver loop limit exceeded',
  'Failed to load resource: the server responded with a status of 404 (Not Found)',
]

export const test = base.extend({
  page: async ({ page }, use, testInfo) => {
    const consoleErrors: string[] = []
    const pageErrors: string[] = []
    const serverErrors: string[] = []

    page.on('console', (message) => {
      if (message.type() !== 'error') {
        return
      }
      const text = message.text()
      if (!IGNORED_CONSOLE_ERRORS.some((ignored) => text.includes(ignored))) {
        consoleErrors.push(text)
      }
    })

    page.on('pageerror', (error) => {
      pageErrors.push(error.message)
    })

    page.on('response', (response) => {
      if (response.status() >= 500) {
        serverErrors.push(`${response.status()} ${response.url()}`)
      }
    })

    await use(page)

    const diagnostics = [
      ...consoleErrors.map((item) => `console.error: ${item}`),
      ...pageErrors.map((item) => `pageerror: ${item}`),
      ...serverErrors.map((item) => `server error: ${item}`),
    ]

    if (diagnostics.length > 0) {
      await testInfo.attach('browser-diagnostics.txt', {
        body: diagnostics.join('\n'),
        contentType: 'text/plain',
      })
      throw new Error(`浏览器稳定性检查失败:\n${diagnostics.join('\n')}`)
    }
  },
})

export { expect }
