<template>
  <div>
    <h1>我的复盘</h1>
    <p class="sub">按 journal/TEMPLATE.md 写当天环境 / 做了什么 / 没做什么。</p>
    <div class="grid cols-2">
      <div class="card">
        <label class="field"><span>日期</span><input v-model="day" type="date" @change="loadOne" /></label>
        <div class="grid cols-2" style="margin-top:10px">
          <label class="field"><span>大盘</span><input v-model="fields.market" /></label>
          <label class="field"><span>情绪</span><input v-model="fields.mood" /></label>
        </div>
        <label class="field" style="margin-top:10px"><span>主线板块</span><input v-model="fields.theme" /></label>
        <label class="field" style="margin-top:10px"><span>候选（池子 / 路径 / 状态）</span><input v-model="fields.candidates" /></label>
        <label class="field" style="margin-top:10px"><span>开仓 / 等待 / 放弃</span><input v-model="fields.action" /></label>
        <label class="field" style="margin-top:10px"><span>路径是否匹配</span><input v-model="fields.path_fit" /></label>
        <label class="field" style="margin-top:10px"><span>命中了哪条</span><input v-model="fields.hit" /></label>
        <label class="field" style="margin-top:10px"><span>打破了哪条</span><input v-model="fields.broke" /></label>
        <label class="field" style="margin-top:10px"><span>准备改哪一条（最多一条）</span><input v-model="fields.change" /></label>
        <label class="field" style="margin-top:10px"><span>方向对不对</span><input v-model="fields.q1" /></label>
        <label class="field" style="margin-top:10px"><span>路径是不是我的</span><input v-model="fields.q2" /></label>
        <div class="row-btns" style="margin-top:12px">
          <button class="btn primary" @click="save">写入 journal/{{ day }}.md</button>
        </div>
      </div>
      <div>
        <div class="card" style="margin-bottom:12px">
          <h3>已有复盘</h3>
          <div class="list" style="margin-top:8px">
            <button class="chip" v-for="j in list" :key="j.date" @click="open(j.date)">{{ j.date }}</button>
            <div v-if="!list.length" class="empty">还没有文件</div>
          </div>
        </div>
        <div class="card">
          <h3>将写入的原文</h3>
          <pre class="pre">{{ preview.markdown }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";

const day = ref(new Date().toISOString().slice(0, 10));
const fields = ref({});
const preview = ref({ markdown: "" });
const list = ref([]);

async function loadList() {
  list.value = (await api.journals()).items || [];
}
async function loadOne() {
  preview.value = await api.journal(day.value);
  fields.value = { ...(preview.value.fields || {}) };
}
function open(d) {
  day.value = d;
  loadOne();
}
async function save() {
  preview.value = await api.saveJournal(day.value, fields.value);
  await loadList();
}
onMounted(async () => {
  await loadList();
  await loadOne();
});
</script>
