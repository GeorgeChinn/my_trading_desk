# GeorgeChin Personal Trade · 本地个人交易空间

把已写进 `PROFILE.md` / `RULES.md` 的个人交易方式做成**只读规则 + 本地筛选 + 手工记账**的网站。  
不做自动成交，不接任何外部账户，不发明新指标。

先读：`AGENTS.md` → `PROFILE.md` → `RULES.md` → 每天用 `journal/TEMPLATE.md` 复盘。

## 这台机器能做什么 / 不能做什么

- 能：按 RULES 总闸把股票分成 排除 / 观察 / 等待 / 买入 / 减仓 / 清仓；冻结观察触发快照；画日线图；手工记账和复盘。
- 不能：改 RULES 仓位数字；把「路径匹配」写成可开仓。买入只表示路径到达，不是成交指令。
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

每日定时除了拉确认收盘，还会**重读 RULES.md**（零轴/低位等已实现的开关）并跑一遍规则扫描。`RULES.md` 不会自己改 Python；新句子若扫描器还不认识，扫描页会提示「尚未实现」。

手工立刻更新：数据与设置 →「现在更新确认收盘」，或：

```powershell
D:\jiaoyi\my-trading-desk\sync_once.cmd
```

## 启动

**方式 A（推荐，两个窗口）**

窗口 1 — 后端（改 `backend/` 下 `.py` 会自动重启）：

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
- 侧栏：首页、规则扫描、规则轨迹、我的观察、我的交易、我的复盘、我的规则、数据与设置。

### 首页

- 「你设置的 N 个观察条件已触发」卡片：代码+名称、条件原文、触发快照（日期、收盘、5日均线、离均线%）。文案固定 **这是事实记录**。最新 CSV 日线只对照，不改写已冻结快照。
- 按钮：查看日线与事实 / 记录我的判断。
- 三个队列数字：待看观察 / 待确认判断 / 监测中心。
- 「今天规则扫描」摘要只数：符合 / 继续跟踪 / 观察 / 排除。符合 ≠ 可开仓。

### 规则轨迹

记录从首次列入买入（确认收盘）到第 7 条卖出条件的价格轨迹。对当前买入池回放历史完整段，给出胜率、平均收益、平均回撤并排名。这是事实记录，不是成交指令。

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

## 扫描状态（总闸）

`排除` → `观察` → `等待` → `买入` → `减仓` / `清仓`

买入只表示 RULES 第 6 条路径到达，不是成交指令。

## 移动端

手机浏览器可直接打开同一地址。窄屏时左上角三条线打开侧栏，K 线高度会缩短，表格可左右滑。建议用 Chrome / Safari，加到主屏幕即可当网页应用。

开发时把电脑和手机放同一 Wi-Fi，在电脑上：

```powershell
cd frontend
npx vite --host
```

终端会给出 `Network: http://192.168.x.x:5173`，手机打开该地址（后端 `python run.py` 需同时开着；若 API 连不上，把 `run.py` 的 `HOST` 设为 `0.0.0.0`）。

---

## 云端部署

把整站打成一个 Docker 镜像：里面是编好的 Vue + FastAPI。确认收盘缓存在磁盘卷 `data/`，北京时间定时器在容器内跑（`TZ=Asia/Shanghai`）。不做自动成交，不接券商。

推荐：**任意一台 2 核 2G 以上的 Linux 云主机 + Docker Compose**（阿里云 / 腾讯云 / 华为云 / 火山引擎轻量应用服务器均可）。下面按 **Ubuntu 22.04** 写到能打开网页。

### 0. 你需要提前准备

1. 一台公网云主机，系统 Ubuntu 22.04，开放安全组：**22**（SSH）。先测通时再开 **8000**；上域名后改开 **80、443**，可关掉 8000。
2. 本机已能 `ssh root@你的公网IP`（或普通用户 + sudo）。
3. 本仓库代码（U 盘拷、Git 拉都可以）。
4. 可选：一个域名，解析 A 记录到该 IP（要 HTTPS 时再用）。

不要把 `data/settings.json`（可能含 Tushare token）提交到公开仓库。

### 1. 登录云主机并安装 Docker

