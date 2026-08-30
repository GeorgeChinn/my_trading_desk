<template>
  <div>
    <h1>我的交易</h1>
    <p class="sub">手工记账。不同步任何外部账户。这里记录你已经做了什么，不发出成交指令。</p>
    <div class="card" style="margin-bottom:16px">
      <div class="grid cols-4">
        <label class="field"><span>代码</span><input v-model="form.code" placeholder="600519" /></label>
        <label class="field">
          <span>方向</span>
          <select v-model="form.direction">
            <option>开仓</option>
            <option>加仓</option>
            <option>减仓</option>
            <option>清仓</option>
            <option>记录</option>
          </select>
        </label>
        <label class="field"><span>仓位 %</span><input v-model.number="form.position_pct" type="number" min="0" max="100" /></label>
        <label class="field"><span>日期</span><input v-model="form.date" type="date" /></label>
      </div>
      <label class="field" style="margin-top:10px"><span>原因</span><input v-model="form.reason" placeholder="命中哪条、为什么这个仓位" /></label>
      <div class="row-btns" style="margin-top:12px">
        <button class="btn primary" @click="add">记一笔</button>
      </div>
    </div>
    <table class="table">
      <thead>
        <tr><th>日期</th><th>代码</th><th>方向</th><th>仓位%</th><th>原因</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="t in items" :key="t.id">
          <td>{{ t.date }}</td>
          <td>{{ t.code }} {{ t.name }}</td>
          <td>{{ t.direction }}</td>
          <td>{{ t.position_pct }}</td>
          <td>{{ t.reason }}</td>
          <td><button class="btn danger" @click="remove(t.id)">删除</button></td>
        </tr>
      </tbody>
    </table>
    <p v-if="!items.length" class="empty">还没有手工记录。</p>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";

const items = ref([]);
const form = ref({ code: "", direction: "开仓", position_pct: 0, reason: "", date: "" });

async function load() {
  items.value = (await api.trades()).items || [];
}
async function add() {
  await api.addTrade(form.value);
  form.value = { code: "", direction: "开仓", position_pct: 0, reason: "", date: "" };
  await load();
}
async function remove(id) {
  await api.delTrade(id);
  await load();
}
onMounted(load);
</script>
