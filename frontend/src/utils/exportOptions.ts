export interface FileExportOption {
  id: string
  name: string
  description: string
  extension: string
}

export interface FileExportOptionGroup {
  id: string
  label: string
  options: FileExportOption[]
}

const EXPORT_OPTION_GROUPS = [
  {
    id: 'file',
    label: '文件',
    optionIds: ['source', 'original', 'translated_pdf', 'translated_svg'],
  },
  {
    id: 'bilingual',
    label: '双语审校',
    optionIds: [
      'bilingual_excel_original',
      'bilingual_pptx_original',
      'bilingual_docx_layout_source_first',
      'bilingual_docx_layout_target_first',
      'bilingual_docx',
      'bilingual_excel',
      'bilingual_txt',
      'bilingual',
    ],
  },
  {
    id: 'exchange',
    label: '交换格式',
    optionIds: ['tmx', 'xliff', 'xliff2'],
  },
] as const

export function getExportOptionExtensionLabel(option: FileExportOption) {
  return option.extension ? option.extension.replace(/^\./, '').toUpperCase() : ''
}

export function groupExportOptions(options: FileExportOption[]): FileExportOptionGroup[] {
  const byId = new Map(options.map((option) => [option.id, option]))
  const groupedIds = new Set<string>()
  const groups: FileExportOptionGroup[] = []

  for (const groupConfig of EXPORT_OPTION_GROUPS) {
    const groupOptions = groupConfig.optionIds
      .map((optionId) => byId.get(optionId))
      .filter((option): option is FileExportOption => Boolean(option))

    if (groupOptions.length === 0) {
      continue
    }

    groupOptions.forEach((option) => groupedIds.add(option.id))
    groups.push({
      id: groupConfig.id,
      label: groupConfig.label,
      options: groupOptions,
    })
  }

  const otherOptions = options.filter((option) => !groupedIds.has(option.id))
  if (otherOptions.length > 0) {
    groups.push({
      id: 'other',
      label: '其他',
      options: otherOptions,
    })
  }

  return groups
}

// 与后端 file_export_queue.EXPORT_COMPLETED_MESSAGE 保持一致。
// 后端把附加提示直接拼在该前缀之后，这里切分出来单独展示。
const EXPORT_COMPLETED_MESSAGE = '导出完成。'

/**
 * 从导出任务的完成消息中提取附加提示。
 *
 * 目前用于 AI 转 SVG 时告知使用者“部分画板的图形已转为位图”。
 * 没有附加提示时返回空串。
 */
export function extractExportNotice(message?: string | null): string {
  const text = String(message || '').trim()
  if (!text.startsWith(EXPORT_COMPLETED_MESSAGE)) {
    return ''
  }
  return text.slice(EXPORT_COMPLETED_MESSAGE.length).trim()
}
