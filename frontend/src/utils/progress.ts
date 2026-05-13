const completedProgressBackground = 'linear-gradient(90deg, #36d1a0, #2f9786)'
const processingProgressBackground = 'linear-gradient(90deg, #4db8ff, var(--state-info))'

const processingStatuses = new Set(['in_progress', 'processing'])

function normalizeProgress(progress: number) {
  return Math.max(0, Math.min(Number.isFinite(progress) ? progress : 0, 100))
}

export function isProgressComplete(progress: number) {
  return normalizeProgress(progress) >= 100
}

export function getProgressStyle(progress: number, status?: string) {
  const normalized = normalizeProgress(progress)

  if (status && processingStatuses.has(status) && normalized < 100) {
    return { width: `${normalized}%`, background: processingProgressBackground }
  }

  if (normalized >= 80) {
    return { width: `${normalized}%`, background: completedProgressBackground }
  }

  if (normalized >= 50) {
    return { width: `${normalized}%`, background: 'linear-gradient(90deg, #4db8ff, #1890ff)' }
  }

  if (normalized >= 20) {
    return { width: `${normalized}%`, background: 'linear-gradient(90deg, #ffd666, #faad14)' }
  }

  return { width: `${normalized}%`, background: 'linear-gradient(90deg, #d9e3e0, #c4d2ce)' }
}
