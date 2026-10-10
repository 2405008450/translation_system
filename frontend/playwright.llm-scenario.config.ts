import { defineConfig } from '@playwright/test'
import reviewConfig from './playwright.review-sync.config'

export default defineConfig({ ...reviewConfig, testMatch: 'llm-scenario.spec.ts' })
