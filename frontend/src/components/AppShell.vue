<template>
  <div class="shell" :class="{ 'nav-open': navOpen }">
    <div class="nav-mask" v-if="navOpen" @click="navOpen = false"></div>
    <aside class="sidebar">
      <div class="brand-mark">
        <div class="gc">GC</div>
        <div>
          <strong>Personal Trade</strong>
          <small>只记录 · 不自动成交</small>
        </div>
      </div>
      <nav class="nav">
        <router-link
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          :class="{ active: isActive(item) }"
          @click="navOpen = false"
        >
          <span v-html="item.icon"></span>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="side-foot">
        主路径：波段持有<br />
        指标：MACD(7,28,4) + KDJ
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <button class="menu-btn" type="button" aria-label="打开菜单" @click="navOpen = !navOpen">
          <span></span><span></span><span></span>
        </button>
        <div class="top-title">
          <b>GeorgeChin Personal Trade</b>
          <span> · 本地个人交易空间</span>
        </div>
        <div class="top-actions">
          <span class="pill" :class="{ warn: !connected }">
            <i class="dot"></i>
            <span class="pill-text">{{ healthLabel }}</span>
          </span>
          <button class="btn primary idea-btn" @click="showIdea = true">+ 记录我的想法</button>
          <button class="btn ghost" type="button" @click="logout">退出</button>
        </div>
      </header>
      <div class="page">
        <router-view />
      </div>
    </div>
    <IdeaModal v-if="showIdea" @close="showIdea = false" />
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api";
import IdeaModal from "./IdeaModal.vue";

const route = useRoute();
const connected = ref(false);
const healthLabel = ref("正在检查本地数据");
const showIdea = ref(false);
const navOpen = ref(false);

watch(
  () => route.fullPath,
  () => {
    navOpen.value = false;
  }
);

const icon = (d) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">${d}</svg>`;

const nav = [
  { to: "/", label: "首页", icon: icon('<path d="M4 11.5 12 4l8 7.5V20H4z"/><path d="M9 20v-6h6v6"/>') },
  { to: "/scan", label: "规则扫描", icon: icon('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>') },
  { to: "/cycles", label: "规则轨迹", icon: icon('<path d="M4 16l4-6 4 3 8-9"/><path d="M4 20h16"/>') },
  { to: "/rules", label: "我的规则", icon: icon('<path d="M6 4h12v16H6z"/><path d="M9 8h6M9 12h6M9 16h4"/>') },
  { to: "/trades", label: "我的交易", icon: icon('<path d="M4 19V5"/><path d="M4 16l5-6 4 4 7-9"/>') },
  { to: "/settings", label: "数据与设置", icon: icon('<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.2-1.6l2-1.5-2-3.5-2.4 1a7 7 0 0 0-2.8-1.6L13 3h-2l.6 2.8a7 7 0 0 0-2.8 1.6l-2.4-1-2 3.5 2 1.5A7 7 0 0 0 5 12c0 .5.1 1.1.2 1.6l-2 1.5 2 3.5 2.4-1a7 7 0 0 0 2.8 1.6L11 21h2l.6-2.8a7 7 0 0 0 2.8-1.6l2.4 1 2-3.5-2-1.5c.1-.5.2-1.1.2-1.6z"/>') },
];

function isActive(item) {
  if (item.to === "/") return route.path === "/" || route.path.startsWith("/chart");
  return route.path.startsWith(item.to);
}

async function logout() {
  await api.logout().catch(() => {});
  window.location.reload();
}

onMounted(async () => {
  try {
    const h = await api.health();
    connected.value = !!h.connected;
    healthLabel.value = h.label || "尚未连接真实行情";
  } catch {
    connected.value = false;
    healthLabel.value = "后端未启动";
  }
});
</script>
