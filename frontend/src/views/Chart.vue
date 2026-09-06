<template>
  <div>
    <h1>{{ code }} {{ name }} · 日线与事实</h1>
    <p class="sub">{{ factNote }} · {{ rulesetTitle }} · 悬停看日期和收盘价，点击 K 线在下方看指标。买入不是成交指令。</p>
    <div class="row-btns" style="margin:0 0 14px" v-if="poolList.length">
      <button class="btn" :disabled="!prevStock" @click="goPool(prevStock)">上一只</button>
      <span class="sub" style="margin:0;align-self:center">{{ poolLabel }} {{ poolIndex + 1 }} / {{ poolList.length }}</span>
      <button class="btn primary" :disabled="!nextStock" @click="goPool(nextStock)">下一只</button>
    </div>
    <div class="warn-banner" v-if="scan.position_block || scan.status">{{ scan.position_block || "总闸" }} · {{ scan.status }}</div>
    <p class="sub" v-if="scan.key_kind">{{ scan.key_kind }} 关键位 {{ n2(scan.key_price) }} · 止损 {{ n2(scan.stop_price) }}</p>
    <div class="card" style="margin-bottom:14px">
      <KlineChart :bars="bars" :trigger-date="triggerDate" @pick="picked = $event" />
    </div>
    <div class="card" style="margin-bottom:14px">
      <div class="ov-title">{{ picked ? "点击日 " + picked.date : "点击 K 线查看该日指标" }}</div>
      <div class="fact-strip" v-if="picked">
        <div><small>开</small><b>{{ n2(picked.open) }}</b></div>
        <div><small>高</small><b>{{ n2(picked.high) }}</b></div>
        <div><small>低</small><b>{{ n2(picked.low) }}</b></div>
        <div><small>收</small><b>{{ n2(picked.close) }}</b></div>
      </div>
      <div class="fact-strip" v-if="picked">
        <div><small>MA5 / 10 / 20</small><b>{{ n2(picked.ma5) }} / {{ n2(picked.ma10) }} / {{ n2(picked.ma20) }}</b></div>
        <div><small>成交量</small><b>{{ vol(picked.volume) }}</b></div>
        <div><small>MACD(7,28,4) DIF / DEA / 柱</small><b>{{ n3(picked.dif) }} / {{ n3(picked.dea) }} / {{ n3(picked.hist) }}</b></div>
        <div><small>K / D / J</small><b>{{ n1(picked.k) }} / {{ n1(picked.d) }} / {{ n1(picked.j) }}</b></div>
        <div><small>PE</small><b>{{ n1(scan.facts && scan.facts.pe) }}</b></div>
      </div>
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
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import KlineChart from "../components/KlineChart.vue";

const route = useRoute();
const router = useRouter();
const code = ref("");
const name = ref("");
const bars = ref([]);
const scan = ref({});
const factNote = ref("这是事实记录");
const picked = ref(null);
const poolList = ref([]);
const triggerDate = computed(() => route.query.trigger || "");
const rulesetId = computed(() => String(route.query.ruleset || "rules"));
const poolGate = computed(() => {
  const g = String(route.query.pool || "");
  return g === "买入" || g === "观察" ? g : "";
});
const rulesetTitle = ref("");
const poolLabel = computed(() => (poolGate.value ? poolGate.value + "池" : ""));
const poolIndex = computed(() => {
  const cur = String(code.value || route.params.code || "");
  return poolList.value.findIndex((s) => String(s.code) === cur);
});
const prevStock = computed(() => {
  const i = poolIndex.value;
  if (i < 0 || poolList.value.length < 2) return null;
  return poolList.value[(i - 1 + poolList.value.length) % poolList.value.length];
});
const nextStock = computed(() => {
  const i = poolIndex.value;
  if (i < 0 || poolList.value.length < 2) return null;
  return poolList.value[(i + 1) % poolList.value.length];
});

function n1(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(1);
}
function n2(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2);
}
function n3(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(3);
}
function vol(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  if (n >= 1e8) return (n / 1e8).toFixed(2) + " 亿";
  if (n >= 1e4) return (n / 1e4).toFixed(0) + " 万";
  return n.toFixed(0);
}

function goPool(item) {
  if (!item || !item.code) return;
  router.push({
    path: "/chart/" + item.code,
    query: { ruleset: rulesetId.value, pool: poolGate.value },
  });
}
async function load() {
  const want = rulesetId.value;
  const wantCode = String(route.params.code || "");
  const wantPool = poolGate.value;
  const data = await api.chart(wantCode, want);
  if (rulesetId.value !== want || String(route.params.code || "") !== wantCode) return;
  const scanRow = data.scan || {};
  if (scanRow.ruleset && scanRow.ruleset !== want) return;
  code.value = wantCode;
  name.value = data.name;
  bars.value = data.bars || [];
  scan.value = scanRow;
  factNote.value = data.fact_note;
  rulesetTitle.value = (data.ruleset && data.ruleset.title) || want;
  picked.value = bars.value[bars.value.length - 1] || null;
  if (wantPool) {
    const scanData = await api.scan(want).catch(() => null);
    if (rulesetId.value !== want || String(route.params.code || "") !== wantCode) return;
    const rid = scanData && scanData.ruleset && scanData.ruleset.id;
    if (rid && rid !== want) {
      poolList.value = [];
    } else {
      const names = (scanData && scanData.names) || {};
      poolList.value = (names[wantPool] || []).filter((s) => !s.ruleset || s.ruleset === want);
    }
  } else {
    poolList.value = [];
  }
  if (route.query.watch) {
    await api.viewWatch(route.query.watch).catch(() => {});
  }
}
onMounted(load);
watch(() => [route.params.code, route.query.ruleset, route.query.pool], load);
</script>
