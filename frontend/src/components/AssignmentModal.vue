<script setup lang="ts">
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  Loader2,
  RotateCcw,
  Search,
  UserMinus,
  UserPlus,
  Users,
  X,
} from 'lucide-vue-next'
import { computed, nextTick, ref, watch } from 'vue'

import { useToast } from '../composables/useToast'
import { formatLanguagePair } from '../constants/languages'
import { getApiErrorMessage } from '../utils/apiError'
import type {
  AssignmentDraft,
  AssignmentSaveRequest,
  AssignmentWorkflowTransitionRequired,
} from '../types/assignment'
import { cloneAssignmentDrafts } from '../types/assignment'
import type {
  AssignmentSplitPart,
  AssignmentSplitPreviewRequest,
  AssignmentSplitPreviewResponse,
  MergeView,
  User,
  WorkflowStep,
} from '../types/api'
import Modal from './base/Modal.vue'
import VirtualList from './VirtualList.vue'

interface AssignmentFile {
  id: string
  filename: string
  total_segments: number
  creator: string | null
  created_at: string
  source_language: string | null
  target_language: string | null
}

interface Allocation {
  assigneeId: string
  fileId: string
  workflowStepId: string
  rangeStart: number | null
  rangeEnd: number | null
}

type FileStateFilter = 'unassigned' | 'assigned' | 'conflict' | 'all'
type SplitMode = AssignmentSplitPreviewRequest['mode']
type SplitDraftPart = AssignmentSplitPart & { assignee_id: string }
type AdvancedEditorMode = 'smart' | 'manual'

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
  previewSplit?: (
    payload: AssignmentSplitPreviewRequest,
  ) => Promise<AssignmentSplitPreviewResponse>
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
const advancedFileId = ref('')
const transferFileId = ref('')
const rangeStart = ref('')
const rangeEnd = ref('')
const splitMode = ref<SplitMode>('by_part_count')
const splitWordsPerPart = ref('1000')
const splitPreview = ref<AssignmentSplitPreviewResponse | null>(null)
const splitParts = ref<SplitDraftPart[]>([])
const splitLoading = ref(false)
const showSplitOverwriteConfirm = ref(false)
const advancedEditorMode = ref<AdvancedEditorMode>('smart')
const smartAssigneeIds = ref<string[]>([])
const showSaveConfirm = ref(false)
const showWorkflowTransitionConfirm = ref(false)
const workflowTransitionPrompt = ref<AssignmentWorkflowTransitionRequired | null>(null)
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
  return user.username
}

function getUserSecondaryLabelById(userId: string) {
  const user = props.users.find((item) => item.id === userId)
  return user ? getUserSecondaryLabel(user) : ''
}

function getStepName(stepId: string) {
  return props.workflowSteps.find((step) => step.id === stepId)?.name || '未知步骤'
}

function getStepToneClass(stepType: string) {
  if (stepType === 'translation') return 'is-step-translation'
  if (stepType === 'review') return 'is-step-review'
  if (stepType === 'proofread') return 'is-step-proofread'
  return 'is-step-custom'
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

const orderedUsers = computed(() => props.users
  .filter((user) => user.translator_type === 'external')
  .sort((left, right) => {
    const leftMember = isProjectMember(left.id) ? 0 : 1
    const rightMember = isProjectMember(right.id) ? 0 : 1
    if (leftMember !== rightMember) return leftMember - rightMember
    return getUserName(left.id).localeCompare(getUserName(right.id), 'zh-CN')
  }))

const filteredUsers = computed(() => {
  const keyword = normalizeKeyword(assigneeSearch.value)
  return orderedUsers.value
    .filter((user) => {
      if (!keyword) return true
      return normalizeKeyword([
        user.nickname,
        user.username,
      ].filter(Boolean).join(' ')).includes(keyword)
    })
})

const projectMemberIds = computed(() => new Set(draftAssignments.value.map((draft) => draft.assignee_id)))
const projectMemberCount = computed(() => projectMemberIds.value.size)
const selectedFiles = computed(() => props.files.filter((file) => selectedFileIds.value.has(file.id)))
const advancedFile = computed(() => props.files.find((file) => file.id === advancedFileId.value) || null)
const advancedAllocations = computed(() => advancedFile.value ? getAllocations(advancedFile.value.id) : [])
const selectedSmartAssignees = computed(() => smartAssigneeIds.value
  .map((userId) => props.users.find((user) => user.id === userId))
  .filter((user): user is User => Boolean(user)))

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
  selectedAssigneeId.value = ''
  assigneeSearch.value = ''
  advancedEditorMode.value = 'smart'
  smartAssigneeIds.value = Array.from(new Set(
    getAllocations(fileId).map((allocation) => allocation.assigneeId),
  ))
  resetSplitPreview()
}

function closeAdvancedRange() {
  advancedFileId.value = ''
  smartAssigneeIds.value = []
  selectedAssigneeId.value = ''
  resetSplitPreview()
}

