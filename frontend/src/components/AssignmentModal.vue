<script setup lang="ts">
import {
  AlertTriangle,
  ArrowRight,
  Check,
  RotateCcw,
  Search,
  UserMinus,
  UserPlus,
  Users,
  X,
} from 'lucide-vue-next'
import { computed, nextTick, ref, watch } from 'vue'

import { useToast } from '../composables/useToast'
import type { AssignmentDraft, AssignmentSaveRequest } from '../types/assignment'
import { cloneAssignmentDrafts } from '../types/assignment'
import type { MergeView, User, WorkflowStep } from '../types/api'
import Modal from './base/Modal.vue'
import VirtualList from './VirtualList.vue'

interface AssignmentFile {
  id: string
  filename: string
  total_segments: number
  creator: string | null
  created_at: string
}

interface Allocation {
  assigneeId: string
  fileId: string
  workflowStepId: string
  rangeStart: number | null
  rangeEnd: number | null
}

type FileStateFilter = 'unassigned' | 'assigned' | 'conflict' | 'all'
type UserTypeFilter = 'all' | 'internal' | 'external'

const props = withDefaults(defineProps<{
  open: boolean
  files: AssignmentFile[]
  users: User[]
  workflowSteps: WorkflowStep[]
  mergeViews: MergeView[]
  assignments: AssignmentDraft[]
  revision: string
  loading?: boolean
  saving?: boolean
  initialFileId?: string | null
}>(), {
  loading: false,
  saving: false,
  initialFileId: null,
})

const emit = defineEmits<{
  close: []
  save: [request: AssignmentSaveRequest]
}>()

const toast = useToast()
const draftAssignments = ref<AssignmentDraft[]>([])
const baselineAssignments = ref<AssignmentDraft[]>([])
const initializedRevision = ref('__closed__')
const activeWorkflowStepId = ref('')
const selectedFileIds = ref(new Set<string>())
const selectedAssigneeId = ref('')
const fileSearch = ref('')
const fileStateFilter = ref<FileStateFilter>('unassigned')
const viewFilter = ref('all')
const assigneeFilter = ref('all')
const assigneeSearch = ref('')
const userTypeFilter = ref<UserTypeFilter>('all')
const advancedFileId = ref('')
const transferFileId = ref('')
const rangeStart = ref('')
const rangeEnd = ref('')
const showSaveConfirm = ref(false)
const showDiscardConfirm = ref(false)
const showTransferConfirm = ref(false)
const statusMessage = ref('')
const fileListRef = ref<InstanceType<typeof VirtualList> | null>(null)

function normalizeKeyword(value: string | null | undefined) {
  return String(value || '').trim().toLocaleLowerCase()
}

function getUserName(userId: string) {
  const user = props.users.find((item) => item.id === userId)
  return user?.nickname || user?.username || '未知译者'
}

function getUserSecondaryLabel(user: User) {
  const typeLabel = user.translator_type === 'internal' ? '内部译者' : '外部译者'
  return [user.username, typeLabel].filter(Boolean).join(' · ')
}

function getStepName(stepId: string) {
  return props.workflowSteps.find((step) => step.id === stepId)?.name || '未知步骤'
}

function getAllocations(fileId: string, stepId = activeWorkflowStepId.value, drafts = draftAssignments.value) {
  const allocations: Allocation[] = []
  for (const draft of drafts) {
    if (draft.workflow_step_id !== stepId || !draft.file_record_ids.has(fileId)) {
      continue
    }
    const range = draft.file_ranges.get(fileId)
    allocations.push({
      assigneeId: draft.assignee_id,
      fileId,
      workflowStepId: stepId,
      rangeStart: range?.range_start ?? null,
      rangeEnd: range?.range_end ?? null,
    })
  }
  return allocations
}

function rangesOverlap(left: Allocation, right: Allocation) {
  if (
    left.rangeStart === null
    || left.rangeEnd === null
    || right.rangeStart === null
    || right.rangeEnd === null
  ) {
    return true
  }
  return Math.max(left.rangeStart, right.rangeStart) <= Math.min(left.rangeEnd, right.rangeEnd)
}

function hasAllocationConflict(allocations: Allocation[]) {
  for (let leftIndex = 0; leftIndex < allocations.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < allocations.length; rightIndex += 1) {
      if (rangesOverlap(allocations[leftIndex], allocations[rightIndex])) {
        return true
      }
    }
  }
  return false
}

function getFileState(fileId: string) {
  const allocations = getAllocations(fileId)
  return {
    allocations,
    assigned: allocations.length > 0,
    conflict: hasAllocationConflict(allocations),
    wholeAssigned: allocations.some((item) => item.rangeStart === null || item.rangeEnd === null),
  }
}

const activeStepStats = computed(() => {
  let assigned = 0
  let conflict = 0
  for (const file of props.files) {
    const state = getFileState(file.id)
    if (state.assigned) assigned += 1
    if (state.conflict) conflict += 1
  }
  return {
    total: props.files.length,
    assigned,
    unassigned: Math.max(0, props.files.length - assigned),
    conflict,
  }
})

const activeViewFileIds = computed(() => {
  if (viewFilter.value === 'all') return null
  const view = props.mergeViews.find((item) => item.id === viewFilter.value)
  return new Set(view?.file_ids || [])
})

