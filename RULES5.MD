# RULES5  利弗莫尔领先股 × 威科夫 SOS/LPS × A股可执行

版本：5.2（校准 8 只：华脉 603042 / 中百 000759 / 红棉 000523 / 华正 603186 / 瑞尔特 002790 / 精华 002349 / 华康 605077 / 安德利 605198 @ 2026-09-07）

来源：《股票大作手回忆录》《威科夫操盘法》+ 上述 8 只当日买入信号倒推规则。

核心：

- 利弗莫尔：看领先集团里的领先股，可同时多条主线；看盘面强度，不把指数均线当死闸。
- 威科夫：同一天可以既买 SOS（过溪）也买 LPS（回踩）。
- A股：T-1 定池，T 日出信号；封死买不到是成交问题，不把信号作废。

时序：

- T-1 15:00  → POOL_FINAL（观察池，≤20）
- T  09:30–14:30 → **只在该池内**判 BUY_A / BUY_B（信号日=T）
- T  13:30–14:30 → B 类执行窗口；A 类未封死也可在此买
- T  14:30 后   → 禁止新开
- 13:30 无快照时，回测用当日已发生的分钟/分时截至该时点；禁止用收盘价假装 13:30 买到涨停。

信号与成交分离：

- 信号成立但 13:30 前已封死 / 一字 → 记 TRIGGER_UNFILLED，不打板、不追。
- 炸板后收盘仍满足 A → 信号成立，打开后买，不在板上追。

------------------------------------------------------------

## 0. 参数

P = dict(
    # 环境
    idx_ma          = 20,
    zt_sos_min      = 40,
    seal_sos_min    = 0.60,
    cyb_sos_pct     = 2.0,      # 创业板指数当日涨幅 %（只作 E_sos，不买创业板个股）
    up_share_off    = 0.35,
    dt_off_min      = 15,
    idx_break       = 0.98,     # MA20 × 此值以下算加速破位
    zt_weak         = 50,
    seal_weak       = 0.55,

    # 可买范围（硬锁，池与信号共用）
    buyable_prefix  = ("000", "001", "002", "003", "600", "601", "603", "605"),

    # 池
    min_price       = 3.0,
    mcap_min        = 20,       # 亿，流通
    mcap_max        = 800,
    list_days       = 60,
    min_bars        = 40,
    ma_band         = 0.97,     # T-1 收盘 ≥ MA20 × 0.97（0.99 会杀掉中百/华正）
    dd60_max        = 0.40,
    turn3_max       = 0.25,
    streak_pool_ban = 3,        # 已 3 连板不进新开池
    pool_max        = 20,
    theme_max       = 3,

    # 主题
    th1_min         = 3,
    th3_min         = 5,

    # BUY_A SOS
    a_pct_min       = 6.0,
    a_vol_ratio     = 1.2,      # 涨停则跳过此条（华正 9/7 量比 1.02）

    a_close_loc     = 1/3,      # 收盘在当日振幅上 1/3

    # BUY_B LPS
    b_vol_ratio_max = 0.85,
    b_turn_ratio    = 0.85,     # 当日换手 ≤ 近 5 日均换手 × 0.85
    b_pct_lo        = -3.0,
    b_pct_hi        = 3.0,
    b1_dd20_lo      = 0.03,
    b1_dd20_hi      = 0.18,
    b2_dd20_max     = 0.02,
    b2_pct_lo       = 0.5,
    b2_pct_hi       = 4.0,

    # 否决
    turn_climax     = 0.30,
    b_break_vol     = 1.3,

    # 仓位 / 卖出
    a_size          = 0.10,     # 8–12% 取中
    b_size          = 0.07,
    max_pos         = 0.40,
    max_names       = 5,
    max_per_theme   = 2,
    weak_mult       = 0.50,
    stop_ma         = 0.97,     # 收盘跌破 MA20×0.97 且放量 → 出
    giveback        = 0.08,     # 从持仓最高回撤 8%
)

指数：上证 sh000001，创业板 sz399006（环境用，个股不买）。
主题：用申万二级（desk events.industry）。
量比：当日成交量 / 前 5 日均量（不含当日）。
连板：相对昨收达到涨停幅度（可买范围主板 10%）的连续天数。

------------------------------------------------------------

## 0.1 可买范围（硬锁）

代码 6 位数字前缀必须落在：

`000 / 001 / 002 / 003 / 600 / 601 / 603 / 605`

全程排除（池、信号、回测一律不得进入）：

