export function getProgressStyle(progress: number) {
  const normalized = Math.max(0, Math.min(progress, 100))

  if (normalized >= 80) {
    return { width: `${normalized}%`, background: 'linear-gradient(90deg, #36d1a0, #2f9786)' }
  }

  if (normalized >= 50) {
    return { width: `${normalized}%`, background: 'linear-gradient(90deg, #4db8ff, #1890ff)' }
  }

  if (normalized >= 20) {
    return { width: `${normalized}%`, background: 'linear-gradient(90deg, #ffd666, #faad14)' }
  }

  return { width: `${normalized}%`, background: 'linear-gradient(90deg, #d9e3e0, #c4d2ce)' }
}
