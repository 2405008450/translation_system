import { openRouterModelPricing, openRouterModelReleaseDates } from '../constants/llmPricing'

// 提取表单独立排序：发布日期降序，同日按输入价、输出价升序，最后按 ID 稳定排序。
// 无发布日期的直连入口放最后，不使用 OpenRouter 报价推断直连费用。
export function sortModelsForTermExtraction<T extends { id: string }>(models: readonly T[]): T[] {
  return [...models].sort((left, right) => {
    const dateOrder = (openRouterModelReleaseDates[right.id] || '').localeCompare(openRouterModelReleaseDates[left.id] || '')
    if (dateOrder) return dateOrder
    const leftPrice = openRouterModelPricing[left.id]
    const rightPrice = openRouterModelPricing[right.id]
    return ((leftPrice?.input ?? Infinity) - (rightPrice?.input ?? Infinity))
      || ((leftPrice?.output ?? Infinity) - (rightPrice?.output ?? Infinity))
      || left.id.localeCompare(right.id)
  })
}
