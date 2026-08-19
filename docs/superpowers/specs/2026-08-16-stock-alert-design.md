# Stock Alert 股价提醒系统 — 设计文档

- 日期：2026-08-16（2026-08-19 修订）
- 项目：`/home/cc/projects/stock-research-hub/alert/`（代码），GCP 项目 `stock-alert-hub`
- 状态：已全量部署上线

---

## 1. 背景与动机

监控 ~372 只股票（184 家有研究目标价 + 173 家 >$100B 大盘 + 用户关注的 16 只小盘），现价跌破"买入目标价"时推 Discord 提醒。用户是价值投资者（美股为主），白天/夜间不同时段活跃，需要自动化价格监控替代手动盯盘。

**数据源已验证**：`yfinance`（Yahoo Finance Python 库，crumb 认证绕过 401）——提供现价、分析师共识目标价（`analyst_price_targets.mean`）、历史行情（算均线）。免费、无 API key。**注意**：Yahoo 会封数据中心 IP，GCP 上需实测；备选 Finnhub 免费 API key。

---

## 2. 目标与非目标

**目标：**
1. 372 只全部监控，现价 < 目标价 → Discord 提醒（7 天冷却防重复）
2. 目标价分来源：
   - `skill`：**报告 (earnings-review + investment-research 双报告)** 得到的买入区
   - `script`：**脚本计算**（`min(共识×0.75, 200MA, 50MA×0.95)`）
   - `manual`：**报告 (研报复核确认/手动指定)**
   - `none`：无目标价
3. 推送时明确标明判断条件来源（若脚本计算标明“脚本计算”，若双报告得出标明“报告”）
4. 推送窗口：美股开盘前 30 分 → 24:00 JST（夏时制感知）；收盘推一次当日总结
5. 永久免费（GCP Always Free，复用 serverless 模式）

**非目标：**
- 不做交易/下单（纯提醒）
- 不监控日股（暂）
- 不做深度研报（目标价用脚本启发式，可后续升级为 skill）

---

## 3. 监控范围（372 只）

| 来源 | 数量 | 目标价与推送判定说明 |
|---|---|---|
| 184 家研究清单（`184家_行业结论对照总表`） | 184 | `skill` / `manual`：**报告 (earnings-review + investment-research 双报告买入区)**（159 家自动解析 + 24 家研报原文复核确认） |
| >$100B 大盘（`research.db` companies 表 market_cap_b>100） | 173（全部不在 184 里） | `script`：**脚本计算**（`min(共识×0.75, 200MA, 50MA×0.95)` 批量生成） |
| 用户关注 16 只：ldos/zeta/rxrx/upst/clne/bbai/eu/bwxt/nok/ceg/alb/path/crwv/hood/pypl/coin | 16 | `script`：**脚本计算**批量生成，用户可在 overrides 覆盖 |
| **合计（去重后）** | **372** | |

用户以后还会加股票（`watchlist_additions.yaml` / `overrides.yaml`），系统合并去重。

---

## 4. 架构（serverless，三模块解耦）

**核心解耦原则：三个独立模块通过 Firestore 通信，互不依赖。** 目标价生成逻辑以后随便改，监控模块无需改动（它只从 Firestore 读目标价）。目标价存 Firestore（不是 git 文件）——周更/改 watchlist 都不用重新部署监控函数。

```
┌─ build_watchlist.py（一次性/加股时）─────────────────┐
│  从 184 CSV + research.db(>100B) + additions + overrides│
│  → 写 Firestore: 每只 {ticker, target, source}         │
└──────────────┬─────────────────────────────────────────┘
               ▼ Firestore: stocks/{ticker} {target, source, last_price, last_notified, updated_at}
┌─ gen_targets.py（每周六）──────────────────────────────┐
│  重算所有 source=script 的目标价 → 更新 Firestore        │
│  (skill/manual 不动; script vs skill 偏离>30% 标记)      │
└──────────────┬─────────────────────────────────────────┘
               ▼
┌─ monitor.py（每30分/收盘）─────────────────────────────┐
│  读 Firestore 全部 → yfinance 批量拉价                   │
│  现价<目标价 且 7天未推 → Discord (带明确判断条件来源)     │
│  收盘 → 当日总结 (带明确判断条件来源)                      │
└────────────────────────────────────────────────────────┘
```

