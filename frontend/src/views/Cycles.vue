<template>
  <div>
    <h1>规则轨迹</h1>
    <p class="sub">
      启动 = 确认收盘首次列入买入。结束 = RULES 第 7 条减仓/清仓条件。
      {{ data.fact_note }}。买入不是成交指令。
    </p>
    <div class="warn-banner">{{ (data.live && data.live.note) || "回放确认收盘，不编造 K 线。" }}</div>

    <div class="row-btns" style="margin-bottom:14px">
      <button class="btn" :class="{ primary: tab === 'live' }" @click="tab = 'live'">进行中 {{ openList.length }}</button>
      <button class="btn" :class="{ primary: tab === 'done' }" @click="tab = 'done'">已结束 {{ closedList.length }}</button>
      <button class="btn" :class="{ primary: tab === 'rank' }" @click="tab = 'rank'">买入池回测排名</button>
    </div>

    <div v-if="loading" class="empty">正在按 RULES 回放确认收盘…</div>

    <template v-if="tab === 'live'">
      <div v-if="!openList.length && !loading" class="empty">当前没有进行中的规则轨迹。有股票进入买入后会出现。</div>
      <div v-for="ep in openList" :key="ep.code" class="card" style="margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
          <div>
            <b>{{ ep.code }} {{ ep.name }}</b>
            <span class="badge 买入" v-if="ep.new_yesterday" style="margin-left:8px">前一日新列入</span>
            <div class="sub" style="margin:6px 0 0">{{ ep.start_date }} 收盘 {{ ep.start_close }} → 最新 {{ ep.last_close }} · 浮动 {{ ep.open_return_pct }}% · 回撤 {{ ep.max_drawdown_pct }}%</div>
          </div>
          <router-link class="btn" :to="'/chart/' + ep.code">日线与事实</router-link>
        </div>
        <SparkLine :path="ep.path || []" />
      </div>
    </template>

    <template v-if="tab === 'done'">
      <div v-if="!closedList.length && !loading" class="empty">还没有走完第 7 条的记录。</div>
      <div v-for="(ep, i) in closedList" :key="ep.code + ep.start_date + i" class="card" style="margin-bottom:12px">
        <div>
          <b>{{ ep.code }} {{ ep.name }}</b>
          <span class="badge" :class="ep.win ? '买入' : '清仓'" style="margin-left:8px">{{ ep.win ? "收盘为正" : "收盘为负" }}</span>
          <div class="sub" style="margin:6px 0 0">{{ ep.start_date }} {{ ep.start_close }} → {{ ep.end_date }} {{ ep.end_close }} · 收益 {{ ep.return_pct }}% · 最大回撤 {{ ep.max_drawdown_pct }}% · {{ ep.bars }} 根</div>
        </div>
        <SparkLine :path="ep.path || []" />
      </div>
    </template>

    <template v-if="tab === 'rank'">
      <p class="sub">对当前买入池每只股票，用本地确认日线回放 RULES 进出场。样本少时胜率不稳定。排名：胜率 → 平均收益 → 回撤。</p>
      <table class="table">
        <thead>
          <tr>
            <th>排名</th>
            <th>代码</th>
            <th>完整段数</th>
            <th>胜率</th>
            <th>平均收益</th>
            <th>平均回撤</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in ranking" :key="row.code">
            <td>{{ row.rank }}</td>
            <td>{{ row.code }} {{ row.name }}</td>
            <td>{{ row.samples }}</td>
            <td>{{ row.win_rate == null ? "证据不足" : row.win_rate + "%" }}</td>
            <td>{{ row.avg_return_pct == null ? "—" : row.avg_return_pct + "%" }}</td>
            <td>{{ row.avg_drawdown_pct == null ? "—" : row.avg_drawdown_pct + "%" }}</td>
            <td><router-link class="btn" :to="'/chart/' + row.code">日线</router-link></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!ranking.length && !loading" class="empty">当前买入池为空。</p>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { api } from "../api";
import SparkLine from "../components/SparkLine.vue";

const data = ref({ live: { open: [], closed: [] }, ranking: [] });
const tab = ref("live");
const loading = ref(true);
const openList = computed(() => (data.value.live && data.value.live.open) || []);
const closedList = computed(() => (data.value.live && data.value.live.closed) || []);
const ranking = computed(() => data.value.ranking || []);

onMounted(async () => {
  try {
    data.value = await api.cycles();
  } finally {
    loading.value = false;
  }
});
</script>
