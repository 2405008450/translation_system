import type { LLMProvider, LLMTranslateScope } from '../types/api'

export interface LLMOption<T extends string> {
  value: T
  label: string
  description: string
}

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