- `300` 创业板
- `688` 科创板
- `8` / `4` 北证
- ST / *ST（名称含 ST）

前缀不在白名单 = 不可买，不再用别的条件救回来。
环境仍看创业板指数（E_sos），那是盘面，不是个股池。

------------------------------------------------------------

## 1. 环境（T 日判定，否则新开排除）

E_index = (上证收盘 ≥ 上证MA20) or (创业板收盘 ≥ 创业板MA20)

E_sos   = (涨停家数 ≥ P.zt_sos_min and 封板率 ≥ P.seal_sos_min and 上涨家数 > 下跌家数)
          or (创业板当日涨幅 ≥ P.cyb_sos_pct and 上涨家数 > 下跌家数)

ENV_OFF = (上涨家数 / (上涨+下跌) < P.up_share_off
           and 跌停家数 ≥ P.dt_off_min
           and 上证收盘 < 上证MA20 × P.idx_break
           and 创业板收盘 < 创业板MA20 × P.idx_break)

ENV_OK   = (E_index or E_sos) and not ENV_OFF
ENV_WEAK = ENV_OK and (涨停家数 < P.zt_weak or 封板率 < P.seal_weak)
ENV_OFF  其余。

含义：指数略低于 MA20 但盘面 SOS（9/7 创业板 +3.4%、涨停 93、封板 83%）→ ENV_OK。

**禁止用「两条指数都必须站上 MA20」一票否决。**
**ENV_OK 只要求 E_index 或 E_sos 其一，不是双均线 AND。**

盘中（13:30）用当时快照算同一套；没有 13:30 快照不得用收盘回填。

------------------------------------------------------------

## 2. 主题（可多条，当天可点火）

live(theme, T) = (当日该主题涨停数 ≥ P.th1_min)
                 or (T-2..T 累计涨停 ≥ P.th3_min)

- 同时最多跟踪 P.theme_max 条活主题（默认 **3**），按当日涨停数 × 封板质量排序。
- **不做「只准 1 条」**。
- **不要求 T-1 已经活**。当天可点火。9/7 元件从 0 到 14，当天点火合法。
- BUY_A：live(theme) **或** 个股自身满足 SOS（红棉综合Ⅱ当天只有 1 只涨停，仍算 A）。
- BUY_B：不要求主题活，只要求 ENV_OK。

------------------------------------------------------------

## 3. 池子（T-1 观察池，不在池 → T 日不得新开）

硬条件（T-1 收盘）：

1. 可买范围（§0.1）；非 ST、非创业板、非科创、非北证；上市 ≥ 60 日、日线 ≥ 40 根、成交量 > 0
2. 收盘价 ≥ 3 元
3. 流通市值 20–800 亿
4. 收盘 ≥ MA20 × **0.97**
5. 距近 60 日高回撤 ≤ 40%
6. 近 3 日换手均值 ≤ 25%
7. 已 3 连板及以上 → 不进新开池（持仓继续管）
8. T-1 已涨停且换手 > 18% → **次日不得新开**（防止 9/8 追 9/7 的一字；不阻止 9/7 当天的 A）

排序：距 MA20 近、60 日回撤小、T-1 缩量优先。**POOL_FINAL ≤ 20 只。**
不按「必须属于昨天已活主题」过滤。8 只校准票 9/4 全部入池。

13:30 草稿用当时价套同一套阈值；终版 = 收盘仍过池者。
**不因炸板自动踢出**（红棉要留到 T 日用收盘形态判 A）。

------------------------------------------------------------

## 4. T 日买入

前提：代码 ∈ T-1 POOL_FINAL，且当日 ENV_OK，且前缀 ∈ 可买范围。

### BUY_A  SOS / 过溪（利弗莫尔突破 + 威科夫 Sign of Strength）

全部满足：

- 涨幅 ≥ 6%  **或** 涨停
- 量能：量比 ≥ 1.2  **或 当日涨停**（涨停视为量能满足）
- 收盘位于当日 [low, high] 上 1/3： close ≥ high - (high-low)/3
- 所属主题 live **或** 仅用本条 SOS 自身（涨幅≥6% 或涨停，且量能过）

**涨停不得再用 量比 ≥ 1.2 卡掉。** 华正 603186 在 2026-09-07 涨停、量比 1.02，必须记 BUY_A。

9/7 命中：华脉、中百、华正、红棉。

执行：