```bash
ssh root@你的公网IP
```

```bash
apt-get update
apt-get install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
docker version
docker compose version
timedatectl set-timezone Asia/Shanghai
```

若权限报错，确认文件属主后重试：`chmod a+r /etc/apt/keyrings/docker.asc`

### 2. 把代码放到云主机

**方式 A：本机已装 Git，云主机也能访问你的仓库**

```bash
mkdir -p /opt
cd /opt
git clone 你的仓库地址 my-trading-desk
cd my-trading-desk
```

**方式 B：从 Windows 打包上传（没有 Git 时）**

在 Windows 资源管理器进入 `D:\jiaoyi\my-trading-desk`，不要包含 `frontend\node_modules`。用 PowerShell：

```powershell
cd D:\jiaoyi
tar --exclude=frontend/node_modules --exclude=frontend/dist --exclude=__pycache__ -cvf desk.tar my-trading-desk
scp desk.tar root@你的公网IP:/opt/
```

云主机上：

```bash
cd /opt
tar -xvf desk.tar
cd my-trading-desk
```

### 3. 构建并启动

```bash
cd /opt/my-trading-desk
mkdir -p data/csv journal
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f --tail=80
```

第一次启动日志里会出现拉新浪/腾讯确认收盘、按 RULES 筛池。等 `Application startup complete` 和健康检查通过。

本机或手机浏览器打开：

```text
http://你的公网IP:8000
```

顶栏应出现「真实行情已连接」和确认收盘日。规则扫描应能列出池子。若打不开：

```bash
# 云厂商控制台 → 安全组，入站放行 TCP 8000
ss -lntp | grep 8000
docker compose logs --tail=200
```

容器内定时：交易日 **15:40、16:30 北京时间** 拉确认收盘并重读 `RULES.md` 已实现开关。

### 4. 改规则、看扫描（云端）

```bash
nano /opt/my-trading-desk/RULES.md
# 保存后刷新网页「规则扫描」
# 已实现的开关（零轴、低位等）会重读；新句子若提示尚未实现，需要改 scanner.py 再重新构建镜像
```

`RULES.md` 已通过 compose 挂进容器，改文件不必重建镜像。改 Python 必须：

```bash
cd /opt/my-trading-desk
docker compose build
docker compose up -d
```

### 5. 域名和 HTTPS（可选，推荐 Caddy）

域名 `trade.example.com` 的 A 记录指向云主机 IP，等解析生效。

```bash
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get install -y caddy
```

编辑 `/etc/caddy/Caddyfile`：

```caddy
trade.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
```

仓库里有一份底稿：`deploy/Caddyfile.example`。

然后把 compose 改成只在本机监听，避免 8000 裸奔：

编辑 `docker-compose.yml` 的 `ports` 为：

```yaml
    ports:
      - "127.0.0.1:8000:8000"
```

```bash
docker compose up -d
systemctl enable --now caddy
systemctl reload caddy
```

浏览器打开 `https://trade.example.com`。证书由 Caddy 自动申请。安全组放行 **80、443**，可删掉 **8000**。

可选：给网页加口令（仍不是券商登录）。在 Caddyfile 的 `reverse_proxy` 前加：

```caddy
    basicauth {
        George $2a$14$把这个换成caddy_hash_password生成的密文
    }
```

生成密文：`caddy hash-password`

### 6. 日常运维

```bash
cd /opt/my-trading-desk

# 看日志
docker compose logs -f --tail=100

# 停 / 开
docker compose stop
docker compose up -d

# 备份数据（规则、池子、日线缓存、复盘、手工记账）
tar -czf ~/desk-backup-$(date +%F).tgz data journal RULES.md PROFILE.md

# 更新代码（Git）
git pull
docker compose build
docker compose up -d
```

### 7. 安全注意

- 这是个人观察台，不是交易通道。不要把 Tushare token 提交到公开 Git。
- 公网 IP 直开 8000 仅供自己试用；长期使用请走第 5 步 HTTPS + 口令或 VPN。
- 不要在云主机上存实盘密码、不要接券商。

相关文件：`Dockerfile`、`docker-compose.yml`、`deploy/Caddyfile.example`。
