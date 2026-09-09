<template>
  <div>
    <h1>情绪资金</h1>
    <p class="sub">
      三层情绪只过滤、不触发买卖。优先级：大盘 → 板块 → 个股。资金异动以量价为第一优先级。不改 RULES 扫描闸门。
    </p>
    <div class="row-btns" style="margin-bottom:14px">
      <button class="btn primary" :disabled="loading" @click="load(true)">刷新评分</button>
      <span class="sub" style="margin:0;align-self:center">确认日 {{ data.asof || "—" }} · {{ data.updated_at || "" }}</span>
    </div>

    <div class="card emo-hero" :class="market.tone" style="margin-bottom:14px">
      <div class="emo-hero-left">
        <div class="emo-label">{{ market.label || "—" }}</div>
        <div class="emo-score">{{ n0(market.score) }}<small>/100</small></div>
        <p class="sub" style="margin:8px 0 0">{{ market.note }}</p>
      </div>
      <div class="score-ring" :style="ringStyle(market.score, market.tone)">
        <span>{{ n0(market.score) }}</span>
      </div>
    </div>

    <div class="grid cols-4" style="margin-bottom:14px">
      <div class="card metric" v-for="m in market.metrics || []" :key="m.key">
        <div class="k">{{ m.key }}</div>
        <div class="n">{{ fmtMetric(m) }}</div>
        <div class="score-track"><i :style="{ width: (m.score || 0) + '%', background: toneColor(m.score) }"></i></div>
        <p class="sub" style="margin:8px 0 0">{{ m.detail }}</p>
        <p class="sub" style="margin:4px 0 0">{{ m.bands }}</p>
      </div>
    </div>

    <div class="card" style="margin-bottom:14px">
      <div class="ov-title">
        板块情绪
        <span>{{ (data.boards || []).length }} / {{ data.board_total || 0 }}</span>
      </div>
      <p class="sub" style="margin:0 0 10px">涨停数：中军+多只小票=共振；单只涨停=孤狼。涨停≥10，次日买入赚钱概率偏高。</p>
      <div class="board-cloud">
        <span
          class="board-chip"
          :class="b.kind === '共振' ? 'pass' : b.kind === '孤狼' ? 'fail' : ''"
          v-for="b in data.boards || []"
          :key="b.name"
        >
          {{ b.name }}
          <em>{{ n0(b.score) }}分 · {{ b.kind }} · 涨停 {{ b.limit_ups }} · 上涨 {{ pct(b.up_pct) }}</em>
        </span>
      </div>
      <div class="empty mini" v-if="!(data.boards || []).length">还没有板块统计。到数据与设置更新一次。</div>
    </div>

    <div class="overview" style="margin-bottom:14px">
      <div class="ov-block">
        <div class="ov-title">建仓 <span>{{ (flow.build || []).length }}</span></div>
        <p class="sub" style="margin:0 0 8px">持续承接、底抬高。量能维持在 5 日均量之上。</p>
        <div class="name-cloud" v-if="(flow.build || []).length">
          <router-link class="name-chip 买入" v-for="s in flow.build" :key="'bd'+s.code" :to="'/chart/' + s.code">
            {{ title(s) }}
            <em>{{ n0(s.score) }} · {{ s.vol_ratio }}×</em>
          </router-link>
        </div>
        <div class="empty mini" v-else>空</div>
      </div>
      <div class="ov-block">
        <div class="ov-title">试盘 <span>{{ (flow.probe || []).length }}</span></div>
        <p class="sub" style="margin:0 0 8px">打一枪就停。试盘 ≠ 拉升。</p>
        <div class="name-cloud" v-if="(flow.probe || []).length">
          <router-link class="name-chip 观察" v-for="s in flow.probe" :key="'pr'+s.code" :to="'/chart/' + s.code">
            {{ title(s) }}
            <em>{{ n0(s.score) }} · {{ s.vol_ratio }}×</em>
          </router-link>
        </div>
        <div class="empty mini" v-else>空</div>
      </div>
    </div>
    <p class="sub">{{ (flow.note) || "" }}</p>

    <div class="card">
      <h3>个股核对</h3>
      <p class="sub">换手、振幅、封板质量。低位放量抬升=活跃；高位高换手慎出货。</p>
      <div class="row-btns" style="align-items:end;margin-bottom:12px">
        <label class="field" style="max-width:240px;margin:0">
          <span>代码</span>
          <input v-model="codeQ" placeholder="603979" @keyup.enter="loadStock" />
        </label>
        <button class="btn" @click="loadStock">看这只</button>
      </div>
      <div v-if="stock">
        <div class="ov-title">
          {{ title(stock) }}
          <span>{{ n0(stock.score) }} 分 · {{ stock.industry || "未分板块" }}</span>
        </div>
        <p class="sub">{{ stock.position }}</p>
        <div class="grid cols-3">
          <div class="stat" v-for="m in stock.metrics || []" :key="m.key">
            <div class="n">{{ fmtMetric(m) }}</div>
            <div class="k">{{ m.key }}</div>
            <div class="score-track"><i :style="{ width: (m.score || 0) + '%', background: toneColor(m.score) }"></i></div>
            <p class="sub" style="margin:6px 0 0">{{ m.detail }}</p>
          </div>
        </div>
        <div class="warn-banner" v-if="stock.flow" style="margin-top:12px">
          资金异动 {{ stock.flow.kind }} {{ n0(stock.flow.score) }} 分
          · {{ (stock.flow.reasons || []).join("；") }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { api } from "../api";
import { setLoadingText } from "../loading.js";

const data = ref({ market: {}, boards: [], flow: {} });
const stock = ref(null);
const codeQ = ref("");
const loading = ref(false);
const market = computed(() => data.value.market || {});
const flow = computed(() => data.value.flow || {});

function n0(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Math.round(Number(v));
}
function pct(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : `${Number(v).toFixed(1)}%`;
}
function fmtMetric(m) {
  if (!m) return "—";
  if (m.value == null || Number.isNaN(Number(m.value))) return "—";
  const n = Number(m.value);
  const body = m.unit === "%" ? n.toFixed(1) : String(Math.round(n * 10) / 10);
  return `${body}${m.unit || ""}`;
}
function title(s) {
  const code = (s && s.code) || "";
  const name = String((s && s.name) || "").trim();
  if (!name || name === code) return code;
  return `${name} ${code}`;
}
function toneColor(score) {
  const n = Number(score);
  if (n >= 65) return "var(--cyan)";
  if (n >= 40) return "var(--amber)";
  return "var(--blue)";
}
function ringStyle(score, tone) {
  const n = Math.max(0, Math.min(100, Number(score) || 0));
  const col = tone === "warm" ? "#5ee0c5" : tone === "ice" ? "#6ea8ff" : "#e6c35c";
  return {
    background: `conic-gradient(${col} ${n * 3.6}deg, #1e3a4c 0deg)`,
  };
}
async function load(force = false) {
  loading.value = true;
  setLoadingText("正在计算大盘 / 板块 / 资金异动评分…");
  try {
    const payload = await api.emotions(force ? { refresh: "true" } : {});
    data.value = payload || {};
  } finally {
    loading.value = false;
  }
}
async function loadStock() {
  const code = codeQ.value.trim();
  if (!code) return;
  setLoadingText("正在核对这只票的换手 / 振幅 / 资金异动…");
  const payload = await api.emotions({ code });
  stock.value = payload.stock || null;
  if (payload.market) data.value = { ...data.value, ...payload };
}
onMounted(() => load(false));
</script>
