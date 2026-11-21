import type { LLMProvider, LLMTranslateScope, LLMTranslationScenario } from '../types/api'
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

export const defaultLLMModelId = 'google/gemini-4-argon'

export const llmScenarioOptions: LLMOption<LLMTranslationScenario>[] = [
  {
    value: 'standard',
    get label() { return translate('llm.scenario.standard') },
    get description() { return translate('llm.scenario.standardDescription') },
  },
  {
    value: 'mixed',
    get label() { return translate('llm.scenario.mixed') },
    get description() { return translate('llm.scenario.mixedDescription') },
  },
]

export const llmModelShortLabelMap: Record<string, string> = {
  'google/gemini-4-neon': 'Gemini 4 Neon',
  'google/gemini-4-hydrogen': 'Gemini 4 Hydrogen',
  'google/gemini-4-carbon': 'Gemini 4 Carbon',
  'google/gemini-4-argon': 'Gemini 4 Argon',
  'google/gemini-3.8-flash': 'Gemini 3.8 Flash',
  'google/gemini-3.7-flash': 'Gemini 3.7 Flash',
  'google/gemini-3.6-flash': 'Gemini 3.6 Flash',
  'google/gemini-3.5-flash': 'Gemini 3.5 Flash',
  'google/gemini-3.1-pro-preview': 'Gemini 3.1 Pro',
  'google/gemini-3.1-flash-lite': 'Gemini 3.1 Lite',
  'google/gemini-3-flash-preview': 'Gemini 3 Flash',
  'openai/gpt-6.2-astra-pro': 'GPT-6.2 Astra Pro',
  'openai/gpt-6.2-astra': 'GPT-6.2 Astra',
  'openai/gpt-6.2-sol-pro': 'GPT-6.2 Sol Pro',
  'openai/gpt-6.2-sol': 'GPT-6.2 Sol',
  'openai/gpt-6.1-terra-pro': 'GPT-6.1 Terra Pro',
  'openai/gpt-6.1-terra': 'GPT-6.1 Terra',
  'openai/gpt-6.1-sol-pro': 'GPT-6.1 Sol Pro',
  'openai/gpt-6.1-sol': 'GPT-6.1 Sol',
  'openai/gpt-6-luna': 'GPT-6 Luna',
  'openai/gpt-6-sol': 'GPT-6 Sol',
  'openai/gpt-6-astra': 'GPT-6 Astra',
  'openai/gpt-5.6-luna': 'GPT-5.6 Luna',
  'openai/gpt-5.6-terra': 'GPT-5.6 Terra',
  'openai/gpt-5.5': 'GPT-5.5',
  'openai/gpt-5.4': 'GPT-5.4',
  'openai/gpt-5.4-mini': 'GPT-5.4 Mini',
  'openai/gpt-5.4-nano': 'GPT-5.4 Nano',
  'deepseek/deepseek-v4.1-flash-1017': 'DeepSeek V4.1 Flash 1017',
  'deepseek/deepseek-v4.1-flash': 'DeepSeek V4.1 Flash',
  'deepseek/deepseek-v4-pro-0813': 'DeepSeek V4 Pro 0813',
  'deepseek/deepseek-chat': 'DeepSeek Chat',
  'deepseek-chat': 'DeepSeek Chat',
}

