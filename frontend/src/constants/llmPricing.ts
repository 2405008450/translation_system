// OpenRouter 公共模型目录中的文本基础单价，单位为美元 / 百万 token。
// 来源：https://openrouter.ai/api/v1/models；不含缓存、长上下文和特殊服务档位。
export const LLM_PRICING_UPDATED_AT = '2026-10-21'
export const LLM_PRICING_SOURCE_URL = 'https://openrouter.ai/models'

export const openRouterModelPricing: Record<string, { input: number; output: number }> = {
  'google/gemini-4-argon': { input: 2, output: 10 },
  'google/gemini-3.8-flash': { input: 0.825, output: 4.125 },
  'google/gemini-3.7-flash': { input: 0.75, output: 3.75 },
  'google/gemini-3.6-flash': { input: 0.75, output: 3.75 },
  'google/gemini-3.5-flash': { input: 1.5, output: 9 },
  'google/gemini-3.1-pro-preview': { input: 2, output: 12 },
  'google/gemini-3.1-flash-lite': { input: 0.25, output: 1.5 },
  'google/gemini-3-flash-preview': { input: 0.5, output: 3 },
  'openai/gpt-6.1-sol': { input: 2, output: 10 },
  'openai/gpt-6-luna': { input: 0.1, output: 0.5 },
  'openai/gpt-6-sol': { input: 2, output: 10 },
  'openai/gpt-6-astra': { input: 10, output: 50 },
  'openai/gpt-5.6-luna': { input: 0.2, output: 1.2 },
  'openai/gpt-5.6-terra': { input: 2, output: 12 },
  'openai/gpt-5.5': { input: 5, output: 30 },
  'openai/gpt-5.4': { input: 2.5, output: 15 },
  'openai/gpt-5.4-mini': { input: 0.75, output: 4.5 },
  'openai/gpt-5.4-nano': { input: 0.2, output: 1.25 },
}
