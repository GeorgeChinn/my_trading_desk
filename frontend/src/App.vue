<template>
  <div v-if="checking" class="empty">正在核对登录…</div>
  <form v-else-if="!ok" class="login-box" @submit.prevent="submit">
    <div class="gc">GC</div>
    <h1>GeorgeChin Personal Trade</h1>
    <p class="sub">本地个人交易空间 · 只记录，不自动成交</p>
    <label class="field">
      <span>密码</span>
      <input v-model="password" type="password" autocomplete="current-password" />
    </label>
    <p v-if="err" class="sub" style="color:var(--red);margin-top:10px">{{ err }}</p>
    <button class="btn primary" style="margin-top:14px;width:100%" type="submit">进入</button>
  </form>
  <AppShell v-else />
</template>

<script setup>
import { onMounted, ref } from "vue";
import AppShell from "./components/AppShell.vue";
import { api } from "./api";

const checking = ref(true);
const ok = ref(false);
const password = ref("");
const err = ref("");

onMounted(async () => {
  try {
    const s = await api.session();
    ok.value = !!s.ok;
  } catch {
    ok.value = false;
  } finally {
    checking.value = false;
  }
});

async function submit() {
  err.value = "";
  try {
    await api.login(password.value);
    ok.value = true;
  } catch (e) {
    err.value = e.message || "密码不对";
  }
}
</script>
