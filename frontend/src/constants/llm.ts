import type { LLMProvider, LLMTranslateScope } from '../types/api'

export interface LLMOption<T extends string> {
  value: T
  label: string
  description: string
}

export interface LLMModelOption {
  id: string
  name: string
  family: 'gemini' | 'gpt' | 'deepseek'
  provider: 'openrouter' | 'deepseek'
}

export const defaultLLMModelId = 'google/gemini-3.5-flash'

export const llmModelOptions: LLMModelOption[] = [
  {
    id: defaultLLMModelId,
    name: 'Gemini 3.5 Flash',
    family: 'gemini',
    provider: 'openrouter',
  },
  {
    id: 'google/gemini-3.1-pro-preview',
    name: 'Gemini 3.1 Pro Preview',
    family: 'gemini',
    provider: 'openrouter',
  },
  {
    id: 'google/gemini-3.1-flash-lite',
    name: 'Gemini 3.1 Flash Lite',
    family: 'gemini',
    provider: 'openrouter',
  },
  {
    id: 'google/gemini-3-flash-preview',
    name: 'Gemini 3 Flash Preview',
    family: 'gemini',
    provider: 'openrouter',
  },
  {
    id: 'google/gemini-2.5-pro',
    name: 'Gemini 2.5 Pro',
    family: 'gemini',
    provider: 'openrouter',
  },
  {
    id: 'google/gemini-2.5-flash',
    name: 'Gemini 2.5 Flash',
    family: 'gemini',
    provider: 'openrouter',
  },
  {
    id: 'google/gemini-2.5-flash-lite',
    name: 'Gemini 2.5 Flash Lite',
    family: 'gemini',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-chat-latest',
    name: 'GPT Chat Latest',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-5.4',
    name: 'GPT-5.4',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-5.4-mini',
    name: 'GPT-5.4 Mini',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-5.4-nano',
    name: 'GPT-5.4 Nano',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-5.3-chat',
    name: 'GPT-5.3 Chat',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-5.2',
    name: 'GPT-5.2',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-5.2-chat',
    name: 'GPT-5.2 Chat',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-5.1',
    name: 'GPT-5.1',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-5.1-chat',
    name: 'GPT-5.1 Chat',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-5-mini',
    name: 'GPT-5 Mini',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'openai/gpt-5-nano',
    name: 'GPT-5 Nano',
    family: 'gpt',
    provider: 'openrouter',
  },
  {
    id: 'deepseek/deepseek-chat',
    name: 'DeepSeek Chat',
    family: 'deepseek',
    provider: 'deepseek',
  },
]

export const llmScopeOptions: LLMOption<LLMTranslateScope>[] = [
  {
    value: 'all',
    label: '全部未确认译文',
    description: '处理模糊匹配和无匹配句段。',
  },
  {
    value: 'all_with_exact',
    label: '全部句段',
    description: '连同精确匹配句段一起重跑。',
  },
  {
    value: 'empty_target_only',
    label: '仅空译文',
    description: '只处理译文为空的句段，保留已有译文。',
  },
  {
    value: 'fuzzy_only',
    label: '仅模糊匹配',
    description: '只修正模糊匹配句段。',
  },
  {
    value: 'none_only',
    label: '仅无匹配',
    description: '只处理没有匹配结果的句段。',
  },
]

export const llmProviderOptions: LLMOption<LLMProvider>[] = [
  {
    value: 'deepseek',
    label: 'DeepSeek',
    description: '使用 DeepSeek 提供的模型。',
  },
  {
    value: 'auto',
    label: '自动选择',
    description: '按当前配置自动选择可用模型。',
  },
  {
    value: 'openrouter',
    label: 'OpenRouter',
    description: '使用 OpenRouter 提供的模型。',
  },
]

export function getLLMScopeLabel(scope: LLMTranslateScope) {
  return llmScopeOptions.find((item) => item.value === scope)?.label || scope
}

export function getLLMProviderLabel(provider: LLMProvider) {
  return llmProviderOptions.find((item) => item.value === provider)?.label || provider
}

export function getLLMModelLabel(modelId: string) {
  return llmModelOptions.find((item) => item.id === modelId)?.name || modelId
}