function toggleSmartAssignee(userId: string) {
  if (smartAssigneeIds.value.includes(userId)) {
    smartAssigneeIds.value = smartAssigneeIds.value.filter((id) => id !== userId)
  } else {
    smartAssigneeIds.value = [...smartAssigneeIds.value, userId]
  }
  resetSplitPreview()
}

function resetSplitPreview() {
  splitPreview.value = null
  splitParts.value = []
  splitLoading.value = false
  showSplitOverwriteConfirm.value = false
}

function formatWordCount(value: number | null | undefined) {
  return value === null || value === undefined ? '暂无' : value.toLocaleString('zh-CN')
}

async function generateSplitPreview() {
  const file = advancedFile.value
  if (!file || !props.previewSplit) {
    toast.error('当前环境未提供按字数拆分预览。')
    return
  }
  const numericValue = Number(splitMode.value === 'by_part_count'
    ? smartAssigneeIds.value.length
    : splitWordsPerPart.value)
  if (!Number.isInteger(numericValue) || numericValue < 1) {
    toast.error(splitMode.value === 'by_part_count' ? '请至少选择一位参与译者。' : '请输入有效的每份字数。')
    return
  }

  splitLoading.value = true
  splitPreview.value = null
  splitParts.value = []
  try {
    const result = await props.previewSplit({
      file_record_id: file.id,
      mode: splitMode.value,
      ...(splitMode.value === 'by_part_count'
        ? { part_count: numericValue }
        : { words_per_part: numericValue }),
    })
    splitPreview.value = result
    splitParts.value = result.parts.map((part, index) => ({
      ...part,
      assignee_id: smartAssigneeIds.value[index] || '',
    }))
  } catch (error) {
    toast.error(getApiErrorMessage(error, '生成拆分方案失败。'))
  } finally {
    splitLoading.value = false
  }
}

function autoFillSplitAssignees() {
  if (!splitParts.value.length) return
  if (smartAssigneeIds.value.length < splitParts.value.length) {
    toast.error(`已选 ${smartAssigneeIds.value.length} 位译者，不足以分配 ${splitParts.value.length} 份。`)
    return
  }
  splitParts.value = splitParts.value.map((part, index) => ({
    ...part,
    assignee_id: smartAssigneeIds.value[index] || '',
  }))
}

function applySplitPartsNow() {
  const file = advancedFile.value
  if (!file) return
  const allocations: Allocation[] = splitParts.value.map((part) => ({
    assigneeId: part.assignee_id,
    fileId: file.id,
    workflowStepId: activeWorkflowStepId.value,
    rangeStart: part.range_start,
    rangeEnd: part.range_end,
  }))
  if (hasAllocationConflict(allocations)) {
    toast.error('拆分方案存在重叠范围，无法应用。')
    return
  }

  const next = cloneAssignmentDrafts(draftAssignments.value)
  for (const draft of next) {
    if (draft.workflow_step_id !== activeWorkflowStepId.value) continue
    draft.file_record_ids.delete(file.id)
    draft.file_ranges.delete(file.id)
  }
  for (const part of splitParts.value) {
    const draft = ensureDraft(next, part.assignee_id, activeWorkflowStepId.value)
    draft.file_record_ids.add(file.id)
    draft.file_ranges.set(file.id, {
      range_start: part.range_start,
      range_end: part.range_end,
    })
  }
  draftAssignments.value = next
  showSplitOverwriteConfirm.value = false
  statusMessage.value = `已将 ${file.filename} 的 ${splitParts.value.length} 份安全范围应用到草稿。`
}

function requestApplySplitParts() {
  if (!splitParts.value.length) return
  if (splitParts.value.some((part) => !part.assignee_id)) {
    toast.error('请为每一份选择译者。')
    return
  }
  const assigneeIds = splitParts.value.map((part) => part.assignee_id)
  if (new Set(assigneeIds).size !== assigneeIds.length) {
    toast.error('同一文件的每个拆分范围需指派给不同译者。')
    return
  }
  if (advancedAllocations.value.length > 0) {
    showSplitOverwriteConfirm.value = true
    return
  }
  applySplitPartsNow()
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
  emit('save', { ...buildSaveRequest(), workflow_transition_mode: 'prompt' })
}

function showWorkflowTransitionPrompt(detail: AssignmentWorkflowTransitionRequired) {
  workflowTransitionPrompt.value = detail
  showWorkflowTransitionConfirm.value = true
}

