<template>
  <div>
    <h1>规则轨迹</h1>
    <p class="sub">
      一段 = 买入条件日 → 卖出条件日。同一只票可多段。价格用确认收盘。买入不是成交指令。
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
    <p class="sub" v-if="currentRuleset">{{ currentRuleset.file }} · {{ currentRuleset.title }}</p>
    <div class="warn-banner">{{ data.note || "收盘未变则读缓存。" }}</div>
    <div class="warn-banner" v-if="data.warming">
      RULES2 轨迹首次回放中 {{ data.warm_done || 0 }}/{{ data.warm_total || "…" }}，请稍候，页面会自动刷新。
    </div>

    <div class="grid cols-4" style="margin-bottom:16px">
      <div class="card stat">
        <div class="n">{{ (summary.open) || 0 }}</div>
        <div class="k">进行中</div>
      </div>
      <div class="card stat">
        <div class="n">{{ (summary.closed) || 0 }}</div>
        <div class="k">已卖出</div>
      </div>
      <div class="card stat">
        <div class="n">{{ summary.win_rate == null ? "—" : summary.win_rate + "%" }}</div>
        <div class="k">已结束胜率</div>
      </div>
      <div class="card stat">
        <div class="n">{{ summary.avg_pnl_pct == null ? "—" : summary.avg_pnl_pct + "%" }}</div>
        <div class="k">已结束平均盈亏</div>
      </div>
    </div>

    <div class="row-btns" style="margin-bottom:12px">
      <button class="btn" :class="{ primary: tab === 'all' }" @click="setTab('all')">全部 {{ summary.total || 0 }}</button>
      <button class="btn" :class="{ primary: tab === 'open' }" @click="setTab('open')">进行中 {{ summary.open || 0 }}</button>
      <button class="btn" :class="{ primary: tab === 'done' }" @click="setTab('done')">已卖出 {{ summary.closed || 0 }}</button>
    </div>
    <div class="row-btns" style="margin-bottom:14px;align-items:end">
      <label class="field" style="max-width:220px;margin:0">
        <span>搜索</span>
        <input v-model="q" placeholder="代码 / 名称" @keyup.enter="page = 1; load()" />
      </label>
      <label class="field" style="max-width:160px;margin:0">
        <span>排序</span>
        <select v-model="sort" @change="page = 1; load()">
          <option value="default">进行中优先</option>
          <option value="buy_date">列入日期</option>
          <option value="sell_date">卖出日期</option>
          <option value="pnl_pct">盈亏%</option>
          <option value="code">代码</option>
        </select>
      </label>
      <label class="field" style="max-width:120px;margin:0">
        <span>方向</span>
        <select v-model="order" @change="page = 1; load()">
          <option value="desc">降序</option>
          <option value="asc">升序</option>
        </select>
      </label>
      <button class="btn" @click="page = 1; load()">筛选</button>
    </div>

    <div v-if="!segments.length" class="empty">{{ emptyText }}</div>
    <div v-else class="card table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>代码</th>
            <th>段</th>
            <th>列入日期</th>
            <th>买入价</th>
            <th>卖出日期</th>
            <th>卖出价</th>
            <th>PE</th>
            <th>盈亏</th>
            <th>每股</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ep in segments" :key="ep.id">
            <td>{{ stockTitle(ep) }}</td>
            <td>第 {{ ep.seq }} 段</td>
            <td>{{ ep.buy_date || "—" }}</td>
            <td>{{ money(ep.buy_price) }}</td>
            <td>{{ ep.sell_date || "—" }}</td>
            <td>{{ ep.closed ? money(ep.sell_price) : money(ep.mark_price) + "（最新）" }}</td>
            <td>{{ pe(ep.pe) }}</td>
            <td>
              <span class="num" :class="pnlClass(ep.pnl_pct)">{{ signedPct(ep.pnl_pct) }}</span>
              <div class="sub" style="margin:4px 0 0">{{ ep.result }}</div>
            </td>
            <td>
              <span class="num" :class="pnlClass(ep.pnl_per_share)">{{ signedMoney(ep.pnl_per_share) }}</span>
            </td>
            <td>
              <span class="badge" :class="ep.closed ? '卖出' : '买入'">{{ ep.closed ? "卖出" : "进行中" }}</span>
            </td>
            <td><router-link class="btn" :to="{ path: '/chart/' + ep.code, query: { ruleset: rulesetId } }">日线</router-link></td>
          </tr>
        </tbody>
      </table>
      <div class="row-btns" style="margin-top:12px;justify-content:flex-end">
        <button class="btn" :disabled="page <= 1" @click="page--; load()">上一页</button>
        <span class="sub" style="margin:0;align-self:center">{{ page }} / {{ pages }}</span>
        <button class="btn" :disabled="page >= pages" @click="page++; load()">下一页</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import { setLoadingText, showLoading } from "../loading.js";

