#!/usr/bin/env python3
"""从 Nasdaq screener 拉取全市场美股，把市值≥100亿(10B)的公司保存/刷新到 companies 表。

规则：
- 新增：只收录 market_cap ≥ 100亿美元 的公司（作为研究候选池）
- 更新：已在 DB 里的公司（无论市值）用最新数据刷新市值（含已研究的 <10B 标的）
- 不删：已有记录不会因本次拉取被删除（保留研究跟踪）

用法：
    python3 scripts/update_universe.py
"""
import json
import sqlite3
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "research.db"
DATE = "20260808"  # 数据日期

NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
HEADERS = [
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept: application/json, text/plain, */*",
]


def fetch_screener():
    r = subprocess.run(
        ["curl", "-s", "--max-time", "90", "-H", HEADERS[0], "-H", HEADERS[1], NASDAQ_URL],
        capture_output=True, text=True,
    )
    return json.loads(r.stdout)


def parse_market_cap(v):
    if not v or str(v).upper() in ("N/A", "NAN", "NONE"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    # 确保 country 列存在
    cols = [r[1] for r in conn.execute("PRAGMA table_info(companies)")]
    if "country" not in cols:
        conn.execute("ALTER TABLE companies ADD COLUMN country TEXT")
        print("已添加 country 列")

    data = fetch_screener()
    # download=true 时为 data.rows；tableonly 时为 data.table.rows
    d = data.get("data", {}) or {}
    rows = d.get("rows") or (d.get("table") or {}).get("rows") or []
    if not rows:
        print("❌ Nasdaq screener 返回空，检查网络/接口。")
        conn.close()
        return
    print(f"Nasdaq screener 返回 {len(rows)} 只股票")

    n_add = n_upd = n_skip = 0
    for r in rows:
        mcap = parse_market_cap(r.get("marketCap"))
        if mcap is None:
            continue
        ticker = r.get("symbol")
        mcap_b = mcap / 1e9

        existing = conn.execute(
            "SELECT market_cap_b FROM companies WHERE ticker=?", (ticker,)
        ).fetchone()

        # 未研究且 <10B → 跳过（不扩大底库）
        if existing is None and mcap < 10e9:
            n_skip += 1
            continue

        conn.execute(
            """INSERT INTO companies (ticker, company_name, market_cap_b, market_cap_date, country)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 company_name = excluded.company_name,
                 market_cap_b = excluded.market_cap_b,
                 market_cap_date = excluded.market_cap_date,
                 country = excluded.country,
                 updated_at = datetime('now','localtime')""",
            (ticker, r.get("name"), mcap_b, DATE, r.get("country")),
        )
        if existing is None:
            n_add += 1
        else:
            n_upd += 1

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    n10 = conn.execute("SELECT COUNT(*) FROM companies WHERE market_cap_b >= 10").fetchone()[0]
    print(f"新增 {n_add} | 更新 {n_upd} | 跳过(<10B未研究) {n_skip}")
    print(f"companies 总数: {total}（其中 ≥$100亿: {n10}）")
    conn.close()


if __name__ == "__main__":
    main()
