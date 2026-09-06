<template>
  <div>
    <p class="sub" v-if="note">{{ note }}</p>
    <div v-if="!rows.length" class="empty">{{ emptyText }}</div>
    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th v-if="showCode">代码</th>
            <th>段</th>
            <th>列入日期</th>
            <th>买入价</th>
            <th>卖出日期</th>
            <th>卖出价</th>
            <th>盈亏</th>
            <th>每股</th>
            <th>状态</th>
            <th v-if="linkChart"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ep in rows" :key="ep.id">
            <td v-if="showCode">{{ stockTitle(ep) }}</td>
            <td>第 {{ ep.seq }} 段</td>
            <td>{{ ep.buy_date || "—" }}</td>
            <td>{{ money(ep.buy_price) }}</td>
            <td>{{ ep.sell_date || "—" }}</td>
            <td>{{ ep.closed ? money(ep.sell_price) : money(ep.mark_price) + "（最新）" }}</td>
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
            <td v-if="linkChart">
              <router-link class="btn" :to="chartLink(ep)">日线</router-link>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  rows: { type: Array, default: () => [] },
  note: { type: String, default: "" },
  emptyText: { type: String, default: "历史上没有走完买入到卖出的轨迹。" },
  showCode: { type: Boolean, default: false },
  linkChart: { type: Boolean, default: false },
  rulesetId: { type: String, default: "rules" },
  pool: { type: String, default: "" },
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
function stockTitle(row) {
  const code = (row && row.code) || "";
  const name = String((row && row.name) || "").trim();
  if (!name || name === code) return code;
  return `${name}  ${code}`;
}
function chartLink(ep) {
  const query = { ruleset: props.rulesetId, backtest: "1" };
  if (props.pool) query.pool = props.pool;
  return { path: "/chart/" + ep.code, query };
}
</script>
