export const supportedTaskExtensions = [
  '.docx',
  '.txt',
  '.csv',
  '.html',
  '.htm',
  '.md',
  '.markdown',
  '.json',
  '.yaml',
  '.yml',
  '.php',
  '.properties',
  '.po',
  '.pot',
  '.strings',
  '.srt',
  '.dita',
  '.ditamap',
  '.xml',
  '.svg',
  '.sdlxliff',
  '.txml',
  '.dxf',
  '.idml',
  '.mif',
  '.zip',
] as const

export const supportedTaskFileAccept = supportedTaskExtensions.join(',')

export function getTaskFileExtension(filename: string | null | undefined) {
  if (!filename) {
    return ''
  }

  const dotIndex = filename.lastIndexOf('.')
  if (dotIndex === -1) {
    return ''
  }

  return filename.slice(dotIndex).toLowerCase()
}

export function getTaskExportFormatLabel(filename: string | null | undefined) {
  const extension = getTaskFileExtension(filename)
  return extension ? extension.slice(1).toUpperCase() : 'FILE'
}

export function buildTranslatedTaskFilename(filename: string | null | undefined) {
  const safeName = filename || 'translated.txt'
  const extension = getTaskFileExtension(safeName) || '.txt'
  const stem = extension ? safeName.slice(0, -extension.length) : safeName
  return `${stem || 'translated'}_translated${extension}`
}