function submitWorkflowTransitionChoice(mode: 'advance' | 'assign_only') {
  emit('save', { ...buildSaveRequest(), workflow_transition_mode: mode })
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
  showWorkflowTransitionConfirm.value = false
  workflowTransitionPrompt.value = null
  showDiscardConfirm.value = false
  showTransferConfirm.value = false
  resetSplitPreview()
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

watch(splitMode, resetSplitPreview)

defineExpose({ showWorkflowTransitionPrompt })
</script>

<template>
  <Modal
    :open="open"
    title="分配任务"
    description="按文件批量指派；已分配文件需要明确转交，句段拆分请使用高级操作。"
    width="min(1160px, calc(100vw - 32px))"
    :close-on-overlay="!saving && !splitLoading && !showSaveConfirm && !showWorkflowTransitionConfirm && !showDiscardConfirm && !showTransferConfirm && !showSplitOverwriteConfirm"
    :close-on-esc="!saving && !splitLoading && !showSaveConfirm && !showWorkflowTransitionConfirm && !showDiscardConfirm && !showTransferConfirm && !showSplitOverwriteConfirm"
    @close="requestClose"
  >
    <div class="assignment-workbench" :class="{ 'is-advanced': advancedFile }" data-testid="assignment-workbench">
      <div v-if="loading" class="assignment-state">正在加载任务分配信息...</div>

      <template v-else>
        <div v-if="!advancedFile" class="assignment-steps" role="tablist" aria-label="工作流步骤">
          <button
            v-for="step in workflowSteps"
            :key="step.id"
            class="assignment-step"
            :class="[getStepToneClass(step.step_type), { 'is-active': activeWorkflowStepId === step.id }]"
            type="button"
            role="tab"
            :aria-selected="activeWorkflowStepId === step.id"
            :disabled="saving"
            @click="activeWorkflowStepId = step.id"
          >
            {{ step.name }}
          </button>
        </div>

        <div v-if="!advancedFile" class="assignment-overview" aria-live="polite">
          <button
            class="is-unassigned"
            :class="{ 'is-active': fileStateFilter === 'unassigned' }"
            type="button"
            @click="selectFileState('unassigned')"
          >
            待分配 {{ activeStepStats.unassigned }}
          </button>
          <button
            class="is-assigned"
            :class="{ 'is-active': fileStateFilter === 'assigned' }"
            type="button"
            @click="selectFileState('assigned')"
          >
            已分配 {{ activeStepStats.assigned }}
          </button>
          <button
            v-if="activeStepStats.conflict"
            class="is-danger"
            :class="{ 'is-active': fileStateFilter === 'conflict' }"
            type="button"
            @click="selectFileState('conflict')"
          >
            冲突 {{ activeStepStats.conflict }}
          </button>
          <span>共 {{ activeStepStats.total }} 个文件 · {{ projectMemberCount }} 位项目成员</span>
        </div>

        <div class="assignment-layout" :class="{ 'is-advanced': advancedFile }">
          <section v-if="!advancedFile" class="assignment-files" aria-label="文件分配列表">
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
                        语言对：{{ formatLanguagePair(file.source_language, file.target_language) }}
                        · {{ file.total_segments }} 段
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

          <aside class="assignment-assignee-panel" :class="{ 'is-advanced': advancedFile }">
            <template v-if="advancedFile">
              <div class="assignment-advanced-header">
                <button type="button" class="assignment-back-button" @click="closeAdvancedRange">
                  <ArrowLeft :size="15" />返回文件列表
                </button>
                <div>
                  <strong :title="advancedFile.filename">{{ advancedFile.filename }}</strong>
                  <span>
                    {{ formatLanguagePair(advancedFile.source_language, advancedFile.target_language) }}
                    · {{ advancedFile.total_segments }} 个句段
                    · 阶段：{{ getStepName(activeWorkflowStepId) }}
                    · 当前 {{ advancedAllocations.length }} 条分配
                  </span>
                </div>
              </div>

              <div v-if="advancedAllocations.length" class="assignment-current-ranges">
                <span>当前任务</span>
                <div>
                  <span v-for="allocation in advancedAllocations" :key="allocationKey(allocation)">
                    {{ getUserName(allocation.assigneeId) }} · {{ formatRange(allocation) }}
                    <button
                      v-if="advancedEditorMode === 'manual'"
                      type="button"
                      :aria-label="`移除 ${getUserName(allocation.assigneeId)} 的范围`"
                      :disabled="saving"
                      @click="removeAllocation(advancedFile.id, allocation.assigneeId)"
                    ><X :size="11" /></button>
                  </span>
                </div>
                <small v-if="advancedEditorMode === 'smart'">应用新方案时将整体替换当前任务。</small>
              </div>

              <div class="assignment-editor-tabs" role="tablist" aria-label="拆分方式">
                <button
                  type="button"
                  role="tab"
                  :aria-selected="advancedEditorMode === 'smart'"
                  :class="{ 'is-active': advancedEditorMode === 'smart' }"
                  @click="advancedEditorMode = 'smart'"
                >智能均分</button>
                <button
                  type="button"
                  role="tab"
                  :aria-selected="advancedEditorMode === 'manual'"
                  :class="{ 'is-active': advancedEditorMode === 'manual' }"
                  @click="advancedEditorMode = 'manual'"
                >手动指定范围</button>
              </div>

              <section v-if="advancedEditorMode === 'smart'" class="assignment-auto-split" data-testid="assignment-auto-split">
                <div class="assignment-split-setup">
                  <div class="assignment-step-heading">
                    <em>1</em>
                    <span><strong>选择拆分规则</strong><small>系统只会在完整句段或段落边界切分</small></span>
                  </div>
                  <div class="assignment-mode-switch" role="radiogroup" aria-label="拆分模式">
                    <button
                      type="button"
                      role="radio"
                      :aria-checked="splitMode === 'by_part_count'"
                      :class="{ 'is-active': splitMode === 'by_part_count' }"
                      :disabled="saving || splitLoading"
                      @click="splitMode = 'by_part_count'"
                    >按所选译者均分</button>
                    <button
                      type="button"
                      role="radio"
                      :aria-checked="splitMode === 'by_words_per_part'"
                      :class="{ 'is-active': splitMode === 'by_words_per_part' }"
                      :disabled="saving || splitLoading"
                      @click="splitMode = 'by_words_per_part'"
                    >按每份目标字数</button>
                  </div>
                  <label v-if="splitMode === 'by_words_per_part'" class="assignment-word-target">
                    每份目标字数
                    <input v-model="splitWordsPerPart" type="number" min="1" :disabled="saving || splitLoading" />
                    <small>实际字数会因完整句段保护略有浮动。</small>
                  </label>

                  <div class="assignment-step-heading">
                    <em>2</em>
                    <span>
                      <strong>{{ splitMode === 'by_part_count' ? '选择参与译者' : '预选参与译者' }}</strong>
                      <small>
                        {{ splitMode === 'by_part_count'
                          ? `已选 ${smartAssigneeIds.length} 人，将自动生成 ${smartAssigneeIds.length} 份`
                          : `已选 ${smartAssigneeIds.length} 人，生成后仍可逐份调整` }}
                      </small>
                    </span>
                  </div>
                  <div class="assignment-assignee-filters">
                    <label class="assignment-search" data-testid="assignment-user-search">
                      <Search :size="15" />
                      <input v-model="assigneeSearch" type="search" placeholder="搜索外部译者姓名或账号" :disabled="saving" />
                      <button v-if="assigneeSearch" type="button" aria-label="清空译者搜索" @click="assigneeSearch = ''">
                        <X :size="13" />
                      </button>
                    </label>
                  </div>
                  <div v-if="selectedSmartAssignees.length" class="assignment-selected-users" aria-label="已选译者">
                    <button
                      v-for="user in selectedSmartAssignees"
                      :key="user.id"
                      type="button"
                      :disabled="saving || splitLoading"
                      @click="toggleSmartAssignee(user.id)"
                    >{{ getUserName(user.id) }}<X :size="11" /></button>
                  </div>
                  <div class="assignment-smart-user-list" role="listbox" aria-label="参与译者" aria-multiselectable="true">
                    <button
                      v-for="user in filteredUsers"
                      :key="user.id"
                      type="button"
                      role="option"
                      :data-testid="`assignment-smart-user-${user.id}`"
                      :aria-selected="smartAssigneeIds.includes(user.id)"
                      :class="{ 'is-active': smartAssigneeIds.includes(user.id) }"
                      :disabled="saving || splitLoading"
                      @click="toggleSmartAssignee(user.id)"
                    >
                      <span><strong>{{ getUserName(user.id) }}</strong><small>{{ getUserSecondaryLabel(user) }}</small></span>
                      <Check v-if="smartAssigneeIds.includes(user.id)" :size="14" />
                    </button>
                  </div>
                  <button
                    class="assignment-generate-button"
                    type="button"
                    data-testid="assignment-split-generate-button"
                    :disabled="saving || splitLoading || !previewSplit || (splitMode === 'by_part_count' && smartAssigneeIds.length === 0)"
                    @click="generateSplitPreview"
                  >
                    <Loader2 v-if="splitLoading" class="lucide-spin" :size="15" />
                    <template v-if="splitLoading">正在计算安全边界...</template>
                    <template v-else-if="splitMode === 'by_part_count'">为 {{ smartAssigneeIds.length }} 位译者生成方案</template>
                    <template v-else>生成字数拆分方案</template>
                  </button>
                </div>

                <div class="assignment-split-preview">
                  <div class="assignment-step-heading">
                    <em>3</em>
                    <span><strong>检查并应用方案</strong><small>生成后可调整每份对应的译者</small></span>
                  </div>
                  <div v-if="!splitPreview && !splitLoading" class="assignment-preview-empty">
                    <Users :size="28" />
                    <strong>方案将在这里预览</strong>
                    <span>先选择左侧的拆分规则和参与译者。</span>
                  </div>
                  <div v-else-if="splitLoading" class="assignment-preview-empty">
                    <Loader2 class="lucide-spin" :size="28" />
                    <strong>正在平衡工作量</strong>
                    <span>同时检查完整句段和表格单元格边界。</span>
                  </div>
                  <template v-else-if="splitPreview">
                    <div class="assignment-split-summary">
                      <span><strong>{{ splitParts.length }}</strong> 份任务</span>
                      <span><strong>{{ formatWordCount(splitPreview.segment_words) }}</strong> 句段字数</span>
                      <span v-if="splitPreview.document_words !== null"><strong>{{ formatWordCount(splitPreview.document_words) }}</strong> 文档字数</span>
                    </div>
                    <p
                      v-if="splitPreview.document_words !== null && Math.abs(splitPreview.document_words - splitPreview.segment_words) > Math.max(10, splitPreview.segment_words * 0.05)"
                      class="assignment-split-note"
                    >
                      字数口径存在差异；工作量拆分以句段原文字数为准。
                    </p>
                    <ul v-if="splitPreview.warnings.length" class="assignment-split-warnings">
                      <li v-for="warning in splitPreview.warnings" :key="warning">{{ warning }}</li>
                    </ul>
                    <div class="assignment-split-heading">
                      <strong>任务明细</strong>
                      <button type="button" :disabled="saving || smartAssigneeIds.length < splitParts.length" @click="autoFillSplitAssignees">
                        按所选顺序重新分配
                      </button>
                    </div>
                    <div class="assignment-split-parts" data-testid="assignment-split-parts">
                      <div v-for="part in splitParts" :key="part.index">
                        <em>{{ part.index }}</em>
                        <span>
                          <strong>{{ part.range_start }}–{{ part.range_end }} 段</strong>
                          <small>{{ part.segment_count }} 个句段 · {{ formatWordCount(part.word_count) }} 字</small>
                        </span>
                        <span class="assignment-part-percent">{{ part.word_percent }}%</span>
                        <div class="assignment-part-assignee">
                          <select v-model="part.assignee_id" :disabled="saving" :aria-label="`第 ${part.index} 份译者`">
                            <option value="">选择译者</option>
                            <option v-for="user in orderedUsers" :key="user.id" :value="user.id">{{ getUserName(user.id) }}</option>
                          </select>
                          <small v-if="part.assignee_id">{{ getUserSecondaryLabelById(part.assignee_id) }}</small>
                        </div>
                      </div>
                    </div>
                    <div class="assignment-split-applybar">
                      <span>应用后写入草稿，点击底部“保存分配”才会正式生效。</span>
                      <button
                        class="assignment-primary-action"
                        data-testid="assignment-split-apply-button"
                        type="button"
                        :disabled="saving || splitLoading || splitParts.some((part) => !part.assignee_id)"
                        @click="requestApplySplitParts"
                      ><Check :size="14" />应用方案到草稿</button>
                    </div>
                  </template>
                </div>
              </section>

              <section v-else class="assignment-manual-editor">
                <div class="assignment-manual-hint">
                  <strong>逐条指定句段范围</strong>
                  <span>适合需要精确控制起止段的场景。范围仍须落在完整段落边界。</span>
                </div>
                <div class="assignment-assignee-filters">
                  <label class="assignment-search" data-testid="assignment-user-search">
                    <Search :size="15" />
                    <input v-model="assigneeSearch" type="search" placeholder="搜索外部译者" :disabled="saving" />
                    <button v-if="assigneeSearch" type="button" aria-label="清空译者搜索" @click="assigneeSearch = ''"><X :size="13" /></button>
                  </label>
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
                    @click="selectedAssigneeId = user.id"
                  >
                    <span><strong>{{ getUserName(user.id) }}</strong><small>{{ getUserSecondaryLabel(user) }}</small></span>
                    <em v-if="isProjectMember(user.id)">成员 {{ getUserAllocationCount(user.id) }}</em>
                  </button>
                </div>
                <div class="assignment-manual-range-card">
                  <strong>{{ selectedAssigneeId ? `为 ${getUserName(selectedAssigneeId)} 指定范围` : '请先选择译者' }}</strong>
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
                  ><Check :size="14" />添加或更新范围</button>
                </div>
              </section>
            </template>

            <template v-else>
              <div class="assignment-panel-title">
                <div><strong>{{ transferFileId ? '转交文件' : '选择译者' }}</strong><span v-if="selectedFiles.length">已选 {{ selectedFiles.length }} 个文件</span></div>
              </div>
              <div class="assignment-assignee-filters">
                <label class="assignment-search" data-testid="assignment-user-search">
                  <Search :size="15" />
                  <input v-model="assigneeSearch" type="search" placeholder="搜索外部译者" :disabled="saving" />
                  <button v-if="assigneeSearch" type="button" aria-label="清空译者搜索" @click="assigneeSearch = ''"><X :size="13" /></button>
                </label>
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
                  <span><strong>{{ getUserName(user.id) }}</strong><small>{{ getUserSecondaryLabel(user) }}</small></span>
                  <em v-if="isProjectMember(user.id)">成员 {{ getUserAllocationCount(user.id) }}</em>
                </button>
              </div>
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

      <div v-if="showWorkflowTransitionConfirm && workflowTransitionPrompt" class="assignment-confirm-backdrop" role="presentation">
        <section class="assignment-confirm assignment-confirm--workflow" role="alertdialog" aria-modal="true" aria-label="确认流程推进">
          <header>
            <strong>所选任务尚未进入目标阶段</strong>
            <span>
              {{ workflowTransitionPrompt.file_count }} 个文件共 {{ workflowTransitionPrompt.matched_count }} 个句段仍在前一阶段。
              是否在完成指派的同时推进流程？
            </span>
          </header>
          <div class="assignment-transition-list">
            <div v-for="item in workflowTransitionPrompt.transitions" :key="`${item.file_record_id}-${item.target_step.id}`">
              <span :title="item.filename">{{ item.filename }}</span>
              <strong>{{ item.from_step.name }} → {{ item.target_step.name }}</strong>
              <em>{{ item.matched_count }} 段</em>
            </div>
          </div>
          <p class="assignment-transition-hint">
            选择“仅指派”后，译者可以查看前序内容，但在句段正式流转前不能编辑。
          </p>
          <footer>
            <button type="button" :disabled="saving" @click="showWorkflowTransitionConfirm = false">返回修改</button>
            <button type="button" :disabled="saving" @click="submitWorkflowTransitionChoice('assign_only')">仅指派，暂不流转</button>
            <button class="is-primary" type="button" :disabled="saving" @click="submitWorkflowTransitionChoice('advance')">
              {{ saving ? '处理中...' : '指派并进入目标阶段' }}
            </button>
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

      <div v-if="showSplitOverwriteConfirm" class="assignment-confirm-backdrop" role="presentation">
        <section class="assignment-confirm assignment-confirm--small" role="alertdialog" aria-modal="true" aria-label="确认覆盖现有拆分">
          <header>
            <strong>覆盖该文件的现有分配？</strong>
            <span>“{{ advancedFile?.filename }}”在当前步骤已有 {{ advancedAllocations.length }} 条分配，应用新方案后将被替换。</span>
          </header>
          <footer>
            <button type="button" @click="showSplitOverwriteConfirm = false">取消</button>
            <button class="is-primary" type="button" @click="applySplitPartsNow">确认覆盖</button>
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
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  gap: 12px;
  height: min(650px, calc(100vh - 257px));
  min-height: min(620px, calc(100vh - 257px));
}