### 文件结构（`stock-research-hub/alert/`）
```
alert/
├── main.py               # Cloud Functions 入口汇聚
├── monitor.py            # 每日监控(Cloud Functions, 独立部署)
├── gen_targets.py        # 周更目标价(Cloud Functions, 独立部署)
├── build_watchlist.py    # 初始化/加股时写入 Firestore(脚本)
├── watchlist_additions.yaml  # 用户手动加股(以后去重合并)
├── overrides.yaml        # 用户覆盖目标价/歧义修正
├── firestore_store.py    # Firestore 读写封装(共用存储层)
├── deploy.sh             # 构建 + 部署函数与调度器
├── requirements.txt      # 依赖声明
└── tests/                # 全量单元测试
```

### Scheduler（Asia/Tokyo）
| Job | 触发 | 函数 | 逻辑 |
|---|---|---|---|
| `monitor-window` | 每 30 分 21:00-23:59 (周一至周五) | monitor | 仅[开盘-30分, 24:00]推送(夏时制感知) |
| `monitor-close` | 05:00 & 06:00 (周二至周六) | monitor | 收盘总结(按日期去重) |
| `gen-targets` | 周六 08:00 | gen_targets | 重算 script 目标价 → Firestore |

---

## 5. 目标价来源与公式

### source 字段（区分可信度与推送说明）
| source | 来源定义 | 推送时判定条件说明 | 优先级 |
|---|---|---|---|
| `skill` | 184 家研究买入区解析 | `报告 (earnings-review + investment-research 双报告)` | 最高 |
| `script` | 批量启发式公式 | `脚本计算 (min(共识×0.75, 200MA, 50MA×0.95))` | 中（占位，可升级） |
| `manual` | 研报原文复核 / overrides.yaml 手填 | `报告 (研报复核确认)` | 最高覆盖 |
| `none` | 无目标价（只监控不提醒） | — | — |

### 批量脚本公式（`gen_targets.py`）
```
目标价 = min(
    分析师共识目标价 × 0.75,   # 安全边际
    200日均线,                 # 长期支撑
    50日均线 × 0.95            # 短期支撑略下
)
（任一数据缺失 → 用其余；全缺 → source=none）
```

### 184 家解析与复核（`build_watchlist.py`）
- 干净区间（159 家）：取**高位**为触发价，source=skill
- 歧义 24 家：经 earnings-review + investment-research 原文逐篇复核填入 overrides.yaml，source=manual
- 大盘 173 家 + 关注 16 家：source=script

---

## 6. 提醒逻辑与推送文案格式

- **触发条件**：现价 < 目标价 且 7 天内未推送
- **推送窗口**：美股开盘前 30 分 → 24:00 JST。函数计算当日美股开盘（夏令时 EDT 22:30 JST 开盘，冬令时 EST 23:30 JST），仅在窗口内推送
- **Discord 实时告警文案格式**：
  ```markdown
  **🐂 2 只股票触及买入区**
  • **AAPL**: 现价 `$240.50` < 目标 `$244.27`
    └ 判断条件: 脚本计算
  • **TME**: 现价 `$7.20` < 目标 `$7.50`
    └ 判断条件: 报告 (earnings-review + investment-research 双报告)
  ```
- **收盘总结文案格式**：
  ```markdown
  **📊 美股收盘总结 2026-08-19**
  【🎯 触及买入区】
  • **AAPL**: 现价 `$240.50` < 目标 `$244.27` (脚本计算)
  • **TME**: 现价 `$7.20` < 目标 `$7.50` (报告 (earnings-review + investment-research 双报告))

  【👀 接近买入区 (偏离 <= 5%)】
  • **MSFT**: 现价 `$395.00` / 目标 `$390.00` (偏离 +1.3%, 报告 (earnings-review + investment-research 双报告))
  ```

---

## 7. 边界与错误处理

| 场景 | 行为 |
|---|---|
| Yahoo 封 GCP IP（yfinance 失败） | 重试退避；连续失败 → 换 Finnhub（需免费 API key）或告警 |
| 单只无数据 | 跳过该只，记录日志，不中断整轮 |
| 目标价缺失（none） | 只记录现价，不触发提醒 |
| Firestore 读写失败 | 函数异常退出，下次调度重试；不重复推送靠 last_notified 持久化 |
| 重复收盘总结 | Firestore 记 `last_summary_date`，同一天只推一次 |

---

## 8. 部署与上线信息

- GCP 项目 `stock-alert-hub`（独立项目，区域 `asia-northeast1`）
- Webhook 存 Secret Manager `stock-discord-webhook`
- Cloud Functions: `stock-monitor`, `stock-close`, `stock-gen-targets`
- Cloud Scheduler: `monitor-window`, `monitor-close`, `gen-targets`
- 永久免费（GCP Always Free）
