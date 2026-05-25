<script setup lang="ts">
import axios from 'axios'
import {
  Activity,
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
import { formatLanguagePair } from '../constants/languages'
import type {
  AnalyticsDashboardResponse,
  AnalyticsGranularity,
  AnalyticsSeriesPoint,
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

const { t } = useI18n()

const granularity = ref<AnalyticsGranularity>('day')
const dashboard = ref<AnalyticsDashboardResponse | null>(null)
const loading = ref(false)
const pageError = ref('')

const chartWidth = 720
const chartHeight = 240
const chartPadding = {
  top: 18,
  right: 24,
  bottom: 36,
  left: 46,
}

const summary = computed(() => dashboard.value?.summary)
const series = computed(() => dashboard.value?.series ?? [])
const topLanguagePairs = computed(() => (dashboard.value?.language_pairs ?? []).slice(0, 8))
const hasSeriesData = computed(() => series.value.some((item) => (
  item.project_created_count
  || item.translated_source_word_count
  || item.llm_processed_source_word_count
  || item.active_user_count
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
