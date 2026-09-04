<template>
  <div>
    <h1>首页</h1>
    <p class="sub">
      主路径 {{ path }} · 总闸：排除 → 观察 → 等待 → 买入 → 减仓 / 清仓。
      买入只表示路径到达，不是成交指令。
    </p>

    <div class="warn-banner">{{ positionBlock }} · 人{{ personPresent ? "在场" : "不在场" }} · 大盘开关：{{ marketRegime }} · RULES 池子 {{ poolCount }} 只{{ poolDate ? "（确认收盘 " + poolDate + "）" : "" }}</div>
    <div class="warn-banner" v-for="(r, i) in reminders" :key="i">{{ r }}</div>

    <div class="card flash" style="margin-bottom:16px">
      <h2>你设置的 {{ triggeredCount }} 个观察条件已触发</h2>
      <p class="sub">触发文案固定为：这是事实记录。当前行情不改写已确认收盘事实。</p>
      <div v-if="!cards.length" class="empty">还没有触发。确认 CSV 已放入 data/csv，或到规则扫描查看当日漏斗。</div>
      <div v-for="card in cards" :key="card.id" class="card" style="margin-top:12px;box-shadow:none">
        <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
          <div>
            <b>{{ card.code }} {{ card.name }}</b>
            <div class="sub" style="margin:6px 0 0">条件原文：{{ card.condition_text }}</div>
          </div>
          <span class="badge 观察">这是事实记录</span>
        </div>
        <div class="fact-strip">
          <div><small>触发日期</small><b>{{ snap(card).date || "—" }}</b></div>
          <div><small>收盘</small><b>{{ fmt(snap(card).close) }}</b></div>
          <div><small>5日均线</small><b>{{ fmt(snap(card).ma5) }}</b></div>
          <div><small>离均线</small><b>{{ pct(snap(card).ma5_gap_pct) }}</b></div>
        </div>
        <p class="sub" v-if="card.latest">
          最新 CSV 日线 {{ card.latest.date }} 收盘 {{ fmt(card.latest.close) }}
          （仅供对照，不改写已确认收盘事实）
        </p>
        <div class="row-btns">
          <router-link class="btn primary" :to="chartLink(card)">查看日线与事实</router-link>
          <button class="btn" @click="openJudge(card)">记录我的判断</button>
        </div>
      </div>
    </div>

    <div class="grid cols-3" style="margin-bottom:16px">
      <div class="card stat">
        <div class="n">{{ queues.pending_view ?? 0 }}</div>
        <div class="k">待看观察</div>
      </div>
      <div class="card stat">
        <div class="n">{{ queues.pending_judge ?? 0 }}</div>
        <div class="k">待确认判断</div>
      </div>
      <div class="card stat">
        <div class="n">{{ queues.monitor ?? 0 }}</div>
        <div class="k">监测中心</div>
      </div>
    </div>

    <div class="card">
      <h2>今天规则扫描</h2>
      <p class="sub">摘要只数。符合 = 总闸到达买入。符合 ≠ 成交指令。</p>
      <div class="grid cols-4">
        <div class="stat"><div class="n">{{ scan.符合 ?? 0 }}</div><div class="k">符合</div></div>
        <div class="stat"><div class="n">{{ scan.继续跟踪 ?? 0 }}</div><div class="k">继续跟踪</div></div>
        <div class="stat"><div class="n">{{ scan.观察 ?? 0 }}</div><div class="k">观察</div></div>
        <div class="stat"><div class="n">{{ scan.排除 ?? 0 }}</div><div class="k">排除</div></div>
      </div>
      <div class="row-btns" style="margin-top:14px">
        <router-link class="btn" to="/scan">打开规则扫描</router-link>
      </div>
    </div>

    <div class="modal-mask" v-if="judgeCard" @click.self="judgeCard = null">
      <div class="modal">
        <h2>记录我的判断 · {{ judgeCard.code }}</h2>
        <p class="sub">判断按总闸记录。买入只表示路径到达，不是成交指令。</p>
        <label class="field">
          <span>状态</span>
          <select v-model="judgeStatus">
            <option v-for="s in statuses" :key="s">{{ s }}</option>
          </select>
        </label>
        <label class="field" style="margin-top:10px">
          <span>备注</span>
          <textarea v-model="judgeNote"></textarea>
        </label>
        <p class="sub" v-if="judgeStatus === '买入'">
          总闸买入不是成交指令。
        </p>
        <div class="row-btns" style="margin-top:12px;justify-content:flex-end">
          <button class="btn ghost" @click="judgeCard = null">取消</button>
          <button class="btn primary" @click="saveJudge">保存判断</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { api, STATUSES } from "../api";

const cards = ref([]);
const triggeredCount = ref(0);
const queues = ref({});
const scan = ref({});
const path = ref("波段持有");
const positionBlock = ref("");
const marketRegime = ref("未设置");
const personPresent = ref(true);
const poolCount = ref(0);
const poolDate = ref("");
const reminders = ref([]);
const judgeCard = ref(null);
const judgeStatus = ref("观察");
const judgeNote = ref("");
const statuses = STATUSES;

function snap(card) {
  return (card.trigger && card.trigger.snapshot) || {};
}
function fmt(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2);
}
function pct(v) {
  return v == null || Number.isNaN(Number(v)) ? "—" : `${Number(v).toFixed(2)}%`;
}
function chartLink(card) {
  const date = snap(card).date || "";
  return { name: "chart", params: { code: card.code }, query: { watch: card.id, trigger: date } };
}
function openJudge(card) {
  judgeCard.value = card;
  judgeStatus.value = (card.judgement && card.judgement.status) || "观察";
  judgeNote.value = (card.judgement && card.judgement.note) || "";
}
async function saveJudge() {
  await api.judgeWatch(judgeCard.value.id, { status: judgeStatus.value, note: judgeNote.value });
  judgeCard.value = null;
  await load();
}
async function load() {
  const data = await api.home();
  cards.value = data.cards || [];
  triggeredCount.value = data.triggered_count || 0;
  queues.value = data.queues || {};
  scan.value = data.scan_summary || {};
  path.value = data.path;
  positionBlock.value = data.position_block;
  marketRegime.value = data.market_regime;
  personPresent.value = data.person_present;
  poolCount.value = data.pool_count || 0;
  poolDate.value = data.pool_trade_date || "";
  reminders.value = data.reminders || [];
}
onMounted(load);
</script>
