export interface UploadBatchPlanLimits {
  max_files_per_batch: number
  max_expanded_files: number
}

export interface NamedUploadFile {
  name: string
}

const ARCHIVE_EXTENSIONS = new Set(['.zip', '.rar'])

export function isArchiveUploadFile(file: NamedUploadFile): boolean {
  const dotIndex = file.name.lastIndexOf('.')
  const extension = dotIndex >= 0 ? file.name.slice(dotIndex).toLowerCase() : ''
  return ARCHIVE_EXTENSIONS.has(extension)
}

export function getUploadBatchCapacity(
  limits: UploadBatchPlanLimits,
  targetLanguageCount: number,
): number {
  if (targetLanguageCount <= 0) {
    return 0
  }

  const generatedTaskCapacity = Math.floor(limits.max_expanded_files / targetLanguageCount)
  return Math.max(0, Math.min(limits.max_files_per_batch, generatedTaskCapacity))
}

export function isUploadSelectionWithinLimit(fileCount: number, maxFilesPerSelection: number): boolean {
  return fileCount <= maxFilesPerSelection
}

export function calculateOverallUploadProgress(
  completedFileCount: number,
  totalFileCount: number,
  batchFileCount: number,
  batchProgress: number,
  previousProgress: number,
): number {
  if (totalFileCount <= 0) {
    return 0
  }

  const normalizedBatchProgress = Math.min(1, Math.max(0, batchProgress))
  const calculated = Math.round(
    ((completedFileCount + batchFileCount * normalizedBatchProgress) / totalFileCount) * 100,
  )
  return Math.max(previousProgress, Math.min(100, calculated))
}

export function getRemainingUploadFiles<T>(files: readonly T[], completedFileCount: number): T[] {
  return files.slice(Math.max(0, completedFileCount))
}

export function buildUploadBatches<T extends NamedUploadFile>(
  files: readonly T[],
  limits: UploadBatchPlanLimits,
  targetLanguageCount: number,
): T[][] {
  const batchCapacity = getUploadBatchCapacity(limits, targetLanguageCount)
  if (batchCapacity <= 0) {
    return []
  }

  const batches: T[][] = []
  let currentBatch: T[] = []

  const flushCurrentBatch = () => {
    if (currentBatch.length > 0) {
      batches.push(currentBatch)
      currentBatch = []
    }
  }

  for (const file of files) {
    // 压缩包解压后的文件数量无法在浏览器端预知，因此独占一个服务端批次。
    if (isArchiveUploadFile(file)) {
      flushCurrentBatch()
      batches.push([file])
      continue
    }

    currentBatch.push(file)
    if (currentBatch.length >= batchCapacity) {
      flushCurrentBatch()
    }
  }

  flushCurrentBatch()
  return batches
}
