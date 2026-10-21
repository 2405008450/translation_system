import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e', testMatch: ['workflow-proofreading.spec.ts', 'merge-workflow-transition.spec.ts'], workers: 1,
  timeout: 30_000, reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:5182', ...devices['Desktop Chrome'], channel: 'chrome', screenshot: 'only-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5182',
    url: 'http://127.0.0.1:5182/e2e/workflow-proofreading-harness.html',
    reuseExistingServer: false, timeout: 60_000,
  },
})