.assignment-workbench.is-advanced {
  grid-template-rows: minmax(0, 1fr) auto;
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

.assignment-step {
  font-weight: 500;
}

.assignment-step:hover:not(:disabled) {
  border-color: currentColor;
}

.assignment-step.is-active {
  border-color: var(--brand-500);
  background: color-mix(in srgb, var(--brand-100) 82%, var(--surface-panel));
  color: var(--brand-700);
  font-weight: 700;
}

/* 工作流步骤：翻译（品牌绿） */
.assignment-step.is-step-translation {
  border-color: color-mix(in srgb, var(--brand-500) 42%, var(--line-soft));
  color: var(--brand-700);
}

.assignment-step.is-step-translation.is-active {
  border-color: var(--brand-500);
  background: color-mix(in srgb, var(--brand-100) 82%, var(--surface-panel));
  color: var(--brand-700);
}

/* 工作流步骤：审校（蓝） */
.assignment-step.is-step-review {
  border-color: color-mix(in srgb, var(--state-info) 42%, var(--line-soft));
  color: var(--state-info);
}

.assignment-step.is-step-review.is-active {
  border-color: var(--state-info);
  background: var(--state-info-bg);
  color: var(--state-info);
}

/* 工作流步骤：校对（琥珀） */
.assignment-step.is-step-proofread {
  border-color: color-mix(in srgb, var(--state-warning) 52%, var(--line-soft));
  color: var(--state-warning);
}

.assignment-step.is-step-proofread.is-active {
  border-color: var(--state-warning);
  background: var(--state-warning-bg);
  color: var(--state-warning);
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

.assignment-overview button {
  font-weight: 600;
  transition: background var(--motion-fast) var(--ease-standard),
    border-color var(--motion-fast) var(--ease-standard),
    color var(--motion-fast) var(--ease-standard);
}

/* 分配状态：待分配（绿）/ 已分配（蓝）/ 冲突（红） */
.assignment-overview button.is-unassigned {
  border-color: color-mix(in srgb, var(--state-success) 42%, var(--line-soft));
  background: var(--state-success-bg);
  color: var(--state-success);
}

.assignment-overview button.is-assigned {
  border-color: color-mix(in srgb, var(--state-info) 42%, var(--line-soft));
  background: var(--state-info-bg);
  color: var(--state-info);
}

.assignment-overview button.is-danger {
  border-color: color-mix(in srgb, var(--state-danger) 42%, var(--line-soft));
  background: var(--state-danger-bg);
  color: var(--state-danger);
}

.assignment-overview button.is-unassigned.is-active {
  border-color: var(--state-success);
  background: var(--state-success);
  color: #fff;
}

.assignment-overview button.is-assigned.is-active {
  border-color: var(--state-info);
  background: var(--state-info);
  color: #fff;
}

.assignment-overview button.is-danger.is-active {
  border-color: var(--state-danger);
  background: var(--state-danger);
  color: #fff;
}

.assignment-footer-summary strong {
  color: var(--state-danger);
}

.assignment-layout {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 330px);
  gap: 12px;
}

.assignment-layout.is-advanced {
  grid-template-columns: minmax(0, 1fr);
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

.assignment-assignee-panel.is-advanced {
  display: grid;
  grid-template-rows: auto auto auto minmax(0, 1fr);
  gap: 12px;
  padding: 14px;
  overflow: hidden;
  border-color: color-mix(in srgb, var(--brand-500) 18%, var(--line-soft));
  border-radius: 14px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--brand-050) 35%, var(--surface-panel)), var(--surface-panel) 90px);
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
  display: block;
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
  grid-template-columns: minmax(0, 1fr);
  grid-auto-rows: minmax(48px, auto);
  align-content: start;
  gap: 3px;
}

