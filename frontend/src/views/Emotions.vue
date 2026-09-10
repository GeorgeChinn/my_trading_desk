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
        板块统计
        <span>{{ visibleBoards.length }} / {{ (data.boards || []).length }}</span>
      </div>
      <p class="sub" style="margin:0 0 10px">
        {{ data.board_note || "上涨占比＝板块一共多少只、多少只收红。涨停数：中军+多只小票联动＝共振；只有孤零零一只涨停＝孤狼。赚钱效应＝近20日板块内个股次日买入赚钱的概率。" }}
      </p>
      <div class="row-btns" style="margin-bottom:10px">
        <button class="btn" :class="{ primary: boardFilter === '全部' }" @click="boardFilter = '全部'">全部</button>
        <button class="btn" :class="{ primary: boardFilter === '共振' }" @click="boardFilter = '共振'">共振</button>
        <button class="btn" :class="{ primary: boardFilter === '孤狼' }" @click="boardFilter = '孤狼'">孤狼</button>
        <button class="btn" :class="{ primary: boardFilter === '有涨停' }" @click="boardFilter = '有涨停'">有涨停</button>
      </div>
      <div class="table-wrap" v-if="visibleBoards.length">
        <table class="table">
          <thead>
            <tr>
              <th>板块</th>
              <th>家数</th>
              <th>收红</th>
              <th>上涨占比</th>
              <th>涨停</th>
              <th>结构</th>
              <th>次日赚钱</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in visibleBoards" :key="b.name">
              <td>
                <b>{{ b.name }}</b>
                <div class="sub" style="margin:2px 0 0" v-if="b.zhongjun">中军 {{ b.zhongjun }}</div>
              </td>
              <td>{{ b.n }}</td>
              <td>{{ b.up }}</td>
              <td>{{ pct(b.up_pct) }}</td>
              <td>
                {{ b.limit_ups }}
                <div class="sub" style="margin:2px 0 0" v-if="(b.limit_names || []).length">{{ (b.limit_names || []).join("、") }}</div>
              </td>
              <td>
                <span class="badge" :class="b.kind === '共振' ? '买入' : b.kind === '孤狼' ? '观察' : ''">{{ b.kind }}</span>
                <div class="sub" style="margin:2px 0 0">{{ b.kind_detail }}</div>
              </td>
              <td>
                {{ b.next_day_win_pct == null ? "—" : b.next_day_win_pct.toFixed(1) + "%" }}
                <div class="sub" style="margin:2px 0 0">{{ b.next_day }}</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="empty mini" v-else>还没有板块统计。到数据与设置更新一次，或点刷新评分。</div>
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
const boardFilter = ref("全部");
const market = computed(() => data.value.market || {});
const flow = computed(() => data.value.flow || {});
const visibleBoards = computed(() => {
  const rows = data.value.boards || [];
  if (boardFilter.value === "共振") return rows.filter((b) => b.kind === "共振");
  if (boardFilter.value === "孤狼") return rows.filter((b) => b.kind === "孤狼");
  if (boardFilter.value === "有涨停") return rows.filter((b) => (b.limit_ups || 0) > 0);
  return rows;
});

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
