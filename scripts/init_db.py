#!/usr/bin/env python3
"""初始化研究数据库：建表 + 种子公司基础财务信息。

用法：
    python3 scripts/init_db.py

- 建表 companies / skill_reports（幂等）
- 种子公司数据（ticker, 名称, 市值 十亿美元, 市值数据日期）
- 重复运行会更新市值（以最近抓取为准）
"""
from pathlib import Path
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "research.db"

# (ticker, 公司名, 市值十亿美元, 市值数据日期)
# 市值来源：2026-08-08 stockanalysis.com / 当日投研报告，≥100亿为主，低市值标的保留以便跟踪
COMPANIES = [
    # (ticker, company_name, market_cap_b, market_cap_date)
    ("ALB", "Albemarle", 15.47, "20260808"),
    ("APP", "AppLovin", 116.06, "20260808"),
    ("AVGO", "Broadcom", 2040.0, "20260808"),
    ("BBAI", "BigBear.ai", 1.57, "20260808"),
    ("BWXT", "BWX Technologies", 15.57, "20260808"),
    ("CEG", "Constellation Energy", 95.62, "20260808"),
    ("COIN", "Coinbase", 40.53, "20260808"),
    ("CRWV", "CoreWeave", 49.47, "20260808"),
    ("EU", "enCore Energy", 0.24, "20260808"),
    ("GOOGL", "Alphabet", 4330.0, "20260808"),
    ("HOOD", "Robinhood", 83.88, "20260808"),
    ("IBM", "IBM", 223.55, "20260808"),
    ("INTC", "Intel", 512.72, "20260808"),
    ("META", "Meta Platforms", 1510.0, "20260808"),
    ("MSFT", "Microsoft", 3710.0, "20260808"),
    ("Metaplanet", "Metaplanet (BTC treasury)", 1.79, "20260808"),
    ("ORCL", "Oracle", 423.49, "20260808"),
    ("PYPL", "PayPal", 50.53, "20260808"),
    ("SPCX", "SpaceX (xAI 复合体)", 1750.0, "20260808"),
    ("TQQQ", "ProShares UltraPro QQQ (3x ETF)", None, "20260808"),
    ("VRT", "Vertiv", 104.87, "20260808"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT NOT NULL UNIQUE,        -- 股票代码，如 CEG / Metaplanet
    company_name      TEXT,                        -- 公司全名
    market_cap_b      REAL,                        -- 市值（十亿美元）；10 = 100亿美元
    market_cap_date   TEXT,                        -- 市值数据日期 YYYYMMDD
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    updated_at        TEXT DEFAULT (datetime('now','localtime'))
);

-- 每(公司, skill)一行，保存该 skill 对该公司的【最新】报告日期与路径
CREATE TABLE IF NOT EXISTS skill_reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    skill               TEXT NOT NULL,             -- investment-team / earnings-review / ...
    latest_report_date  TEXT NOT NULL,             -- 最新报告日期 YYYYMMDD（同 skill 多份时取最大）
    report_path         TEXT,                      -- 最新报告相对路径
    created_at          TEXT DEFAULT (datetime('now','localtime')),
    updated_at          TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(company_id, skill)
);

CREATE INDEX IF NOT EXISTS idx_skill_reports_skill ON skill_reports(skill);
"""


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)

    n_ins = n_upd = 0
    for ticker, name, mcap, mdate in COMPANIES:
        cur = conn.execute(
            """INSERT INTO companies (ticker, company_name, market_cap_b, market_cap_date)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 company_name=excluded.company_name,
                 market_cap_b=excluded.market_cap_b,
                 market_cap_date=excluded.market_cap_date,
                 updated_at=datetime('now','localtime')""",
            (ticker, name, mcap, mdate),
        )
        if cur.rowcount == 1:
            n_ins += 1
        else:
            n_upd += 1

    conn.commit()

    # 概览
    n10 = conn.execute("SELECT COUNT(*) FROM companies WHERE market_cap_b >= 10").fetchone()[0]
    n_all = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    print(f"数据库: {DB_PATH}")
    print(f"companies: 新增 {n_ins} / 更新 {n_upd} | 共 {n_all} 家，其中 ≥100亿美元 {n10} 家")
    print("skill_reports 表待 ingest_reports.py 填充。")
    conn.close()


if __name__ == "__main__":
    main()
