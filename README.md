# stock-research-hub

投研报告库 + 研究跟踪数据库。存放 ai-berkshire 各 skill（investment-team / earnings-review / ...）生成的公司研究报告，并用 SQLite 记录"哪天对哪家公司跑了什么 skill"。

## 目录约定

```
报告_<skill名>/    ← 该 skill 生成的报告都放这里（没有就新建）
```

| 目录 | 内容 |
|------|------|
| `报告_investment-team/` | investment-team 四角色报告（已含 2026-08-07/08 的 21 份） |
| `报告_earnings-review/` | earnings-review 财报精读报告（预留） |

## 报告命名约定

新报告一律命名：**`股票名_skill名_研究报告_YYYYMMDD.md`**

```
CEG_earnings-review_研究报告_20260808.md
CEG_investment-team_研究报告_20260808.md
```

遗留旧命名（investment-team 已生成的）：`CEG投资研究报告_20260808.md`（skill 由所在文件夹推断）。

非单公司文件（汇总表、组合评估等）以 `_研究报告_` / `投资研究报告_` 区分，不会被登记。

## SQLite 数据库（research.db）

### companies — 公司基础财务信息

| 字段 | 说明 |
|------|------|
| ticker | 股票代码（如 CEG / Metaplanet） |
| company_name | 公司全名 |
| market_cap_b | 市值（十亿美元），10 = 100亿美元（市值≥100亿为目标范围，低市值标的保留以便跟踪） |
| market_cap_date | 市值数据日期 YYYYMMDD |

### skill_reports — 每(公司, skill)一行，只保留该 skill 的最新报告

| 字段 | 说明 |
|------|------|
| company_id | → companies.id |
| skill | skill 名：`investment-team` / `earnings-review` / ... |
| latest_report_date | 该 skill 对该公司的**最新**报告日期（同 skill 多份时取最大，如 20251112 与 20260808 并存取 20260808） |
| report_path | 最新报告相对路径 |

## 使用

```bash
python3 scripts/init_db.py           # 建表 + 种子市值数据（幂等，可重复跑更新市值）
python3 scripts/ingest_reports.py    # 扫描所有 报告_* 目录 → 登记到 skill_reports（自动取最新）

# 查询示例
sqlite3 research.db "SELECT * FROM companies WHERE market_cap_b >= 10;"
sqlite3 research.db "SELECT c.ticker, s.skill, s.latest_report_date FROM skill_reports s JOIN companies c ON c.id=s.company_id;"
```

## 工作流

1. 用某 skill 生成报告 → 存到 `报告_<skill名>/`，文件名 `股票名_skill名_研究报告_日期.md`
2. 跑 `python3 scripts/ingest_reports.py` → 自动登记，同 skill 新报告会覆盖旧的（取最新日期）
