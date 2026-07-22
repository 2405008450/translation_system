<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import type { CSSProperties } from 'vue'

import type { WorkflowProgress } from '../types/api'
import {
  calculateOverallWorkflowProgress,
  calculateProgressPercent,
  clampDisplayProgress,
  getProgressStyle,
  isProgressComplete,
} from '../utils/progress'

type ProgressDisplayMode = 'overall' | 'current-stage'

const props = withDefaults(defineProps<{
  progress: number
  status?: string
  workflowProgress?: WorkflowProgress[]
  label?: string
  detailTitle?: string
  compact?: boolean
  displayMode?: ProgressDisplayMode
  currentStepId?: string | null
  completedSegments?: number
  totalSegments?: number
}>(), {
  status: '',
  workflowProgress: () => [],
  label: '总进度',
  detailTitle: '阶段进度',
  compact: false,
  displayMode: 'overall',
  currentStepId: null,
  completedSegments: 0,
  totalSegments: 0,
})

const anchorRef = ref<HTMLElement | null>(null)
const panelRef = ref<HTMLElement | null>(null)
const showDetails = ref(false)
const popoverStyle = ref<CSSProperties>({})
let hideTimer: number | null = null

const workflowItems = computed(() => (
  [...(props.workflowProgress || [])]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((item) => {
      const totalSegments = Number(item.total_segments || 0)
      const completedSegments = Number(item.completed_segments || 0)
      return {
        ...item,
        progress: totalSegments > 0
          ? calculateProgressPercent(completedSegments, totalSegments)
          : clampDisplayProgress(item.progress),
        total_segments: totalSegments,
        completed_segments: completedSegments,
      }
    })
))
const hasWorkflowDetails = computed(() => workflowItems.value.length > 0)
const normalizedProgress = computed(() => calculateOverallWorkflowProgress(
  workflowItems.value,
  props.progress,
))
const currentWorkflowItem = computed(() => (
  workflowItems.value.find((item) => item.id === props.currentStepId) ?? null
))
const displayProgress = computed(() => {
  if (props.displayMode === 'current-stage') {
    if (currentWorkflowItem.value) {
      return currentWorkflowItem.value.progress
    }
    if (Number(props.totalSegments || 0) > 0) {
      return calculateProgressPercent(props.completedSegments, props.totalSegments)
    }
  }
  return normalizedProgress.value
})
const displayProgressLabel = computed(() => {
  if (props.displayMode === 'current-stage' && currentWorkflowItem.value) {
    return currentWorkflowItem.value.name
  }
  return props.label
})
const displayCompletedSegments = computed(() => (
  currentWorkflowItem.value?.completed_segments ?? Number(props.completedSegments || 0)
))
const displayTotalSegments = computed(() => (
  currentWorkflowItem.value?.total_segments ?? Number(props.totalSegments || 0)
))
const displayCountText = computed(() => (
  props.displayMode === 'current-stage' && displayTotalSegments.value > 0
    ? `${displayCompletedSegments.value}/${displayTotalSegments.value}`
    : ''
))
const progressAriaLabel = computed(() => (
  `${displayProgressLabel.value} ${displayProgress.value}%${displayCountText.value ? `，${displayCountText.value}` : ''}`
))

function removePositionListeners() {
  window.removeEventListener('resize', updatePopoverPosition)
  window.removeEventListener('scroll', updatePopoverPosition, true)
}

function addPositionListeners() {
  removePositionListeners()
  window.addEventListener('resize', updatePopoverPosition)
  window.addEventListener('scroll', updatePopoverPosition, true)
}

function updatePopoverPosition() {
  const anchor = anchorRef.value
  if (!anchor) {
    return
  }

  const rect = anchor.getBoundingClientRect()
  const viewportWidth = window.innerWidth
  const viewportHeight = window.innerHeight
  const panelWidth = Math.min(320, Math.max(240, viewportWidth - 24))
  const estimatedHeight = Math.min(92 + workflowItems.value.length * 44, 380)
  let left = rect.left + rect.width / 2 - panelWidth / 2
  let top = rect.bottom + 8

  left = Math.max(12, Math.min(left, viewportWidth - panelWidth - 12))
  if (top + estimatedHeight > viewportHeight - 12) {
    top = Math.max(12, rect.top - estimatedHeight - 8)
  }

  popoverStyle.value = {
    left: `${left}px`,
    top: `${top}px`,
    width: `${panelWidth}px`,
  }

  void nextTick(() => {
    const panel = panelRef.value
    if (!panel) {
      return
    }
    const panelRect = panel.getBoundingClientRect()
    let adjustedLeft = panelRect.left
    let adjustedTop = panelRect.top

    if (panelRect.right > viewportWidth - 12) {
      adjustedLeft -= panelRect.right - viewportWidth + 12
    }
    if (panelRect.left < 12) {
      adjustedLeft = 12
    }
    if (panelRect.bottom > viewportHeight - 12) {
      adjustedTop = Math.max(12, rect.top - panelRect.height - 8)
    }

    popoverStyle.value = {
      ...popoverStyle.value,
      left: `${adjustedLeft}px`,
      top: `${adjustedTop}px`,
    }
  })
}