const filteredFiles = computed(() => {
  const keyword = normalizeKeyword(fileSearch.value)
  return props.files.filter((file) => {
    if (keyword && !normalizeKeyword(file.filename).includes(keyword)) return false
    if (activeViewFileIds.value && !activeViewFileIds.value.has(file.id)) return false
    const state = getFileState(file.id)
    if (fileStateFilter.value === 'unassigned' && state.assigned) return false
    if (fileStateFilter.value === 'assigned' && !state.assigned) return false
    if (fileStateFilter.value === 'conflict' && !state.conflict) return false
    if (
      assigneeFilter.value !== 'all'
      && !state.allocations.some((allocation) => allocation.assigneeId === assigneeFilter.value)
    ) return false
    return true
  })
})

const filteredUsers = computed(() => {
  const keyword = normalizeKeyword(assigneeSearch.value)
  return [...props.users]
    .filter((user) => userTypeFilter.value === 'all' || user.translator_type === userTypeFilter.value)
    .filter((user) => {
      if (!keyword) return true
      return normalizeKeyword([
        user.nickname,
        user.username,
        user.translator_type,
      ].filter(Boolean).join(' ')).includes(keyword)
    })
    .sort((left, right) => {
      const leftMember = isProjectMember(left.id) ? 0 : 1
      const rightMember = isProjectMember(right.id) ? 0 : 1
      if (leftMember !== rightMember) return leftMember - rightMember
      return getUserName(left.id).localeCompare(getUserName(right.id), 'zh-CN')
    })
})

const projectMemberIds = computed(() => new Set(draftAssignments.value.map((draft) => draft.assignee_id)))
const projectMemberCount = computed(() => projectMemberIds.value.size)
const selectedFiles = computed(() => props.files.filter((file) => selectedFileIds.value.has(file.id)))
const advancedFile = computed(() => props.files.find((file) => file.id === advancedFileId.value) || null)
const advancedAllocations = computed(() => advancedFile.value ? getAllocations(advancedFile.value.id) : [])

function allocationKey(allocation: Allocation) {
  return `${allocation.workflowStepId}:${allocation.fileId}:${allocation.assigneeId}`
}

function collectAssignmentState(drafts: AssignmentDraft[]) {
  const members = new Set(drafts.map((draft) => draft.assignee_id))
  const allocations = new Map<string, Allocation>()
  for (const draft of drafts) {
    for (const fileId of draft.file_record_ids) {
      const range = draft.file_ranges.get(fileId)
      const allocation: Allocation = {
        assigneeId: draft.assignee_id,
        fileId,
        workflowStepId: draft.workflow_step_id,
        rangeStart: range?.range_start ?? null,
        rangeEnd: range?.range_end ?? null,
      }
      allocations.set(allocationKey(allocation), allocation)
    }
  }
  return { members, allocations }
}

function sameRange(left: Allocation, right: Allocation) {
  return left.rangeStart === right.rangeStart && left.rangeEnd === right.rangeEnd
}

const assignmentDiff = computed(() => {
  const baseline = collectAssignmentState(baselineAssignments.value)
  const current = collectAssignmentState(draftAssignments.value)
  const items: Array<{ type: 'add' | 'remove' | 'change'; text: string }> = []

  for (const userId of current.members) {
    if (!baseline.members.has(userId)) {
      items.push({ type: 'add', text: `新增项目成员：${getUserName(userId)}` })
    }
  }
  for (const userId of baseline.members) {
    if (!current.members.has(userId)) {
      items.push({ type: 'remove', text: `移除项目成员：${getUserName(userId)}` })
    }
  }
  for (const [key, allocation] of current.allocations) {
    const previous = baseline.allocations.get(key)
    const file = props.files.find((item) => item.id === allocation.fileId)
    const label = `${file?.filename || allocation.fileId} · ${getStepName(allocation.workflowStepId)} · ${getUserName(allocation.assigneeId)}`
    if (!previous) {
      items.push({ type: 'add', text: `新增分配：${label}${formatRangeSuffix(allocation)}` })
    } else if (!sameRange(previous, allocation)) {
      items.push({ type: 'change', text: `调整范围：${label}，${formatRange(previous)} → ${formatRange(allocation)}` })
    }
  }
  for (const [key, allocation] of baseline.allocations) {
    if (current.allocations.has(key)) continue
    const file = props.files.find((item) => item.id === allocation.fileId)
    const label = `${file?.filename || allocation.fileId} · ${getStepName(allocation.workflowStepId)} · ${getUserName(allocation.assigneeId)}`
    items.push({ type: 'remove', text: `取消分配：${label}${formatRangeSuffix(allocation)}` })
  }

  return {
    items,
    added: items.filter((item) => item.type === 'add').length,
    removed: items.filter((item) => item.type === 'remove').length,
    changed: items.filter((item) => item.type === 'change').length,
  }
})

const hasChanges = computed(() => assignmentDiff.value.items.length > 0)

const allConflicts = computed(() => {
  const conflicts: Array<{ fileId: string; stepId: string; allocations: Allocation[] }> = []
  for (const step of props.workflowSteps) {
    for (const file of props.files) {
      const allocations = getAllocations(file.id, step.id)
      if (hasAllocationConflict(allocations)) {
        conflicts.push({ fileId: file.id, stepId: step.id, allocations })
      }
    }
  }
  return conflicts
})

function isProjectMember(userId: string) {
  return draftAssignments.value.some((draft) => draft.assignee_id === userId)
}

function getUserAllocationCount(userId: string) {
  return draftAssignments.value.reduce((count, draft) => (
    draft.assignee_id === userId ? count + draft.file_record_ids.size : count
  ), 0)
}

