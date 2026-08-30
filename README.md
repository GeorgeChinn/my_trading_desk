# GeorgeChin Personal Trade · 本地个人交易空间

把已写进 `PROFILE.md` / `RULES.md` 的个人交易方式做成**只读规则 + 本地筛选 + 手工记账**的网站。  
不做自动成交，不接任何外部账户，不发明新指标。

先读：`AGENTS.md` → `PROFILE.md` → `RULES.md` → 每天用 `journal/TEMPLATE.md` 复盘。

## 这台机器能做什么 / 不能做什么

- 能：按 RULES 把股票分成 排除 / 观察 / 等待 / 试仓 / 标准仓 / 禁止；冻结观察触发快照；画日线图；手工记账和复盘。
- 不能：改 RULES 仓位数字；把「路径匹配」写成可开仓；在仓位阈值空缺时升到试仓 / 标准仓。
- 指标只许：`MACD(7,28,4)` + `KDJ` + 均线辅助展示。

`data/csv` 只是腾讯/新浪/东财确认收盘的本地缓存，不是数据源。不再写入示例 K 线。

## 安装

需要：Python 3.11+、Node.js 18+。

```powershell
cd D:\jiaoyi\my-trading-desk

python -m pip install -r backend\requirements.txt

cd frontend
npm install
cd ..
```

## 真实行情

日线后备链：**腾讯 → 新浪 → 东财**（一个断了自动换）。全市场筛池优先新浪快照。Tushare 仅在 token 真有权限时才会被尝试。

按 RULES §3 硬门槛筛全部入池股（流通市值 ≥ 300 亿、日成交额 ≥ 5 亿、非 ST、股价 ≥ 5 元）。沪股通 / 沪深300 / 上证50 只打优先标签。PROFILE 跟踪带宽 100 只，扫描不截断。

### 什么时候更新

A 股 15:00 收盘。交易所日线大约 15:10–15:30 才齐，盘中 K 线不是确认收盘，系统**不在盘中改写**。

| 时间（北京） | 做什么 |
|---|---|
| 交易日 15:40 | 第一趟：筛池 + 拉确认日线 |
| 交易日 16:30 | 第二趟：只补失败/缺根 |
| 周六日 | 不跑 |

`python run.py` 开着时，进程内定时器会自己打点。若电脑开着但交易台没开，可登记 Windows 计划任务：

```powershell
powershell -ExecutionPolicy Bypass -File D:\jiaoyi\my-trading-desk\scripts\install_schedule.ps1
```

手工立刻更新：数据与设置 →「现在更新确认收盘」，或：

```powershell
D:\jiaoyi\my-trading-desk\sync_once.cmd
```

## 启动

**方式 A（推荐，两个窗口）**

窗口 1 — 后端：

```powershell
cd D:\jiaoyi\my-trading-desk
python run.py
```

窗口 2 — 前端：

```powershell
cd D:\jiaoyi\my-trading-desk\frontend
npm run dev
```

打开：**http://127.0.0.1:5173**（若 5173 被占用，Vite 会改用 5174，看终端提示）

**方式 B（单端口，先编译前端）**

```powershell
cd D:\jiaoyi\my-trading-desk\frontend
npm run build

cd D:\jiaoyi\my-trading-desk
python run.py
```

打开：**http://127.0.0.1:8000**

顶栏应显示「真实行情已连接」和确认收盘日。

## 每页干什么

### 顶栏 / 侧栏

- 顶栏：`GeorgeChin Personal Trade · 本地个人交易空间`；数据状态「本地数据已连接」；按钮「+ 记录我的想法」（只存本地文本）。
- 侧栏 7 项：首页、规则扫描、我的观察、我的交易、我的复盘、我的规则、数据与设置。

### 首页

- 「你设置的 N 个观察条件已触发」卡片：代码+名称、条件原文、触发快照（日期、收盘、5日均线、离均线%）。文案固定 **这是事实记录**。最新 CSV 日线只对照，不改写已冻结快照。
- 按钮：查看日线与事实 / 记录我的判断。
- 三个队列数字：待看观察 / 待确认判断 / 监测中心。
- 「今天规则扫描」摘要只数：符合 / 继续跟踪 / 观察 / 排除。符合 ≠ 可开仓。

### 规则扫描

按总闸分组。每只股票写清命中哪条、还缺哪条。RULES 没有试仓/标准仓数字，「靠近」也无量化阈值 → 最高停在 **等待**。

### 我的观察

人工加代码 + 条件。内置「日线收盘重新站上5日均线」「MACD绿柱缩短不创新低」。自定义条件只记录、不自动发明指标。

### 我的交易

手工记账：代码、方向（开仓/加仓/减仓/清仓/记录）、仓位%、原因。

### 我的复盘

按 `journal/TEMPLATE.md` 字段写，保存为 `journal/YYYY-MM-DD.md`。

### 我的规则

只读展示 `RULES.md` / `PROFILE.md`。网站不能改仓位上限。

### 数据与设置

上传 CSV、人工定性大盘（多/空/震荡/未设置）、人是否在场、预留 Tushare。

### 日线与事实

K 线 + MA5/10/20 + MACD(7,28,4) + KDJ。触发日有虚线标记。快照区是确认收盘事实。

## 扫描状态（全站只用这六个词）

`排除` `观察` `等待` `试仓` `标准仓` `禁止`

当前 `RULES.md` 没有试仓/标准仓数字，引擎**不会**把任何股票升到这两档。
