import { beginLoading, endLoading } from "./loading.js";

const QUIET = ["/api/session", "/api/health"];

async function request(path, options = {}) {
  const silent = Boolean(options.silent);
  const rest = { ...options };
  delete rest.silent;
  const quiet = silent || QUIET.some((p) => path === p || path.startsWith(p + "?"));
  if (!quiet) beginLoading();
  try {
    const headers = { ...(rest.headers || {}) };
    if (rest.body && !(rest.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      rest.body = JSON.stringify(rest.body);
    }
    const res = await fetch(path, { ...rest, headers, credentials: "include" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data.detail || data.message || res.statusText;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  } finally {
    if (!quiet) endLoading();
  }
}

export const api = {
  session: () => request("/api/session"),
  login: (password) => request("/api/login", { method: "POST", body: { password } }),
  logout: () => request("/api/logout", { method: "POST" }),
  health: () => request("/api/health"),
  home: () => request("/api/home"),
  rulesets: () => request("/api/rulesets"),
  scan: (ruleset = "rules") => request(`/api/scan?ruleset=${encodeURIComponent(ruleset || "rules")}`),
  cycles: (ruleset = "rules", params = {}, silent = false) => {
    const q = new URLSearchParams({ ruleset: ruleset || "rules", ...params });
    return request(`/api/cycles?${q.toString()}`, { silent });
  },
  scanOne: (code, ruleset = "rules") =>
    request(`/api/scan/${encodeURIComponent(code)}?ruleset=${encodeURIComponent(ruleset || "rules")}`),
  chart: (code, ruleset = "rules") =>
    request(`/api/chart/${encodeURIComponent(code)}?ruleset=${encodeURIComponent(ruleset || "rules")}`),
  watch: () => request("/api/watch"),
  addWatch: (body) => request("/api/watch", { method: "POST", body }),
  delWatch: (id) => request(`/api/watch/${id}`, { method: "DELETE" }),
  viewWatch: (id) => request(`/api/watch/${id}/viewed`, { method: "POST" }),
  judgeWatch: (id, body) => request(`/api/watch/${id}/judgement`, { method: "POST", body }),
  trades: () => request("/api/trades"),
  addTrade: (body) => request("/api/trades", { method: "POST", body }),
  delTrade: (id) => request(`/api/trades/${id}`, { method: "DELETE" }),
  rules: () => request("/api/rules"),
  settings: () => request("/api/settings"),
  saveSettings: (body) => request("/api/settings", { method: "PUT", body }),
  uploadCsv: (file) => {
    const body = new FormData();
    body.append("file", file);
    return request("/api/settings/csv", { method: "POST", body });
  },
  pullTushare: (code) => request("/api/settings/tushare", { method: "POST", body: { code } }),
  pool: () => request("/api/pool"),
  syncStatus: () => request("/api/sync"),
  startSync: (force = false) => request(`/api/sync?force=${force ? "true" : "false"}`, { method: "POST" }),
  startHistory: () => request("/api/sync/history", { method: "POST" }),
  sources: () => request("/api/sources"),
  schedule: () => request("/api/schedule"),
  ideas: () => request("/api/ideas"),
  addIdea: (body) => request("/api/ideas", { method: "POST", body }),
  seed: () => request("/api/seed", { method: "POST" }),
};

export const STATUSES = ["排除", "观察", "买入", "卖出"];
export const GATES = ["排除", "观察", "买入", "卖出"];
