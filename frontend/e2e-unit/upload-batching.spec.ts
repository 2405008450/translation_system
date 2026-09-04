import { expect, test } from '@playwright/test'

import {
  buildUploadBatches,
  calculateOverallUploadProgress,
  getRemainingUploadFiles,
  getUploadBatchCapacity,
  isUploadSelectionWithinLimit,
} from '../src/utils/uploadBatching'

const limits = {
  max_files_per_batch: 50,
  max_expanded_files: 100,
}

function files(count: number) {
  return Array.from({ length: count }, (_, index) => ({ name: `source-${index + 1}.txt` }))
}

test('按 50 个文件拆分 51、100 和 200 个文件', () => {
  expect(buildUploadBatches(files(51), limits, 1).map((batch) => batch.length)).toEqual([50, 1])
  expect(buildUploadBatches(files(100), limits, 1).map((batch) => batch.length)).toEqual([50, 50])
  expect(buildUploadBatches(files(200), limits, 1).map((batch) => batch.length)).toEqual([50, 50, 50, 50])
})

test('一次最多选择 200 个文件', () => {
  expect(isUploadSelectionWithinLimit(200, 200)).toBe(true)
  expect(isUploadSelectionWithinLimit(201, 200)).toBe(false)
})

test('多目标语言会缩小批次，确保生成任务不超过上限', () => {
  expect(getUploadBatchCapacity(limits, 3)).toBe(33)
  const batches = buildUploadBatches(files(100), limits, 3)
  expect(batches.map((batch) => batch.length)).toEqual([33, 33, 33, 1])
  expect(batches.every((batch) => batch.length * 3 <= limits.max_expanded_files)).toBe(true)
})

test('ZIP 和 RAR 压缩包独占批次且保持选择顺序', () => {
  const selected = [
    { name: 'a.txt' },
    { name: 'bundle.zip' },
    { name: 'b.txt' },
    { name: 'archive.RAR' },
  ]
  expect(buildUploadBatches(selected, limits, 1).map((batch) => batch.map((file) => file.name))).toEqual([
    ['a.txt'],
    ['bundle.zip'],
    ['b.txt'],
    ['archive.RAR'],
  ])
})

test('总体进度按文件数加权且不会倒退', () => {
  const firstBatchHalf = calculateOverallUploadProgress(0, 100, 50, 0.5, 0)
  const firstBatchDone = calculateOverallUploadProgress(50, 100, 0, 1, firstBatchHalf)
  const staleProgress = calculateOverallUploadProgress(50, 100, 50, 0.1, firstBatchDone)
  const allDone = calculateOverallUploadProgress(100, 100, 0, 1, staleProgress)

  expect(firstBatchHalf).toBe(25)
  expect(firstBatchDone).toBe(50)
  expect(staleProgress).toBeGreaterThanOrEqual(firstBatchDone)
  expect(allDone).toBe(100)
})

test('第二批失败时保留失败批次和后续文件供重试', () => {
  const selected = files(120)
  const batches = buildUploadBatches(selected, limits, 1)
  const completedFileCount = batches[0].length
  const remaining = getRemainingUploadFiles(selected, completedFileCount)

  expect(completedFileCount).toBe(50)
  expect(remaining).toHaveLength(70)
  expect(remaining[0].name).toBe('source-51.txt')
  expect(remaining.some((file) => file.name === 'source-1.txt')).toBe(false)
})