function ensureDraft(drafts: AssignmentDraft[], userId: string, stepId: string) {
  let draft = drafts.find((item) => item.assignee_id === userId && item.workflow_step_id === stepId)
  if (!draft) {
    draft = {
      assignee_id: userId,
      workflow_step_id: stepId,
      file_record_ids: new Set<string>(),
      file_ranges: new Map(),
    }
    drafts.push(draft)
  }
  return draft
}

function toggleFileSelection(fileId: string) {
  const state = getFileState(fileId)
  if (state.assigned) return
  const next = new Set(selectedFileIds.value)
  if (next.has(fileId)) next.delete(fileId)
  else next.add(fileId)
  selectedFileIds.value = next
  transferFileId.value = ''
}

function selectFilteredAvailableFiles() {
  const next = new Set(selectedFileIds.value)
  let skipped = 0
  for (const file of filteredFiles.value) {
    if (getFileState(file.id).assigned) skipped += 1
    else next.add(file.id)
  }
  selectedFileIds.value = next
  statusMessage.value = skipped > 0
    ? `已选择 ${next.size} 个文件，跳过 ${skipped} 个已分配文件。`
    : `已选择 ${next.size} 个可分配文件。`
}

function clearSelectedFiles() {
  selectedFileIds.value = new Set()
  transferFileId.value = ''
  statusMessage.value = ''
}

function selectFileState(state: FileStateFilter) {
  fileStateFilter.value = state
  if (state === 'unassigned') assigneeFilter.value = 'all'
}

function handleAssigneeFilterChange() {
  if (assigneeFilter.value !== 'all') fileStateFilter.value = 'assigned'
}

function applyWholeFileAssignment(fileIds: string[], userId: string, replaceExisting: boolean) {
  const next = cloneAssignmentDrafts(draftAssignments.value)
  if (replaceExisting) {
    for (const draft of next) {
      if (draft.workflow_step_id !== activeWorkflowStepId.value) continue
      for (const fileId of fileIds) {
        draft.file_record_ids.delete(fileId)
        draft.file_ranges.delete(fileId)
      }
    }
  }
  const target = ensureDraft(next, userId, activeWorkflowStepId.value)
  for (const fileId of fileIds) {
    target.file_record_ids.add(fileId)
    target.file_ranges.delete(fileId)
  }
  draftAssignments.value = next
  clearSelectedFiles()
}

function requestAssignSelectedFiles() {
  if (!selectedAssigneeId.value) {
    toast.error('请先选择译者。')
    return
  }
  if (transferFileId.value) {
    showTransferConfirm.value = true
    return
  }
  const availableIds = selectedFiles.value
    .filter((file) => !getFileState(file.id).assigned)
    .map((file) => file.id)
  if (availableIds.length === 0) {
    toast.error('请先选择可分配文件。')
    return
  }
  applyWholeFileAssignment(availableIds, selectedAssigneeId.value, false)
  statusMessage.value = `已在草稿中分配给 ${getUserName(selectedAssigneeId.value)}。`
}

function startTransfer(fileId: string) {
  transferFileId.value = fileId
  selectedFileIds.value = new Set([fileId])
  selectedAssigneeId.value = ''
  statusMessage.value = '请选择新的译者，然后确认转交。'
}

function confirmTransfer() {
  if (!transferFileId.value || !selectedAssigneeId.value) return
  applyWholeFileAssignment([transferFileId.value], selectedAssigneeId.value, true)
  showTransferConfirm.value = false
  statusMessage.value = `已在草稿中转交给 ${getUserName(selectedAssigneeId.value)}。`
}

function removeAllocation(fileId: string, assigneeId: string) {
  const next = cloneAssignmentDrafts(draftAssignments.value)
  const draft = next.find((item) => (
    item.assignee_id === assigneeId && item.workflow_step_id === activeWorkflowStepId.value
  ))
  draft?.file_record_ids.delete(fileId)
  draft?.file_ranges.delete(fileId)
  draftAssignments.value = next
}

function toggleProjectMembership() {
  const userId = selectedAssigneeId.value
  if (!userId) return
  const next = cloneAssignmentDrafts(draftAssignments.value)
  if (isProjectMember(userId)) {
    if (getUserAllocationCount(userId) > 0) {
      toast.error('该译者仍有文件任务，请先取消或转交任务。')
      return
    }
    draftAssignments.value = next.filter((draft) => draft.assignee_id !== userId)
    return
  }
  const stepId = props.workflowSteps[0]?.id || activeWorkflowStepId.value
  if (!stepId) return
  ensureDraft(next, userId, stepId)
  draftAssignments.value = next
}

function openAdvancedRange(fileId: string) {
  advancedFileId.value = fileId
  rangeStart.value = ''
  rangeEnd.value = ''
}

function addOrUpdateRange() {
  const file = advancedFile.value
  const userId = selectedAssigneeId.value
  const start = Number(rangeStart.value)
  const end = Number(rangeEnd.value)
  if (!file || !userId) {
    toast.error('请先选择文件和译者。')
    return
  }
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) {
    toast.error('请输入有效的起始和结束句段。')
    return
  }
  if (file.total_segments > 0 && end > file.total_segments) {
    toast.error(`结束句段不能超过 ${file.total_segments}。`)
    return
  }
  const candidate: Allocation = {
    assigneeId: userId,
    fileId: file.id,
    workflowStepId: activeWorkflowStepId.value,
    rangeStart: start,
    rangeEnd: end,
  }
  const conflict = advancedAllocations.value.some((allocation) => (
    allocation.assigneeId !== userId && rangesOverlap(candidate, allocation)
  ))
  if (conflict) {
    toast.error('该句段范围与其他译者的任务重叠。')
    return
  }
  const next = cloneAssignmentDrafts(draftAssignments.value)
  const draft = ensureDraft(next, userId, activeWorkflowStepId.value)
  draft.file_record_ids.add(file.id)
  draft.file_ranges.set(file.id, { range_start: start, range_end: end })
  draftAssignments.value = next
  rangeStart.value = ''
  rangeEnd.value = ''
}

