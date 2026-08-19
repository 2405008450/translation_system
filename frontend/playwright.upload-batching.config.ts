import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e-unit',
  fullyParallel: true,
  workers: 1,
  reporter: 'list',
})
