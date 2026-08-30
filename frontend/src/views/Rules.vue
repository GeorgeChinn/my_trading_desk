<template>
  <div>
    <h1>我的规则</h1>
    <p class="sub">只读展示 PROFILE.md / RULES.md。网站上不能编辑仓位上限。</p>
    <div class="warn-banner">{{ banner }}</div>
    <div class="row-btns" style="margin-bottom:12px">
      <button class="btn" :class="{ primary: tab === 'rules' }" @click="tab = 'rules'">RULES.md</button>
      <button class="btn" :class="{ primary: tab === 'profile' }" @click="tab = 'profile'">PROFILE.md</button>
    </div>
    <div class="card">
      <pre class="pre">{{ tab === 'rules' ? rules : profile }}</pre>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";

const tab = ref("rules");
const rules = ref("");
const profile = ref("");
const banner = ref("");

onMounted(async () => {
  const data = await api.rules();
  rules.value = data.rules;
  profile.value = data.profile;
  banner.value = data.banner;
});
</script>