function formatRange(allocation: Allocation) {
  if (allocation.rangeStart === null || allocation.rangeEnd === null) return '整文件'
  return `${allocation.rangeStart}–${allocation.rangeEnd} 段`
}

function formatRangeSuffix(allocation: Allocation) {
  return allocation.rangeStart === null || allocation.rangeEnd === null
    ? ''
    : `（${formatRange(allocation)}）`
}

function getMergeViewMeta(view: MergeView) {
  return `${(view.file_ids || []).filter((fileId) => props.files.some((file) => file.id === fileId)).length} 个文件`
}

function getCheckedMergeViewIds(draft: AssignmentDraft) {
  return props.mergeViews
    .filter((view) => {
      const fileIds = (view.file_ids || []).filter((fileId) => props.files.some((file) => file.id === fileId))
      return fileIds.length > 0 && fileIds.every((fileId) => draft.file_record_ids.has(fileId))
    })
    .map((view) => view.id)
}

function buildSaveRequest(): AssignmentSaveRequest {
  return {
    base_revision: props.revision,
    assignments: draftAssignments.value.map((draft) => {
      const file_record_ids: string[] = []
      const file_ranges: Array<{ file_record_id: string; range_start: number; range_end: number }> = []
      for (const fileId of draft.file_record_ids) {
        const range = draft.file_ranges.get(fileId)
        if (range && range.range_start !== null && range.range_end !== null) {
          file_ranges.push({
            file_record_id: fileId,
            range_start: range.range_start,
            range_end: range.range_end,
          })
        } else {
          file_record_ids.push(fileId)
        }
      }
      return {
        assignee_id: draft.assignee_id,
        workflow_step_id: draft.workflow_step_id,
        file_record_ids,
        file_ranges,
        merge_view_ids: getCheckedMergeViewIds(draft),
      }
    }),
  }
}

function requestSave() {
  if (!hasChanges.value) {
    toast.info('当前没有待保存的变更。')
    return
  }
  if (allConflicts.value.length > 0) {
    fileStateFilter.value = 'conflict'
    assigneeFilter.value = 'all'
    toast.error(`仍有 ${allConflicts.value.length} 个分配冲突，请先处理。`)
    return
  }
  showSaveConfirm.value = true
}

function confirmSave() {
  showSaveConfirm.value = false
  emit('save', buildSaveRequest())
}

function requestClose() {
  if (props.saving) return
  if (hasChanges.value) {
    showDiscardConfirm.value = true
    return
  }
  emit('close')
}

function discardAndClose() {
  showDiscardConfirm.value = false
  emit('close')
}

function resetFilters() {
  fileSearch.value = ''
  fileStateFilter.value = 'unassigned'
  viewFilter.value = 'all'
  assigneeFilter.value = 'all'
  assigneeSearch.value = ''
  userTypeFilter.value = 'all'
  clearSelectedFiles()
}

async function focusInitialFile() {
  const fileId = props.initialFileId
  if (!fileId || !props.files.some((file) => file.id === fileId)) return
  fileStateFilter.value = 'all'
  const state = getFileState(fileId)
  if (!state.assigned) selectedFileIds.value = new Set([fileId])
  await nextTick()
  const index = filteredFiles.value.findIndex((file) => file.id === fileId)
  if (index >= 0) await fileListRef.value?.scrollToIndex(index, 'center')
}

function initializeDraft() {
  draftAssignments.value = cloneAssignmentDrafts(props.assignments)
  baselineAssignments.value = cloneAssignmentDrafts(props.assignments)
  initializedRevision.value = props.revision
  activeWorkflowStepId.value = props.workflowSteps[0]?.id || ''
  selectedAssigneeId.value = ''
  advancedFileId.value = ''
  transferFileId.value = ''
  showSaveConfirm.value = false
  showDiscardConfirm.value = false
  showTransferConfirm.value = false
  statusMessage.value = ''
  resetFilters()
  void focusInitialFile()
}

watch(
  [() => props.open, () => props.loading, () => props.revision, () => props.assignments],
  ([open, loading]) => {
    if (!open) {
      initializedRevision.value = '__closed__'
      return
    }
    if (!loading && initializedRevision.value !== props.revision) initializeDraft()
  },
  { immediate: true },
)

watch(activeWorkflowStepId, () => {
  clearSelectedFiles()
  advancedFileId.value = ''
  fileStateFilter.value = 'unassigned'
  assigneeFilter.value = 'all'
})
</script>

