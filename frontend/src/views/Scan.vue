<template>
  <div>
    <h1>规则扫描</h1>
    <p class="sub">
      总闸：排除 → 观察 → 买入 → 卖出。买入 = 路径到达，不是成交指令。
    </p>
    <div class="tabs">
      <button
        class="btn"
        v-for="rs in rulesets"
        :key="rs.id"
        :class="{ primary: rulesetId === rs.id }"
        @click="switchRuleset(rs.id)"
      >
        {{ rs.file }}
      </button>
    </div>
    <p class="sub" v-if="currentRuleset && data.pool">
      {{ currentRuleset.file }} · {{ currentRuleset.title }}
      · 池子 {{ data.pool.count }} 只 · {{ data.pool.source }} {{ data.pool.trade_date }}
    </p>
    <div class="warn-banner" v-for="(r, i) in (data.reminders || [])" :key="'rm'+i">{{ r }}</div>

    <div class="grid cols-4" style="margin-bottom:14px">
      <div class="card stat" v-for="k in gates" :key="k">
        <div class="n">{{ (data.by_gate && data.by_gate[k]) || 0 }}</div>
        <div class="k">{{ k }}</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:14px" v-if="isPullback">
      <div class="ov-title">
        第3.2条 主线
        <span>过关 {{ passedBoards.length }} / {{ boards.length }} · 沪深300 近3日 {{ pct(marketRet) }}</span>
      </div>
      <p class="sub" style="margin:0 0 10px">先筛主线：近3日相对大盘不弱即可，不要求领涨。过关后再在主线里挑个股。映射价格当日下跌不单独否决。</p>
      <div class="board-cloud" v-if="passedBoards.length">
        <span class="board-chip pass" v-for="b in passedBoards" :key="'p'+b.name">
          {{ b.name }}
          <em>{{ signed(b.ret_3d) }}</em>
        </span>
      </div>
      <div class="empty mini" v-else>没有过关板块</div>
      <p class="sub" style="margin:10px 0 0" v-if="failedBoards.length">
        未过关 {{ failedBoards.length }} 个：{{ failedBoards.slice(0, 8).map((b) => b.name).join("、") }}{{ failedBoards.length > 8 ? "…" : "" }}
      </p>
    </div>

    <div class="card overview" style="margin-bottom:14px">
      <div class="ov-block">
        <div class="ov-title">
          <div>买入池 <span>{{ buyNames.length }}</span></div>
          <button class="btn" :disabled="!buyNames.length" @click="runPoolBacktest('买入')">历史回测</button>
        </div>
        <p class="sub" style="margin:0 0 8px">路径到达，不是成交指令</p>
        <div class="name-cloud" v-if="buyNames.length">
          <router-link class="name-chip 买入" v-for="s in buyNames" :key="'b'+s.code" :to="chartLink(s.code, '买入')">
            {{ stockTitle(s) }}
            <em v-if="s.pe != null">PE {{ pe(s.pe) }}</em>
          </router-link>
        </div>
        <div class="empty mini" v-else>空</div>
      </div>
      <div class="ov-block">
        <div class="ov-title">
          <div>观察池 <span>{{ watchNames.length }}</span></div>
          <button class="btn" :disabled="!watchNames.length" @click="runPoolBacktest('观察')">历史回测</button>
        </div>
        <div class="name-cloud" v-if="watchNames.length">
          <router-link class="name-chip 观察" v-for="s in watchNames" :key="'w'+s.code" :to="chartLink(s.code, '观察')">
            {{ stockTitle(s) }}
            <em v-if="s.pe != null">PE {{ pe(s.pe) }}</em>
          </router-link>
        </div>
        <div class="empty mini" v-else>空</div>
      </div>
    </div>
    <div class="card" style="margin-bottom:14px" v-if="poolBacktest">
      <div class="ov-title">
        历史回测 · {{ poolBacktest.gate }}池
        <span>{{ poolClosed }} 段已卖出 · {{ poolOpen }} 段进行中</span>
      </div>
      <BacktestTable
        :rows="poolBacktest.segments"
        :note="poolBacktest.note"
        :empty-text="'当前' + poolBacktest.gate + '池股票，历史上没有买入到卖出的轨迹。'"
        show-code
        link-chart
        :ruleset-id="rulesetId"
        :pool="poolBacktest.gate"
      />
    </div>

    <div class="row-btns" style="margin-bottom:14px">
      <button class="btn" v-for="g in gates" :key="g" :class="{ primary: filter === g }" @click="filter = g">
        {{ g }} {{ (data.by_gate && data.by_gate[g]) || 0 }}
      </button>
      <button class="btn" :class="{ primary: filter === '在池' }" @click="filter = '在池'">观察+买入</button>
      <button class="btn" :class="{ primary: filter === '全部' }" @click="filter = '全部'">全部</button>
    </div>
    <label class="field" style="margin-bottom:14px;max-width:320px">
      <span>搜索代码 / 名称 / 板块</span>
      <input v-model="q" placeholder="600519 或 茅台 或 有色" />
    </label>
    <p class="sub">当前列出 {{ visible.length }} / {{ (data.rows || []).length }}</p>
    <div v-if="!visible.length" class="empty">{{ emptyText }}</div>
    <div v-for="grp in groups" :key="grp.key" style="margin-bottom:18px">
      <div class="ov-title" v-if="grp.title" style="margin-bottom:10px">
        {{ grp.title }}
        <span>{{ grp.rows.length }}</span>
      </div>
      <div v-for="row in grp.rows" :key="row.code" class="card" style="margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
          <div>
            <b>{{ stockTitle(row) }}</b>
            <span class="badge 观察" v-if="industryOf(row)" style="margin-left:8px">{{ industryOf(row) }}</span>
            <span class="pe-tag" v-if="row.facts && row.facts.pe != null">PE {{ pe(row.facts.pe) }}</span>
          </div>
          <StatusBadge :status="row.status" />
        </div>
        <p class="sub" style="margin:8px 0 0" v-if="row.facts && (row.facts.board_ret_3d != null || row.facts.vs_market != null)">
          板块近3日 {{ signed(row.facts.board_ret_3d) }}
          · 沪深300 {{ signed(row.facts.market_ret_3d) }}
          · 相对 {{ signed(row.facts.vs_market) }}
        </p>
        <div class="grid cols-2" style="margin-top:12px">
          <div>
            <div class="k" style="color:var(--muted);margin-bottom:6px">命中</div>
            <div class="list">
              <div class="chip hit" v-for="(x, i) in row.hit_rules" :key="'h'+i">{{ x }}</div>
              <div class="chip" v-if="!row.hit_rules.length">无</div>
            </div>
          </div>
          <div>
            <div class="k" style="color:var(--muted);margin-bottom:6px">还缺</div>
            <div class="list">
              <div class="chip miss" v-for="(x, i) in row.missing_rules" :key="'m'+i">{{ x }}</div>
              <div class="chip" v-if="!row.missing_rules.length">无</div>
            </div>
          </div>
        </div>
        <p class="sub" style="margin:10px 0 0">
          {{ row.facts && row.facts.date }} 收盘 {{ money(row.facts && row.facts.close) }}
          <span v-if="row.facts && row.facts.dif != null"> · DIF {{ fmt(row.facts.dif) }}</span>
          <span v-if="row.key_kind"> · {{ row.key_kind }} 关键位 {{ money(row.key_price) }} 止损 {{ money(row.stop_price) }}</span>
        </p>
        <div class="row-btns" style="margin-top:10px">
          <router-link class="btn" :to="chartLink(row.code, row.status === '买入' || row.status === '观察' ? row.status : '')">查看日线与事实</router-link>
          <router-link
            class="btn"
            v-if="row.status === '买入' || row.status === '观察'"
            :to="backtestLink(row.code, row.status)"
          >历史回测</router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, GATES } from "../api";
