<script setup lang="ts">
import axios from 'axios'
import {
  Activity,
  BarChart3,
  Briefcase,
  Languages,
  RefreshCw,
  Sparkles,
  TrendingUp,
  Users,
} from 'lucide-vue-next'
import {
  NAlert,
  NButton,
  NCard,
  NConfigProvider,
  NDataTable,
  NEmpty,
  NIcon,
  NRadioButton,
  NRadioGroup,
  NSpin,
  NStatistic,
  NTabPane,
  NTag,
  NTabs,
  type DataTableColumns,
} from 'naive-ui'
import type { EChartsCoreOption } from 'echarts/core'
import { computed, h, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { http } from '../api/http'
import EChart from '../components/charts/EChart.vue'
import { getLLMModelShortLabel } from '../constants/llm'
import { formatLanguagePair } from '../constants/languages'
import { useDashboardTheme } from '../composables/useDashboardTheme'
import type {
  AnalyticsDashboardResponse,
  AnalyticsGranularity,
  AnalyticsLlmModelSeries,
  AnalyticsUserStat,
} from '../types/api'

interface LlmModelLine extends AnalyticsLlmModelSeries {
  label: string
}

const { t } = useI18n()
const { naiveTheme, naiveThemeOverrides, chartTheme } = useDashboardTheme()

const granularity = ref<AnalyticsGranularity>('day')
const dashboard = ref<AnalyticsDashboardResponse | null>(null)
const loading = ref(false)
const pageError = ref('')

const summary = computed(() => dashboard.value?.summary)
const series = computed(() => dashboard.value?.series ?? [])
const llmModelLines = computed<LlmModelLine[]>(() => (
  (dashboard.value?.llm_model_series ?? []).map((item) => ({
    ...item,
    label: getLlmModelLabel(item.model),
  }))
))
const topLanguagePairs = computed(() => (dashboard.value?.language_pairs ?? []).slice(0, 8))
const userStats = computed(() => dashboard.value?.user_stats ?? [])
const sourceBreakdown = computed(() => dashboard.value?.source_breakdown ?? [])
const hasSeriesData = computed(() => series.value.some((item) => (
  item.project_created_count
  || item.translated_source_word_count
  || item.llm_processed_source_word_count
  || item.active_user_count
)))
const hasLlmModelSeriesData = computed(() => llmModelLines.value.some((line) => (
  line.total_segment_count > 0
)))

const kpiItems = computed(() => {
  const data = summary.value
  return [
    {
      key: 'progress',
      label: t('dashboard.kpis.progress'),
      value: `${formatNumber(data?.translation_progress, 1)}%`,
      icon: Activity,
      color: '#607677',
      background: 'rgba(96, 118, 119, 0.12)',
    },
    {
      key: 'translated',
      label: t('dashboard.kpis.translatedWords'),
      value: formatNumber(data?.translated_source_word_count),
      icon: TrendingUp,
      color: '#0d7a68',
      background: 'rgba(13, 122, 104, 0.12)',
    },
    {
      key: 'llm',
      label: t('dashboard.kpis.llmWords'),
      value: formatNumber(data?.llm_processed_source_word_count),
      icon: Sparkles,
      color: '#7a5fb5',
      background: 'rgba(122, 95, 181, 0.12)',
    },
    {
      key: 'activeUsers',
      label: t('dashboard.kpis.activeUsers'),
      value: formatNumber(data?.active_users_today),
      icon: Users,
      color: '#b7791f',
      background: 'rgba(183, 121, 31, 0.14)',
    },
    {
      key: 'resources',
      label: t('dashboard.kpis.resources'),
      value: `${formatNumber(data?.total_projects)} / ${formatNumber(data?.total_files)}`,
      icon: Briefcase,
      color: '#2f9786',
      background: 'rgba(47, 151, 134, 0.12)',
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

function parseUserLastSeenTime(user: AnalyticsUserStat) {
  if (!user.last_seen_at) {
    return null
  }
  const timestamp = new Date(user.last_seen_at).getTime()
  return Number.isNaN(timestamp) ? null : timestamp
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

function getUserRoleTagType(user: AnalyticsUserStat) {
  if (!user.user_id) {
    return 'default' as const
  }
  if (user.role === 'super_admin') {
    return 'error' as const
  }
  if (user.role === 'admin') {
    return 'warning' as const
  }
  return 'success' as const
}

function getUserStatusLabel(user: AnalyticsUserStat) {
  if (!user.user_id) {
    return t('dashboard.userStats.historicalStatus')
  }
  return user.is_active ? t('dashboard.userStats.activeStatus') : t('dashboard.userStats.disabledStatus')
}

function getTranslatorTypeLabel(user: AnalyticsUserStat) {
  if (!user.user_id || user.role !== 'user') {
    return ''
  }
  return user.translator_type === 'internal'
    ? t('dashboard.userStats.internalTranslator')
    : t('dashboard.userStats.externalTranslator')
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

function getPairLabel(sourceLanguage: string | null, targetLanguage: string | null) {
  return formatLanguagePair(sourceLanguage, targetLanguage)
}

// ---------- ECharts 配置 ----------

const chartGrid = { left: 12, right: 20, top: 40, bottom: 8, containLabel: true }

function axisTooltip() {
  return {
    trigger: 'axis' as const,
    backgroundColor: chartTheme.value.tooltipBg,
    borderColor: chartTheme.value.tooltipBorder,
    textStyle: { color: chartTheme.value.textColor },
  }
}

function categoryAxis(data: string[]) {
  return {
    type: 'category' as const,
    data,
    axisLine: { lineStyle: { color: chartTheme.value.axisLineColor } },
    axisTick: { show: false },
    axisLabel: { color: chartTheme.value.axisLabelColor },
  }
}

function valueAxis() {
  return {
    type: 'value' as const,
    axisLabel: { color: chartTheme.value.axisLabelColor },
    splitLine: { lineStyle: { color: chartTheme.value.splitLineColor } },
  }
}

function legendConfig() {
  return {
    top: 0,
    icon: 'roundRect',
    itemWidth: 14,
    itemHeight: 6,
    textStyle: { color: chartTheme.value.textColor, fontSize: 12 },
  }
}

const translationChartOption = computed<EChartsCoreOption>(() => ({
  color: chartTheme.value.palette,
  tooltip: axisTooltip(),
  legend: legendConfig(),
  grid: chartGrid,
  xAxis: categoryAxis(series.value.map((item) => item.bucket)),
  yAxis: valueAxis(),
  series: [
    {
      name: t('dashboard.charts.translatedWords'),
      type: 'line',
      smooth: true,
      symbolSize: 6,
      data: series.value.map((item) => item.translated_source_word_count),
      areaStyle: { opacity: 0.12 },
      lineStyle: { width: 2.5 },
    },
    {
      name: t('dashboard.charts.llmWords'),
      type: 'line',
      smooth: true,
      symbolSize: 6,
      data: series.value.map((item) => item.llm_processed_source_word_count),
      areaStyle: { opacity: 0.12 },
      lineStyle: { width: 2.5 },
    },
  ],
}))

const activityChartOption = computed<EChartsCoreOption>(() => ({
  color: [chartTheme.value.palette[2], chartTheme.value.palette[0]],
  tooltip: axisTooltip(),
  legend: legendConfig(),
  grid: chartGrid,
  xAxis: categoryAxis(series.value.map((item) => item.bucket)),
  yAxis: [
    valueAxis(),
    { ...valueAxis(), splitLine: { show: false } },
  ],
  series: [
    {
      name: t('dashboard.charts.createdProjects'),
      type: 'bar',
      barMaxWidth: 26,
      itemStyle: { borderRadius: [4, 4, 0, 0] },
      data: series.value.map((item) => item.project_created_count),
    },
    {
      name: t('dashboard.charts.activeUsers'),
      type: 'line',
      yAxisIndex: 1,
      smooth: true,
      symbolSize: 6,
      lineStyle: { width: 2.5 },
      data: series.value.map((item) => item.active_user_count),
    },
  ],
}))

const llmModelChartOption = computed<EChartsCoreOption>(() => ({
  color: chartTheme.value.palette,
  tooltip: axisTooltip(),
  legend: { ...legendConfig(), type: 'scroll' },
  grid: chartGrid,
  xAxis: categoryAxis(llmModelLines.value[0]?.points.map((point) => point.bucket) ?? []),
  yAxis: valueAxis(),
  series: llmModelLines.value.map((line) => ({
    name: line.label,
    type: 'line',
    smooth: true,
    symbolSize: 5,
    lineStyle: { width: 2 },
    emphasis: { focus: 'series' as const },
    data: line.points.map((point) => point.segment_count),
  })),
}))

const sourcePieOption = computed<EChartsCoreOption>(() => ({
  color: chartTheme.value.palette,
  tooltip: {
    trigger: 'item' as const,
    backgroundColor: chartTheme.value.tooltipBg,
    borderColor: chartTheme.value.tooltipBorder,
    textStyle: { color: chartTheme.value.textColor },
  },
  legend: {
    ...legendConfig(),
    top: 'auto',
    bottom: 0,
    type: 'scroll',
  },
  series: [
    {
      type: 'pie',
      radius: ['48%', '72%'],
      center: ['50%', '44%'],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderWidth: 2, borderColor: chartTheme.value.tooltipBg },
      label: { color: chartTheme.value.textColor, formatter: '{b}\n{d}%' },
      data: sourceBreakdown.value.map((item) => ({
        name: item.label,
        value: item.source_word_count,
      })),
    },
  ],
}))

const languagePairBarOption = computed<EChartsCoreOption>(() => {
  const pairs = [...topLanguagePairs.value].reverse()
  return {
    color: [chartTheme.value.palette[0], chartTheme.value.palette[3]],
    tooltip: axisTooltip(),
    legend: legendConfig(),
    grid: { ...chartGrid, left: 8 },
    xAxis: valueAxis(),
    yAxis: {
      type: 'category' as const,
      data: pairs.map((pair) => getPairLabel(pair.source_language, pair.target_language)),
      axisLine: { lineStyle: { color: chartTheme.value.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: chartTheme.value.textColor },
    },
    series: [
      {
        name: t('dashboard.languagePairs.translated'),
        type: 'bar',
        barMaxWidth: 14,
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        data: pairs.map((pair) => pair.translated_source_word_count),
      },
      {
        name: t('dashboard.languagePairs.llm'),
        type: 'bar',
        barMaxWidth: 14,
        itemStyle: { borderRadius: [0, 4, 4, 0] },
        data: pairs.map((pair) => pair.llm_processed_source_word_count),
      },
    ],
  }
})

// ---------- 用户统计表 ----------

const userStatColumns = computed<DataTableColumns<AnalyticsUserStat>>(() => [
  {
    title: t('dashboard.userStats.account'),
    key: 'account',
    fixed: 'left',
    minWidth: 160,
    render(row) {
      const displayName = row.display_name?.trim() || t('dashboard.userStats.unassignedUsername')
      const username = row.username?.trim()
      const showUsername = Boolean(username && username.toLocaleLowerCase() !== displayName.toLocaleLowerCase())
      return h('div', { class: 'user-stat-account' }, [
        h('strong', null, displayName),
        showUsername ? h('span', null, username) : null,
      ])
    },
  },
  {
    title: t('dashboard.userStats.roleStatus'),
    key: 'roleStatus',
    minWidth: 130,
    render(row) {
      const translatorLabel = getTranslatorTypeLabel(row)
      return h('div', { class: 'user-stat-role' }, [
        h(NTag, { size: 'small', type: getUserRoleTagType(row), bordered: false }, {
          default: () => getUserRoleLabel(row),
        }),
        h('small', null, translatorLabel
          ? `${getUserStatusLabel(row)} / ${translatorLabel}`
          : getUserStatusLabel(row)),
      ])
    },
  },
  {
    title: t('dashboard.userStats.activeTime'),
    key: 'estimated_active_minutes',
    align: 'right',
    minWidth: 110,
    sorter: (a, b) => Number(a.estimated_active_minutes || 0) - Number(b.estimated_active_minutes || 0),
    render: (row) => formatDuration(row.estimated_active_minutes),
  },
  {
    title: t('dashboard.userStats.activeDays'),
    key: 'active_day_count',
    align: 'right',
    minWidth: 90,
    sorter: (a, b) => Number(a.active_day_count || 0) - Number(b.active_day_count || 0),
    render: (row) => formatNumber(row.active_day_count),
  },
  {
    title: t('dashboard.userStats.requests'),
    key: 'request_count',
    align: 'right',
    minWidth: 90,
    sorter: (a, b) => Number(a.request_count || 0) - Number(b.request_count || 0),
    render: (row) => formatNumber(row.request_count),
  },
  {
    title: t('dashboard.userStats.newModifiedWords'),
    key: 'source_word_breakdown',
    align: 'right',
    minWidth: 140,
    render: (row) => h('span', {
      title: `${t('dashboard.userStats.newWords')}: ${formatNumber(row.new_source_word_count)}\n${t('dashboard.userStats.modifiedWords')}: ${formatNumber(row.modified_source_word_count)}`,
    }, `${formatNumber(row.new_source_word_count)} / ${formatNumber(row.modified_source_word_count)}`),
  },
  {
    title: t('dashboard.userStats.totalWords'),
    key: 'total_source_word_count',
    align: 'right',
    minWidth: 110,
    defaultSortOrder: 'descend',
    sorter: (a, b) => compareUserStats(a, b),
    render: (row) => h('strong', null, formatNumber(row.total_source_word_count)),
  },
  {
    title: t('dashboard.userStats.lastSeen'),
    key: 'last_seen_at',
    align: 'right',
    minWidth: 150,
    sorter: (a, b) => (parseUserLastSeenTime(a) ?? -1) - (parseUserLastSeenTime(b) ?? -1),
    render: (row) => formatDateTime(row.last_seen_at),
  },
])

function getUserStatRowKey(row: AnalyticsUserStat) {
  return row.user_id || 'unassigned'
}

watch(granularity, () => {
  void loadDashboard()
})

onMounted(() => {
  void loadDashboard()
})
</script>

<template>
  <n-config-provider
    class="dashboard-theme"
    :theme="naiveTheme"
    :theme-overrides="naiveThemeOverrides"
  >
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
          <n-radio-group v-model:value="granularity" size="small" :aria-label="t('dashboard.range.label')">
            <n-radio-button value="day">
              {{ t('dashboard.range.day') }}
            </n-radio-button>
            <n-radio-button value="month">
              {{ t('dashboard.range.month') }}
            </n-radio-button>
          </n-radio-group>
          <n-button
            quaternary
            circle
            :title="t('common.actions.refresh')"
            :loading="loading"
            @click="loadDashboard"
          >
            <template #icon>
              <n-icon :component="RefreshCw" :size="18" />
            </template>
          </n-button>
        </div>
      </section>

      <n-alert v-if="pageError" type="error" :bordered="false">
        {{ pageError }}
      </n-alert>
      <div v-else-if="loading && !dashboard" class="dashboard-state">
        <n-spin size="large" />
      </div>
      <template v-else>
        <section class="dashboard-kpis" aria-live="polite">
          <n-card v-for="item in kpiItems" :key="item.key" size="small" class="dashboard-kpi" :bordered="true">
            <div class="dashboard-kpi__inner">
              <span class="dashboard-kpi__icon" :style="{ color: item.color, backgroundColor: item.background }">
                <component :is="item.icon" :size="20" />
              </span>
              <n-statistic :label="item.label" :value="item.value" class="dashboard-kpi__stat" />
            </div>
          </n-card>
        </section>

        <section class="dashboard-grid">
          <n-card class="dashboard-panel dashboard-panel--wide" :bordered="true">
            <template #header>
              <div class="dashboard-panel__title">
                <h3>{{ t('dashboard.charts.translationTitle') }}</h3>
                <p>{{ t('dashboard.charts.translationHint') }}</p>
              </div>
            </template>
            <EChart v-if="hasSeriesData" :option="translationChartOption" height="300px" />
            <n-empty v-else :description="t('dashboard.empty.series')" class="dashboard-empty" />
          </n-card>

          <n-card class="dashboard-panel" :bordered="true">
            <template #header>
              <div class="dashboard-panel__title">
                <h3>{{ t('dashboard.charts.activityTitle') }}</h3>
                <p>{{ t('dashboard.charts.activityHint') }}</p>
              </div>
            </template>
            <EChart v-if="hasSeriesData" :option="activityChartOption" height="300px" />
            <n-empty v-else :description="t('dashboard.empty.series')" class="dashboard-empty" />
          </n-card>

          <n-card class="dashboard-panel dashboard-panel--full dashboard-analysis" :bordered="true">
            <n-tabs type="line" animated>
              <n-tab-pane name="language-source" :tab="t('dashboard.tabs.languageSources')">
                <div class="dashboard-tab-grid">
                  <section class="dashboard-tab-section">
                    <div class="dashboard-panel__title dashboard-tab-section__title">
                      <div>
                        <h3>{{ t('dashboard.languagePairs.title') }}</h3>
                        <p>{{ t('dashboard.languagePairs.hint') }}</p>
                      </div>
                      <n-icon :component="Languages" :size="20" class="dashboard-panel__icon" />
                    </div>
                    <EChart v-if="topLanguagePairs.length" :option="languagePairBarOption" height="320px" />
                    <n-empty v-else :description="t('dashboard.empty.languagePairs')" class="dashboard-empty" />
                  </section>

                  <section class="dashboard-tab-section">
                    <div class="dashboard-panel__title dashboard-tab-section__title">
                      <div>
                        <h3>{{ t('dashboard.sources.title') }}</h3>
                        <p>{{ t('dashboard.sources.hint') }}</p>
                      </div>
                    </div>
                    <EChart v-if="sourceBreakdown.length" :option="sourcePieOption" height="320px" />
                    <n-empty v-else :description="t('dashboard.empty.sources')" class="dashboard-empty" />
                  </section>
                </div>
              </n-tab-pane>

              <n-tab-pane name="models" :tab="t('dashboard.tabs.models')">
                <div class="dashboard-panel__title dashboard-tab-section__title">
                  <div>
                    <h3>{{ t('dashboard.charts.llmModelsTitle') }}</h3>
                    <p>{{ t('dashboard.charts.llmModelsHint') }}</p>
                  </div>
                  <n-icon :component="Sparkles" :size="20" class="dashboard-panel__icon" />
                </div>
                <EChart v-if="hasLlmModelSeriesData" :option="llmModelChartOption" height="320px" />
                <n-empty v-else :description="t('dashboard.empty.llmModels')" class="dashboard-empty" />
              </n-tab-pane>

              <n-tab-pane name="team" :tab="t('dashboard.tabs.team')">
                <div class="dashboard-panel__title dashboard-tab-section__title">
                  <div>
                    <h3>{{ t('dashboard.userStats.title') }}</h3>
                    <p>{{ t('dashboard.userStats.hint') }}</p>
                  </div>
                  <n-icon :component="Users" :size="20" class="dashboard-panel__icon" />
                </div>
                <n-data-table
                  v-if="userStats.length"
                  :columns="userStatColumns"
                  :data="userStats"
                  :row-key="getUserStatRowKey"
                  :pagination="false"
                  :scroll-x="960"
                  size="small"
                  :bordered="false"
                  striped
                />
                <n-empty v-else :description="t('dashboard.empty.userStats')" class="dashboard-empty" />
              </n-tab-pane>
            </n-tabs>
          </n-card>
        </section>
      </template>
    </main>
  </n-config-provider>
</template>

<style scoped>
.dashboard-theme {
  display: block;
}

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

.dashboard-toolbar__title h2 {
  margin: 0;
  color: var(--text-primary);
  font-size: 20px;
}

.dashboard-toolbar__title p {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.dashboard-toolbar__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  border-radius: 8px;
  color: var(--brand-700);
  background: var(--brand-050);
}

.dashboard-toolbar__actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dashboard-kpis {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}

.dashboard-kpi :deep(.n-card__content) {
  padding: 14px;
}

.dashboard-kpi__inner {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.dashboard-kpi__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  border-radius: 10px;
}

.dashboard-kpi__stat {
  min-width: 0;
}

.dashboard-kpi__stat :deep(.n-statistic-value) {
  font-size: 22px;
  line-height: 1.2;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
  gap: 14px;
}

.dashboard-panel {
  min-width: 0;
}

.dashboard-panel--full {
  grid-column: 1 / -1;
}

.dashboard-panel__title h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 15px;
}

.dashboard-panel__title p {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.dashboard-panel__icon {
  color: var(--text-muted);
}

.dashboard-analysis :deep(.n-card__content) {
  padding-top: 8px;
}

.dashboard-tab-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
  gap: 28px;
}

.dashboard-tab-section {
  min-width: 0;
}

.dashboard-tab-section__title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin: 8px 0 12px;
}

.dashboard-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 18px;
}

.dashboard-empty {
  padding: 32px 0;
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

.user-stat-role :deep(.n-tag) {
  width: max-content;
}

@media (max-width: 1180px) {
  .dashboard-kpis {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .dashboard-grid {
    grid-template-columns: 1fr;
  }

  .dashboard-tab-grid {
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
}
</style>
