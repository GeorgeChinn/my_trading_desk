<template>
  <div>
    <h1>规则轨迹</h1>
    <p class="sub">
      一段轨迹 = 路径到达买入条件的确认收盘 → §7.1 失败离场或 §7.2 波段离场。同一只票可以有多段。
      买入价 / 卖出价用当日收盘。{{ data.fact_note }}。买入不是成交指令。
    </p>
    <div class="tabs">
      <button
        class="btn"
        v-for="rs in rulesets"
        :key="rs.id"
        :class="{ primary: rulesetId === rs.id }"
        @click="switchRuleset(rs.id)"
      >
        {{ rs.title }}
      </button>
    </div>
    <div class="warn-banner" v-if="currentRuleset">
      当前规则：{{ currentRuleset.file }} · {{ currentRuleset.title }}
    </div>
    <div class="warn-banner">{{ data.note || "回放确认收盘，不编造 K 线。" }}</div>

    <div class="grid cols-4" style="margin-bottom:16px">
      <div class="card stat">
        <div class="n">{{ (summary.open) || 0 }}</div>
        <div class="k">进行中</div>
      </div>
      <div class="card stat">
        <div class="n">{{ (summary.closed) || 0 }}</div>
        <div class="k">已结束</div>
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

    <div class="row-btns" style="margin-bottom:14px">
      <button class="btn" :class="{ primary: tab === 'all' }" @click="tab = 'all'">全部 {{ segments.length }}</button>
      <button class="btn" :class="{ primary: tab === 'open' }" @click="tab = 'open'">进行中 {{ openN }}</button>
      <button class="btn" :class="{ primary: tab === 'done' }" @click="tab = 'done'">已结束 {{ closedN }}</button>
    </div>
    <label class="field" style="margin-bottom:14px;max-width:320px">
      <span>搜索代码 / 名称</span>
      <input v-model="q" placeholder="600519 或 茅台" />
    </label>

    <div v-if="loading" class="empty">正在按当前规则回放确认收盘…</div>
    <div v-else-if="!visible.length" class="empty">{{ emptyText }}</div>
    <div v-else class="card table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>代码</th>
            <th>第几段</th>
            <th>列入日期</th>
            <th>买入价</th>
            <th>卖出日期</th>
            <th>卖出价</th>
            <th>盈亏</th>
            <th>每股</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ep in visible" :key="ep.id">
            <td>{{ ep.code }} {{ ep.name }}</td>
            <td>第 {{ ep.seq }} 段</td>
            <td>{{ ep.buy_date || "—" }}</td>
            <td>{{ money(ep.buy_price) }}</td>
            <td>{{ ep.sell_date || "—" }}</td>
            <td>{{ ep.closed ? money(ep.sell_price) : money(ep.mark_price) + "（最新）" }}</td>
            <td>
              <span class="num" :class="pnlClass(ep.pnl_pct)">{{ signedPct(ep.pnl_pct) }}</span>
              <div class="sub" style="margin:4px 0 0">{{ ep.result }}</div>
              <div class="sub" style="margin:4px 0 0" v-if="ep.exit_detail">{{ ep.exit_detail }}</div>
            </td>
            <td>
              <span class="num" :class="pnlClass(ep.pnl_per_share)">{{ signedMoney(ep.pnl_per_share) }}</span>
            </td>
            <td>
              <span class="badge" :class="ep.closed ? (ep.win ? '买入' : '清仓') : '等待'">{{ ep.status }}</span>
            </td>
            <td><router-link class="btn" :to="'/chart/' + ep.code">日线</router-link></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";

const route = useRoute();
const router = useRouter();
const data = ref({ segments: [], summary: {}, rulesets: [] });
const tab = ref("all");
const q = ref("");
const loading = ref(true);
const rulesetId = computed(() => String(route.query.ruleset || "rules"));
const rulesets = computed(() => data.value.rulesets || []);
const currentRuleset = computed(() => data.value.ruleset || rulesets.value.find((r) => r.id === rulesetId.value) || null);
const summary = computed(() => data.value.summary || {});
const segments = computed(() => data.value.segments || []);
const openN = computed(() => segments.value.filter((s) => !s.closed).length);
const closedN = computed(() => segments.value.filter((s) => s.closed).length);
const visible = computed(() => {
  const query = q.value.trim();
  return segments.value.filter((s) => {
    if (tab.value === "open" && s.closed) return false;
    if (tab.value === "done" && !s.closed) return false;
    if (!query) return true;
    return (s.code && s.code.includes(query)) || (s.name && s.name.includes(query));
  });
});
const emptyText = computed(() => {
  if (currentRuleset.value && !currentRuleset.value.engine_ok) {
    return currentRuleset.value.engine_note || "本规则尚未写成扫描器，没有轨迹。";
  }
  return "还没有列入买入池后的完整轨迹。";
});

function money(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2);
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
function switchRuleset(id) {
  router.replace({ path: "/cycles", query: { ruleset: id } });
}
async function load() {
  loading.value = true;
  try {
    data.value = await api.cycles(rulesetId.value);
  } finally {
    loading.value = false;
  }
}
watch(rulesetId, load);
onMounted(load);
</script>
