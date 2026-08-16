# Stock Alert 股价提醒系统 — 设计文档

- 日期：2026-08-16
- 项目：`/home/cc/projects/stock-research-hub/alert/`（代码），GCP 项目 `stock-alert`
- 状态：设计待用户确认

---

## 1. 背景与动机

监控 ~357 只股票（184 家有研究目标价 + 173 家 >$100B 大盘 + 用户关注的 16 只小盘），现价跌破"买入目标价"时推 Discord 提醒。用户是价值投资者（美股为主），白天/夜间不同时段活跃，需要自动化价格监控替代手动盯盘。

**数据源已验证**：`yfinance`（Yahoo Finance Python 库，crumb 认证绕过 401）——提供现价、分析师共识目标价（`analyst_price_targets.mean`）、历史行情（算均线）。免费、无 API key。**注意**：Yahoo 会封数据中心 IP，GCP 上需实测；备选 Finnhub 免费 API key。

---

## 2. 目标与非目标

**目标：**
1. 357 只全部监控，现价 < 目标价 → Discord 提醒（7 天冷却防重复）
2. 目标价分来源：`skill`（184 家研究买入区）/ `script`（批量启发式）/ `manual`（用户覆盖）/ `none`
3. 推送窗口：美股开盘前 30 分 → 24:00 JST（夏时制感知）；收盘推一次当日总结
4. 永久免费（GCP Always Free，复用 danchi 的 serverless 模式）

**非目标：**
- 不做交易/下单（纯提醒）
- 不监控日股（暂）
- 不做深度研报（目标价用脚本启发式，可后续升级为 skill）

---

## 3. 监控范围（~357 只）

| 来源 | 数量 | 目标价 |
|---|---|---|
| 184 家研究清单（`184家_行业结论对照总表`） | 184 | `skill`（买入区高位；160 可自动解析，15 家歧义人工确认，10 家空跳过） |
| >$100B 大盘（`research.db` companies 表 market_cap_b>100） | 173（全部不在 184 里） | `script` 批量生成 |
| 用户关注 16 只：ldos/zeta/rxrx/upst/clne/bbai/eu/bwxt/nok/ceg/alb/path/crwv/hood/pypl/coin | 16 | `script` 批量生成，用户可覆盖 |
| **合计（去重后）** | **~357** | |

用户以后还会加股票（`watchlist_additions.yaml`），系统合并去重。

---

## 4. 架构（serverless，三模块解耦）

**核心解耦原则：三个独立模块通过 Firestore 通信，互不依赖。** 目标价生成逻辑以后随便改，监控模块无需改动（它只从 Firestore 读目标价）。目标价存 Firestore（不是 git 文件）——周更/改 watchlist 都不用重新部署监控函数。

```
┌─ build_watchlist.py（一次性/加股时）─────────────┐
│  从 184 CSV + research.db(>100B) + additions.yaml │
│  → 写 Firestore: 每只 {ticker, target, source}    │
└──────────────┬────────────────────────────────────┘
               ▼ Firestore: stocks/{ticker} {target, source, last_price, last_notified, updated_at}
┌─ gen_targets.py（每周六）─────────────────────────┐
│  重算所有 source=script 的目标价 → 更新 Firestore   │
│  (skill/manual 不动; script vs skill 偏离>30% 标记) │
└──────────────┬────────────────────────────────────┘
               ▼
┌─ monitor.py（每30分/收盘）────────────────────────┐
│  读 Firestore 全部 → yfinance 批量拉价              │
│  现价<目标价 且 7天未推 → Discord                   │
│  收盘 → 当日总结                                   │
└───────────────────────────────────────────────────┘
```

### 文件结构（`stock-research-hub/alert/`）
```
alert/
├── monitor.py            # 每日监控(Cloud Functions, 独立部署)
├── gen_targets.py        # 周更目标价(Cloud Functions, 独立部署)
├── build_watchlist.py    # 初始化/加股时写入 Firestore(脚本)
├── watchlist_additions.yaml  # 用户手动加股(以后去重合并)
├── overrides.yaml        # 用户覆盖目标价/歧义修正
├── firestore_store.py    # Firestore 读写封装(两函数共用)
├── deploy.sh             # 构建 + 部署两个函数
├── requirements.txt      # yfinance, google-cloud-firestore, google-cloud-secret-manager
└── tests/
```

