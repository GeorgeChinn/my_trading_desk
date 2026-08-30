async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.detail || data.message || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export const api = {
  health: () => request("/api/health"),
  home: () => request("/api/home"),
  scan: () => request("/api/scan"),
  scanOne: (code) => request(`/api/scan/${code}`),
  chart: (code) => request(`/api/chart/${code}`),
  watch: () => request("/api/watch"),
  addWatch: (body) => request("/api/watch", { method: "POST", body }),
  delWatch: (id) => request(`/api/watch/${id}`, { method: "DELETE" }),
  viewWatch: (id) => request(`/api/watch/${id}/viewed`, { method: "POST" }),
  judgeWatch: (id, body) => request(`/api/watch/${id}/judgement`, { method: "POST", body }),
  trades: () => request("/api/trades"),
  addTrade: (body) => request("/api/trades", { method: "POST", body }),
  delTrade: (id) => request(`/api/trades/${id}`, { method: "DELETE" }),
  journals: () => request("/api/journal"),
  journal: (day) => request(`/api/journal/${day}`),
  saveJournal: (day, body) => request(`/api/journal/${day}`, { method: "PUT", body }),
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
  sources: () => request("/api/sources"),
  schedule: () => request("/api/schedule"),
  ideas: () => request("/api/ideas"),
  addIdea: (body) => request("/api/ideas", { method: "POST", body }),
  seed: () => request("/api/seed", { method: "POST" }),
};

export const STATUSES = ["排除", "观察", "等待", "买入", "减仓", "清仓"];
export const GATES = ["排除", "观察", "等待", "买入", "减仓", "清仓"];
