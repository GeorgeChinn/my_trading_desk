<template>
  <div>
    <div class="scan-head">
      <div class="scan-head-left">
        <h1>规则扫描</h1>
        <p class="sub">
          {{ data.position_block || "总闸：排除 → 观察 → 买入 → 卖出。买入 = 路径到达，不是成交指令。" }}
          观察/买入/试仓名单可以做规则回测。
        </p>
        <p class="stamp" v-if="stamp.asof || stamp.scanned_at">
          总股池 {{ universeN }} 只
          · 数据日 {{ stamp.asof || "—" }}
          · 扫描 {{ stamp.scanned_at || "—" }}
        </p>
      </div>
      <div class="card buy-log-card">
        <div class="ov-title">
          买入池记录
          <span>进行中 {{ buyLog.open || 0 }} · 已结束 {{ buyLog.closed || 0 }}</span>
        </div>
        <p class="sub" style="margin:0 0 8px">
          本次更新 {{ buyLog.updated_at || stamp.scanned_at || "—" }}
          · 记录从昨天（{{ buyLog.started_at || "—" }}）起
        </p>
        <p class="sub" style="margin:0 0 8px">列入日期 = 扫描列入买入/试仓池的那天。只有列入过的票才能做规则回测。</p>
        <div v-if="!(buyLog.items || []).length" class="empty mini">还没有记录。扫描出现买入后会写在这里。</div>
        <div v-else class="table-wrap buy-log-table">
          <table class="table">
            <thead>
              <tr>
                <th>股票</th>
                <th>列入</th>
                <th>买入价</th>
                <th>卖出</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ep in buyLog.items" :key="ep.id">
                <td>
                  <router-link class="name-chip 买入" :to="chartLink(ep.code, ep.closed ? '' : '买入')">
                    {{ stockTitle(ep) }}
                  </router-link>
                </td>
                <td>{{ ep.buy_date || "—" }}<div class="sub" style="margin:2px 0 0">{{ money(ep.buy_price) }}</div></td>
                <td>{{ money(ep.closed ? ep.sell_price : ep.mark_price) }}{{ ep.closed ? "" : "（最新）" }}</td>
                <td>{{ ep.sell_date || "进行中" }}</td>
                <td>
                  <span class="num" :class="pnlClass(ep.pnl_pct)">{{ signedPct(ep.pnl_pct) }}</span>
                  <div class="sub" style="margin:2px 0 0">{{ ep.result || "—" }}</div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
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
      · 总股池 {{ universeN }} 只 · 排除 {{ (data.by_gate && data.by_gate.排除) || 0 }} · 剩下 {{ data.remain || 0 }}
      · {{ data.pool.source }} {{ data.pool.trade_date }}
    </p>
    <div class="warn-banner" v-for="(r, i) in (data.reminders || [])" :key="'rm'+i">{{ r }}</div>

    <div class="grid cols-4" style="margin-bottom:14px">
      <div class="card stat">
        <div class="n">{{ universeN }}</div>
        <div class="k">总股池</div>
      </div>
      <div class="card stat">
        <div class="n">{{ (data.by_gate && data.by_gate.排除) || 0 }}</div>
        <div class="k">排除</div>
      </div>
      <div class="card stat">
        <div class="n">{{ data.remain || 0 }}</div>
        <div class="k">剩下</div>
      </div>
      <div class="card stat" v-for="k in remainGates" :key="k">
        <div class="n">{{ (data.by_gate && data.by_gate[k]) || 0 }}</div>
        <div class="k">{{ k }}</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:14px" v-if="isPullback && data.mainline">
      <div class="ov-title">
        主线
        <span>过关 {{ passedBoards.length }} / {{ boards.length }} · 沪深300 近3日 {{ pct(marketRet) }}</span>
      </div>
      <p class="sub" style="margin:0 0 10px">主线：先强段内申万二级涨幅 ≥ 沪深300 同期 + 3pct，且该板块累计涨停 ≥ 6；买入日近 3 日 ≥ 沪深300 且至少 1 只涨停。禁止用其他票涨停代替。</p>
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
          <div>{{ isPullback ? "试仓池" : "买入池" }} <span>{{ buyNames.length }}</span></div>
          <button class="btn" :disabled="!buyNames.length" @click="runPoolBacktest(isPullback ? '试仓' : '买入')">规则回测</button>
        </div>
        <p class="sub" style="margin:0 0 8px">{{ isPullback ? "试仓" : "买入" }} = 路径到达，不是成交指令。回测按当前名单回放，不写成交指令。</p>
        <div class="name-cloud" v-if="buyNames.length">
          <router-link class="name-chip 买入" v-for="s in buyNames" :key="'b'+s.code" :to="chartLink(s.code, isPullback ? '试仓' : '买入')">
            {{ stockTitle(s) }}
            <em v-if="s.pe != null">PE {{ pe(s.pe) }}</em>
          </router-link>
        </div>
        <div class="empty mini" v-else>空</div>
      </div>
      <div class="ov-block">
        <div class="ov-title">
          <div>观察池 <span>{{ watchNames.length }}</span></div>
          <button class="btn" :disabled="!watchNames.length" @click="runPoolBacktest('观察')">规则回测</button>
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
      <button class="btn" :class="{ primary: filter === '在池' }" @click="filter = '在池'">剩下</button>
      <button class="btn" :class="{ primary: filter === '全部' }" @click="filter = '全部'">全部</button>
    </div>
    <label class="field" style="margin-bottom:14px;max-width:320px">
      <span>搜索代码 / 名称 / 板块</span>
      <input v-model="q" placeholder="600519 或 茅台 或 有色" />
    </label>
    <p class="sub">
      当前列出 {{ visible.length }} / {{ (data.rows || []).length }}
      <span v-if="pageCount > 1"> · 第 {{ page }} / {{ pageCount }} 页</span>
    </p>
    <div class="row-btns" v-if="pageCount > 1" style="margin-bottom:12px">
      <button class="btn" :disabled="page <= 1" @click="page--">上一页</button>
      <button class="btn" :disabled="page >= pageCount" @click="page++">下一页</button>
    </div>
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
          {{ row.facts && row.facts.date }} 最新 {{ money(row.facts && row.facts.close) }}
          <span v-if="row.facts && row.facts.dif != null"> · DIF {{ fmt(row.facts.dif) }}</span>
          <span v-if="row.key_kind"> · {{ row.key_kind }} 关键位 {{ money(row.key_price) }} 止损 {{ money(row.stop_price) }}</span>
        </p>
        <div class="row-btns" style="margin-top:10px">
          <router-link class="btn" :to="chartLink(row.code, row.status === '买入' || row.status === '观察' ? row.status : '')">查看日线与事实</router-link>
          <router-link
            class="btn"
            v-if="['买入', '观察', '试仓', '持有'].includes(row.status)"
            :to="backtestLink(row.code, row.status)"
          >规则回测</router-link>
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
const page = ref(1);
const pageSize = 80;
const loading = ref(true);
const poolBacktest = ref(null);
const gates = computed(() => (data.value.gates && data.value.gates.length ? data.value.gates : GATES));
const remainGates = computed(() => gates.value.filter((g) => g !== "排除"));
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
const buyNames = computed(() => {
  const names = data.value.names || {};
  const out = [];
  for (const k of ["买入", "试仓", "持有"]) {
    for (const s of names[k] || []) {
      if (ownRow(s)) out.push(s);
    }
  }
  return out;
});
const watchNames = computed(() => ((data.value.names && data.value.names.观察) || []).filter(ownRow));
const buyLog = computed(() => data.value.buy_log || { items: [], open: 0, closed: 0 });
const stamp = computed(() => data.value.stamp || {});
const universeN = computed(() => {
  const s = data.value.stamp || {};
  const p = data.value.pool || {};
  const listed = Number((p.funnel && p.funnel.listed) || 0);
  const csv = Number(s.csv_n || p.universe_count || p.total || 0);
  if (csv > 0) return csv;
  if (listed > 0) return listed;
  const rows = (data.value.rows || []).length;
  if (rows > listed && rows > 1000) return rows;
  return Number(s.pool_n || p.count || rows || 0);
});
function signedPct(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}
function pnlClass(v) {
  const n = Number(v);
  if (v == null || Number.isNaN(n) || n === 0) return "zero";
  return n > 0 ? "pos" : "neg";
}
const visible = computed(() => {
  const query = q.value.trim();
  return (data.value.rows || []).filter((r) => {
    if (!ownRow(r)) return false;
    if (filter.value === "在池" && r.status === "排除") return false;
    if (filter.value !== "全部" && filter.value !== "在池" && r.status !== filter.value) return false;
    if (!query) return true;
    const ind = industryOf(r) || "";
    return (r.code && r.code.includes(query)) || (r.name && r.name.includes(query)) || ind.includes(query);
  });
});
const pageCount = computed(() => Math.max(1, Math.ceil(visible.value.length / pageSize)));
const pageRows = computed(() => {
  const start = (page.value - 1) * pageSize;
  return visible.value.slice(start, start + pageSize);
});
watch([filter, q, rulesetId], () => {
  page.value = 1;
});
const groups = computed(() => {
  const rows = pageRows.value;
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
  const total = (data.value.rows || []).length;
  const g = data.value.by_gate || {};
  if (filter.value === "观察" && total && !(g.观察)) {
    return `观察闸 0 只。底池 ${total} 只（排除 ${g.排除 || 0}）。点「全部」或「排除」看未过池原因。`;
  }
  if (filter.value === "买入" && total && !(g.买入)) {
    return `买入闸 0 只。底池 ${total} 只。点「观察」或「全部」看未齐条件的票。`;
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
    note: (payload && payload.note) || "一段回测 = 路径到达买入的最新更新 → 卖出条件日。买入不是成交指令。",
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
    buy_log: { items: [], open: 0, closed: 0 },
    stamp: {},
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
