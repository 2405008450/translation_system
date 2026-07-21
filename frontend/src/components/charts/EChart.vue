<script setup lang="ts">
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'
import VChart from 'vue-echarts'
import { computed } from 'vue'

// 按需注册，保持 bundle 体积
use([
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
])

const props = withDefaults(
  defineProps<{
    option: EChartsCoreOption
    height?: string
  }>(),
  { height: '280px' },
)

const style = computed(() => ({ width: '100%', height: props.height }))
</script>

<template>
  <VChart class="echart" :option="props.option" :style="style" autoresize />
</template>

<style scoped>
.echart {
  display: block;
}
</style>