<template>
  <Modal
    :open="open"
    title="分配任务"
    description="按文件批量指派；已分配文件需要明确转交，句段拆分请使用高级操作。"
    width="min(1160px, calc(100vw - 32px))"
    :close-on-overlay="!saving && !showSaveConfirm && !showDiscardConfirm && !showTransferConfirm"
    :close-on-esc="!saving && !showSaveConfirm && !showDiscardConfirm && !showTransferConfirm"
    @close="requestClose"
  >
    <div class="assignment-workbench" data-testid="assignment-workbench">
      <div v-if="loading" class="assignment-state">正在加载任务分配信息...</div>

      <template v-else>
        <div class="assignment-steps" role="tablist" aria-label="工作流步骤">
          <button
            v-for="step in workflowSteps"
            :key="step.id"
            class="assignment-step"
            :class="{ 'is-active': activeWorkflowStepId === step.id }"
            type="button"
            role="tab"
            :aria-selected="activeWorkflowStepId === step.id"
            :disabled="saving"
            @click="activeWorkflowStepId = step.id"
          >
            {{ step.name }}
          </button>
        </div>

        <div class="assignment-overview" aria-live="polite">
          <button type="button" @click="selectFileState('unassigned')">待分配 {{ activeStepStats.unassigned }}</button>
          <button type="button" @click="selectFileState('assigned')">已分配 {{ activeStepStats.assigned }}</button>
          <button
            v-if="activeStepStats.conflict"
            class="is-danger"
            type="button"
            @click="selectFileState('conflict')"
          >
            冲突 {{ activeStepStats.conflict }}
          </button>
          <span>共 {{ activeStepStats.total }} 个文件 · {{ projectMemberCount }} 位项目成员</span>
        </div>

        <div class="assignment-layout">
          <section class="assignment-files" aria-label="文件分配列表">
            <div class="assignment-toolbar">
              <label class="assignment-search" data-testid="assignment-file-search">
                <Search :size="15" />
                <input v-model="fileSearch" type="search" placeholder="搜索文件名" :disabled="saving" />
                <button v-if="fileSearch" type="button" aria-label="清空文件搜索" @click="fileSearch = ''">
                  <X :size="13" />
                </button>
              </label>
              <select
                v-model="fileStateFilter"
                :disabled="saving"
                aria-label="文件分配状态"
                @change="selectFileState(fileStateFilter)"
              >
                <option value="unassigned">待分配</option>
                <option value="assigned">已分配</option>
                <option value="conflict">存在冲突</option>
                <option value="all">全部文件</option>
              </select>
              <select v-model="viewFilter" :disabled="saving" aria-label="视图筛选">
                <option value="all">全部视图</option>
                <option v-for="view in mergeViews" :key="view.id" :value="view.id">
                  {{ view.name }}（{{ getMergeViewMeta(view) }}）
                </option>
              </select>
              <select
                v-model="assigneeFilter"
                :disabled="saving"
                aria-label="负责人筛选"
                @change="handleAssigneeFilterChange"
              >
                <option value="all">全部负责人</option>
                <option v-for="user in users" :key="user.id" :value="user.id">
                  {{ getUserName(user.id) }}
                </option>
              </select>
            </div>

            <div class="assignment-bulkbar">
              <span>已选 {{ selectedFileIds.size }} 个</span>
              <button type="button" :disabled="saving || filteredFiles.length === 0" @click="selectFilteredAvailableFiles">
                <Check :size="13" />选择筛选中的可分配文件
              </button>
              <button type="button" :disabled="saving || selectedFileIds.size === 0" @click="clearSelectedFiles">
                <X :size="13" />清空选择
              </button>
              <button type="button" :disabled="saving" @click="resetFilters">
                <RotateCcw :size="13" />重置筛选
              </button>
            </div>

            <div v-if="filteredFiles.length === 0" class="assignment-state">没有符合条件的文件。</div>
            <VirtualList
              v-else
              ref="fileListRef"
              class="assignment-file-list"
              data-testid="assignment-file-list"
              :items="filteredFiles"
              item-key="id"
              :item-height="78"
              :overscan="5"
              adaptive
            >
              <template #default="{ item: file }">
                <article
                  class="assignment-file-row"
                  data-testid="assignment-file-row"
                  :class="{
                    'is-selected': selectedFileIds.has(file.id),
                    'is-assigned': getFileState(file.id).assigned,
                    'is-conflict': getFileState(file.id).conflict,
                  }"
                >
                  <label class="assignment-file-select">
                    <input
                      type="checkbox"
                      :checked="selectedFileIds.has(file.id)"
                      :disabled="saving || getFileState(file.id).assigned"
                      @change="toggleFileSelection(file.id)"
                    />
                    <span>
                      <strong :title="file.filename">{{ file.filename }}</strong>
                      <small>
                        {{ file.total_segments }} 段
                        <template v-if="file.creator"> · 创建人 {{ file.creator }}</template>
                      </small>
                    </span>
                  </label>

                  <div class="assignment-file-status">
                    <span v-if="!getFileState(file.id).assigned" class="assignment-badge is-free">待分配</span>
                    <template v-else>
                      <span
                        v-for="allocation in getFileState(file.id).allocations"
                        :key="`${allocation.assigneeId}-${allocation.rangeStart}-${allocation.rangeEnd}`"
                        class="assignment-owner"
                      >
                        <span>{{ getUserName(allocation.assigneeId) }} · {{ formatRange(allocation) }}</span>
                        <button
                          type="button"
                          :aria-label="`取消 ${getUserName(allocation.assigneeId)} 的分配`"
                          :disabled="saving"
                          @click="removeAllocation(file.id, allocation.assigneeId)"
                        >
                          <X :size="12" />
                        </button>
                      </span>
                    </template>
                  </div>

                  <div class="assignment-file-actions">
                    <button
                      v-if="getFileState(file.id).allocations.length === 1 && getFileState(file.id).wholeAssigned"
                      type="button"
                      :disabled="saving"
                      @click="startTransfer(file.id)"
                    >
                      <ArrowRight :size="13" />转交
                    </button>
                    <button type="button" :disabled="saving" @click="openAdvancedRange(file.id)">高级拆分</button>
                  </div>
                </article>
              </template>
            </VirtualList>
          </section>

          <aside class="assignment-assignee-panel">
            <div class="assignment-panel-title">
              <div>
                <strong>{{ advancedFile ? '句段拆分' : transferFileId ? '转交文件' : '选择译者' }}</strong>
                <span v-if="selectedFiles.length">已选 {{ selectedFiles.length }} 个文件</span>
              </div>
              <button v-if="advancedFile" type="button" aria-label="关闭句段拆分" @click="advancedFileId = ''">
                <X :size="14" />
              </button>
            </div>

            <template v-if="advancedFile">
              <div class="assignment-advanced-file">
                <strong :title="advancedFile.filename">{{ advancedFile.filename }}</strong>
                <span>共 {{ advancedFile.total_segments }} 段</span>
              </div>
              <div v-if="advancedAllocations.length" class="assignment-range-list">
                <div v-for="allocation in advancedAllocations" :key="allocationKey(allocation)">
                  <span>{{ getUserName(allocation.assigneeId) }}</span>
                  <strong>{{ formatRange(allocation) }}</strong>
                  <button type="button" :disabled="saving" @click="removeAllocation(advancedFile.id, allocation.assigneeId)">
                    移除
                  </button>
                </div>
              </div>
            </template>

            <div class="assignment-assignee-filters">
              <label class="assignment-search" data-testid="assignment-user-search">
                <Search :size="15" />
                <input v-model="assigneeSearch" type="search" placeholder="搜索译者" :disabled="saving" />
                <button v-if="assigneeSearch" type="button" aria-label="清空译者搜索" @click="assigneeSearch = ''">
                  <X :size="13" />
                </button>
              </label>
              <select v-model="userTypeFilter" :disabled="saving" aria-label="译者类型">
                <option value="all">全部类型</option>
                <option value="internal">内部</option>
                <option value="external">外部</option>
              </select>
            </div>

            <div class="assignment-user-list" data-testid="assignment-user-list" role="listbox" aria-label="译者列表">
              <button
                v-for="user in filteredUsers"
                :key="user.id"
                type="button"
                role="option"
                :aria-selected="selectedAssigneeId === user.id"
                :class="{ 'is-active': selectedAssigneeId === user.id }"
                :disabled="saving"
                :title="`${getUserName(user.id)} · ${getUserSecondaryLabel(user)}`"
                @click="selectedAssigneeId = user.id"
              >
                <span>
                  <strong>{{ getUserName(user.id) }}</strong>
                  <small>{{ getUserSecondaryLabel(user) }}</small>
                </span>
                <em v-if="isProjectMember(user.id)">成员 {{ getUserAllocationCount(user.id) }}</em>
              </button>
            </div>

            <template v-if="advancedFile">
              <div class="assignment-range-form">
                <label>起始段<input v-model="rangeStart" type="number" min="1" :disabled="saving" /></label>
                <span>—</span>
                <label>结束段<input v-model="rangeEnd" type="number" min="1" :disabled="saving" /></label>
              </div>
              <button
                class="assignment-primary-action"
                data-testid="assignment-range-apply-button"
                type="button"
                :disabled="saving || !selectedAssigneeId"
                @click="addOrUpdateRange"
              >
                <Check :size="14" />添加或更新句段范围
              </button>
            </template>
            <template v-else>
              <button
                class="assignment-primary-action"
                data-testid="assignment-apply-button"
                type="button"
                :disabled="saving || !selectedAssigneeId || selectedFileIds.size === 0"
                @click="requestAssignSelectedFiles"
              >
                <Users :size="14" />{{ transferFileId ? '转交给所选译者' : '分配给所选译者' }}
              </button>
              <button
                class="assignment-member-action"
                type="button"
                :disabled="saving || !selectedAssigneeId"
                @click="toggleProjectMembership"
              >
                <UserMinus v-if="selectedAssigneeId && isProjectMember(selectedAssigneeId)" :size="14" />
                <UserPlus v-else :size="14" />
                {{ selectedAssigneeId && isProjectMember(selectedAssigneeId) ? '移除项目成员' : '仅加入项目成员' }}
              </button>
            </template>
          </aside>
        </div>

        <p v-if="statusMessage" class="assignment-message" aria-live="polite">{{ statusMessage }}</p>
      </template>

      <div v-if="showSaveConfirm" class="assignment-confirm-backdrop" role="presentation">
        <section class="assignment-confirm" role="alertdialog" aria-modal="true" aria-label="确认保存任务分配">
          <header><strong>确认保存分配</strong><span>请检查本次变更，尤其是取消和转交项。</span></header>
          <div class="assignment-diff-summary">
            <span class="is-add">新增 {{ assignmentDiff.added }}</span>
            <span class="is-change">调整 {{ assignmentDiff.changed }}</span>
            <span class="is-remove">取消 {{ assignmentDiff.removed }}</span>
          </div>
          <div class="assignment-diff-list">
            <p v-for="(item, index) in assignmentDiff.items" :key="`${item.type}-${index}`" :class="`is-${item.type}`">
              {{ item.text }}
            </p>
          </div>
          <footer>
            <button type="button" @click="showSaveConfirm = false">返回检查</button>
            <button class="is-primary" type="button" @click="confirmSave">确认保存</button>
          </footer>
        </section>
      </div>

      <div v-if="showDiscardConfirm" class="assignment-confirm-backdrop" role="presentation">
        <section class="assignment-confirm assignment-confirm--small" role="alertdialog" aria-modal="true" aria-label="放弃未保存修改">
          <header><strong>放弃未保存的修改？</strong><span>当前有 {{ assignmentDiff.items.length }} 项变更，关闭后无法恢复。</span></header>
          <footer>
            <button type="button" @click="showDiscardConfirm = false">继续编辑</button>
            <button class="is-danger" type="button" @click="discardAndClose">放弃修改</button>
          </footer>
        </section>
      </div>

      <div v-if="showTransferConfirm" class="assignment-confirm-backdrop" role="presentation">
        <section class="assignment-confirm assignment-confirm--small" role="alertdialog" aria-modal="true" aria-label="确认转交文件">
          <header>
            <strong>确认转交文件？</strong>
            <span>原译者在“{{ getStepName(activeWorkflowStepId) }}”步骤的整文件授权将被取消，并改为 {{ getUserName(selectedAssigneeId) }}。</span>
          </header>
          <footer>
            <button type="button" @click="showTransferConfirm = false">取消</button>
            <button class="is-primary" type="button" @click="confirmTransfer">确认转交</button>
          </footer>
        </section>
      </div>
    </div>

    <template #footer>
      <div class="assignment-footer-summary" aria-live="polite">
        <span v-if="hasChanges">
          待保存：新增 {{ assignmentDiff.added }} · 调整 {{ assignmentDiff.changed }} · 取消 {{ assignmentDiff.removed }}
        </span>
        <span v-else>暂无修改</span>
        <strong v-if="allConflicts.length">{{ allConflicts.length }} 个冲突待处理</strong>
      </div>
      <button class="button" type="button" :disabled="saving" @click="requestClose">取消</button>
      <button
        class="button button--primary"
        data-testid="assignment-save-button"
        type="button"
        :disabled="saving || loading || !hasChanges || allConflicts.length > 0"
        @click="requestSave"
      >
        {{ saving ? '保存中...' : '保存分配' }}
      </button>
    </template>
  </Modal>
