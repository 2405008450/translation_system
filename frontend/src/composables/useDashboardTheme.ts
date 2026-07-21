import { computed } from 'vue'
import { darkTheme, type GlobalThemeOverrides } from 'naive-ui'

import { usePreferencesStore } from '../stores/preferences'

/**
 * 数据看板主题桥接：把应用现有设计 token（青绿品牌色 + data-theme 深色模式）
 * 映射给 Naive UI 组件和 ECharts 图表，保证看板与全站主题联动。
 *
 * 颜色取自 styles.css 的 :root / [data-theme='dark'] token。
 */
export function useDashboardTheme() {
  const preferencesStore = usePreferencesStore()
  const isDark = computed(() => preferencesStore.isDark)

  // Naive UI 组件主题：深色模式传 darkTheme，浅色传 null
  const naiveTheme = computed(() => (isDark.value ? darkTheme : null))

  // Naive UI 主题覆盖：把品牌青绿色注入主色，统一圆角风格
  const naiveThemeOverrides = computed<GlobalThemeOverrides>(() => {
    const primary = '#2f9786' // --brand-500
    const primaryHover = '#167e71' // --brand-650
    const primaryPressed = '#0d7a68' // --brand-700
    return {
      common: {
        primaryColor: primary,
        primaryColorHover: primaryHover,
        primaryColorPressed: primaryPressed,
        primaryColorSuppl: primary,
        borderRadius: '8px',
        fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
      },
    }
  })

  // ECharts 调色板与坐标轴/文字颜色，跟随深色模式
  const chartTheme = computed(() => {
    if (isDark.value) {
      return {
        // 系列调色板：品牌 teal 优先，其余取高区分度色
        palette: ['#2f9786', '#58b09c', '#e0a458', '#5b8dd9', '#b7791f', '#c23b3f', '#8a7ec9', '#9db0b1'],
        textColor: '#d7e4df', // --text-secondary (dark)
        axisLabelColor: '#9db0b1', // --text-muted (dark)
        axisLineColor: 'rgba(130, 156, 161, 0.34)', // --line-strong (dark)
        splitLineColor: 'rgba(130, 156, 161, 0.16)',
        tooltipBg: 'rgba(22, 31, 39, 0.96)', // --surface-panel (dark)
        tooltipBorder: 'rgba(130, 156, 161, 0.24)',
      }
    }
    return {
      palette: ['#2f9786', '#1565c0', '#b7791f', '#7a5fb5', '#c23b3f', '#5b8dd9', '#0d7a68', '#607677'],
      textColor: '#35515a', // --text-secondary (light)
      axisLabelColor: '#607677', // --text-muted (light)
      axisLineColor: '#bfd2cc', // --line-strong (light)
      splitLineColor: 'rgba(191, 210, 204, 0.45)',
      tooltipBg: 'rgba(255, 255, 255, 0.98)',
      tooltipBorder: '#d6e2de',
    }
  })

  return {
    isDark,
    naiveTheme,
    naiveThemeOverrides,
    chartTheme,
  }
}
