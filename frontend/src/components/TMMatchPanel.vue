<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { http } from '../api/http'
import type { TermMatch } from '../types/api'

interface TMCandidate {
  source_text: string
  target_text: string
  score: number
  diff_html: string
}

const props = defineProps<{
  fileRecordId: string
  activeSentenceId: string | null
  activeSourceText: string
  threshold?: number
}>()

const emit = defineEmits<{
  close: []
  applyTarget: [sentenceId: string, targetText: string]
}>()

const loading = ref(false)
const error = ref('')
const candidates = ref<TMCandidate[]>([])
const termMatches = ref<TermMatch[]>([])
const currentSourceText = ref('')
const CANDIDATE_DEBOUNCE_MS = 250
const CANDIDATE_CACHE_TTL_MS = 30_000
type CandidateCacheEntry = {
  candidates: TMCandidate[]
  sourceText: string
  expiresAt: number
}
const candidateCache = new Map<string, CandidateCacheEntry>()
let candidateRequestId = 0
let candidateLoadTimer: number | null = null

async function loadCandidates() {
  if (!props.activeSentenceId || !props.fileRecordId) {
    candidates.value = []
    termMatches.value = []
    currentSourceText.value = ''
    return
  }

  loading.value = true
  error.value = ''

  // 分别处理TM和术语匹配，避免一个失败导致另一个也无法显示
  try {
    const tmResponse = await http.get<{
      sentence_id: string
      source_text: string
      candidates: TMCandidate[]
    }>(`/file-records/${props.fileRecordId}/segments/${props.activeSentenceId}/tm-candidates`, {
      params: {
        ...(props.threshold == null ? {} : { threshold: props.threshold }),
        max_candidates: 5,
      },
    })
    candidates.value = tmResponse.data.candidates
    currentSourceText.value = tmResponse.data.source_text
  } catch (err) {
    error.value = '加载匹配记忆失败'
    candidates.value = []
  }

  // 术语匹配独立处理，失败时静默
  try {
    if (props.activeSourceText) {
      const termResponse = await http.get<{ matches: TermMatch[] }>('/termbase/match', {
        params: { text: props.activeSourceText },
      })
      termMatches.value = termResponse.data.matches
    } else {
      termMatches.value = []
    }
  } catch {
    termMatches.value = []
  }

  loading.value = false
}

function buildCandidateCacheKey() {
  return [
    props.fileRecordId,
    props.activeSentenceId || '',
    props.threshold == null ? '' : String(props.threshold),
  ].join(':')
}

function clearCandidateLoadTimer() {
  if (candidateLoadTimer !== null) {
    window.clearTimeout(candidateLoadTimer)
    candidateLoadTimer = null
  }
}

function scheduleLoadCandidates() {
  const requestId = ++candidateRequestId
  clearCandidateLoadTimer()
  candidateLoadTimer = window.setTimeout(() => {
    candidateLoadTimer = null
    void loadCandidatesWithRequest(requestId)
  }, CANDIDATE_DEBOUNCE_MS)
}

async function loadCandidatesWithRequest(requestId: number) {
  if (!props.activeSentenceId || !props.fileRecordId) {
    candidates.value = []
    termMatches.value = []
    currentSourceText.value = ''
    loading.value = false
    error.value = ''
    return
  }

  loading.value = true
  error.value = ''

  try {
    const cacheKey = buildCandidateCacheKey()
    const cached = candidateCache.get(cacheKey)
    if (cached && cached.expiresAt > Date.now()) {
      if (requestId !== candidateRequestId) return
      candidates.value = cached.candidates
      currentSourceText.value = cached.sourceText
    } else {
      const tmResponse = await http.get<{
        sentence_id: string
        source_text: string
        candidates: TMCandidate[]
      }>(`/file-records/${props.fileRecordId}/segments/${props.activeSentenceId}/tm-candidates`, {
        params: {
          ...(props.threshold == null ? {} : { threshold: props.threshold }),
          max_candidates: 5,
        },
      })
      if (requestId !== candidateRequestId) return
      candidates.value = tmResponse.data.candidates
      currentSourceText.value = tmResponse.data.source_text
      candidateCache.set(cacheKey, {
        candidates: tmResponse.data.candidates,
        sourceText: tmResponse.data.source_text,
        expiresAt: Date.now() + CANDIDATE_CACHE_TTL_MS,
      })
    }
  } catch {
    if (requestId !== candidateRequestId) return
    error.value = 'Failed to load TM candidates'
    candidates.value = []
  }

  try {
    if (props.activeSourceText) {
      const termResponse = await http.get<{ matches: TermMatch[] }>('/termbase/match', {
        params: { text: props.activeSourceText },
      })
      if (requestId !== candidateRequestId) return
      termMatches.value = termResponse.data.matches
    } else {
      termMatches.value = []
    }
  } catch {
    if (requestId !== candidateRequestId) return
    termMatches.value = []
  } finally {
    if (requestId === candidateRequestId) {
      loading.value = false
    }
  }
}