.assignment-user-list > button {
  width: 100%;
  min-width: 0;
  min-height: 48px;
  height: auto;
  display: flex;
  align-items: flex-start;
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
  white-space: normal;
  overflow-wrap: anywhere;
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
  line-height: 1.3;
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

.assignment-auto-split {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(390px, 430px) minmax(0, 1fr);
  gap: 14px;
}

.assignment-advanced-header {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
}

.assignment-advanced-header > div {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.assignment-advanced-header strong {
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.assignment-advanced-header span {
  color: var(--text-muted);
  font-size: 12px;
}

.assignment-back-button {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 10px;
  border: 0;
  border-radius: 9px;
  background: color-mix(in srgb, var(--brand-100) 55%, var(--surface-panel));
  color: var(--brand-700);
  font-weight: 650;
}

.assignment-current-ranges {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: color-mix(in srgb, var(--state-info-bg) 45%, var(--surface-panel));
  font-size: 11px;
}

.assignment-current-ranges > span,
.assignment-current-ranges > small {
  flex: 0 0 auto;
  color: var(--text-muted);
}

.assignment-current-ranges > div {
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.assignment-current-ranges > div > span,
.assignment-selected-users button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  border-radius: 999px;
  background: var(--state-info-bg);
  color: var(--state-info);
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.assignment-current-ranges button {
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: currentColor;
}

.assignment-editor-tabs,
.assignment-mode-switch {
  display: flex;
  gap: 4px;
  padding: 4px;
  border-radius: 11px;
  background: var(--surface-muted);
}

.assignment-editor-tabs {
  width: fit-content;
}

.assignment-editor-tabs button,
.assignment-mode-switch button {
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-muted);
  box-shadow: none;
}

.assignment-editor-tabs button.is-active,
.assignment-mode-switch button.is-active {
  background: color-mix(in srgb, var(--brand-100) 75%, var(--surface-panel));
  color: var(--brand-700);
  box-shadow: none;
  font-weight: 700;
}

.assignment-split-setup,
.assignment-split-preview {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 14px;
  border: 1px solid color-mix(in srgb, var(--brand-500) 14%, var(--line-soft));
  border-radius: 14px;
  background: var(--surface-panel);
  box-shadow: 0 10px 30px rgba(15, 31, 28, 0.055);
}

.assignment-split-setup {
  overflow: hidden;
}

.assignment-split-preview {
  overflow: auto;
}

.assignment-step-heading {
  display: flex;
  align-items: center;
  gap: 8px;
}

.assignment-step-heading > em {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--brand-100);
  color: var(--brand-700);
  font-size: 12px;
  font-style: normal;
  font-weight: 700;
}

.assignment-step-heading > span {
  min-width: 0;
  display: grid;
  gap: 1px;
}

.assignment-step-heading strong,
.assignment-split-heading strong {
  color: var(--text-primary);
  font-size: 12px;
}

.assignment-step-heading small,
.assignment-split-parts small {
  color: var(--text-muted);
  font-size: 11px;
}

.assignment-mode-switch button {
  flex: 1;
  min-height: 36px;
  padding: 7px 9px;
  font-size: 11px;
}

.assignment-word-target {
  display: grid;
  grid-template-columns: auto minmax(100px, 1fr);
  align-items: center;
  gap: 5px 8px;
  color: var(--text-secondary);
  font-size: 11px;
}

.assignment-word-target small {
  grid-column: 1 / -1;
  color: var(--text-muted);
}

.assignment-word-target input,
.assignment-split-parts select {
  min-width: 0;
  width: 100%;
  padding: 6px 7px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--surface-panel);
  color: var(--text-primary);
}

.assignment-selected-users {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.assignment-selected-users button {
  border: 0;
  font-size: 11px;
  white-space: normal;
}

.assignment-smart-user-list {
  min-height: 0;
  flex: 1;
  overflow: auto;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-auto-rows: minmax(52px, auto);
  align-content: start;
  gap: 4px;
}

.assignment-smart-user-list > button {
  min-width: 0;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 6px;
  padding: 7px 9px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: color-mix(in srgb, var(--surface-muted) 72%, var(--surface-panel));
  color: var(--text-secondary);
  text-align: left;
}

.assignment-smart-user-list > button.is-active {
  border-color: var(--brand-500);
  background: color-mix(in srgb, var(--brand-100) 64%, var(--surface-panel));
  color: var(--brand-700);
}

.assignment-smart-user-list span {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.assignment-smart-user-list strong,
.assignment-smart-user-list small {
  white-space: normal;
  overflow-wrap: anywhere;
}

.assignment-smart-user-list strong {
  font-size: 12px;
}

.assignment-smart-user-list small {
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1.3;
}

.assignment-generate-button {
  min-height: 38px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border: 1px solid var(--brand-700);
  border-radius: 10px;
  background: linear-gradient(135deg, var(--brand-650), var(--brand-700));
  color: white;
  box-shadow: 0 8px 18px rgba(13, 122, 104, 0.18);
  font-weight: 700;
}

.assignment-generate-button:not(:disabled):hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 22px rgba(13, 122, 104, 0.24);
}

.assignment-split-note {
  margin: 0;
  font-size: 11px;
  line-height: 1.45;
}

.assignment-split-note,
.assignment-split-warnings {
  color: var(--state-warning);
}

.assignment-split-warnings {
  margin: 0;
  padding-left: 18px;
  font-size: 11px;
  line-height: 1.45;
}

.assignment-preview-empty {
  min-height: 220px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 6px;
  color: var(--text-muted);
  text-align: center;
  border: 1px dashed color-mix(in srgb, var(--brand-500) 25%, var(--line-soft));
  border-radius: 12px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--brand-050) 45%, transparent), transparent);
}

