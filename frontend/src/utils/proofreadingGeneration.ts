import { defaultLLMModelId, llmModelOptions } from '../constants/llm'

// 校对和翻译入口共用模型列表，历史批次保存的模型也保留为可选项。
export type ProofreadingProvider = 'auto' | 'deepseek' | 'openrouter'

export const PROOFREADING_OPENROUTER_MODELS = llmModelOptions
  .filter(option => option.provider === 'openrouter')
  .map(option => ({ value: option.id, label: option.name }))
export const PROOFREADING_DEFAULT_OPENROUTER_MODEL = defaultLLMModelId

export function proofreadingOpenRouterModels(current: string) {
  return current && !PROOFREADING_OPENROUTER_MODELS.some(option => option.value === current)
    ? [...PROOFREADING_OPENROUTER_MODELS, { value: current, label: current }]
    : PROOFREADING_OPENROUTER_MODELS
}