</template>

<style scoped>
.assignment-workbench {
  position: relative;
  display: grid;
  gap: 12px;
  height: min(650px, calc(100vh - 257px));
  min-height: min(620px, calc(100vh - 257px));
}

.assignment-steps,
.assignment-overview,
.assignment-bulkbar,
.assignment-file-actions,
.assignment-confirm footer {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.assignment-step,
.assignment-overview button,
.assignment-bulkbar button,
.assignment-file-actions button,
.assignment-member-action,
.assignment-confirm button {
  min-height: 30px;
  padding: 5px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-panel);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.assignment-step.is-active {
  border-color: var(--brand-600);
  background: color-mix(in srgb, var(--brand-100) 82%, var(--surface-panel));
  color: var(--brand-700);
  font-weight: 700;
}

.assignment-overview {
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-muted);
}

.assignment-overview span {
  margin-left: auto;
  color: var(--text-muted);
  font-size: 12px;
}

.assignment-overview button.is-danger,
.assignment-footer-summary strong {
  color: var(--state-danger);
}

.assignment-layout {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 330px);
  gap: 12px;
}

.assignment-files,
.assignment-assignee-panel {
  min-height: 0;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.assignment-files {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: 8px;
  padding: 10px;
}

.assignment-toolbar {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) repeat(3, minmax(120px, 160px));
  gap: 8px;
}

