# 个人交易台

这不是荐股系统，是把你自己的交易方式写成可执行文件，让 AI 做筛选和解释，你做否决和仓位。

## 先读
1. `AGENTS.md` 宪法
2. 填 `PROFILE.md`
3. 按自己的数字改 `RULES.md`
4. 把真实漏洞写入 `GAPS.md`
5. 每天用 `journal/TEMPLATE.md` 复盘

## 工具怎么选
- 规则没写清：只用对话模型（Grok ）
- 规则已稳定、要筛选和回测：再上Grok Build

## 目录
```text
my-trading-desk/
  AGENTS.md
  PROFILE.md
  RULES.md
  GAPS.md
  STRATEGY.md
  README.md
  journal/
  prompts/
  data/
```