- 13:30 仍未封 / 已打开 → 13:30–14:30 买
- 13:30 前已封死 → TRIGGER_UNFILLED，禁止打板
- 炸板但收盘仍过 A → 信号成立，打开后买

### BUY_B  LPS / 回踩或爬升（威科夫 Last Point of Support）

共同：

- 收盘 ≥ MA20
- 量比 ≤ 0.85  **或** 当日换手 ≤ 近 5 日均换手 × 0.85

再满足其一：

B1 回踩

- 距近 20 日高 3%–18%
- 当日涨跌幅 ∈ [−3%, +3%]

B2 爬升

- 距近 20 日高 ≤ 2%
- 当日涨幅 ∈ [+0.5%, +4%]

9/7 命中：瑞尔特 B1、精华 B1、安德利 B1、华康 B2。

执行：13:30 附近买。B 类几乎总能成交。

同一只同一天 A 优先于 B。已持仓不加出新的 A/B 标签，只走加仓/减仓。

------------------------------------------------------------

## 5. 否决（新开）

- ENV_OFF
- 不在 T-1 POOL_FINAL
- 前缀不在可买范围（300 / 688 / 8 / 4 / 非白名单）
- ST / *ST
- 已 3 连板及以上
- 当日换手 > 30% 的高潮板
- B 类：收盘 < MA20×0.97 **且** 量比 ≥ 1.3（回踩失败，当天不作 B）
- 14:30 之后
- 同一主题已有 2 只新开仓

明确 **不再否决**：

- 指数双双略低于 MA20（有 E_sos 即可；禁止双均线一票否决）
- 主题昨天没活（当天可点火）
- 同时最多 3 条主线
- 早盘封死（只影响成交，不影响信号）
- 炸板但收盘仍强
- 涨停但量比 < 1.2（涨停 = 量能满足）

------------------------------------------------------------

## 6. 观察

已在 POOL_FINAL，ENV 不是 OFF，当天尚未触发 A/B。
次日开盘仍用该池，直到 T 日收盘重算新池。

------------------------------------------------------------

## 7. 仓位

size = A ? P.a_size : P.b_size
if ENV_WEAK: size *= P.weak_mult
单只上限 12%；总仓 ≤ 40%；同时最多 5 只；单主题最多 2 只。
TRIGGER_UNFILLED 不占仓。

加仓（T+1 及以后，仅已成交仓）：

- 次日高开不封死、回踩分时均价不破昨收 → 可加 1 次，加仓量 ≤ 原仓 50%
- 3 连板只管理、不再加

------------------------------------------------------------

## 8. 卖出

任一即出（收盘或盘中触发，盘中优先）：

1. 收盘 < MA20 × 0.97 且 量比 ≥ 1.3
2. 从持仓最高价回撤 ≥ 8%
3. 涨停打开超过 30 分钟不回封，且跌回分时均价下
4. 所属主题当日涨停数较峰值腰斩，且该股量比 ≥ 1.5 收阴
5. ENV_OFF 当日：B 类全出；A 类若未封死减半，次日再论

破 MA20 的 spring 失败 = 威科夫 invalidation，不扛。

------------------------------------------------------------

## 9. 伪代码

def buyable(stk):
    p = prefix6(stk.code)          # 前 3 位
    if p not in ("000", "001", "002", "003", "600", "601", "603", "605"):
        return False
    if st(stk) or cyb(stk) or kcb(stk) or bj(stk):
        return False               # 300 / 688 / 8* / 4* / ST
    return True

def env_state(T, snap):
    e_index = (sh.close >= sh.ma20) or (cyb.close >= cyb.ma20)
    e_sos   = ((snap.zt >= 40 and snap.seal >= 0.60 and snap.up > snap.down)
               or (cyb.pct >= 2.0 and snap.up > snap.down))
    e_off   = (snap.up/(snap.up+snap.down) < 0.35 and snap.dt >= 15
               and sh.close < sh.ma20*0.98 and cyb.close < cyb.ma20*0.98)
    if e_off: return "OFF"
    if not (e_index or e_sos): return "OFF"   # 禁止改成 e_index AND 双均线
    if snap.zt < 50 or snap.seal < 0.55: return "WEAK"
    return "OK"

def live(theme, T):
    return zt_count(theme,T) >= 3 or sum(zt_count(theme,d) for d in last3(T)) >= 5

