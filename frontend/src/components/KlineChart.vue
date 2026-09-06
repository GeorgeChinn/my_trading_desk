<template>
  <div ref="el" class="chart-box"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";

const GOLD = "#e8c547";
const DEA_RED = "#e53935";

const props = defineProps({
  bars: { type: Array, default: () => [] },
  triggerDate: { type: String, default: "" },
  segments: { type: Array, default: () => [] },
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
  const vols = bars.map((b, i) => {
    const up = Number(b.close) >= Number(b.open);
    return {
      value: b.volume == null ? 0 : Number(b.volume),
      itemStyle: { color: up ? "#e36a6a" : "#5ee0c5" },
    };
  });
  const dif = bars.map((b) => b.dif);
  const dea = bars.map((b) => b.dea);
  const hist = bars.map((b) => ({
    value: b.hist,
    itemStyle: { color: b.hist >= 0 ? "#e36a6a" : "#5ee0c5" },
  }));
  const kv = bars.map((b) => b.k);
  const dv = bars.map((b) => b.d);
  const jv = bars.map((b) => b.j);
  const kdjVals = [...kv, ...dv, ...jv].filter((x) => x != null && !Number.isNaN(Number(x))).map(Number);
  let kdjMin = 0;
  let kdjMax = 100;
  if (kdjVals.length) {
    kdjMin = Math.min(0, ...kdjVals);
    kdjMax = Math.max(100, ...kdjVals);
    const pad = Math.max(4, (kdjMax - kdjMin) * 0.06);
    kdjMin -= pad;
    kdjMax += pad;
  }
  const markLine = props.triggerDate
    ? {
        symbol: "none",
        label: { formatter: "触发日", color: GOLD },
        lineStyle: { color: GOLD, type: "dashed" },
        data: [{ xAxis: props.triggerDate }],
      }
    : undefined;
  const segs = (props.segments || []).filter((s) => s && s.buy_date);
  const markPointData = [];
  const markAreaData = [];
  for (const s of segs) {
    if (s.buy_date && s.buy_price != null) {
      markPointData.push({
        name: "买",
        coord: [s.buy_date, s.buy_price],
        value: "买",
        itemStyle: { color: GOLD },
        label: { formatter: "买", color: "#1a1208", fontSize: 10 },
      });
    }
    if (s.closed && s.sell_date && s.sell_price != null) {
      markPointData.push({
        name: "卖",
        coord: [s.sell_date, s.sell_price],
        value: "卖",
        itemStyle: { color: DEA_RED },
        label: { formatter: "卖", color: "#fff", fontSize: 10 },
      });
      markAreaData.push([{ xAxis: s.buy_date }, { xAxis: s.sell_date }]);
    } else if (s.buy_date) {
      const lastDate = dates.length ? dates[dates.length - 1] : s.buy_date;
      markAreaData.push([{ xAxis: s.buy_date }, { xAxis: lastDate }]);
    }
  }
  const markPoint = markPointData.length
    ? { symbol: "pin", symbolSize: 28, data: markPointData }
    : undefined;
  const markArea = markAreaData.length
    ? { itemStyle: { color: "rgba(232, 197, 71, 0.10)" }, data: markAreaData }
    : undefined;
  let zoomStart = 60;
  let zoomEnd = 100;
  if (segs.length && dates.length > 1) {
    const idxs = [];
    for (const s of segs) {
      const a = dates.indexOf(s.buy_date);
      const b = dates.indexOf(s.sell_date || dates[dates.length - 1]);
      if (a >= 0) idxs.push(a);
      if (b >= 0) idxs.push(b);
    }
    if (idxs.length) {
      const lo = Math.max(0, Math.min(...idxs) - 10);
      const hi = Math.min(dates.length - 1, Math.max(...idxs) + 6);
      zoomStart = (lo / (dates.length - 1)) * 100;
      zoomEnd = (hi / (dates.length - 1)) * 100;
      if (zoomEnd - zoomStart < 15) {
        zoomStart = Math.max(0, zoomStart - 8);
        zoomEnd = Math.min(100, zoomEnd + 8);
      }
    }
  }

  chart.setOption(
    {
      backgroundColor: "transparent",
      animation: false,
      legend: {
        data: ["MA5", "MA10", "MA20", "成交量", "MACD柱", "DIF", "DEA", "K", "D", "J"],
        selected: { MA5: true, MA10: false, MA20: true },
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
        { left: 56, right: 18, top: 36, height: "36%", backgroundColor: "rgba(232, 238, 244, 0.16)" },
        { left: 56, right: 18, top: "46%", height: "10%" },
        { left: 56, right: 18, top: "59%", height: "14%" },
        { left: 56, right: 18, top: "76%", height: "14%" },
      ],
      xAxis: [
        { type: "category", data: dates, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#1e3a4c" } } },
        { type: "category", data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#1e3a4c" } } },
        { type: "category", data: dates, gridIndex: 2, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#1e3a4c" } } },
        { type: "category", data: dates, gridIndex: 3, axisLabel: { color: "#8aa0b2" }, axisLine: { lineStyle: { color: "#1e3a4c" } } },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: "#1e3a4c" } }, axisLabel: { color: "#8aa0b2" } },
        { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { color: "#8aa0b2", fontSize: 10 }, name: "量", nameTextStyle: { color: "#8aa0b2", fontSize: 10 } },
        { scale: true, gridIndex: 2, splitLine: { show: false }, axisLabel: { color: "#8aa0b2" }, name: "MACD(7,28,4)", nameTextStyle: { color: "#8aa0b2", fontSize: 10 } },
        { scale: true, gridIndex: 3, min: kdjMin, max: kdjMax, splitLine: { show: false }, axisLabel: { color: "#8aa0b2" }, name: "KDJ", nameTextStyle: { color: "#8aa0b2", fontSize: 10 } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1, 2, 3], start: zoomStart, end: zoomEnd },
        { type: "slider", xAxisIndex: [0, 1, 2, 3], start: zoomStart, end: zoomEnd, height: 18, bottom: 4, borderColor: "#1e3a4c" },
      ],
      series: [
        { name: "日线", type: "candlestick", data: k, xAxisIndex: 0, yAxisIndex: 0, markLine, markPoint, markArea, itemStyle: { color: "#e36a6a", color0: "#5ee0c5", borderColor: "#e36a6a", borderColor0: "#5ee0c5" } },
        { name: "MA5", type: "line", data: ma5, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, itemStyle: { color: GOLD }, lineStyle: { width: 1.7, color: GOLD } },
        { name: "MA10", type: "line", data: ma10, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: "#6ea8ff" } },
        { name: "MA20", type: "line", data: ma20, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1.3, color: "#e53935" } },
        { name: "成交量", type: "bar", data: vols, xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 8 },
        { name: "MACD柱", type: "bar", data: hist, xAxisIndex: 2, yAxisIndex: 2, barMaxWidth: 8 },
        { name: "DIF", type: "line", data: dif, showSymbol: false, xAxisIndex: 2, yAxisIndex: 2, itemStyle: { color: GOLD }, lineStyle: { width: 1.2, color: GOLD } },
        { name: "DEA", type: "line", data: dea, showSymbol: false, xAxisIndex: 2, yAxisIndex: 2, itemStyle: { color: DEA_RED }, lineStyle: { width: 1.2, color: DEA_RED } },
        { name: "K", type: "line", data: kv, showSymbol: false, xAxisIndex: 3, yAxisIndex: 3, clip: false, lineStyle: { width: 1, color: "#e0a35c" } },
        { name: "D", type: "line", data: dv, showSymbol: false, xAxisIndex: 3, yAxisIndex: 3, clip: false, lineStyle: { width: 1, color: "#6ea8ff" } },
        { name: "J", type: "line", data: jv, showSymbol: false, xAxisIndex: 3, yAxisIndex: 3, clip: false, lineStyle: { width: 1, color: "#e36a6a" } },
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
watch(() => props.segments, render, { deep: true });
</script>

<style scoped>
.chart-box { width: 100%; height: 760px; }
@media (max-width: 720px) {
  .chart-box { height: 560px; }
}
</style>
