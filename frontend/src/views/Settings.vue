<template>
  <div>
    <h1>数据与设置</h1>
    <p class="sub">
      全股池就是 data/csv 里那五千多只股票。规则门槛只在「规则扫描」里按各自 RULES 记排除，这里不另建小池子。
    </p>
    <div class="card" style="margin-bottom:14px">
      <h3>全股池</h3>
      <p class="sub">{{ ashare.note || "data/csv 有多少只，全股池就是多少只。" }}</p>
      <div class="grid cols-4" style="margin-top:12px">
        <div class="stat"><div class="n">{{ ashare.count || poolCount || 0 }}</div><div class="k">全股池</div></div>
      </div>
      <p class="stamp" style="margin-top:12px">
        数据日 {{ ashare.asof || "—" }}
        · 上次更新 {{ ashare.updated_at || ashare.sync_at || "—" }}
      </p>
    </div>
    <div class="card">
      <h3>定时更新</h3>
      <p class="sub">{{ schedule.why }}</p>
      <label class="field">
        <span>交易日自动拉数</span>
        <select v-model="scheduleOn" @change="saveSchedule">
          <option :value="true">开</option>
          <option :value="false">关</option>
        </select>
      </label>
      <p class="sub" style="margin-top:10px">下次：{{ schedule.next_run || "—" }} · 上次触发：{{ schedule.last_fired || "—" }}</p>
      <p class="sub">全股池 {{ ashare.count || poolCount }} 只</p>
    </div>
    <div class="card" style="margin-top:14px">
      <h3>更新时间点</h3>
      <p class="sub">一天 24 小时，每隔半小时。可多选。工作日到点自动拉数并重扫。</p>
      <div class="time-grid">
        <button
          type="button"
          class="time-chip"
          v-for="t in slots"
          :key="'b'+t"
          :class="{ on: picked.includes(t) }"
          @click="toggleTime(t)"
        >{{ t }}</button>
      </div>
      <p class="sub" style="margin-top:10px">已选 {{ pickedList }}</p>
    </div>

    <div class="card" style="margin-top:14px">
      <h3>数据源探测</h3>
      <p class="sub">链：腾讯 → 新浪 → 东财。一个源失败自动换下一个。</p>
      <div class="row-btns" style="margin-bottom:12px">
        <button class="btn" @click="probe">探测后备源</button>
        <button class="btn primary" :disabled="syncing" @click="sync(false)">现在更新实时数据</button>
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
      <p class="sub">补全日线是给全股池里每一只补历史 K 线，不另建小池子。</p>
    </div>

  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api";

const poolCount = ref(0);
const ashare = ref({});
const syncText = ref("");
const syncing = ref(false);
const barsDone = ref(0);
const barsTotal = ref(0);
const sources = ref([]);
const schedule = ref({});
const scheduleOn = ref(true);
const picked = ref(["15:30", "16:30"]);
const slots = computed(() => schedule.value.slots || defaultSlots());
const pickedList = computed(() => [...picked.value].sort().join(" / ") || "（未选）");
let timer = null;

function defaultSlots() {
  const out = [];
  for (let h = 0; h < 24; h++) {
    out.push(`${String(h).padStart(2, "0")}:00`);
    out.push(`${String(h).padStart(2, "0")}:30`);
  }
  return out;
}
function toggleTime(t) {
  const cur = picked.value;
  picked.value = cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t].sort();
  saveSchedule();
}

async function load() {
  const s = await api.settings();
  poolCount.value = s.pool_count || 0;
  ashare.value = s.ashare_pool || {};
  schedule.value = s.schedule || {};
  scheduleOn.value = schedule.value.enabled !== false;
  picked.value = schedule.value.times && schedule.value.times.length ? [...schedule.value.times] : ["15:30", "16:30"];
  applySync(s.sync || {});
}
function applySync(st) {
  syncing.value = st.state === "running";
  syncText.value = st.message || "";
  barsDone.value = st.bars_done || 0;
  barsTotal.value = st.bars_total || 0;
  if (st.pool_size && st.pool_size >= (poolCount.value || 0)) poolCount.value = st.pool_size;
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
async function saveSchedule() {
  await api.saveSettings({
    schedule_enabled: scheduleOn.value,
    schedule_times: [...picked.value].sort(),
  });
  schedule.value = await api.schedule();
  picked.value = schedule.value.times && schedule.value.times.length ? [...schedule.value.times] : [...picked.value];
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
