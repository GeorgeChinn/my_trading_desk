<template>
  <div ref="el" class="spark"></div>
</template>
<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";

const props = defineProps({
  path: { type: Array, default: () => [] },
});
const el = ref(null);
let chart;
function render() {
  if (!el.value) return;
  if (!chart) chart = echarts.init(el.value);
  const pts = props.path || [];
  chart.setOption(
    {
      animation: false,
      grid: { left: 8, right: 8, top: 8, bottom: 8 },
      xAxis: { type: "category", data: pts.map((p) => p.date), show: false },
      yAxis: { type: "value", scale: true, show: false },
      series: [
        {
          type: "line",
          data: pts.map((p) => p.close),
          showSymbol: false,
          lineStyle: { width: 1.4, color: "#e0a35c" },
        },
      ],
    },
    true
  );
}
onMounted(() => {
  render();
  window.addEventListener("resize", resize);
});
onBeforeUnmount(() => {
  window.removeEventListener("resize", resize);
  chart?.dispose();
});
function resize() {
  chart?.resize();
}
watch(() => props.path, render, { deep: true });
</script>
<style scoped>
.spark { width: 100%; height: 120px; }
@media (max-width: 720px) {
  .spark { height: 96px; }
}
</style>
