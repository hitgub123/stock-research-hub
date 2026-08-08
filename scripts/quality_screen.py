#!/usr/bin/env python3
"""quality-screen 全市场去劣筛选（批量版）。

Stage 1 — 批量抓取：对 DB 中全部 ≥100亿 且未研究的公司，抓取 stockanalysis.com statistics 页的 49 个结构化指标。
Stage 2 — 去劣筛选：按 quality-screen 7 条指标（用 trailing 值作代理）程序化过滤，输出漏斗。

数据源：stockanalysis.com/stocks/{ticker}/statistics/（curl 直抓，页面内嵌结构化 JSON）
注意：批量页只给 trailing 指标，7 条中多年度聚合项（10年均ROE/5年累计FCF/5年稀释）用 trailing 代理，属"快速去劣"，
     通过者需逐家跑完整 quality-screen 复核多年度数据。

用法：
    python3 scripts/quality_screen.py            # 抓取(缓存)+筛选
    python3 scripts/quality_screen.py --fetch-only   # 只抓取/更新缓存
    python3 scripts/quality_screen.py --screen-only  # 只用缓存筛选
"""
import concurrent.futures
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "research.db"
DATA_DIR = PROJECT_ROOT / "data"
CACHE_FILE = DATA_DIR / "quality_metrics.json"
DATE = "20260808"

STAT_URL = "https://stockanalysis.com/stocks/{}/statistics/"
HEADERS = ["User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)"]
WORKERS = 10
TIMEOUT = 20

METRIC_RE = re.compile(r'id:"([a-z0-9_]+)",title:"([^"]+)",value:"([^"]*)"')


# ---------------- Stage 1: fetch ----------------
# stockanalysis 有 Cloudflare JS challenge，突发的并发请求会触发封禁。
# 策略：单线程慢速（~1.2 req/s）+ 失败重试（退避等待挑战冷却）。

def fetch_page(ticker, retries=4):
    # stockanalysis URL 用 brk-b 表示 BRK/B
    url_ticker = ticker.lower().replace("/", "-")
    for attempt in range(retries):
        r = subprocess.run(
            ["curl", "-s", "--max-time", str(TIMEOUT), "-H", HEADERS[0], STAT_URL.format(url_ticker)],
            capture_output=True, text=True,
        )
        metrics = {m[0]: m[2] for m in METRIC_RE.findall(r.stdout or "")}
        if metrics:
            return metrics
        # 退避：挑战需时间冷却，重试间隔逐渐拉长
        time.sleep(5 * (attempt + 1))
    return None


def fetch_all(tickers):
    DATA_DIR.mkdir(exist_ok=True)
    cache = {}
    if CACHE_FILE.exists():
        cache = json.loads(CACHE_FILE.read_text())
    todo = [t for t in tickers if t not in cache or not cache[t].get("_ok")]
    ok_cache = sum(1 for v in cache.values() if v.get("_ok"))
    print(f"待抓取 {len(todo)} 家（缓存已有成功 {ok_cache} 家）")

    fails = []
    done = 0
    # 单线程慢速；若连续失败说明又被挑战，加长冷却
    consecutive_fail = 0
    for ticker in todo:
        time.sleep(0.5)  # 节奏控制
        metrics = fetch_page(ticker)
        if metrics:
            metrics["_ok"] = True
            cache[ticker] = metrics
            consecutive_fail = 0
        else:
            fails.append(ticker)
            cache[ticker] = {"_ok": False}
            consecutive_fail += 1
            if consecutive_fail >= 5:
                print(f"  ⚠️ 连续 {consecutive_fail} 次失败，可能触发挑战，冷却 60s…")
                time.sleep(60)
                consecutive_fail = 0
        done += 1
        if done % 25 == 0 or done == len(todo):
            # 增量保存：即使中途停止，最多丢 25 家
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))
            print(f"  进度 {done}/{len(todo)}（已增量保存）")

    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
    ok = sum(1 for v in cache.values() if v.get("_ok"))
    print(f"缓存 {len(cache)} 家（成功 {ok}，失败 {len(fails)}）→ {CACHE_FILE}")
    if fails:
        print("抓取失败（可重跑 --fetch-only 续抓）: " + ", ".join(fails[:20]) + ("..." if len(fails) > 20 else ""))
    return cache


# ---------------- helpers ----------------
def parse_pct(v):
    """'15.05%' → 15.05 ; '-3.4%' → -3.4"""
    if not v:
        return None
    m = re.search(r"-?[\d.]+", str(v))
    return float(m.group()) if m else None


def parse_amt(v):
    """'95.62B'/'1.23T'/'295.00M'/'-24.00B' → float(单位: 1) ; 返回原始数值(百万美元为单位)"""
    if not v:
        return None
    s = str(v).replace(",", "").replace("$", "").strip()
    m = re.match(r"(-?[\d.]+)([A-Za-z]?)$", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    mult = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}.get(unit, 1)
    return num * mult


