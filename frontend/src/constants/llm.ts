import type { LLMProvider, LLMTranslateScope } from '../types/api'
import { translate } from '../i18n'

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

export const llmModelShortLabelMap: Record<string, string> = {
  'google/gemini-3.5-flash': 'Gemini 3.5',
  'google/gemini-3.1-pro-preview': 'Gemini 3.1 Pro',
  'google/gemini-3.1-flash-lite': 'Gemini 3.1 Lite',
  'google/gemini-3-flash-preview': 'Gemini 3 Flash',
  'google/gemini-2.5-pro': 'Gemini 2.5 Pro',
  'google/gemini-2.5-flash': 'Gemini 2.5 Flash',
  'google/gemini-2.5-flash-lite': 'Gemini 2.5 Lite',
  'openai/gpt-chat-latest': 'GPT Chat',
  'openai/gpt-5.4': 'GPT-5.4',
  'openai/gpt-5.4-mini': 'GPT-5.4 Mini',
  'openai/gpt-5.4-nano': 'GPT-5.4 Nano',
  'openai/gpt-5.3-chat': 'GPT-5.3 Chat',
  'openai/gpt-5.2': 'GPT-5.2',
  'openai/gpt-5.2-chat': 'GPT-5.2 Chat',
  'openai/gpt-5.1': 'GPT-5.1',
  'openai/gpt-5.1-chat': 'GPT-5.1 Chat',
  'openai/gpt-5-mini': 'GPT-5 Mini',
  'openai/gpt-5-nano': 'GPT-5 Nano',
  'deepseek/deepseek-chat': 'DeepSeek Chat',
  'deepseek-chat': 'DeepSeek Chat',
}

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
    value: 'current_segment',
    get label() { return translate('llm.scope.currentSegment.label') },
    get description() { return translate('llm.scope.currentSegment.description') },
  },
  {
    value: 'all',
    get label() { return translate('llm.scope.all.label') },
    get description() { return translate('llm.scope.all.description') },
  },
  {
    value: 'all_with_exact',
    get label() { return translate('llm.scope.allWithExact.label') },
    get description() { return translate('llm.scope.allWithExact.description') },
  },
  {
    value: 'empty_target_only',
    get label() { return translate('llm.scope.emptyTargetOnly.label') },
    get description() { return translate('llm.scope.emptyTargetOnly.description') },
  },
  {
    value: 'fuzzy_only',
    get label() { return translate('llm.scope.fuzzyOnly.label') },
    get description() { return translate('llm.scope.fuzzyOnly.description') },
  },
  {
    value: 'none_only',
    get label() { return translate('llm.scope.noneOnly.label') },
    get description() { return translate('llm.scope.noneOnly.description') },
  },
]

export const llmProviderOptions: LLMOption<LLMProvider>[] = [
  {
    value: 'deepseek',
    label: 'DeepSeek',
    get description() { return translate('llm.provider.deepseek.description') },
  },
  {
    value: 'auto',
    get label() { return translate('llm.provider.auto.label') },
    get description() { return translate('llm.provider.auto.description') },
  },
  {
    value: 'openrouter',
    label: 'OpenRouter',
    get description() { return translate('llm.provider.openrouter.description') },
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

function formatUnknownLLMModelShortLabel(modelId: string) {
  const rawName = modelId.split('/').pop()?.replace(/:.+$/, '') || modelId
  const tokenMap: Record<string, string> = {
    chat: 'Chat',
    claude: 'Claude',
    deepseek: 'DeepSeek',
    flash: 'Flash',
    gemini: 'Gemini',
    gpt: 'GPT',
    haiku: 'Haiku',
    latest: 'Latest',
    lite: 'Lite',
    mini: 'Mini',
    nano: 'Nano',
    opus: 'Opus',
    preview: 'Preview',
    pro: 'Pro',
    sonnet: 'Sonnet',
  }

  return rawName
    .split(/[-_]+/)
    .filter(Boolean)
    .map((token) => tokenMap[token.toLowerCase()] || token)
    .join(' ')
}

export function getLLMModelShortLabel(modelId: string) {
  const normalizedModelId = modelId.trim()
  if (!normalizedModelId) {
    return ''
  }
  return llmModelShortLabelMap[normalizedModelId] || formatUnknownLLMModelShortLabel(normalizedModelId)
}