export const llmModelOptions: LLMModelOption[] = [
  { id: 'google/gemini-4-neon', name: "Gemini 4 Neon", family: 'gemini', provider: 'openrouter' },
  { id: 'openai/gpt-6.2-astra-pro', name: "GPT-6.2 Astra Pro", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-6.2-astra', name: "GPT-6.2 Astra", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-6.2-sol-pro', name: "GPT-6.2 Sol Pro", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-6.2-sol', name: "GPT-6.2 Sol", family: 'gpt', provider: 'openrouter' },
  { id: 'google/gemini-4-hydrogen', name: "Gemini 4 Hydrogen", family: 'gemini', provider: 'openrouter' },
  { id: 'google/gemini-4-carbon', name: "Gemini 4 Carbon", family: 'gemini', provider: 'openrouter' },
  { id: 'openai/gpt-6.1-terra-pro', name: "GPT-6.1 Terra Pro", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-6.1-terra', name: "GPT-6.1 Terra", family: 'gpt', provider: 'openrouter' },
  { id: 'deepseek/deepseek-v4.1-flash-1017', name: "DeepSeek V4.1 Flash 1017", family: 'deepseek', provider: 'openrouter' },
  { id: 'google/gemini-4-argon', name: "Gemini 4 Argon", family: 'gemini', provider: 'openrouter' },
  { id: 'openai/gpt-6.1-sol-pro', name: "GPT-6.1 Sol Pro", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-6.1-sol', name: "GPT-6.1 Sol", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-6-luna-pro', name: "GPT-6 Luna Pro", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-6-luna', name: "GPT-6 Luna", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-6-sol-pro', name: "GPT-6 Sol Pro", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-6-sol', name: "GPT-6 Sol", family: 'gpt', provider: 'openrouter' },
  { id: 'deepseek/deepseek-v4.1-flash', name: "DeepSeek V4.1 Flash", family: 'deepseek', provider: 'openrouter' },
  { id: 'openai/gpt-6-astra', name: "GPT-6 Astra", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-6-astra-pro', name: "GPT-6 Astra Pro", family: 'gpt', provider: 'openrouter' },
  { id: 'google/gemini-3.8-flash', name: "Gemini 3.8 Flash", family: 'gemini', provider: 'openrouter' },
  { id: 'google/gemini-3.7-flash', name: "Gemini 3.7 Flash", family: 'gemini', provider: 'openrouter' },
  { id: 'deepseek/deepseek-v4-pro-0813', name: "DeepSeek V4 Pro 0813", family: 'deepseek', provider: 'openrouter' },
  { id: 'google/gemini-3.6-flash', name: "Gemini 3.6 Flash", family: 'gemini', provider: 'openrouter' },
  { id: 'google/gemini-3.5-flash-lite', name: "Gemini 3.5 Flash Lite", family: 'gemini', provider: 'openrouter' },
  { id: 'openai/gpt-5.6-luna', name: "GPT-5.6 Luna", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-5.6-terra', name: "GPT-5.6 Terra", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-5.6-sol-pro', name: "GPT-5.6 Sol Pro", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-5.6-sol', name: "GPT-5.6 Sol", family: 'gpt', provider: 'openrouter' },
  { id: 'google/gemini-3.5-flash', name: "Gemini 3.5 Flash", family: 'gemini', provider: 'openrouter' },
  { id: 'google/gemini-3.1-flash-lite', name: "Gemini 3.1 Flash Lite", family: 'gemini', provider: 'openrouter' },
  { id: 'openai/gpt-5.5', name: "GPT-5.5", family: 'gpt', provider: 'openrouter' },
  { id: 'deepseek/deepseek-v4-pro', name: "DeepSeek V4 Pro 0423", family: 'deepseek', provider: 'openrouter' },
  { id: 'deepseek/deepseek-v4-flash', name: "DeepSeek V4 Flash 0423", family: 'deepseek', provider: 'openrouter' },
  { id: 'openai/gpt-5.4-nano', name: "GPT-5.4 Nano", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-5.4-mini', name: "GPT-5.4 Mini", family: 'gpt', provider: 'openrouter' },
  { id: 'openai/gpt-5.4', name: "GPT-5.4", family: 'gpt', provider: 'openrouter' },
  { id: 'google/gemini-3.1-pro-preview', name: "Gemini 3.1 Pro Preview", family: 'gemini', provider: 'openrouter' },
  { id: 'google/gemini-3-flash-preview', name: "Gemini 3 Flash Preview", family: 'gemini', provider: 'openrouter' },
  { id: 'deepseek/deepseek-chat', name: 'DeepSeek Chat（直连）', family: 'deepseek', provider: 'deepseek' },
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
  return llmModelShortLabelMap[normalizedModelId] || llmModelOptions.find(item => item.id === normalizedModelId)?.name || formatUnknownLLMModelShortLabel(normalizedModelId)
}