def screen_one(ticker, m):
    """对单家应用 7 条指标（trailing 代理），返回 (通过/排除/边界, 明细 dict)。"""
    if not m.get("_ok"):
        return "数据失败", {}

    roe = parse_pct(m.get("roe"))
    gp, rev = parse_amt(m.get("gp")), parse_amt(m.get("revenue"))
    ni, ocf, fcf = parse_amt(m.get("netinc")), parse_amt(m.get("ncfo")), parse_amt(m.get("fcf"))
    shares_yoy = parse_pct(m.get("sharesgrowthyoy"))
    pe = m.get("pe")

    # 非经营性/数据缺失保护
    rev = rev or 0
    gm = (gp / rev * 100) if rev and gp is not None else None
    nm = (ni / rev * 100) if rev and ni is not None else None
    ocf_ni = (ocf / ni) if (ni is not None and ni > 0) else None

    detail = {}

    # ① ROE ≥ 8%（豁免A：高毛利>30% + 现金流转正）
    c1 = None
    if roe is not None:
        c1 = roe >= 8
        if not c1 and gm is not None and gm > 30 and ocf is not None and ocf > 0:
            c1 = True  # 豁免A
    detail["①ROE"] = (c1, roe)

    # ② FCF ≥ 0
    c2 = None
    if fcf is not None:
        c2 = fcf >= 0
    detail["②FCF"] = (c2, fcf)

    # ③ 利息覆盖（批量页无利息支出 → 数据不足，不计入；净负债大户另行标记）
    debt = parse_amt(m.get("debt"))
    detail["③利息"] = (None, debt)

    # ④ 毛利率 ≥ 15%（豁免C：ROE>20 且 OCF/NI>1.0）
    c4 = None
    if gm is not None:
        c4 = gm >= 15
        if not c4 and roe is not None and roe > 20 and ocf_ni is not None and ocf_ni > 1.0:
            c4 = True  # 豁免C
    detail["④毛利率"] = (c4, gm)

    # ⑤ OCF/NI ≥ 0.7
    c5 = None
    if ocf_ni is not None:
        c5 = ocf_ni >= 0.7
    detail["⑤OCF/NI"] = (c5, ocf_ni)

    # ⑥ 净利率 ≥ 5%（豁免C同④；豁免B近似：gm>30 → 边界）
    c6 = None
    if nm is not None:
        c6 = nm >= 5
        if not c6 and roe is not None and roe > 20 and ocf_ni is not None and ocf_ni > 1.0:
            c6 = True  # 豁免C
    detail["⑥净利率"] = (c6, nm)

    # ⑦ 股本膨胀 ≤ 20%（1年 YoY 代理）
    c7 = None
    if shares_yoy is not None:
        c7 = shares_yoy <= 20
    detail["⑦稀释"] = (c7, shares_yoy)

    # 汇总：①~⑦ 中可判定的
    judged = {k: v[0] for k, v in detail.items() if k != "③利息" and v[0] is not None}
    fails = [k for k, v in judged.items() if v is False]
    if not judged:
        return "数据不足", detail
    if not fails:
        return "通过", detail
    if len(fails) <= 2:
        return "边界", detail
    return "排除", detail


# ---------------- Stage 2: screen ----------------
def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    conn = sqlite3.connect(DB_PATH)
    tickers = [r[0] for r in conn.execute(
        """SELECT ticker FROM companies WHERE market_cap_b >= 10
           AND NOT EXISTS(SELECT 1 FROM skill_reports s WHERE s.company_id = companies.id)
           ORDER BY market_cap_b DESC"""
    ).fetchall()]
    conn.close()
    print(f"待筛选公司（≥100亿 且未研究）: {len(tickers)} 家")

    cache = {}
    if only != "--screen-only":
        cache = fetch_all(tickers)
    elif CACHE_FILE.exists():
        cache = json.loads(CACHE_FILE.read_text())
        print(f"从缓存读取 {len(cache)} 家")

    if only == "--fetch-only":
        return

    results = {}
    for t in tickers:
        if t not in cache:
            continue
        verdict, detail = screen_one(t, cache[t])
        results[t] = {"verdict": verdict, "detail": detail,
                      "mcap_b": None}

    # 附加市值/名称
    conn = sqlite3.connect(DB_PATH)
    meta = {}
    if results:
        qmarks = ",".join("?" * len(results))
        meta = {r[0]: (r[1], r[2]) for r in conn.execute(
            f"SELECT ticker, company_name, market_cap_b FROM companies WHERE ticker IN ({qmarks})",
            tuple(results.keys()),
        ).fetchall()}
    conn.close()

    from collections import Counter
    cnt = Counter(r["verdict"] for r in results.values())
    print(f"\n=== 去劣筛选结果（trailing 代理）===")
    print(f"通过 {cnt.get('通过',0)} | 边界 {cnt.get('边界',0)} | 排除 {cnt.get('排除',0)} | 数据不足/失败 {cnt.get('数据不足',0)+cnt.get('数据失败',0)}")

    # 输出通过+边界名单
    shortlist = {t: r for t, r in results.items() if r["verdict"] in ("通过", "边界")}
    out = DATA_DIR / f"quality_shortlist_{DATE}.json"
    DATA_DIR.mkdir(exist_ok=True)
    out.write_text(json.dumps({t: {"verdict": r["verdict"],
                                   "detail": {k: v[1] for k, v in r["detail"].items()}}
                               for t, r in shortlist.items()}, ensure_ascii=False, indent=1))
    print(f"\n通过+边界 {len(shortlist)} 家 → {out}")
    print("\n=== 通过名单（前 60，按市值）===")
    rows = [(t, r) for t, r in shortlist.items()]
    rows.sort(key=lambda x: meta.get(x[0], (None, 0))[1] or 0, reverse=True)
    for t, r in rows[:60]:
        name = (meta.get(t) or ("?", 0))[0][:30]
        print(f"  {t:<8} {name:<30} [{r['verdict']}]  ROE={r['detail'].get('①ROE',(None,None))[1]} "
              f"毛利={r['detail'].get('④毛利率',(None,None))[1]} 净利={r['detail'].get('⑥净利率',(None,None))[1]}  FCF={r['detail'].get('②FCF',(None,None))[1]}")


if __name__ == "__main__":
    main()
