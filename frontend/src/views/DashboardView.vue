<script setup lang="ts">
import axios from 'axios'
import {
  Activity,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  BarChart3,
  Briefcase,
  FileText,
  Languages,
  RefreshCw,
  Sparkles,
  TrendingUp,
  Users,
} from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import { getLLMModelShortLabel } from '../constants/llm'
import { formatLanguagePair } from '../constants/languages'
import type {
  AnalyticsDashboardResponse,
  AnalyticsGranularity,
  AnalyticsLlmModelSeries,
  AnalyticsSeriesPoint,
  AnalyticsUserStat,
} from '../types/api'

type SeriesKey = keyof Pick<
  AnalyticsSeriesPoint,
  | 'project_created_count'
  | 'translated_source_word_count'
  | 'llm_processed_source_word_count'
  | 'active_user_count'
>

interface LineSpec {
  key: SeriesKey
  label: string
  color: string
}

interface LlmModelLineSpec extends AnalyticsLlmModelSeries {
  label: string
  color: string
}

const { t } = useI18n()

const granularity = ref<AnalyticsGranularity>('day')
const dashboard = ref<AnalyticsDashboardResponse | null>(null)
const loading = ref(false)
const pageError = ref('')
const userStatsSortKey = ref<'last_seen_at' | ''>('')
const userStatsSortOrder = ref<'asc' | 'desc'>('desc')

const chartWidth = 720
const chartHeight = 240
const chartPadding = {
  top: 18,
  right: 24,
  bottom: 36,
  left: 46,
}
const llmModelChartHeight = 210
const llmModelColors = ['#7c3aed', '#2563eb', '#059669', '#d97706', '#db2777', '#64748b']

const summary = computed(() => dashboard.value?.summary)
const series = computed(() => dashboard.value?.series ?? [])
const llmModelLines = computed<LlmModelLineSpec[]>(() => (
  (dashboard.value?.llm_model_series ?? []).map((item, index) => ({
    ...item,
    label: getLlmModelLabel(item.model),
    color: llmModelColors[index % llmModelColors.length],
  }))
))
const topLanguagePairs = computed(() => (dashboard.value?.language_pairs ?? []).slice(0, 8))
const userStats = computed(() => {
  const stats = [...(dashboard.value?.user_stats ?? [])]
  if (userStatsSortKey.value === 'last_seen_at') {
    return stats.sort(compareUserStatsByLastSeen)
  }
  return stats.sort(compareUserStats)
})
const hasSeriesData = computed(() => series.value.some((item) => (
  item.project_created_count
  || item.translated_source_word_count
  || item.llm_processed_source_word_count
  || item.active_user_count
)))
const hasLlmModelSeriesData = computed(() => llmModelLines.value.some((line) => (
  line.total_segment_count > 0
)))

const translationLines = computed<LineSpec[]>(() => [
  {
    key: 'translated_source_word_count',
    label: t('dashboard.charts.translatedWords'),
    color: '#2563eb',
  },
  {
    key: 'llm_processed_source_word_count',
    label: t('dashboard.charts.llmWords'),
    color: '#7c3aed',
  },
])

const activityLines = computed<LineSpec[]>(() => [
  {
    key: 'active_user_count',
    label: t('dashboard.charts.activeUsers'),
    color: '#059669',
  },
  {
    key: 'project_created_count',
    label: t('dashboard.charts.createdProjects'),
    color: '#d97706',
  },
])

const kpiItems = computed(() => {
  const data = summary.value
  return [
    {
      key: 'projects',
      label: t('dashboard.kpis.projects'),
      value: formatNumber(data?.total_projects),
      icon: Briefcase,
      tone: 'blue',
    },
    {
      key: 'files',
      label: t('dashboard.kpis.files'),
      value: formatNumber(data?.total_files),
      icon: FileText,
      tone: 'teal',
    },
    {
      key: 'translated',
      label: t('dashboard.kpis.translatedWords'),
      value: formatNumber(data?.translated_source_word_count),
      icon: TrendingUp,
      tone: 'green',
    },
    {
      key: 'llm',
      label: t('dashboard.kpis.llmWords'),
      value: formatNumber(data?.llm_processed_source_word_count),
      icon: Sparkles,
      tone: 'violet',
    },
    {
      key: 'activeUsers',
      label: t('dashboard.kpis.activeUsers'),
      value: formatNumber(data?.active_users_today),
      icon: Users,
      tone: 'orange',
    },
    {
      key: 'progress',
      label: t('dashboard.kpis.progress'),
      value: `${formatNumber(data?.translation_progress, 1)}%`,
      icon: Activity,
      tone: 'slate',
    },
  ]
})