const route = useRoute();
const router = useRouter();
const data = ref({ segments: [], summary: {}, rulesets: [], pages: 1 });
const extraRulesets = ref([]);
const cache = {};
const q = ref("");
const sort = ref("default");
const order = ref("desc");
const page = ref(1);
const loading = ref(true);
const rulesetId = computed(() => String(route.query.ruleset || "rules"));
const tab = computed(() => {
  const t = String(route.query.tab || "all");
  return t === "open" || t === "done" ? t : "all";
});
const rulesets = computed(() => (data.value.rulesets && data.value.rulesets.length ? data.value.rulesets : extraRulesets.value));
const currentRuleset = computed(() => data.value.ruleset || rulesets.value.find((r) => r.id === rulesetId.value) || null);
const summary = computed(() => data.value.summary || {});
const segments = computed(() => data.value.segments || []);
const pages = computed(() => data.value.pages || 1);
const emptyText = computed(() => {
  if (data.value.warming) return "RULES2 轨迹首次回放中，完成后自动出现。";
  if (currentRuleset.value && !currentRuleset.value.engine_ok) {
    return currentRuleset.value.engine_note || "本规则尚未写成扫描器，没有轨迹。";
  }
  return "还没有买入到卖出的轨迹。";
});

function money(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2);
}
function pe(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(1);
}
function signedPct(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}
function signedMoney(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}`;
}
function pnlClass(v) {
  const n = Number(v);
  if (v == null || Number.isNaN(n) || n === 0) return "zero";
  return n > 0 ? "pos" : "neg";
}
function applyCache(id) {
  const hit = cache[id];
  if (hit) {
    data.value = hit;
    return;
  }
  data.value = {
    segments: [],
    summary: {},
    rulesets: extraRulesets.value.length ? extraRulesets.value : data.value.rulesets,
    pages: 1,
    warming: false,
    note: "",
  };
}
function switchRuleset(id) {
  page.value = 1;
  showLoading(id === "rules2" ? "正在切换到 RULES2 轨迹…" : "正在切换规则轨迹…");
  applyCache(id);
  router.replace({ path: "/cycles", query: { ruleset: id, tab: "all" } });
}
function setTab(t) {
  page.value = 1;
  router.replace({ path: "/cycles", query: { ruleset: rulesetId.value, tab: t } });
}
function stockTitle(row) {
  const code = (row && row.code) || "";
  const name = String((row && row.name) || "").trim();
  if (!name || name === code) return code;
  return `${name}  ${code}`;
}
let pollTimer = 0;
function clearPoll() {
  if (pollTimer) {
    window.clearTimeout(pollTimer);
    pollTimer = 0;
  }
}
async function load(silent = false) {
  loading.value = !silent;
  if (!silent) {
    setLoadingText(rulesetId.value === "rules2" ? "正在读取 RULES2 轨迹…" : "正在读取规则轨迹…");
  }
  try {
    const payload = await api.cycles(
      rulesetId.value,
      {
        tab: tab.value,
        q: q.value,
        sort: sort.value,
        order: order.value,
        page: String(page.value),
        page_size: "40",
      },
      silent
    );
    data.value = payload;
    cache[rulesetId.value] = payload;
    if (payload.rulesets && payload.rulesets.length) extraRulesets.value = payload.rulesets;
    clearPoll();
    if (payload && payload.warming) {
      pollTimer = window.setTimeout(() => load(true), 2000);
    }
  } finally {
    loading.value = false;
  }
}
watch(
  [rulesetId, tab],
  () => {
    page.value = 1;
    clearPoll();
    load(false);
  },
  { immediate: true }
);
onMounted(() => {
  api.rulesets().then((rs) => {
    extraRulesets.value = rs.items || [];
  }).catch(() => {});
});
onUnmounted(clearPoll);
</script>