.assignment-search {
  min-width: 0;
  min-height: 36px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border: 1px solid var(--line-soft);
  border-radius: 7px;
  background: var(--surface-1);
  color: var(--text-muted);
}

.assignment-search:focus-within {
  border-color: var(--brand-600);
  box-shadow: var(--focus-ring);
}

.assignment-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text-primary);
}

.assignment-search button,
.assignment-panel-title button,
.assignment-owner button {
  display: grid;
  place-items: center;
  padding: 2px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
}

.assignment-toolbar select,
.assignment-assignee-panel > select {
  min-width: 0;
  min-height: 36px;
  padding: 0 28px 0 10px;
  border: 1px solid var(--line-soft);
  border-radius: 7px;
  background: var(--surface-1);
  color: var(--text-primary);
}

.assignment-bulkbar {
  min-height: 34px;
  color: var(--text-muted);
  font-size: 12px;
}

.assignment-bulkbar button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.assignment-file-list {
  min-height: 180px;
  height: 100%;
  overflow: auto;
}

.assignment-file-row {
  min-height: 70px;
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(180px, auto) auto;
  align-items: center;
  gap: 10px;
  margin: 3px 2px;
  padding: 9px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.assignment-file-row.is-selected {
  border-color: var(--brand-600);
  background: color-mix(in srgb, var(--brand-100) 65%, var(--surface-panel));
}

.assignment-file-row.is-assigned {
  background: var(--surface-muted);
}

.assignment-file-row.is-conflict {
  border-color: var(--state-danger);
  box-shadow: inset 3px 0 0 var(--state-danger);
}

.assignment-file-select {
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 9px;
}

.assignment-file-select > span,
.assignment-panel-title > div,
.assignment-advanced-file {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.assignment-file-select strong,
.assignment-advanced-file strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assignment-file-select small,
.assignment-panel-title span,
.assignment-advanced-file span {
  color: var(--text-muted);
  font-size: 12px;
}

.assignment-file-status {
  min-width: 0;
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 5px;
}

.assignment-badge,
.assignment-owner {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  border-radius: 999px;
  font-size: 11px;
}

.assignment-badge.is-free {
  background: var(--state-success-bg);
  color: var(--state-success);
}

.assignment-owner {
  background: var(--state-info-bg);
  color: var(--state-info);
}

.assignment-file-actions {
  justify-content: flex-end;
}

.assignment-assignee-panel {
  display: grid;
  grid-template-rows: auto auto minmax(120px, 1fr) auto auto;
  align-content: start;
  gap: 9px;
  padding: 10px;
}

.assignment-assignee-filters {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(92px, 112px);
  gap: 6px;
}

.assignment-assignee-filters > select {
  min-width: 0;
  min-height: 36px;
  padding: 0 24px 0 8px;
  border: 1px solid var(--line-soft);
  border-radius: 7px;
  background: var(--surface-1);
  color: var(--text-primary);
}

.assignment-panel-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.assignment-panel-title strong {
  color: var(--text-primary);
  font-size: 14px;
}

.assignment-user-list {
  min-height: 120px;
  height: 100%;
  overflow: auto;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-auto-rows: 40px;
  align-content: start;
  gap: 3px;
}

.assignment-user-list > button {
  width: 100%;
  min-width: 0;
  min-height: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 5px;
  padding: 3px 6px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--text-secondary);
  text-align: left;
}

.assignment-user-list > button:hover,
.assignment-user-list > button.is-active {
  border-color: var(--brand-500);
  background: color-mix(in srgb, var(--brand-100) 68%, var(--surface-panel));
}

.assignment-user-list span {
  min-width: 0;
  display: grid;
  gap: 2px;
  overflow: hidden;
}

.assignment-user-list strong,
.assignment-user-list small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assignment-user-list strong {
  color: var(--text-primary);
  font-size: 13px;
}

.assignment-user-list small,
.assignment-user-list em {
  color: var(--text-muted);
  font-size: 11px;
  font-style: normal;
}

.assignment-user-list em {
  flex: 0 0 auto;
}

.assignment-primary-action {
  min-height: 38px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 7px 12px;
  border: 1px solid var(--brand-700);
  border-radius: 7px;
  background: var(--brand-700);
  color: white;
  font-weight: 700;
}

.assignment-member-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.assignment-advanced-file {
  padding: 8px;
  border-radius: 7px;
  background: var(--surface-muted);
}

.assignment-range-list {
  display: grid;
  gap: 5px;
}

.assignment-range-list > div {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  font-size: 12px;
}

.assignment-range-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: end;
  gap: 6px;
}