async function loadDashboard() {
  loading.value = true
  pageError.value = ''
  try {
    const { data } = await http.get<AnalyticsDashboardResponse>('/analytics/dashboard', {
      params: { granularity: granularity.value },
    })
    dashboard.value = data
  } catch (error) {
    if (axios.isAxiosError(error)) {
      pageError.value = String(error.response?.data?.detail || t('dashboard.errors.load'))
      return
    }
    pageError.value = error instanceof Error ? error.message : t('dashboard.errors.load')
  } finally {
    loading.value = false
  }
}

function setGranularity(nextGranularity: AnalyticsGranularity) {
  granularity.value = nextGranularity
}

function formatNumber(value: number | null | undefined, maximumFractionDigits = 0) {
  return Number(value || 0).toLocaleString('zh-CN', {
    maximumFractionDigits,
  })
}

function formatDuration(minutes: number | null | undefined) {
  const totalMinutes = Math.max(0, Math.round(Number(minutes || 0)))
  const hours = Math.floor(totalMinutes / 60)
  const remainingMinutes = totalMinutes % 60
  if (hours > 0 && remainingMinutes > 0) {
    return t('dashboard.userStats.durationHoursMinutes', { hours, minutes: remainingMinutes })
  }
  if (hours > 0) {
    return t('dashboard.userStats.durationHours', { hours })
  }
  return t('dashboard.userStats.durationMinutes', { minutes: totalMinutes })
}

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return '--'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '--'
  }
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function compareUserStats(left: AnalyticsUserStat, right: AnalyticsUserStat) {
  return (
    Number(right.total_source_word_count || 0) - Number(left.total_source_word_count || 0)
    || Number(right.estimated_active_minutes || 0) - Number(left.estimated_active_minutes || 0)
    || Number(right.request_count || 0) - Number(left.request_count || 0)
    || left.display_name.localeCompare(right.display_name, 'zh-CN')
  )
}

function compareUserStatsByLastSeen(left: AnalyticsUserStat, right: AnalyticsUserStat) {
  const leftTime = parseUserLastSeenTime(left)
  const rightTime = parseUserLastSeenTime(right)
  if (leftTime === null && rightTime === null) {
    return compareUserStats(left, right)
  }
  if (leftTime === null) {
    return 1
  }
  if (rightTime === null) {
    return -1
  }
  const result = userStatsSortOrder.value === 'asc'
    ? leftTime - rightTime
    : rightTime - leftTime
  return result || compareUserStats(left, right)
}

function parseUserLastSeenTime(user: AnalyticsUserStat) {
  if (!user.last_seen_at) {
    return null
  }
  const timestamp = new Date(user.last_seen_at).getTime()
  return Number.isNaN(timestamp) ? null : timestamp
}

function toggleUserStatsLastSeenSort() {
  if (userStatsSortKey.value !== 'last_seen_at') {
    userStatsSortKey.value = 'last_seen_at'
    userStatsSortOrder.value = 'desc'
    return
  }
  userStatsSortOrder.value = userStatsSortOrder.value === 'desc' ? 'asc' : 'desc'
}

function getUserStatsLastSeenSortIcon() {
  if (userStatsSortKey.value !== 'last_seen_at') {
    return ArrowUpDown
  }
  return userStatsSortOrder.value === 'desc' ? ArrowDown : ArrowUp
}

function getUserStatsLastSeenAriaSort() {
  if (userStatsSortKey.value !== 'last_seen_at') {
    return 'none'
  }
  return userStatsSortOrder.value === 'desc' ? 'descending' : 'ascending'
}

