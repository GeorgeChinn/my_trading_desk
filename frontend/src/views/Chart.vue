<template>
  <div>
    <h1>{{ code }} {{ name }} · 日线与事实</h1>
    <p class="sub">{{ factNote }} · 只展示 MACD(7,28,4) + KDJ + 均线辅助。当前行情不改写已确认收盘事实。</p>
    <div class="warn-banner" v-if="scan.position_block">{{ scan.position_block }} · 总闸 {{ scan.status }}</div>
    <div class="card" style="margin-bottom:14px">
      <div class="fact-strip">
        <div><small>确认收盘日</small><b>{{ last.date || "—" }}</b></div>
        <div><small>收盘</small><b>{{ fmt(last.close) }}</b></div>
        <div><small>5日均线</small><b>{{ fmt(last.ma5) }}</b></div>
        <div><small>离均线</small><b>{{ pct(last.ma5_gap_pct) }}</b></div>
      </div>
      <div class="fact-strip">
        <div><small>DIF</small><b>{{ fmt4(last.dif) }}</b></div>
        <div><small>DEA</small><b>{{ fmt4(last.dea) }}</b></div>
        <div><small>MACD柱</small><b>{{ fmt4(last.hist) }}</b></div>
        <div><small>KDJ</small><b>{{ fmt(last.k) }} / {{ fmt(last.d) }} / {{ fmt(last.j) }}</b></div>
      </div>
    </div>
    <div class="card" style="margin-bottom:14px">
      <KlineChart :bars="bars" :trigger-date="triggerDate" />
    </div>
    <div class="grid cols-2">
      <div class="card">
        <h3>命中</h3>
        <div class="list" style="margin-top:8px">
          <div class="chip hit" v-for="(x, i) in scan.hit_rules || []" :key="i">{{ x }}</div>
        </div>
      </div>
      <div class="card">
        <h3>还缺</h3>
        <div class="list" style="margin-top:8px">
          <div class="chip miss" v-for="(x, i) in scan.missing_rules || []" :key="i">{{ x }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api";
import KlineChart from "../components/KlineChart.vue";

const route = useRoute();
const code = ref("");
const name = ref("");
const bars = ref([]);
const scan = ref({});
const factNote = ref("这是事实记录");
const triggerDate = computed(() => route.query.trigger || "");
const last = computed(() => {
  const row = bars.value[bars.value.length - 1] || {};
  const gap = row.close && row.ma5 ? ((row.close - row.ma5) / row.ma5) * 100 : null;
  return { ...row, ma5_gap_pct: gap };
});

function fmt(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2);
}
function fmt4(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(4);
}
function pct(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : `${Number(v).toFixed(2)}%`;
}

async function load() {
  code.value = route.params.code;
  const data = await api.chart(code.value);
  name.value = data.name;
  bars.value = data.bars || [];
  scan.value = data.scan || {};
  factNote.value = data.fact_note;
  if (route.query.watch) {
    await api.viewWatch(route.query.watch).catch(() => {});
  }
}
onMounted(load);
watch(() => route.params.code, load);
</script>