### Scheduler（Asia/Tokyo）
| Job | 触发 | 函数 | 逻辑 |
|---|---|---|---|
| `monitor-window` | 每 30 分 21:00-23:59 | monitor | 仅[开盘-30分, 24:00]推送(夏时制感知) |
| `monitor-close` | 05:00 & 06:00 | monitor | 收盘总结(按日期去重) |
| `gen-targets` | 周六 08:00 | gen_targets | 重算 script 目标价 → Firestore |

---

## 5. 目标价来源与公式

### source 字段（区分可信度）
| source | 来源 | 优先级 |
|---|---|---|
| `skill` | 184 家研究买入区解析（160 自动 + 15 人工确认） | 最高 |
| `script` | 批量启发式（下方公式） | 中（占位，可升级） |
| `manual` | 用户 overrides.yaml 手填 | 最高覆盖 |
| `none` | 无目标价（只监控不提醒） | — |

### 批量脚本公式（`gen_targets.py`）
```
目标价 = min(
    分析师共识目标价 × 0.75,   # 安全边际
    200日均线,                 # 长期支撑
    50日均线 × 0.95            # 短期支撑略下
)
（任一数据缺失 → 用其余；全缺 → source=none）
```

### 184 家解析（`build_watchlist.py`）
- 干净区间（160 家）：取**高位**为触发价，source=skill
- 歧义 15 家（信号/错乱/日期）：生成 `buy_zones_review.md` 清单 → 用户确认后写进 overrides
- 空 10 家：跳过

### 目标价周更（`gen_targets.py`，每周六）
- 目标价存 **Firestore**（非 git 文件）→ 周更不需重新部署监控函数
- **周更只重算 `source=script` 的目标价**（均线/共识价会漂移）；`skill`/`manual` 不动
- 每只记 `updated_at`（你能看到目标价新鲜度）
- **偏离标记**：同一只若 `script` 目标价与 `skill` 目标价**偏离 >30%** → 在收盘总结里标出（提示"脚本估值和研究结论差很多，值得重跑 skill"）
- 周更公式不变：`min(共识×0.75, 200MA, 50MA×0.95)`；**公式改动只改 gen_targets，monitor 无感知**（解耦）

---

## 6. 提醒逻辑

- **触发**：现价 < 目标价 → 推 Discord（`🐂 {ticker} {现价} < 目标 {目标价} ({source})`）
- **冷却**：每只记录 `last_notified`；**7 天内不重复推**（用户决策）
- **推送窗口**：美股开盘前 30 分 → 24:00 JST。函数计算当日美股开盘（夏时制：3月第2周日-11月第1周日为 EDT 22:30 JST 开盘，否则 EST 23:30 JST），仅在窗口内推送
- **收盘总结**：US 收盘（夏季 05:00 / 冬季 06:00 JST）推一次当日汇总（按日期去重）：当日触发清单 + 接近目标（现价 ≤ 目标×1.05）的股票按偏离排序

---

## 7. 边界与错误处理

| 场景 | 行为 |
|---|---|
| Yahoo 封 GCP IP（yfinance 失败） | 重试退避；连续失败 → 换 Finnhub（需免费 API key）或告警 |
| 单只无数据 | 跳过该只，记录日志，不中断整轮 |
| 目标价缺失（none） | 只记录现价，不触发提醒 |
| Firestore 读写失败 | 函数异常退出，下次调度重试；不重复推送靠 last_notified 持久化 |
| 357 只批量超限 | 拆 2-3 个批次请求 |
| 重复收盘总结 | Firestore 记 `last_summary_date`，同一天只推一次 |

---

## 8. 测试

1. `build_watchlist.py`：从 184 CSV + research.db 生成正确（160 解析 / 15 歧义进 review / 173 大盘 / 16 关注合并去重）
2. `gen_targets.py`：公式单测（共识×0.75 / 200MA / 50MA×0.95 取最低）
3. `price_alert.py`：触发逻辑（现价<目标价→推）、7 天冷却、推送窗口（夏/冬）、收盘总结去重——用 fake yfinance + fake Firestore
4. 线上：部署后手动触发 → 首次建库 → 触发一次真实提醒验证

---

## 9. 部署（复用 danchi 模式）

- GCP 项目 `stock-alert`（独立，用户组织下）；webhook 存 Secret Manager `stock-discord-webhook`（用户已给 URL）
- 部署：`./deploy.sh`（`GCP_PROJECT=stock-alert gcloud functions deploy ...`）
- Scheduler：2 个 job（每 30 分窗口 + 收盘，Asia/Tokyo）
- Budget Alert $1

## 10. 不做 / 待定

- 不做日股；不做交易
- 173 大盘的 `script` 目标价是启发式——用户以后想对某只深挖，再跑 skill 升级为 `skill` 来源
