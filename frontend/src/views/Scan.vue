<template>
  <div>
    <h1>规则扫描</h1>
    <p class="sub">
      {{ (data.pool && data.pool.note) || "按 RULES 漏斗与总闸分类。" }}
      阈值空缺不得升到试仓 / 标准仓。
    </p>
    <div class="warn-banner">
      {{ data.position_block }}
      <span v-if="data.pool"> · 池子 {{ data.pool.count }} 只 · 优先样本 {{ data.pool.preferred ?? "—" }} · {{ data.pool.source }} {{ data.pool.trade_date }}</span>
    </div>
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
      <button class="btn" :class="{ primary: onlyPreferred }" @click="onlyPreferred = !onlyPreferred">只看优先样本</button>
    </div>
    <label class="field" style="margin-bottom:14px;max-width:320px">
      <span>搜索代码 / 名称</span>
      <input v-model="q" placeholder="600519 或 茅台" />
    </label>
    <p class="sub">当前列出 {{ visible.length }} / {{ (data.rows || []).length }}</p>
    <div v-if="!visible.length" class="empty">{{ loading ? "正在按 RULES 扫描…" : "这一闸没有股票。若池子仍是示例，去「数据与设置」拉真实行情。" }}</div>
    <div v-for="row in visible" :key="row.code" class="card" style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;gap:12px">
        <div>
          <b>{{ row.code }} {{ row.name }}</b>
          <span class="badge 观察" v-if="row.index_member && row.index_member.length" style="margin-left:8px">{{ row.index_member.join(" / ") }}</span>
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

const data = ref({ rows: [], summary: {}, by_gate: {}, pool: {} });
const filter = ref("全部");
const onlyPreferred = ref(false);
const q = ref("");
const loading = ref(true);
const gates = GATES;
const visible = computed(() => {
  const query = q.value.trim();
  return (data.value.rows || []).filter((r) => {
    if (filter.value !== "全部" && r.status !== filter.value) return false;
    if (onlyPreferred.value && !(r.index_member && r.index_member.length)) return false;
    if (!query) return true;
    return (r.code && r.code.includes(query)) || (r.name && r.name.includes(query));
  });
});
function fmt(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(4);
}
onMounted(async () => {
  try {
    data.value = await api.scan();
  } finally {
    loading.value = false;
  }
});
</script>
