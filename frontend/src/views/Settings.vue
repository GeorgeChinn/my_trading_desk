<template>
  <div>
    <h1>数据与设置</h1>
    <p class="sub">
      数据源只用真实行情：日线腾讯 → 新浪 → 东财；筛池优先新浪全市场快照。不使用示例 CSV 当数据源。盘中不改写确认收盘。
    </p>
    <div class="grid cols-2">
      <div class="card">
        <h3>在场与市况</h3>
        <label class="field" style="margin-top:10px">
          <span>人是否在场</span>
          <select v-model="person" @change="save">
            <option :value="true">在场</option>
            <option :value="false">不在场</option>
          </select>
        </label>
        <label class="field" style="margin-top:10px">
          <span>大盘开关（只定性，不编造）</span>
          <select v-model="regime" @change="save">
            <option>未设置</option>
            <option>多</option>
            <option>空</option>
            <option>震荡</option>
          </select>
        </label>
      </div>
      <div class="card">
        <h3>定时更新（确认收盘）</h3>
        <p class="sub">{{ schedule.why }}</p>
        <label class="field">
          <span>交易日自动拉数</span>
          <select v-model="scheduleOn" @change="saveSchedule">
            <option :value="true">开（15:40 / 16:30 北京时间）</option>
            <option :value="false">关</option>
          </select>
        </label>
        <p class="sub" style="margin-top:10px">下次：{{ schedule.next_run || "—" }} · 上次触发：{{ schedule.last_fired || "—" }}</p>
        <p class="sub">{{ dataLabel }} · 池子 {{ poolCount }} 只</p>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <h3>数据源探测</h3>
      <p class="sub">链：腾讯 → 新浪 → 东财。一个源失败自动换下一个。</p>
      <div class="row-btns" style="margin-bottom:12px">
        <button class="btn" @click="probe">探测后备源</button>
        <button class="btn primary" :disabled="syncing" @click="sync(false)">现在更新确认收盘</button>
        <button class="btn" :disabled="syncing" @click="sync(true)">强制重拉日线</button>
        <button class="btn" :disabled="syncing" @click="history">补全全A近3年日线</button>
      </div>
      <table class="table" v-if="sources.length">
        <thead><tr><th>源</th><th>用途</th><th>状态</th><th>耗时</th><th>最近确认日</th></tr></thead>
        <tbody>
          <tr v-for="s in sources" :key="s.name">
            <td>{{ s.name }}</td>
            <td>{{ s.role }}</td>
            <td><span class="badge" :class="s.ok ? '观察' : '禁止'">{{ s.ok ? "通" : "断" }}</span></td>
            <td>{{ s.ms }} ms</td>
            <td>{{ s.last_date || s.error || "—" }}</td>
          </tr>
        </tbody>
      </table>
      <p class="sub" style="margin-top:10px">{{ syncText }}</p>
      <div v-if="syncing || barsTotal" class="sub">日线 {{ barsDone }} / {{ barsTotal }}</div>
      <p class="sub">全A近3年是确认收盘补历史，大约 5000+ 只、每只约 800 根，要较长时间。不改规则扫描池子，失败会跳过不编 K 线。</p>
    </div>

    <div class="card" style="margin-top:14px">
      <h3>第3条 漏斗（全市场 → 入池）</h3>
      <p class="sub">流通市值 ≥ 300 亿 · 日成交额 ≥ 5 亿 · 非 ST · 股价 ≥ 5 元。优先样本只加标签。PROFILE 跟踪带宽 100 只，扫描不截断。</p>
      <div class="grid cols-4" v-if="funnel && Object.keys(funnel).length">
        <div class="stat"><div class="n">{{ funnel.listed || 0 }}</div><div class="k">上市 A 股</div></div>
        <div class="stat"><div class="n">{{ funnel.non_st || 0 }}</div><div class="k">非 ST</div></div>
        <div class="stat"><div class="n">{{ funnel.mcap_ok || 0 }}</div><div class="k">市值门槛过的行</div></div>
        <div class="stat"><div class="n">{{ funnel.pool || 0 }}</div><div class="k">同时满足入池</div></div>
      </div>
      <p class="sub" v-if="funnel && funnel.preferred != null">其中优先样本 {{ funnel.preferred }} 只 · 确认收盘 {{ funnel.trade_date }} · 来源 {{ funnel.source }}</p>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api";

const person = ref(true);
const regime = ref("未设置");
const poolCount = ref(0);
const dataLabel = ref("");
const funnel = ref({});
const syncText = ref("");
const syncing = ref(false);
const barsDone = ref(0);
const barsTotal = ref(0);
const sources = ref([]);
const schedule = ref({});
const scheduleOn = ref(true);
let timer = null;

async function load() {
  const s = await api.settings();
  person.value = !!s.person_present;
  regime.value = s.market_regime || "未设置";
  poolCount.value = s.pool_count || 0;
  dataLabel.value = s.data_label || "";
  funnel.value = s.pool_snapshot || {};
  schedule.value = s.schedule || {};
  scheduleOn.value = schedule.value.enabled !== false;
  applySync(s.sync || {});
}
function applySync(st) {
  syncing.value = st.state === "running";
  syncText.value = st.message || "";
  barsDone.value = st.bars_done || 0;
  barsTotal.value = st.bars_total || 0;
  if (st.funnel) funnel.value = st.funnel;
  if (st.pool_size) poolCount.value = st.pool_size;
}
async function poll() {
  try {
    const st = await api.syncStatus();
    applySync(st);
    if (st.state !== "running") {
      stopPoll();
      await load();
    }
  } catch {
    stopPoll();
  }
}
function startPoll() {
  stopPoll();
  timer = setInterval(poll, 1500);
  poll();
}
function stopPoll() {
  if (timer) clearInterval(timer);
  timer = null;
}
async function save() {
  await api.saveSettings({ person_present: person.value, market_regime: regime.value });
}
async function saveSchedule() {
  await api.saveSettings({ schedule_enabled: scheduleOn.value });
  schedule.value = await api.schedule();
}
async function sync(force) {
  const r = await api.startSync(force);
  syncText.value = r.message;
  startPoll();
}
async function history() {
  const r = await api.startHistory();
  syncText.value = r.message;
  startPoll();
}
async function probe() {
  const r = await api.sources();
  sources.value = r.items || [];
  syncText.value = r.note || "";
}
onMounted(async () => {
  await load();
  if (syncing.value) startPoll();
  probe().catch(() => {});
});
onBeforeUnmount(stopPoll);
</script>
