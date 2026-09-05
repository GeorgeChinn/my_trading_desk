<template>
  <div ref="el" class="chart-box"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";

const props = defineProps({
  bars: { type: Array, default: () => [] },
  triggerDate: { type: String, default: "" },
});
const emit = defineEmits(["pick"]);

const el = ref(null);
let chart;

function n2(v) {
  const x = Number(v);
  return v == null || Number.isNaN(x) ? "—" : x.toFixed(2);
}

function render() {
  if (!el.value) return;
  if (!chart) chart = echarts.init(el.value);
  const bars = props.bars || [];
  const dates = bars.map((b) => b.date);
  const k = bars.map((b) => [b.open, b.close, b.low, b.high]);
  const ma5 = bars.map((b) => b.ma5);
  const ma10 = bars.map((b) => b.ma10);
  const ma20 = bars.map((b) => b.ma20);
  const dif = bars.map((b) => b.dif);
  const dea = bars.map((b) => b.dea);
  const hist = bars.map((b) => ({
    value: b.hist,
    itemStyle: { color: b.hist >= 0 ? "#e36a6a" : "#5ee0c5" },
  }));
  const kv = bars.map((b) => b.k);
  const dv = bars.map((b) => b.d);
  const jv = bars.map((b) => b.j);
  const markLine = props.triggerDate
    ? {
        symbol: "none",
        label: { formatter: "触发日", color: "#e0a35c" },
        lineStyle: { color: "#e0a35c", type: "dashed" },
        data: [{ xAxis: props.triggerDate }],
      }
    : undefined;

  chart.setOption(
    {
      backgroundColor: "transparent",
      animation: false,
      legend: {
        data: ["MA5", "MA10", "MA20", "DIF", "DEA", "K", "D", "J"],
        textStyle: { color: "#8aa0b2" },
        top: 0,
      },
      tooltip: {
        trigger: "axis",
        triggerOn: "mousemove",
        axisPointer: { type: "line", label: { show: false } },
        backgroundColor: "rgba(7, 16, 24, 0.42)",
        borderWidth: 0,
        padding: [6, 10],
        textStyle: { color: "#e7eef4", fontSize: 12 },
        extraCssText: "backdrop-filter: blur(8px); pointer-events: none; box-shadow: none;",
        formatter: (params) => {
          const idx = params && params[0] ? params[0].dataIndex : null;
          const b = idx == null ? null : bars[idx];
          if (!b) return "";
          return `${b.date}<br/>收 ${n2(b.close)}`;
        },
      },
      axisPointer: {
        link: [{ xAxisIndex: "all" }],
        label: { show: false },
        lineStyle: { color: "rgba(224,163,92,0.45)", width: 1 },
      },
      grid: [
        { left: 48, right: 18, top: 36, height: "48%" },
        { left: 48, right: 18, top: "62%", height: "14%" },
        { left: 48, right: 18, top: "80%", height: "14%" },
      ],
      xAxis: [
        { type: "category", data: dates, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#1e3a4c" } } },
        { type: "category", data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#1e3a4c" } } },
        { type: "category", data: dates, gridIndex: 2, axisLabel: { color: "#8aa0b2" }, axisLine: { lineStyle: { color: "#1e3a4c" } } },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: "#1e3a4c" } }, axisLabel: { color: "#8aa0b2" } },
        { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { color: "#8aa0b2" } },
        { scale: true, gridIndex: 2, min: 0, max: 100, splitLine: { show: false }, axisLabel: { color: "#8aa0b2" } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1, 2], start: 60, end: 100 },
        { type: "slider", xAxisIndex: [0, 1, 2], start: 60, end: 100, height: 18, bottom: 4, borderColor: "#1e3a4c" },
      ],
      series: [
        { name: "日线", type: "candlestick", data: k, xAxisIndex: 0, yAxisIndex: 0, markLine, itemStyle: { color: "#e36a6a", color0: "#5ee0c5", borderColor: "#e36a6a", borderColor0: "#5ee0c5" } },
        { name: "MA5", type: "line", data: ma5, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1.2, color: "#e0a35c" } },
        { name: "MA10", type: "line", data: ma10, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: "#6ea8ff" } },
        { name: "MA20", type: "line", data: ma20, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: "#d38bff" } },
        { name: "MACD柱", type: "bar", data: hist, xAxisIndex: 1, yAxisIndex: 1 },
        { name: "DIF", type: "line", data: dif, showSymbol: false, xAxisIndex: 1, yAxisIndex: 1, lineStyle: { width: 1, color: "#e0a35c" } },
        { name: "DEA", type: "line", data: dea, showSymbol: false, xAxisIndex: 1, yAxisIndex: 1, lineStyle: { width: 1, color: "#6ea8ff" } },
        { name: "K", type: "line", data: kv, showSymbol: false, xAxisIndex: 2, yAxisIndex: 2, lineStyle: { width: 1, color: "#e0a35c" } },
        { name: "D", type: "line", data: dv, showSymbol: false, xAxisIndex: 2, yAxisIndex: 2, lineStyle: { width: 1, color: "#6ea8ff" } },
        { name: "J", type: "line", data: jv, showSymbol: false, xAxisIndex: 2, yAxisIndex: 2, lineStyle: { width: 1, color: "#e36a6a" } },
      ],
    },
    true
  );
  chart.off("click");
  chart.on("click", (params) => {
    const idx = params && params.dataIndex;
    if (idx == null || !bars[idx]) return;
    emit("pick", bars[idx]);
  });
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
watch(() => props.bars, render, { deep: true });
watch(() => props.triggerDate, render);
</script>

<style scoped>
.chart-box { width: 100%; height: 640px; }
@media (max-width: 720px) {
  .chart-box { height: 440px; }
}
</style>
