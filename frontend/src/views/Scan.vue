<template>
  <div>
    <h1>规则扫描</h1>
    <p class="sub">按 RULES 漏斗与总闸分类。每只写命中哪条、还缺哪条。阈值空缺不得升到试仓 / 标准仓。</p>
    <div class="warn-banner">{{ data.position_block }}</div>
    <div class="grid cols-4" style="margin-bottom:16px">
      <div class="card stat" v-for="k in ['符合','继续跟踪','观察','排除']" :key="k">
        <div class="n">{{ (data.summary && data.summary[k]) || 0 }}</div>
        <div class="k">{{ k }}</div>
      </div>
    </div>
    <div class="row-btns" style="margin-bottom:14px">
      <button class="btn" v-for="g in gates" :key="g" :class="{ primary: filter === g }" @click="filter = g">
        {{ g }} {{ (data.by_gate && data.by_gate[g]) || 0 }}
      </button>
      <button class="btn" :class="{ primary: filter === '全部' }" @click="filter = '全部'">全部</button>
    </div>
    <div v-if="!visible.length" class="empty">这一闸没有股票。</div>
    <div v-for="row in visible" :key="row.code" class="card" style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;gap:12px">
        <div>
          <b>{{ row.code }} {{ row.name }}</b>
          <span class="sub"> · 路径 {{ row.path }} · 人{{ row.person_present ? "在场" : "不在场" }} · 大盘 {{ row.market_regime }}</span>
        </div>
        <StatusBadge :status="row.status" />
      </div>
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
      <p class="sub" style="margin:10px 0 0">{{ row.fact_note }} · 确认收盘 {{ row.facts && row.facts.date }} 收盘 {{ fmt(row.facts && row.facts.close) }} DIF {{ fmt(row.facts && row.facts.dif) }}</p>
      <div class="row-btns" style="margin-top:10px">
        <router-link class="btn" :to="'/chart/' + row.code">查看日线与事实</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { api, GATES } from "../api";
import StatusBadge from "../components/StatusBadge.vue";

const data = ref({ rows: [], summary: {}, by_gate: {} });
const filter = ref("全部");
const gates = GATES;
const visible = computed(() => {
  const rows = data.value.rows || [];
  if (filter.value === "全部") return rows;
  return rows.filter((r) => r.status === filter.value);
});
function fmt(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(4);
}
onMounted(async () => {
  data.value = await api.scan();
});
</script>
