import type { WorkflowProgress } from '../types/api'

const completedProgressBackground = 'linear-gradient(90deg, #36d1a0, #2f9786)'
const processingProgressBackground = 'linear-gradient(90deg, #4db8ff, var(--state-info))'

const processingStatuses = new Set(['in_progress', 'processing'])

function normalizeProgress(progress: number) {
  return Math.max(0, Math.min(Number.isFinite(progress) ? progress : 0, 100))
}

export function clampDisplayProgress(value: unknown) {
  const progress = Number(value)
  const normalized = Math.max(0, Math.min(Number.isFinite(progress) ? progress : 0, 100))
  return Number(normalized.toFixed(2))
}

export function calculateProgressPercent(completed: number, total: number) {
  const safeTotal = Number(total || 0)
  const safeCompleted = Number(completed || 0)
  if (safeTotal <= 0) {
    return 0
  }
  return clampDisplayProgress((safeCompleted / safeTotal) * 100)
}

export function calculateOverallWorkflowProgress(
  workflowProgress: WorkflowProgress[],
  fallbackProgress: number,
) {
  const fallback = clampDisplayProgress(fallbackProgress)
  if (!workflowProgress.length) {
    return fallback
  }

  let completedUnits = 0
  let totalUnits = 0
  const progressValues: number[] = []
  for (const item of workflowProgress) {
    progressValues.push(clampDisplayProgress(item.progress))
    const totalSegments = Number(item.total_segments || 0)
    const completedSegments = Number(item.completed_segments || 0)
    if (totalSegments <= 0) {
      continue
    }
    totalUnits += totalSegments
    completedUnits += Math.max(0, Math.min(completedSegments, totalSegments))
  }

  if (totalUnits > 0) {
    return calculateProgressPercent(completedUnits, totalUnits)
  }
  if (progressValues.length > 0) {
    return clampDisplayProgress(progressValues.reduce((sum, value) => sum + value, 0) / progressValues.length)
  }
  return fallback
}

export function patchTranslationWorkflowProgress(
  workflowProgress: WorkflowProgress[],
  completed: number,
  total: number,
) {
  if (!workflowProgress.length) {
    return workflowProgress
  }
  const progress = calculateProgressPercent(completed, total)
  return workflowProgress.map((item) => (
    item.step_key === 'translate' || item.step_type === 'translation'
      ? {
          ...item,
          progress,
          completed_segments: completed,
          total_segments: total,
        }
      : item
  ))
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