function cancelHide() {
  if (hideTimer !== null) {
    window.clearTimeout(hideTimer)
    hideTimer = null
  }
}

function openDetails() {
  if (!hasWorkflowDetails.value) {
    return
  }
  cancelHide()
  showDetails.value = true
  updatePopoverPosition()
  addPositionListeners()
}

function hideDetails() {
  cancelHide()
  showDetails.value = false
  removePositionListeners()
}

function scheduleHide() {
  cancelHide()
  hideTimer = window.setTimeout(hideDetails, 120)
}

onBeforeUnmount(() => {
  cancelHide()
  removePositionListeners()
})
</script>

<template>
  <div
    ref="anchorRef"
    class="workflow-progress-summary"
    :class="{
      'workflow-progress-summary--compact': compact,
      'has-detail': hasWorkflowDetails,
    }"
    :tabindex="hasWorkflowDetails ? 0 : undefined"
    :aria-label="progressAriaLabel"
    @mouseenter="openDetails"
    @mouseleave="scheduleHide"
    @focusin="openDetails"
    @focusout="scheduleHide"
    @keydown.esc="hideDetails"
  >
    <div class="progress-bar workflow-progress-summary__bar">
      <div class="progress-bar__track">
        <div
          class="progress-bar__fill"
          :class="{
            'has-progress': displayProgress > 0,
            'is-complete': isProgressComplete(displayProgress),
          }"
          :style="getProgressStyle(displayProgress, status)"
        />
      </div>
      <span class="progress-bar__text">
        {{ displayProgress }}%<template v-if="displayCountText"> · {{ displayCountText }}</template>
      </span>
    </div>
  </div>

  <Teleport to="body">
    <div
      v-if="showDetails"
      ref="panelRef"
      class="workflow-progress-popover"
      :style="popoverStyle"
      role="tooltip"
      @mouseenter="cancelHide"
      @mouseleave="scheduleHide"
    >
      <div class="workflow-progress-popover__head">
        <span>{{ detailTitle }}</span>
        <strong>{{ normalizedProgress }}%</strong>
      </div>
      <div class="workflow-progress-popover__list">
        <div
          v-for="item in workflowItems"
          :key="item.id"
          class="workflow-progress-popover__item"
        >
          <div class="workflow-progress-popover__row">
            <span class="workflow-progress-popover__name">{{ item.name }}</span>
            <span class="workflow-progress-popover__value">{{ item.progress }}%</span>
          </div>
          <div class="workflow-progress-popover__bar">
            <div class="progress-bar__track">
              <div
                class="progress-bar__fill"
                :class="{
                  'has-progress': item.progress > 0,
                  'is-complete': isProgressComplete(item.progress),
                }"
                :style="getProgressStyle(item.progress)"
              />
            </div>
            <span
              v-if="item.total_segments > 0"
              class="workflow-progress-popover__count"
            >
              {{ item.completed_segments }}/{{ item.total_segments }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.workflow-progress-summary {
  display: grid;
  width: 100%;
  min-width: 0;
}

.workflow-progress-summary.has-detail {
  cursor: help;
}

.workflow-progress-summary:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--brand-700) 55%, transparent);
  outline-offset: 3px;
  border-radius: 6px;
}

.workflow-progress-summary__bar {
  width: 100%;
}

.workflow-progress-summary--compact :deep(.progress-bar__track) {
  min-width: 58px;
}

.workflow-progress-summary :deep(.progress-bar__fill.has-progress) {
  min-width: 2px;
}

.workflow-progress-popover {
  position: fixed;
  z-index: 1800;
  display: grid;
  gap: 10px;
  padding: 10px 12px;
  border: 0;
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-panel) 88%, transparent);
  backdrop-filter: blur(14px) saturate(1.08);
  box-shadow:
    0 18px 44px rgba(17, 49, 42, 0.14),
    0 2px 8px rgba(17, 49, 42, 0.06);
  color: var(--text-primary);
}

.workflow-progress-popover__head,
.workflow-progress-popover__row,
.workflow-progress-popover__bar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.workflow-progress-popover__head {
  justify-content: space-between;
  padding-bottom: 2px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 700;
}

.workflow-progress-popover__head strong {
  color: var(--brand-700);
  font-size: 13px;
}

.workflow-progress-popover__list {
  display: grid;
  gap: 10px;
}

.workflow-progress-popover__item {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.workflow-progress-popover__row {
  justify-content: space-between;
  min-width: 0;
}

.workflow-progress-popover__name {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workflow-progress-popover__value,
.workflow-progress-popover__count {
  flex: 0 0 auto;
  color: var(--text-muted);
  font-size: 12px;
}

.workflow-progress-popover__bar :deep(.progress-bar__track) {
  min-width: 0;
  height: 7px;
}

.workflow-progress-popover__count {
  min-width: 48px;
  text-align: right;
}

@media (max-width: 640px) {
  .workflow-progress-popover {
    max-width: calc(100vw - 24px);
  }
}
</style>
