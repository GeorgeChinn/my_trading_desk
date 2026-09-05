import { createRouter, createWebHistory } from "vue-router";
import Home from "./views/Home.vue";
import Scan from "./views/Scan.vue";
import Watch from "./views/Watch.vue";
import Trades from "./views/Trades.vue";
import Rules from "./views/Rules.vue";
import Settings from "./views/Settings.vue";
import Chart from "./views/Chart.vue";
import Cycles from "./views/Cycles.vue";

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: Home, meta: { title: "首页" } },
    { path: "/scan", name: "scan", component: Scan, meta: { title: "规则扫描" } },
    { path: "/cycles", name: "cycles", component: Cycles, meta: { title: "规则轨迹" } },
    { path: "/watch", name: "watch", component: Watch, meta: { title: "我的观察" } },
    { path: "/trades", name: "trades", component: Trades, meta: { title: "我的交易" } },
    { path: "/rules", name: "rules", component: Rules, meta: { title: "我的规则" } },
    { path: "/settings", name: "settings", component: Settings, meta: { title: "数据与设置" } },
    { path: "/chart/:code", name: "chart", component: Chart, meta: { title: "日线与事实" } },
  ],
});
