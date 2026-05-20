import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(frontendDir, '..')

const apiPort = Number(process.env.E2E_API_PORT || 19113)
const frontendPort = Number(process.env.E2E_FRONTEND_PORT || 5178)
const apiBaseUrl = `http://127.0.0.1:${apiPort}`
const frontendBaseUrl = process.env.E2E_BASE_URL || `http://127.0.0.1:${frontendPort}`

const databaseUrl = process.env.E2E_DATABASE_URL
  || process.env.DATABASE_URL
  || 'postgresql+psycopg://postgres:postgres@localhost:5432/tm_demo_e2e'

const fileStorageDir = process.env.E2E_FILE_STORAGE_DIR
  || path.join(rootDir, 'data', 'e2e_file_records')
const browserChannel = process.env.E2E_BROWSER_CHANNEL || (process.env.CI ? undefined : 'chrome')

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]],
  outputDir: 'test-results',
  use: {
    baseURL: frontendBaseUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: [
    {
      command: `node scripts/e2e_uvicorn_runner.mjs ${apiPort}`,
      cwd: rootDir,
      url: `${apiBaseUrl}/api/auth/init`,
      reuseExistingServer: process.env.E2E_REUSE_EXISTING_SERVER === '1',
      timeout: 60_000,
      env: {
        ...process.env,
        DATABASE_URL: databaseUrl,
        FILE_STORAGE_DIR: fileStorageDir,
        JWT_SECRET_KEY: process.env.E2E_JWT_SECRET_KEY || 'e2e-local-secret-key',
        TM_VECTOR_ENABLED: process.env.TM_VECTOR_ENABLED || 'false',
        REDIS_URL: process.env.E2E_REDIS_URL || '',
      },
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${frontendPort}`,
      cwd: frontendDir,
      url: frontendBaseUrl,
      reuseExistingServer: process.env.E2E_REUSE_EXISTING_SERVER === '1',
      timeout: 60_000,
      env: {
        ...process.env,
        VITE_API_PROXY_TARGET: apiBaseUrl,
      },
    },
  ],
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        ...(browserChannel ? { channel: browserChannel } : {}),
      },
    },
  ],
})
