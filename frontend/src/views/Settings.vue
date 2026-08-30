<template>
  <div>
    <h1>数据与设置</h1>
    <p class="sub">日线 CSV 放在 data/csv，格式 code,date,open,high,low,close,volume。没有 Tushare token 时只用 CSV。</p>
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
        <h3>Tushare（预留）</h3>
        <p class="sub">未配置 token 时所有扫描走本地 CSV。</p>
        <label class="field">
          <span>Token</span>
          <input v-model="token" type="password" placeholder="留空 = 只用 CSV" />
        </label>
        <label class="field" style="margin-top:10px">
          <span>拉取代码（可选）</span>
          <input v-model="pullCode" placeholder="600519" />
        </label>
        <div class="row-btns" style="margin-top:12px">
          <button class="btn" @click="saveToken">保存 token</button>
          <button class="btn" @click="pull">尝试拉取</button>
        </div>
        <p class="sub" v-if="pullMsg">{{ pullMsg }}</p>
      </div>
    </div>
    <div class="card" style="margin-top:14px">
      <h3>上传或放置 CSV</h3>
      <p class="sub">目录：{{ csvDir }}</p>
      <input type="file" accept=".csv" @change="upload" />
      <table class="table" style="margin-top:12px">
        <thead><tr><th>文件</th><th>代码</th><th>大小</th></tr></thead>
        <tbody>
          <tr v-for="f in files" :key="f.file">
            <td>{{ f.file }}</td>
            <td>{{ f.code }}</td>
            <td>{{ f.bytes }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";

const person = ref(true);
const regime = ref("未设置");
const token = ref("");
const pullCode = ref("600519");
const pullMsg = ref("");
const files = ref([]);
const csvDir = ref("");

async function load() {
  const s = await api.settings();
  person.value = !!s.person_present;
  regime.value = s.market_regime || "未设置";
  token.value = s.tushare_configured ? "********" : "";
  files.value = s.csv_files || [];
  csvDir.value = s.csv_dir || "";
}
async function save() {
  await api.saveSettings({ person_present: person.value, market_regime: regime.value });
}
async function saveToken() {
  if (token.value && token.value !== "********") {
    await api.saveSettings({ tushare_token: token.value });
  }
  await load();
}
async function pull() {
  try {
    const r = await api.pullTushare(pullCode.value);
    pullMsg.value = r.message;
    await load();
  } catch (e) {
    pullMsg.value = e.message;
  }
}
async function upload(ev) {
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  await api.uploadCsv(file);
  ev.target.value = "";
  await load();
}
onMounted(load);
</script>
