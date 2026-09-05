<template>
  <div>
    <h1>我的规则</h1>
    <p class="sub">只读展示 PROFILE.md 与各 RULES 文件。网站上不能编辑仓位上限。</p>
    <div class="warn-banner">{{ banner }}</div>
    <div class="row-btns" style="margin-bottom:12px">
      <button
        class="btn"
        v-for="item in items"
        :key="item.id"
        :class="{ primary: tab === item.id }"
        @click="tab = item.id"
      >
        {{ item.file }}
      </button>
      <button class="btn" :class="{ primary: tab === 'profile' }" @click="tab = 'profile'">PROFILE.md</button>
    </div>
    <p class="sub" v-if="current && current.engine_note">{{ current.title }} · {{ current.engine_note }}</p>
    <div class="card">
      <pre class="pre">{{ body }}</pre>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { api } from "../api";

const tab = ref("rules");
const rules = ref("");
const profile = ref("");
const banner = ref("");
const items = ref([]);
const current = computed(() => items.value.find((x) => x.id === tab.value) || null);
const body = computed(() => {
  if (tab.value === "profile") return profile.value;
  if (current.value && current.value.text) return current.value.text;
  if (tab.value === "rules") return rules.value;
  return "";
});

onMounted(async () => {
  const data = await api.rules();
  rules.value = data.rules;
  profile.value = data.profile;
  banner.value = data.banner;
  items.value = data.items || [];
});
</script>