import { setLoadingText, showLoading } from "../loading.js";
import StatusBadge from "../components/StatusBadge.vue";
import BacktestTable from "../components/BacktestTable.vue";

const route = useRoute();
const router = useRouter();
const data = ref({ rows: [], summary: {}, by_gate: {}, names: {}, pool: {}, rulesets: [], boards: [] });
const extraRulesets = ref([]);
const cache = {};
const filter = ref("观察");
const q = ref("");
const loading = ref(true);
const poolBacktest = ref(null);
const gates = GATES;
const poolClosed = computed(() => ((poolBacktest.value && poolBacktest.value.segments) || []).filter((s) => s.closed).length);
const poolOpen = computed(() => ((poolBacktest.value && poolBacktest.value.segments) || []).filter((s) => !s.closed).length);
const rulesetId = computed(() => String(route.query.ruleset || "rules"));
const rulesets = computed(() => (data.value.rulesets && data.value.rulesets.length ? data.value.rulesets : extraRulesets.value));
const currentRuleset = computed(() => rulesets.value.find((r) => r.id === rulesetId.value) || data.value.ruleset || null);
const isPullback = computed(() => (currentRuleset.value && currentRuleset.value.engine) === "pullback_restart");
const boards = computed(() => data.value.boards || []);
const passedBoards = computed(() => boards.value.filter((b) => b.pass));
const failedBoards = computed(() => boards.value.filter((b) => !b.pass));
const marketRet = computed(() => (data.value.market && data.value.market.ret_3d_pct) ?? null);
function ownRow(row) {
  return !row || !row.ruleset || row.ruleset === rulesetId.value;
}
const buyNames = computed(() => ((data.value.names && data.value.names.买入) || []).filter(ownRow));
const watchNames = computed(() => ((data.value.names && data.value.names.观察) || []).filter(ownRow));
const visible = computed(() => {
  const query = q.value.trim();
  return (data.value.rows || []).filter((r) => {
    if (!ownRow(r)) return false;
    if (filter.value === "在池" && r.status !== "观察" && r.status !== "买入") return false;
    if (filter.value !== "全部" && filter.value !== "在池" && r.status !== filter.value) return false;
    if (!query) return true;
    const ind = industryOf(r) || "";
    return (r.code && r.code.includes(query)) || (r.name && r.name.includes(query)) || ind.includes(query);
  });
});
const groups = computed(() => {
  const rows = visible.value;
  if (!isPullback.value) return [{ key: "_all", title: "", rows }];
  const map = new Map();
  for (const r of rows) {
    const k = industryOf(r) || "未分板块";
    if (!map.has(k)) map.set(k, []);
    map.get(k).push(r);
  }
  const boardMap = Object.fromEntries(boards.value.map((b) => [b.name, b]));
  const keys = [...map.keys()].sort((a, b) => {
    const ra = boardMap[a] && boardMap[a].ret_3d != null ? boardMap[a].ret_3d : -999;
    const rb = boardMap[b] && boardMap[b].ret_3d != null ? boardMap[b].ret_3d : -999;
    return rb - ra;
  });
  return keys.map((k) => {
    const b = boardMap[k];
    let title = k;
    if (b && b.ret_3d != null) {
      const vs = b.vs_market == null ? "" : `相对沪深300 ${signed(b.vs_market)}`;
      title = `${k} · 近3日 ${signed(b.ret_3d)} · ${vs}`;
    }
    return { key: k, title, rows: map.get(k) };
  });
});
const emptyText = computed(() => {
  if (loading.value) return "正在按当前规则扫描…";
  if (currentRuleset.value && !currentRuleset.value.engine_ok) {
    return currentRuleset.value.engine_note || "本规则尚未写成扫描器。";
  }
  return "这一闸没有股票。";
});
function industryOf(row) {
  return (row && (row.industry || (row.facts && row.facts.industry))) || "";
}
function stockTitle(row) {
  const code = (row && row.code) || "";
  const name = String((row && row.name) || "").trim();
  if (!name || name === code) return code;
  return `${name}  ${code}`;
}
function fmt(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(4);
}
function money(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2);
}
function pe(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(1);
}
function pct(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : `${Number(v).toFixed(2)}%`;
}
function signed(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}
function chartLink(code, pool) {
  const query = { ruleset: rulesetId.value };
  if (pool) query.pool = pool;
  return { path: "/chart/" + code, query };
}
function backtestLink(code, pool) {
  const query = { ruleset: rulesetId.value, backtest: "1" };
  if (pool) query.pool = pool;
  return { path: "/chart/" + code, query };
}
async function runPoolBacktest(gate) {
  const want = rulesetId.value;
  showLoading("正在按当前规则回放买入到卖出…");
  setLoadingText("正在按当前规则回放买入到卖出…");
  const payload = await api.cycles(want, { gate }).catch(() => null);
  if (rulesetId.value !== want) return;
  const rid = payload && payload.ruleset && payload.ruleset.id;
  if (rid && rid !== want) return;
  poolBacktest.value = {
    gate,
    segments: ((payload && payload.segments) || []).filter((s) => !s.ruleset || s.ruleset === want),
    note: (payload && payload.note) || "一段轨迹 = 路径到达买入的确认收盘 → 卖出条件日。买入不是成交指令。",
  };
}
function blankFor(id) {
  const list = extraRulesets.value.length ? extraRulesets.value : data.value.rulesets || [];
  return {
    rows: [],
    summary: {},
    by_gate: {},
    names: {},
    pool: {},
    rulesets: list,
    boards: [],
    market: null,
    reminders: [],
    ruleset: list.find((r) => r.id === id) || null,
  };
}
function applyCache(id) {
  const hit = cache[id];
  if (hit) {
    data.value = hit;
    return;
  }
  data.value = blankFor(id);
}
function payloadOf(id, payload) {
  const rid = payload && payload.ruleset && payload.ruleset.id;
  return payload && (!rid || rid === id);
}
function switchRuleset(id) {
  filter.value = "观察";
  poolBacktest.value = null;
  showLoading(id === "rules2" ? "正在切换到 RULES2…" : "正在切换规则…");
  applyCache(id);
  router.replace({ path: "/scan", query: { ruleset: id } });
}
async function load() {
  const want = rulesetId.value;
  loading.value = true;
  setLoadingText(want === "rules2" ? "正在按 RULES2 先筛板块再扫个股…" : "正在按当前规则扫描…");
  try {
    const payload = await api.scan(want);
    if (!payloadOf(want, payload)) return;
    cache[want] = payload;
    if (payload.rulesets && payload.rulesets.length) extraRulesets.value = payload.rulesets;
    if (rulesetId.value !== want) return;
    data.value = payload;
  } finally {
    if (rulesetId.value === want) loading.value = false;
  }
}
watch(rulesetId, load);
onMounted(() => {
  api.rulesets().then((rs) => {
    extraRulesets.value = rs.items || [];
  }).catch(() => {});
  load();
});
</script>
