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

    <div class="card overview" style="margin-bottom:14px">
      <div class="ov-block">
        <div class="ov-title">买入池 <span>{{ buyNames.length }}</span></div>
        <p class="sub" style="margin:0 0 8px">路径到达，不是成交指令</p>
        <div class="name-cloud" v-if="buyNames.length">
          <router-link class="name-chip 买入" v-for="s in buyNames" :key="'b'+s.code" :to="chartLink(s.code)">
            {{ s.name }}
            <em v-if="s.pe != null">PE {{ pe(s.pe) }}</em>
          </router-link>
        </div>
        <div class="empty mini" v-else>空</div>
      </div>
      <div class="ov-block">
        <div class="ov-title">观察池 <span>{{ watchNames.length }}</span></div>
        <div class="name-cloud" v-if="watchNames.length">
          <router-link class="name-chip 观察" v-for="s in watchNames" :key="'w'+s.code" :to="chartLink(s.code)">
            {{ s.name }}
            <em v-if="s.pe != null">PE {{ pe(s.pe) }}</em>
          </router-link>
        </div>
        <div class="empty mini" v-else>空</div>
      </div>
    </div>

    <div class="row-btns" style="margin-bottom:14px">
      <button class="btn" v-for="g in gates" :key="g" :class="{ primary: filter === g }" @click="filter = g">
        {{ g }} {{ (data.by_gate && data.by_gate[g]) || 0 }}
      </button>
      <button class="btn" :class="{ primary: filter === '在池' }" @click="filter = '在池'">观察+买入</button>
      <button class="btn" :class="{ primary: filter === '全部' }" @click="filter = '全部'">全部</button>
    </div>
    <label class="field" style="margin-bottom:14px;max-width:320px">
      <span>搜索代码 / 名称</span>
      <input v-model="q" placeholder="600519 或 茅台" />
    </label>
    <p class="sub">当前列出 {{ visible.length }} / {{ (data.rows || []).length }}</p>
    <div v-if="!visible.length" class="empty">{{ emptyText }}</div>
    <div v-for="row in visible" :key="row.code" class="card" style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;gap:12px">
        <div>
          <b>{{ row.code }} {{ row.name }}</b>
          <span class="badge 观察" v-if="row.index_member && row.index_member.length" style="margin-left:8px">{{ row.index_member.join(" / ") }}</span>
          <span class="pe-tag" v-if="row.facts && row.facts.pe != null">PE {{ pe(row.facts.pe) }}</span>
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
      <p class="sub" style="margin:10px 0 0">
        {{ row.fact_note }} · {{ row.facts && row.facts.date }} 收盘 {{ money(row.facts && row.facts.close) }}
        · DIF {{ fmt(row.facts && row.facts.dif) }}
        <span v-if="row.facts && row.facts.pe != null"> · 动态市盈 {{ pe(row.facts.pe) }}</span>
        <span v-if="row.key_kind"> · {{ row.key_kind }} 关键位 {{ money(row.key_price) }} 止损 {{ money(row.stop_price) }}</span>
      </p>
      <div class="row-btns" style="margin-top:10px">
        <router-link class="btn" :to="chartLink(row.code)">查看日线与事实</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, GATES } from "../api";
import StatusBadge from "../components/StatusBadge.vue";

const route = useRoute();
const router = useRouter();
const data = ref({ rows: [], summary: {}, by_gate: {}, names: {}, pool: {}, rulesets: [] });
const extraRulesets = ref([]);
const filter = ref("在池");
const q = ref("");
const loading = ref(true);
const gates = GATES;
const rulesetId = computed(() => String(route.query.ruleset || "rules"));
const rulesets = computed(() => (data.value.rulesets && data.value.rulesets.length ? data.value.rulesets : extraRulesets.value));
const currentRuleset = computed(() => data.value.ruleset || rulesets.value.find((r) => r.id === rulesetId.value) || null);
const buyNames = computed(() => (data.value.names && data.value.names.买入) || []);
const watchNames = computed(() => (data.value.names && data.value.names.观察) || []);
const visible = computed(() => {
  const query = q.value.trim();
  return (data.value.rows || []).filter((r) => {
    if (filter.value === "在池" && r.status !== "观察" && r.status !== "买入") return false;
    if (filter.value !== "全部" && filter.value !== "在池" && r.status !== filter.value) return false;
    if (!query) return true;
    return (r.code && r.code.includes(query)) || (r.name && r.name.includes(query));
  });
});
const emptyText = computed(() => {
  if (loading.value) return "正在按当前规则扫描…";
  if (currentRuleset.value && !currentRuleset.value.engine_ok) {
    return currentRuleset.value.engine_note || "本规则尚未写成扫描器。";
  }
  return "这一闸没有股票。";
});
function fmt(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(4);
}
function money(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2);
}
function pe(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(1);
}
function chartLink(code) {
  return { path: "/chart/" + code, query: { ruleset: rulesetId.value } };
}
function switchRuleset(id) {
  router.replace({ path: "/scan", query: { ruleset: id } });
}
async function load() {
  loading.value = true;
  try {
    data.value = await api.scan(rulesetId.value);
  } finally {
    loading.value = false;
  }
}
watch(rulesetId, load);
onMounted(async () => {
  const rs = await api.rulesets().catch(() => ({ items: [] }));
  extraRulesets.value = rs.items || [];
  await load();
});
</script>