.assignment-range-form label {
  display: grid;
  gap: 4px;
  color: var(--text-muted);
  font-size: 11px;
}

.assignment-range-form input {
  min-width: 0;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
}

.assignment-message {
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
}

.assignment-state {
  display: grid;
  place-items: center;
  min-height: 180px;
  color: var(--text-muted);
  font-size: 13px;
}

.assignment-confirm-backdrop {
  position: absolute;
  z-index: 20;
  inset: -12px;
  display: grid;
  place-items: center;
  padding: 16px;
  border-radius: 8px;
  background: rgba(15, 31, 28, 0.48);
}

.assignment-confirm {
  width: min(620px, 100%);
  max-height: min(540px, calc(100vh - 160px));
  display: grid;
  gap: 14px;
  padding: 18px;
  overflow: auto;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--surface-panel);
  box-shadow: 0 24px 70px rgba(15, 31, 28, 0.28);
}

.assignment-confirm--small {
  width: min(460px, 100%);
}

.assignment-confirm header {
  display: grid;
  gap: 5px;
}

.assignment-confirm header strong {
  color: var(--text-primary);
  font-size: 16px;
}

.assignment-confirm header span {
  color: var(--text-muted);
  font-size: 13px;
}

.assignment-diff-summary {
  display: flex;
  gap: 8px;
}

.assignment-diff-summary span {
  padding: 4px 8px;
  border-radius: 999px;
  font-size: 12px;
}

.assignment-diff-summary .is-add,
.assignment-diff-list .is-add {
  color: var(--state-success);
}

.assignment-diff-summary .is-change,
.assignment-diff-list .is-change {
  color: var(--state-warning);
}

.assignment-diff-summary .is-remove,
.assignment-diff-list .is-remove,
.assignment-confirm button.is-danger {
  color: var(--state-danger);
}

.assignment-diff-list {
  display: grid;
  gap: 5px;
}

.assignment-diff-list p {
  margin: 0;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--surface-muted);
  font-size: 12px;
}

.assignment-confirm footer {
  justify-content: flex-end;
}

.assignment-confirm button.is-primary {
  border-color: var(--brand-700);
  background: var(--brand-700);
  color: white;
}

.assignment-footer-summary {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  margin-right: auto;
  color: var(--text-muted);
  font-size: 12px;
}

button:disabled,
select:disabled,
input:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

@media (max-width: 900px) {
  .assignment-workbench {
    min-height: 0;
  }

  .assignment-layout {
    grid-template-columns: 1fr;
  }

  .assignment-toolbar {
    grid-template-columns: 1fr;
  }

  .assignment-file-list {
    height: 420px;
  }

  .assignment-file-row {
    grid-template-columns: 1fr;
  }

  .assignment-file-status,
  .assignment-file-actions {
    justify-content: flex-start;
  }
}

@media (max-width: 560px) {
  .assignment-user-list {
    grid-template-columns: 1fr;
  }
}
</style>
