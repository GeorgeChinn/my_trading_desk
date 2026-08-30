<template>
  <div>
    <h1>我的观察</h1>
    <p class="sub">人工添加代码 + 观察条件。内置两条，不发明新指标。</p>
    <div class="card" style="margin-bottom:16px">
      <div class="grid cols-3">
        <label class="field">
          <span>代码</span>
          <input v-model="form.code" placeholder="600519" />
        </label>
        <label class="field">
          <span>观察条件</span>
          <select v-model="form.condition_id">
            <option v-for="c in conditions" :key="c.id" :value="c.id">{{ c.text }}</option>
            <option value="custom">自定义（需人工核对）</option>
          </select>
        </label>
        <label class="field" v-if="form.condition_id === 'custom'">
          <span>条件原文</span>
          <input v-model="form.condition_text" placeholder="用你自己的话写，系统不自动发明指标" />
        </label>
      </div>
      <div class="row-btns" style="margin-top:12px">
        <button class="btn primary" @click="add">加入观察</button>
      </div>
    </div>
    <div class="grid cols-3" style="margin-bottom:16px">
      <div class="card stat"><div class="n">{{ queues.pending_view || 0 }}</div><div class="k">待看观察</div></div>
      <div class="card stat"><div class="n">{{ queues.pending_judge || 0 }}</div><div class="k">待确认判断</div></div>
      <div class="card stat"><div class="n">{{ queues.monitor || 0 }}</div><div class="k">监测中心</div></div>
    </div>
    <table class="table">
      <thead>
        <tr>
          <th>代码</th>
          <th>条件原文</th>
          <th>触发</th>
          <th>快照</th>
          <th>判断</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id">
          <td>{{ item.code }} {{ item.name }}</td>
          <td>{{ item.condition_text }}</td>
          <td>
            <span v-if="item.triggered" class="badge 观察">这是事实记录</span>
            <span v-else class="badge 排除">未触发</span>
          </td>
          <td>
            <div v-if="item.trigger && item.trigger.snapshot">
              {{ item.trigger.snapshot.date }}
              收盘 {{ fmt(item.trigger.snapshot.close) }}
              均线 {{ fmt(item.trigger.snapshot.ma5) }}
              离均线 {{ pct(item.trigger.snapshot.ma5_gap_pct) }}
            </div>
            <div v-else class="sub">—</div>
          </td>
          <td>{{ (item.judgement && item.judgement.status) || "待确认" }}</td>
          <td>
            <div class="row-btns">
              <router-link class="btn" :to="'/chart/' + item.code">日线</router-link>
              <button class="btn danger" @click="remove(item.id)">移除</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";

const items = ref([]);
const conditions = ref([]);
const queues = ref({});
const form = ref({ code: "", condition_id: "ma5_reclaim", condition_text: "" });

function fmt(v) {
  return v == null ? "—" : Number(v).toFixed(2);
}
function pct(v) {
  return v == null ? "—" : `${Number(v).toFixed(2)}%`;
}
async function load() {
  const data = await api.watch();
  items.value = data.items || [];
  conditions.value = data.conditions || [];
  queues.value = data.queues || {};
}
async function add() {
  await api.addWatch(form.value);
  form.value = { code: "", condition_id: "ma5_reclaim", condition_text: "" };
  await load();
}
async function remove(id) {
  await api.delWatch(id);
  await load();
}
onMounted(load);
</script>