function handleApply(candidate: TMCandidate) {
  if (props.activeSentenceId) {
    emit('applyTarget', props.activeSentenceId, candidate.target_text)
  }
}

function handleApplyTerm(term: TermMatch) {
  if (props.activeSentenceId) {
    emit('applyTarget', props.activeSentenceId, term.target_text)
  }
}

watch(() => [
  props.fileRecordId,
  props.activeSentenceId,
  props.activeSourceText,
  props.threshold ?? '',
], () => {
  scheduleLoadCandidates()
}, { immediate: true })

onBeforeUnmount(() => {
  candidateRequestId += 1
  clearCandidateLoadTimer()
})
</script>

<template>
  <section class="panel tm-match-panel">
    <div class="panel-header panel-header--compact">
      <div>
        <div class="section-title section-title--tight">记忆/术语匹配</div>
        <p class="panel-subtitle">选中句段后显示匹配的 TM 记录和术语</p>
      </div>
      <button class="button preview-panel__close" type="button" @click="emit('close')">关闭</button>
    </div>

    <div v-if="!activeSentenceId" class="tm-match-panel__empty">
      <p>请在左侧选中一个句段</p>
    </div>

    <div v-else-if="loading" class="tm-match-panel__empty">
      <p>正在加载匹配记忆...</p>
    </div>

    <div v-else-if="error" class="tm-match-panel__empty tm-match-panel__error">
      <p>{{ error }}</p>
    </div>

    <div v-else-if="candidates.length === 0 && termMatches.length === 0" class="tm-match-panel__empty">
      <p>未找到满足阈值的匹配记忆或术语</p>
    </div>

    <div v-else class="tm-match-panel__content">
      <table class="tm-match-table">
        <tbody>
          <tr v-for="term in termMatches" :key="'term-' + term.term_id" class="tm-match-row tm-match-row--term">
            <td class="tm-match-table__cell--source">
              <span class="tm-match-term-source">{{ term.source_text }}</span>
            </td>
            <td class="tm-match-table__cell--score tm-match-table__cell--tb">
              <span class="tm-match-score tm-match-score--tb">TB</span>
            </td>
            <td class="tm-match-table__cell--target">{{ term.target_text }}</td>
            <td class="tm-match-table__cell--action">
              <button class="button tm-match-apply" type="button" @click="handleApplyTerm(term)">
                应用
              </button>
            </td>
          </tr>
          <tr v-for="(candidate, index) in candidates" :key="'tm-' + index" class="tm-match-row">
            <td class="tm-match-table__cell--source">
              <div class="tm-match-diff" v-html="candidate.diff_html" />
            </td>
            <td class="tm-match-table__cell--score">
              <span class="tm-match-score" :class="{ 'is-exact': candidate.score >= 1 }">
                {{ (candidate.score * 100).toFixed(0) }}%
              </span>
            </td>
            <td class="tm-match-table__cell--target">{{ candidate.target_text }}</td>
            <td class="tm-match-table__cell--action">
              <button class="button tm-match-apply" type="button" @click="handleApply(candidate)">
                应用
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