function getUserRoleLabel(user: AnalyticsUserStat) {
  if (!user.user_id) {
    return t('dashboard.userStats.historicalRole')
  }
  if (user.role === 'super_admin') {
    return t('common.roles.superAdmin')
  }
  if (user.role === 'admin') {
    return t('common.roles.admin')
  }
  return t('common.roles.user')
}

function getTranslatorTypeLabel(user: AnalyticsUserStat) {
  if (!user.user_id || user.role !== 'user') {
    return ''
  }
  return user.translator_type === 'internal'
    ? t('dashboard.userStats.internalTranslator')
    : t('dashboard.userStats.externalTranslator')
}

function getUserStatusLabel(user: AnalyticsUserStat) {
  if (!user.user_id) {
    return t('dashboard.userStats.historicalStatus')
  }
  return user.is_active ? t('dashboard.userStats.activeStatus') : t('dashboard.userStats.disabledStatus')
}

function getChartMax(lines: LineSpec[]) {
  const values = series.value.flatMap((item) => lines.map((line) => Number(item[line.key] || 0)))
  return Math.max(1, ...values)
}

function buildLinePath(lines: LineSpec[], key: SeriesKey) {
  const points = buildPoints(lines, key)
  if (!points.length) {
    return ''
  }
  return points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ')
}

function buildPoints(lines: LineSpec[], key: SeriesKey) {
  const items = series.value
  if (!items.length) {
    return []
  }
  const maxValue = getChartMax(lines)
  const innerWidth = chartWidth - chartPadding.left - chartPadding.right
  const innerHeight = chartHeight - chartPadding.top - chartPadding.bottom
  return items.map((item, index) => {
    const x = chartPadding.left + (items.length === 1 ? innerWidth / 2 : (innerWidth * index) / (items.length - 1))
    const y = chartPadding.top + innerHeight - (Number(item[key] || 0) / maxValue) * innerHeight
    return { x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 }
  })
}

function getAxisLabels() {
  const items = series.value
  if (!items.length) {
    return []
  }
  const indexes = Array.from(new Set([0, Math.floor((items.length - 1) / 2), items.length - 1]))
  return indexes.map((index) => ({
    bucket: items[index].bucket,
    x: chartPadding.left + (
      items.length === 1
        ? (chartWidth - chartPadding.left - chartPadding.right) / 2
        : ((chartWidth - chartPadding.left - chartPadding.right) * index) / (items.length - 1)
    ),
  }))
}

function getLlmModelLabel(model: string) {
  if (model === '__other__') {
    return t('dashboard.charts.otherModels')
  }
  if (model === '__unknown__') {
    return t('dashboard.charts.unknownModel')
  }
  return getLLMModelShortLabel(model)
}

function getLlmModelChartMax() {
  const values = llmModelLines.value.flatMap((line) => line.points.map((point) => Number(point.segment_count || 0)))
  return Math.max(1, ...values)
}

function buildLlmModelPoints(line: LlmModelLineSpec) {
  const items = line.points
  if (!items.length) {
    return []
  }
  const maxValue = getLlmModelChartMax()
  const innerWidth = chartWidth - chartPadding.left - chartPadding.right
  const innerHeight = llmModelChartHeight - chartPadding.top - chartPadding.bottom
  return items.map((item, index) => ({
    ...item,
    x: Math.round((chartPadding.left + (
      items.length === 1 ? innerWidth / 2 : (innerWidth * index) / (items.length - 1)
    )) * 100) / 100,
    y: Math.round((chartPadding.top + innerHeight - (Number(item.segment_count || 0) / maxValue) * innerHeight) * 100) / 100,
  }))
}

