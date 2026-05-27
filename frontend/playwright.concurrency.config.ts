import { defineConfig } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(frontendDir, '..')

const apiPort = Number(process.env.E2E_API_PORT || 19115)
const fakeLlmPort = Number(process.env.E2E_FAKE_LLM_PORT || 19114)
const apiBaseUrl = `http://127.0.0.1:${apiPort}`
const fakeLlmBaseUrl = `http://127.0.0.1:${fakeLlmPort}`

const databaseUrl = process.env.E2E_DATABASE_URL
  || process.env.DATABASE_URL
  || 'postgresql+psycopg://postgres:postgres@localhost:5432/tm_demo_e2e'

const fileStorageDir = process.env.E2E_FILE_STORAGE_DIR
  || path.join(rootDir, 'data', 'e2e_file_records')

export default defineConfig({
  testDir: './e2e-concurrency',
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: {
    timeout: 10_000,
  },
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never', outputFolder: 'playwright-report-concurrency' }]]
    : [['list'], ['html', { open: 'never', outputFolder: 'playwright-report-concurrency' }]],
  outputDir: 'test-results-concurrency',
  use: {
    baseURL: apiBaseUrl,
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `node scripts/e2e_fake_llm_server.mjs ${fakeLlmPort}`,
      cwd: rootDir,
      url: `${fakeLlmBaseUrl}/health`,
      reuseExistingServer: process.env.E2E_REUSE_EXISTING_SERVER === '1',
      timeout: 30_000,
      env: {
        ...process.env,
        E2E_FAKE_LLM_PORT: String(fakeLlmPort),
        E2E_FAKE_LLM_DELAY_MS: process.env.E2E_FAKE_LLM_DELAY_MS || '200',
      },
    },
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
        DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY || 'e2e-fake-key',
        DEEPSEEK_BASE_URL: process.env.DEEPSEEK_BASE_URL || fakeLlmBaseUrl,
        DEEPSEEK_MODEL: process.env.DEEPSEEK_MODEL || 'e2e-fake-model',
        LLM_TIMEOUT_SECONDS: process.env.LLM_TIMEOUT_SECONDS || '10',
        LLM_STALL_TIMEOUT_SECONDS: process.env.LLM_STALL_TIMEOUT_SECONDS || '30',
        LLM_MAX_CONCURRENCY: process.env.LLM_MAX_CONCURRENCY || '3',
      },
    },
  ],
})