.assignment-preview-empty strong {
  color: var(--text-secondary);
  font-size: 13px;
}

.assignment-preview-empty span {
  font-size: 11px;
}

.assignment-split-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
}

.assignment-split-summary > span {
  display: grid;
  gap: 2px;
  padding: 8px 9px;
  border-radius: 7px;
  background: var(--surface-muted);
  color: var(--text-muted);
  font-size: 10px;
}

.assignment-split-summary strong {
  color: var(--text-primary);
  font-size: 15px;
}

.assignment-split-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.assignment-split-heading button {
  min-height: 30px;
  padding: 5px 9px;
  border: 0;
  border-radius: 8px;
  background: color-mix(in srgb, var(--brand-100) 72%, var(--surface-panel));
  color: var(--brand-700);
  font-size: 10px;
  font-weight: 700;
}

.assignment-split-parts {
  min-height: 0;
  display: grid;
  gap: 5px;
}

.assignment-split-parts > div {
  display: grid;
  grid-template-columns: 28px minmax(120px, 1fr) 54px minmax(170px, 220px);
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: color-mix(in srgb, var(--surface-muted) 58%, var(--surface-panel));
}

.assignment-split-parts > div > em {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: var(--brand-100);
  color: var(--brand-700);
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
}

.assignment-split-parts span {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.assignment-split-parts strong {
  color: var(--text-secondary);
  font-size: 11px;
  white-space: normal;
  overflow-wrap: anywhere;
}

.assignment-part-assignee {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.assignment-part-assignee small {
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.25;
}

.assignment-part-percent {
  color: var(--text-muted);
  font-size: 11px;
  text-align: right;
}

.assignment-split-applybar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: auto;
  padding-top: 8px;
  border-top: 1px solid var(--line-soft);
}

.assignment-split-applybar > span {
  color: var(--text-muted);
  font-size: 11px;
}

.assignment-split-applybar .assignment-primary-action {
  flex: 0 0 auto;
}

.assignment-manual-editor {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  grid-template-rows: auto auto minmax(0, 1fr);
  gap: 9px 12px;
}

.assignment-manual-hint {
  grid-column: 1 / -1;
  display: grid;
  gap: 3px;
  padding: 9px 10px;
  border-radius: 7px;
  background: var(--surface-muted);
}

.assignment-manual-hint strong,
.assignment-manual-range-card > strong {
  color: var(--text-primary);
  font-size: 12px;
}

.assignment-manual-hint span {
  color: var(--text-muted);
  font-size: 11px;
}

.assignment-manual-editor > .assignment-assignee-filters,
.assignment-manual-editor > .assignment-user-list {
  grid-column: 1;
}

.assignment-manual-range-card {
  grid-column: 2;
  grid-row: 2 / 4;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-muted);
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

.assignment-confirm--workflow {
  width: min(680px, 100%);
}

.assignment-transition-list {
  display: grid;
  gap: 6px;
  max-height: 260px;
  overflow: auto;
}

.assignment-transition-list > div {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 12px;
  padding: 9px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 7px;
  background: var(--surface-muted);
  font-size: 12px;
}

.assignment-transition-list span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assignment-transition-list strong {
  color: var(--text-secondary);
}

.assignment-transition-list em {
  color: var(--brand-700);
  font-style: normal;
  font-weight: 700;
  white-space: nowrap;
}

.assignment-transition-hint {
  margin: 0;
  padding: 9px 10px;
  border: 1px solid #ead8a4;
  border-radius: 7px;
  background: #fff8e6;
  color: #75520b;
  font-size: 12px;
  line-height: 1.5;
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

  .assignment-auto-split,
  .assignment-manual-editor {
    grid-template-columns: 1fr;
    overflow: auto;
  }

  .assignment-manual-editor > .assignment-assignee-filters,
  .assignment-manual-editor > .assignment-user-list,
  .assignment-manual-range-card {
    grid-column: 1;
    grid-row: auto;
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
