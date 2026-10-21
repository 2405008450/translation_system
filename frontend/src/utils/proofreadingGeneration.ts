// 两个校对入口共用模型选项，历史批次保存的模型也保留为可选项。
export type ProofreadingProvider = 'auto' | 'deepseek' | 'openrouter'

export const PROOFREADING_OPENROUTER_MODELS = [
  { value: 'google/gemini-4-argon', label: 'Gemini 4 Argon' },
  { value: 'google/gemini-3.8-flash', label: 'Gemini 3.8 Flash' },
  { value: 'google/gemini-3.7-flash', label: 'Gemini 3.7 Flash' },
  { value: 'google/gemini-3.6-flash', label: 'Gemini 3.6 Flash' },
  { value: 'google/gemini-3.5-flash', label: 'Gemini 3.5 Flash' },
  { value: 'google/gemini-3.1-pro-preview', label: 'Gemini 3.1 Pro Preview' },
  { value: 'google/gemini-3-flash-preview', label: 'Gemini 3 Flash Preview' },
]
export const PROOFREADING_DEFAULT_OPENROUTER_MODEL = 'google/gemini-3.7-flash'

export function proofreadingOpenRouterModels(current: string) {
  return current && !PROOFREADING_OPENROUTER_MODELS.some(option => option.value === current)
    ? [...PROOFREADING_OPENROUTER_MODELS, { value: current, label: current }]
    : PROOFREADING_OPENROUTER_MODELS
}