def in_pool(stk, T_1):
    return (buyable(stk) and listed(stk) >= 60 and bars(stk) >= 40
            and stk.close >= 3 and 20 <= stk.float_mcap_yi <= 800
            and stk.close >= stk.ma20 * 0.97
            and (stk.high60 - stk.close)/stk.high60 <= 0.40
            and mean(stk.turnover[-3:]) <= 0.25
            and stk.streak < 3
            and not (stk.limit_up and stk.turnover > 0.18))  # T-1 高潮板次日不新开

def loc_upper_third(stk):
    return stk.close >= stk.high - (stk.high - stk.low)/3.0

def vol_ok_A(stk):
    # 涨停视为量能满足（华正 9/7 量比 1.02 不得掉）
    return stk.limit_up or stk.vol_ratio >= 1.2

def trigger_A(stk, T):
    return (in_pool(stk, T-1) and env_state(T) != "OFF"
            and (stk.pct >= 6 or stk.limit_up)
            and vol_ok_A(stk)
            and loc_upper_third(stk)
            and (live(stk.theme,T) or (stk.pct >= 6 or stk.limit_up) and vol_ok_A(stk)))

def trigger_B(stk, T):
    if not in_pool(stk, T-1) or env_state(T) == "OFF": return False
    if stk.close < stk.ma20: return False
    if not (stk.vol_ratio <= 0.85 or stk.turnover <= mean(stk.turnover[-5:])*0.85):
        return False
    dd20 = (stk.high20 - stk.close)/stk.high20
    b1 = 0.03 <= dd20 <= 0.18 and -3 <= stk.pct <= 3
    b2 = dd20 <= 0.02 and 0.5 <= stk.pct <= 4
    if stk.close < stk.ma20*0.97 and stk.vol_ratio >= 1.3: return False
    return b1 or b2

# 回测 / 扫描：按交易日，只对 T-1 POOL_FINAL 判信号
# 禁止对全市场 ~5000 只逐日 walk。
for T in trading_days:
    pool = rank(filter(in_pool, buyable_universe()), key=pool_sort)[:20]   # POOL_FINAL
    if env_state(T) == "OFF":
        continue
    for stk in pool:                          # ≤20，不是 5000
        if trigger_A(stk, T):
            signal(stk, "BUY_A")
            fill = buy_if_not_sealed(stk, window="13:30-14:30")  # 封死 → UNFILLED
        elif trigger_B(stk, T):
            signal(stk, "BUY_B")
            fill = buy(stk, window="13:30-14:30")

------------------------------------------------------------

## 10. 校准（必须回归）

T = 2026-09-07，T-1 = 2026-09-04。
ENV = OK（E_sos：涨停 93 / 封板 83% / 创业板 +3.41%；**不是**双均线一票否决）。
8 只 9/4 均过池（MA20×0.97）。9/7 正推必须 **8/8**：

| 代码   | 名称     | 信号   | 卡点备注 |
|--------|----------|--------|----------|
| 603042 | 华脉科技 | BUY_A  |          |
| 000759 | 中百集团 | BUY_A  | 池用 MA20×0.97 |
| 603186 | 华正新材 | BUY_A  | 涨停，量比 1.02 → 量能满足 |
| 000523 | 红棉股份 | BUY_A  | 当天点火；炸板不踢池 |
| 002790 | 瑞尔特   | BUY_B1 | 量比≤0.85 或 换手≤5 日均×0.85 |
| 002349 | 精华制药 | BUY_B1 | 同上 |
| 605198 | 安德利   | BUY_B1 | 同上 |
| 605077 | 华康股份 | BUY_B2 | 同上 |

改参数若导致 8 只缺 1，视为规则回归失败，**不得合并**进主分支。

------------------------------------------------------------

## 11. 回测约束（防马后炮、防全市场扫）

- **禁止对全市场 ~5000 只逐日 walk。**
- 每个交易日：先用 T-1 收盘在可买范围内生成 POOL_FINAL（≤20），**T 日只对该池判** BUY_A / BUY_B。
- 可买范围先锁前缀（§0.1）；300 / 688 / 8 / 4 / ST 不得进入 walk。
- T 日信号只用 ≤T 当日当时点数据。
- 13:30 判定不得偷看 15:00 收盘价。
- 已封死的 A 记 UNFILLED，成交价不得填涨停价。
- 池只用 T-1 收盘，不得用 T 日涨停反推「早该进池」。
- 规则回测名单 = 各日 POOL_FINAL 的并集（量级几十只），不是 scan 全 A 留下的观察/试仓名单去逐根日线扫 5000。
