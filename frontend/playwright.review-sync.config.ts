import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  testMatch: 'review-sync.spec.ts',
  workers: 1,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:5183', ...devices['Desktop Chrome'], channel: 'chrome' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5183',
    url: 'http://127.0.0.1:5183/e2e/review-sync-harness.html',
    reuseExistingServer: false,
  },
})