function buildLlmModelLinePath(line: LlmModelLineSpec) {
  return buildLlmModelPoints(line)
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`)
    .join(' ')
}

function getLlmModelAxisLabels() {
  const items = llmModelLines.value[0]?.points ?? []
  if (!items.length) {
    return []
  }
  const indexes = Array.from(new Set([0, Math.floor((items.length - 1) / 2), items.length - 1]))
  return indexes.map((index) => ({
    bucket: items[index].bucket,
    x: chartPadding.left + (
      items.length === 1
        ? (chartWidth - chartPadding.left - chartPadding.right) / 2
        : ((chartWidth - chartPadding.left - chartPadding.right) * index) / (items.length - 1)
    ),
  }))
}

function getPairLabel(sourceLanguage: string | null, targetLanguage: string | null) {
  return formatLanguagePair(sourceLanguage, targetLanguage)
}

watch(granularity, () => {
  void loadDashboard()
})

onMounted(() => {
  void loadDashboard()
})
</script>

<template>
  <main class="dashboard-page" data-testid="dashboard-page">
    <section class="dashboard-toolbar">
      <div class="dashboard-toolbar__title">
        <span class="dashboard-toolbar__icon">
          <BarChart3 :size="20" />
        </span>
        <div>
          <h2>{{ t('dashboard.title') }}</h2>
          <p>{{ t('dashboard.subtitle') }}</p>
        </div>
      </div>
      <div class="dashboard-toolbar__actions">
        <div class="segmented-control" role="tablist" :aria-label="t('dashboard.range.label')">
          <button
            type="button"
            :class="{ active: granularity === 'day' }"
            @click="setGranularity('day')"
          >
            {{ t('dashboard.range.day') }}
          </button>
          <button
            type="button"
            :class="{ active: granularity === 'month' }"
            @click="setGranularity('month')"
          >
            {{ t('dashboard.range.month') }}
          </button>
        </div>
        <button class="icon-button" type="button" :title="t('common.actions.refresh')" @click="loadDashboard">
          <RefreshCw :size="18" :class="{ spinning: loading }" />
        </button>
      </div>
    </section>

    <div v-if="pageError" class="dashboard-state dashboard-state--error">
      {{ pageError }}
    </div>
    <div v-else-if="loading && !dashboard" class="dashboard-state">
      {{ t('table.loading') }}
    </div>
    <template v-else>
      <section class="dashboard-kpis" aria-live="polite">
        <article v-for="item in kpiItems" :key="item.key" class="dashboard-kpi" :class="`dashboard-kpi--${item.tone}`">
          <div class="dashboard-kpi__icon">
            <component :is="item.icon" :size="20" />
          </div>
          <div class="dashboard-kpi__body">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </article>
      </section>

      <section class="dashboard-grid">
        <article class="dashboard-panel dashboard-panel--wide">
          <div class="dashboard-panel__header">
            <div>
              <h3>{{ t('dashboard.charts.translationTitle') }}</h3>
              <p>{{ t('dashboard.charts.translationHint') }}</p>
            </div>
          </div>
          <div v-if="hasSeriesData" class="line-chart">
            <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" role="img" :aria-label="t('dashboard.charts.translationTitle')">
              <line
                :x1="chartPadding.left"
                :x2="chartWidth - chartPadding.right"
                :y1="chartHeight - chartPadding.bottom"
                :y2="chartHeight - chartPadding.bottom"
                class="line-chart__axis"
              />
              <line
                :x1="chartPadding.left"
                :x2="chartPadding.left"
                :y1="chartPadding.top"
                :y2="chartHeight - chartPadding.bottom"
                class="line-chart__axis"
              />
              <path
                v-for="line in translationLines"
                :key="line.key"
                class="line-chart__line"
                :d="buildLinePath(translationLines, line.key)"
                :stroke="line.color"
              />
              <g v-for="line in translationLines" :key="`${line.key}-points`">
                <circle
                  v-for="point in buildPoints(translationLines, line.key)"
                  :key="`${point.x}-${point.y}`"
                  :cx="point.x"
                  :cy="point.y"
                  r="3"
                  :fill="line.color"
                />
              </g>
              <text
                v-for="label in getAxisLabels()"
                :key="label.bucket"
                :x="label.x"
                :y="chartHeight - 10"
                class="line-chart__label"
                text-anchor="middle"
              >
                {{ label.bucket }}
              </text>
            </svg>
            <div class="line-chart__legend">
              <span v-for="line in translationLines" :key="line.key">
                <i :style="{ backgroundColor: line.color }"></i>{{ line.label }}
              </span>
            </div>
          </div>
          <div v-else class="dashboard-empty">{{ t('dashboard.empty.series') }}</div>
        </article>

        <article class="dashboard-panel">
          <div class="dashboard-panel__header">
            <div>
              <h3>{{ t('dashboard.charts.activityTitle') }}</h3>
              <p>{{ t('dashboard.charts.activityHint') }}</p>
            </div>
          </div>
          <div v-if="hasSeriesData" class="line-chart line-chart--compact">
            <svg :viewBox="`0 0 ${chartWidth} ${chartHeight}`" role="img" :aria-label="t('dashboard.charts.activityTitle')">
              <line
                :x1="chartPadding.left"
                :x2="chartWidth - chartPadding.right"
                :y1="chartHeight - chartPadding.bottom"
                :y2="chartHeight - chartPadding.bottom"
                class="line-chart__axis"
              />
              <line
                :x1="chartPadding.left"
                :x2="chartPadding.left"
                :y1="chartPadding.top"
                :y2="chartHeight - chartPadding.bottom"
                class="line-chart__axis"
              />
              <path
                v-for="line in activityLines"
                :key="line.key"
                class="line-chart__line"
                :d="buildLinePath(activityLines, line.key)"
                :stroke="line.color"
              />
              <g v-for="line in activityLines" :key="`${line.key}-points`">
                <circle
                  v-for="point in buildPoints(activityLines, line.key)"
                  :key="`${point.x}-${point.y}`"
                  :cx="point.x"
                  :cy="point.y"
                  r="3"
                  :fill="line.color"
                />
              </g>
              <text
                v-for="label in getAxisLabels()"
                :key="label.bucket"
                :x="label.x"
                :y="chartHeight - 10"
                class="line-chart__label"
                text-anchor="middle"
              >
                {{ label.bucket }}
              </text>
            </svg>
            <div class="line-chart__legend">
              <span v-for="line in activityLines" :key="line.key">
                <i :style="{ backgroundColor: line.color }"></i>{{ line.label }}
              </span>
            </div>
          </div>
          <div v-else class="dashboard-empty">{{ t('dashboard.empty.series') }}</div>
        </article>

        <article class="dashboard-panel dashboard-panel--full dashboard-panel--llm-models">
          <div class="dashboard-panel__header">
            <div>
              <h3>{{ t('dashboard.charts.llmModelsTitle') }}</h3>
              <p>{{ t('dashboard.charts.llmModelsHint') }}</p>
            </div>
            <Sparkles :size="20" />
          </div>
          <div v-if="hasLlmModelSeriesData" class="line-chart line-chart--llm-models">
            <svg :viewBox="`0 0 ${chartWidth} ${llmModelChartHeight}`" role="img" :aria-label="t('dashboard.charts.llmModelsTitle')">
              <line
                :x1="chartPadding.left"
                :x2="chartWidth - chartPadding.right"
                :y1="llmModelChartHeight - chartPadding.bottom"
                :y2="llmModelChartHeight - chartPadding.bottom"
                class="line-chart__axis"
              />
              <line
                :x1="chartPadding.left"
                :x2="chartPadding.left"
                :y1="chartPadding.top"
                :y2="llmModelChartHeight - chartPadding.bottom"
                class="line-chart__axis"
              />
              <path
                v-for="line in llmModelLines"
                :key="line.model"
                class="line-chart__line"
                :d="buildLlmModelLinePath(line)"
                :stroke="line.color"
              />
              <g v-for="line in llmModelLines" :key="`${line.model}-points`">
                <circle
                  v-for="point in buildLlmModelPoints(line)"
                  :key="`${line.model}-${point.bucket}`"
                  :cx="point.x"
                  :cy="point.y"
                  r="3"
                  :fill="line.color"
                >
                  <title>{{ `${line.label} · ${point.bucket}: ${formatNumber(point.segment_count)} ${t('dashboard.charts.segments')}` }}</title>
                </circle>
              </g>
              <text
                v-for="label in getLlmModelAxisLabels()"
                :key="label.bucket"
                :x="label.x"
                :y="llmModelChartHeight - 10"
                class="line-chart__label"
                text-anchor="middle"
              >
                {{ label.bucket }}
              </text>
            </svg>
            <div class="line-chart__legend">
              <span v-for="line in llmModelLines" :key="line.model" :title="line.model.startsWith('__') ? line.label : line.model">
                <i :style="{ backgroundColor: line.color }"></i>{{ line.label }}（{{ formatNumber(line.total_segment_count) }}）
              </span>
            </div>
          </div>
          <div v-else class="dashboard-empty">{{ t('dashboard.empty.llmModels') }}</div>
        </article>

        <article class="dashboard-panel dashboard-panel--full">
          <div class="dashboard-panel__header">
            <div>
              <h3>{{ t('dashboard.userStats.title') }}</h3>
              <p>{{ t('dashboard.userStats.hint') }}</p>
            </div>
            <Users :size="20" />
          </div>
          <div v-if="userStats.length" class="user-stats-table">
            <table>
              <thead>
                <tr>
                  <th>{{ t('dashboard.userStats.account') }}</th>
                  <th>{{ t('dashboard.userStats.roleStatus') }}</th>
                  <th>{{ t('dashboard.userStats.activeTime') }}</th>
                  <th>{{ t('dashboard.userStats.activeDays') }}</th>
                  <th>{{ t('dashboard.userStats.requests') }}</th>
                  <th>{{ t('dashboard.userStats.newWords') }}</th>
                  <th>{{ t('dashboard.userStats.modifiedWords') }}</th>
                  <th>{{ t('dashboard.userStats.totalWords') }}</th>
                  <th class="user-stats-table__sortable" :aria-sort="getUserStatsLastSeenAriaSort()">
                    <button
                      type="button"
                      class="user-stats-sort-button"
                      :title="t('dashboard.userStats.lastSeen')"
                      @click="toggleUserStatsLastSeenSort"
                    >
                      <span>{{ t('dashboard.userStats.lastSeen') }}</span>
                      <component :is="getUserStatsLastSeenSortIcon()" :size="14" aria-hidden="true" />
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="user in userStats" :key="user.user_id || 'unassigned'">
                  <td>
                    <div class="user-stat-account">
                      <strong>{{ user.display_name }}</strong>
                      <span>{{ user.username || t('dashboard.userStats.unassignedUsername') }}</span>
                    </div>
                  </td>
                  <td>
                    <div class="user-stat-role">
                      <span class="user-stat-chip" :class="{ 'is-muted': !user.user_id }">
                        {{ getUserRoleLabel(user) }}
                      </span>
                      <small>
                        {{ getUserStatusLabel(user) }}
                        <template v-if="getTranslatorTypeLabel(user)">
                          / {{ getTranslatorTypeLabel(user) }}
                        </template>
                      </small>
                    </div>
                  </td>
                  <td>{{ formatDuration(user.estimated_active_minutes) }}</td>
                  <td>{{ formatNumber(user.active_day_count) }}</td>
                  <td>{{ formatNumber(user.request_count) }}</td>
                  <td>{{ formatNumber(user.new_source_word_count) }}</td>
                  <td>{{ formatNumber(user.modified_source_word_count) }}</td>
                  <td><strong>{{ formatNumber(user.total_source_word_count) }}</strong></td>
                  <td>{{ formatDateTime(user.last_seen_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="dashboard-empty">{{ t('dashboard.empty.userStats') }}</div>
        </article>

        <article class="dashboard-panel">
          <div class="dashboard-panel__header">
            <div>
              <h3>{{ t('dashboard.languagePairs.title') }}</h3>
              <p>{{ t('dashboard.languagePairs.hint') }}</p>
            </div>
            <Languages :size="20" />
          </div>
          <div v-if="topLanguagePairs.length" class="language-pair-list">
            <div v-for="pair in topLanguagePairs" :key="`${pair.source_language}-${pair.target_language}`" class="language-pair-row">
              <div class="language-pair-row__main">
                <strong>{{ getPairLabel(pair.source_language, pair.target_language) }}</strong>
                <span>
                  {{ t('dashboard.languagePairs.meta', { projects: pair.project_count, files: pair.file_count }) }}
                </span>
              </div>
              <div class="language-pair-row__stats">
                <span>{{ formatNumber(pair.translated_source_word_count) }}</span>
                <small>{{ t('dashboard.languagePairs.translated') }}</small>
              </div>
              <div class="language-pair-row__stats">
                <span>{{ formatNumber(pair.llm_processed_source_word_count) }}</span>
                <small>{{ t('dashboard.languagePairs.llm') }}</small>
              </div>
            </div>
          </div>
          <div v-else class="dashboard-empty">{{ t('dashboard.empty.languagePairs') }}</div>
        </article>

        <article class="dashboard-panel dashboard-panel--sources">
          <div class="dashboard-panel__header">
            <div>
              <h3>{{ t('dashboard.sources.title') }}</h3>
              <p>{{ t('dashboard.sources.hint') }}</p>
            </div>
          </div>
          <div v-if="dashboard?.source_breakdown.length" class="source-breakdown">
            <div v-for="item in dashboard.source_breakdown" :key="item.source" class="source-breakdown__item">
              <span>{{ item.label }}</span>
              <strong>{{ formatNumber(item.source_word_count) }}</strong>
              <small>{{ t('dashboard.sources.events', { count: item.event_count }) }}</small>
            </div>
          </div>
          <div v-else class="dashboard-empty">{{ t('dashboard.empty.sources') }}</div>
        </article>
      </section>
    </template>
  </main>
</template>

<style scoped>
.dashboard-page {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding-bottom: 32px;
}

.dashboard-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.dashboard-toolbar__title {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.dashboard-toolbar__title h2,
.dashboard-panel h3 {
  margin: 0;
  color: var(--text-primary);
}

.dashboard-toolbar__title h2 {
  font-size: 20px;
}

.dashboard-toolbar__title p,
.dashboard-panel p {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.dashboard-toolbar__icon,
.dashboard-kpi__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  border-radius: 8px;
}

.dashboard-toolbar__icon {
  width: 38px;
  height: 38px;
  color: #2563eb;
  background: rgba(37, 99, 235, 0.1);
}

.dashboard-toolbar__actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.segmented-control {
  display: inline-grid;
  grid-template-columns: repeat(2, minmax(72px, 1fr));
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  overflow: hidden;
  background: var(--surface-panel);
}

.segmented-control button,
.icon-button {
  border: 0;
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
}

.segmented-control button {
  min-height: 34px;
  padding: 0 14px;
  font-size: 13px;
}

.segmented-control button.active {
  color: #ffffff;
  background: #2563eb;
}

.icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.spinning {
  animation: dashboard-spin 1s linear infinite;
}

.dashboard-kpis {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
}

.dashboard-kpi,
.dashboard-panel,
.dashboard-state {
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-panel);
}

.dashboard-kpi {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  padding: 14px;
}

.dashboard-kpi__icon {
  width: 36px;
  height: 36px;
}

.dashboard-kpi--blue .dashboard-kpi__icon { color: #2563eb; background: rgba(37, 99, 235, 0.1); }
.dashboard-kpi--teal .dashboard-kpi__icon { color: #0f766e; background: rgba(15, 118, 110, 0.1); }
.dashboard-kpi--green .dashboard-kpi__icon { color: #059669; background: rgba(5, 150, 105, 0.1); }
.dashboard-kpi--violet .dashboard-kpi__icon { color: #7c3aed; background: rgba(124, 58, 237, 0.1); }
.dashboard-kpi--orange .dashboard-kpi__icon { color: #d97706; background: rgba(217, 119, 6, 0.12); }
.dashboard-kpi--slate .dashboard-kpi__icon { color: #475569; background: rgba(71, 85, 105, 0.1); }

.dashboard-kpi__body {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.dashboard-kpi__body span {
  color: var(--text-secondary);
  font-size: 12px;
}

.dashboard-kpi__body strong {
  color: var(--text-primary);
  font-size: 22px;
  line-height: 1.1;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
  gap: 14px;
}

.dashboard-panel {
  min-width: 0;
  padding: 16px;
}

.dashboard-panel--wide {
  grid-column: span 1;
}

.dashboard-panel--full {
  grid-column: 1 / -1;
}

.dashboard-panel__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.line-chart {
  min-height: 260px;
}

.line-chart svg {
  display: block;
  width: 100%;
  height: auto;
  min-height: 220px;
}

.line-chart--llm-models {
  min-height: 220px;
}

.line-chart--llm-models svg {
  min-height: 180px;
}

.line-chart__axis {
  stroke: var(--line-soft);
  stroke-width: 1;
}

.line-chart__line {
  fill: none;
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.line-chart__label {
  fill: var(--text-muted);
  font-size: 12px;
}

.line-chart__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 8px;
  color: var(--text-secondary);
  font-size: 12px;
}

.line-chart__legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.line-chart__legend i {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.language-pair-list,
.source-breakdown {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.language-pair-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 100px 100px;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-muted);
}

.language-pair-row__main {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.language-pair-row__main strong {
  overflow: hidden;
  color: var(--text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.language-pair-row__main span,
.language-pair-row__stats small,
.source-breakdown__item small {
  color: var(--text-secondary);
  font-size: 12px;
}

.language-pair-row__stats {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.language-pair-row__stats span,
.source-breakdown__item strong {
  color: var(--text-primary);
  font-weight: 700;
}

.source-breakdown__item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 10px;
  padding: 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--surface-muted);
}

.source-breakdown__item span {
  color: var(--text-primary);
  font-weight: 600;
}

.source-breakdown__item small {
  grid-column: 1 / -1;
}

.user-stats-table {
  overflow-x: auto;
}

.user-stats-table table {
  width: 100%;
  min-width: 980px;
  border-collapse: collapse;
}

.user-stats-table th,
.user-stats-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-soft);
  color: var(--text-primary);
  font-size: 13px;
  text-align: right;
  white-space: nowrap;
}

.user-stats-table th {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  background: var(--surface-muted);
}

.user-stats-table__sortable {
  user-select: none;
}

.user-stats-sort-button {
  display: inline-flex;
  width: 100%;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  border: 0;
  padding: 0;
  color: inherit;
  background: transparent;
  font: inherit;
  cursor: pointer;
}

.user-stats-sort-button:hover,
.user-stats-sort-button:focus-visible {
  color: #2563eb;
}

.user-stats-sort-button:focus-visible {
  outline: 2px solid rgba(37, 99, 235, 0.35);
  outline-offset: 3px;
}

.user-stats-sort-button svg {
  flex: 0 0 auto;
}

.user-stats-table th:first-child,
.user-stats-table th:nth-child(2),
.user-stats-table td:first-child,
.user-stats-table td:nth-child(2) {
  text-align: left;
}

.user-stats-table tbody tr:last-child td {
  border-bottom: 0;
}

.user-stat-account,
.user-stat-role {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.user-stat-account strong {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-stat-account span,
.user-stat-role small {
  color: var(--text-secondary);
  font-size: 12px;
}

.user-stat-chip {
  display: inline-flex;
  width: max-content;
  max-width: 140px;
  align-items: center;
  justify-content: center;
  padding: 3px 8px;
  border-radius: 999px;
  color: #0f766e;
  background: rgba(15, 118, 110, 0.1);
  font-size: 12px;
  font-weight: 700;
}

.user-stat-chip.is-muted {
  color: #475569;
  background: rgba(71, 85, 105, 0.1);
}

.dashboard-state,
.dashboard-empty {
  padding: 18px;
  color: var(--text-secondary);
  text-align: center;
}

.dashboard-state--error {
  color: #b91c1c;
  border-color: rgba(185, 28, 28, 0.25);
  background: rgba(254, 226, 226, 0.6);
}

@keyframes dashboard-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1180px) {
  .dashboard-kpis {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .dashboard-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .dashboard-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .dashboard-toolbar__actions {
    justify-content: space-between;
  }

  .dashboard-kpis {
    grid-template-columns: 1fr;
  }

  .language-pair-row {
    grid-template-columns: 1fr;
  }

  .language-pair-row__stats {
    align-items: flex-start;
  }
}
</style>
