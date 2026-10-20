export function isStandaloneProofreading(templateId?: string | null): boolean {
  return templateId === 'proofread' || templateId === 'proofread_only'
}

export function supportsAlignment(templateId?: string | null): boolean {
  return templateId === 'proofread'
}

export const workflowDescriptions: Record<string, string> = {
  translate: '翻译原文并导出译文。',
  translate_review: '先翻译，再审校。',
  translate_review_proofread: '翻译、审校后，对已有句段进行 LLM 校对，无需重新对齐。',
  proofread_only: '导入已有对应关系的 Excel 原文列和译文列，直接校对，无需对齐。',
  proofread: '分别上传原文与译文文档，先确认对齐关系，再进行校对。',
}
